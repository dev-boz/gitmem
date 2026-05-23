"""Prune phase of the Dream pipeline per gitmem spec §11."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from umx.models import Fact, SourceType


@dataclass
class PruneDecision:
    fact_id: str
    action: str   # "keep", "prune", or "archive"
    reason: str


def should_prune(fact: Fact) -> PruneDecision:
    """Evaluate a single Fact and return a PruneDecision."""
    if fact.encoding_strength < 2:
        return PruneDecision(
            fact_id=fact.fact_id,
            action="prune",
            reason="encoding_strength below minimum (S:2)",
        )
    if fact.superseded_by is not None:
        return PruneDecision(
            fact_id=fact.fact_id,
            action="prune",
            reason="fact is superseded",
        )
    if fact.encoding_strength == 2 and fact.source_type.value == "llm_inference":
        return PruneDecision(
            fact_id=fact.fact_id,
            action="archive",
            reason="inferred fact at minimum strength",
        )
    return PruneDecision(
        fact_id=fact.fact_id,
        action="keep",
        reason="passes prune threshold",
    )


def run_prune(
    facts: list[Fact],
    *,
    dry_run: bool = False,
) -> tuple[list[PruneDecision], list[Fact]]:
    """Apply should_prune to each fact and return (decisions, surviving_facts)."""
    decisions = [should_prune(f) for f in facts]
    if dry_run:
        surviving_facts = list(facts)
    else:
        surviving_facts = [
            f for f, d in zip(facts, decisions) if d.action != "prune"
        ]
    return decisions, surviving_facts


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
        "generated_at": datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z"),
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
    hot_tier_limit: int = 50,
) -> dict:
    """Sort facts by strength then recency; return hot-tier summary dict."""
    sorted_facts = sorted(
        facts,
        key=lambda f: (f.encoding_strength, f.created),
        reverse=True,
    )
    hot = sorted_facts[:hot_tier_limit]
    return {
        "hot": [
            {
                "fact_id": f.fact_id,
                "summary": f.text[:80],
                "strength": f.encoding_strength,
            }
            for f in hot
        ],
        "total": len(facts),
    }


def run_dream_prune(
    repo_dir,
    facts: list[Fact],
    *,
    dry_run: bool = False,
) -> dict:
    """Run the full prune phase: prune → report → rebuild index."""
    repo_dir = Path(repo_dir)
    decisions, surviving_facts = run_prune(facts, dry_run=dry_run)
    path = write_prune_report(repo_dir, decisions)
    rebuild_memory_index(repo_dir, surviving_facts)

    pruned = sum(1 for d in decisions if d.action == "prune")
    archived = sum(1 for d in decisions if d.action == "archive")
    kept = sum(1 for d in decisions if d.action == "keep")

    return {
        "pruned": pruned,
        "archived": archived,
        "kept": kept,
        "report_path": str(path),
    }
