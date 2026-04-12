from __future__ import annotations

from pathlib import Path

from umx.config import UMXConfig, default_config
from umx.dream.conflict import facts_conflict, resolve_conflict
from umx.memory import load_all_facts, replace_fact
from umx.models import Fact, SourceType
from umx.strength import trust_score


def find_conflicts(repo_dir: Path) -> list[tuple[Fact, Fact]]:
    """Find all pairs of conflicting facts."""
    facts = load_all_facts(repo_dir, include_superseded=False)
    pairs: list[tuple[Fact, Fact]] = []
    seen: set[tuple[str, str]] = set()
    for i, left in enumerate(facts):
        for right in facts[i + 1 :]:
            key = (min(left.fact_id, right.fact_id), max(left.fact_id, right.fact_id))
            if key in seen:
                continue
            if facts_conflict(left, right):
                pairs.append((left, right))
                seen.add(key)
    return pairs


def arbitrate_conflict(
    left: Fact, right: Fact, config: UMXConfig,
) -> tuple[Fact, Fact, str]:
    """Resolve a conflict pair. Returns (winner, loser, reason)."""
    # Ground-truth hard rule
    if (
        left.source_type == SourceType.GROUND_TRUTH_CODE
        and right.source_type != SourceType.GROUND_TRUTH_CODE
    ):
        reason = "ground_truth_code beats non-ground-truth"
    elif (
        right.source_type == SourceType.GROUND_TRUTH_CODE
        and left.source_type != SourceType.GROUND_TRUTH_CODE
    ):
        reason = "ground_truth_code beats non-ground-truth"
    else:
        left_score = trust_score(left, config)
        right_score = trust_score(right, config)
        reason = f"trust_score {left_score:.2f} vs {right_score:.2f}"

    winner, loser = resolve_conflict(left, right, config=config)
    return winner, loser, reason


def merge_all(
    repo_dir: Path, config: UMXConfig, dry_run: bool = False,
) -> list[dict]:
    """Find and resolve all conflicts.

    Returns list of {winner_id, loser_id, reason} dicts.
    If not dry_run, applies the resolutions (sets supersedes/superseded_by).
    """
    pairs = find_conflicts(repo_dir)
    results: list[dict] = []
    resolved_ids: set[str] = set()
    for left, right in pairs:
        # Skip if either fact was already resolved in this pass
        if left.fact_id in resolved_ids or right.fact_id in resolved_ids:
            continue
        winner, loser, reason = arbitrate_conflict(left, right, config)
        results.append({
            "winner_id": winner.fact_id,
            "loser_id": loser.fact_id,
            "reason": reason,
        })
        resolved_ids.add(loser.fact_id)
        if not dry_run:
            replace_fact(repo_dir, winner)
            replace_fact(repo_dir, loser)
    return results
