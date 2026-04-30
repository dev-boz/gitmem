"""Subprocess provider that drives OpenCode in headless JSON mode."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from tempfile import TemporaryDirectory
from typing import Any

OPENCODE_CLI_BINARY_ENV = "UMX_OPENCODE_CLI_BIN"
OPENCODE_CLI_TIMEOUT_ENV = "UMX_OPENCODE_CLI_TIMEOUT"
DEFAULT_TIMEOUT_SECONDS = 300


@dataclass(slots=True, frozen=True)
class OpenCodeCLIMessageResult:
    text: str
    model: str
    usage: dict[str, int]


def opencode_cli_available(binary: str | None = None) -> bool:
    return _resolve_binary(binary) is not None


def send_opencode_cli_message(
    *,
    model: str,
    system: str,
    prompt: str,
    binary: str | None = None,
    timeout: int | None = None,
    extra_args: list[str] | None = None,
    runner: "subprocess._CompletedProcess[str] | None" = None,
) -> OpenCodeCLIMessageResult:
    resolved = _resolve_binary(binary)
    if resolved is None:
        raise RuntimeError(
            "OpenCode CLI not found; install `opencode` and authenticate, "
            "or set UMX_OPENCODE_CLI_BIN to the binary path"
        )

    timeout_seconds = _resolve_timeout(timeout)
    with TemporaryDirectory(prefix="gitmem-opencode-cli-") as temp_dir:
        cmd = [
            resolved,
            "run",
            "--model",
            model,
            "--format",
            "json",
            "--dir",
            temp_dir,
            "--pure",
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
                raise RuntimeError(f"failed to execute OpenCode CLI: {exc}") from exc
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError(f"OpenCode CLI timed out after {timeout_seconds}s") from exc
        else:
            completed = runner

    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        if not detail:
            detail = f"exit code {completed.returncode}"
        raise RuntimeError(f"OpenCode CLI failed: {detail}")

    text = _extract_text(completed.stdout or "")
    if not text:
        raise RuntimeError("OpenCode CLI returned empty output")

    usage = _extract_usage(completed.stdout or "")
    return OpenCodeCLIMessageResult(text=text, model=model, usage=usage)


def _resolve_binary(binary: str | None) -> str | None:
    candidate = binary or os.getenv(OPENCODE_CLI_BINARY_ENV) or "opencode"
    if os.path.sep in candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
        return candidate
    return shutil.which(candidate)


def _resolve_timeout(timeout: int | None) -> int:
    if isinstance(timeout, int) and timeout > 0:
        return timeout
    raw = os.getenv(OPENCODE_CLI_TIMEOUT_ENV)
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


def _extract_text(raw: str) -> str:
    text_parts: list[str] = []
    for record in _parse_jsonl(raw):
        if record.get("type") != "text":
            continue
        part = record.get("part")
        if not isinstance(part, dict):
            continue
        text = part.get("text")
        if isinstance(text, str) and text:
            text_parts.append(text)
    return "".join(text_parts).strip()


def _extract_usage(raw: str) -> dict[str, int]:
    for record in reversed(_parse_jsonl(raw)):
        if record.get("type") != "step_finish":
            continue
        part = record.get("part")
        if not isinstance(part, dict):
            continue
        tokens = part.get("tokens")
        if not isinstance(tokens, dict):
            continue
        total_tokens = int(tokens.get("total", 0) or 0)
        output_tokens = int(tokens.get("output", 0) or 0)
        reasoning_output_tokens = int(tokens.get("reasoning", 0) or 0)
        output_tokens += reasoning_output_tokens
        input_tokens = int(tokens.get("input", 0) or 0)
        if total_tokens > 0:
            input_tokens = max(total_tokens - output_tokens, input_tokens)
        else:
            total_tokens = input_tokens + output_tokens
        usage = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
        }
        if reasoning_output_tokens:
            usage["reasoning_output_tokens"] = reasoning_output_tokens
        cache_payload = tokens.get("cache")
        if isinstance(cache_payload, dict):
            cached_input_tokens = int(cache_payload.get("read", 0) or 0)
            if cached_input_tokens:
                usage["cached_input_tokens"] = cached_input_tokens
        return usage
    return {}


def _parse_jsonl(raw: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records
