"""Capture GitHub Copilot CLI session logs as umx sessions.

Copilot CLI stores session events in ~/.copilot/session-state/<uuid>/events.jsonl.
Each file is a newline-delimited JSON stream with typed events:

  session.start          — session metadata (id, cwd, version, timestamp)
  user.message           — data.content has the user prompt
  assistant.message      — data.content has assistant text
  assistant.turn_start/end — turn boundaries
  tool.execution_start   — data.toolName + data.arguments
  tool.execution_complete — data.output (tool results)
  session.compaction_*   — context compaction markers
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from umx.config import UMXConfig, default_config
from umx.scope import find_project_root, project_memory_dir
from umx.sessions import write_session

_COPILOT_STATE_DIR = ".copilot/session-state"

# User messages that are just system-injected context, not real prompts
_SKIPPED_USER_PREFIXES = (
    "<environment_context>",
    "<turn_aborted>",
    "<current_datetime>",
    "<reminder>",
    "<system_notification>",
)


@dataclass(slots=True)
class CopilotTranscript:
    session_id: str
    started: str | None
    cwd: str | None
    copilot_version: str | None
    model: str | None
    source_path: Path
    events: list[dict[str, str]] = field(default_factory=list)

    @property
    def umx_session_id(self) -> str:
        return f"{self.session_date}-copilot-{self.session_id[:12]}"

    @property
    def session_date(self) -> str:
        if isinstance(self.started, str) and len(self.started) >= 10:
            return self.started[:10]
        return "1970-01-01"


def _copilot_sessions_root() -> Path:
    return Path.home() / _COPILOT_STATE_DIR


def list_copilot_sessions(source_root: Path | None = None) -> list[Path]:
    root = source_root or _copilot_sessions_root()
    if not root.exists():
        return []
    candidates = sorted(root.glob("*/events.jsonl"))
    return sorted(candidates, key=lambda p: p.stat().st_mtime_ns)


def latest_copilot_session_path(source_root: Path | None = None) -> Path | None:
    candidates = list_copilot_sessions(source_root)
    if not candidates:
        return None
    return candidates[-1]


def _iter_events(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    try:
        raw = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return records
    for line in raw:
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


def _extract_user_content(data: dict[str, Any]) -> str:
    """Extract the real user message, stripping system-injected wrappers."""
    content = data.get("content", "")
    if not isinstance(content, str):
        return ""
    text = content.strip()
    if not text:
        return ""
    # Skip pure system injections
    for prefix in _SKIPPED_USER_PREFIXES:
        if text.startswith(prefix):
            return ""
    return text


def _extract_assistant_content(data: dict[str, Any]) -> str:
    content = data.get("content", "")
    if not isinstance(content, str):
        return ""
    return content.strip()


def parse_copilot_session(path: Path) -> CopilotTranscript:
    session_id = path.parent.name
    started: str | None = None
    cwd: str | None = None
    copilot_version: str | None = None
    model: str | None = None
    events: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    for record in _iter_events(path):
        etype = record.get("type", "")
        data = record.get("data", {})
        if not isinstance(data, dict):
            continue
        ts = record.get("timestamp")

        if etype == "session.start":
            session_id = data.get("sessionId", session_id)
            started = data.get("startTime") or ts
            ctx = data.get("context", {})
            if isinstance(ctx, dict):
                cwd = ctx.get("cwd")
            copilot_version = data.get("copilotVersion")

        elif etype == "session.model_change":
            model = data.get("newModel", model)

        elif etype == "user.message":
            text = _extract_user_content(data)
            if not text:
                continue
            key = ("user", text[:200])
            if key in seen:
                continue
            seen.add(key)
            event: dict[str, str] = {"role": "user", "content": text}
            if isinstance(ts, str):
                event["ts"] = ts
            events.append(event)

        elif etype == "assistant.message":
            text = _extract_assistant_content(data)
            if not text:
                continue
            key = ("assistant", text[:200])
            if key in seen:
                continue
            seen.add(key)
            event = {"role": "assistant", "content": text}
            if isinstance(ts, str):
                event["ts"] = ts
            events.append(event)

    return CopilotTranscript(
        session_id=session_id,
        started=started,
        cwd=cwd,
        copilot_version=copilot_version,
        model=model,
        source_path=path,
        events=events,
    )


def capture_copilot_session(
    cwd: Path,
    session_path: Path,
    *,
    config: UMXConfig | None = None,
) -> dict[str, Any]:
    cfg = config or default_config()
    transcript = parse_copilot_session(session_path)
    project_root = find_project_root(cwd)
    repo_dir = project_memory_dir(project_root)
    meta: dict[str, Any] = {
        "session_id": transcript.umx_session_id,
        "tool": "copilot",
        "source": "copilot-cli",
        "copilot_session_id": transcript.session_id,
        "copilot_events_path": str(transcript.source_path),
    }
    if transcript.started:
        meta["started"] = transcript.started
    if transcript.cwd:
        meta["copilot_cwd"] = transcript.cwd
    if transcript.copilot_version:
        meta["copilot_version"] = transcript.copilot_version
    if transcript.model:
        meta["copilot_model"] = transcript.model

    session_file = write_session(
        repo_dir,
        meta=meta,
        events=transcript.events,
        config=cfg,
        auto_commit=False,
    )
    return {
        "source_file": str(transcript.source_path),
        "umx_session_id": transcript.umx_session_id,
        "events_imported": len(transcript.events),
        "session_file": str(session_file),
        "tool": "copilot",
    }
