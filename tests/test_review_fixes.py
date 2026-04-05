"""Tests for review findings: session gate, promote, Claude adapter, tool-scope,
file-scope, dream skip, lock timezone, injection scoring, decay floor,
and edit detection."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from umx.models import (
    DreamStatus,
    EncodingStrength,
    Fact,
    MemoryType,
    Scope,
    UmxConfig,
)


# ─── Session gate persistence ───────────────────────────────────


class TestSessionGatePersistence:
    """Session count must persist in .session_count and be read by should_dream."""

    def test_increment_writes_durable_file(self, tmp_path: Path):
        from umx.dream.gates import increment_session_count

        umx_dir = tmp_path / ".umx"
        umx_dir.mkdir()
        count = increment_session_count(umx_dir)
        assert count == 1
        assert (umx_dir / ".session_count").exists()
        assert (umx_dir / ".session_count").read_text().strip() == "1"

    def test_increment_accumulates(self, tmp_path: Path):
        from umx.dream.gates import increment_session_count

        umx_dir = tmp_path / ".umx"
        umx_dir.mkdir()
        increment_session_count(umx_dir)
        increment_session_count(umx_dir)
        count = increment_session_count(umx_dir)
        assert count == 3

    def test_read_dream_state_reads_session_count(self, tmp_path: Path):
        from umx.dream.gates import read_dream_state

        umx_dir = tmp_path / ".umx"
        umx_dir.mkdir()
        (umx_dir / ".session_count").write_text("7")

        state = read_dream_state(umx_dir)
        assert state["session_count"] == 7

    def test_should_dream_fires_on_session_gate(self, tmp_path: Path):
        from umx.dream.gates import should_dream

        umx_dir = tmp_path / ".umx"
        umx_dir.mkdir()
        # Recent dream, so time gate is closed
        now = datetime.now(timezone.utc)
        (umx_dir / "MEMORY.md").write_text(f"last_dream: {now.isoformat()}\n")
        # But session count is high
        (umx_dir / ".session_count").write_text("10")
        assert should_dream(umx_dir, session_threshold=5)

    def test_should_dream_blocked_when_below_threshold(self, tmp_path: Path):
        from umx.dream.gates import should_dream

        umx_dir = tmp_path / ".umx"
        umx_dir.mkdir()
        now = datetime.now(timezone.utc)
        (umx_dir / "MEMORY.md").write_text(f"last_dream: {now.isoformat()}\n")
        (umx_dir / ".session_count").write_text("2")
        assert not should_dream(umx_dir, session_threshold=5)

    def test_reset_session_count(self, tmp_path: Path):
        from umx.dream.gates import increment_session_count, reset_session_count

        umx_dir = tmp_path / ".umx"
        umx_dir.mkdir()
        increment_session_count(umx_dir)
        increment_session_count(umx_dir)
        reset_session_count(umx_dir)
        assert (umx_dir / ".session_count").read_text().strip() == "0"


# ─── Claude adapter filtering ───────────────────────────────────


class TestClaudeAdapterFiltering:
    """Claude adapter must only read memory for the current project."""

    def test_matches_project_exact(self, tmp_path: Path):
        from umx.adapters.claude_code import ClaudeCodeAdapter

        adapter = ClaudeCodeAdapter()
        project_root = Path("/home/user/myproject")

        # Create a dir that matches the project path
        claude_dir = tmp_path / "home-user-myproject"
        claude_dir.mkdir()

        assert adapter._matches_project(claude_dir, project_root)

    def test_rejects_unrelated_project(self, tmp_path: Path):
        from umx.adapters.claude_code import ClaudeCodeAdapter

        adapter = ClaudeCodeAdapter()
        project_root = Path("/home/user/myproject")

        unrelated = tmp_path / "home-user-otherproject"
        unrelated.mkdir()

        assert not adapter._matches_project(unrelated, project_root)

    def test_no_cross_project_leakage(self, tmp_path: Path, monkeypatch):
        from umx.adapters.claude_code import ClaudeCodeAdapter

        adapter = ClaudeCodeAdapter()
        # Set up fake ~/.claude/projects/ with two project dirs
        claude_home = tmp_path / "claude_home"
        projects_dir = claude_home / ".claude" / "projects"

        # Our project
        our_dir = projects_dir / "home-user-myproject"
        our_dir.mkdir(parents=True)
        (our_dir / "memory.md").write_text("- Our secret API key pattern\n")

        # Other project (should NOT be read)
        other_dir = projects_dir / "home-user-otherproject"
        other_dir.mkdir(parents=True)
        (other_dir / "memory.md").write_text("- Other project secret\n")

        monkeypatch.setattr(Path, "home", lambda: claude_home)

        project_root = Path("/home/user/myproject")
        facts = adapter.read_native_memory(project_root)

        fact_texts = [f.text for f in facts]
        assert any("Our secret" in t for t in fact_texts)
        assert not any("Other project" in t for t in fact_texts)


# ─── Promote fix ─────────────────────────────────────────────────


class TestPromoteFix:
    """promote must remove from correct source directory."""

    def test_promote_removes_from_local(self, tmp_path: Path):
        from umx.memory import add_fact, find_fact_by_id, load_all_facts

        umx_dir = tmp_path / ".umx"
        local_dir = umx_dir / "local"
        local_dir.mkdir(parents=True)
        (umx_dir / "topics").mkdir(parents=True)

        fact = Fact(
            id="f_local_001",
            text="Local fact to promote",
            scope=Scope.PROJECT_LOCAL,
            topic="test",
            encoding_strength=3,
            memory_type=MemoryType.EXPLICIT_SEMANTIC,
            confidence=0.8,
        )
        add_fact(local_dir, fact)

        # Verify it's in local
        assert find_fact_by_id(local_dir, "f_local_001", Scope.PROJECT_LOCAL)

        # Simulate promote: remove from local, add to team
        from umx.memory import remove_fact

        removed = remove_fact(local_dir, "f_local_001", "test", Scope.PROJECT_LOCAL)
        assert removed

        fact.scope = Scope.PROJECT_TEAM
        add_fact(umx_dir, fact)

        # Verify it's now in team and NOT in local
        team_facts = load_all_facts(umx_dir, Scope.PROJECT_TEAM)
        local_facts = load_all_facts(local_dir, Scope.PROJECT_LOCAL)

        assert any(f.id == "f_local_001" for f in team_facts)
        assert not any(f.id == "f_local_001" for f in local_facts)


# ─── Tool scope isolation ────────────────────────────────────────


class TestToolScopeIsolation:
    """Tool scope must point to per-tool directory, not shared."""

    def test_tool_scope_per_tool(self, tmp_path: Path, monkeypatch):
        from umx.scope import resolve_scopes

        monkeypatch.setenv("HOME", str(tmp_path))
        # Create project root
        project_root = tmp_path / "myproject"
        project_root.mkdir()
        (project_root / ".git").mkdir()
        (project_root / ".umx").mkdir()

        layers = resolve_scopes(project_root, tool="claude-code")

        tool_layers = [l for l in layers if l.scope == Scope.TOOL]
        assert len(tool_layers) == 1
        # Must include the tool name in the path
        assert "claude-code" in str(tool_layers[0].path)

    def test_different_tools_different_paths(self, tmp_path: Path, monkeypatch):
        from umx.scope import resolve_scopes

        monkeypatch.setenv("HOME", str(tmp_path))
        project_root = tmp_path / "myproject"
        project_root.mkdir()
        (project_root / ".git").mkdir()
        (project_root / ".umx").mkdir()

        layers_claude = resolve_scopes(project_root, tool="claude-code")
        layers_aider = resolve_scopes(project_root, tool="aider")

        tool_claude = [l for l in layers_claude if l.scope == Scope.TOOL]
        tool_aider = [l for l in layers_aider if l.scope == Scope.TOOL]

        assert tool_claude[0].path != tool_aider[0].path


# ─── File scope ──────────────────────────────────────────────────


class TestFileScopeFix:
    """File scope must point to specific file, not directory."""

    def test_file_scope_points_to_specific_file(self, tmp_path: Path, monkeypatch):
        from umx.scope import resolve_scopes

        monkeypatch.setenv("HOME", str(tmp_path))
        project_root = tmp_path / "myproject"
        project_root.mkdir()
        (project_root / ".git").mkdir()
        (project_root / ".umx").mkdir()

        target_file = project_root / "src" / "main.py"
        target_file.parent.mkdir(parents=True)
        target_file.touch()

        layers = resolve_scopes(
            project_root, target_file=target_file
        )
        file_layers = [l for l in layers if l.scope == Scope.FILE]
        assert len(file_layers) == 1
        assert file_layers[0].path.name == "main.py.md"

    def test_load_all_facts_includes_files(self, tmp_path: Path):
        from umx.memory import add_fact, load_all_facts

        umx_dir = tmp_path / ".umx"
        files_dir = umx_dir / "files"
        files_dir.mkdir(parents=True)
        (umx_dir / "topics").mkdir()

        # Add a file-scope fact
        fact = Fact(
            id="f_file_001",
            text="File-specific fact",
            scope=Scope.FILE,
            topic="main.py",
            encoding_strength=4,
            memory_type=MemoryType.EXPLICIT_SEMANTIC,
            confidence=0.8,
        )
        # Save it under files/ instead of topics/
        from umx.memory import save_topic_facts, derive_json

        topic_path = files_dir / "main.py.md"
        save_topic_facts(topic_path, "main.py", [fact])

        all_facts = load_all_facts(umx_dir, Scope.PROJECT_TEAM)
        assert any(f.id == "f_file_001" for f in all_facts)


# ─── Dream skip status ──────────────────────────────────────────


class TestDreamSkipStatus:
    """Dream should return SKIPPED when gates aren't met."""

    def test_dream_returns_skipped(self, tmp_path: Path):
        from umx.dream.pipeline import DreamPipeline

        project_root = tmp_path / "project"
        project_root.mkdir()
        umx_dir = project_root / ".umx"
        umx_dir.mkdir()
        (umx_dir / "topics").mkdir()
        # Recent dream, low session count → gates not met
        now = datetime.now(timezone.utc)
        (umx_dir / "MEMORY.md").write_text(f"last_dream: {now.isoformat()}\n")
        (umx_dir / ".session_count").write_text("0")

        config = UmxConfig()
        pipeline = DreamPipeline(project_root, config=config)
        status = pipeline.run()
        assert status == DreamStatus.SKIPPED

    def test_dream_status_skipped_exists(self):
        assert hasattr(DreamStatus, "SKIPPED")
        assert DreamStatus.SKIPPED.value == "skipped"


