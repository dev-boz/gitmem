from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from umx.config import default_config, save_config
from umx.dream.gates import increment_session_count, read_dream_state
from umx.git_ops import git_add_and_commit, git_init
from umx.hooks import dispatch_hook
from umx.hooks.assistant_output import run as assistant_output_run
from umx.hooks.post_tool_use import run as post_tool_use_run
from umx.hooks.pre_compact import run as pre_compact_run
from umx.hooks.pre_tool_use import run as pre_tool_use_run
from umx.hooks.session_end import run as session_end_run
from umx.hooks.session_start import run as session_start_run
from umx.hooks.subagent_start import run as subagent_start_run
from umx.memory import add_fact
from umx.models import (
    ConsolidationStatus,
    Fact,
    MemoryType,
    Scope,
    SourceType,
    TaskStatus,
    Verification,
)
from umx.search import usage_snapshot
from umx.sessions import read_session, session_path
from umx.scope import (
    config_path,
    ensure_repo_structure,
    init_local_umx,
    init_project_memory,
    project_memory_dir,
)
from umx.inject import build_injection_block


@pytest.fixture
def umx_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "umxhome"
    monkeypatch.setenv("UMX_HOME", str(home))
    init_local_umx()
    save_config(config_path(), default_config())
    return home


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    (project / ".git").mkdir()
    return project


@pytest.fixture
def project_repo(umx_home: Path, project_dir: Path) -> Path:
    init_project_memory(project_dir)
    return project_memory_dir(project_dir)


# --- session_start ---


def test_session_start_returns_injection_block(
    project_dir: Path, project_repo: Path
) -> None:
    result = session_start_run(cwd=project_dir, tool="copilot")
    assert result is not None
    assert isinstance(result, str)
    assert "UMX Memory" in result


def test_session_start_increments_session_count(
    project_dir: Path, project_repo: Path
) -> None:
    state_before = read_dream_state(project_repo)
    count_before = int(state_before.get("session_count", 0))

    session_start_run(cwd=project_dir)

    state_after = read_dream_state(project_repo)
    count_after = int(state_after.get("session_count", 0))
    assert count_after == count_before + 1


def test_session_start_handles_missing_repo(tmp_path: Path, umx_home: Path) -> None:
    # A directory with no .git or .umx-project — still shouldn't crash
    bare = tmp_path / "nowhere"
    bare.mkdir()
    result = session_start_run(cwd=bare)
    # May return a block or None; must not raise
    assert result is None or isinstance(result, str)


def test_session_start_runs_safety_sweep(
    project_dir: Path, project_repo: Path
) -> None:
    git_init(project_repo)
    # Create an uncommitted session file
    sessions_dir = project_repo / "sessions" / "2026" / "01"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    session_file = sessions_dir / "2026-01-15-orphan.jsonl"
    session_file.write_text('{"_meta": {"session_id": "2026-01-15-orphan"}}\n')

    with patch("umx.hooks.session_start.safety_sweep") as mock_sweep:
        session_start_run(cwd=project_dir)
        mock_sweep.assert_called_once_with(project_repo)


# --- session_end ---


def test_session_end_writes_session(
    project_dir: Path, project_repo: Path
) -> None:
    git_init(project_repo)
    events = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi there"},
    ]
    result = session_end_run(
        cwd=project_dir,
        session_id="2026-01-15-testend",
        tool="copilot",
        events=events,
    )
    assert result["session_written"] is True


def test_session_end_no_events(
    project_dir: Path, project_repo: Path
) -> None:
    result = session_end_run(
        cwd=project_dir,
        session_id="2026-01-15-noevents",
    )
    assert result["session_written"] is False


def test_session_end_triggers_dream_when_gates_met(
    project_dir: Path, project_repo: Path
) -> None:
    # Force dream gates to be met by setting high session count
    for _ in range(10):
        increment_session_count(project_repo)

    with patch("umx.hooks.session_end.DreamPipeline") as MockPipeline:
        mock_result = type("DreamResult", (), {
            "status": "ok", "added": 3, "pruned": 1, "message": "done",
        })()
        MockPipeline.return_value.run.return_value = mock_result

        result = session_end_run(
            cwd=project_dir,
            session_id="2026-01-15-dream",
        )

    assert result["dream_triggered"] is True
    assert result["dream_result"]["status"] == "ok"


def test_session_end_dream_error_returns_error(
    project_dir: Path, project_repo: Path
) -> None:
    for _ in range(10):
        increment_session_count(project_repo)

    with patch("umx.hooks.session_end.DreamPipeline") as MockPipeline:
        MockPipeline.return_value.run.side_effect = RuntimeError("boom")

        result = session_end_run(
            cwd=project_dir,
            session_id="2026-01-15-dreamerr",
        )

    assert result["dream_triggered"] is True
    assert result["dream_result"]["status"] == "error"
    assert "boom" in result["dream_result"]["error"]


# --- pre_compact ---


