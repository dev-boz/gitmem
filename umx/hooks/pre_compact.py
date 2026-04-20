from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from umx.config import load_config
from umx.git_ops import (
    GitSignedHistoryError,
    assert_signed_commit_range,
    changed_paths,
    git_add_and_commit,
    git_commit_failure_message,
    git_fetch,
    git_push,
)
from umx.push_safety import PushSafetyError, assert_push_safe
from umx.scope import config_path, find_project_root, project_memory_dir

logger = logging.getLogger(__name__)


def run(
    cwd: Path,
    session_id: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {"committed": False, "pushed": False, "error": None}

    try:
        root = find_project_root(cwd)
        repo_dir = project_memory_dir(root)
    except Exception:
        logger.debug("pre_compact: no project root found for %s", cwd)
        return result

    mode = "local"

    # Emergency sync: commit all uncommitted facts
    try:
        cfg = load_config(config_path())
        mode = cfg.dream.mode
        if mode in ("remote", "hybrid"):
            session_paths = [
                path
                for path in changed_paths(repo_dir, prefix="sessions/")
                if path.suffix == ".jsonl"
            ]
            committed = (
                git_add_and_commit(
                    repo_dir,
                    paths=session_paths,
                    message="umx: pre-compact session sync",
                    config=cfg,
                )
                if session_paths
                else None
            )
        else:
            committed = git_add_and_commit(
                repo_dir,
                message="umx: pre-compact emergency sync",
                config=cfg,
            )
        if committed is not None and committed.failed:
            result["error"] = git_commit_failure_message(committed, context="commit failed")
            return result
        result["committed"] = bool(committed and committed.committed)
    except Exception:
        logger.debug("pre_compact: commit failed", exc_info=True)

    # Push if remote/hybrid mode
    try:
        if mode in ("remote", "hybrid") and result["committed"]:
            from umx.github_ops import GitHubRemoteIdentityError, assert_expected_github_origin

            try:
                assert_expected_github_origin(
                    repo_dir,
                    config_org=cfg.org,
                    repo_label="project memory repo",
                    operation="pre-compact sync",
                )
            except GitHubRemoteIdentityError as exc:
                result["error"] = str(exc)
                return result
            if not git_fetch(repo_dir):
                result["error"] = "fetch failed"
                return result
            assert_push_safe(
                repo_dir,
                project_root=root,
                base_ref="origin/main",
                branch="main",
                config=cfg,
                include_bridge=True,
            )
            assert_signed_commit_range(
                repo_dir,
                base_ref="origin/main",
                head_ref="HEAD",
                config=cfg,
                operation="pre-compact sync",
            )
            pushed = git_push(repo_dir)
            result["pushed"] = pushed
            if not pushed:
                result["error"] = "push failed"
    except PushSafetyError as exc:
        result["error"] = str(exc)
    except GitSignedHistoryError as exc:
        result["error"] = str(exc)
    except Exception:
        logger.debug("pre_compact: push failed", exc_info=True)

    return result
