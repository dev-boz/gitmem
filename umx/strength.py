from __future__ import annotations

import math
import re
from datetime import datetime

from umx.config import UMXConfig, default_config
from umx.models import (
    ConsolidationStatus,
    Fact,
    Scope,
    SourceType,
    TaskStatus,
    Verification,
)


SOURCE_TYPE_WEIGHTS = {
    SourceType.GROUND_TRUTH_CODE: 1.5,
    SourceType.TOOL_OUTPUT: 0.5,
    SourceType.EXTERNAL_DOC: 0.5,
    SourceType.USER_PROMPT: 0.3,
    SourceType.DREAM_CONSOLIDATION: 0.0,
    SourceType.LLM_INFERENCE: 0.0,
}

VERIFICATION_BONUS = {
    Verification.SELF_REPORTED: 0.0,
    Verification.CORROBORATED: 0.5,
    Verification.SOTA_REVIEWED: 1.0,
    Verification.HUMAN_CONFIRMED: 1.5,
}

SCOPE_PROXIMITY = {
    Scope.FILE: 4.0,
    Scope.FOLDER: 3.0,
    Scope.PROJECT: 2.0,
    Scope.PROJECT_PRIVATE: 2.0,
    Scope.TOOL: 1.5,
    Scope.MACHINE: 1.2,
    Scope.USER: 1.0,
    Scope.PROJECT_SECRET: 0.0,
}


def recency_value(created_at: datetime, now: datetime, decay_lambda: float) -> float:
    age_days = max(0.0, (now - created_at).total_seconds() / 86400)
    return math.exp(-decay_lambda * age_days)


def decay_lambda_for_fact(fact: Fact, config: UMXConfig | None = None) -> float:
    cfg = config or default_config()
    if fact.repo and fact.repo in cfg.decay.per_project:
        return float(cfg.decay.per_project[fact.repo])
    return float(cfg.decay.decay_lambda)


def recency_score(fact: Fact, now: datetime, config: UMXConfig | None = None) -> float:
    reference = fact.last_referenced or fact.created
    return recency_value(reference, now, decay_lambda_for_fact(fact, config))


def verification_bonus(value: Verification) -> float:
    return VERIFICATION_BONUS[value]


def source_type_weight(value: SourceType, corroborating_source_weights: list[float] | None = None) -> float:
    if value == SourceType.DREAM_CONSOLIDATION and corroborating_source_weights:
        return sum(corroborating_source_weights) / len(corroborating_source_weights)
    return SOURCE_TYPE_WEIGHTS[value]


def trust_score(fact: Fact, config: UMXConfig | None = None) -> float:
    cfg = config or default_config()
    weights = cfg.weights.trust
    score = (
        weights.strength * fact.encoding_strength
        + weights.corroboration * len(fact.corroborated_by_tools + fact.corroborated_by_facts)
        + weights.verification * verification_bonus(fact.verification)
        + weights.source_type * source_type_weight(fact.source_type)
    )
    if fact.consolidation_status == ConsolidationStatus.FRAGILE:
        score -= 0.25
    return score


def relevance_score(
    fact: Fact,
    target_scope: Scope,
    keywords: set[str] | None = None,
    recent_retrieval: float = 0.0,
    semantic_similarity: float = 0.0,
    config: UMXConfig | None = None,
) -> float:
    cfg = config or default_config()
    weights = cfg.weights.relevance
    keywords = keywords or set()
    scope_distance = abs(SCOPE_PROXIMITY[fact.scope] - SCOPE_PROXIMITY[target_scope])
    scope_component = max(0.0, 1.0 - 0.25 * scope_distance)
    text_terms = set(re.findall(r"[a-zA-Z0-9_]+", fact.text.lower()))
    tag_terms = {tag.lower() for tag in fact.tags}
    overlap_count = len(keywords & (text_terms | tag_terms))
    keyword_component = overlap_count / max(1, len(keywords))
    task_bonus = 1.0 if fact.task_status in {TaskStatus.OPEN, TaskStatus.BLOCKED} else 0.0
    return (
        weights.scope_proximity * scope_component
        + weights.keyword_overlap * keyword_component
        + weights.recent_retrieval * recent_retrieval
        + weights.encoding_strength * (fact.encoding_strength / 5.0)
        + weights.context_match * 0.0
        + weights.task_salience * task_bonus
        + weights.semantic_similarity * semantic_similarity
    )


