"""Subprocess provider that drives Codex CLI in headless `exec` mode."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

CODEX_CLI_BINARY_ENV = "UMX_CODEX_CLI_BIN"
CODEX_CLI_TIMEOUT_ENV = "UMX_CODEX_CLI_TIMEOUT"
DEFAULT_TIMEOUT_SECONDS = 300


@dataclass(slots=True, frozen=True)
class CodexCLIMessageResult:
    text: str
    model: str
    usage: dict[str, int]


def codex_cli_available(binary: str | None = None) -> bool:
    """Return True when a usable `codex` binary is on PATH."""

    return _resolve_binary(binary) is not None


def send_codex_cli_message(
    *,
    model: str,
    system: str,
    prompt: str,
    binary: str | None = None,
    timeout: int | None = None,
    extra_args: list[str] | None = None,
    runner: "subprocess._CompletedProcess | None" = None,
) -> CodexCLIMessageResult:
    """Invoke Codex CLI in non-interactive `exec` mode and parse the result."""

    resolved = _resolve_binary(binary)
    if resolved is None:
        raise RuntimeError(
            "Codex CLI not found; install `codex` and authenticate, "
            "or set UMX_CODEX_CLI_BIN to the binary path"
        )

    timeout_seconds = _resolve_timeout(timeout)
    with TemporaryDirectory(prefix="gitmem-codex-cli-") as temp_dir:
        output_path = Path(temp_dir) / "last-message.txt"
        cmd = [
            resolved,
            "exec",
            "--model",
            model,
            "--sandbox",
            "read-only",
            "--skip-git-repo-check",
            "--ephemeral",
            "--ignore-rules",
            "--ignore-user-config",
            "--output-last-message",
            str(output_path),
            "--json",
            "-",
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
                )
            except FileNotFoundError as exc:
                raise RuntimeError(f"failed to execute Codex CLI: {exc}") from exc
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError(f"Codex CLI timed out after {timeout_seconds}s") from exc
        else:
            completed = runner

        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            if not detail:
                detail = f"exit code {completed.returncode}"
            raise RuntimeError(f"Codex CLI failed: {detail}")

        text = _read_last_message(output_path)
        if not text:
            text = _extract_last_message(completed.stdout or "")
        if not text:
            raise RuntimeError("Codex CLI returned empty output")

        usage = _extract_usage(completed.stdout or "")
        return CodexCLIMessageResult(text=text, model=model, usage=usage)


def _resolve_binary(binary: str | None) -> str | None:
    candidate = binary or os.getenv(CODEX_CLI_BINARY_ENV) or "codex"
    if os.path.sep in candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
        return candidate
    return shutil.which(candidate)


def _resolve_timeout(timeout: int | None) -> int:
    if isinstance(timeout, int) and timeout > 0:
        return timeout
    raw = os.getenv(CODEX_CLI_TIMEOUT_ENV)
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


def _read_last_message(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _extract_last_message(raw: str) -> str:
    lines = _parse_jsonl(raw)
    for record in reversed(lines):
        if record.get("type") != "item.completed":
            continue
        item = record.get("item")
        if not isinstance(item, dict) or item.get("type") != "agent_message":
            continue
        text = item.get("text")
        if isinstance(text, str) and text.strip():
            return text.strip()
    return ""


def _extract_usage(raw: str) -> dict[str, int]:
    usage: dict[str, int] = {}
    for record in _parse_jsonl(raw):
        if record.get("type") != "turn.completed":
            continue
        payload = record.get("usage")
        if not isinstance(payload, dict):
            continue
        input_tokens = int(payload.get("input_tokens", 0) or 0)
        output_tokens = int(payload.get("output_tokens", 0) or 0)
        reasoning_output_tokens = int(payload.get("reasoning_output_tokens", 0) or 0)
        cached_input_tokens = int(payload.get("cached_input_tokens", 0) or 0)
        usage["input_tokens"] = input_tokens
        usage["output_tokens"] = output_tokens + reasoning_output_tokens
        usage["total_tokens"] = usage["input_tokens"] + usage["output_tokens"]
        if cached_input_tokens:
            usage["cached_input_tokens"] = cached_input_tokens
        if reasoning_output_tokens:
            usage["reasoning_output_tokens"] = reasoning_output_tokens
    return usage


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
