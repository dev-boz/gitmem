from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from umx.config import DEFAULT_EMBEDDING_PROVIDER, UMXConfig, default_config


class EmbeddingProvider(Protocol):
    name: str

    def is_available(self, config: UMXConfig) -> bool: ...

    def embed_text(self, text: str, config: UMXConfig) -> list[float] | None: ...


_MODEL_CACHE: dict[tuple[str, str], Any] = {}
_HTTP_TIMEOUT_SECONDS = 30
_OPENAI_API_KEY_ENVS: tuple[str, ...] = ("UMX_OPENAI_API_KEY", "OPENAI_API_KEY")
_VOYAGE_API_KEY_ENVS: tuple[str, ...] = ("UMX_VOYAGE_API_KEY", "VOYAGE_API_KEY")


def _first_env(*names: str) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value and value.strip():
            return value.strip()
    return None


def _http_post_json(url: str, headers: dict[str, str], payload: dict[str, Any]) -> dict[str, Any]:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urlopen(request, timeout=_HTTP_TIMEOUT_SECONDS) as response:
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace").strip()
        message = detail or getattr(exc, "reason", "") or f"HTTP {exc.code}"
        raise RuntimeError(f"embedding request failed: {message}") from exc
    except URLError as exc:
        raise RuntimeError(f"embedding request failed: {exc.reason}") from exc
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("embedding response was not valid JSON") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("embedding response was not a JSON object")
    return parsed


def _coerce_embedding(value: Any) -> list[float] | None:
    if not isinstance(value, list) or not value:
        return None
    embedding: list[float] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            return None
        embedding.append(float(item))
    return embedding


def _extract_indexed_embeddings(payload: dict[str, Any], expected_count: int) -> list[list[float] | None]:
    if expected_count <= 0:
        return []
    data = payload.get("data")
    if not isinstance(data, list):
        return [None] * expected_count
    embeddings: list[list[float] | None] = [None] * expected_count
    for position, item in enumerate(data):
        if not isinstance(item, dict):
            continue
        index = item.get("index")
        slot = (
            index
            if isinstance(index, int) and 0 <= index < expected_count
            else position if position < expected_count else None
        )
        if slot is None:
            continue
        embeddings[slot] = _coerce_embedding(item.get("embedding"))
    return embeddings


def embed_batch_texts(
    texts: list[str],
    provider: EmbeddingProvider,
    config: UMXConfig,
) -> list[list[float] | None]:
    if not texts:
        return []
    embed_batch = getattr(provider, "embed_batch", None)
    if callable(embed_batch):
        try:
            payload = embed_batch(texts, config)
        except Exception:
            payload = None
        if isinstance(payload, list) and len(payload) == len(texts):
            return [_coerce_embedding(item) for item in payload]
    return [provider.embed_text(text, config) for text in texts]


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


class OpenAIEmbeddingProvider:
    name = "openai"
    _DEFAULT_BASE_URL = "https://api.openai.com/v1/embeddings"

    def is_available(self, config: UMXConfig) -> bool:
        return _first_env(*_OPENAI_API_KEY_ENVS) is not None

    def _embed(self, inputs: str | list[str], config: UMXConfig) -> list[list[float] | None]:
        api_key = _first_env(*_OPENAI_API_KEY_ENVS)
        if api_key is None:
            return []
        response = _http_post_json(
            config.search.embedding.api_base or self._DEFAULT_BASE_URL,
            {
                "content-type": "application/json",
                "authorization": f"Bearer {api_key}",
            },
            {
                "model": config.search.embedding.model,
                "input": inputs,
                "encoding_format": "float",
            },
        )
        expected_count = len(inputs) if isinstance(inputs, list) else 1
        return _extract_indexed_embeddings(response, expected_count)

    def embed_text(self, text: str, config: UMXConfig) -> list[float] | None:
        try:
            payload = self._embed(text, config)
        except Exception:
            return None
        return payload[0] if payload else None

    def embed_batch(self, texts: list[str], config: UMXConfig) -> list[list[float] | None]:
        try:
            return self._embed(texts, config)
        except Exception:
            return [None] * len(texts)


class VoyageEmbeddingProvider:
    name = "voyage"
    _DEFAULT_BASE_URL = "https://api.voyageai.com/v1/embeddings"

    def is_available(self, config: UMXConfig) -> bool:
        return _first_env(*_VOYAGE_API_KEY_ENVS) is not None

    def _embed(self, inputs: str | list[str], config: UMXConfig) -> list[list[float] | None]:
        api_key = _first_env(*_VOYAGE_API_KEY_ENVS)
        if api_key is None:
            return []
        response = _http_post_json(
            config.search.embedding.api_base or self._DEFAULT_BASE_URL,
            {
                "content-type": "application/json",
                "authorization": f"Bearer {api_key}",
            },
            {
                "model": config.search.embedding.model,
                "input": inputs,
            },
        )
        expected_count = len(inputs) if isinstance(inputs, list) else 1
        return _extract_indexed_embeddings(response, expected_count)

    def embed_text(self, text: str, config: UMXConfig) -> list[float] | None:
        try:
            payload = self._embed(text, config)
        except Exception:
            return None
        return payload[0] if payload else None

    def embed_batch(self, texts: list[str], config: UMXConfig) -> list[list[float] | None]:
        try:
            return self._embed(texts, config)
        except Exception:
            return [None] * len(texts)


_EMBEDDING_PROVIDERS: dict[str, EmbeddingProvider] = {
    SentenceTransformersEmbeddingProvider.name: SentenceTransformersEmbeddingProvider(),
    FixtureEmbeddingProvider.name: FixtureEmbeddingProvider(),
    OpenAIEmbeddingProvider.name: OpenAIEmbeddingProvider(),
    VoyageEmbeddingProvider.name: VoyageEmbeddingProvider(),
}


def resolve_embedding_provider(config: UMXConfig | None = None) -> EmbeddingProvider:
    cfg = config or default_config()
    name = (cfg.search.embedding.provider or DEFAULT_EMBEDDING_PROVIDER).strip() or DEFAULT_EMBEDDING_PROVIDER
    provider = _EMBEDDING_PROVIDERS.get(name)
    if provider is None:
        raise RuntimeError(f"unknown embedding provider: {name}")
    return provider
