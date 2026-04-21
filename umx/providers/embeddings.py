from __future__ import annotations

import hashlib
from typing import Any, Protocol

from umx.config import DEFAULT_EMBEDDING_PROVIDER, UMXConfig, default_config


class EmbeddingProvider(Protocol):
    name: str

    def is_available(self, config: UMXConfig) -> bool: ...

    def embed_text(self, text: str, config: UMXConfig) -> list[float] | None: ...


_MODEL_CACHE: dict[tuple[str, str], Any] = {}


class SentenceTransformersEmbeddingProvider:
    name = DEFAULT_EMBEDDING_PROVIDER

    def is_available(self, config: UMXConfig) -> bool:
        try:
            import sentence_transformers  # noqa: F401
        except Exception:
            return False
        return True

    def _get_model(self, config: UMXConfig):
        key = (self.name, config.search.embedding.model)
        cached = _MODEL_CACHE.get(key)
        if cached is not None:
            return cached
        if not self.is_available(config):
            return None
        try:
            from sentence_transformers import SentenceTransformer

            model = SentenceTransformer(config.search.embedding.model)
        except Exception:
            return None
        _MODEL_CACHE[key] = model
        return model

    def embed_text(self, text: str, config: UMXConfig) -> list[float] | None:
        model = self._get_model(config)
        if model is None:
            return None
        try:
            embedding = model.encode(text, convert_to_numpy=True)
            return embedding.tolist()
        except Exception:
            return None


class FixtureEmbeddingProvider:
    name = "fixture"

    def is_available(self, config: UMXConfig) -> bool:
        return True

    def embed_text(self, text: str, config: UMXConfig) -> list[float] | None:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        return [
            round(int.from_bytes(digest[index : index + 2], "big") / 65535.0, 6)
            for index in range(0, 6, 2)
        ]


_EMBEDDING_PROVIDERS: dict[str, EmbeddingProvider] = {
    SentenceTransformersEmbeddingProvider.name: SentenceTransformersEmbeddingProvider(),
    FixtureEmbeddingProvider.name: FixtureEmbeddingProvider(),
}


def resolve_embedding_provider(config: UMXConfig | None = None) -> EmbeddingProvider:
    cfg = config or default_config()
    name = (cfg.search.embedding.provider or DEFAULT_EMBEDDING_PROVIDER).strip() or DEFAULT_EMBEDDING_PROVIDER
    provider = _EMBEDDING_PROVIDERS.get(name)
    if provider is None:
        raise RuntimeError(f"unknown embedding provider: {name}")
    return provider
