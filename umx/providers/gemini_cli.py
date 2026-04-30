"""Subprocess provider that drives Gemini CLI in headless JSON mode."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from tempfile import TemporaryDirectory
from typing import Any

GEMINI_CLI_BINARY_ENV = "UMX_GEMINI_CLI_BIN"
GEMINI_CLI_TIMEOUT_ENV = "UMX_GEMINI_CLI_TIMEOUT"
DEFAULT_TIMEOUT_SECONDS = 300


@dataclass(slots=True, frozen=True)
class GeminiCLIMessageResult:
    text: str
    model: str
    usage: dict[str, int]


def gemini_cli_available(binary: str | None = None) -> bool:
    return _resolve_binary(binary) is not None


def send_gemini_cli_message(
    *,
    model: str,
    system: str,
    prompt: str,
    binary: str | None = None,
    timeout: int | None = None,
    extra_args: list[str] | None = None,
    runner: "subprocess._CompletedProcess[str] | None" = None,
) -> GeminiCLIMessageResult:
    resolved = _resolve_binary(binary)
    if resolved is None:
        raise RuntimeError(
            "Gemini CLI not found; install `gemini` and authenticate, "
            "or set UMX_GEMINI_CLI_BIN to the binary path"
        )

    timeout_seconds = _resolve_timeout(timeout)
    with TemporaryDirectory(prefix="gitmem-gemini-cli-") as temp_dir:
        cmd = [
            resolved,
            "--model",
            model,
            "--approval-mode",
            "plan",
            "--output-format",
            "json",
        ]
        if extra_args:
            cmd.extend(extra_args)

        if runner is None:
            try:
                completed = subprocess.run(  # noqa: S603 - explicit binary path
                    cmd,
                    input=_render_prompt(system=system, prompt=prompt),
                    capture_output=True,
                    text=True,
                    timeout=timeout_seconds,
                    check=False,
                    cwd=temp_dir,
                )
            except FileNotFoundError as exc:
                raise RuntimeError(f"failed to execute Gemini CLI: {exc}") from exc
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError(f"Gemini CLI timed out after {timeout_seconds}s") from exc
        else:
            completed = runner

    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        if not detail:
            detail = f"exit code {completed.returncode}"
        raise RuntimeError(f"Gemini CLI failed: {detail}")

    payload = _parse_output(completed.stdout or "")
    text = payload.get("response")
    if not isinstance(text, str) or not text.strip():
        raise RuntimeError("Gemini CLI returned empty output")

    actual_model, usage = _extract_usage(payload, default_model=model)
    return GeminiCLIMessageResult(text=text.strip(), model=actual_model, usage=usage)


def _resolve_binary(binary: str | None) -> str | None:
    candidate = binary or os.getenv(GEMINI_CLI_BINARY_ENV) or "gemini"
    if os.path.sep in candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
        return candidate
    return shutil.which(candidate)


def _resolve_timeout(timeout: int | None) -> int:
    if isinstance(timeout, int) and timeout > 0:
        return timeout
    raw = os.getenv(GEMINI_CLI_TIMEOUT_ENV)
    if raw:
        try:
            value = int(raw)
        except ValueError:
            value = 0
        if value > 0:
            return value
    return DEFAULT_TIMEOUT_SECONDS


def _render_prompt(*, system: str, prompt: str) -> str:
    return (
        "System instructions:\n"
        f"{system.strip()}\n\n"
        "User prompt:\n"
        f"{prompt.strip()}\n"
    )


def _parse_output(raw: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw.strip())
    except json.JSONDecodeError as exc:
        raise RuntimeError("Gemini CLI returned non-JSON output") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Gemini CLI returned unexpected output shape")
    return payload


def _extract_usage(payload: dict[str, Any], *, default_model: str) -> tuple[str, dict[str, int]]:
    stats = payload.get("stats")
    if not isinstance(stats, dict):
        return default_model, {}
    models = stats.get("models")
    if not isinstance(models, dict) or not models:
        return default_model, {}
    actual_model = next(iter(models.keys()))
    model_payload = models.get(actual_model)
    if not isinstance(model_payload, dict):
        return actual_model, {}
    tokens = model_payload.get("tokens")
    if not isinstance(tokens, dict):
        return actual_model, {}
    input_tokens = int(tokens.get("input", tokens.get("prompt", 0)) or 0)
    total_tokens = int(tokens.get("total", 0) or 0)
    thoughts = int(tokens.get("thoughts", 0) or 0)
    cached = int(tokens.get("cached", 0) or 0)
    if total_tokens >= input_tokens:
        output_tokens = total_tokens - input_tokens
    else:
        candidates = int(tokens.get("candidates", 0) or 0)
        tool_tokens = int(tokens.get("tool", 0) or 0)
        output_tokens = candidates + thoughts + tool_tokens
        total_tokens = input_tokens + output_tokens
    usage = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }
    if cached:
        usage["cached_input_tokens"] = cached
    if thoughts:
        usage["reasoning_output_tokens"] = thoughts
    return actual_model, usage