# ─── Lock timezone safety ───────────────────────────────────────


class TestLockTimezone:
    """DreamLock.is_locked must handle naive timestamps without crashing."""

    def test_naive_timestamp_handled(self, tmp_path: Path):
        from umx.dream.gates import DreamLock

        umx_dir = tmp_path / ".umx"
        umx_dir.mkdir()
        lock = DreamLock(umx_dir)

        # Write a lock with a naive (no timezone) timestamp
        data = {
            "locked_at": datetime.now().isoformat(),  # naive!
            "pid": 12345,
        }
        lock.lock_path.write_text(json.dumps(data))
        # Should not raise TypeError
        result = lock.is_locked
        assert isinstance(result, bool)

    def test_corrupt_timestamp_handled(self, tmp_path: Path):
        from umx.dream.gates import DreamLock

        umx_dir = tmp_path / ".umx"
        umx_dir.mkdir()
        lock = DreamLock(umx_dir)

        data = {"locked_at": "not-a-date", "pid": 12345}
        lock.lock_path.write_text(json.dumps(data))
        # Should not raise, should treat as stale
        result = lock.is_locked
        assert result is False


# ─── Injection scoring ──────────────────────────────────────────


class TestInjectionScoring:
    """Injection must use composite_score as tiebreaker."""

    def test_composite_score_used_for_tiebreaker(self, tmp_path: Path):
        from umx.inject import collect_facts_for_injection
        from umx.memory import add_fact

        umx_dir = tmp_path / ".umx"
        (umx_dir / "topics").mkdir(parents=True)
        (tmp_path / ".git").mkdir()

        now = datetime.now(timezone.utc)
        # Two facts with same relevance but different composite scores
        fact_high = Fact(
            id="f_high",
            text="High composite fact",
            scope=Scope.PROJECT_TEAM,
            topic="test",
            encoding_strength=5,
            memory_type=MemoryType.EXPLICIT_SEMANTIC,
            confidence=1.0,
            created=now,
        )
        fact_low = Fact(
            id="f_low",
            text="Low composite fact",
            scope=Scope.PROJECT_TEAM,
            topic="test",
            encoding_strength=1,
            memory_type=MemoryType.EXPLICIT_SEMANTIC,
            confidence=0.3,
            created=now - timedelta(days=90),
        )
        add_fact(umx_dir, fact_high)
        add_fact(umx_dir, fact_low)

        facts = collect_facts_for_injection(tmp_path)
        # High composite should come first
        ids = [f.id for f in facts]
        assert ids.index("f_high") < ids.index("f_low")

    def test_relevance_score_uses_target_scope(self):
        from umx.strength import relevance_score

        now = datetime.now(timezone.utc)
        fact = Fact(
            id="f_001",
            text="test fact",
            scope=Scope.FILE,
            topic="test",
            encoding_strength=3,
            memory_type=MemoryType.EXPLICIT_SEMANTIC,
            confidence=0.8,
            created=now,
        )

        # Score when target is FILE (same scope → high proximity)
        score_file = relevance_score(fact, target_scope=Scope.FILE)
        # Score when target is USER (far scope → lower proximity)
        score_user = relevance_score(fact, target_scope=Scope.USER)

        # FILE scope fact should score higher when targeting FILE scope
        assert score_file >= score_user


