from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from umx.claude_code_capture import capture_claude_code_session, parse_claude_code_session
from umx.config import load_config
from umx.git_ops import git_add_and_commit
from umx.hooks.pre_compact import run as pre_compact_hook
from umx.hooks.pre_tool_use import run as pre_tool_use_hook
from umx.hooks.session_end import run as session_end_hook
from umx.hooks.session_start import run as session_start_hook
from umx.scope import config_path, find_project_root, project_memory_dir


def read_hook_payload(payload_file: Path | None = None) -> dict[str, Any]:
    if payload_file is not None:
        raw = payload_file.read_text(encoding="utf-8")
    else:
        raw = sys.stdin.read()
    text = raw.strip()
    if not text:
        return {}
    return json.loads(text)


def claude_code_hook_config(command_prefix: str = "umx") -> dict[str, Any]:
    def _command(name: str) -> dict[str, str]:
        return {
            "type": "command",
            "command": f"{command_prefix} hooks claude-code {name}",
        }

    return {
        "hooks": {
            "SessionStart": [{"hooks": [_command("session-start")]}],
            "PreToolUse": [{"matcher": "*", "hooks": [_command("pre-tool-use")]}],
            "PreCompact": [{"hooks": [_command("pre-compact")]}],
            "SessionEnd": [{"hooks": [_command("session-end")]}],
        }
    }


def claude_code_settings_path(cwd: Path, scope: str) -> Path:
    if scope == "user":
        return Path.home() / ".claude" / "settings.json"
    root = find_project_root(cwd)
    if scope == "project":
        return root / ".claude" / "settings.json"
    if scope == "local":
        return root / ".claude" / "settings.local.json"
    raise ValueError(f"unsupported Claude hook scope: {scope}")


def install_claude_code_hooks(
    cwd: Path,
    *,
    scope: str = "local",
    command_prefix: str = "umx",
) -> Path:
    settings_path = claude_code_settings_path(cwd, scope)
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    if settings_path.exists():
        data = json.loads(settings_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Claude Code settings must be a JSON object")
    else:
        data = {}

    hook_block = claude_code_hook_config(command_prefix)
    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError("Claude Code settings 'hooks' field must be an object")
    for event_name, groups in hook_block["hooks"].items():
        existing = hooks.setdefault(event_name, [])
        if not isinstance(existing, list):
            raise ValueError(f"Claude Code settings hook entry for {event_name} must be a list")
        for group in groups:
            if group not in existing:
                existing.append(group)

    settings_path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return settings_path


def _normalize_file_path(cwd: Path, value: str) -> str:
    path = Path(value)
    try:
        return path.relative_to(cwd).as_posix()
    except ValueError:
        return path.as_posix()


def _tool_command_text(tool_name: str | None, tool_input: dict[str, Any]) -> str | None:
    if tool_name == "Bash":
        command = tool_input.get("command")
        return str(command) if isinstance(command, str) else None
    if tool_name == "WebSearch":
        query = tool_input.get("query")
        return str(query) if isinstance(query, str) else None
    if tool_name == "WebFetch":
        url = tool_input.get("url")
        prompt = tool_input.get("prompt")
        if isinstance(url, str) and isinstance(prompt, str):
            return f"{url} {prompt}".strip()
        if isinstance(url, str):
            return url
        if isinstance(prompt, str):
            return prompt
        return None
    if tool_name == "Agent":
        prompt = tool_input.get("prompt")
        description = tool_input.get("description")
        if isinstance(prompt, str) and prompt:
            return prompt
        if isinstance(description, str) and description:
            return description
        return None
    if tool_name == "AskUserQuestion":
        questions = tool_input.get("questions")
        if isinstance(questions, list):
            texts = [
                str(question.get("question", "")).strip()
                for question in questions
                if isinstance(question, dict)
            ]
            joined = " ".join(text for text in texts if text)
            return joined or None
        return None
    flattened = [
        str(value).strip()
        for value in tool_input.values()
        if isinstance(value, (str, int, float)) and str(value).strip()
    ]
    return " ".join(flattened) or None


def _tool_file_paths(cwd: Path, tool_input: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    for field in ("file_path", "path"):
        value = tool_input.get(field)
        if isinstance(value, str) and value.strip():
            paths.append(_normalize_file_path(cwd, value))
    return paths


def _fallback_umx_session_id(session_id: str) -> str:
    date = datetime.now(tz=UTC).date().isoformat()
    return f"{date}-claude-code-{session_id[:8]}"


def umx_session_id_from_payload(payload: dict[str, Any]) -> str:
    transcript_path = payload.get("transcript_path")
    if isinstance(transcript_path, str) and transcript_path:
        path = Path(transcript_path)
        if path.exists():
            transcript = parse_claude_code_session(path)
            if transcript.started:
                return transcript.umx_session_id
            return _fallback_umx_session_id(transcript.session_id)
        return _fallback_umx_session_id(path.stem)
    session_id = str(payload.get("session_id", "claude-code"))
    return _fallback_umx_session_id(session_id)


def session_start_response(payload: dict[str, Any]) -> dict[str, Any] | None:
    cwd = Path(str(payload["cwd"]))
    block = session_start_hook(
        cwd=cwd,
        tool="claude-code",
        session_id=umx_session_id_from_payload(payload),
    )
    if not block:
        return None
    return {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": block,
        }
    }


def pre_tool_use_response(payload: dict[str, Any]) -> dict[str, Any] | None:
    cwd = Path(str(payload["cwd"]))
    tool_name = payload.get("tool_name")
    tool_input = payload.get("tool_input", {})
    if not isinstance(tool_input, dict):
        tool_input = {}
    block = pre_tool_use_hook(
        cwd=cwd,
        tool_name=str(tool_name) if tool_name else None,
        command_text=_tool_command_text(str(tool_name) if tool_name else None, tool_input),
        file_paths=_tool_file_paths(cwd, tool_input),
        session_id=umx_session_id_from_payload(payload),
    )
    if not block:
        return None
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": block,
        }
    }


def pre_compact_response(payload: dict[str, Any]) -> None:
    pre_compact_hook(cwd=Path(str(payload["cwd"])))
    return None


def session_end_response(payload: dict[str, Any]) -> None:
    cwd = Path(str(payload["cwd"]))
    transcript_path = payload.get("transcript_path")
    if not isinstance(transcript_path, str) or not transcript_path:
        return None
    cfg = load_config(config_path())
    result = capture_claude_code_session(cwd, Path(transcript_path), config=cfg)
    repo = project_memory_dir(find_project_root(cwd))
    git_add_and_commit(
        repo,
        paths=[Path(result["session_file"])],
        message=f"umx: capture claude-code session {result['umx_session_id']}",
    )
    session_end_hook(
        cwd=cwd,
        session_id=result["umx_session_id"],
        tool="claude-code",
    )
    return None
