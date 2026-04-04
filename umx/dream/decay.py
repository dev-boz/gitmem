"""Exponential recency decay with configurable λ.

Based on Ebbinghaus's forgetting curve: memory strength decays
exponentially without reinforcement.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

from umx.models import Fact, UmxConfig


def decay_score(
    age_days: float,
    decay_lambda: float = 0.023,
) -> float:
    """Calculate decay score for a given age.

    recency = exp(-λ × age_days)

    Args:
        age_days: Days since last retrieval or creation.
        decay_lambda: Decay constant.

    Returns:
        Score between 0 and 1 (1 = brand new, 0 = very old).
    """
    return math.exp(-decay_lambda * max(0.0, age_days))


def fact_age_days(fact: Fact, now: datetime | None = None) -> float:
    """Calculate the age of a fact in days.

    Uses last_retrieved if available, falls back to created.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    reference = fact.last_retrieved or fact.created
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return max(0.0, (now - reference).total_seconds() / 86400)


def apply_time_decay(
    facts: list[Fact],
    config: UmxConfig | None = None,
    now: datetime | None = None,
) -> list[Fact]:
    """Apply time decay to uncorroborated low-strength facts.

    Facts at strength 1-2 with no corroboration and low recency
    have their strength decremented. Returns the modified list.
    """
    if config is None:
        config = UmxConfig()
    if now is None:
        now = datetime.now(timezone.utc)

    result: list[Fact] = []
    for fact in facts:
        age = fact_age_days(fact, now)
        rec = decay_score(age, config.decay_lambda)

        # Only decay uncorroborated low-strength facts
        if (
            fact.encoding_strength <= 2
            and not fact.corroborated_by
            and rec < 0.3
        ):
            fact.encoding_strength = max(0, fact.encoding_strength - 1)

        result.append(fact)

    return result
