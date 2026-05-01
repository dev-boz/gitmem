from __future__ import annotations

import json
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


NVIDIA_API_URL = "https://integrate.api.nvidia.com/v1/chat/completions"


@dataclass(slots=True, frozen=True)
class NvidiaMessageResult:
    text: str
    model: str
    usage: dict[str, int]


def send_nvidia_message(
    *,
    api_key: str,
    model: str,
    system: str,
    prompt: str,
    max_tokens: int = 900,
    temperature: float = 0.0,
) -> NvidiaMessageResult:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    request = Request(
        NVIDIA_API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "authorization": f"Bearer {api_key}",
            "content-type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace").strip()
        message = detail or getattr(exc, "reason", "") or f"HTTP {exc.code}"
        raise RuntimeError(f"NVIDIA request failed: {message}") from exc
    except URLError as exc:
        raise RuntimeError(f"NVIDIA request failed: {exc.reason}") from exc

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("NVIDIA response was not valid JSON") from exc

    error_payload = parsed.get("error")
    if isinstance(error_payload, dict):
        message = error_payload.get("message")
        if isinstance(message, str) and message.strip():
            raise RuntimeError(f"NVIDIA request failed: {message.strip()}")

    choices = parsed.get("choices")
    if not isinstance(choices, list) or not choices:
        raise RuntimeError("NVIDIA response did not include choices")
    first_choice = choices[0]
    if not isinstance(first_choice, dict):
        raise RuntimeError("NVIDIA response did not include a valid choice payload")
    message_payload = first_choice.get("message")
    if not isinstance(message_payload, dict):
        raise RuntimeError("NVIDIA response did not include a valid message payload")
    content = message_payload.get("content")
    text = _message_text(content)
    if not text:
        raise RuntimeError("NVIDIA response did not include text content")

    usage_payload = parsed.get("usage")
    usage: dict[str, int] = {}
    if isinstance(usage_payload, dict):
        prompt_tokens = usage_payload.get("prompt_tokens")
        completion_tokens = usage_payload.get("completion_tokens")
        total_tokens = usage_payload.get("total_tokens")
        if prompt_tokens is not None:
            usage["input_tokens"] = int(prompt_tokens)
        if completion_tokens is not None:
            usage["output_tokens"] = int(completion_tokens)
        if total_tokens is not None:
            usage["total_tokens"] = int(total_tokens)
        elif "input_tokens" in usage and "output_tokens" in usage:
            usage["total_tokens"] = usage["input_tokens"] + usage["output_tokens"]

    response_model = parsed.get("model")
    return NvidiaMessageResult(
        text=text,
        model=str(response_model) if isinstance(response_model, str) and response_model.strip() else model,
        usage=usage,
    )


def _message_text(content: object) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())
        return "\n".join(parts).strip()
    return ""
