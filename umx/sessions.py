from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import gzip
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator

from umx.config import UMXConfig, default_config, load_config
from umx.git_ops import git_add_and_commit, git_commit_failure_message
from umx.identity import generate_fact_id
from umx.redaction import RedactionError, redact_jsonl_lines, redact_jsonl_lines_with_issues
from umx.search_semantic import load_semantic_cache, save_semantic_cache
from umx.scope import config_path, ensure_repo_structure


@dataclass(slots=True)
class QuarantineEntry:
    session_id: str
    path: Path
    reason: str
    snippet: str
    tool: str | None = None
    started: str | None = None
    quarantined_at: str | None = None


@dataclass(slots=True)
class QuarantineActionResult:
    ok: bool
    action: str
    message: str
    session_id: str | None = None


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


def archive_state_path(repo_dir: Path) -> Path:
    return repo_dir / ".umx.json"


def quarantine_path(repo_dir: Path, session_id: str) -> Path:
    return repo_dir / "local" / "quarantine" / f"{session_id}.jsonl"


def quarantine_metadata_path(repo_dir: Path, session_id: str) -> Path:
    return repo_dir / "local" / "quarantine" / f"{session_id}.meta.json"


def quarantine_decision_log_path(repo_dir: Path) -> Path:
    return repo_dir / "local" / "quarantine-decisions.jsonl"


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


def _payload_meta(payload: list[dict[str, Any]]) -> dict[str, Any]:
    if payload and "_meta" in payload[0]:
        return dict(payload[0].get("_meta", {}))
    return {}


def _write_quarantine_metadata(
    repo_dir: Path,
    session_id: str,
    *,
    reason: str,
    meta: dict[str, Any],
) -> None:
    path = quarantine_metadata_path(repo_dir, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "kind": "session",
                "session_id": session_id,
                "quarantined_at": _iso_now(),
                "reason": reason,
                "tool": meta.get("tool"),
                "started": meta.get("started"),
            },
            sort_keys=True,
        )
        + "\n"
    )


def _load_quarantine_metadata(repo_dir: Path, session_id: str) -> dict[str, Any]:
    path = quarantine_metadata_path(repo_dir, session_id)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _preview_redaction_config(config: UMXConfig | None = None) -> UMXConfig:
    cfg = config or load_config(config_path())
    preview = default_config()
    preview.sessions.redaction = "default"
    preview.sessions.entropy_threshold = cfg.sessions.entropy_threshold
    preview.sessions.entropy_min_length = cfg.sessions.entropy_min_length
    preview.sessions.entropy_assignment_patterns = list(cfg.sessions.entropy_assignment_patterns)
    return preview


def _payload_snippet(payload: list[dict[str, Any]]) -> str:
    records = payload[1:] if payload and "_meta" in payload[0] else payload
    for record in records:
        if not isinstance(record, dict):
            continue
        for key in ("content", "text", "message", "output"):
            value = record.get(key)
            if isinstance(value, str) and value.strip():
                return " ".join(value.split())
        dumped = json.dumps(record, sort_keys=True)
        if dumped:
            return " ".join(dumped.split())
    if payload:
        return " ".join(json.dumps(payload[0], sort_keys=True).split())
    return ""


def _reason_from_issues(payload: list[dict[str, Any]], config: UMXConfig) -> str:
    try:
        _, issues = redact_jsonl_lines_with_issues(payload, config)
    except RedactionError:
        return "unknown (legacy quarantine entry)"
    if not issues:
        return "unknown (legacy quarantine entry)"
    counts = Counter(issue.kind for issue in issues)
    return ", ".join(
        f"{kind} ({count} hit{'s' if count != 1 else ''})"
        for kind, count in sorted(counts.items())
    )


def _quarantine_snippet(payload: list[dict[str, Any]], config: UMXConfig) -> str:
    try:
        redacted_payload, _ = redact_jsonl_lines_with_issues(payload, config)
    except RedactionError:
        return "[snippet unavailable: could not safely redact]"
    snippet = _payload_snippet(redacted_payload)
    if not snippet:
        return "[snippet unavailable: no preview text]"
    return snippet[:157] + "..." if len(snippet) > 160 else snippet


