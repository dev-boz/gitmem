from __future__ import annotations

import logging
from pathlib import Path

from umx.config import load_config
from umx.dream.gates import increment_session_count
from umx.git_ops import git_fetch, git_pull_rebase, safety_sweep
from umx.inject import inject_for_tool
from umx.search import ensure_session_state
from umx.scope import config_path, find_project_root, project_memory_dir

logger = logging.getLogger(__name__)


def run(
    cwd: Path,
    tool: str | None = None,
    session_id: str | None = None,
    max_tokens: int = 4000,
    context_window_tokens: int | None = None,
) -> str | None:
    try:
        root = find_project_root(cwd)
        repo_dir = project_memory_dir(root)
    except Exception:
        logger.debug("session_start: no project root found for %s", cwd)
        return None
    cfg = load_config(config_path())

    # Startup sweep: commit any uncommitted session files from crashed runs
    try:
        safety_sweep(repo_dir)
    except Exception:
        logger.debug("session_start: safety sweep failed", exc_info=True)

    # Pull latest if remote/hybrid mode
    try:
        mode = cfg.dream.mode
        if mode in ("remote", "hybrid"):
            git_fetch(repo_dir)
            git_pull_rebase(repo_dir)
    except Exception:
        logger.debug("session_start: remote sync failed", exc_info=True)

    # Log start to dream state
    try:
        increment_session_count(repo_dir)
    except Exception:
        logger.debug("session_start: failed to increment session count", exc_info=True)

    # Generate injection block
    try:
        if session_id:
            ensure_session_state(
                repo_dir,
                session_id,
                tool=tool,
                context_window_tokens=context_window_tokens,
                avg_tokens_per_turn=cfg.inject.turn_token_estimate,
            )
        block = inject_for_tool(
            cwd,
            tool=tool,
            max_tokens=max_tokens,
            session_id=session_id,
            injection_point="session_start",
            context_window_tokens=context_window_tokens,
        )
        return block
    except Exception:
        logger.debug("session_start: injection failed", exc_info=True)
        return None
