from __future__ import annotations

from pathlib import Path

from umx.adapters.generic import NativeMemoryAdapter
from umx.models import Fact


class AiderAdapter(NativeMemoryAdapter):
    name = "aider"

    def read_native_memory(self, project_root: Path) -> list[Fact]:
        """Read facts from Aider's native memory files.

        Aider stores context in .aider.chat.history.md and may have
        conventions in .aider.conf.yml or .aiderignore.
        """
        facts: list[Fact] = []
        history = project_root / ".aider.chat.history.md"
        if history.exists():
            facts.extend(self._parse_history(history))
        return facts

    def _parse_history(self, path: Path) -> list[Fact]:
        facts: list[Fact] = []
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return facts

        for line in content.splitlines():
            stripped = line.strip()
            if not stripped.startswith("> "):
                continue
            text = stripped[2:].strip()
            if len(text) < 10 or len(text) > 200:
                continue
            # Aider quotes are user instructions — treat as conventions
            fact = self._make_fact(text, topic="conventions", session="aider-history")
            if fact:
                facts.append(fact)
        return facts
