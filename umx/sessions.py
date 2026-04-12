from __future__ import annotations

import gzip
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator

from umx.config import UMXConfig, default_config
from umx.identity import generate_fact_id
from umx.redaction import RedactionError, redact_jsonl_lines
from umx.scope import ensure_repo_structure


def generate_session_id(now: datetime | None = None) -> str:
    current = now or datetime.now(tz=UTC)
    return f"{current.date().isoformat()}-{generate_fact_id()}"


import re as _re

_SESSION_ID_UNSAFE = _re.compile(r"[/\\]|\.\.")


def _validate_session_id(session_id: str) -> None:
    """Validate session_id to prevent path traversal."""
    if not session_id or _SESSION_ID_UNSAFE.search(session_id):
        raise ValueError(f"Invalid session_id: {session_id!r}")


def session_path(repo_dir: Path, session_id: str) -> Path:
    _validate_session_id(session_id)
    year, month, *_ = session_id.split("-", 2)
    return repo_dir / "sessions" / year / month / f"{session_id}.jsonl"


def archive_path(repo_dir: Path, year: str, month: str) -> Path:
    return repo_dir / "sessions" / year / month / f"{year}-{month}-archive.jsonl.gz"


def session_index_path(repo_dir: Path, year: str, month: str) -> Path:
    return repo_dir / "sessions" / year / month / f"{year}-{month}-index.json"


def quarantine_path(repo_dir: Path, session_id: str) -> Path:
    return repo_dir / "local" / "quarantine" / f"{session_id}.jsonl"


def _iso_now() -> str:
    return datetime.now(tz=UTC).isoformat().replace("+00:00", "Z")


def normalize_session_payload(
    repo_dir: Path,
    meta: dict[str, Any],
    events: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    normalized_meta = dict(meta)
    normalized_meta.setdefault("project", repo_dir.name)
    had_started = bool(normalized_meta.get("started"))
    normalized_events: list[dict[str, Any]] = []
    first_ts: str | None = None
    for event in events:
        normalized = dict(event)
        timestamp = normalized.get("ts")
        if not isinstance(timestamp, str) or not timestamp:
            timestamp = _iso_now()
            normalized["ts"] = timestamp
        if first_ts is None:
            first_ts = timestamp
        normalized_events.append(normalized)
    if first_ts and not had_started:
        normalized_meta["started"] = first_ts
    normalized_meta.setdefault("started", _iso_now())
    return normalized_meta, normalized_events


def write_session(
    repo_dir: Path,
    meta: dict[str, Any],
    events: list[dict[str, Any]],
    config: UMXConfig | None = None,
    *,
    auto_commit: bool = True,
) -> Path:
    cfg = config or default_config()
    ensure_repo_structure(repo_dir)
    session_id = meta.setdefault("session_id", generate_session_id())
    normalized_meta, normalized_events = normalize_session_payload(repo_dir, meta, events)
    payload = [{"_meta": normalized_meta}, *normalized_events]
    try:
        redacted = payload if cfg.sessions.redaction == "none" else redact_jsonl_lines(payload, cfg)
    except RedactionError:
        path = quarantine_path(repo_dir, session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(json.dumps(line, sort_keys=True) for line in payload) + "\n")
        raise
    path = session_path(repo_dir, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(line, sort_keys=True) for line in redacted) + "\n")
    if auto_commit:
        from umx.git_ops import git_add_and_commit

        git_add_and_commit(repo_dir, paths=[path], message=f"umx: session {session_id}")
    return path


def read_session(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def list_sessions(repo_dir: Path) -> list[Path]:
    return sorted(repo_dir.glob("sessions/**/*.jsonl"))


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _session_started(meta: dict[str, Any], session_id: str) -> datetime | None:
    started = _parse_iso(meta.get("started"))
    if started is not None:
        return started
    try:
        return datetime.fromisoformat(session_id[:10]).replace(tzinfo=UTC)
    except ValueError:
        return None


def _archive_index_entry(
    session_id: str,
    payload: list[dict[str, Any]],
    archive_file: Path,
) -> dict[str, Any]:
    meta = dict(payload[0].get("_meta", {})) if payload and "_meta" in payload[0] else {}
    return {
        "session_id": session_id,
        "project": meta.get("project"),
        "tool": meta.get("tool"),
        "machine": meta.get("machine"),
        "started": meta.get("started"),
        "ended": meta.get("ended"),
        "duration_seconds": meta.get("duration_seconds"),
        "topics": list(meta.get("topics", [])) if isinstance(meta.get("topics"), list) else [],
        "archive": str(archive_file.relative_to(archive_file.parents[2])),
    }


def _read_archive_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _write_archive_records(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, sort_keys=True) + "\n")


