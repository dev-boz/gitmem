from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass(slots=True, frozen=True)
class PushQueueEntry:
    branch: str
    set_upstream: bool
    queued_at: str
    attempts: int = 1
    last_error: str | None = None

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "PushQueueEntry":
        attempts = payload.get("attempts", 1)
        try:
            parsed_attempts = max(1, int(attempts))
        except (TypeError, ValueError):
            parsed_attempts = 1
        last_error = payload.get("last_error")
        return cls(
            branch=str(payload.get("branch") or "main"),
            set_upstream=bool(payload.get("set_upstream", False)),
            queued_at=str(payload.get("queued_at") or _iso_now()),
            attempts=parsed_attempts,
            last_error=str(last_error) if last_error else None,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempts": self.attempts,
            "branch": self.branch,
            "last_error": self.last_error,
            "queued_at": self.queued_at,
            "set_upstream": self.set_upstream,
        }


def _iso_now() -> str:
    return datetime.now(tz=UTC).isoformat().replace("+00:00", "Z")


def push_queue_path(repo_dir: Path) -> Path:
    return repo_dir / "local" / "push-queue.jsonl"


def load_push_queue(repo_dir: Path) -> list[PushQueueEntry]:
    path = push_queue_path(repo_dir)
    if not path.exists():
        return []
    entries: list[PushQueueEntry] = []
    for raw_line in path.read_text().splitlines():
        if not raw_line.strip():
            continue
        try:
            payload = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            entries.append(PushQueueEntry.from_dict(payload))
    return entries


def _write_push_queue(repo_dir: Path, entries: list[PushQueueEntry]) -> None:
    path = push_queue_path(repo_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not entries:
        path.unlink(missing_ok=True)
        return
    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text("\n".join(json.dumps(entry.to_dict(), sort_keys=True) for entry in entries) + "\n")
    tmp_path.replace(path)


def enqueue_push(
    repo_dir: Path,
    *,
    branch: str,
    set_upstream: bool = False,
    attempts: int = 1,
    last_error: str | None = None,
) -> PushQueueEntry:
    current = load_push_queue(repo_dir)
    existing = next(
        (
            entry
            for entry in current
            if entry.branch == branch and entry.set_upstream == set_upstream
        ),
        None,
    )
    queued_entry = PushQueueEntry(
        branch=branch,
        set_upstream=set_upstream,
        queued_at=existing.queued_at if existing else _iso_now(),
        attempts=(existing.attempts if existing else 0) + max(1, attempts),
        last_error=last_error or (existing.last_error if existing else None),
    )
    remaining = [
        entry
        for entry in current
        if not (entry.branch == branch and entry.set_upstream == set_upstream)
    ]
    remaining.append(queued_entry)
    _write_push_queue(repo_dir, remaining)
    return queued_entry


def remove_queued_push(repo_dir: Path, *, branch: str, set_upstream: bool = False) -> None:
    remaining = [
        entry
        for entry in load_push_queue(repo_dir)
        if not (entry.branch == branch and entry.set_upstream == set_upstream)
    ]
    _write_push_queue(repo_dir, remaining)


def push_queue_summary(repo_dir: Path) -> dict[str, object]:
    entries = load_push_queue(repo_dir)
    return {
        "branches": [entry.branch for entry in entries],
        "count": len(entries),
        "oldest_queued_at": min((entry.queued_at for entry in entries), default=None),
    }
