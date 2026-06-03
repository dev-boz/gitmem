from __future__ import annotations

from pathlib import Path

from umx.adapters.generic import NativeMemoryAdapter
from umx.gemini_capture import list_gemini_sessions, parse_gemini_session
from umx.models import Fact


class GeminiAdapter(NativeMemoryAdapter):
    name = "gemini"

    def read_native_memory(self, project_root: Path) -> list[Fact]:
        """Read facts from Gemini CLI's native session store."""
        facts: list[Fact] = []
        for session_path in list_gemini_sessions(project_root=project_root):
            transcript = parse_gemini_session(session_path)
            if not transcript.events:
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
