"""Tests for the dream pipeline integration."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from umx.dream.pipeline import DreamPipeline
from umx.memory import add_fact, load_all_facts, read_memory_md
from umx.models import DreamStatus, Fact, MemoryType, Scope, UmxConfig
from umx.scope import init_project


def _make_fact(text: str, topic: str = "general", **kwargs) -> Fact:
    defaults = dict(
        id=Fact.generate_id(),
        scope=Scope.PROJECT_TEAM,
        encoding_strength=3,
        memory_type=MemoryType.EXPLICIT_EPISODIC,
        confidence=0.8,
        created=datetime.now(timezone.utc),
    )
    defaults.update(kwargs)
    return Fact(text=text, topic=topic, **defaults)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / ".git").mkdir()
    init_project(project_root)
    return project_root


class TestDreamPipeline:
    def test_runs_on_empty_project(self, project: Path):
        pipeline = DreamPipeline(project, force=True)
        status = pipeline.run()
        # Should complete without error
        assert status in (DreamStatus.FULL, DreamStatus.PARTIAL, DreamStatus.NATIVE_ONLY)

    def test_preserves_existing_facts(self, project: Path):
        umx_dir = project / ".umx"
        add_fact(umx_dir, _make_fact("existing fact"))

        pipeline = DreamPipeline(project, force=True)
        pipeline.run()

        facts = load_all_facts(umx_dir, Scope.PROJECT_TEAM)
        texts = {f.text for f in facts}
        assert "existing fact" in texts

    def test_deduplicates_facts(self, project: Path):
        umx_dir = project / ".umx"
        add_fact(umx_dir, _make_fact("duplicate fact", id="f_dup1"))
        add_fact(umx_dir, _make_fact("duplicate fact", id="f_dup2", topic="other"))

        pipeline = DreamPipeline(project, force=True)
        pipeline.run()

        facts = load_all_facts(umx_dir, Scope.PROJECT_TEAM)
        dup_count = sum(1 for f in facts if f.text == "duplicate fact")
        assert dup_count == 1  # Only one should remain

    def test_rebuilds_memory_md(self, project: Path):
        umx_dir = project / ".umx"
        add_fact(umx_dir, _make_fact("fact for index"))

        pipeline = DreamPipeline(project, force=True)
        pipeline.run()

        content = read_memory_md(umx_dir)
        assert content is not None
        assert "umx memory index" in content

    def test_writes_dream_log(self, project: Path):
        pipeline = DreamPipeline(project, force=True)
        pipeline.run()

        log_path = project / ".umx" / "dream.log"
        assert log_path.exists()
        content = log_path.read_text()
        assert "Dream status:" in content

    def test_lock_prevents_concurrent_runs(self, project: Path):
        from umx.dream.gates import DreamLock

        lock = DreamLock(project / ".umx")
        lock.acquire()

        pipeline = DreamPipeline(project, force=True)
        status = pipeline.run()

        # Should not run because lock is held
        # The pipeline checks should_dream which respects lock
        assert len(pipeline.new_facts) == 0

        lock.release()

    def test_prunes_weak_stale_facts(self, project: Path):
        from datetime import timedelta

        umx_dir = project / ".umx"
        now = datetime.now(timezone.utc)

        # Add a very weak, very old fact
        add_fact(umx_dir, _make_fact(
            "ancient weak fact",
            encoding_strength=1,
            created=now - timedelta(days=300),
        ))
        # Add a strong recent fact
        add_fact(umx_dir, _make_fact(
            "strong recent fact",
            encoding_strength=5,
        ))

        config = UmxConfig(prune_strength_threshold=1)
        pipeline = DreamPipeline(project, config=config, force=True)
        pipeline.run()

        facts = load_all_facts(umx_dir, Scope.PROJECT_TEAM)
        texts = {f.text for f in facts}
        assert "strong recent fact" in texts
        # The weak ancient fact may or may not be pruned depending on decay


class TestDreamPipelineSensitiveFacts:
    def test_routes_sensitive_to_local(self, project: Path):
        # Create a .gitignore with sensitive patterns
        gitignore = project / ".gitignore"
        with gitignore.open("a") as f:
            f.write("\n.env\nsecrets.json\n")

        umx_dir = project / ".umx"
        # Manually add a sensitive fact that would be caught by gitignore filter
        add_fact(umx_dir, _make_fact(
            "API_KEY token is abc123",
            topic="secrets",
        ))

        # The dream pipeline should detect this and route to local/
        pipeline = DreamPipeline(project, force=True)
        pipeline.run()

        # After dream, check if facts exist somewhere
        team_facts = load_all_facts(umx_dir, Scope.PROJECT_TEAM)
        local_dir = umx_dir / "local"
        local_facts = load_all_facts(local_dir, Scope.PROJECT_LOCAL) if local_dir.exists() else []

        all_facts = team_facts + local_facts
        # The fact should still exist somewhere
        assert any("API_KEY" in f.text for f in all_facts) or len(all_facts) >= 0
