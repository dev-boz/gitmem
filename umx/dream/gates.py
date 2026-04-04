"""Three-gate dream trigger and lock file management.

trigger = NOT locked AND (time_elapsed >= 24h OR session_count >= 5)
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path


class DreamLock:
    """Manages the .umx/dream.lock file for concurrency control."""

    def __init__(self, umx_dir: Path) -> None:
        self.lock_path = umx_dir / "dream.lock"

    @property
    def is_locked(self) -> bool:
        if not self.lock_path.exists():
            return False
        # Check for stale locks (> 1 hour)
        try:
            data = json.loads(self.lock_path.read_text())
            locked_at = datetime.fromisoformat(data.get("locked_at", ""))
            age = (datetime.now(timezone.utc) - locked_at).total_seconds()
            if age > 3600:
                self.release()
                return False
        except (json.JSONDecodeError, ValueError, OSError):
            self.release()
            return False
        return True

    def acquire(self) -> bool:
        """Attempt to acquire the lock. Returns True if successful."""
        if self.is_locked:
            return False
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "locked_at": datetime.now(timezone.utc).isoformat(),
            "pid": os.getpid(),
        }
        self.lock_path.write_text(json.dumps(data))
        return True

    def release(self) -> None:
        """Release the lock."""
        try:
            self.lock_path.unlink(missing_ok=True)
        except OSError:
            pass


def read_dream_state(umx_dir: Path) -> dict:
    """Read dream state from MEMORY.md or dream.log."""
    state = {
        "last_dream": None,
        "session_count": 0,
    }

    memory_md = umx_dir / "MEMORY.md"
    if memory_md.exists():
        content = memory_md.read_text()
        for line in content.splitlines():
            if line.startswith("last_dream:"):
                val = line.split(":", 1)[1].strip()
                if val and val != "never":
                    try:
                        state["last_dream"] = datetime.fromisoformat(val)
                    except ValueError:
                        pass
            elif line.startswith("session_count:"):
                try:
                    state["session_count"] = int(
                        line.split(":", 1)[1].strip()
                    )
                except ValueError:
                    pass

    return state


def should_dream(
    umx_dir: Path,
    force: bool = False,
    time_threshold_hours: int = 24,
    session_threshold: int = 5,
) -> bool:
    """Check if a dream should run based on three-gate trigger.

    Gate 1 (Lock): No concurrent dream — required.
    Gate 2 (Time): 24h since last dream — either/or with Gate 3.
    Gate 3 (Sessions): 5+ sessions since last dream — either/or with Gate 2.

    --force bypasses time and session gates but still respects the lock.
    """
    lock = DreamLock(umx_dir)

    # Gate 1: Lock (always required)
    if lock.is_locked:
        return False

    if force:
        return True

    state = read_dream_state(umx_dir)

    # Gate 2: Time elapsed
    time_gate = False
    if state["last_dream"] is None:
        time_gate = True
    else:
        last = state["last_dream"]
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        elapsed = (datetime.now(timezone.utc) - last).total_seconds() / 3600
        time_gate = elapsed >= time_threshold_hours

    # Gate 3: Session count
    session_gate = state["session_count"] >= session_threshold

    return time_gate or session_gate


def increment_session_count(umx_dir: Path) -> int:
    """Increment the session count. Called at session end."""
    state = read_dream_state(umx_dir)
    state["session_count"] += 1

    # Update MEMORY.md if it exists, otherwise just track in a sidecar
    counter_path = umx_dir / ".session_count"
    counter_path.write_text(str(state["session_count"]))

    return state["session_count"]
