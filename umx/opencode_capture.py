from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from umx.config import UMXConfig, default_config
from umx.scope import find_project_root, project_memory_dir
from umx.sessions import write_session


@dataclass(slots=True)
class OpenCodeSession:
    session_id: str
    slug: str | None
    directory: str | None
    title: str | None
    version: str | None
    time_created: int | None
    events: list[dict[str, str]]

    @property
    def started(self) -> str | None:
        return _iso_from_epoch_ms(self.time_created)

    @property
    def umx_session_id(self) -> str:
        date = (self.started or "1970-01-01")[:10]
        return f"{date}-opencode-{self.session_id[:8]}"


def _opencode_db_path(source_root: Path | None = None) -> Path:
    return source_root or (
        Path.home() / ".local" / "share" / "opencode" / "opencode.db"
    )


def _as_optional_str(value: Any) -> str | None:
    if isinstance(value, str):
        text = value.strip()
        return text or None
    return None


def _as_optional_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return int(text)
        except ValueError:
            return None
    return None


def _iso_from_epoch_ms(value: int | None) -> str | None:
    if value is None:
        return None
    try:
        return (
            datetime.fromtimestamp(value / 1000, tz=UTC)
            .isoformat()
            .replace("+00:00", "Z")
        )
    except (OverflowError, OSError, ValueError):
        return None


def _parse_json_dict(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, str):
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(value, dict):
        return None
    return value


def _normalize_directory(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    text = str(Path(value).expanduser()).replace("\\", "/")
    while len(text) > 1 and text.endswith("/"):
        text = text[:-1]
    return text or None


def _filter_sessions_for_project(
    sessions: list[OpenCodeSession], project_root: Path
) -> list[OpenCodeSession]:
    target = _normalize_directory(str(project_root))
    if target is None:
        return []

    exact_matches = [
        session
        for session in sessions
        if _normalize_directory(session.directory) == target
    ]
    if exact_matches:
        return exact_matches

    prefix = "/" if target == "/" else f"{target}/"
    return [
        session
        for session in sessions
        if (
            isinstance(session.directory, str)
            and (_normalize_directory(session.directory) or "").startswith(prefix)
        )
    ]


def _session_events(conn: sqlite3.Connection, session_id: str) -> list[dict[str, str]]:
    events: list[dict[str, str]] = []
    message_rows = conn.execute(
        """
        SELECT id, time_created, data
        FROM message
        WHERE session_id = ?
        ORDER BY time_created, id
        """,
        (session_id,),
    ).fetchall()

    for message_id, message_time_created, message_data in message_rows:
        message = _parse_json_dict(message_data)
        if message is None:
            continue
        role = message.get("role")
        if role not in {"user", "assistant"}:
            continue

        parts = conn.execute(
            """
            SELECT data
            FROM part
            WHERE session_id = ? AND message_id = ?
            ORDER BY time_created, id
            """,
            (session_id, message_id),
        ).fetchall()

        text_parts: list[str] = []
        for (part_data,) in parts:
            part = _parse_json_dict(part_data)
            if part is None or part.get("type") != "text":
                continue
            part_text = part.get("text")
            if isinstance(part_text, str) and part_text.strip():
                text_parts.append(part_text.strip())

        if not text_parts:
            continue

        event: dict[str, str] = {
            "role": role,
            "content": "\n\n".join(text_parts),
        }
        ts = _iso_from_epoch_ms(_as_optional_int(message_time_created))
        if ts:
            event["ts"] = ts
        events.append(event)

    return events


def list_opencode_sessions(
    project_root: Path | None = None,
    source_root: Path | None = None,
) -> list[OpenCodeSession]:
    db_path = _opencode_db_path(source_root)
    if not db_path.exists():
        return []

    sessions: list[OpenCodeSession] = []
    try:
        conn = sqlite3.connect(str(db_path))
    except sqlite3.Error:
        return []

    try:
        try:
            rows = conn.execute(
                """
                SELECT id, slug, directory, title, version, time_created
                FROM session
                ORDER BY time_created, id
                """
            ).fetchall()
        except sqlite3.Error:
            return []

        for session_id, slug, directory, title, version, time_created in rows:
            sid = _as_optional_str(session_id)
            if sid is None:
                continue
            sessions.append(
                OpenCodeSession(
                    session_id=sid,
                    slug=_as_optional_str(slug),
                    directory=_as_optional_str(directory),
                    title=_as_optional_str(title),
                    version=_as_optional_str(version),
                    time_created=_as_optional_int(time_created),
                    events=_session_events(conn, sid),
                )
            )
    finally:
        conn.close()

    if project_root is None:
        return sessions
    return _filter_sessions_for_project(sessions, project_root)


def latest_opencode_session(
    project_root: Path | None = None,
    source_root: Path | None = None,
) -> OpenCodeSession | None:
    sessions = list_opencode_sessions(
        project_root=project_root, source_root=source_root
    )
    if not sessions:
        return None
    return max(
        sessions, key=lambda session: (session.time_created or 0, session.session_id)
    )


def capture_opencode_session(
    cwd: Path,
    session: OpenCodeSession,
    *,
    config: UMXConfig | None = None,
) -> dict[str, Any]:
    cfg = config or default_config()
    project_root = find_project_root(cwd)
    repo_dir = project_memory_dir(project_root)

    meta: dict[str, Any] = {
        "session_id": session.umx_session_id,
        "tool": "opencode",
        "source": "opencode-db",
        "opencode_session_id": session.session_id,
    }
    if session.started:
        meta["started"] = session.started
    if session.slug:
        meta["opencode_slug"] = session.slug
    if session.directory:
        meta["opencode_directory"] = session.directory
    if session.title:
        meta["opencode_title"] = session.title
    if session.version:
        meta["opencode_version"] = session.version

    session_file = write_session(
        repo_dir,
        meta=meta,
        events=session.events,
        config=cfg,
        auto_commit=False,
    )
    return {
        "source_session_id": session.session_id,
        "umx_session_id": session.umx_session_id,
        "events_imported": len(session.events),
        "session_file": str(session_file),
        "tool": "opencode",
    }
