from __future__ import annotations

import logging
from pathlib import Path

from umx.inject import build_subagent_handoff

logger = logging.getLogger(__name__)


def run(
    cwd: Path,
    parent_session_id: str,
    subagent_session_id: str | None = None,
    tool_name: str | None = None,
    objective: str | None = None,
    max_tokens: int | None = None,
) -> str | None:
    try:
        return build_subagent_handoff(
            cwd,
            parent_session_id=parent_session_id,
            subagent_session_id=subagent_session_id,
            tool=tool_name,
            objective=objective,
            max_tokens=max_tokens,
        )
    except Exception:
        logger.debug("subagent_start: handoff failed", exc_info=True)
        return None
