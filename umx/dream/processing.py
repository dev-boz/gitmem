from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from umx.dream.gates import read_dream_state
from umx.git_ops import git_path_exists_at_ref, git_read_text_at_ref, git_ref_exists
from umx.models import parse_datetime


def processing_log_path(repo_dir: Path) -> Path:
    return repo_dir / "meta" / "processing.jsonl"


def append_processing_event(repo_dir: Path, record: dict[str, Any]) -> None:
    path = processing_log_path(repo_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


def read_processing_log(repo_dir: Path, *, ref: str | None = None) -> list[dict[str, Any]]:
    if ref is None:
        path = processing_log_path(repo_dir)
        if not path.exists():
            return []
        content = path.read_text()
    else:
        if not git_ref_exists(repo_dir, ref):
            return []
        relative = processing_log_path(repo_dir).relative_to(repo_dir).as_posix()
        if not git_path_exists_at_ref(repo_dir, ref, relative):
            return []
        content = git_read_text_at_ref(repo_dir, ref, relative)
    rows: list[dict[str, Any]] = []
    for line in content.splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            rows.append(record)
    return rows


def _now_z() -> str:
    return datetime.now(tz=UTC).isoformat().replace("+00:00", "Z")


def _processing_actor() -> str:
    if os.getenv("GITHUB_ACTIONS") == "true":
        return "github-actions"
    return "local-cli"


def start_processing_run(
    repo_dir: Path,
    *,
    mode: str,
    force: bool,
    branch: str | None,
    tier: str = "l1",
) -> str:
    state = read_dream_state(repo_dir)
    run_id = f"dream-{uuid4().hex}"
    record: dict[str, Any] = {
        "run_id": run_id,
        "tier": tier,
        "mode": mode,
        "event": "started",
        "status": "running",
        "ts": _now_z(),
        "actor": _processing_actor(),
        "host": os.uname().nodename,
        "pid": os.getpid(),
        "branch": branch or "detached",
        "session_count": int(state.get("session_count", 0)),
    }
    last_dream = state.get("last_dream")
    if last_dream:
        record["last_dream"] = last_dream
    if force:
        record["force"] = True
    github_run_id = os.getenv("GITHUB_RUN_ID")
    if github_run_id:
        record["github_run_id"] = github_run_id
    append_processing_event(repo_dir, record)
    return run_id


def complete_processing_run(
    repo_dir: Path,
    run_id: str,
    *,
    mode: str,
    branch: str | None,
    added: int,
    pruned: int,
    message: str | None,
    tier: str = "l1",
    pr_branch: str | None = None,
    pr_number: int | None = None,
    dream_provider: str | None = None,
    dream_partial: bool = False,
) -> None:
    record: dict[str, Any] = {
        "run_id": run_id,
        "tier": tier,
        "mode": mode,
        "event": "completed",
        "status": "completed",
        "ts": _now_z(),
        "branch": branch or "detached",
        "added": added,
        "pruned": pruned,
    }
    if message:
        record["message"] = message
    if pr_branch:
        record["pr_branch"] = pr_branch
    if pr_number is not None:
        record["pr_number"] = pr_number
    if dream_provider:
        record["dream_provider"] = dream_provider
    if dream_partial:
        record["dream_partial"] = True
    append_processing_event(repo_dir, record)


def fail_processing_run(
    repo_dir: Path,
    run_id: str,
    *,
    mode: str,
    branch: str | None,
    error: str,
    tier: str = "l1",
) -> None:
    append_processing_event(
        repo_dir,
        {
            "run_id": run_id,
            "tier": tier,
            "mode": mode,
            "event": "failed",
            "status": "failed",
            "ts": _now_z(),
            "branch": branch or "detached",
            "error": error,
        },
    )


def _record_timestamp(record: dict[str, Any]) -> datetime:
    stamp = parse_datetime(str(record.get("ts") or ""))
    if stamp is not None:
        return stamp
    return datetime.min.replace(tzinfo=UTC)


def summarize_processing_log(
    repo_dir: Path,
    *,
    refs: tuple[str, ...] = (),
    stale_minutes: int = 30,
) -> dict[str, Any]:
    records = read_processing_log(repo_dir)
    seen = {json.dumps(record, sort_keys=True) for record in records}
    for ref in refs:
        for record in read_processing_log(repo_dir, ref=ref):
            key = json.dumps(record, sort_keys=True)
            if key in seen:
                continue
            seen.add(key)
            records.append(record)

    latest_by_run: dict[str, dict[str, Any]] = {}
    latest_at: dict[str, datetime] = {}
    for record in records:
        run_id = str(record.get("run_id") or "")
        if not run_id:
            continue
        stamp = _record_timestamp(record)
        if run_id not in latest_by_run or stamp >= latest_at[run_id]:
            latest_by_run[run_id] = record
            latest_at[run_id] = stamp

    now = datetime.now(tz=UTC)
    stale_before = now - timedelta(minutes=stale_minutes)
    active = sorted(
        [
            record
            for record in latest_by_run.values()
            if record.get("event") == "started" and _record_timestamp(record) > stale_before
        ],
        key=_record_timestamp,
        reverse=True,
    )
    last_event = max(records, key=_record_timestamp) if records else None
    last_completed = max(
        (record for record in records if record.get("event") == "completed"),
        key=_record_timestamp,
        default=None,
    )
    last_failed = max(
        (record for record in records if record.get("event") == "failed"),
        key=_record_timestamp,
        default=None,
    )
    return {
        "active_runs": len(active),
        "active": active,
        "last_event": last_event,
        "last_completed": last_completed,
        "last_failed": last_failed,
    }


def active_processing_runs(
    repo_dir: Path,
    *,
    refs: tuple[str, ...] = (),
    stale_minutes: int = 30,
) -> list[dict[str, Any]]:
    summary = summarize_processing_log(repo_dir, refs=refs, stale_minutes=stale_minutes)
    return list(summary["active"])
