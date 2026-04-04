"""Tests for umx.models — core data models."""

from __future__ import annotations

import pytest
from datetime import datetime, timezone

from umx.models import (
    EncodingStrength,
    Fact,
    MemoryType,
    Scope,
    UmxConfig,
    ConflictEntry,
    SCOPE_PROXIMITY,
    DreamStatus,
)


class TestFact:
    def test_create_basic_fact(self):
        fact = Fact(
            id="f_001",
            text="postgres runs on port 5433",
            scope=Scope.PROJECT_TEAM,
            topic="devenv",
            encoding_strength=4,
            memory_type=MemoryType.EXPLICIT_SEMANTIC,
            confidence=0.97,
        )
        assert fact.id == "f_001"
        assert fact.text == "postgres runs on port 5433"
        assert fact.scope == Scope.PROJECT_TEAM
        assert fact.encoding_strength == 4
        assert fact.memory_type == MemoryType.EXPLICIT_SEMANTIC
        assert fact.confidence == 0.97
        assert fact.tags == []
        assert fact.corroborated_by == []
        assert fact.last_retrieved is None

    def test_invalid_encoding_strength(self):
        with pytest.raises(ValueError, match="encoding_strength must be 1-5"):
            Fact(
                id="f_bad",
                text="bad",
                scope=Scope.PROJECT_TEAM,
                topic="test",
                encoding_strength=6,
                memory_type=MemoryType.IMPLICIT,
                confidence=0.5,
            )

    def test_invalid_encoding_strength_zero(self):
        with pytest.raises(ValueError, match="encoding_strength must be 1-5"):
            Fact(
                id="f_bad",
                text="bad",
                scope=Scope.PROJECT_TEAM,
                topic="test",
                encoding_strength=0,
                memory_type=MemoryType.IMPLICIT,
                confidence=0.5,
            )

    def test_invalid_confidence(self):
        with pytest.raises(ValueError, match="confidence must be 0.0-1.0"):
            Fact(
                id="f_bad",
                text="bad",
                scope=Scope.PROJECT_TEAM,
                topic="test",
                encoding_strength=3,
                memory_type=MemoryType.IMPLICIT,
                confidence=1.5,
            )

    def test_generate_id(self):
        id1 = Fact.generate_id()
        id2 = Fact.generate_id()
        assert id1.startswith("f_")
        assert id2.startswith("f_")
        assert id1 != id2
        assert len(id1) == 10  # f_ + 8 hex chars

    def test_to_dict_roundtrip(self):
        now = datetime(2026, 4, 3, 20, 11, tzinfo=timezone.utc)
        fact = Fact(
            id="f_001",
            text="postgres runs on port 5433",
            scope=Scope.PROJECT_TEAM,
            topic="devenv",
            encoding_strength=4,
            memory_type=MemoryType.EXPLICIT_SEMANTIC,
            confidence=0.97,
            tags=["database", "environment"],
            source_tool="claude-code",
            corroborated_by=["aider"],
            last_retrieved=now,
            created=now,
        )
        d = fact.to_dict()
        restored = Fact.from_dict(d)

        assert restored.id == fact.id
        assert restored.text == fact.text
        assert restored.scope == fact.scope
        assert restored.encoding_strength == fact.encoding_strength
        assert restored.memory_type == fact.memory_type
        assert restored.confidence == fact.confidence
        assert restored.tags == fact.tags
        assert restored.source_tool == fact.source_tool
        assert restored.corroborated_by == fact.corroborated_by
        assert restored.last_retrieved == fact.last_retrieved
        assert restored.created == fact.created

    def test_to_dict_none_last_retrieved(self):
        fact = Fact(
            id="f_002",
            text="test",
            scope=Scope.USER,
            topic="test",
            encoding_strength=1,
            memory_type=MemoryType.IMPLICIT,
            confidence=0.5,
        )
        d = fact.to_dict()
        assert d["last_retrieved"] is None
        restored = Fact.from_dict(d)
        assert restored.last_retrieved is None


class TestEnums:
    def test_memory_type_values(self):
        assert MemoryType.EXPLICIT_SEMANTIC.value == "explicit_semantic"
        assert MemoryType.EXPLICIT_EPISODIC.value == "explicit_episodic"
        assert MemoryType.IMPLICIT.value == "implicit"

    def test_encoding_strength_values(self):
        assert EncodingStrength.INCIDENTAL == 1
        assert EncodingStrength.INFERRED == 2
        assert EncodingStrength.EXTRACTED == 3
        assert EncodingStrength.DELIBERATE == 4
        assert EncodingStrength.GROUND_TRUTH == 5

    def test_scope_values(self):
        assert Scope.FILE.value == "file"
        assert Scope.FOLDER.value == "folder"
        assert Scope.PROJECT_LOCAL.value == "project_local"
        assert Scope.PROJECT_TEAM.value == "project_team"
        assert Scope.TOOL.value == "tool"
        assert Scope.USER.value == "user"

    def test_scope_proximity(self):
        assert SCOPE_PROXIMITY[Scope.FILE] == 1.0
        assert SCOPE_PROXIMITY[Scope.USER] == 0.2
        assert SCOPE_PROXIMITY[Scope.FILE] > SCOPE_PROXIMITY[Scope.FOLDER]
        assert SCOPE_PROXIMITY[Scope.FOLDER] > SCOPE_PROXIMITY[Scope.PROJECT_TEAM]

    def test_dream_status(self):
        assert DreamStatus.FULL.value == "full"
        assert DreamStatus.PARTIAL.value == "partial"
        assert DreamStatus.NATIVE_ONLY.value == "native_only"


class TestUmxConfig:
    def test_defaults(self):
        cfg = UmxConfig()
        assert cfg.decay_lambda == 0.023
        assert cfg.dream_time_hours == 24
        assert cfg.dream_session_threshold == 5
        assert cfg.default_max_tokens == 4000
        assert cfg.prune_strength_threshold == 1

    def test_from_dict(self):
        cfg = UmxConfig.from_dict({
            "decay_lambda": 0.046,
            "dream_time_hours": 12,
            "default_max_tokens": 8000,
        })
        assert cfg.decay_lambda == 0.046
        assert cfg.dream_time_hours == 12
        assert cfg.default_max_tokens == 8000
        # Unchanged defaults
        assert cfg.dream_session_threshold == 5

    def test_from_dict_ignores_unknown(self):
        cfg = UmxConfig.from_dict({"unknown_field": "value"})
        assert not hasattr(cfg, "unknown_field")


class TestConflictEntry:
    def test_create(self):
        fact_a = Fact(
            id="f_001", text="port 5433", scope=Scope.PROJECT_TEAM,
            topic="devenv", encoding_strength=4,
            memory_type=MemoryType.EXPLICIT_SEMANTIC, confidence=0.97,
        )
        fact_b = Fact(
            id="f_002", text="port 5432", scope=Scope.PROJECT_TEAM,
            topic="devenv", encoding_strength=2,
            memory_type=MemoryType.IMPLICIT, confidence=0.7,
        )
        conflict = ConflictEntry(
            topic="devenv",
            description="port mismatch",
            fact_a=fact_a,
            fact_b=fact_b,
        )
        assert conflict.status == "OPEN"
        assert conflict.topic == "devenv"
