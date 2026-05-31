from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from umx.models import parse_datetime
from umx.scope import get_umx_home


def _state_path(repo_dir: Path) -> Path:
    return repo_dir / "meta" / "dream-state.json"


def read_dream_state(repo_dir: Path) -> dict[str, object]:
    path = _state_path(repo_dir)
    if not path.exists():
        return {"last_dream": None, "session_count": 0}
    return json.loads(path.read_text())


def increment_session_count(repo_dir: Path) -> int:
    state = read_dream_state(repo_dir)
    count = int(state.get("session_count", 0)) + 1
    state["session_count"] = count
    _state_path(repo_dir).write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    return count


def reset_session_count(repo_dir: Path) -> None:
    state = read_dream_state(repo_dir)
    state["session_count"] = 0
    _state_path(repo_dir).write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


def mark_dream_complete(repo_dir: Path, now: datetime) -> None:
    state = read_dream_state(repo_dir)
    state["last_dream"] = now.isoformat().replace("+00:00", "Z")
    state["session_count"] = 0
    _state_path(repo_dir).write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


def _lock_payload() -> dict[str, object]:
    stamp = datetime.now(tz=UTC).isoformat().replace("+00:00", "Z")
    return {
        "pid": os.getpid(),
        "hostname": os.uname().nodename,
        "started": stamp,
        "heartbeat": stamp,
    }


def _read_lock_payload(path: Path) -> dict[str, object]:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _pid_is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


class _JsonFileLock:
    stale_minutes: int

    @property
    def path(self) -> Path:
        raise NotImplementedError

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists() and not self.is_stale():
            return False
        self.path.write_text(json.dumps(_lock_payload(), indent=2, sort_keys=True) + "\n")
        return True

    def release(self) -> None:
        self.path.unlink(missing_ok=True)

    def heartbeat(self) -> None:
        if not self.path.exists():
            return
        payload = _read_lock_payload(self.path)
        payload["heartbeat"] = datetime.now(tz=UTC).isoformat().replace("+00:00", "Z")
        self.path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")

    def is_stale(self) -> bool:
        if not self.path.exists():
            return False
        payload = _read_lock_payload(self.path)
        heartbeat = parse_datetime(str(payload.get("heartbeat") or ""))
        pid = payload.get("pid")
        if isinstance(pid, bool):
            pid = None
        if isinstance(pid, str) and pid.isdigit():
            pid = int(pid)
        if isinstance(pid, int) and pid > 0 and not _pid_is_running(pid):
            return True
        if heartbeat is None:
            return True
        return heartbeat <= datetime.now(tz=UTC) - timedelta(minutes=self.stale_minutes)


@dataclass(slots=True)
class DreamLock(_JsonFileLock):
    repo_dir: Path
    stale_minutes: int = 30

    @property
    def path(self) -> Path:
        return self.repo_dir / "meta" / "dream.lock"

 

@dataclass(slots=True)
class UserDreamLock(_JsonFileLock):
    umx_home: Path = field(default_factory=get_umx_home)
    stale_minutes: int = 30

    @property
    def path(self) -> Path:
        return self.umx_home / "state" / "dream.lock"


@dataclass(slots=True)
class CompositeDreamLock:
    primary: _JsonFileLock
    secondary: _JsonFileLock

    def acquire(self) -> bool:
        if not self.primary.acquire():
            return False
        if self.secondary.acquire():
            return True
        self.primary.release()
        return False

    def release(self) -> None:
        self.secondary.release()
        self.primary.release()

    def heartbeat(self) -> None:
        self.primary.heartbeat()
        self.secondary.heartbeat()


def should_dream(
    repo_dir: Path,
    *,
    force: bool = False,
    session_threshold: int = 5,
    interval_hours: int = 24,
) -> bool:
    if force:
        return True
    state = read_dream_state(repo_dir)
    session_gate = int(state.get("session_count", 0)) >= session_threshold
    last_dream = parse_datetime(state.get("last_dream")) if state.get("last_dream") else None
    if last_dream is None:
        return True
    time_gate = last_dream <= datetime.now(tz=UTC) - timedelta(hours=interval_hours)
    return bool(session_gate or time_gate)
