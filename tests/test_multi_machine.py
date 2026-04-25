from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from umx.cli import main
from umx.config import default_config, save_config
from umx.memory import add_fact, load_all_facts
from umx.models import (
    ConsolidationStatus,
    Fact,
    MemoryType,
    Scope,
    SourceType,
    Verification,
)
from umx.scope import config_path
from umx.sessions import session_path, write_session


def _make_fact(text: str, topic: str = "general", **overrides) -> Fact:
    values = {
        "fact_id": overrides.pop("fact_id", "01TESTFACT0000000000005300"),
        "text": text,
        "scope": Scope.PROJECT,
        "topic": topic,
        "encoding_strength": 3,
        "memory_type": MemoryType.EXPLICIT_SEMANTIC,
        "verification": Verification.SELF_REPORTED,
        "source_type": SourceType.TOOL_OUTPUT,
        "source_tool": "copilot-cli",
        "source_session": "sess-multi-machine-001",
        "consolidation_status": ConsolidationStatus.FRAGILE,
    }
    values.update(overrides)
    return Fact(**values)


def _init_bare_remote(tmp_path: Path, name: str) -> Path:
    remote = tmp_path / f"{name}.git"
    subprocess.run(["git", "init", "--bare", str(remote)], capture_output=True, check=True)
    subprocess.run(
        ["git", "--git-dir", str(remote), "symbolic-ref", "HEAD", "refs/heads/main"],
        capture_output=True,
        check=True,
    )
    return remote


def _select_machine(monkeypatch, home: Path) -> None:
    monkeypatch.setenv("UMX_HOME", str(home))


def _set_mode(monkeypatch, home: Path, *, mode: str) -> None:
    _select_machine(monkeypatch, home)
    cfg = default_config()
    cfg.org = "memory-org"
    cfg.dream.mode = mode
    save_config(config_path(), cfg)


def _invoke_bootstrap_command(
    runner: CliRunner,
    args: list[str],
    *,
    remote: Path,
):
    with patch("umx.github_ops.gh_available", return_value=True), patch(
        "umx.github_ops.ensure_repo",
        return_value=str(remote),
    ), patch("umx.cli.assert_push_safe"), patch("umx.git_ops.assert_signed_commit_range"):
        return runner.invoke(main, args)


def _init_machine(
    monkeypatch,
    runner: CliRunner,
    home: Path,
    project_dir: Path,
    *,
    project_remote: Path | None,
    user_remote: Path,
    mode: str,
) -> tuple[Path, Path]:
    bootstrap_mode = "hybrid" if mode == "local" else mode
    _select_machine(monkeypatch, home)

    init_result = _invoke_bootstrap_command(
        runner,
        ["init", "--org", "memory-org", "--mode", bootstrap_mode],
        remote=user_remote,
    )
    assert init_result.exit_code == 0, init_result.output

    if mode == "local" and project_remote is None:
        _set_mode(monkeypatch, home, mode="local")
        project_result = runner.invoke(
            main,
            ["init-project", "--cwd", str(project_dir), "--slug", "project"],
        )
    else:
        assert project_remote is not None
        project_result = _invoke_bootstrap_command(
            runner,
            ["init-project", "--cwd", str(project_dir), "--slug", "project"],
            remote=project_remote,
        )
    assert project_result.exit_code == 0, project_result.output

    if mode == "local" and project_remote is not None:
        _set_mode(monkeypatch, home, mode="local")

    return home / "projects" / "project", home / "user"


