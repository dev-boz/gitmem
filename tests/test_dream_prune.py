"""Tests for the Dream prune compatibility layer."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from umx.config import default_config
from umx.dream.consolidation import stabilize_facts
from umx.dream.prune import PruneDecision, run_dream_prune, run_prune, should_prune, write_prune_report
from umx.memory import read_memory_md
from umx.models import ConsolidationStatus, Fact, MemoryType, Scope, SourceType, Verification


def make_fact(
    fact_id: str,
    text: str = "A test fact.",
    *,
    encoding_strength: int = 4,
    superseded_by: str | None = None,
    source_type: SourceType = SourceType.GROUND_TRUTH_CODE,
    created: datetime | None = None,
    expires_at: datetime | None = None,
) -> Fact:
    return Fact(
        fact_id=fact_id,
        text=text,
        scope=Scope.PROJECT,
        topic="test-topic",
        encoding_strength=encoding_strength,
        memory_type=MemoryType.EXPLICIT_SEMANTIC,
        verification=Verification.CORROBORATED,
        source_type=source_type,
        source_tool="manual",
        source_session="2026-01-01-test",
        consolidation_status=ConsolidationStatus.STABLE,
        superseded_by=superseded_by,
        created=created or datetime(2026, 1, 1, tzinfo=UTC),
        expires_at=expires_at,
    )


def test_should_prune_expired_active_fact() -> None:
    now = datetime(2026, 1, 10, tzinfo=UTC)
    fact = make_fact("FACT_EXPIRED", expires_at=now - timedelta(days=1))

    decision = should_prune(fact, now=now)

    assert decision.fact_id == "FACT_EXPIRED"
    assert decision.action == "prune"
    assert "expired" in decision.reason


def test_should_keep_superseded_fact_for_index_compatibility() -> None:
    now = datetime(2026, 1, 10, tzinfo=UTC)
    fact = make_fact(
        "FACT_OLD",
        superseded_by="FACT_NEW",
        expires_at=now - timedelta(days=1),
    )

    decision = should_prune(fact, now=now)

    assert decision.fact_id == "FACT_OLD"
    assert decision.action == "keep"
    assert "superseded facts are retained" in decision.reason


def test_run_prune_dry_run_keeps_all_facts() -> None:
    now = datetime(2026, 1, 10, tzinfo=UTC)
    prune_worthy = make_fact("FACT_PRUNE", expires_at=now - timedelta(days=1))
    keeper = make_fact("FACT_KEEP", encoding_strength=5)

    decisions, surviving = run_prune([prune_worthy, keeper], dry_run=True, now=now)

    assert len(decisions) == 2
    assert len(surviving) == 2
    assert {decision.action for decision in decisions} == {"keep", "prune"}


def test_write_prune_report_creates_file(tmp_path: Path) -> None:
    decisions = [
        PruneDecision(fact_id="F1", action="keep", reason="retained by Dream memory policy"),
        PruneDecision(fact_id="F2", action="prune", reason="expired retention window reached"),
    ]
    report_path = write_prune_report(tmp_path, decisions)

    assert report_path.exists()
    data = json.loads(report_path.read_text())
    assert data["schema_version"] == "0.6"
    assert data["total"] == 2
    assert data["pruned"] == 1
    assert data["kept"] == 1
    assert len(data["decisions"]) == 2


def test_run_dream_prune_rebuilds_memory_index_with_active_facts_only(tmp_path: Path) -> None:
    now = datetime(2026, 1, 10, tzinfo=UTC)
    (tmp_path / "meta").mkdir(parents=True, exist_ok=True)
    facts = [
        make_fact("FACT_A", "expired active fact", expires_at=now - timedelta(days=1)),
        make_fact("FACT_B", "active fact that stays"),
        make_fact("FACT_C", "superseded fact is retained", superseded_by="FACT_B"),
    ]

    result = run_dream_prune(tmp_path, facts, now=now, config=default_config())

    assert result["pruned"] == 1
    assert result["kept"] == 2
    assert result["active_indexed"] == 1
    assert Path(result["report_path"]).exists()
    memory_md = read_memory_md(tmp_path)
    assert "active fact that stays" in memory_md
    assert "expired active fact" not in memory_md
    assert "superseded fact is retained" not in memory_md


def test_stabilize_facts_rule_two_promotes_newly_corroborated_fragile_fact() -> None:
    now = datetime(2026, 1, 10, tzinfo=UTC)
    fact = make_fact("FACT_RULE_TWO")
    fact.consolidation_status = ConsolidationStatus.FRAGILE
    fact.corroborated_by_tools.append("copilot")

    stabilized = stabilize_facts([fact], {"FACT_RULE_TWO"}, now)

    assert stabilized[0].consolidation_status == ConsolidationStatus.STABLE


def test_stabilize_facts_rule_two_isolated_from_survive_one_cycle() -> None:
    """Rule 2 (independent corroboration) promotes a fragile fact even when
    rule 1 (survive-one-cycle) cannot apply because the fact is new this cycle."""
    now = datetime(2026, 1, 10, tzinfo=UTC)
    uncorroborated = make_fact("FACT_NEW_FRAGILE")
    uncorroborated.consolidation_status = ConsolidationStatus.FRAGILE
    corroborated = make_fact("FACT_NEW_CORROBORATED")
    corroborated.consolidation_status = ConsolidationStatus.FRAGILE
    corroborated.corroborated_by_facts.append("FACT_SOURCE")

    new_ids = {"FACT_NEW_FRAGILE", "FACT_NEW_CORROBORATED"}
    stabilized = stabilize_facts([uncorroborated, corroborated], new_ids, now)
    by_id = {fact.fact_id: fact for fact in stabilized}

    # Both are new this cycle, so rule 1 is unavailable; only recorded
    # corroboration (rule 2) promotes the second fact.
    assert by_id["FACT_NEW_FRAGILE"].consolidation_status == ConsolidationStatus.FRAGILE
    assert by_id["FACT_NEW_CORROBORATED"].consolidation_status == ConsolidationStatus.STABLE
