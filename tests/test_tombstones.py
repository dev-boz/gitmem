from __future__ import annotations

from pathlib import Path

from umx.memory import add_fact, load_all_facts
from umx.models import (
    ConsolidationStatus,
    Fact,
    MemoryType,
    Scope,
    SourceType,
    Verification,
)
from umx.tombstones import forget_fact, load_tombstones


def make_fact(
    fact_id: str,
    text: str,
    *,
    supersedes: str | None = None,
    superseded_by: str | None = None,
) -> Fact:
    return Fact(
        fact_id=fact_id,
        text=text,
        scope=Scope.PROJECT,
        topic="devenv",
        encoding_strength=4,
        memory_type=MemoryType.EXPLICIT_SEMANTIC,
        verification=Verification.CORROBORATED,
        source_type=SourceType.GROUND_TRUTH_CODE,
        source_tool="manual",
        source_session="2026-04-17-manual",
        consolidation_status=ConsolidationStatus.STABLE,
        supersedes=supersedes,
        superseded_by=superseded_by,
    )


def test_forget_superseded_predecessor_keeps_successor_active(project_repo: Path) -> None:
    """Forgetting an old predecessor tombstones only that fact; the successor stays active."""

    older = make_fact("FACT_OLD", "postgres runs on 5432 in dev", superseded_by="FACT_NEW")
    newer = make_fact("FACT_NEW", "postgres runs on 5433 in dev", supersedes="FACT_OLD")
    add_fact(project_repo, older, auto_commit=False)
    add_fact(project_repo, newer, auto_commit=False)

    removed = forget_fact(project_repo, older.fact_id)

    assert removed is not None
    active = load_all_facts(project_repo, include_superseded=False)
    all_facts = load_all_facts(project_repo, include_superseded=True)
    assert [fact.fact_id for fact in active] == [newer.fact_id]
    assert {fact.fact_id for fact in all_facts} == {newer.fact_id}
    assert [tombstone.fact_id for tombstone in load_tombstones(project_repo)] == [older.fact_id]


def test_forget_active_successor_does_not_resurrect_predecessor(project_repo: Path) -> None:
    """Forgetting the active successor leaves the predecessor historical rather than resurrected."""

    older = make_fact("FACT_PREV", "postgres runs on 5432 in dev", superseded_by="FACT_CURR")
    newer = make_fact("FACT_CURR", "postgres runs on 5433 in dev", supersedes="FACT_PREV")
    add_fact(project_repo, older, auto_commit=False)
    add_fact(project_repo, newer, auto_commit=False)

    removed = forget_fact(project_repo, newer.fact_id)

    assert removed is not None
    active = load_all_facts(project_repo, include_superseded=False)
    historical = load_all_facts(project_repo, include_superseded=True)
    assert active == []
    assert len(historical) == 1
    assert historical[0].fact_id == older.fact_id
    assert historical[0].superseded_by == newer.fact_id
    assert [tombstone.fact_id for tombstone in load_tombstones(project_repo)] == [newer.fact_id]
