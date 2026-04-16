from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from umx.config import load_config
from umx.dream.gates import should_dream
from umx.dream.pipeline import DreamPipeline
from umx.git_ops import git_add_and_commit, git_commit_failure_message
from umx.scope import config_path, find_project_root, project_memory_dir
from umx.session_runtime import record_session_event
from umx.sessions import archive_sessions, write_session

logger = logging.getLogger(__name__)


def run(
    cwd: Path,
    session_id: str,
    tool: str | None = None,
    events: list[dict] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "session_written": False,
        "archived_sessions": 0,
        "dream_triggered": False,
        "dream_result": None,
        "error": None,
    }

    try:
        root = find_project_root(cwd)
        repo_dir = project_memory_dir(root)
    except Exception:
        logger.debug("session_end: no project root found for %s", cwd)
        return result

    # Write session if events provided
    if events:
        try:
            cfg = load_config(config_path())
            meta: dict[str, Any] = {"session_id": session_id}
            if tool:
                meta["tool"] = tool
            write_session(repo_dir, meta=meta, events=events, config=cfg, auto_commit=False)
            archive_result = archive_sessions(repo_dir, config=cfg)
            result["archived_sessions"] = int(archive_result.get("archived_sessions", 0))
            commit_result = git_add_and_commit(
                repo_dir,
                message=f"umx: session {session_id}",
                config=cfg,
            )
            if commit_result.failed:
                raise RuntimeError(
                    git_commit_failure_message(commit_result, context="commit failed")
                )
            for event in events:
                record_session_event(
                    cwd,
                    session_id,
                    event,
                    tool=tool,
                    persist=False,
                    auto_commit=False,
                )
            result["session_written"] = True
        except Exception as exc:
            result["error"] = str(exc)
            logger.debug("session_end: failed to write session", exc_info=True)

    # Check dream gates and run pipeline if met
    try:
        if should_dream(repo_dir):
            result["dream_triggered"] = True
            try:
                dream_result = DreamPipeline(cwd).run()
                result["dream_result"] = {
                    "status": dream_result.status,
                    "added": dream_result.added,
                    "pruned": dream_result.pruned,
                    "message": dream_result.message,
                }
            except Exception as exc:
                result["dream_result"] = {"status": "error", "error": str(exc)}
    except Exception:
        logger.debug("session_end: dream gate check failed", exc_info=True)

    return result
