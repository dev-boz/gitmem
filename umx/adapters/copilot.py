from __future__ import annotations

from pathlib import Path

from umx.adapters.generic import NativeMemoryAdapter
from umx.models import Fact


class CopilotAdapter(NativeMemoryAdapter):
    name = "copilot"

    def read_native_memory(self, project_root: Path) -> list[Fact]:
        """Read facts from GitHub Copilot's memory files.

        Copilot stores instructions in .github/copilot-instructions.md
        and the project root copilot-instructions.md.
        """
        facts: list[Fact] = []
        candidates = [
            project_root / ".github" / "copilot-instructions.md",
            project_root / "copilot-instructions.md",
        ]
        for path in candidates:
            if path.exists():
                facts.extend(self._parse_instructions(path))
        return facts

    def _parse_instructions(self, path: Path) -> list[Fact]:
        facts: list[Fact] = []
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return facts

        current_topic = "general"
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("# ") or stripped.startswith("## "):
                header = stripped.lstrip("# ").strip()
                current_topic = header.lower().replace(" ", "_")
                continue
            if not stripped.startswith("- ") and not stripped.startswith("* "):
                continue
            text = stripped[2:].strip()
            if len(text) < 10 or len(text) > 200:
                continue
            fact = self._make_fact(text, topic=current_topic, session=f"copilot-{path.stem}")
            if fact:
                facts.append(fact)
        return facts
