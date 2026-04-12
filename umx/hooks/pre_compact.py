from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from umx.config import load_config
from umx.git_ops import git_add_and_commit, git_push
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

    # Emergency sync: commit all uncommitted facts
    try:
        committed = git_add_and_commit(
            repo_dir,
            message="umx: pre-compact emergency sync",
        )
        result["committed"] = committed
    except Exception:
        logger.debug("pre_compact: commit failed", exc_info=True)

    # Push if remote/hybrid mode
    try:
        cfg = load_config(config_path())
        mode = cfg.dream.mode
        if mode in ("remote", "hybrid") and result["committed"]:
            pushed = git_push(repo_dir)
            result["pushed"] = pushed
    except Exception:
        logger.debug("pre_compact: push failed", exc_info=True)

    return result
