from __future__ import annotations

import re
from dataclasses import dataclass

from umx.config import UMXConfig, default_config
from umx.models import Fact


COMMENT_RE = re.compile(r"<!--\s*umx:.*?-->", re.DOTALL)


def strip_inline_metadata(text: str) -> str:
    return COMMENT_RE.sub("", text).strip()


def estimate_tokens(text: str) -> int:
    clean = strip_inline_metadata(text)
    if not clean:
        return 1
    return max(1, (len(clean) + 3) // 4)


def estimate_fact_tokens(fact: Fact) -> int:
    return estimate_tokens(fact.text)


@dataclass(slots=True)
class BudgetDecision:
    selected: list[Fact]
    packing_scores: dict[str, float]


def enforce_budget(
    facts: list[Fact],
    max_tokens: int,
    relevance_scores: dict[str, float] | None = None,
    always_include_ids: set[str] | None = None,
    config: UMXConfig | None = None,
) -> BudgetDecision:
    cfg = config or default_config()
    relevance_scores = relevance_scores or {}
    always_include_ids = always_include_ids or set()
    selected: list[Fact] = []
    used_tokens = 0
    packing_scores = {
        fact.fact_id: (
            relevance_scores.get(fact.fact_id, 0.0) / max(1, estimate_fact_tokens(fact))
        )
        for fact in facts
    }

    always = [fact for fact in facts if fact.fact_id in always_include_ids]
    always.sort(key=lambda fact: (fact.encoding_strength, fact.created), reverse=True)
    for fact in always:
        tokens = estimate_fact_tokens(fact)
        if used_tokens + tokens <= max_tokens:
            selected.append(fact)
            used_tokens += tokens

    remaining = [fact for fact in facts if fact.fact_id not in always_include_ids]
    remaining.sort(
        key=lambda fact: (
            packing_scores.get(fact.fact_id, 0.0),
            relevance_scores.get(fact.fact_id, 0.0),
            fact.encoding_strength,
        ),
        reverse=True,
    )
    for fact in remaining:
        tokens = estimate_fact_tokens(fact)
        if used_tokens + tokens > max_tokens:
            continue
        selected.append(fact)
        used_tokens += tokens

    removable = [fact for fact in selected if fact.fact_id not in always_include_ids]
    removable.sort(
        key=lambda fact: (
            packing_scores.get(fact.fact_id, 0.0),
            relevance_scores.get(fact.fact_id, 0.0),
            fact.encoding_strength,
        )
    )
    while len(selected) > cfg.inject.max_concurrent_facts and removable:
        victim = removable.pop(0)
        selected = [fact for fact in selected if fact.fact_id != victim.fact_id]

    if len(selected) < cfg.inject.min_facts and facts:
        return BudgetDecision(selected=selected, packing_scores=packing_scores)
    return BudgetDecision(selected=selected, packing_scores=packing_scores)
