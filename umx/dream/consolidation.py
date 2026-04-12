from __future__ import annotations

from datetime import datetime

from umx.models import ConsolidationStatus, Fact


def stabilize_facts(facts: list[Fact], new_fact_ids: set[str], now: datetime) -> list[Fact]:
    stabilized: list[Fact] = []
    for fact in facts:
        if (
            fact.consolidation_status == ConsolidationStatus.FRAGILE
            and fact.fact_id not in new_fact_ids
            and fact.superseded_by is None
        ):
            stabilized.append(fact.clone(consolidation_status=ConsolidationStatus.STABLE))
        else:
            stabilized.append(fact)
    return stabilized
