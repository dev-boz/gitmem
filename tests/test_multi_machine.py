from __future__ import annotations

from pathlib import Path
import subprocess
from unittest.mock import patch

from click.testing import CliRunner

from umx.cli import main
from umx.config import default_config, save_config
from umx.scope import config_path, init_local_umx
from umx.sessions import session_path, write_session


def _seed_bare_remote(
    tmp_path: Path,
    name: str,
    relative_path: str,
    content: str,
    *,
    message: str,
) -> Path:
    remote = tmp_path / f"{name}.git"
    worktree = tmp_path / f"{name}-seed"
    subprocess.run(["git", "init", "--bare", str(remote)], capture_output=True, check=True)
    subprocess.run(["git", "init", "-b", "main", str(worktree)], capture_output=True, check=True)
    subprocess.run(
        ["git", "-C", str(worktree), "config", "user.name", "Test User"],
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(worktree), "config", "user.email", "test@example.com"],
        capture_output=True,
        check=True,
    )
    target = worktree / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    subprocess.run(["git", "-C", str(worktree), "add", "-A"], capture_output=True, check=True)
    subprocess.run(
        ["git", "-C", str(worktree), "commit", "-m", message],
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(worktree), "remote", "add", "origin", str(remote)],
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(worktree), "push", "-u", "origin", "main"],
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "--git-dir", str(remote), "symbolic-ref", "HEAD", "refs/heads/main"],
        capture_output=True,
        check=True,
    )
    return remote


def _configure_machine(monkeypatch, home: Path) -> None:
    monkeypatch.setenv("UMX_HOME", str(home))
    init_local_umx()
    cfg = default_config()
    cfg.org = "memory-org"
    cfg.dream.mode = "hybrid"
    save_config(config_path(), cfg)


def _init_project_for_machine(
    runner: CliRunner,
    project_dir: Path,
    remote: Path,
) -> None:
    with patch("umx.github_ops.gh_available", return_value=True), patch(
        "umx.github_ops.ensure_repo",
        return_value=str(remote),
    ), patch("umx.cli.assert_push_safe"), patch("umx.git_ops.assert_signed_commit_range"):
        result = runner.invoke(main, ["init-project", "--cwd", str(project_dir), "--slug", "project"])
    assert result.exit_code == 0, result.output


def test_multi_machine_hybrid_project_sync_propagates_session_history(
    tmp_path: Path,
    monkeypatch,
) -> None:
    remote = _seed_bare_remote(
        tmp_path,
        "multi-machine-project",
        "facts/topics/seed.md",
        "# seed\n\n## Facts\n- shared project memory baseline\n",
        message="seed shared project remote",
    )
    project_a = tmp_path / "project-a"
    project_b = tmp_path / "project-b"
    for project_dir in (project_a, project_b):
        project_dir.mkdir()
        (project_dir / ".git").mkdir()

    home_a = tmp_path / "home-a"
    home_b = tmp_path / "home-b"
    runner = CliRunner()

    _configure_machine(monkeypatch, home_a)
    _init_project_for_machine(runner, project_a, remote)
    repo_a = home_a / "projects" / "project"

    _configure_machine(monkeypatch, home_b)
    _init_project_for_machine(runner, project_b, remote)
    repo_b = home_b / "projects" / "project"

    session_id = "2026-04-23-multi-machine-sync"
    _configure_machine(monkeypatch, home_a)
    write_session(
        repo_a,
        {
            "session_id": session_id,
            "tool": "copilot-cli",
            "started": "2026-04-23T00:00:00Z",
        },
        [{"role": "user", "content": "machine A captured a new session"}],
        auto_commit=False,
    )

    sync_a = runner.invoke(main, ["sync", "--cwd", str(project_a)])
    assert sync_a.exit_code == 0, sync_a.output

    _configure_machine(monkeypatch, home_b)
    assert not session_path(repo_b, session_id).exists()
    sync_b = runner.invoke(main, ["sync", "--cwd", str(project_b)])

    assert sync_b.exit_code == 0, sync_b.output
    synced_path = session_path(repo_b, session_id)
    assert synced_path.exists()
    assert "machine A captured a new session" in synced_path.read_text()


def test_multi_machine_hybrid_project_sync_rebases_and_preserves_both_session_sets(
    tmp_path: Path,
    monkeypatch,
) -> None:
    remote = _seed_bare_remote(
        tmp_path,
        "multi-machine-project-rebase",
        "facts/topics/seed.md",
        "# seed\n\n## Facts\n- shared project memory baseline\n",
        message="seed shared project remote for rebase",
    )
    project_a = tmp_path / "project-a"
    project_b = tmp_path / "project-b"
    for project_dir in (project_a, project_b):
        project_dir.mkdir()
        (project_dir / ".git").mkdir()

    home_a = tmp_path / "home-a"
    home_b = tmp_path / "home-b"
    runner = CliRunner()

    _configure_machine(monkeypatch, home_a)
    _init_project_for_machine(runner, project_a, remote)
    repo_a = home_a / "projects" / "project"

    _configure_machine(monkeypatch, home_b)
    _init_project_for_machine(runner, project_b, remote)
    repo_b = home_b / "projects" / "project"

    session_a = "2026-04-23-multi-machine-a"
    session_b = "2026-04-23-multi-machine-b"

    _configure_machine(monkeypatch, home_a)
    write_session(
        repo_a,
        {
            "session_id": session_a,
            "tool": "copilot-cli",
            "started": "2026-04-23T00:00:00Z",
        },
        [{"role": "user", "content": "machine A session"}],
        auto_commit=False,
    )
    sync_a = runner.invoke(main, ["sync", "--cwd", str(project_a)])
    assert sync_a.exit_code == 0, sync_a.output

    _configure_machine(monkeypatch, home_b)
    write_session(
        repo_b,
        {
            "session_id": session_b,
            "tool": "copilot-cli",
            "started": "2026-04-23T00:05:00Z",
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

    _configure_machine(monkeypatch, home_a)
    sync_a_again = runner.invoke(main, ["sync", "--cwd", str(project_a)])
    assert sync_a_again.exit_code == 0, sync_a_again.output
    session_a_on_a = session_path(repo_a, session_a)
    session_b_on_a = session_path(repo_a, session_b)
    assert session_a_on_a.exists()
    assert session_b_on_a.exists()
    assert "machine A session" in session_a_on_a.read_text()
    assert "machine B session" in session_b_on_a.read_text()
