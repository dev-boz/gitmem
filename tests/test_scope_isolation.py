from __future__ import annotations

from datetime import timedelta
import subprocess
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from umx.cli import main
from umx.config import default_config, save_config
from umx.memory import add_fact
from umx.models import (
    ConsolidationStatus,
    Fact,
    MemoryType,
    Provenance,
    Scope,
    SourceType,
    Verification,
    utcnow,
)
from umx.scope import config_path, init_project_memory, project_memory_dir


def _set_origin(repo_dir: Path, url: str) -> None:
    existing = subprocess.run(
        ["git", "-C", str(repo_dir), "remote", "get-url", "origin"],
        capture_output=True,
        text=True,
        check=False,
    )
    command = ["git", "-C", str(repo_dir), "remote", "set-url", "origin", url]
    if existing.returncode != 0:
        command = ["git", "-C", str(repo_dir), "remote", "add", "origin", url]
    subprocess.run(command, capture_output=True, text=True, check=True)


def _make_fact(fact_id: str, text: str, *, topic: str = "shared") -> Fact:
    created = utcnow() - timedelta(days=8)
    return Fact(
        fact_id=fact_id,
        text=text,
        scope=Scope.PROJECT,
        topic=topic,
        encoding_strength=3,
        memory_type=MemoryType.EXPLICIT_SEMANTIC,
        verification=Verification.CORROBORATED,
        source_type=SourceType.LLM_INFERENCE,
        source_tool="test",
        source_session=f"session-{fact_id.lower()}",
        consolidation_status=ConsolidationStatus.STABLE,
        created=created,
        provenance=Provenance(extracted_by="test", sessions=[f"session-{fact_id.lower()}"]),
    )


def _init_project(tmp_path: Path, name: str) -> tuple[Path, Path]:
    project_dir = tmp_path / name
    project_dir.mkdir()
    (project_dir / ".git").mkdir()
    init_project_memory(project_dir)
    return project_dir, project_memory_dir(project_dir)


def _git_commit_all(repo_dir: Path, message: str) -> None:
    status = subprocess.run(
        ["git", "-C", str(repo_dir), "status", "--short"],
        capture_output=True,
        text=True,
        check=True,
    )
    if not status.stdout.strip():
        return
    subprocess.run(
        ["git", "-C", str(repo_dir), "add", "-A"],
        capture_output=True,
        text=True,
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repo_dir), "commit", "-m", message],
        capture_output=True,
        text=True,
        check=True,
    )


def test_scope_isolation_refuses_sync_for_misconfigured_project_github_remote(
    project_dir: Path,
    project_repo: Path,
) -> None:
    cfg = default_config()
    cfg.org = "memory-org"
    cfg.dream.mode = "remote"
    save_config(config_path(), cfg)

    _set_origin(project_repo, "https://github.com/memory-org/not-project.git")

    fetch_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    pull_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    push_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    with patch(
        "umx.git_ops.git_fetch",
        side_effect=lambda *args, **kwargs: fetch_calls.append((args, kwargs)) or True,
    ), patch(
        "umx.git_ops.git_pull_rebase",
        side_effect=lambda *args, **kwargs: pull_calls.append((args, kwargs)) or True,
    ), patch(
        "umx.git_ops.git_push",
        side_effect=lambda *args, **kwargs: push_calls.append((args, kwargs)) or True,
    ):
        result = CliRunner().invoke(main, ["sync", "--cwd", str(project_dir)])

    assert result.exit_code != 0
    assert "project memory repo GitHub origin does not match the expected sync target" in result.output
    assert "expected memory-org/project, found memory-org/not-project" in result.output
    assert fetch_calls == []
    assert pull_calls == []
    assert push_calls == []


def test_scope_isolation_refuses_cross_project_push_for_misconfigured_user_github_remote(
    umx_home: Path,
    tmp_path: Path,
    user_repo: Path,
) -> None:
    _git_commit_all(user_repo, "test: clean user baseline")
    cfg = default_config()
    cfg.org = "memory-org"
    save_config(config_path(), cfg)

    _set_origin(user_repo, "https://github.com/memory-org/not-umx-user.git")

    project_dir, repo = _init_project(tmp_path, "alpha")
    add_fact(
        repo,
        _make_fact("FACT_ALPHA", "Shared release process lives in docs/releases", topic="release"),
        auto_commit=False,
    )
    for index, repo_name in enumerate(("beta", "gamma"), start=1):
        _, other_repo = _init_project(tmp_path, repo_name)
        add_fact(
            other_repo,
            _make_fact(
                f"FACT_{index}",
                "Shared release process lives in docs/releases",
                topic="release",
            ),
            auto_commit=False,
        )

    fetch_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    with patch(
        "umx.git_ops.git_fetch",
        side_effect=lambda *args, **kwargs: fetch_calls.append((args, kwargs)) or True,
    ):
        result = CliRunner().invoke(
            main,
            [
                "propose",
                "--cwd",
                str(project_dir),
                "--cross-project",
                "--proposal-key",
                "shared release process lives in docs/releases",
                "--push",
            ],
        )

    assert result.exit_code != 0
    assert "user memory repo GitHub origin does not match the expected proposal push target" in result.output
    assert "expected memory-org/umx-user, found memory-org/not-umx-user" in result.output
    assert fetch_calls == []


def test_scope_isolation_requires_proposal_key_for_cross_project_proposals(
    umx_home: Path,
    tmp_path: Path,
) -> None:
    _ = umx_home
    project_dir, _ = _init_project(tmp_path, "alpha")

    result = CliRunner().invoke(
        main,
        ["propose", "--cwd", str(project_dir), "--cross-project"],
    )

    assert result.exit_code != 0
    assert "--proposal-key is required for propose" in result.output
