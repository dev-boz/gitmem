"""Hook handler for session end.

Collects tool memory and queues dream if trigger conditions met.
"""

from __future__ import annotations

from pathlib import Path

from umx.dream.gates import increment_session_count, should_dream
from umx.dream.pipeline import DreamPipeline
from umx.memory import load_config
from umx.models import UmxConfig


def on_session_end(
    cwd: Path,
    tool: str,
    config: UmxConfig | None = None,
) -> dict:
    """Handle session end: collect memory and possibly trigger dream.

    Returns a status dict.
    """
    umx_dir = cwd / ".umx"

    if config is None:
        config = load_config(umx_dir) if umx_dir.exists() else UmxConfig()

    # Increment session count
    session_count = 0
    if umx_dir.exists():
        session_count = increment_session_count(umx_dir)

    # Check if dream should run
    dream_triggered = False
    if umx_dir.exists() and should_dream(umx_dir):
        pipeline = DreamPipeline(cwd, config=config)
        status = pipeline.run()
        dream_triggered = True

    return {
        "tool": tool,
        "session_count": session_count,
        "dream_triggered": dream_triggered,
    }