def retention_score(
    fact: Fact,
    now: datetime,
    usage_frequency: int = 0,
    config: UMXConfig | None = None,
) -> float:
    cfg = config or default_config()
    weights = cfg.weights.retention
    return (
        weights.strength * fact.encoding_strength
        + weights.recency * recency_score(fact, now, cfg)
        + weights.usage_frequency * usage_frequency
        + weights.verification * verification_bonus(fact.verification)
    )


def independent_corroboration(existing: Fact, incoming: Fact) -> bool:
    if (
        "bridge" in existing.provenance.extracted_by
        or "bridge" in incoming.provenance.extracted_by
    ):
        return False
    if (
        existing.source_session == incoming.source_session
        and existing.source_type == incoming.source_type
    ):
        return False
    if existing.source_session != incoming.source_session and existing.source_tool != incoming.source_tool:
        return True
    if existing.source_tool == incoming.source_tool and existing.source_session != incoming.source_session:
        if not existing.created or not incoming.created:
            return True
        return abs((incoming.created - existing.created).total_seconds()) >= 86400
    return False


def apply_corroboration(existing: Fact, incoming: Fact) -> Fact:
    updated = existing.clone()
    if incoming.source_tool not in updated.corroborated_by_tools:
        updated.corroborated_by_tools.append(incoming.source_tool)
    if incoming.fact_id not in updated.corroborated_by_facts:
        updated.corroborated_by_facts.append(incoming.fact_id)
    anchored = (
        existing.source_type == SourceType.GROUND_TRUTH_CODE
        or incoming.source_type == SourceType.GROUND_TRUTH_CODE
        or existing.verification == Verification.HUMAN_CONFIRMED
        or incoming.verification == Verification.HUMAN_CONFIRMED
    )
    if updated.encoding_strength < 4:
        updated.encoding_strength += 1
    if updated.encoding_strength >= 4 and not anchored:
        updated.encoding_strength = 3
    if existing.verification == Verification.HUMAN_CONFIRMED or incoming.verification == Verification.HUMAN_CONFIRMED:
        updated.verification = Verification.HUMAN_CONFIRMED
    else:
        updated.verification = Verification.CORROBORATED
    updated.confidence = round((updated.confidence + incoming.confidence) / 2.0, 4)
    return updated


def should_prune(fact: Fact, now: datetime, usage_frequency: int = 0, config: UMXConfig | None = None) -> bool:
    cfg = config or default_config()
    if fact.expires_at and fact.expires_at <= now:
        return True
    age_days = max(0.0, (now - fact.created).total_seconds() / 86400)
    if age_days < cfg.prune.min_age_days:
        return False
    if fact.encoding_strength >= 5:
        return False
    if fact.task_status in {TaskStatus.OPEN, TaskStatus.BLOCKED}:
        return False
    return retention_score(fact, now, usage_frequency=usage_frequency, config=cfg) < cfg.prune.threshold


def conflict_winner(left: Fact, right: Fact, config: UMXConfig | None = None) -> Fact:
    # Ground-truth hard rule: ground_truth_code MUST NOT lose to ANY other source type
    if (
        left.source_type == SourceType.GROUND_TRUTH_CODE
        and right.source_type != SourceType.GROUND_TRUTH_CODE
    ):
        return left
    if (
        right.source_type == SourceType.GROUND_TRUTH_CODE
        and left.source_type != SourceType.GROUND_TRUTH_CODE
    ):
        return right
    return left if trust_score(left, config) >= trust_score(right, config) else right
