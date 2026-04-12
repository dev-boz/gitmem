from __future__ import annotations

from pathlib import Path

from umx.identity import generate_fact_id
from umx.models import (
    ConsolidationStatus,
    Fact,
    MemoryType,
    Provenance,
    Scope,
    SourceType,
    Verification,
)
from umx.redaction import redact_candidate_fact_text


class NativeMemoryAdapter:
    name = "generic"

    def read_native_memory(self, _project_root: Path) -> list[Fact]:
        return []

    def _make_fact(
        self,
        text: str,
        topic: str = "general",
        *,
        scope: Scope = Scope.PROJECT,
        session: str = "adapter",
    ) -> Fact | None:
        redacted = redact_candidate_fact_text(text)
        if not redacted or len(redacted.strip()) < 5:
            return None
        return Fact(
            fact_id=generate_fact_id(),
            text=redacted,
            scope=scope,
            topic=topic,
            encoding_strength=3,
            memory_type=MemoryType.IMPLICIT,
            verification=Verification.SELF_REPORTED,
            source_type=SourceType.TOOL_OUTPUT,
            confidence=0.6,
            source_tool=self.name,
            source_session=session,
            consolidation_status=ConsolidationStatus.FRAGILE,
            provenance=Provenance(extracted_by=f"adapter-{self.name}", sessions=[session]),
        )
