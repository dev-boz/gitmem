"""Generic shim for tools without specific shim support.

Writes memory to a standard location.
"""

from __future__ import annotations

from pathlib import Path

from umx.inject import inject_for_tool


def shim_generic(
    cwd: Path,
    tool: str,
    max_tokens: int | None = None,
) -> Path | None:
    """Write memory context for any tool.

    Creates .umx/inject/<tool>-context.md.
    Returns the path to the written file, or None if no content.
    """
    umx_dir = cwd / ".umx" / "inject"
    output = umx_dir / f"{tool}-context.md"
    content = inject_for_tool(
        cwd, tool=tool, max_tokens=max_tokens, output_path=output
    )
    return output if content else None
