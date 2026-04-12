"""Tests for umx.strength — composite scoring, relevance, corroboration."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest

from umx.models import EncodingStrength, Fact, MemoryType, Scope, UmxConfig
from umx.strength import (
    apply_corroboration,
    composite_score,
    promote_to_ground_truth,
    recency_score,
    relevance_score,
    should_prune,
)


def _make_fact(**kwargs) -> Fact:
    defaults = dict(
        id="f_test",
        text="test fact",
        scope=Scope.PROJECT_TEAM,
        topic="test",
        encoding_strength=3,
        memory_type=MemoryType.EXPLICIT_EPISODIC,
        confidence=0.8,
        created=datetime.now(timezone.utc),
    )
    defaults.update(kwargs)
    return Fact(**defaults)


class TestRecencyScore:
    def test_brand_new_fact(self):
        now = datetime.now(timezone.utc)
        fact = _make_fact(created=now)
        score = recency_score(fact, now=now)
        assert score == pytest.approx(1.0, abs=0.01)

    def test_30_day_old_fact_default_lambda(self):
        now = datetime.now(timezone.utc)
        fact = _make_fact(created=now - timedelta(days=30))
        score = recency_score(fact, now=now, decay_lambda=0.023)
        assert score == pytest.approx(0.50, abs=0.05)

    def test_7_day_old_fact(self):
        now = datetime.now(timezone.utc)
        fact = _make_fact(created=now - timedelta(days=7))
        score = recency_score(fact, now=now, decay_lambda=0.023)
        assert score == pytest.approx(0.85, abs=0.05)

    def test_90_day_old_fact(self):
        now = datetime.now(timezone.utc)
        fact = _make_fact(created=now - timedelta(days=90))
        score = recency_score(fact, now=now, decay_lambda=0.023)
        assert score == pytest.approx(0.13, abs=0.05)

    def test_uses_last_retrieved_over_created(self):
        now = datetime.now(timezone.utc)
        fact = _make_fact(
            created=now - timedelta(days=90),
            last_retrieved=now - timedelta(days=1),
        )
        score = recency_score(fact, now=now)
        assert score > 0.9  # Recent retrieval means high recency

    def test_fast_decay_lambda(self):
        now = datetime.now(timezone.utc)
        fact = _make_fact(created=now - timedelta(days=15))
        score = recency_score(fact, now=now, decay_lambda=0.046)
        assert score == pytest.approx(0.50, abs=0.05)

    def test_slow_decay_lambda(self):
        now = datetime.now(timezone.utc)
        fact = _make_fact(created=now - timedelta(days=69))
        score = recency_score(fact, now=now, decay_lambda=0.010)
        assert score == pytest.approx(0.50, abs=0.05)


class TestCompositeScore:
    def test_high_strength_high_confidence(self):
        fact = _make_fact(encoding_strength=5, confidence=0.99)
        score = composite_score(fact)
        assert score > 0.5

    def test_low_strength_low_confidence(self):
        fact = _make_fact(encoding_strength=1, confidence=0.3)
        score = composite_score(fact)
        low_score = score

        fact2 = _make_fact(encoding_strength=5, confidence=0.99)
        high_score = composite_score(fact2)

        assert high_score > low_score

    def test_corroboration_increases_score(self):
        fact1 = _make_fact(corroborated_by=[])
        fact2 = _make_fact(corroborated_by=["aider", "claude-code"])

        assert composite_score(fact2) > composite_score(fact1)

    def test_custom_weights(self):
        config = UmxConfig(
            weight_strength=0.8,
            weight_confidence=0.1,
            weight_recency=0.05,
            weight_corroboration=0.05,
        )
        fact = _make_fact(encoding_strength=5, confidence=0.5)
        score = composite_score(fact, config=config)
        # Strength dominates with this config
        assert score > 0.6

    def test_stale_fact_penalized(self):
        now = datetime.now(timezone.utc)
        fresh = _make_fact(
            encoding_strength=3, confidence=0.8,
            created=now,
        )
        stale = _make_fact(
            encoding_strength=3, confidence=0.8,
            created=now - timedelta(days=180),
        )
        assert composite_score(fresh, now=now) > composite_score(stale, now=now)


class TestRelevanceScore:
    def test_file_scope_highest_proximity(self):
        fact = _make_fact(scope=Scope.FILE)
        score = relevance_score(fact, target_scope=Scope.FILE)
        assert score > 0

    def test_keyword_match_increases_relevance(self):
        fact = _make_fact(
            text="postgres runs on port 5433",
            tags=["database"],
        )
        no_kw = relevance_score(fact, target_scope=Scope.PROJECT_TEAM)
        with_kw = relevance_score(
            fact, target_scope=Scope.PROJECT_TEAM,
            keywords=["postgres", "database"],
        )
        assert with_kw > no_kw

    def test_no_keyword_match(self):
        fact = _make_fact(text="postgres port 5433")
        score = relevance_score(
            fact, target_scope=Scope.PROJECT_TEAM,
            keywords=["redis", "cache"],
        )
        # Should still have base relevance from scope + strength
        assert score > 0

    def test_session_fact_ids_boost(self):
        fact = _make_fact(id="f_recent")
        without = relevance_score(fact, target_scope=Scope.PROJECT_TEAM)
        with_ids = relevance_score(
            fact, target_scope=Scope.PROJECT_TEAM,
            session_fact_ids={"f_recent"},
        )
        assert with_ids > without


class TestCorroboration:
    def test_apply_corroboration_adds_tool(self):
        fact = _make_fact(encoding_strength=3, corroborated_by=[])
        fact = apply_corroboration(fact, "aider")
        assert "aider" in fact.corroborated_by

    def test_corroboration_promotes_strength(self):
        fact = _make_fact(encoding_strength=3, corroborated_by=[])
        fact = apply_corroboration(fact, "aider")
        assert fact.encoding_strength == 4

    def test_corroboration_capped_at_4(self):
        fact = _make_fact(encoding_strength=4, corroborated_by=["aider"])
        fact = apply_corroboration(fact, "gemini")
        assert fact.encoding_strength == 4  # Cannot exceed 4

    def test_corroboration_averages_confidence(self):
        fact = _make_fact(confidence=0.9, corroborated_by=[])
        fact = apply_corroboration(fact, "aider", other_confidence=0.7)
        assert fact.confidence == pytest.approx(0.8, abs=0.01)

    def test_duplicate_corroboration_noop(self):
        fact = _make_fact(
            encoding_strength=3,
            confidence=0.9,
            corroborated_by=["aider"],
        )
        fact = apply_corroboration(fact, "aider")
        assert fact.corroborated_by == ["aider"]
        assert fact.encoding_strength == 3  # No change

    def test_promote_to_ground_truth(self):
        fact = _make_fact(encoding_strength=3)
        fact = promote_to_ground_truth(fact)
        assert fact.encoding_strength == EncodingStrength.GROUND_TRUTH


class TestPruning:
    def test_ground_truth_never_pruned(self):
        fact = _make_fact(encoding_strength=5)
        assert not should_prune(fact)

    def test_below_threshold_pruned(self):
        config = UmxConfig(prune_strength_threshold=2)
        fact = _make_fact(encoding_strength=1)
        assert should_prune(fact, config=config)

    def test_stale_uncorroborated_weak_fact_pruned(self):
        now = datetime.now(timezone.utc)
        fact = _make_fact(
            encoding_strength=2,
            corroborated_by=[],
            created=now - timedelta(days=200),
        )
        assert should_prune(fact, now=now)

    def test_corroborated_weak_fact_not_pruned(self):
        now = datetime.now(timezone.utc)
        fact = _make_fact(
            encoding_strength=2,
            corroborated_by=["claude-code"],
            created=now - timedelta(days=200),
        )
        assert not should_prune(fact, now=now)
