"""Compatibility prune wrappers aligned with the live Dream retention model."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from umx.config import UMXConfig
from umx.memory import write_memory_md
from umx.models import Fact
from umx.strength import should_prune as should_prune_fact


@dataclass
class PruneDecision:
    fact_id: str
    action: str
    reason: str


def should_prune(
    fact: Fact,
    *,
    now: datetime | None = None,
    usage_frequency: int = 0,
    config: UMXConfig | None = None,
) -> PruneDecision:
    """Return a compatibility decision using the active-retention rules."""

    current = now or datetime.now(tz=UTC)
    if fact.superseded_by is not None:
        return PruneDecision(
            fact_id=fact.fact_id,
            action="keep",
            reason="superseded facts are retained outside the active memory index",
        )
    if fact.expires_at and fact.expires_at <= current:
        return PruneDecision(
            fact_id=fact.fact_id,
            action="prune",
            reason="expired retention window reached",
        )
    if should_prune_fact(fact, current, usage_frequency=usage_frequency, config=config):
        return PruneDecision(
            fact_id=fact.fact_id,
            action="prune",
            reason="retention score below prune threshold",
        )
    return PruneDecision(
        fact_id=fact.fact_id,
        action="keep",
        reason="retained by Dream memory policy",
    )


def run_prune(
    facts: list[Fact],
    *,
    dry_run: bool = False,
    now: datetime | None = None,
    usage_frequency_by_fact: dict[str, int] | None = None,
    config: UMXConfig | None = None,
) -> tuple[list[PruneDecision], list[Fact]]:
    """Evaluate compatibility prune decisions and optionally drop active prunes."""

    current = now or datetime.now(tz=UTC)
    usage_frequency_by_fact = usage_frequency_by_fact or {}
    decisions: list[PruneDecision] = []
    surviving: list[Fact] = []
    for fact in facts:
        decision = should_prune(
            fact,
            now=current,
            usage_frequency=usage_frequency_by_fact.get(fact.fact_id, 0),
            config=config,
        )
        decisions.append(decision)
        if dry_run or decision.action != "prune":
            surviving.append(fact)
    return decisions, surviving


def write_prune_report(
    repo_dir: Path,
    decisions: list[PruneDecision],
) -> Path:
    """Write a prune-report.json into {repo_dir}/.gitmem/dream/ and return the path."""

    report_path = repo_dir / ".gitmem" / "dream" / "prune-report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)

    total = len(decisions)
    pruned = sum(1 for d in decisions if d.action == "prune")
    archived = sum(1 for d in decisions if d.action == "archive")
    kept = sum(1 for d in decisions if d.action == "keep")

    content = {
        "schema_version": "0.6",
        "generated_at": datetime.now(tz=UTC).isoformat().replace("+00:00", "Z"),
        "total": total,
        "pruned": pruned,
        "archived": archived,
        "kept": kept,
        "decisions": [
            {"fact_id": d.fact_id, "action": d.action, "reason": d.reason}
            for d in decisions
        ],
    }

    tmp_path = report_path.with_suffix(report_path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(content, indent=2), encoding="utf-8")
    os.replace(tmp_path, report_path)
    return report_path


def rebuild_memory_index(
    repo_dir: Path,
    facts: list[Fact],
    *,
    config: UMXConfig | None = None,
) -> dict[str, int]:
    active_facts = [fact for fact in facts if fact.superseded_by is None]
    write_memory_md(repo_dir, active_facts, config=config, auto_commit=False)
    return {
        "total": len(facts),
        "active": len(active_facts),
    }


def run_dream_prune(
    repo_dir: Path,
    facts: list[Fact],
    *,
    dry_run: bool = False,
    now: datetime | None = None,
    usage_frequency_by_fact: dict[str, int] | None = None,
    config: UMXConfig | None = None,
) -> dict[str, object]:
    """Run the compatibility prune phase and refresh the active memory index."""

    decisions, surviving_facts = run_prune(
        facts,
        dry_run=dry_run,
        now=now,
        usage_frequency_by_fact=usage_frequency_by_fact,
        config=config,
    )
    path = write_prune_report(repo_dir, decisions)
    index_stats = rebuild_memory_index(repo_dir, facts if dry_run else surviving_facts, config=config)

    return {
        "pruned": sum(1 for d in decisions if d.action == "prune"),
        "archived": sum(1 for d in decisions if d.action == "archive"),
        "kept": sum(1 for d in decisions if d.action == "keep"),
        "report_path": str(path),
        "active_indexed": index_stats["active"],
    }
