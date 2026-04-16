from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(slots=True)
class ProjectConfig:
    slug_format: str = "name"


@dataclass(slots=True)
class DreamConfig:
    mode: str = "local"
    provider_rotation: list[str] = field(
        default_factory=lambda: ["cerebras", "groq", "glm", "minimax", "openrouter"]
    )
    local_model: str | None = None
    paid_provider: str | None = None
    paid_api_key: str | None = None
    lint_interval: str = "weekly"


@dataclass(slots=True)
class DecayConfig:
    decay_lambda: float = 0.023
    per_project: dict[str, float] = field(default_factory=dict)


@dataclass(slots=True)
class PruneConfig:
    threshold: int = 2
    min_age_days: int = 7
    abandon_days: int = 30


@dataclass(slots=True)
class MemoryConfig:
    index_max_lines: int = 200
    hot_tier_max_tokens: int = 3000


@dataclass(slots=True)
class RetentionConfig:
    active_days: int = 90
    compression: str = "gzip"


@dataclass(slots=True)
class SessionsConfig:
    redaction: str = "default"
    redaction_patterns: list[str] = field(default_factory=list)
    entropy_threshold: float = 4.5
    entropy_min_length: int = 16
    entropy_assignment_patterns: list[str] = field(default_factory=list)
    retention: RetentionConfig = field(default_factory=RetentionConfig)


@dataclass(slots=True)
class InjectConfig:
    min_facts: int = 3
    refresh_window_pct: float = 0.25
    max_refreshes_per_fact: int = 3
    max_concurrent_facts: int = 12
    pre_tool_max_tokens: int = 1000
    subagent_max_tokens: int = 2000
    subagent_hot_tokens: int = 1500
    turn_token_estimate: int = 250


@dataclass(slots=True)
class SearchEmbeddingConfig:
    model: str = "all-MiniLM-L6-v2"
    model_version: str = "v1.0"
    input_fields: list[str] = field(default_factory=lambda: ["text", "topic", "scope"])
    candidate_limit: int = 100


@dataclass(slots=True)
class SearchConfig:
    rebuild: str = "incremental"
    backend: str = "fts5"
    embedding: SearchEmbeddingConfig = field(default_factory=SearchEmbeddingConfig)


@dataclass(slots=True)
class BridgeConfig:
    enabled: bool = False
    targets: list[str] = field(default_factory=lambda: ["CLAUDE.md", "AGENTS.md", ".cursorrules"])
    max_facts: int = 20


@dataclass(slots=True)
class GitConfig:
    sign_commits: bool = False
    require_signed_commits: bool = False


@dataclass(slots=True)
class TrustWeights:
    strength: float = 1.0
    corroboration: float = 0.4
    verification: float = 0.3
    source_type: float = 0.4


@dataclass(slots=True)
class RelevanceWeights:
    scope_proximity: float = 1.0
    keyword_overlap: float = 0.8
    recent_retrieval: float = 0.3
    encoding_strength: float = 0.5
    context_match: float = 0.0
    task_salience: float = 0.5
    semantic_similarity: float = 0.3


@dataclass(slots=True)
class RetentionWeights:
    strength: float = 1.0
    recency: float = 0.3
    usage_frequency: float = 0.4
    verification: float = 0.3


@dataclass(slots=True)
class WeightConfig:
    trust: TrustWeights = field(default_factory=TrustWeights)
    relevance: RelevanceWeights = field(default_factory=RelevanceWeights)
    retention: RetentionWeights = field(default_factory=RetentionWeights)


@dataclass(slots=True)
class UMXConfig:
    org: str | None = None
    github_token: str | None = None
    project: ProjectConfig = field(default_factory=ProjectConfig)
    dream: DreamConfig = field(default_factory=DreamConfig)
    decay: DecayConfig = field(default_factory=DecayConfig)
    prune: PruneConfig = field(default_factory=PruneConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    sessions: SessionsConfig = field(default_factory=SessionsConfig)
    inject: InjectConfig = field(default_factory=InjectConfig)
    search: SearchConfig = field(default_factory=SearchConfig)
    bridge: BridgeConfig = field(default_factory=BridgeConfig)
    git: GitConfig = field(default_factory=GitConfig)
    weights: WeightConfig = field(default_factory=WeightConfig)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["decay"]["lambda"] = data["decay"].pop("decay_lambda")
        return data


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


NESTED_TYPES: dict[type[Any], dict[str, type[Any]]] = {
    UMXConfig: {
        "project": ProjectConfig,
        "dream": DreamConfig,
        "decay": DecayConfig,
        "prune": PruneConfig,
        "memory": MemoryConfig,
        "sessions": SessionsConfig,
        "inject": InjectConfig,
        "search": SearchConfig,
        "bridge": BridgeConfig,
        "git": GitConfig,
        "weights": WeightConfig,
    },
    SessionsConfig: {"retention": RetentionConfig},
    SearchConfig: {"embedding": SearchEmbeddingConfig},
    WeightConfig: {
        "trust": TrustWeights,
        "relevance": RelevanceWeights,
        "retention": RetentionWeights,
    },
}


def _from_dict(cls: type[Any], data: dict[str, Any]) -> Any:
    kwargs: dict[str, Any] = {}
    nested = NESTED_TYPES.get(cls, {})
    for name, field_def in cls.__dataclass_fields__.items():  # type: ignore[attr-defined]
        if name not in data:
            continue
        value = data[name]
        if name in nested and isinstance(value, dict):
            kwargs[name] = _from_dict(nested[name], value)
        else:
            kwargs[name] = value
    return cls(**kwargs)


def default_config() -> UMXConfig:
    return UMXConfig()


def load_config(path: Path | None) -> UMXConfig:
    if path is None or not path.exists():
        return default_config()
    raw = yaml.safe_load(path.read_text()) or {}
    if "decay" in raw and "lambda" in raw["decay"]:
        raw["decay"]["decay_lambda"] = raw["decay"].pop("lambda")
    if "inject" in raw:
        inject = raw["inject"]
        if "redisclose_pct" in inject and "refresh_window_pct" not in inject:
            inject["refresh_window_pct"] = inject.pop("redisclose_pct")
        if "max_redisclosures" in inject and "max_refreshes_per_fact" not in inject:
            inject["max_refreshes_per_fact"] = inject.pop("max_redisclosures")
    merged = _deep_merge(default_config().to_dict(), raw)
    if "decay" in merged and "lambda" in merged["decay"]:
        merged["decay"]["decay_lambda"] = merged["decay"].pop("lambda")
    return _from_dict(UMXConfig, merged)


def save_config(path: Path, config: UMXConfig) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(config.to_dict(), sort_keys=False))