# ─── Decay floor ─────────────────────────────────────────────────


class TestDecayFloor:
    """Time decay must never go below 1 (the 1–5 invariant)."""

    def test_decay_floors_at_1(self):
        from umx.dream.decay import apply_time_decay

        now = datetime.now(timezone.utc)
        fact = Fact(
            id="f_001",
            text="weak old fact",
            scope=Scope.PROJECT_TEAM,
            topic="test",
            encoding_strength=1,
            memory_type=MemoryType.EXPLICIT_SEMANTIC,
            confidence=0.3,
            created=now - timedelta(days=365),
        )
        result = apply_time_decay([fact], now=now)
        # Must stay >= 1
        assert result[0].encoding_strength >= 1

    def test_decay_does_not_go_to_zero(self):
        from umx.dream.decay import apply_time_decay

        now = datetime.now(timezone.utc)
        fact = Fact(
            id="f_002",
            text="another weak fact",
            scope=Scope.PROJECT_TEAM,
            topic="test",
            encoding_strength=2,
            memory_type=MemoryType.EXPLICIT_SEMANTIC,
            confidence=0.3,
            created=now - timedelta(days=500),
        )
        result = apply_time_decay([fact], now=now)
        assert result[0].encoding_strength >= 1


# ─── Edit detection ─────────────────────────────────────────────


