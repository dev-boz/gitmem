from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from umx.session_runtime import record_session_event

logger = logging.getLogger(__name__)


def run(
    cwd: Path,
    session_id: str,
    event: dict[str, Any],
    tool: str | None = None,
    context_window_tokens: int | None = None,
) -> dict[str, Any] | None:
    try:
        return record_session_event(
            cwd,
            session_id,
            event,
            tool=tool,
            context_window_tokens=context_window_tokens,
            persist=True,
            auto_commit=False,
        )
    except Exception:
        logger.debug("assistant_output: failed to record event", exc_info=True)
        return None
