"""Tests for the Dream pipeline Prune phase (umx.dream.prune)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from umx.dream.prune import (
    PruneDecision,
    run_dream_prune,
    run_prune,
    should_prune,
    write_prune_report,
)
from umx.models import (
    ConsolidationStatus,
    Fact,
    MemoryType,
    Scope,
    SourceType,
    Verification,
)


def make_fact(
    fact_id: str,
    text: str = "A test fact.",
    *,
    encoding_strength: int = 4,
    superseded_by: str | None = None,
    source_type: SourceType = SourceType.GROUND_TRUTH_CODE,
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
    )


def test_should_prune_fact_with_low_strength() -> None:
    fact = make_fact("FACT_LOW", encoding_strength=1, superseded_by=None)
    decision = should_prune(fact)
    assert decision.fact_id == "FACT_LOW"
    assert decision.action == "prune"
    assert "encoding_strength" in decision.reason


def test_should_keep_high_strength_fact() -> None:
    fact = make_fact("FACT_HIGH", encoding_strength=4, superseded_by=None)
    decision = should_prune(fact)
    assert decision.fact_id == "FACT_HIGH"
    assert decision.action == "keep"


def test_should_prune_superseded_fact() -> None:
    fact = make_fact("FACT_OLD", encoding_strength=4, superseded_by="other-id")
    decision = should_prune(fact)
    assert decision.fact_id == "FACT_OLD"
    assert decision.action == "prune"
    assert "superseded" in decision.reason


def test_run_prune_dry_run_keeps_all_facts() -> None:
    prune_worthy = make_fact("FACT_PRUNE", encoding_strength=1)
    keeper = make_fact("FACT_KEEP", encoding_strength=5)
    facts = [prune_worthy, keeper]

    decisions, surviving = run_prune(facts, dry_run=True)

    assert len(decisions) == 2
    assert len(surviving) == 2  # dry_run keeps all facts
    prune_actions = [d.action for d in decisions]
    assert "prune" in prune_actions
    assert "keep" in prune_actions


def test_write_prune_report_creates_file(tmp_path: Path) -> None:
    decisions = [
        PruneDecision(fact_id="F1", action="keep", reason="passes prune threshold"),
        PruneDecision(fact_id="F2", action="prune", reason="encoding_strength below minimum (S:2)"),
    ]
    report_path = write_prune_report(tmp_path, decisions)

    assert report_path.exists()
    data = json.loads(report_path.read_text())
    assert data["schema_version"] == "0.6"
    assert data["total"] == 2
    assert data["pruned"] == 1
    assert data["kept"] == 1
    assert len(data["decisions"]) == 2


def test_run_dream_prune_returns_counts(tmp_path: Path) -> None:
    facts = [
        make_fact("FACT_A", encoding_strength=1),   # will be pruned
        make_fact("FACT_B", encoding_strength=4),   # will be kept
        make_fact("FACT_C", encoding_strength=5),   # will be kept
    ]
    result = run_dream_prune(tmp_path, facts)

    assert "pruned" in result
    assert "kept" in result
    assert result["pruned"] == 1
    assert result["kept"] == 2
    assert Path(result["report_path"]).exists()