@pytest.mark.parametrize("mode", ["local", "hybrid", "remote"])
def test_multi_machine_project_sync_propagates_session_history(
    mode: str,
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_remote = _init_bare_remote(tmp_path, f"multi-machine-project-{mode}")
    user_remote = _init_bare_remote(tmp_path, f"multi-machine-user-{mode}")
    project_a = tmp_path / f"project-a-{mode}"
    project_b = tmp_path / f"project-b-{mode}"
    for project_dir in (project_a, project_b):
        project_dir.mkdir()
        (project_dir / ".git").mkdir()

    home_a = tmp_path / f"home-a-{mode}"
    home_b = tmp_path / f"home-b-{mode}"
    runner = CliRunner()

    repo_a, _ = _init_machine(
        monkeypatch,
        runner,
        home_a,
        project_a,
        project_remote=project_remote,
        user_remote=user_remote,
        mode=mode,
    )
    repo_b, _ = _init_machine(
        monkeypatch,
        runner,
        home_b,
        project_b,
        project_remote=project_remote,
        user_remote=user_remote,
        mode=mode,
    )

    session_id = f"2026-04-24-multi-machine-sync-{mode}"
    _select_machine(monkeypatch, home_a)
    write_session(
        repo_a,
        {
            "session_id": session_id,
            "tool": "copilot-cli",
            "started": "2026-04-24T00:00:00Z",
        },
        [{"role": "user", "content": f"machine A captured a new {mode} session"}],
        auto_commit=False,
    )

    sync_a = runner.invoke(main, ["sync", "--cwd", str(project_a)])
    assert sync_a.exit_code == 0, sync_a.output

    _select_machine(monkeypatch, home_b)
    assert not session_path(repo_b, session_id).exists()
    sync_b = runner.invoke(main, ["sync", "--cwd", str(project_b)])

    assert sync_b.exit_code == 0, sync_b.output
    synced_path = session_path(repo_b, session_id)
    assert synced_path.exists()
    assert f"machine A captured a new {mode} session" in synced_path.read_text()


@pytest.mark.parametrize("mode", ["local", "hybrid", "remote"])
def test_multi_machine_project_sync_rebases_and_preserves_both_session_sets(
    mode: str,
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_remote = _init_bare_remote(tmp_path, f"multi-machine-project-rebase-{mode}")
    user_remote = _init_bare_remote(tmp_path, f"multi-machine-user-rebase-{mode}")
    project_a = tmp_path / f"project-a-rebase-{mode}"
    project_b = tmp_path / f"project-b-rebase-{mode}"
    for project_dir in (project_a, project_b):
        project_dir.mkdir()
        (project_dir / ".git").mkdir()

    home_a = tmp_path / f"home-a-rebase-{mode}"
    home_b = tmp_path / f"home-b-rebase-{mode}"
    runner = CliRunner()

    repo_a, _ = _init_machine(
        monkeypatch,
        runner,
        home_a,
        project_a,
        project_remote=project_remote,
        user_remote=user_remote,
        mode=mode,
    )
    repo_b, _ = _init_machine(
        monkeypatch,
        runner,
        home_b,
        project_b,
        project_remote=project_remote,
        user_remote=user_remote,
        mode=mode,
    )

    session_a = f"2026-04-24-multi-machine-a-{mode}"
    session_b = f"2026-04-24-multi-machine-b-{mode}"

    _select_machine(monkeypatch, home_a)
    write_session(
        repo_a,
        {
            "session_id": session_a,
            "tool": "copilot-cli",
            "started": "2026-04-24T00:00:00Z",
        },
        [{"role": "user", "content": "machine A session"}],
        auto_commit=False,
    )
    sync_a = runner.invoke(main, ["sync", "--cwd", str(project_a)])
    assert sync_a.exit_code == 0, sync_a.output

    _select_machine(monkeypatch, home_b)
    write_session(
        repo_b,
        {
            "session_id": session_b,
            "tool": "copilot-cli",
            "started": "2026-04-24T00:05:00Z",
        },
        [{"role": "user", "content": "machine B session"}],
        auto_commit=False,
    )
    sync_b = runner.invoke(main, ["sync", "--cwd", str(project_b)])
    assert sync_b.exit_code == 0, sync_b.output
    session_a_on_b = session_path(repo_b, session_a)
    session_b_on_b = session_path(repo_b, session_b)
    assert session_a_on_b.exists()
    assert session_b_on_b.exists()
    assert "machine A session" in session_a_on_b.read_text()
    assert "machine B session" in session_b_on_b.read_text()

    _select_machine(monkeypatch, home_a)
    sync_a_again = runner.invoke(main, ["sync", "--cwd", str(project_a)])
    assert sync_a_again.exit_code == 0, sync_a_again.output
    session_a_on_a = session_path(repo_a, session_a)
    session_b_on_a = session_path(repo_a, session_b)
    assert session_a_on_a.exists()
    assert session_b_on_a.exists()
    assert "machine A session" in session_a_on_a.read_text()
    assert "machine B session" in session_b_on_a.read_text()


def test_multi_machine_local_sync_propagates_user_scope_promotions(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_remote = _init_bare_remote(tmp_path, "multi-machine-project-user-scope")
    user_remote = _init_bare_remote(tmp_path, "multi-machine-user-scope")
    project_a = tmp_path / "project-a-user-scope"
    project_b = tmp_path / "project-b-user-scope"
    for project_dir in (project_a, project_b):
        project_dir.mkdir()
        (project_dir / ".git").mkdir()

    home_a = tmp_path / "home-a-user-scope"
    home_b = tmp_path / "home-b-user-scope"
    runner = CliRunner()

    repo_a, user_repo_a = _init_machine(
        monkeypatch,
        runner,
        home_a,
        project_a,
        project_remote=project_remote,
        user_remote=user_remote,
        mode="local",
    )
    repo_b, user_repo_b = _init_machine(
        monkeypatch,
        runner,
        home_b,
        project_b,
        project_remote=project_remote,
        user_remote=user_remote,
        mode="local",
    )

    fact = _make_fact(
        "release notes live in docs/releases",
        topic="docs",
        fact_id="01TESTFACT0000000000005301",
    )
    _select_machine(monkeypatch, home_a)
    add_fact(repo_a, fact)
    sync_a_initial = runner.invoke(main, ["sync", "--cwd", str(project_a)])
    assert sync_a_initial.exit_code == 0, sync_a_initial.output

    _select_machine(monkeypatch, home_b)
    sync_b_initial = runner.invoke(main, ["sync", "--cwd", str(project_b)])
    assert sync_b_initial.exit_code == 0, sync_b_initial.output
    assert any(item.fact_id == fact.fact_id for item in load_all_facts(repo_b, include_superseded=False))

    _select_machine(monkeypatch, home_a)
    promote = runner.invoke(
        main,
        ["promote", "--cwd", str(project_a), "--fact", fact.fact_id, "--to", "user"],
    )
    assert promote.exit_code == 0, promote.output
    assert all(item.fact_id != fact.fact_id for item in load_all_facts(repo_a, include_superseded=False))
    assert any(item.fact_id == fact.fact_id for item in load_all_facts(user_repo_a, include_superseded=False))

    sync_a = runner.invoke(main, ["sync", "--cwd", str(project_a)])
    assert sync_a.exit_code == 0, sync_a.output
    assert "synced user memory with" in sync_a.output

    _select_machine(monkeypatch, home_b)
    sync_b = runner.invoke(main, ["sync", "--cwd", str(project_b)])
    assert sync_b.exit_code == 0, sync_b.output
    assert "synced user memory with" in sync_b.output

    assert all(item.fact_id != fact.fact_id for item in load_all_facts(repo_b, include_superseded=False))
    user_facts = load_all_facts(user_repo_b, include_superseded=False)
    promoted = next(item for item in user_facts if item.fact_id == fact.fact_id)
    assert promoted.scope == Scope.USER


def test_multi_machine_local_sync_supports_user_remote_without_project_remote(
    tmp_path: Path,
    monkeypatch,
) -> None:
    user_remote = _init_bare_remote(tmp_path, "multi-machine-user-only-remote")
    project_a = tmp_path / "project-a-user-only-remote"
    project_b = tmp_path / "project-b-user-only-remote"
    for project_dir in (project_a, project_b):
        project_dir.mkdir()
        (project_dir / ".git").mkdir()

    home_a = tmp_path / "home-a-user-only-remote"
    home_b = tmp_path / "home-b-user-only-remote"
    runner = CliRunner()

    repo_a, _ = _init_machine(
        monkeypatch,
        runner,
        home_a,
        project_a,
        project_remote=None,
        user_remote=user_remote,
        mode="local",
    )
    _, user_repo_b = _init_machine(
        monkeypatch,
        runner,
        home_b,
        project_b,
        project_remote=None,
        user_remote=user_remote,
        mode="local",
    )

    fact = _make_fact(
        "shared release checklist lives in user scope",
        topic="release",
        fact_id="01TESTFACT0000000000005302",
    )
    _select_machine(monkeypatch, home_a)
    add_fact(repo_a, fact)
    promote = runner.invoke(
        main,
        ["promote", "--cwd", str(project_a), "--fact", fact.fact_id, "--to", "user"],
    )
    assert promote.exit_code == 0, promote.output

    sync_a = runner.invoke(main, ["sync", "--cwd", str(project_a)])
    assert sync_a.exit_code == 0, sync_a.output
    assert sync_a.output.strip() == f"synced user memory with {user_remote}"

    _select_machine(monkeypatch, home_b)
    sync_b = runner.invoke(main, ["sync", "--cwd", str(project_b)])
    assert sync_b.exit_code == 0, sync_b.output
    assert sync_b.output.strip() == f"synced user memory with {user_remote}"
    assert any(item.fact_id == fact.fact_id for item in load_all_facts(user_repo_b, include_superseded=False))
