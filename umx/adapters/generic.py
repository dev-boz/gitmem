from __future__ import annotations

from pathlib import Path
from typing import Any

from umx.dream.extract import _facts_from_session_payload
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
from umx.scope import project_memory_dir


class NativeMemoryAdapter:
    name = "generic"

    def read_native_memory(self, _project_root: Path) -> list[Fact]:
        return []

    def _facts_from_transcript_events(
        self,
        project_root: Path,
        *,
        session: str,
        events: list[dict[str, Any]],
        encoding_context: dict[str, Any] | None = None,
    ) -> list[Fact]:
        repo_dir = project_memory_dir(project_root)
        transcript_facts = _facts_from_session_payload(repo_dir, session, events)
        facts: list[Fact] = []
        for fact in transcript_facts:
            provenance = fact.provenance.to_dict()
            provenance["extracted_by"] = f"adapter-{self.name}"
            merged_context = dict(fact.encoding_context)
            if encoding_context:
                merged_context.update(encoding_context)
            facts.append(
                fact.clone(
                    encoding_strength=3,
                    memory_type=MemoryType.EXPLICIT_SEMANTIC,
                    verification=Verification.SELF_REPORTED,
                    source_type=SourceType.TOOL_OUTPUT,
                    confidence=0.6,
                    source_tool=self.name,
                    source_session=session,
                    provenance=Provenance.from_dict(provenance),
                    encoding_context=merged_context,
                )
            )
        return facts

    def _path_matches_project(self, path: str | Path | None, project_root: Path) -> bool:
        if path is None:
            return False
        try:
            candidate = Path(path).expanduser().resolve()
            root = project_root.resolve()
        except OSError:
            return False
        try:
            candidate.relative_to(root)
        except ValueError:
            return False
        return True

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
            memory_type=MemoryType.EXPLICIT_SEMANTIC,
            verification=Verification.SELF_REPORTED,
            source_type=SourceType.TOOL_OUTPUT,
            confidence=0.6,
            source_tool=self.name,
            source_session=session,
            consolidation_status=ConsolidationStatus.FRAGILE,
            provenance=Provenance(extracted_by=f"adapter-{self.name}", sessions=[session]),
        )
