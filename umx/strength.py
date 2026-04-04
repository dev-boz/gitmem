"""Encoding strength, composite scoring, corroboration, and relevance.

Implements ACT-R activation strength model with exponential recency decay.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

from umx.models import (
    SCOPE_PROXIMITY,
    EncodingStrength,
    Fact,
    Scope,
    UmxConfig,
)


def recency_score(
    fact: Fact,
    now: datetime | None = None,
    decay_lambda: float = 0.023,
) -> float:
    """Calculate recency as normalised 0-1 score using exponential decay.

    recency = exp(-λ × age_days)

    Uses last_retrieved if available, falls back to created.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    reference = fact.last_retrieved or fact.created
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    age_days = max(0.0, (now - reference).total_seconds() / 86400)
    return math.exp(-decay_lambda * age_days)


def composite_score(
    fact: Fact,
    config: UmxConfig | None = None,
    now: datetime | None = None,
) -> float:
    """Calculate composite fact score for prioritisation.

    fact_score =
      (w_s × encoding_strength)
    + (w_c × confidence)
    + (w_r × recency)
    + (w_k × corroboration_count)
    """
    if config is None:
        config = UmxConfig()

    rec = recency_score(fact, now=now, decay_lambda=config.decay_lambda)
    corr_count = len(fact.corroborated_by)

    # Normalise strength to 0-1 range for weighted sum
    norm_strength = fact.encoding_strength / 5.0

    score = (
        config.weight_strength * norm_strength
        + config.weight_confidence * fact.confidence
        + config.weight_recency * rec
        + config.weight_corroboration * min(corr_count / 3.0, 1.0)
    )
    return round(score, 4)


def relevance_score(
    fact: Fact,
    target_scope: Scope,
    keywords: list[str] | None = None,
    session_fact_ids: set[str] | None = None,
    config: UmxConfig | None = None,
) -> float:
    """Calculate relevance score for injection prioritisation.

    relevance_score =
      (p_s × scope_proximity)
    + (p_k × keyword_overlap)
    + (p_r × recent_retrieval)
    + (p_e × encoding_strength)
    """
    if config is None:
        config = UmxConfig()
    if keywords is None:
        keywords = []
    if session_fact_ids is None:
        session_fact_ids = set()

    # Scope proximity
    scope_prox = SCOPE_PROXIMITY.get(fact.scope, 0.5)

    # Keyword overlap: fraction of keywords that appear in fact text or tags
    kw_score = 0.0
    if keywords:
        text_lower = fact.text.lower()
        tag_set = {t.lower() for t in fact.tags}
        matches = sum(
            1 for kw in keywords
            if kw.lower() in text_lower or kw.lower() in tag_set
        )
        kw_score = matches / len(keywords)

    # Recent retrieval in this session
    recent_ret = 1.0 if fact.id in session_fact_ids else 0.0

    # Encoding strength normalised
    norm_strength = fact.encoding_strength / 5.0

    score = (
        config.relevance_scope_proximity * scope_prox
        + config.relevance_keyword_overlap * kw_score
        + config.relevance_recent_retrieval * recent_ret
        + config.relevance_encoding_strength * norm_strength
    )
    return round(score, 4)


def apply_corroboration(
    fact: Fact,
    corroborating_tool: str,
    other_confidence: float | None = None,
) -> Fact:
    """Apply corroboration bonus to a fact.

    - encoding_strength promoted (+1, capped at 4)
    - corroborated_by updated
    - confidence averaged across sources

    Corroboration alone cannot reach strength 5 (ground truth).
    """
    if corroborating_tool in fact.corroborated_by:
        return fact

    fact.corroborated_by.append(corroborating_tool)
    if fact.encoding_strength < EncodingStrength.DELIBERATE:
        fact.encoding_strength = min(fact.encoding_strength + 1, 4)

    if other_confidence is not None:
        fact.confidence = round(
            (fact.confidence + other_confidence) / 2.0, 4
        )

    return fact


def promote_to_ground_truth(fact: Fact) -> Fact:
    """Promote a fact to strength 5 (ground truth).

    Used when a user manually confirms or edits a fact.
    """
    fact.encoding_strength = EncodingStrength.GROUND_TRUTH
    return fact


def should_prune(
    fact: Fact,
    config: UmxConfig | None = None,
    now: datetime | None = None,
) -> bool:
    """Determine if a fact should be pruned.

    Facts below strength threshold are candidates. Time decay on
    uncorroborated low-strength facts (Ebbinghaus forgetting curve).
    """
    if config is None:
        config = UmxConfig()

    if fact.encoding_strength >= EncodingStrength.GROUND_TRUTH:
        return False

    if fact.encoding_strength < config.prune_strength_threshold:
        return True

    # Aggressive decay for low-strength uncorroborated facts
    if (
        fact.encoding_strength <= EncodingStrength.INFERRED
        and not fact.corroborated_by
    ):
        rec = recency_score(fact, now=now, decay_lambda=config.decay_lambda)
        if rec < 0.1:  # very stale
            return True

    return False