class TestEditDetection:
    """Editing a fact's text (keeping same id) must promote to S:5."""

    def test_edited_fact_promoted_to_s5(self, tmp_path: Path):
        from umx.memory import (
            add_fact,
            format_fact_line,
            load_topic_facts,
            save_topic_facts,
        )

        umx_dir = tmp_path / ".umx"
        topics_dir = umx_dir / "topics"
        topics_dir.mkdir(parents=True)

        # Create a fact at S:3
        fact = Fact(
            id="f_edit_001",
            text="Original text about auth",
            scope=Scope.PROJECT_TEAM,
            topic="test",
            encoding_strength=3,
            memory_type=MemoryType.EXPLICIT_SEMANTIC,
            confidence=0.8,
        )
        add_fact(umx_dir, fact)

        # Simulate user editing the text in the markdown file
        topic_path = topics_dir / "test.md"
        content = topic_path.read_text()
        # Replace the original text with edited text (keeping the metadata)
        content = content.replace(
            "Original text about auth",
            "Edited text about auth flow"
        )
        topic_path.write_text(content)

        # Re-load — should detect the edit and promote to S:5
        facts = load_topic_facts(topic_path, "test", Scope.PROJECT_TEAM)
        edited = [f for f in facts if f.id == "f_edit_001"]
        assert len(edited) == 1
        assert edited[0].encoding_strength == 5
        assert edited[0].confidence == 1.0

    def test_unedited_fact_keeps_strength(self, tmp_path: Path):
        from umx.memory import add_fact, load_topic_facts

        umx_dir = tmp_path / ".umx"
        topics_dir = umx_dir / "topics"
        topics_dir.mkdir(parents=True)

        fact = Fact(
            id="f_noedit_001",
            text="Unchanged fact",
            scope=Scope.PROJECT_TEAM,
            topic="test",
            encoding_strength=2,
            memory_type=MemoryType.EXPLICIT_SEMANTIC,
            confidence=0.7,
        )
        add_fact(umx_dir, fact)

        # Re-load without editing — should keep original strength
        topic_path = topics_dir / "test.md"
        facts = load_topic_facts(topic_path, "test", Scope.PROJECT_TEAM)
        unchanged = [f for f in facts if f.id == "f_noedit_001"]
        assert len(unchanged) == 1
        assert unchanged[0].encoding_strength == 2


# ─── File scope nearest .umx walk-up ────────────────────────────


