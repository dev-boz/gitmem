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