def list_quarantined_sessions(
    repo_dir: Path,
    *,
    config: UMXConfig | None = None,
) -> list[QuarantineEntry]:
    quarantine_dir = repo_dir / "local" / "quarantine"
    if not quarantine_dir.exists():
        return []
    preview_cfg = _preview_redaction_config(config)
    entries: list[QuarantineEntry] = []
    for path in sorted(quarantine_dir.glob("*.jsonl"), reverse=True):
        try:
            payload = read_session(path)
        except json.JSONDecodeError:
            payload = []
        meta = _payload_meta(payload)
        session_id = str(meta.get("session_id") or path.stem)
        metadata = _load_quarantine_metadata(repo_dir, session_id)
        reason = str(metadata.get("reason") or "").strip() or _reason_from_issues(payload, preview_cfg)
        entries.append(
            QuarantineEntry(
                session_id=session_id,
                path=path,
                reason=reason,
                snippet=_quarantine_snippet(payload, preview_cfg),
                tool=str(metadata.get("tool") or meta.get("tool") or "") or None,
                started=str(metadata.get("started") or meta.get("started") or "") or None,
                quarantined_at=str(metadata.get("quarantined_at") or "") or None,
            )
        )
    return sorted(
        entries,
        key=lambda entry: ((entry.quarantined_at or ""), entry.session_id),
        reverse=True,
    )


def quarantine_summary(
    repo_dir: Path,
) -> dict[str, object]:
    quarantine_dir = repo_dir / "local" / "quarantine"
    if not quarantine_dir.exists():
        return {"count": 0, "files": []}
    files = sorted(
        path.relative_to(repo_dir).as_posix()
        for path in quarantine_dir.iterdir()
        if path.is_file() and not path.name.endswith(".meta.json")
    )
    return {"count": len(files), "files": files[:10]}


