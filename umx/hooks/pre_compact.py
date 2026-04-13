from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from umx.config import load_config
from umx.git_ops import changed_paths, git_add_and_commit, git_push
from umx.scope import config_path, find_project_root, project_memory_dir

logger = logging.getLogger(__name__)


def run(
    cwd: Path,
    session_id: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {"committed": False, "pushed": False}

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
                )
                if session_paths
                else False
            )
        else:
            committed = git_add_and_commit(
                repo_dir,
                message="umx: pre-compact emergency sync",
            )
        result["committed"] = committed
    except Exception:
        logger.debug("pre_compact: commit failed", exc_info=True)

    # Push if remote/hybrid mode
    try:
        if mode in ("remote", "hybrid") and result["committed"]:
            pushed = git_push(repo_dir)
            result["pushed"] = pushed
    except Exception:
        logger.debug("pre_compact: push failed", exc_info=True)

    return result
