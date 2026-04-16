from __future__ import annotations

import json
import subprocess
from unittest.mock import Mock, patch

from click.testing import CliRunner

from umx.config import default_config, save_config
from umx.cli import main
from umx.scope import config_path, project_memory_dir
from umx.sessions import write_session


def test_cli_init_and_status(project_dir, umx_home) -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["init", "--org", "memory-org"])
    assert result.exit_code == 0

    result = runner.invoke(main, ["init-project", "--cwd", str(project_dir), "--slug", "demo"])
    assert result.exit_code == 0

    status = runner.invoke(main, ["status", "--cwd", str(project_dir)])
    assert status.exit_code == 0
    payload = json.loads(status.output)
    assert payload["slug"] == "demo"
    assert "fact_count" in payload
    assert "tombstones" in payload
    assert "pending_session_count" in payload
    assert "ok" in payload
    assert "flags" in payload
    assert "guidance" in payload


def test_cli_inject_outputs_memory_block(project_dir, project_repo, user_repo) -> None:
    from umx.memory import add_fact
    from umx.models import (
        ConsolidationStatus,
        Fact,
        MemoryType,
        Scope,
        SourceType,
        Verification,
    )

    add_fact(
        project_repo,
        Fact(
            fact_id="01TESTFACT0000000000000200",
            text="postgres runs on 5433 in dev",
            scope=Scope.PROJECT,
            topic="devenv",
            encoding_strength=4,
            memory_type=MemoryType.EXPLICIT_SEMANTIC,
            verification=Verification.CORROBORATED,
            source_type=SourceType.GROUND_TRUTH_CODE,
            source_tool="codex",
            source_session="2026-04-11",
            consolidation_status=ConsolidationStatus.STABLE,
        ),
    )

    runner = CliRunner()
    result = runner.invoke(main, ["inject", "--cwd", str(project_dir), "--prompt", "postgres"])
    assert result.exit_code == 0
    assert "# UMX Memory" in result.output
    assert "postgres runs on 5433 in dev" in result.output


def test_cli_view_fact_and_health(project_dir, project_repo) -> None:
    from umx.memory import add_fact
    from umx.models import (
        ConsolidationStatus,
        Fact,
        MemoryType,
        Scope,
        SourceType,
        Verification,
    )

    fact = Fact(
        fact_id="01TESTFACT0000000000000201",
        text="deploys run through staging first",
        scope=Scope.PROJECT,
        topic="release",
        encoding_strength=4,
        memory_type=MemoryType.EXPLICIT_SEMANTIC,
        verification=Verification.CORROBORATED,
        source_type=SourceType.GROUND_TRUTH_CODE,
        source_tool="codex",
        source_session="2026-04-11",
        consolidation_status=ConsolidationStatus.STABLE,
    )
    add_fact(project_repo, fact)

    runner = CliRunner()
    view_result = runner.invoke(main, ["view", "--cwd", str(project_dir), "--fact", fact.fact_id])
    assert view_result.exit_code == 0
    payload = json.loads(view_result.output)
    assert payload["fact_id"] == fact.fact_id

    health_result = runner.invoke(main, ["health", "--cwd", str(project_dir)])
    assert health_result.exit_code == 0
    health = json.loads(health_result.output)
    assert "metrics" in health
    assert "flags" in health
    assert "guidance" in health


def test_cli_view_launches_viewer_by_default(project_dir, project_repo) -> None:
    runner = CliRunner()
    server = Mock()
    server.serve_forever.side_effect = KeyboardInterrupt
    with patch("umx.cli.start_viewer", return_value=("http://127.0.0.1:43123", server)) as mock_run:
        result = runner.invoke(main, ["view", "--cwd", str(project_dir)])

    assert result.exit_code == 0
    assert result.output.strip() == "http://127.0.0.1:43123"
    mock_run.assert_called_once_with(project_dir)
    server.serve_forever.assert_called_once_with()