def _append_quarantine_decision(
    repo_dir: Path,
    *,
    action: str,
    session_id: str,
    reason: str,
    tool: str | None,
    started: str | None,
) -> None:
    path = quarantine_decision_log_path(repo_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": _iso_now(),
        "action": action,
        "session_id": session_id,
        "reason": reason,
        "source": "viewer",
        "tool": tool,
        "started": started,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


def release_quarantined_session(
    repo_dir: Path,
    session_id: str,
    *,
    confirm: bool,
    config: UMXConfig | None = None,
) -> QuarantineActionResult:
    if not confirm:
        return QuarantineActionResult(
            ok=False,
            action="release",
            message="release requires explicit confirm",
            session_id=session_id,
        )
    path = quarantine_path(repo_dir, session_id)
    if not path.exists():
        return QuarantineActionResult(
            ok=False,
            action="release",
            message=f"quarantined session not found: {session_id}",
            session_id=session_id,
        )
    cfg = config or load_config(config_path())
    if cfg.sessions.redaction == "none":
        return QuarantineActionResult(
            ok=False,
            action="release",
            message="release requires sessions.redaction to stay enabled",
            session_id=session_id,
        )
    payload = read_session(path)
    meta = _payload_meta(payload)
    resolved_session_id = str(meta.get("session_id") or session_id)
    destination = session_path(repo_dir, resolved_session_id)
    if destination.exists():
        return QuarantineActionResult(
            ok=False,
            action="release",
            message=f"session already exists: {resolved_session_id}",
            session_id=resolved_session_id,
        )
    try:
        redacted = redact_jsonl_lines(payload, cfg)
    except RedactionError as exc:
        return QuarantineActionResult(
            ok=False,
            action="release",
            message=f"release failed: {exc}",
            session_id=resolved_session_id,
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("\n".join(json.dumps(line, sort_keys=True) for line in redacted) + "\n")
    result = git_add_and_commit(
        repo_dir,
        paths=[destination],
        message=f"umx: release quarantined session {resolved_session_id}",
        config=cfg,
    )
    if result.failed:
        destination.unlink(missing_ok=True)
        return QuarantineActionResult(
            ok=False,
            action="release",
            message=git_commit_failure_message(result, context="commit failed"),
            session_id=resolved_session_id,
        )
    metadata = _load_quarantine_metadata(repo_dir, resolved_session_id)
    path.unlink(missing_ok=True)
    quarantine_metadata_path(repo_dir, resolved_session_id).unlink(missing_ok=True)
    _append_quarantine_decision(
        repo_dir,
        action="release",
        session_id=resolved_session_id,
        reason=str(metadata.get("reason") or "released after manual review"),
        tool=str(metadata.get("tool") or meta.get("tool") or "") or None,
        started=str(metadata.get("started") or meta.get("started") or "") or None,
    )
    return QuarantineActionResult(
        ok=True,
        action="release",
        message=f"released {resolved_session_id}",
        session_id=resolved_session_id,
    )


def discard_quarantined_session(repo_dir: Path, session_id: str) -> QuarantineActionResult:
    path = quarantine_path(repo_dir, session_id)
    if not path.exists():
        return QuarantineActionResult(
            ok=False,
            action="discard",
            message=f"quarantined session not found: {session_id}",
            session_id=session_id,
        )
    metadata = _load_quarantine_metadata(repo_dir, session_id)
    payload = read_session(path)
    meta = _payload_meta(payload)
    path.unlink(missing_ok=True)
    quarantine_metadata_path(repo_dir, session_id).unlink(missing_ok=True)
    _append_quarantine_decision(
        repo_dir,
        action="discard",
        session_id=session_id,
        reason=str(metadata.get("reason") or "discarded after manual review"),
        tool=str(metadata.get("tool") or meta.get("tool") or "") or None,
        started=str(metadata.get("started") or meta.get("started") or "") or None,
    )
    return QuarantineActionResult(
        ok=True,
        action="discard",
        message=f"discarded {session_id}",
        session_id=session_id,
    )


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
    except RedactionError as exc:
        path = quarantine_path(repo_dir, session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(json.dumps(line, sort_keys=True) for line in payload) + "\n")
        _write_quarantine_metadata(repo_dir, session_id, reason=str(exc), meta=normalized_meta)
        raise
    path = session_path(repo_dir, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(line, sort_keys=True) for line in redacted) + "\n")
    if auto_commit:
        result = git_add_and_commit(
            repo_dir,
            paths=[path],
            message=f"umx: session {session_id}",
            config=cfg,
        )
        if result.failed:
            raise RuntimeError(git_commit_failure_message(result, context="commit failed"))
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


def _read_archive_state(repo_dir: Path) -> dict[str, Any]:
    payload = load_semantic_cache(repo_dir)
    sessions = payload.get("sessions")
    return sessions if isinstance(sessions, dict) else {}


def _write_archive_state(repo_dir: Path, archived_at: datetime) -> None:
    payload = load_semantic_cache(repo_dir)
    sessions = payload.setdefault("sessions", {})
    if not isinstance(sessions, dict):
        sessions = {}
        payload["sessions"] = sessions
    sessions["last_archive_compaction"] = archived_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
    save_semantic_cache(repo_dir, payload)


def _archive_cadence_due(cadence: str, *, now: datetime, last_archive_at: datetime | None) -> bool:
    normalized = cadence.strip().lower() if cadence else "daily"
    if normalized == "never":
        return False
    if last_archive_at is None:
        return True
    if normalized == "weekly":
        now_year, now_week, _ = now.isocalendar()
        last_year, last_week, _ = last_archive_at.isocalendar()
        return (now_year, now_week) != (last_year, last_week)
    if normalized == "monthly":
        return (now.year, now.month) != (last_archive_at.year, last_archive_at.month)
    return now.date() != last_archive_at.date()


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


def scheduled_archive_sessions(
    repo_dir: Path,
    *,
    now: datetime | None = None,
    config: UMXConfig | None = None,
) -> dict[str, Any]:
    cfg = config or default_config()
    current = now or datetime.now(tz=UTC)
    cadence = cfg.sessions.archive_interval
    normalized = cadence.strip().lower() if cadence else "daily"
    state = _read_archive_state(repo_dir)
    last_archive_at = _parse_iso(
        state.get("last_archive_compaction") if isinstance(state.get("last_archive_compaction"), str) else None
    )
    if normalized == "never":
        return {"archived_sessions": 0, "archived_months": 0, "ran": False, "reason": "disabled"}
    if not _archive_cadence_due(cadence, now=current, last_archive_at=last_archive_at):
        return {
            "archived_sessions": 0,
            "archived_months": 0,
            "ran": False,
            "reason": f"{normalized}-not-due",
        }
    result = archive_sessions(repo_dir, now=current, config=cfg)
    _write_archive_state(repo_dir, current)
    return {
        **result,
        "ran": True,
        "reason": "first-run" if last_archive_at is None else f"{normalized}-due",
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
