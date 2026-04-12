from __future__ import annotations

from pathlib import Path

from umx.inject import build_injection_block


def generate_prompt(
    cwd: Path, tool: str | None = None, max_tokens: int = 4000
) -> str:
    """Generate memory injection for any tool.

    Can be used as:
    - Piped into tool: `umx shim generic | tool-name`
    - Written to file: `umx shim generic --output context.md`
    - Read programmatically
    """
    return build_injection_block(cwd, tool=tool, max_tokens=max_tokens)


def write_context_file(
    cwd: Path,
    output_path: Path,
    tool: str | None = None,
    max_tokens: int = 4000,
) -> Path:
    """Write memory context to a file."""
    content = generate_prompt(cwd, tool=tool, max_tokens=max_tokens)
    output_path.write_text(content)
    return output_path


def run(
    cwd: Path | None = None, tool: str | None = None, max_tokens: int = 4000
) -> str:
    """Main entry point for generic shim."""
    cwd = cwd or Path.cwd()
    return generate_prompt(cwd, tool=tool, max_tokens=max_tokens)