def test_pre_compact_commits_changes(
    project_dir: Path, project_repo: Path
) -> None:
    git_init(project_repo)
    # Create an uncommitted file
    facts_dir = project_repo / "facts" / "topics"
    facts_dir.mkdir(parents=True, exist_ok=True)
    (facts_dir / "test.md").write_text("# test fact\n")

    result = pre_compact_run(cwd=project_dir)
    assert result["committed"] is True


def test_pre_compact_nothing_to_commit(
    project_dir: Path, project_repo: Path
) -> None:
    git_init(project_repo)
    # Commit everything so there's nothing left
    git_add_and_commit(project_repo, message="setup")
    result = pre_compact_run(cwd=project_dir)
    assert result["committed"] is False
    assert result["pushed"] is False


# --- post_tool_use ---


def test_post_tool_use_returns_scoped_injection(
    project_dir: Path, project_repo: Path
) -> None:
    result = post_tool_use_run(
        cwd=project_dir,
        tool_name="copilot",
        file_paths=["src/main.py"],
    )
    assert result is not None
    assert isinstance(result, str)
    assert "UMX Memory" in result


def test_post_tool_use_no_file_paths(
    project_dir: Path, project_repo: Path
) -> None:
    result = post_tool_use_run(cwd=project_dir)
    assert result is None


def test_pre_tool_use_injects_matching_procedure(
    project_dir: Path, project_repo: Path
) -> None:
    procedure = project_repo / "procedures" / "deploy.md"
    procedure.write_text(
        "# Deploy to staging\n\n"
        "<!-- id:01TESTPROC0000000000000001 conf:4 src:human_authored -->\n\n"
        "## Triggers\n\n"
        "- command: `kubectl apply|helm upgrade`\n"
        "- file: `k8s/*.yaml|infrastructure/**`\n\n"
        "## Steps\n\n"
        "1. Run `make test`\n"
        "2. Verify the cluster context\n"
    )

    result = pre_tool_use_run(
        cwd=project_dir,
        tool_name="shell",
        command_text="kubectl apply -k overlays/staging",
        file_paths=["k8s/deploy.yaml"],
        session_id="pretool-001",
    )

    assert result is not None
    assert "## Procedures" in result
    assert "Deploy to staging" in result
    assert "Run `make test`" in result


def test_pre_tool_use_ignores_invalid_procedure_regex(
    project_dir: Path, project_repo: Path
) -> None:
    procedure = project_repo / "procedures" / "broken.md"
    procedure.write_text(
        "# Broken procedure\n\n"
        "## Triggers\n\n"
        "- command: `[` \n\n"
        "## Steps\n\n"
        "1. This should never break injection\n"
    )

    result = pre_tool_use_run(
        cwd=project_dir,
        tool_name="shell",
        command_text="kubectl apply -k overlays/staging",
        session_id="pretool-bad-regex",
    )

    assert result is not None
    assert "Broken procedure" not in result


def test_subagent_start_relays_active_working_set(
    project_dir: Path, project_repo: Path
) -> None:
    fact = Fact(
        fact_id="01TESTFACT0000000000000300",
        text="postgres runs on 5433 in dev",
        scope=Scope.PROJECT,
        topic="devenv",
        encoding_strength=4,
        memory_type=MemoryType.EXPLICIT_SEMANTIC,
        verification=Verification.CORROBORATED,
        source_type=SourceType.GROUND_TRUTH_CODE,
        source_tool="codex",
        source_session="sess-parent-001",
        consolidation_status=ConsolidationStatus.STABLE,
    )
    task = Fact(
        fact_id="01TESTFACT0000000000000301",
        text="finish database migration",
        scope=Scope.PROJECT,
        topic="tasks",
        encoding_strength=3,
        memory_type=MemoryType.EXPLICIT_SEMANTIC,
        verification=Verification.SELF_REPORTED,
        source_type=SourceType.TOOL_OUTPUT,
        source_tool="codex",
        source_session="sess-parent-001",
        consolidation_status=ConsolidationStatus.STABLE,
        task_status=TaskStatus.OPEN,
    )
    add_fact(project_repo, fact)
    add_fact(project_repo, task)

    session_start_run(cwd=project_dir, session_id="sess-parent-001", tool="copilot")
    session_end_run(
        cwd=project_dir,
        session_id="sess-parent-001",
        events=[{"role": "assistant", "content": "Remember that postgres runs on 5433 in dev."}],
    )

    result = subagent_start_run(
        cwd=project_dir,
        parent_session_id="sess-parent-001",
        subagent_session_id="sess-child-001",
        objective="debug deployment",
    )

    assert result is not None
    assert "## Hot Summary" in result
    assert "postgres runs on 5433 in dev" in result
    assert "finish database migration" in result


