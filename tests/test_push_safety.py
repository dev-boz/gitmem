from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.secret_literals import AWS_ACCESS_KEY_ID
from umx.config import default_config
from umx.git_ops import git_add_and_commit, git_push
from umx.push_safety import PushSafetyError, assert_push_safe


def _connect_origin(repo_dir: Path, remote_dir: Path) -> None:
    subprocess.run(["git", "init", "--bare", str(remote_dir)], capture_output=True, check=True)
    subprocess.run(
        ["git", "-C", str(repo_dir), "remote", "add", "origin", str(remote_dir)],
        capture_output=True,
        check=True,
    )


def _push_baseline(repo_dir: Path) -> None:
    git_add_and_commit(repo_dir, message="push safety baseline")
    git_push(repo_dir)


def test_push_safety_blocks_committed_fact_markdown(
    project_dir: Path,
    project_repo: Path,
    tmp_path: Path,
) -> None:
    remote = tmp_path / "push-safety-facts.git"
    _connect_origin(project_repo, remote)
    _push_baseline(project_repo)

    fact_path = project_repo / "facts" / "topics" / "deploy.md"
    fact_path.parent.mkdir(parents=True, exist_ok=True)
    fact_path.write_text(f"# deploy\n\n## Facts\n- aws key {AWS_ACCESS_KEY_ID}\n")
    git_add_and_commit(project_repo, message="commit unsafe fact")

    with pytest.raises(PushSafetyError) as exc_info:
        assert_push_safe(
            project_repo,
            project_root=project_dir,
            config=default_config(),
            include_bridge=True,
        )

    assert "facts/topics/deploy.md" in str(exc_info.value)
    reports = list((project_repo / "local" / "quarantine").glob("push-safety-*.json"))
    assert reports


def test_push_safety_blocks_raw_session_push(
    project_dir: Path,
    project_repo: Path,
    tmp_path: Path,
) -> None:
    remote = tmp_path / "push-safety-sessions.git"
    _connect_origin(project_repo, remote)
    _push_baseline(project_repo)

    session_path = project_repo / "sessions" / "2026" / "01" / "2026-01-15-raw.jsonl"
    session_path.parent.mkdir(parents=True, exist_ok=True)
    session_path.write_text('{"_meta":{"session_id":"2026-01-15-raw"},"content":"raw secret"}\n')
    git_add_and_commit(project_repo, message="commit raw session")

    cfg = default_config()
    cfg.sessions.redaction = "none"

    with pytest.raises(PushSafetyError) as exc_info:
        assert_push_safe(
            project_repo,
            project_root=project_dir,
            config=cfg,
            include_bridge=True,
        )

    assert "raw-session-push" in str(exc_info.value)


def test_push_safety_blocks_bridge_targets(
    project_dir: Path,
    project_repo: Path,
    tmp_path: Path,
) -> None:
    remote = tmp_path / "push-safety-bridge.git"
    _connect_origin(project_repo, remote)
    _push_baseline(project_repo)

    cfg = default_config()
    cfg.bridge.enabled = True
    (project_dir / "CLAUDE.md").write_text("Bearer token Bearer abcdefghijklmnopqrstuvwx\n")

    with pytest.raises(PushSafetyError) as exc_info:
        assert_push_safe(
            project_repo,
            project_root=project_dir,
            config=cfg,
            include_bridge=True,
        )

    assert "CLAUDE.md" in str(exc_info.value)


def test_push_safety_fails_closed_on_invalid_scan_config(
    project_dir: Path,
    project_repo: Path,
    tmp_path: Path,
) -> None:
    remote = tmp_path / "push-safety-scan-error.git"
    _connect_origin(project_repo, remote)
    _push_baseline(project_repo)

    memory_path = project_repo / "meta" / "MEMORY.md"
    memory_path.write_text("# umx memory index\napi_key = test\n")
    git_add_and_commit(project_repo, message="commit memory change")

    cfg = default_config()
    cfg.sessions.redaction_patterns = ["["]

    with pytest.raises(PushSafetyError) as exc_info:
        assert_push_safe(
            project_repo,
            project_root=project_dir,
            config=cfg,
            include_bridge=True,
        )

    assert "scan-error" in str(exc_info.value)
    reports = list((project_repo / "local" / "quarantine").glob("push-safety-*.json"))
    assert reports


def test_push_safety_fails_closed_when_base_ref_is_missing(
    project_dir: Path,
    project_repo: Path,
) -> None:
    fact_path = project_repo / "facts" / "topics" / "deploy.md"
    fact_path.parent.mkdir(parents=True, exist_ok=True)
    fact_path.write_text("# deploy\n\n## Facts\n- api_key = test\n")
    git_add_and_commit(project_repo, message="commit without remote base")

    with pytest.raises(PushSafetyError) as exc_info:
        assert_push_safe(
            project_repo,
            project_root=project_dir,
            config=default_config(),
            include_bridge=True,
        )

    assert "scan-error" in str(exc_info.value)
