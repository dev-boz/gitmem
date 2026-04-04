"""Tests for umx.budget — context budget enforcement."""

from __future__ import annotations

import pytest

from umx.budget import enforce_budget, estimate_fact_tokens, estimate_tokens
from umx.models import Fact, MemoryType, Scope, UmxConfig


def _make_fact(text: str, **kwargs) -> Fact:
    defaults = dict(
        id=Fact.generate_id(),
        scope=Scope.PROJECT_TEAM,
        topic="test",
        encoding_strength=3,
        memory_type=MemoryType.EXPLICIT_EPISODIC,
        confidence=0.8,
    )
    defaults.update(kwargs)
    return Fact(text=text, **defaults)


class TestEstimateTokens:
    def test_short_text(self):
        assert estimate_tokens("hello") >= 1

    def test_longer_text(self):
        text = "This is a longer piece of text that should have more tokens."
        tokens = estimate_tokens(text)
        assert tokens > 5

    def test_empty_text(self):
        assert estimate_tokens("") == 1  # Min 1


class TestEnforceBudget:
    def test_all_facts_fit(self):
        facts = [_make_fact("short fact") for _ in range(3)]
        selected = enforce_budget(facts, max_tokens=4000)
        assert len(selected) == 3

    def test_budget_exceeded_drops_last(self):
        # Create many facts that won't all fit in a tiny budget
        facts = [_make_fact(f"fact number {i} with some text") for i in range(100)]
        selected = enforce_budget(facts, max_tokens=100)
        assert len(selected) < 100
        assert len(selected) > 0

    def test_no_partial_facts(self):
        facts = [
            _make_fact("short"),
            _make_fact("x" * 4000),  # Very long fact
            _make_fact("also short"),
        ]
        selected = enforce_budget(facts, max_tokens=200)
        # The long fact should be skipped entirely, not truncated
        for f in selected:
            assert len(f.text) < 4000

    def test_empty_input(self):
        assert enforce_budget([], max_tokens=4000) == []

    def test_uses_config_default(self):
        config = UmxConfig(default_max_tokens=50)
        facts = [_make_fact(f"fact {i}") for i in range(100)]
        selected = enforce_budget(facts, config=config)
        assert len(selected) < 100
