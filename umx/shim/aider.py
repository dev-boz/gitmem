from __future__ import annotations

from pathlib import Path

from umx.inject import build_injection_block


def generate_aider_prompt(cwd: Path, max_tokens: int = 4000) -> str:
    """Generate memory injection for aider's --message-file or --read flag.

    Aider reads .aider.conf.yml and supports --read to prepend files.
    This generates the memory block that should be prepended.
    """
    return build_injection_block(cwd, tool="aider", max_tokens=max_tokens)


def write_aider_memory_file(
    cwd: Path, output_path: Path | None = None, max_tokens: int = 4000
) -> Path:
    """Write memory to a file suitable for aider --read.

    Default output: .umx-aider-context.md in the project root.
    """
    if output_path is None:
        output_path = cwd / ".umx-aider-context.md"
    content = generate_aider_prompt(cwd, max_tokens=max_tokens)
    output_path.write_text(content)
    return output_path


def run(cwd: Path | None = None, max_tokens: int = 4000) -> str:
    """Main entry point for aider shim."""
    cwd = cwd or Path.cwd()
    return generate_aider_prompt(cwd, max_tokens=max_tokens)
