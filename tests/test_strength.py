from __future__ import annotations

from datetime import UTC, datetime, timedelta

from umx.cross_project import build_cross_project_promotion_fact
from umx.models import ConsolidationStatus, Fact, MemoryType, Scope, SourceType, Verification
from umx.strength import independent_corroboration, trust_score


def _fact(
    fact_id: str,
    *,
    source_tool: str = "codex",
    source_session: str = "sess-1",
    source_type: SourceType = SourceType.TOOL_OUTPUT,
    created: datetime | None = None,
) -> Fact:
    return Fact(
        fact_id=fact_id,
        text="A durable fact for testing.",
        scope=Scope.PROJECT,
        topic="general",
        encoding_strength=4,
        memory_type=MemoryType.EXPLICIT_SEMANTIC,
        verification=Verification.CORROBORATED,
        source_type=source_type,
        source_tool=source_tool,
        source_session=source_session,
        consolidation_status=ConsolidationStatus.STABLE,
        created=created or datetime(2026, 4, 11, tzinfo=UTC),
    )


def test_trust_score_uses_cross_project_source_types() -> None:
    report = {
        "candidate": {
            "key": "shared-runtime",
            "text": "Runtime setup is shared across repositories.",
            "repos": ["repo-a", "repo-b"],
            "occurrences": [
                {
                    "repo": "repo-a",
                    "fact_id": "fact-a",
                    "text": "Runtime setup is shared across repositories.",
                    "topic": "runtime",
                    "created": "2026-04-11T00:00:00Z",
                    "encoding_strength": 4,
                    "file_path": None,
                    "source_type": SourceType.GROUND_TRUTH_CODE.value,
                },
                {
                    "repo": "repo-b",
                    "fact_id": "fact-b",
                    "text": "Runtime setup is shared across repositories.",
                    "topic": "runtime",
                    "created": "2026-04-12T00:00:00Z",
                    "encoding_strength": 4,
                    "file_path": None,
                    "source_type": SourceType.TOOL_OUTPUT.value,
                },
            ],
        },
        "target": {
            "topic": "runtime",
        },
    }

    promoted = build_cross_project_promotion_fact(report)
    without_sources = promoted.clone(encoding_context={})

    assert promoted.encoding_context["cross_project_occurrences"][0]["source_type"] == SourceType.GROUND_TRUTH_CODE.value
    assert trust_score(promoted) > trust_score(without_sources)


def test_independent_corroboration_requires_time_gap_for_same_tool() -> None:
    base = _fact("01TESTSTRENGTH000000000001", source_tool="copilot", source_session="sess-a")
    soon = _fact(
        "01TESTSTRENGTH000000000002",
        source_tool="copilot",
        source_session="sess-b",
        created=base.created + timedelta(hours=2),
    )
    later = _fact(
        "01TESTSTRENGTH000000000003",
        source_tool="copilot",
        source_session="sess-c",
        created=base.created + timedelta(days=2),
    )

    assert not independent_corroboration(base, soon)
    assert independent_corroboration(base, later)


def test_sota_reviewed_verification_bonus_increases_trust_score() -> None:
    self_reported = _fact("01TESTSTRENGTHSOTA0000001").clone(
        verification=Verification.SELF_REPORTED,
    )
    sota_reviewed = self_reported.clone(verification=Verification.SOTA_REVIEWED)

    assert trust_score(sota_reviewed) > trust_score(self_reported)
