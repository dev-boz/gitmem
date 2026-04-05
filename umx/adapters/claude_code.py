"""Claude Code native memory adapter.

Reads from ~/.claude/projects/<path>/ — tool native memory at S:4.
Only reads from directories that match the current project_root to avoid
cross-project fact leakage.
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
        where <hash> is derived from the project path. Only directories
        whose name matches the project path are read.
        """
        claude_dir = Path.home() / ".claude"
        if not claude_dir.exists():
            return []

        facts: list[Fact] = []
        projects_dir = claude_dir / "projects"
        if not projects_dir.exists():
            return []

        resolved_root = project_root.resolve()

        for project_dir in projects_dir.iterdir():
            if not project_dir.is_dir():
                continue
            if not self._matches_project(project_dir, resolved_root):
                continue
            facts.extend(self._read_project_dir(project_dir))

        return facts

    @staticmethod
    def _matches_project(claude_project_dir: Path, project_root: Path) -> bool:
        """Check if a Claude project directory belongs to the current project.

        Claude Code names project directories using the project path with
        slashes replaced (e.g. "home-user-myproject" for /home/user/myproject).
        We match the directory name against the project root path.
        """
        dir_name = claude_project_dir.name

        # Strategy 1: directory name is the project path with / → -
        normalised = str(project_root).strip("/").replace("/", "-")
        if dir_name == normalised:
            return True

        # Strategy 2: directory name contains the project folder name
        if project_root.name and project_root.name in dir_name:
            # Check for a metadata file that records the source path
            for meta_file in ("project.json", ".project"):
                meta_path = claude_project_dir / meta_file
                if meta_path.exists():
                    try:
                        content = meta_path.read_text()
                        if str(project_root) in content:
                            return True
                    except OSError:
                        pass

        return False

    def _read_project_dir(self, project_dir: Path) -> list[Fact]:
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
            if json_file.name in ("project.json", ".project"):
                continue  # skip metadata
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
