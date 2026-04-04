"""Context budget inference and enforcement.

Ensures memory injection never crowds out codebase context.
No partial fact inclusion — a fact is either fully included or excluded.
"""

from __future__ import annotations

from umx.models import Fact, UmxConfig


def estimate_tokens(text: str) -> int:
    """Estimate token count for a text string.

    Uses a simple heuristic: ~4 characters per token for English.
    """
    return max(1, len(text) // 4)


def estimate_fact_tokens(fact: Fact) -> int:
    """Estimate the token cost of injecting a single fact."""
    line = f"- [S:{fact.encoding_strength}] {fact.text}"
    return estimate_tokens(line)


def enforce_budget(
    facts: list[Fact],
    max_tokens: int | None = None,
    config: UmxConfig | None = None,
) -> list[Fact]:
    """Select facts that fit within the context budget.

    Facts should be pre-sorted by relevance/priority (highest first).
    No partial fact inclusion: a fact is either fully included or excluded.

    Args:
        facts: Facts sorted by priority (highest first).
        max_tokens: Override max tokens. Falls back to config default.
        config: UmxConfig for defaults.

    Returns:
        List of facts that fit within budget.
    """
    if config is None:
        config = UmxConfig()
    budget = max_tokens or config.default_max_tokens

    # Reserve some tokens for framing (headers, separators)
    overhead = 50
    remaining = budget - overhead

    selected: list[Fact] = []
    for fact in facts:
        cost = estimate_fact_tokens(fact)
        if cost <= remaining:
            selected.append(fact)
            remaining -= cost
        # No partial facts — skip if doesn't fit
    return selected
