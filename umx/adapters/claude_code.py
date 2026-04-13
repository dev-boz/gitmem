from __future__ import annotations

import json
from pathlib import Path

from umx.adapters.generic import NativeMemoryAdapter
from umx.models import Fact


class ClaudeCodeAdapter(NativeMemoryAdapter):
    name = "claude-code"

    def read_native_memory(self, project_root: Path) -> list[Fact]:
        """Read facts from Claude Code's native memory files.

        Claude Code stores project memory in ~/.claude/projects/<hash>/
        as JSONL files or markdown. We look for CLAUDE.md in the project
        root and ~/.claude/ directories.
        """
        facts: list[Fact] = []
        candidates: list[Path] = []

        # Project-local CLAUDE.md
        local_claude = project_root / "CLAUDE.md"
        if local_claude.exists():
            candidates.append(local_claude)

        # User-level Claude config
        claude_home = Path.home() / ".claude"
        if claude_home.is_dir():
            # Global user-level CLAUDE.md
            global_claude = claude_home / "CLAUDE.md"
            if global_claude.exists():
                candidates.append(global_claude)

            # Look for project-specific memory
            for proj_dir in claude_home.glob("projects/*/"):
                memory_file = proj_dir / "CLAUDE.md"
                if memory_file.exists():
                    candidates.append(memory_file)

        for path in candidates:
            facts.extend(self._parse_claude_md(path))
        return facts

    def _parse_claude_md(self, path: Path) -> list[Fact]:
        """Extract factual lines from a CLAUDE.md file."""
        facts: list[Fact] = []
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return facts

        current_topic = "general"
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("# "):
                current_topic = stripped[2:].strip().lower().replace(" ", "_")
                continue
            if stripped.startswith("## "):
                current_topic = stripped[3:].strip().lower().replace(" ", "_")
                continue
            if not stripped.startswith("- ") and not stripped.startswith("* "):
                continue
            text = stripped[2:].strip()
            if len(text) < 10 or len(text) > 200:
                continue
            fact = self._make_fact(text, topic=current_topic, session=f"claude-{path.stem}")
            if fact:
                facts.append(fact)
        return facts
