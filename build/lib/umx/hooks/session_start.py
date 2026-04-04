"""Hook handlers for session lifecycle events.

Session start: inject memory into tool context.
"""

from __future__ import annotations

from pathlib import Path

from umx.dream.notice import read_notice
from umx.inject import collect_facts_for_injection, build_injection_block
from umx.models import UmxConfig


def on_session_start(
    cwd: Path,
    tool: str,
    max_tokens: int | None = None,
    config: UmxConfig | None = None,
) -> str:
    """Handle session start: inject memory context.

    Returns the injection block to prepend to the session.
    """
    # Check for notices
    umx_dir = cwd / ".umx"
    notice = read_notice(umx_dir) if umx_dir.exists() else None

    # Collect and build injection
    facts = collect_facts_for_injection(cwd, tool=tool, config=config)
    block = build_injection_block(facts, max_tokens=max_tokens, config=config)

    if notice:
        block = f"⚠️ umx notice: {notice}\n\n{block}"

    return block
