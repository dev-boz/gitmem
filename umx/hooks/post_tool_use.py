from __future__ import annotations

import logging
from pathlib import Path

from umx.budget import estimate_tokens
from umx.inject import build_injection_block
from umx.search import advance_session_state
from umx.scope import find_project_root, project_memory_dir

logger = logging.getLogger(__name__)


def run(
    cwd: Path,
    tool_name: str | None = None,
    file_paths: list[str] | None = None,
    session_id: str | None = None,
    context_window_tokens: int | None = None,
) -> str | None:
    if not file_paths:
        return None

    try:
        if session_id:
            observed_text = " ".join(part for part in [tool_name or "", *file_paths] if part)
            advance_session_state(
                project_memory_dir(find_project_root(cwd)),
                session_id,
                tool=tool_name,
                observed_tokens=estimate_tokens(observed_text) if observed_text else None,
                context_window_tokens=context_window_tokens,
            )
        block = build_injection_block(
            cwd,
            tool=tool_name,
            file_paths=file_paths,
            session_id=session_id,
            injection_point="post_tool",
            context_window_tokens=context_window_tokens,
        )
        return block
    except Exception:
        logger.debug("post_tool_use: injection failed", exc_info=True)
        return None
