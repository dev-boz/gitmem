from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from umx.config import UMXConfig
from umx.providers import claude_cli as claude_cli_provider
from umx.providers import codex_cli as codex_cli_provider

CLI_MAX_ATTEMPTS = 3


@dataclass(slots=True, frozen=True)
class BenchmarkMessageResult:
    text: str
    model: str
    usage: dict[str, int]


def normalize_benchmark_provider(provider: str | None) -> str:
    if provider is None:
        return "claude-cli"
    name = provider.strip().lower()
    if name in {"claude-cli", "claude-code", "cli", "oauth"}:
        return "claude-cli"
    if name in {"codex", "codex-cli"}:
        return "codex-cli"
    raise RuntimeError(
        f"unknown benchmark provider: {provider!r} "
        "(expected one of `claude-cli` or `codex-cli`)"
    )


def send_benchmark_message_with_provider(
    provider: str,
    *,
    config: UMXConfig,
    model: str,
    system: str,
    prompt: str,
) -> BenchmarkMessageResult:
    if provider == "claude-cli":
        response = claude_cli_provider.send_claude_cli_message(
            model=model,
            system=system,
            prompt=prompt,
        )
    elif provider == "codex-cli":
        response = codex_cli_provider.send_codex_cli_message(
            model=model,
            system=system,
            prompt=prompt,
        )
    else:
        raise RuntimeError(f"unsupported benchmark provider `{provider}`")
    return BenchmarkMessageResult(text=response.text, model=response.model, usage=response.usage)


def send_benchmark_message_with_retry(
    provider: str,
    *,
    config: UMXConfig,
    model: str,
    system: str,
    prompt: str,
) -> BenchmarkMessageResult:
    last_error: RuntimeError | None = None
    for attempt in range(1, CLI_MAX_ATTEMPTS + 1):
        try:
            return send_benchmark_message_with_provider(
                provider,
                config=config,
                model=model,
                system=system,
                prompt=prompt,
            )
        except RuntimeError as exc:
            last_error = exc
            if not _is_retryable_cli_error(provider, exc) or attempt >= CLI_MAX_ATTEMPTS:
                raise
            time.sleep(attempt * 2)
    assert last_error is not None
    raise last_error


def resolve_benchmark_model(
    provider: str,
    *,
    explicit_model: str | None,
    config: UMXConfig,
) -> str:
    if explicit_model:
        return explicit_model
    configured = config.dream.l2_model
    if provider == "claude-cli":
        return configured or "claude-opus-4-7"
    if provider == "codex-cli":
        if isinstance(configured, str) and _looks_like_openai_model(configured):
            return configured
        return "gpt-5.2"
    raise RuntimeError(f"unsupported benchmark provider `{provider}`")


def empty_usage() -> dict[str, int]:
    return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}


def add_usage(target: dict[str, int], usage: dict[str, int]) -> None:
    for key in ("input_tokens", "output_tokens", "total_tokens"):
        value = usage.get(key)
        if isinstance(value, int):
            target[key] += value


def render_history_sessions(sessions: Sequence[Any], *, history_format: str) -> str:
    history_chunks: list[str] = []
    for index, session in enumerate(sorted(sessions, key=lambda item: item.started or ""), start=1):
        turns: list[dict[str, Any]] = []
        for turn in session.turns:
            normalized_turn = {key: value for key, value in dict(turn).items() if key != "has_answer"}
            turns.append(normalized_turn)
        if history_format == "json":
            rendered_turns = "\n" + json.dumps(turns)
        else:
            rendered_lines = [
                f"{turn.get('role', 'unknown')}: {str(turn.get('content', '')).strip()}"
                for turn in turns
                if str(turn.get("content", "")).strip()
            ]
            rendered_turns = "\n".join(rendered_lines)
        history_chunks.append(
            f"\n### Session {index}:\n"
            f"Session ID: {session.session_id}\n"
            f"Session Date: {session.started or 'unknown'}\n"
            f"Session Content:\n{rendered_turns}\n"
        )
    return "".join(history_chunks).strip()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _is_retryable_cli_error(provider: str, exc: RuntimeError) -> bool:
    message = str(exc)
    if provider == "claude-cli":
        return message.startswith("Claude CLI ")
    if provider == "codex-cli":
        return message.startswith("Codex CLI ")
    return False


def _looks_like_openai_model(model: str) -> bool:
    normalized = model.strip().lower()
    return normalized.startswith(("gpt-", "o1", "o3", "o4"))
