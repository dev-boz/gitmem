from __future__ import annotations

from datetime import datetime

from umx.models import Fact
from umx.strength import recency_score


def decayed_retention_order(
    facts: list[Fact],
    now: datetime,
    config=None,
) -> list[tuple[Fact, float]]:
    scored = [(fact, recency_score(fact, now, config=config)) for fact in facts]
    scored.sort(key=lambda item: (item[1], item[0].encoding_strength), reverse=True)
    return scored


def apply_time_decay(facts: list[Fact], now: datetime, config=None) -> list[Fact]:
    return [fact for fact, _ in decayed_retention_order(facts, now, config=config)]


def decay_confidence_by_chain_depth(
    confidence: float,
    chain_depth: int,
    decay_per_hop: float = 0.05,
    floor: float = 0.10,
) -> float:
    """Apply per-hop confidence decay for contamination-aware consolidation.

    Each agent-to-agent handoff hop reduces confidence by `decay_per_hop`,
    reflecting that errors and bias can compound across the chain. The floor
    prevents confidence from reaching zero even at high chain depths.

    Per IMX spec v0.6 §11.6: confidence decay is weighted by peer reliability
    and chain_depth. This function applies a simple linear decay as the baseline.
    """
    if chain_depth <= 0:
        return confidence
    decayed = confidence * ((1.0 - decay_per_hop) ** chain_depth)
    return max(floor, decayed)


def chain_depth_from_provenance(provenance_dict: dict) -> int:
    """Extract chain_depth from an IMX provenance dict, defaulting to 0."""
    try:
        return int(provenance_dict.get("chain_depth", 0))
    except (TypeError, ValueError):
        return 0
