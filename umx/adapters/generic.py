"""Generic adapter for tools without specific adapters.

Reads CLAUDE.md, AGENTS.md, .cursorrules, and similar conventional files.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from umx.models import Fact, MemoryType, Scope


class GenericAdapter:
    """Generic adapter that reads conventional memory files."""

    tool_name = "generic"

    # Files commonly used as memory by various tools
    CONVENTIONAL_FILES = [
        "CLAUDE.md",
        "AGENTS.md",
        ".cursorrules",
        ".windsurfrules",
        "CONVENTIONS.md",
        "CODING_GUIDELINES.md",
    ]

    def read_native_memory(self, project_root: Path) -> list[Fact]:
        """Read facts from conventional project files."""
        facts: list[Fact] = []

        for filename in self.CONVENTIONAL_FILES:
            filepath = project_root / filename
            if filepath.exists():
                facts.extend(self._read_file(filepath, filename))

        return facts

    def _read_file(self, path: Path, filename: str) -> list[Fact]:
        """Extract facts from a conventional file."""
        facts: list[Fact] = []
        now = datetime.now(timezone.utc)

        try:
            content = path.read_text()
        except OSError:
            return []

        # Skip umx-managed sections
        content = re.sub(
            r"<!-- umx-start:.*?-->.*?<!-- umx-end -->",
            "",
            content,
            flags=re.DOTALL,
        )

        for line in content.splitlines():
            line = line.strip()
            if line.startswith("- ") or line.startswith("* "):
                text = line.lstrip("-* ").strip()
                if text and len(text) > 5 and not text.startswith("#"):
                    facts.append(Fact(
                        id=Fact.generate_id(),
                        text=text,
                        scope=Scope.PROJECT_TEAM,
                        topic="general",
                        encoding_strength=4,
                        memory_type=MemoryType.EXPLICIT_SEMANTIC,
                        confidence=0.85,
                        source_tool=f"file:{filename}",
                        created=now,
                    ))

        return facts
