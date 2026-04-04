"""Legacy file bridge for CLAUDE.md, AGENTS.md, .cursorrules.

Writes condensed memory within bounded markers to preserve compatibility
with tools that already read those files.
"""

from __future__ import annotations

import re
from pathlib import Path

from umx.memory import load_all_facts
from umx.models import Scope

UMX_START_MARKER = "<!-- umx-start: do not edit manually -->"
UMX_END_MARKER = "<!-- umx-end -->"


def write_bridge(
    project_root: Path,
    target_files: list[str] | None = None,
    max_facts: int = 20,
) -> list[Path]:
    """Write condensed memory to legacy files within markers.

    Args:
        project_root: Project root directory.
        target_files: Files to write to. Defaults to CLAUDE.md, AGENTS.md.
        max_facts: Maximum number of facts to include.

    Returns:
        List of files that were written/updated.
    """
    if target_files is None:
        target_files = ["CLAUDE.md", "AGENTS.md"]

    umx_dir = project_root / ".umx"
    facts = load_all_facts(umx_dir, Scope.PROJECT_TEAM)

    # Sort by strength descending, take top N
    facts.sort(key=lambda f: -f.encoding_strength)
    selected = facts[:max_facts]

    if not selected:
        return []

    # Build the bridge content
    lines = [UMX_START_MARKER]
    for fact in selected:
        lines.append(f"- {fact.text}")
    lines.append(UMX_END_MARKER)
    bridge_content = "\n".join(lines)

    written: list[Path] = []
    for filename in target_files:
        filepath = project_root / filename
        written_path = _update_file_with_bridge(filepath, bridge_content)
        if written_path:
            written.append(written_path)

    return written


def _update_file_with_bridge(
    filepath: Path,
    bridge_content: str,
) -> Path | None:
    """Update a single file with bridge content.

    If file exists, replaces content between markers.
    If file doesn't exist, creates it with just the bridge content.
    """
    if filepath.exists():
        content = filepath.read_text()
        # Replace existing bridge section
        pattern = re.compile(
            re.escape(UMX_START_MARKER) + r".*?" + re.escape(UMX_END_MARKER),
            re.DOTALL,
        )
        if pattern.search(content):
            new_content = pattern.sub(bridge_content, content)
        else:
            # Append bridge section
            new_content = content.rstrip() + "\n\n" + bridge_content + "\n"
        filepath.write_text(new_content)
    else:
        filepath.write_text(bridge_content + "\n")

    return filepath


def remove_bridge(project_root: Path, target_files: list[str] | None = None) -> None:
    """Remove umx bridge sections from legacy files."""
    if target_files is None:
        target_files = ["CLAUDE.md", "AGENTS.md"]

    for filename in target_files:
        filepath = project_root / filename
        if not filepath.exists():
            continue

        content = filepath.read_text()
        pattern = re.compile(
            r"\n?" + re.escape(UMX_START_MARKER) + r".*?" + re.escape(UMX_END_MARKER) + r"\n?",
            re.DOTALL,
        )
        new_content = pattern.sub("", content)
        filepath.write_text(new_content)
