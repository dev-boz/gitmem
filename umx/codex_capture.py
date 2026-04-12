from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from umx.config import UMXConfig, default_config
from umx.scope import find_project_root, project_memory_dir
from umx.sessions import write_session

_ROLLOUT_NAME_RE = re.compile(
    r"^rollout-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}-(?P<session_id>.+)\.jsonl$"
)
_SKIPPED_USER_PREFIXES = (
    "<environment_context>",
    "<turn_aborted>",
)
_SKIPPED_USER_MESSAGES = {".", "..."}


@dataclass(slots=True)
class CodexTranscript:
    codex_session_id: str
    started: str | None
    cwd: str | None
    cli_version: str | None
    source_path: Path
    events: list[dict[str, str]]

    @property
    def umx_session_id(self) -> str:
        return f"{self.session_date}-codex-{self.codex_session_id}"

    @property
    def session_date(self) -> str:
        if isinstance(self.started, str) and len(self.started) >= 10:
            return self.started[:10]
        parts = self.source_path.parent.parts[-3:]
        if len(parts) == 3:
            year, month, day = parts
            if year.isdigit() and month.isdigit() and day.isdigit():
                return f"{year}-{month}-{day}"
        return "1970-01-01"


def _codex_sessions_root(source_root: Path | None = None) -> Path:
    root = source_root or (Path.home() / ".codex")
    if root.name == "sessions":
        return root
    return root / "sessions"


def list_codex_rollouts(source_root: Path | None = None) -> list[Path]:
    sessions_root = _codex_sessions_root(source_root)
    if not sessions_root.exists():
        return []
    return sorted(sessions_root.glob("**/rollout-*.jsonl"))


def latest_codex_rollout_path(source_root: Path | None = None) -> Path | None:
    candidates = list_codex_rollouts(source_root)
    if not candidates:
        return None
    return max(candidates, key=lambda path: (path.stat().st_mtime_ns, str(path)))


def _fallback_codex_session_id(path: Path) -> str:
    match = _ROLLOUT_NAME_RE.match(path.name)
    if match is not None:
        return match.group("session_id")
    return path.stem


def _iter_rollout_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    try:
        raw_lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return records
    for raw_line in raw_lines:
        line = raw_line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            # Active Codex rollouts can end with a partially-written line.
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        if item.get("type") not in {"input_text", "output_text"}:
            continue
        text = item.get("text")
        if isinstance(text, str) and text.strip():
            parts.append(text.strip())
    return "\n\n".join(parts).strip()


def _skip_user_message(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return True
    if stripped in _SKIPPED_USER_MESSAGES:
        return True
    return stripped.startswith(_SKIPPED_USER_PREFIXES)


def parse_codex_rollout(path: Path) -> CodexTranscript:
    codex_session_id = _fallback_codex_session_id(path)
    started: str | None = None
    cwd: str | None = None
    cli_version: str | None = None
    events: list[dict[str, str]] = []
    seen_messages: set[tuple[str, str | None, str]] = set()

    for record in _iter_rollout_records(path):
        record_type = record.get("type")
        payload = record.get("payload", {})
        if not isinstance(payload, dict):
            continue

        if record_type == "session_meta":
            meta_session_id = payload.get("id")
            if isinstance(meta_session_id, str) and meta_session_id:
                codex_session_id = meta_session_id
            meta_started = payload.get("timestamp")
            if isinstance(meta_started, str) and meta_started:
                started = meta_started
            meta_cwd = payload.get("cwd")
            if isinstance(meta_cwd, str) and meta_cwd:
                cwd = meta_cwd
            version = payload.get("cli_version")
            if isinstance(version, str) and version:
                cli_version = version
            continue

        if record_type != "response_item":
            continue
        if payload.get("type") != "message":
            continue

        role = payload.get("role")
        if role not in {"user", "assistant"}:
            continue
        text = _message_text(payload.get("content"))
        if role == "user" and _skip_user_message(text):
            continue
        if not text:
            continue

        timestamp = record.get("timestamp")
        ts = timestamp if isinstance(timestamp, str) and timestamp else None
        key = (role, ts, text)
        if key in seen_messages:
            continue
        seen_messages.add(key)

        event: dict[str, str] = {
            "role": role,
            "content": text,
        }
        if ts is not None:
            event["ts"] = ts
        events.append(event)

    return CodexTranscript(
        codex_session_id=codex_session_id,
        started=started,
        cwd=cwd,
        cli_version=cli_version,
        source_path=path,
        events=events,
    )


def capture_codex_rollout(
    cwd: Path,
    rollout_path: Path,
    *,
    config: UMXConfig | None = None,
) -> dict[str, Any]:
    cfg = config or default_config()
    transcript = parse_codex_rollout(rollout_path)
    project_root = find_project_root(cwd)
    repo_dir = project_memory_dir(project_root)
    meta: dict[str, Any] = {
        "session_id": transcript.umx_session_id,
        "tool": "codex",
        "source": "codex-rollout",
        "codex_session_id": transcript.codex_session_id,
        "codex_rollout_path": str(transcript.source_path),
    }
    if transcript.started:
        meta["started"] = transcript.started
    if transcript.cwd:
        meta["codex_cwd"] = transcript.cwd
    if transcript.cli_version:
        meta["codex_cli_version"] = transcript.cli_version

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
        "tool": "codex",
    }
