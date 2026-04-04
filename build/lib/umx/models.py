"""Core data models for umx.

Defines the Fact schema, enums, and configuration dataclasses used throughout
the system. Grounded in Tulving's episodic/semantic and Schacter's
explicit/implicit memory taxonomy.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class MemoryType(str, Enum):
    """Memory type from cognitive science taxonomy."""

    EXPLICIT_SEMANTIC = "explicit_semantic"
    EXPLICIT_EPISODIC = "explicit_episodic"
    IMPLICIT = "implicit"


class EncodingStrength(int, Enum):
    """Encoding strength levels (1-5).

    Higher = more deliberately encoded = more reliable.
    """

    INCIDENTAL = 1   # single transcript mention, unconfirmed
    INFERRED = 2     # repeated pattern across multiple logs
    EXTRACTED = 3    # dream pipeline from session transcript
    DELIBERATE = 4   # tool native memory (LLM intentionally wrote it)
    GROUND_TRUTH = 5  # user manually edited in viewer


class Scope(str, Enum):
    """Memory scope hierarchy, most specific first."""

    FILE = "file"
    FOLDER = "folder"
    PROJECT_LOCAL = "project_local"
    PROJECT_TEAM = "project_team"
    TOOL = "tool"
    USER = "user"


# Numeric proximity for relevance scoring (higher = more specific)
SCOPE_PROXIMITY: dict[Scope, float] = {
    Scope.FILE: 1.0,
    Scope.FOLDER: 0.8,
    Scope.PROJECT_LOCAL: 0.7,
    Scope.PROJECT_TEAM: 0.6,
    Scope.TOOL: 0.4,
    Scope.USER: 0.2,
}


class DreamStatus(str, Enum):
    """Status of a dream run."""

    FULL = "full"
    PARTIAL = "partial"
    NATIVE_ONLY = "native_only"
    FAILED = "failed"


@dataclass
class Fact:
    """An atomic memory fact.

    Facts must remain atomic — never merge multiple facts into a single
    narrative statement.
    """

    id: str
    text: str
    scope: Scope
    topic: str
    encoding_strength: int  # 1-5
    memory_type: MemoryType
    confidence: float  # 0.0-1.0, extractor certainty
    tags: list[str] = field(default_factory=list)
    source_tool: str = ""
    source_session: str = ""
    corroborated_by: list[str] = field(default_factory=list)
    last_retrieved: datetime | None = None
    created: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        if not 1 <= self.encoding_strength <= 5:
            raise ValueError(
                f"encoding_strength must be 1-5, got {self.encoding_strength}"
            )
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(
                f"confidence must be 0.0-1.0, got {self.confidence}"
            )

    @staticmethod
    def generate_id() -> str:
        """Generate a unique fact ID."""
        return f"f_{uuid.uuid4().hex[:8]}"

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "id": self.id,
            "text": self.text,
            "scope": self.scope.value,
            "topic": self.topic,
            "encoding_strength": self.encoding_strength,
            "memory_type": self.memory_type.value,
            "confidence": self.confidence,
            "tags": self.tags,
            "source_tool": self.source_tool,
            "source_session": self.source_session,
            "corroborated_by": self.corroborated_by,
            "last_retrieved": (
                self.last_retrieved.isoformat() if self.last_retrieved else None
            ),
            "created": self.created.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Fact:
        """Deserialize from dictionary."""
        d = dict(data)
        d["scope"] = Scope(d["scope"])
        d["memory_type"] = MemoryType(d["memory_type"])
        if d.get("last_retrieved"):
            d["last_retrieved"] = datetime.fromisoformat(d["last_retrieved"])
        else:
            d["last_retrieved"] = None
        if d.get("created"):
            d["created"] = datetime.fromisoformat(d["created"])
        else:
            d["created"] = datetime.now(timezone.utc)
        return cls(**d)


@dataclass
class UmxConfig:
    """Configuration for umx, loaded from .umx/config.yaml or ~/.umx/config.yaml."""

    # Composite score weights
    weight_strength: float = 0.4
    weight_confidence: float = 0.2
    weight_recency: float = 0.2
    weight_corroboration: float = 0.2

    # Relevance score weights
    relevance_scope_proximity: float = 0.3
    relevance_keyword_overlap: float = 0.3
    relevance_recent_retrieval: float = 0.2
    relevance_encoding_strength: float = 0.2

    # Decay
    decay_lambda: float = 0.023  # ~30 day half-life

    # Dream gates
    dream_time_hours: int = 24
    dream_session_threshold: int = 5

    # Prune
    prune_strength_threshold: int = 1
    memory_md_max_lines: int = 200
    memory_md_max_bytes: int = 25 * 1024  # 25 KB

    # Context budget
    default_max_tokens: int = 4000

    # LLM providers
    llm_providers: list[str] = field(
        default_factory=lambda: [
            "cerebras",
            "groq",
            "glm",
            "minimax",
            "openrouter",
        ]
    )
    local_llm_endpoint: str = ""
    local_llm_model: str = ""

    # Legacy bridge
    bridge_targets: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UmxConfig:
        """Create config from a dictionary (parsed YAML)."""
        cfg = cls()
        for k, v in data.items():
            if hasattr(cfg, k):
                setattr(cfg, k, v)
        return cfg


@dataclass
class TopicIndex:
    """A row in the MEMORY.md index table."""

    topic: str
    file: str
    updated: str
    avg_strength: float


@dataclass
class ConflictEntry:
    """A conflict between two facts."""

    topic: str
    description: str
    fact_a: Fact
    fact_b: Fact
    resolution: str = ""
    status: str = "OPEN"  # OPEN | RESOLVED