def iter_session_payloads(
    repo_dir: Path,
    *,
    include_archived: bool = True,
    session_ids: set[str] | None = None,
) -> Iterator[tuple[str, list[dict[str, Any]]]]:
    wanted = session_ids or set()
    yielded: set[str] = set()
    for path in list_sessions(repo_dir):
        session_id = path.stem
        if wanted and session_id not in wanted:
            continue
        yield session_id, read_session(path)
        yielded.add(session_id)
    if not include_archived:
        return
    for index_file in sorted(repo_dir.glob("sessions/*/*/*-index.json")):
        archive_file = index_file.with_name(index_file.name.replace("-index.json", "-archive.jsonl.gz"))
        if not archive_file.exists():
            continue
        if wanted:
            try:
                index_data = json.loads(index_file.read_text())
            except json.JSONDecodeError:
                index_data = {}
            if not any(session_id in index_data and session_id not in yielded for session_id in wanted):
                continue
        for record in _read_archive_records(archive_file):
            session_id = str(record.get("session_id", "")).strip()
            payload = record.get("payload", record.get("records"))
            if not session_id or session_id in yielded or not isinstance(payload, list):
                continue
            if wanted and session_id not in wanted:
                continue
            yield session_id, payload
            yielded.add(session_id)


def archive_sessions(
    repo_dir: Path,
    *,
    now: datetime | None = None,
    config: UMXConfig | None = None,
) -> dict[str, int]:
    cfg = config or default_config()
    cutoff = (now or datetime.now(tz=UTC)) - timedelta(days=cfg.sessions.retention.active_days)
    monthly: dict[tuple[str, str], list[tuple[Path, list[dict[str, Any]]]]] = {}

    for path in list_sessions(repo_dir):
        payload = read_session(path)
        if not payload:
            continue
        meta = dict(payload[0].get("_meta", {})) if "_meta" in payload[0] else {}
        session_id = str(meta.get("session_id") or path.stem)
        started = _session_started(meta, session_id)
        if started is None or started > cutoff:
            continue
        year, month = session_id.split("-", 2)[:2]
        monthly.setdefault((year, month), []).append((path, payload))

    archived_sessions = 0
    for (year, month), entries in monthly.items():
        archive_file = archive_path(repo_dir, year, month)
        existing: dict[str, list[dict[str, Any]]] = {}
        for record in _read_archive_records(archive_file):
            session_id = str(record.get("session_id", "")).strip()
            payload = record.get("payload")
            if session_id and isinstance(payload, list):
                existing[session_id] = payload
        for path, payload in entries:
            meta = dict(payload[0].get("_meta", {})) if payload and "_meta" in payload[0] else {}
            session_id = str(meta.get("session_id") or path.stem)
            existing[session_id] = payload
        records = [
            {"session_id": session_id, "payload": payload}
            for session_id, payload in sorted(existing.items())
        ]
        _write_archive_records(archive_file, records)
        index_file = session_index_path(repo_dir, year, month)
        index_payload = {
            session_id: _archive_index_entry(session_id, payload, archive_file)
            for session_id, payload in sorted(existing.items())
        }
        index_file.write_text(json.dumps(index_payload, indent=2, sort_keys=True) + "\n")
        for path, payload in entries:
            meta = dict(payload[0].get("_meta", {})) if payload and "_meta" in payload[0] else {}
            session_id = str(meta.get("session_id") or path.stem)
            if session_id in existing:
                archived_sessions += 1
            path.unlink(missing_ok=True)
    return {
        "archived_sessions": archived_sessions,
        "archived_months": len(monthly),
    }


def remove_session_payload(repo_dir: Path, session_id: str) -> bool:
    path = session_path(repo_dir, session_id)
    if path.exists():
        path.unlink()
        return True

    year, month = session_id.split("-", 2)[:2]
    archive_file = archive_path(repo_dir, year, month)
    index_file = session_index_path(repo_dir, year, month)
    if not archive_file.exists():
        return False

    remaining = []
    removed = False
    for record in _read_archive_records(archive_file):
        record_session_id = str(record.get("session_id", "")).strip()
        if record_session_id == session_id:
            removed = True
            continue
        remaining.append(record)
    if not removed:
        return False

    if remaining:
        _write_archive_records(archive_file, remaining)
    else:
        archive_file.unlink(missing_ok=True)

    if index_file.exists():
        try:
            index_payload = json.loads(index_file.read_text())
        except json.JSONDecodeError:
            index_payload = {}
        if isinstance(index_payload, dict):
            index_payload.pop(session_id, None)
            if index_payload:
                index_file.write_text(json.dumps(index_payload, indent=2, sort_keys=True) + "\n")
            else:
                index_file.unlink(missing_ok=True)
    return True


def append_session_event(
    repo_dir: Path,
    session_id: str,
    event: dict[str, Any],
    *,
    tool: str | None = None,
    config: UMXConfig | None = None,
    auto_commit: bool = False,
) -> Path:
    path = session_path(repo_dir, session_id)
    meta: dict[str, Any]
    events: list[dict[str, Any]]
    if path.exists():
        payload = read_session(path)
        meta = dict(payload[0].get("_meta", {})) if payload else {"session_id": session_id}
        events = payload[1:] if len(payload) > 1 else []
    else:
        meta = {"session_id": session_id}
        events = []
    if tool and not meta.get("tool"):
        meta["tool"] = tool
    events.append(dict(event))
    return write_session(repo_dir, meta, events, config=config, auto_commit=auto_commit)
