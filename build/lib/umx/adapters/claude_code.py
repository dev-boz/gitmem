"""Claude Code native memory adapter.

Reads from ~/.claude/projects/<path>/ — tool native memory at S:4.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from umx.models import Fact, MemoryType, Scope


class ClaudeCodeAdapter:
    """Adapter for Claude Code's native memory store."""

    tool_name = "claude-code"

    def read_native_memory(self, project_root: Path) -> list[Fact]:
        """Read facts from Claude Code's native memory.

        Claude Code stores project memory in ~/.claude/projects/<hash>/
        """
        claude_dir = Path.home() / ".claude"
        if not claude_dir.exists():
            return []

        facts: list[Fact] = []
        projects_dir = claude_dir / "projects"
        if not projects_dir.exists():
            return []

        # Walk project directories looking for memory files
        for project_dir in projects_dir.iterdir():
            if not project_dir.is_dir():
                continue
            facts.extend(self._read_project_dir(project_dir, project_root))

        return facts

    def _read_project_dir(
        self, project_dir: Path, project_root: Path
    ) -> list[Fact]:
        """Read memory from a single Claude Code project directory."""
        facts: list[Fact] = []
        now = datetime.now(timezone.utc)

        # Look for CLAUDE.md-style files
        for md_file in project_dir.glob("*.md"):
            try:
                content = md_file.read_text()
            except OSError:
                continue

            for line in content.splitlines():
                line = line.strip()
                if line.startswith("- ") or line.startswith("* "):
                    text = line.lstrip("-* ").strip()
                    if text and len(text) > 5:
                        facts.append(Fact(
                            id=Fact.generate_id(),
                            text=text,
                            scope=Scope.PROJECT_TEAM,
                            topic="general",
                            encoding_strength=4,
                            memory_type=MemoryType.EXPLICIT_SEMANTIC,
                            confidence=0.9,
                            source_tool=self.tool_name,
                            created=now,
                        ))

        # Look for JSON memory files
        for json_file in project_dir.glob("*.json"):
            try:
                data = json.loads(json_file.read_text())
            except (OSError, json.JSONDecodeError):
                continue

            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict) and "text" in item:
                        facts.append(Fact(
                            id=Fact.generate_id(),
                            text=item["text"],
                            scope=Scope.PROJECT_TEAM,
                            topic=item.get("topic", "general"),
                            encoding_strength=4,
                            memory_type=MemoryType.EXPLICIT_SEMANTIC,
                            confidence=item.get("confidence", 0.9),
                            source_tool=self.tool_name,
                            created=now,
                        ))

        return facts
