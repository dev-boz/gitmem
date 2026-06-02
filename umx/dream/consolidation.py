from __future__ import annotations

from datetime import datetime

from umx.models import ConsolidationStatus, Fact


def _has_rule_two_corroboration(fact: Fact) -> bool:
    return bool(fact.corroborated_by_tools or fact.corroborated_by_facts)


def stabilize_facts(facts: list[Fact], new_fact_ids: set[str], now: datetime) -> list[Fact]:
    stabilized: list[Fact] = []
    for fact in facts:
        if (
            fact.consolidation_status == ConsolidationStatus.FRAGILE
            and fact.superseded_by is None
            and (
                fact.fact_id not in new_fact_ids
                or _has_rule_two_corroboration(fact)
            )
        ):
            stabilized.append(fact.clone(consolidation_status=ConsolidationStatus.STABLE))
        else:
            stabilized.append(fact)
    return stabilized
