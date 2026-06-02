from __future__ import annotations

from pathlib import Path

from umx.adapters.generic import NativeMemoryAdapter
from umx.copilot_capture import list_copilot_sessions, parse_copilot_session
from umx.models import Fact


class CopilotAdapter(NativeMemoryAdapter):
    name = "copilot"

    def read_native_memory(self, project_root: Path) -> list[Fact]:
        """Read facts from GitHub Copilot's memory files.

        Copilot stores project guidance in copilot-instructions markdown files
        and session-state logs under ~/.copilot/session-state/.
        """
        facts: list[Fact] = []
        candidates = [
            project_root / ".github" / "copilot-instructions.md",
            project_root / "copilot-instructions.md",
        ]
        for path in candidates:
            if path.exists():
                facts.extend(self._parse_instructions(path))

        for session_path in list_copilot_sessions():
            transcript = parse_copilot_session(session_path)
            if not transcript.events or not self._path_matches_project(transcript.cwd, project_root):
                continue
            facts.extend(
                self._facts_from_transcript_events(
                    project_root,
                    session=transcript.umx_session_id,
                    events=transcript.events,
                    encoding_context={"native_store_path": str(session_path)},
                )
            )
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
