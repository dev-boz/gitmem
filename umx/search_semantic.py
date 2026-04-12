from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

from umx.config import UMXConfig, default_config


CACHE_NAME = ".umx.json"
_MODEL_CACHE: dict[str, Any] = {}


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


def embeddings_available() -> bool:
    try:
        import sentence_transformers  # noqa: F401
    except Exception:
        return False
    return True


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


def _model_key(config: UMXConfig) -> str:
    return config.search.embedding.model


def _get_model(config: UMXConfig | None = None):
    cfg = config or default_config()
    key = _model_key(cfg)
    if key in _MODEL_CACHE:
        return _MODEL_CACHE[key]
    if not embeddings_available():
        return None
    try:
        from sentence_transformers import SentenceTransformer

        model = SentenceTransformer(key)
    except Exception:
        return None
    _MODEL_CACHE[key] = model
    return model


def embed_text(text: str, *, config: UMXConfig | None = None) -> list[float] | None:
    if not text.strip():
        return None
    cfg = config or default_config()
    model = _get_model(cfg)
    if model is None:
        return None
    try:
        embedding = model.encode(text, convert_to_numpy=True)
        return embedding.tolist()
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
    if not isinstance(entry, dict):
        return False
    embedding = entry.get("embedding")
    return (
        isinstance(embedding, list)
        and bool(embedding)
        and entry.get("embedding_model") == config.search.embedding.model
        and entry.get("embedding_model_version") == config.search.embedding.model_version
    )


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
) -> int:
    cfg = config or default_config()
    payload = load_semantic_cache(repo_dir)
    fact_cache = payload.setdefault("facts", {})
    if not isinstance(fact_cache, dict):
        fact_cache = {}
        payload["facts"] = fact_cache
    updated = 0
    for fact in facts:
        fact_id = getattr(fact, "fact_id", None)
        if not fact_id:
            continue
        if not force and _cache_entry_valid(fact_cache.get(fact_id), cfg):
            continue
        embedding = embed_fact(fact, config=cfg)
        if embedding is None:
            continue
        fact_cache[fact_id] = {
            "embedding": embedding,
            "embedding_model": cfg.search.embedding.model,
            "embedding_model_version": cfg.search.embedding.model_version,
            "embedded_at": datetime.now(tz=UTC).isoformat().replace("+00:00", "Z"),
        }
        updated += 1
    if updated:
        save_semantic_cache(repo_dir, payload)
    return updated


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