def test_assistant_output_records_session_and_references(
    project_dir: Path, project_repo: Path
) -> None:
    fact = Fact(
        fact_id="01TESTFACT0000000000000302",
        text="deploys run through staging first",
        scope=Scope.PROJECT,
        topic="release",
        encoding_strength=4,
        memory_type=MemoryType.EXPLICIT_SEMANTIC,
        verification=Verification.CORROBORATED,
        source_type=SourceType.GROUND_TRUTH_CODE,
        source_tool="codex",
        source_session="sess-live-001",
        consolidation_status=ConsolidationStatus.STABLE,
    )
    add_fact(project_repo, fact)
    build_injection_block(project_dir, prompt="staging deploys", session_id="sess-live-001")

    snapshot = assistant_output_run(
        cwd=project_dir,
        session_id="sess-live-001",
        tool="copilot",
        event={"role": "assistant", "content": "Reminder: deploys run through staging first."},
    )

    assert snapshot is not None
    usage = usage_snapshot(project_repo)
    assert usage[fact.fact_id]["cited_count"] >= 1

    payload = read_session(session_path(project_repo, "sess-live-001"))
    assert payload[0]["_meta"]["project"] == project_repo.name
    assert payload[0]["_meta"]["started"]
    assert payload[1]["ts"]


def test_subagent_start_uses_latest_referenced_turn_only(
    project_dir: Path, project_repo: Path, user_repo: Path
) -> None:
    stale_user = Fact(
        fact_id="01TESTFACT0000000000000303",
        text="always prefer concise release notes",
        scope=Scope.USER,
        topic="writing",
        encoding_strength=5,
        memory_type=MemoryType.EXPLICIT_SEMANTIC,
        verification=Verification.HUMAN_CONFIRMED,
        source_type=SourceType.USER_PROMPT,
        source_tool="human",
        source_session="sess-parent-002",
        consolidation_status=ConsolidationStatus.STABLE,
    )
    latest_project = Fact(
        fact_id="01TESTFACT0000000000000304",
        text="the replacement worker runs as a systemd service",
        scope=Scope.PROJECT,
        topic="operations",
        encoding_strength=4,
        memory_type=MemoryType.EXPLICIT_SEMANTIC,
        verification=Verification.CORROBORATED,
        source_type=SourceType.GROUND_TRUTH_CODE,
        source_tool="codex",
        source_session="sess-parent-002",
        consolidation_status=ConsolidationStatus.STABLE,
    )
    add_fact(user_repo, stale_user)
    add_fact(project_repo, latest_project)

    session_start_run(cwd=project_dir, session_id="sess-parent-002", tool="copilot")
    build_injection_block(project_dir, prompt="release notes", session_id="sess-parent-002")
    assistant_output_run(
        cwd=project_dir,
        session_id="sess-parent-002",
        tool="copilot",
        event={"role": "assistant", "content": "Always prefer concise release notes."},
    )
    build_injection_block(project_dir, prompt="worker scheduling", session_id="sess-parent-002")
    assistant_output_run(
        cwd=project_dir,
        session_id="sess-parent-002",
        tool="copilot",
        event={"role": "assistant", "content": "The replacement worker runs as a systemd service."},
    )

    result = subagent_start_run(
        cwd=project_dir,
        parent_session_id="sess-parent-002",
        subagent_session_id="sess-child-002",
        objective="debug worker scheduling",
    )

    assert result is not None
    assert "the replacement worker runs as a systemd service" in result
    assert "always prefer concise release notes" not in result


# --- dispatch_hook ---


def test_dispatch_hook_routes_session_start(
    project_dir: Path, project_repo: Path
) -> None:
    result = dispatch_hook("session_start", cwd=project_dir)
    assert result is None or isinstance(result, str)


def test_dispatch_hook_routes_session_end(
    project_dir: Path, project_repo: Path
) -> None:
    result = dispatch_hook(
        "session_end",
        cwd=project_dir,
        session_id="2026-01-15-dispatch",
    )
    assert isinstance(result, dict)
    assert "session_written" in result


def test_dispatch_hook_routes_pre_compact(
    project_dir: Path, project_repo: Path
) -> None:
    result = dispatch_hook("pre_compact", cwd=project_dir)
    assert isinstance(result, dict)
    assert "committed" in result


def test_dispatch_hook_routes_post_tool_use(
    project_dir: Path, project_repo: Path
) -> None:
    result = dispatch_hook("post_tool_use", cwd=project_dir)
    assert result is None


def test_dispatch_hook_routes_pre_tool_use(
    project_dir: Path, project_repo: Path
) -> None:
    result = dispatch_hook("pre_tool_use", cwd=project_dir)
    assert result is None or isinstance(result, str)


def test_dispatch_hook_routes_subagent_start(
    project_dir: Path, project_repo: Path
) -> None:
    result = dispatch_hook(
        "subagent_start",
        cwd=project_dir,
        parent_session_id="dispatch-parent",
    )
    assert result is None or isinstance(result, str)


def test_dispatch_hook_unknown_raises() -> None:
    with pytest.raises(ValueError, match="Unknown hook"):
        dispatch_hook("nonexistent_hook", cwd=Path("/"))
