from __future__ import annotations

import json
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_API_VERSION = "2023-06-01"


@dataclass(slots=True, frozen=True)
class AnthropicMessageResult:
    text: str
    model: str
    usage: dict[str, int]


def send_anthropic_message(
    *,
    api_key: str,
    model: str,
    system: str,
    prompt: str,
    max_tokens: int = 900,
    temperature: float = 0.0,
) -> AnthropicMessageResult:
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "system": system,
        "messages": [
            {
                "role": "user",
                "content": prompt,
            }
        ],
    }
    request = Request(
        ANTHROPIC_API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "content-type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": ANTHROPIC_API_VERSION,
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace").strip()
        message = detail or getattr(exc, "reason", "") or f"HTTP {exc.code}"
        raise RuntimeError(f"Anthropic request failed: {message}") from exc
    except URLError as exc:
        raise RuntimeError(f"Anthropic request failed: {exc.reason}") from exc

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Anthropic response was not valid JSON") from exc

    content = parsed.get("content")
    if not isinstance(content, list):
        raise RuntimeError("Anthropic response did not include content blocks")
    parts = [
        block.get("text", "").strip()
        for block in content
        if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str)
    ]
    text = "\n".join(part for part in parts if part).strip()
    if not text:
        raise RuntimeError("Anthropic response did not include text content")

    usage_payload = parsed.get("usage")
    usage: dict[str, int] = {}
    if isinstance(usage_payload, dict):
        for key in ("input_tokens", "output_tokens"):
            value = usage_payload.get(key)
            if value is not None:
                usage[key] = int(value)
    if "input_tokens" in usage and "output_tokens" in usage:
        usage["total_tokens"] = usage["input_tokens"] + usage["output_tokens"]

    response_model = parsed.get("model")
    return AnthropicMessageResult(
        text=text,
        model=str(response_model) if isinstance(response_model, str) and response_model.strip() else model,
        usage=usage,
    )