class TestFileScopeWalkUp:
    """File scope should walk up to find nearest .umx/ dir, not just parent."""

    def test_finds_intermediate_umx(self, tmp_path: Path, monkeypatch):
        from umx.scope import resolve_scopes

        monkeypatch.setenv("HOME", str(tmp_path))
        project_root = tmp_path / "project"
        project_root.mkdir()
        (project_root / ".git").mkdir()
        (project_root / ".umx").mkdir()

        # Create intermediate .umx at src/
        src_dir = project_root / "src"
        src_dir.mkdir()
        src_umx = src_dir / ".umx"
        src_umx.mkdir()

        # Target file is in src/utils/
        utils_dir = src_dir / "utils"
        utils_dir.mkdir()
        target = utils_dir / "helpers.py"
        target.touch()

        layers = resolve_scopes(project_root, target_file=target)
        file_layers = [l for l in layers if l.scope == Scope.FILE]
        assert len(file_layers) == 1
        # Should find src/.umx/ (nearest), not project/.umx/
        assert "src/.umx/files/helpers.py.md" in str(file_layers[0].path)

    def test_falls_back_to_project_root(self, tmp_path: Path, monkeypatch):
        from umx.scope import resolve_scopes

        monkeypatch.setenv("HOME", str(tmp_path))
        project_root = tmp_path / "project"
        project_root.mkdir()
        (project_root / ".git").mkdir()
        (project_root / ".umx").mkdir()

        # No intermediate .umx/ dirs
        deep_dir = project_root / "a" / "b" / "c"
        deep_dir.mkdir(parents=True)
        target = deep_dir / "file.py"
        target.touch()

        layers = resolve_scopes(project_root, target_file=target)
        file_layers = [l for l in layers if l.scope == Scope.FILE]
        assert len(file_layers) == 1
        assert str(project_root / ".umx" / "files" / "file.py.md") == str(file_layers[0].path)


# ─── Injection config selection ──────────────────────────────────


class TestInjectionConfigSelection:
    """Config should be loaded from PROJECT_TEAM .umx/config.yaml, not local."""

    def test_loads_team_config(self, tmp_path: Path, monkeypatch):
        from umx.inject import collect_facts_for_injection
        from umx.memory import add_fact, save_config
        from umx.models import UmxConfig

        monkeypatch.setenv("HOME", str(tmp_path))
        project_root = tmp_path / "project"
        project_root.mkdir()
        (project_root / ".git").mkdir()
        umx_dir = project_root / ".umx"
        umx_dir.mkdir()
        (umx_dir / "topics").mkdir()
        local_dir = umx_dir / "local"
        local_dir.mkdir()
        (local_dir / "topics").mkdir()

        # Save a custom config to the team dir
        custom_config = UmxConfig(decay_lambda=0.05)
        save_config(umx_dir, custom_config)

        # Add a fact
        fact = Fact(
            id="f_cfg_001",
            text="Config test fact",
            scope=Scope.PROJECT_TEAM,
            topic="test",
            encoding_strength=3,
            memory_type=MemoryType.EXPLICIT_SEMANTIC,
            confidence=0.8,
        )
        add_fact(umx_dir, fact)

        # Should successfully load facts (config found at team level)
        facts = collect_facts_for_injection(project_root)
        assert any(f.id == "f_cfg_001" for f in facts)


# ─── FILE layer direct loading ───────────────────────────────────


class TestFileScopeDirectLoad:
    """FILE scope layer must load facts from its specific .md file,
    not try to call load_all_facts on a file path."""

    def test_file_layer_loads_facts(self, tmp_path: Path, monkeypatch):
        from umx.inject import collect_facts_for_injection
        from umx.memory import save_topic_facts

        monkeypatch.setenv("HOME", str(tmp_path))
        project_root = tmp_path / "project"
        project_root.mkdir()
        (project_root / ".git").mkdir()
        umx_dir = project_root / ".umx"
        umx_dir.mkdir()
        (umx_dir / "topics").mkdir()
        files_dir = umx_dir / "files"
        files_dir.mkdir()

        # Create a file-scope fact at .umx/files/app.py.md
        fact = Fact(
            id="f_file_direct",
            text="app.py uses Flask blueprints",
            scope=Scope.FILE,
            topic="app.py",
            encoding_strength=4,
            memory_type=MemoryType.EXPLICIT_SEMANTIC,
            confidence=0.9,
        )
        save_topic_facts(files_dir / "app.py.md", "app.py", [fact])

        # Create the target file
        target = project_root / "app.py"
        target.touch()

        facts = collect_facts_for_injection(
            project_root, target_file=target
        )
        file_facts = [f for f in facts if f.id == "f_file_direct"]
        assert len(file_facts) >= 1
        assert file_facts[0].scope == Scope.FILE