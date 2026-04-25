from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

from umx.config import DEFAULT_EMBEDDING_PROVIDER, UMXConfig, default_config
from umx.providers.embeddings import embed_batch_texts, resolve_embedding_provider


CACHE_NAME = ".umx.json"
EMBEDDING_CONFIG_KEY = "embedding_config"


@dataclass(slots=True, frozen=True)
class EmbeddingSignature:
    provider: str
    model: str
    model_version: str

    def to_dict(self) -> dict[str, str]:
        return {
            "provider": self.provider,
            "model": self.model,
            "model_version": self.model_version,
        }

    def label(self) -> str:
        return f"{self.provider}:{self.model}@{self.model_version}"


@dataclass(slots=True, frozen=True)
class EmbeddingCacheState:
    signature: EmbeddingSignature
    state: str
    needs_rebuild: bool = False
    message: str | None = None


@dataclass(slots=True, frozen=True)
class EnsureEmbeddingsResult:
    updated: int
    needs_rebuild: bool = False
    message: str | None = None


def semantic_cache_path(repo_dir: Path) -> Path:
    return repo_dir / CACHE_NAME


def load_semantic_cache(repo_dir: Path) -> dict[str, Any]:
    path = semantic_cache_path(repo_dir)
    if not path.exists():
        return {"facts": {}}
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        return {"facts": {}}
    if not isinstance(data, dict):
        return {"facts": {}}
    facts = data.get("facts")
    if not isinstance(facts, dict):
        data["facts"] = {}
    return data


def save_semantic_cache(repo_dir: Path, payload: dict[str, Any]) -> None:
    path = semantic_cache_path(repo_dir)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _normalize_provider_name(value: Any) -> str | None:
    if value is None:
        return DEFAULT_EMBEDDING_PROVIDER
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or DEFAULT_EMBEDDING_PROVIDER


def current_embedding_signature(config: UMXConfig | None = None) -> EmbeddingSignature:
    cfg = config or default_config()
    return EmbeddingSignature(
        provider=_normalize_provider_name(cfg.search.embedding.provider) or DEFAULT_EMBEDDING_PROVIDER,
        model=cfg.search.embedding.model,
        model_version=cfg.search.embedding.model_version,
    )


def _signature_from_mapping(payload: Any) -> EmbeddingSignature | None:
    if not isinstance(payload, dict):
        return None
    provider = _normalize_provider_name(payload.get("provider"))
    model = payload.get("model")
    model_version = payload.get("model_version")
    if provider is None or not isinstance(model, str) or not model or not isinstance(model_version, str) or not model_version:
        return None
    return EmbeddingSignature(provider=provider, model=model, model_version=model_version)


def _entry_signature(entry: dict[str, Any] | None) -> EmbeddingSignature | None:
    if not isinstance(entry, dict):
        return None
    embedding = entry.get("embedding")
    if not isinstance(embedding, list) or not embedding:
        return None
    return _signature_from_mapping(
        {
            "provider": entry.get("embedding_provider"),
            "model": entry.get("embedding_model"),
            "model_version": entry.get("embedding_model_version"),
        }
    )


def _rebuild_required_message(stored: str, current: str) -> str:
    return (
        f"embedding config changed from {stored} to {current}; "
        "run `umx rebuild-index --embeddings`"
    )


def inspect_embedding_cache_state(
    repo_dir: Path,
    *,
    config: UMXConfig | None = None,
    cache: dict[str, Any] | None = None,
) -> EmbeddingCacheState:
    cfg = config or default_config()
    current = current_embedding_signature(cfg)
    payload = cache or load_semantic_cache(repo_dir)
    stored = _signature_from_mapping(payload.get(EMBEDDING_CONFIG_KEY))
    if stored is not None:
        if stored == current:
            return EmbeddingCacheState(signature=current, state="ready")
        return EmbeddingCacheState(
            signature=current,
            state="needs-rebuild",
            needs_rebuild=True,
            message=_rebuild_required_message(stored.label(), current.label()),
        )
    facts = payload.get("facts", {})
    if not isinstance(facts, dict):
        return EmbeddingCacheState(signature=current, state="empty")
    signatures = {
        signature
        for signature in (_entry_signature(entry) for entry in facts.values())
        if signature is not None
    }
    if not signatures:
        return EmbeddingCacheState(signature=current, state="empty")
    if signatures == {current}:
        return EmbeddingCacheState(signature=current, state="legacy-compatible")
    if len(signatures) == 1:
        stored_label = next(iter(signatures)).label()
    else:
        stored_label = "mixed cache (" + ", ".join(sorted(signature.label() for signature in signatures)) + ")"
    return EmbeddingCacheState(
        signature=current,
        state="needs-rebuild",
        needs_rebuild=True,
        message=_rebuild_required_message(stored_label, current.label()),
    )


