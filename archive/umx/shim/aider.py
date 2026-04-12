"""Aider shim wrapper.

Prepends memory to Aider's config at launch.
"""

from __future__ import annotations

from pathlib import Path

from umx.inject import inject_for_tool


def shim_aider(cwd: Path, max_tokens: int | None = None) -> Path | None:
    """Write memory context for Aider.

    Creates .aider/memory-context.md that Aider can read.
    Returns the path to the written file, or None if no content.
    """
    output = cwd / ".aider" / "memory-context.md"
    content = inject_for_tool(cwd, tool="aider", max_tokens=max_tokens, output_path=output)
    return output if content else None
