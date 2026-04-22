"""Opt-in anonymous telemetry collection and upload."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

from umx import __version__
from umx.config import UMXConfig, default_config
from umx.dream.gates import read_dream_state
from umx.manifest import manifest_path
from umx.scope import get_umx_home, project_memory_dir

TELEMETRY_SCHEMA_VERSION = 1
TELEMETRY_RETRY_DELAY = timedelta(minutes=15)
TELEMETRY_MAX_QUEUE_EVENTS = 200
TELEMETRY_DISABLE_ENV = "UMX_TELEMETRY_DISABLE"
TELEMETRY_ENDPOINT_ENV = "UMX_TELEMETRY_ENDPOINT"


@dataclass(slots=True)
class TelemetryState:
    installation_id: str
    last_attempt: str | None = None
    next_attempt_after: str | None = None
    last_success: str | None = None
    last_error: str | None = None
    disabled_by_server: bool = False
    disabled_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "installation_id": self.installation_id,
            "last_attempt": self.last_attempt,
            "next_attempt_after": self.next_attempt_after,
            "last_success": self.last_success,
            "last_error": self.last_error,
            "disabled_by_server": self.disabled_by_server,
            "disabled_reason": self.disabled_reason,
        }

    @classmethod
    def fresh(cls) -> "TelemetryState":
        return cls(installation_id=uuid4().hex)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TelemetryState":
        installation_id = data.get("installation_id")
        if not isinstance(installation_id, str) or not installation_id.strip():
            return cls.fresh()
        return cls(
            installation_id=installation_id,
            last_attempt=data.get("last_attempt") if isinstance(data.get("last_attempt"), str) else None,
            next_attempt_after=(
                data.get("next_attempt_after")
                if isinstance(data.get("next_attempt_after"), str)
                else None
            ),
            last_success=data.get("last_success") if isinstance(data.get("last_success"), str) else None,
            last_error=data.get("last_error") if isinstance(data.get("last_error"), str) else None,
            disabled_by_server=bool(data.get("disabled_by_server", False)),
            disabled_reason=(
                data.get("disabled_reason")
                if isinstance(data.get("disabled_reason"), str)
                else None
            ),
        )


def telemetry_dir() -> Path:
    return get_umx_home() / "telemetry"


def telemetry_state_path() -> Path:
    return telemetry_dir() / "state.json"


def telemetry_queue_path() -> Path:
    return telemetry_dir() / "queue.jsonl"


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _now_z() -> str:
    return _now().isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _truthy(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _load_state() -> TelemetryState:
    path = telemetry_state_path()
    if not path.exists():
        return TelemetryState.fresh()
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return TelemetryState.fresh()
    if not isinstance(payload, dict):
        return TelemetryState.fresh()
    return TelemetryState.from_dict(payload)


def _save_state(state: TelemetryState) -> None:
    path = telemetry_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state.to_dict(), sort_keys=True) + "\n")


def _load_queue() -> list[dict[str, Any]]:
    path = telemetry_queue_path()
    if not path.exists():
        return []
    try:
        raw = path.read_text()
    except OSError:
        return []
    events: list[dict[str, Any]] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            events.append(payload)
    return events[-TELEMETRY_MAX_QUEUE_EVENTS:]


def _save_queue(events: list[dict[str, Any]]) -> None:
    path = telemetry_queue_path()
    if not events:
        if path.exists():
            path.unlink()
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    trimmed = events[-TELEMETRY_MAX_QUEUE_EVENTS:]
    with path.open("w", encoding="utf-8") as handle:
        for event in trimmed:
            handle.write(json.dumps(event, sort_keys=True) + "\n")


def _bucket_count(value: int) -> str:
    if value <= 0:
        return "0"
    if value < 10:
        return "1-9"
    if value < 50:
        return "10-49"
    if value < 200:
        return "50-199"
    if value < 1000:
        return "200-999"
    if value < 5000:
        return "1000-4999"
    return "5000+"


def _bucket_duration(duration_ms: int) -> str:
    if duration_ms < 50:
        return "<50"
    if duration_ms < 200:
        return "50-199"
    if duration_ms < 1000:
        return "200-999"
    if duration_ms < 5000:
        return "1000-4999"
    return "5000+"


def _command_segments(command_path: str) -> str:
    stripped = command_path.strip()
    return stripped.replace(" ", "/") if stripped else "unknown"


def _repo_snapshot(cwd: Path | None, config: UMXConfig) -> dict[str, Any] | None:
    if cwd is None:
        return None
    try:
        repo = project_memory_dir(cwd)
    except (OSError, RuntimeError, ValueError):
        return None

    snapshot: dict[str, Any] = {
        "present": repo.exists(),
        "mode": config.dream.mode,
        "search_backend": config.search.backend,
    }
    if not repo.exists():
        return snapshot

    try:
        state = read_dream_state(repo)
    except (OSError, RuntimeError, ValueError):
        return snapshot

    try:
        manifest = json.loads(manifest_path(repo).read_text())
    except (OSError, json.JSONDecodeError):
        manifest = {}
    fact_count = 0
    topics = manifest.get("topics") if isinstance(manifest, dict) else None
    if isinstance(topics, dict):
        for payload in topics.values():
            if isinstance(payload, dict):
                fact_count += int(payload.get("fact_count", 0) or 0)

    snapshot.update(
        {
            "fact_count_bucket": _bucket_count(fact_count),
            "pending_session_count_bucket": _bucket_count(int(state.get("session_count", 0))),
        }
    )
    return snapshot


def _effective_endpoint(config: UMXConfig) -> str:
    override = os.getenv(TELEMETRY_ENDPOINT_ENV)
    if override is not None:
        return override.strip()
    return config.telemetry.endpoint.strip()


def _effective_timeout(config: UMXConfig) -> int:
    return max(1, int(config.telemetry.timeout_seconds))


def _effective_batch_size(config: UMXConfig) -> int:
    return max(1, min(int(config.telemetry.batch_size), 100))


def _build_event(
    command_path: str,
    *,
    cwd: Path | None,
    config: UMXConfig,
    success: bool,
    duration_ms: int,
    error_kind: str | None,
) -> dict[str, Any]:
    event: dict[str, Any] = {
        "event_id": uuid4().hex,
        "event_kind": "cli_command",
        "ts": _now_z(),
        "command": _command_segments(command_path),
        "success": success,
        "duration_ms": duration_ms,
        "duration_bucket": _bucket_duration(duration_ms),
        "runtime": {
            "python": f"{sys.version_info.major}.{sys.version_info.minor}",
            "platform": sys.platform,
        },
        "config": {
            "mode": config.dream.mode,
            "search_backend": config.search.backend,
        },
    }
    normalized_error = _normalize_error_kind(error_kind)
    if normalized_error:
        event["error_kind"] = normalized_error
    snapshot = _repo_snapshot(cwd, config)
    if snapshot is not None:
        event["repo"] = snapshot
    return event


def _normalize_error_kind(error_kind: str | None) -> str | None:
    if error_kind is None:
        return None
    mapping = {
        "Abort": "abort",
        "ClickException": "click_error",
        "Exit": "exit",
        "UsageError": "usage_error",
    }
    return mapping.get(error_kind, "other")


def _kill_switched(state: TelemetryState) -> bool:
    return _truthy(os.getenv(TELEMETRY_DISABLE_ENV)) or state.disabled_by_server


def _should_attempt_upload(state: TelemetryState) -> bool:
    if state.disabled_by_server:
        return False
    next_attempt = _parse_timestamp(state.next_attempt_after)
    return next_attempt is None or _now() >= next_attempt


def _apply_server_kill_switch(state: TelemetryState, *, reason: str | None) -> None:
    stamp = _now_z()
    state.disabled_by_server = True
    state.disabled_reason = reason or "server kill switch"
    state.last_attempt = stamp
    state.last_success = stamp
    state.next_attempt_after = None
    state.last_error = None
    _save_state(state)
    _save_queue([])


def _record_failure(state: TelemetryState, message: str) -> None:
    now = _now()
    state.last_attempt = now.isoformat().replace("+00:00", "Z")
    state.next_attempt_after = (now + TELEMETRY_RETRY_DELAY).isoformat().replace("+00:00", "Z")
    state.last_error = message
    _save_state(state)


def _record_success(state: TelemetryState, remaining: list[dict[str, Any]]) -> None:
    stamp = _now_z()
    state.last_attempt = stamp
    state.last_success = stamp
    state.next_attempt_after = None
    state.last_error = None
    _save_state(state)
    _save_queue(remaining)


def _server_requested_disable(raw: str, headers: Any) -> tuple[bool, str | None]:
    if _truthy(headers.get("X-UMX-Telemetry-Disabled")):
        reason = headers.get("X-UMX-Telemetry-Reason")
        return True, reason if isinstance(reason, str) and reason.strip() else None
    if not raw.strip():
        return False, None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return False, None
    if not isinstance(payload, dict):
        return False, None
    kill_switch = payload.get("kill_switch")
    if not isinstance(kill_switch, dict) or kill_switch.get("enabled") is not True:
        return False, None
    reason = kill_switch.get("reason")
    return True, reason if isinstance(reason, str) and reason.strip() else None


def _send_batch(
    *,
    endpoint: str,
    state: TelemetryState,
    batch: list[dict[str, Any]],
    config: UMXConfig,
) -> None:
    if not endpoint:
        _record_failure(state, "telemetry endpoint is blank")
        return

    payload = {
        "schema_version": TELEMETRY_SCHEMA_VERSION,
        "installation_id": state.installation_id,
        "client": {"name": "gitmem", "version": __version__},
        "events": batch,
    }
    request = Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "content-type": "application/json",
            "user-agent": f"gitmem/{__version__} telemetry",
        },
        method="POST",
    )

    try:
        with urlopen(request, timeout=_effective_timeout(config)) as response:
            raw = response.read().decode("utf-8", errors="replace")
            disable, reason = _server_requested_disable(raw, response.headers)
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace").strip()
        if exc.code == 410:
            _apply_server_kill_switch(state, reason=detail or "telemetry disabled by server")
            return
        _record_failure(state, detail or f"HTTP {exc.code}")
        return
    except URLError as exc:
        _record_failure(state, str(exc.reason))
        return
    except OSError as exc:
        _record_failure(state, str(exc))
        return

    if disable:
        _apply_server_kill_switch(state, reason=reason)
        return

    queue = _load_queue()
    remaining = queue[len(batch) :]
    _record_success(state, remaining)


def record_cli_invocation(
    command_path: str,
    *,
    cwd: Path | None = None,
    success: bool,
    duration_ms: int,
    error_kind: str | None = None,
    config: UMXConfig | None = None,
) -> None:
    try:
        cfg = config or default_config()
        if not cfg.telemetry.enabled:
            return

        state = _load_state()
        if _kill_switched(state):
            return

        queue = _load_queue()
        queue.append(
            _build_event(
                command_path,
                cwd=cwd,
                config=cfg,
                success=success,
                duration_ms=max(0, int(duration_ms)),
                error_kind=error_kind,
            )
        )
        _save_queue(queue)

        if not _should_attempt_upload(state):
            return

        pending = _load_queue()
        if not pending:
            return
        batch = pending[: _effective_batch_size(cfg)]
        _send_batch(
            endpoint=_effective_endpoint(cfg),
            state=state,
            batch=batch,
            config=cfg,
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return


__all__ = [
    "record_cli_invocation",
    "telemetry_dir",
    "telemetry_queue_path",
    "telemetry_state_path",
]