def embedding_rebuild_message(repo_dir: Path, *, config: UMXConfig | None = None) -> str | None:
    state = inspect_embedding_cache_state(repo_dir, config=config)
    return state.message if state.needs_rebuild else None


def embeddings_available(config: UMXConfig | None = None) -> bool:
    cfg = config or default_config()
    try:
        provider = resolve_embedding_provider(cfg)
    except RuntimeError:
        return False
    return provider.is_available(cfg)


def cosine_similarity(left: Iterable[float], right: Iterable[float]) -> float:
    left_list = list(left)
    right_list = list(right)
    if not left_list or not right_list or len(left_list) != len(right_list):
        return 0.0
    dot = sum(a * b for a, b in zip(left_list, right_list))
    left_norm = math.sqrt(sum(a * a for a in left_list))
    right_norm = math.sqrt(sum(b * b for b in right_list))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot / (left_norm * right_norm)


def embed_text(text: str, *, config: UMXConfig | None = None) -> list[float] | None:
    if not text.strip():
        return None
    cfg = config or default_config()
    try:
        provider = resolve_embedding_provider(cfg)
    except RuntimeError:
        return None
    if not provider.is_available(cfg):
        return None
    try:
        return provider.embed_text(text, cfg)
    except Exception:
        return None


def _fact_input_text(fact: Any, config: UMXConfig | None = None) -> str:
    cfg = config or default_config()
    parts: list[str] = []
    for field in cfg.search.embedding.input_fields:
        if field == "text" and hasattr(fact, "text"):
            parts.append(str(fact.text))
        elif field == "topic" and hasattr(fact, "topic"):
            parts.append(str(fact.topic))
        elif field == "scope" and hasattr(fact, "scope"):
            scope_value = fact.scope.value if hasattr(fact.scope, "value") else str(fact.scope)
            parts.append(scope_value)
        elif hasattr(fact, field):
            parts.append(str(getattr(fact, field)))
    return " ".join(part for part in parts if part)


def embed_fact(fact: Any, *, config: UMXConfig | None = None) -> list[float] | None:
    return embed_text(_fact_input_text(fact, config=config), config=config)


def _cache_entry_valid(entry: dict[str, Any] | None, config: UMXConfig) -> bool:
    return _entry_signature(entry) == current_embedding_signature(config)


def cached_embedding(
    repo_dir: Path,
    fact_id: str,
    *,
    config: UMXConfig | None = None,
    cache: dict[str, Any] | None = None,
) -> list[float] | None:
    cfg = config or default_config()
    payload = cache or load_semantic_cache(repo_dir)
    facts = payload.get("facts", {})
    if not isinstance(facts, dict):
        return None
    entry = facts.get(fact_id)
    if not _cache_entry_valid(entry, cfg):
        return None
    embedding = entry.get("embedding")
    return list(embedding) if isinstance(embedding, list) else None


