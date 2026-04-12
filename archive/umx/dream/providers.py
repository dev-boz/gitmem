"""LLM provider rotation and fallback chain.

Provider-agnostic: one client, swap base URL.
Default rotation: Cerebras → Groq → GLM → MiniMax → OpenRouter
Local fallback: Ollama or any OpenAI-compatible endpoint.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import httpx

from umx.models import UmxConfig


@dataclass
class ProviderConfig:
    """Configuration for a single LLM provider."""

    name: str
    base_url: str
    api_key_env: str
    model: str
    headers: dict[str, str] | None = None


# Known free-tier providers
PROVIDERS: dict[str, ProviderConfig] = {
    "cerebras": ProviderConfig(
        name="cerebras",
        base_url="https://api.cerebras.ai/v1",
        api_key_env="CEREBRAS_API_KEY",
        model="llama-4-scout-17b-16e-instruct",
    ),
    "groq": ProviderConfig(
        name="groq",
        base_url="https://api.groq.com/openai/v1",
        api_key_env="GROQ_API_KEY",
        model="llama-3.3-70b-versatile",
    ),
    "glm": ProviderConfig(
        name="glm",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        api_key_env="GLM_API_KEY",
        model="glm-4-flash",
    ),
    "minimax": ProviderConfig(
        name="minimax",
        base_url="https://api.minimax.chat/v1",
        api_key_env="MINIMAX_API_KEY",
        model="MiniMax-Text-01",
    ),
    "openrouter": ProviderConfig(
        name="openrouter",
        base_url="https://openrouter.ai/api/v1",
        api_key_env="OPENROUTER_API_KEY",
        model="meta-llama/llama-3.3-70b-instruct:free",
    ),
}


class LLMClient:
    """OpenAI-compatible client with provider rotation and fallback."""

    def __init__(self, config: UmxConfig | None = None) -> None:
        self.config = config or UmxConfig()
        self._client = httpx.Client(timeout=60.0)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> LLMClient:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def chat_completion(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.3,
        max_tokens: int = 2000,
    ) -> str | None:
        """Send a chat completion request, rotating through providers.

        Returns the response text, or None if all providers fail.
        """
        # Try configured providers in order
        for provider_name in self.config.llm_providers:
            provider = PROVIDERS.get(provider_name)
            if not provider:
                continue

            api_key = os.environ.get(provider.api_key_env, "")
            if not api_key:
                continue

            result = self._try_provider(
                provider, api_key, messages, temperature, max_tokens
            )
            if result is not None:
                return result

        # Try local fallback
        if self.config.local_llm_endpoint:
            result = self._try_local(messages, temperature, max_tokens)
            if result is not None:
                return result

        return None

    def _try_provider(
        self,
        provider: ProviderConfig,
        api_key: str,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: int,
    ) -> str | None:
        """Attempt a completion with a single provider."""
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        if provider.headers:
            headers.update(provider.headers)

        payload = {
            "model": provider.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        try:
            resp = self._client.post(
                f"{provider.base_url}/chat/completions",
                headers=headers,
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]
        except (httpx.HTTPError, KeyError, IndexError):
            return None

    def _try_local(
        self,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: int,
    ) -> str | None:
        """Attempt a completion with local LLM endpoint."""
        payload = {
            "model": self.config.local_llm_model or "llama3",
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        try:
            resp = self._client.post(
                f"{self.config.local_llm_endpoint}/chat/completions",
                headers={"Content-Type": "application/json"},
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]
        except (httpx.HTTPError, KeyError, IndexError):
            return None

    def is_available(self) -> bool:
        """Check if any provider is available."""
        for name in self.config.llm_providers:
            provider = PROVIDERS.get(name)
            if provider and os.environ.get(provider.api_key_env):
                return True
        if self.config.local_llm_endpoint:
            return True
        return False
