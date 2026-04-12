from __future__ import annotations

import logging
from pathlib import Path

from umx.budget import estimate_tokens
from umx.config import load_config
from umx.inject import build_injection_block
from umx.search import advance_session_state
from umx.scope import find_project_root, project_memory_dir
from umx.scope import config_path

logger = logging.getLogger(__name__)


def run(
    cwd: Path,
    tool_name: str | None = None,
    command_text: str | None = None,
    file_paths: list[str] | None = None,
    session_id: str | None = None,
    max_tokens: int | None = None,
    context_window_tokens: int | None = None,
) -> str | None:
    try:
        cfg = load_config(config_path())
        if session_id:
            observed_text = " ".join(part for part in [tool_name or "", command_text or "", *(file_paths or [])] if part)
            advance_session_state(
                project_memory_dir(find_project_root(cwd)),
                session_id,
                tool=tool_name,
                observed_tokens=estimate_tokens(observed_text) if observed_text else None,
                avg_tokens_per_turn=cfg.inject.turn_token_estimate,
                context_window_tokens=context_window_tokens,
            )
        block = build_injection_block(
            cwd,
            tool=tool_name,
            file_paths=file_paths,
            max_tokens=max_tokens or cfg.inject.pre_tool_max_tokens,
            session_id=session_id,
            injection_point="pre_tool",
            command_text=command_text,
            context_window_tokens=context_window_tokens,
        )
        return block
    except Exception:
        logger.debug("pre_tool_use: injection failed", exc_info=True)
        return None
