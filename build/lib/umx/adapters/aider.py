"""Aider native memory adapter.

Reads from .aider.tags.cache and session logs.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from umx.models import Fact, MemoryType, Scope


class AiderAdapter:
    """Adapter for Aider's native memory."""

    tool_name = "aider"

    def read_native_memory(self, project_root: Path) -> list[Fact]:
        """Read facts from Aider's memory files."""
        facts: list[Fact] = []

        # Read .aider.tags.cache
        tags_cache = project_root / ".aider.tags.cache"
        if tags_cache.exists():
            facts.extend(self._read_tags_cache(tags_cache))

        # Read aider chat history
        chat_history = project_root / ".aider.chat.history.md"
        if chat_history.exists():
            facts.extend(self._read_chat_history(chat_history))

        # Read aider input history
        input_history = project_root / ".aider.input.history"
        if input_history.exists():
            facts.extend(self._read_input_history(input_history))

        return facts

    def _read_tags_cache(self, path: Path) -> list[Fact]:
        """Read facts from Aider's tags cache."""
        # Tags cache is primarily for code navigation, not memory
        # But can indicate project structure knowledge
        return []

    def _read_chat_history(self, path: Path) -> list[Fact]:
        """Extract notable facts from chat history."""
        facts: list[Fact] = []
        now = datetime.now(timezone.utc)

        try:
            content = path.read_text(errors="replace")
        except OSError:
            return []

        # Look for explicit memory-worthy patterns in assistant responses
        patterns = [
            (r"(?:note|remember|important):\s*(.+?)(?:\n|$)", 0.8),
            (r"(?:configured|set up|using)\s+(.+?)(?:\n|$)", 0.7),
        ]

        for pattern, confidence in patterns:
            for match in re.finditer(pattern, content, re.IGNORECASE):
                text = match.group(1).strip()
                if 10 < len(text) < 200:
                    facts.append(Fact(
                        id=Fact.generate_id(),
                        text=text,
                        scope=Scope.PROJECT_TEAM,
                        topic="general",
                        encoding_strength=3,
                        memory_type=MemoryType.EXPLICIT_EPISODIC,
                        confidence=confidence,
                        source_tool=self.tool_name,
                        created=now,
                    ))

        return facts

    def _read_input_history(self, path: Path) -> list[Fact]:
        """Extract patterns from input history."""
        # Input history could reveal common commands/patterns
        # but is low-strength implicit memory
        return []
