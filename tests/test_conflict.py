from __future__ import annotations

from umx.dream.conflict import facts_conflict, resolve_conflict
from umx.models import (
    AppliesTo,
    ConsolidationStatus,
    Fact,
    MemoryType,
    Scope,
    SourceType,
    Verification,
)


def make_fact(text: str, source_type: SourceType, **overrides) -> Fact:
    values = {
        "fact_id": overrides.pop("fact_id", "01TESTFACT0000000000000100"),
        "text": text,
        "scope": Scope.PROJECT,
        "topic": "devenv",
        "encoding_strength": 3,
        "memory_type": MemoryType.EXPLICIT_SEMANTIC,
        "verification": Verification.SELF_REPORTED,
        "source_type": source_type,
        "source_tool": "codex",
        "source_session": "2026-04-11",
        "consolidation_status": ConsolidationStatus.FRAGILE,
    }
    values.update(overrides)
    return Fact(**values)


def test_ground_truth_beats_llm_inference_on_conflict() -> None:
    code_fact = make_fact(
        "postgres runs on 5433 in dev",
        SourceType.GROUND_TRUTH_CODE,
        fact_id="01TESTFACT0000000000000101",
    )
    inferred = make_fact(
        "postgres runs on 5432 in dev",
        SourceType.LLM_INFERENCE,
        fact_id="01TESTFACT0000000000000102",
    )

    winner, loser = resolve_conflict(code_fact, inferred)

    assert winner.fact_id == code_fact.fact_id
    assert loser.fact_id == inferred.fact_id
    assert loser.superseded_by == winner.fact_id


def test_non_overlapping_applies_to_do_not_conflict() -> None:
    dev = make_fact(
        "postgres runs on 5433",
        SourceType.GROUND_TRUTH_CODE,
        applies_to=AppliesTo(env="dev", os="*", machine="*", branch="*"),
    )
    prod = make_fact(
        "postgres runs on 5432",
        SourceType.GROUND_TRUTH_CODE,
        fact_id="01TESTFACT0000000000000103",
        applies_to=AppliesTo(env="prod", os="*", machine="*", branch="*"),
    )

    assert not facts_conflict(dev, prod)