def ensure_embeddings(
    repo_dir: Path,
    facts: Iterable[Any],
    *,
    config: UMXConfig | None = None,
    force: bool = False,
    allow_config_mismatch: bool = False,
) -> EnsureEmbeddingsResult:
    cfg = config or default_config()
    payload = load_semantic_cache(repo_dir)
    state = inspect_embedding_cache_state(repo_dir, config=cfg, cache=payload)
    if state.needs_rebuild and not allow_config_mismatch:
        return EnsureEmbeddingsResult(updated=0, needs_rebuild=True, message=state.message)
    if state.needs_rebuild and not force:
        return EnsureEmbeddingsResult(updated=0, needs_rebuild=True, message=state.message)
    fact_list = list(facts)
    existing_cache = payload.get("facts", {})
    if not isinstance(existing_cache, dict):
        existing_cache = {}
    fact_cache = {} if force else dict(existing_cache)
    current = current_embedding_signature(cfg)
    updated = 0
    pending_facts: list[tuple[Any, str]] = []
    for fact in fact_list:
        fact_id = getattr(fact, "fact_id", None)
        if not fact_id:
            continue
        if not force and _cache_entry_valid(fact_cache.get(fact_id), cfg):
            continue
        text = _fact_input_text(fact, config=cfg)
        if not text.strip():
            continue
        pending_facts.append((fact, text))
    try:
        provider = resolve_embedding_provider(cfg)
    except RuntimeError:
        provider = None
    if provider is None:
        embeddings = [None] * len(pending_facts)
    elif callable(getattr(provider, "embed_batch", None)):
        embeddings = embed_batch_texts([text for _, text in pending_facts], provider, cfg)
    else:
        embeddings = [embed_fact(fact, config=cfg) for fact, _ in pending_facts]
    for (fact, _), embedding in zip(pending_facts, embeddings):
        if embedding is None:
            continue
        fact_id = getattr(fact, "fact_id", None)
        if not fact_id:
            continue
        fact_cache[fact_id] = {
            "embedding": embedding,
            "embedding_provider": current.provider,
            "embedding_model": cfg.search.embedding.model,
            "embedding_model_version": cfg.search.embedding.model_version,
            "embedded_at": datetime.now(tz=UTC).isoformat().replace("+00:00", "Z"),
        }
        updated += 1
    if updated or (force and not fact_list) or state.state == "legacy-compatible":
        payload["facts"] = fact_cache
        payload[EMBEDDING_CONFIG_KEY] = current.to_dict()
        save_semantic_cache(repo_dir, payload)
    return EnsureEmbeddingsResult(updated=updated)


def semantic_similarity_map(
    repo_dir: Path,
    facts: Iterable[Any],
    query: str,
    *,
    config: UMXConfig | None = None,
) -> dict[str, float]:
    if not query.strip():
        return {}
    cfg = config or default_config()
    query_embedding = embed_text(query, config=cfg)
    if query_embedding is None:
        return {}
    cache = load_semantic_cache(repo_dir)
    if inspect_embedding_cache_state(repo_dir, config=cfg, cache=cache).needs_rebuild:
        return {}
    similarities: dict[str, float] = {}
    for fact in facts:
        fact_id = getattr(fact, "fact_id", None)
        if not fact_id:
            continue
        fact_embedding = cached_embedding(repo_dir, fact_id, config=cfg, cache=cache)
        if fact_embedding is None:
            continue
        similarities[fact_id] = cosine_similarity(query_embedding, fact_embedding)
    return similarities


def rerank_candidates(
    candidates: list[tuple[str, float]],
    query: str = "",
    facts_by_id: dict[str, Any] | None = None,
    relevance_scores: dict[str, float] | None = None,
    config: UMXConfig | None = None,
    repo_dir: Path | None = None,
) -> list[tuple[str, float]]:
    if not candidates or not query:
        return candidates

    cfg = config or default_config()
    query_embedding = embed_text(query, config=cfg)
    if query_embedding is None:
        return candidates

    facts_by_id = facts_by_id or {}
    cache = load_semantic_cache(repo_dir) if repo_dir is not None else None
    if repo_dir is not None and cache is not None:
        if inspect_embedding_cache_state(repo_dir, config=cfg, cache=cache).needs_rebuild:
            return candidates
    semantic_weight = cfg.weights.relevance.semantic_similarity

    rescored: list[tuple[str, float]] = []
    for fact_id, lexical_score in candidates:
        semantic_score = 0.0
        if repo_dir is not None:
            fact_embedding = cached_embedding(
                repo_dir,
                fact_id,
                config=cfg,
                cache=cache,
            )
            if fact_embedding is not None:
                semantic_score = cosine_similarity(query_embedding, fact_embedding)
        base_score = relevance_scores.get(fact_id, lexical_score) if relevance_scores else lexical_score
        rescored.append((fact_id, base_score + semantic_weight * semantic_score))

    rescored.sort(key=lambda item: item[1], reverse=True)
    return rescored
