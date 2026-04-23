"""Subprocess provider that drives the Claude Code CLI in headless `-p` mode.

Lets gitmem reach Claude over the user's existing Claude Code OAuth session
without forcing an `ANTHROPIC_API_KEY`. The CLI is invoked with
`--print --output-format json` so the response can be parsed deterministically.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass


CLAUDE_CLI_BINARY_ENV = "UMX_CLAUDE_CLI_BIN"
CLAUDE_CLI_TIMEOUT_ENV = "UMX_CLAUDE_CLI_TIMEOUT"
DEFAULT_TIMEOUT_SECONDS = 180

# Tools we never want the headless review session to invoke. The reviewer
# should only ever return JSON; if the model attempts a tool call instead of
# answering, fail fast rather than letting it touch the host filesystem.
_DISALLOWED_TOOLS = (
    "Bash Edit Write Read Glob Grep NotebookEdit WebFetch WebSearch "
    "Task TaskCreate TaskList TaskUpdate"
)


@dataclass(slots=True, frozen=True)
class ClaudeCLIMessageResult:
    text: str
    model: str
    usage: dict[str, int]


def claude_cli_available(binary: str | None = None) -> bool:
    """Return True when a usable `claude` binary is on PATH."""

    return _resolve_binary(binary) is not None


def send_claude_cli_message(
    *,
    model: str,
    system: str,
    prompt: str,
    binary: str | None = None,
    timeout: int | None = None,
    extra_args: list[str] | None = None,
    runner: "subprocess._CompletedProcess | None" = None,
) -> ClaudeCLIMessageResult:
    """Invoke the Claude CLI in headless `-p` mode and parse the JSON result.

    Args:
        model: Anthropic model alias or id (e.g. ``claude-opus-4-7``).
        system: System prompt appended via ``--append-system-prompt``.
        prompt: User prompt. Sent on stdin to avoid argv length limits.
        binary: Optional explicit path to a ``claude`` binary; falls back to
            the ``UMX_CLAUDE_CLI_BIN`` env var, then ``shutil.which("claude")``.
        timeout: Per-call timeout in seconds. Defaults to
            ``UMX_CLAUDE_CLI_TIMEOUT`` if set, else 180s.
        extra_args: Extra command line arguments appended after the standard
            flags. Useful for tests and for passing ``--max-budget-usd``.
        runner: Optional pre-built ``subprocess.CompletedProcess`` (for tests).
    """

    resolved = _resolve_binary(binary)
    if resolved is None:
        raise RuntimeError(
            "Claude Code CLI not found; install `claude` and authenticate, "
            "or set UMX_CLAUDE_CLI_BIN to the binary path"
        )

    timeout_seconds = _resolve_timeout(timeout)
    cmd = [
        resolved,
        "--print",
        "--output-format",
        "json",
        "--model",
        model,
        "--append-system-prompt",
        system,
        "--no-session-persistence",
        "--disallowedTools",
        _DISALLOWED_TOOLS,
        "--exclude-dynamic-system-prompt-sections",
        "--disable-slash-commands",
    ]
    if extra_args:
        cmd.extend(extra_args)

    if runner is None:
        try:
            completed = subprocess.run(  # noqa: S603 - explicit binary path
                cmd,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(f"failed to execute Claude CLI: {exc}") from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"Claude CLI timed out after {timeout_seconds}s"
            ) from exc
    else:
        completed = runner

    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip() or f"exit code {completed.returncode}"
        raise RuntimeError(f"Claude CLI failed: {stderr}")

    raw = (completed.stdout or "").strip()
    if not raw:
        raise RuntimeError("Claude CLI returned empty output")

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Claude CLI did not return valid JSON output") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("Claude CLI JSON output was not an object")

    if parsed.get("is_error"):
        message = str(parsed.get("result") or parsed.get("subtype") or "unknown error").strip()
        raise RuntimeError(f"Claude CLI reported an error: {message}")

    text_payload = parsed.get("result")
    if not isinstance(text_payload, str) or not text_payload.strip():
        raise RuntimeError("Claude CLI response missing `result` text")

    response_model = parsed.get("model")
    usage_payload = parsed.get("usage") if isinstance(parsed.get("usage"), dict) else {}
    usage: dict[str, int] = {}
    input_tokens = usage_payload.get("input_tokens")
    output_tokens = usage_payload.get("output_tokens")
    if isinstance(input_tokens, int):
        usage["input_tokens"] = input_tokens
    if isinstance(output_tokens, int):
        usage["output_tokens"] = output_tokens
    if "input_tokens" in usage and "output_tokens" in usage:
        usage["total_tokens"] = usage["input_tokens"] + usage["output_tokens"]

    return ClaudeCLIMessageResult(
        text=text_payload.strip(),
        model=str(response_model)
        if isinstance(response_model, str) and response_model.strip()
        else model,
        usage=usage,
    )


def _resolve_binary(binary: str | None) -> str | None:
    candidate = binary or os.getenv(CLAUDE_CLI_BINARY_ENV) or "claude"
    if os.path.sep in candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
        return candidate
    return shutil.which(candidate)


def _resolve_timeout(timeout: int | None) -> int:
    if isinstance(timeout, int) and timeout > 0:
        return timeout
    raw = os.getenv(CLAUDE_CLI_TIMEOUT_ENV)
    if raw:
        try:
            value = int(raw)
        except ValueError:
            value = 0
        if value > 0:
            return value
    return DEFAULT_TIMEOUT_SECONDS