def test_cli_status_remains_json_when_hot_tier_warns(project_dir, project_repo, umx_home) -> None:
    cfg = default_config()
    cfg.memory.hot_tier_max_tokens = 1
    save_config(config_path(), cfg)
    (project_repo / "MEMORY.md").write_text("# Memory\n\n" + ("hot token\n" * 40))

    runner = CliRunner()
    result = runner.invoke(main, ["status", "--cwd", str(project_dir)])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["metrics"]["hot_tier_utilisation"]["status"] == "warn"
    assert payload["ok"] is False
    assert any("Hot tier utilisation" in flag for flag in payload["flags"])
    assert any(item["metric"] == "hot_tier_utilisation" for item in payload["advice"])
    assert payload["guidance"] == payload["advice"]
    assert any("prompt budget" in item["why_it_matters"] for item in payload["guidance"])


def test_cli_status_counts_session_files_and_preserves_pending_counter(project_dir, project_repo) -> None:
    write_session(
        project_repo,
        {"session_id": "2026-04-16-status", "tool": "cursor"},
        [{"role": "assistant", "content": "health surfaces stay aligned"}],
        auto_commit=False,
    )
    (project_repo / "meta" / "dream-state.json").write_text(
        json.dumps({"last_dream": None, "session_count": 7}, sort_keys=True) + "\n"
    )

    runner = CliRunner()
    result = runner.invoke(main, ["status", "--cwd", str(project_dir)])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["session_count"] == 1
    assert payload["pending_session_count"] == 7


def test_cli_status_surfaces_git_signing_config(project_dir, umx_home) -> None:
    cfg = default_config()
    cfg.git.sign_commits = True
    cfg.git.require_signed_commits = True
    save_config(config_path(), cfg)

    runner = CliRunner()
    result = runner.invoke(main, ["status", "--cwd", str(project_dir)])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["git"] == {
        "enabled": True,
        "sign_commits": True,
        "require_signed_commits": True,
    }


def test_cli_init_project_remote_bootstraps_main(project_dir, umx_home, tmp_path) -> None:
    cfg = default_config()
    cfg.org = "memory-org"
    cfg.dream.mode = "remote"
    save_config(config_path(), cfg)

    remote = tmp_path / "memory.git"
    subprocess.run(["git", "init", "--bare", str(remote)], capture_output=True, check=True)

    runner = CliRunner()
    with patch("umx.github_ops.gh_available", return_value=True), patch(
        "umx.github_ops.ensure_repo",
        return_value=str(remote),
    ):
        result = runner.invoke(main, ["init-project", "--cwd", str(project_dir), "--slug", "demo"])

    assert result.exit_code == 0, result.output

    repo = project_memory_dir(project_dir)
    verify = subprocess.run(
        ["git", "--git-dir", str(remote), "rev-parse", "--verify", "main"],
        capture_output=True,
        text=True,
    )
    assert verify.returncode == 0

    tree = subprocess.run(
        ["git", "--git-dir", str(remote), "ls-tree", "-r", "--name-only", "main"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert ".github/workflows/l1-dream.yml" in tree.stdout
    assert ".github/workflows/l2-review.yml" in tree.stdout

    log = subprocess.run(
        ["git", "-C", str(repo), "log", "--oneline"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "bootstrap remote project memory" in log.stdout


def test_cli_init_remote_bootstraps_user_workflows(umx_home, user_repo, tmp_path) -> None:
    remote = tmp_path / "user-memory.git"
    subprocess.run(["git", "init", "--bare", str(remote)], capture_output=True, check=True)

    runner = CliRunner()
    with patch("umx.github_ops.gh_available", return_value=True), patch(
        "umx.github_ops.ensure_repo",
        return_value=str(remote),
    ):
        result = runner.invoke(main, ["init", "--org", "memory-org", "--mode", "remote"])

    assert result.exit_code == 0, result.output
    assert (user_repo / ".github" / "workflows" / "l1-dream.yml").exists()
    assert (user_repo / ".github" / "workflows" / "l2-review.yml").exists()

    tree = subprocess.run(
        ["git", "--git-dir", str(remote), "ls-tree", "-r", "--name-only", "main"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert ".github/workflows/l1-dream.yml" in tree.stdout
    assert ".github/workflows/l2-review.yml" in tree.stdout


def test_cli_doctor_surfaces_git_signing_config(umx_home) -> None:
    cfg = default_config()
    cfg.git.require_signed_commits = True
    save_config(config_path(), cfg)

    runner = CliRunner()
    result = runner.invoke(main, ["doctor"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["git_signing"] == {
        "enabled": True,
        "sign_commits": False,
        "require_signed_commits": True,
    }
    assert "guidance" in payload["health"]


def test_cli_init_project_surfaces_git_init_commit_failure(project_dir) -> None:
    from umx.git_ops import GitCommitError, GitCommitResult

    runner = CliRunner()
    failure = GitCommitError(
        "git init commit failed: gpg failed to sign the data",
        GitCommitResult.failed_result(returncode=128, stderr="gpg failed to sign the data"),
    )

    with patch("umx.cli.init_project_memory", side_effect=failure):
        result = runner.invoke(main, ["init-project", "--cwd", str(project_dir)])

    assert result.exit_code != 0
    assert "git init commit failed: gpg failed to sign the data" in result.output


def test_cli_setup_remote_blocks_unsafe_bootstrap(project_dir, project_repo, tmp_path) -> None:
    from umx.git_ops import git_add_and_commit

    cfg = default_config()
    cfg.org = "memory-org"
    save_config(config_path(), cfg)

    fact_path = project_repo / "facts" / "topics" / "deploy.md"
    fact_path.parent.mkdir(parents=True, exist_ok=True)
    fact_path.write_text("# deploy\n\n## Facts\n- aws key AKIA1234567890ABCDEF\n")
    git_add_and_commit(project_repo, message="unsafe bootstrap")

    remote = tmp_path / "unsafe-bootstrap.git"
    subprocess.run(["git", "init", "--bare", str(remote)], capture_output=True, check=True)

    runner = CliRunner()
    with patch("umx.github_ops.gh_available", return_value=True), patch(
        "umx.github_ops.ensure_repo",
        return_value=str(remote),
    ):
        result = runner.invoke(main, ["setup-remote", "--cwd", str(project_dir), "--mode", "remote"])

    assert result.exit_code != 0
    assert "push safety blocked" in result.output


def test_cli_sync_returns_nonzero_when_push_fails(project_dir, project_repo, tmp_path, monkeypatch) -> None:
    from umx.git_ops import git_add_and_commit, git_push

    remote = tmp_path / "sync-push-fails.git"
    subprocess.run(["git", "init", "--bare", str(remote)], capture_output=True, check=True)
    subprocess.run(
        ["git", "-C", str(project_repo), "remote", "add", "origin", str(remote)],
        capture_output=True,
        check=True,
    )
    git_add_and_commit(project_repo, message="sync push fail baseline")
    git_push(project_repo)

    cfg = default_config()
    cfg.org = "memory-org"
    cfg.dream.mode = "remote"
    save_config(config_path(), cfg)

    sessions_dir = project_repo / "sessions" / "2026" / "01"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    (sessions_dir / "2026-01-15-sync-fail.jsonl").write_text('{"_meta":{"session_id":"2026-01-15-sync-fail"}}\n')

    monkeypatch.setattr("umx.git_ops.git_pull_rebase", lambda *args, **kwargs: True)
    monkeypatch.setattr("umx.git_ops.git_push", lambda *args, **kwargs: False)

    result = CliRunner().invoke(main, ["sync", "--cwd", str(project_dir)])

    assert result.exit_code != 0
    assert "push failed" in result.output
