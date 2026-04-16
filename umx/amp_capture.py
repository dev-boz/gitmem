from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from umx.config import UMXConfig, default_config
from umx.scope import find_project_root, project_memory_dir
from umx.sessions import write_session


@dataclass(slots=True)
class AmpTranscript:
    thread_id: str
    started: str | None
    agent_mode: str | None
    client: str | None
    client_version: str | None
    project_roots: list[str]
    source_path: Path
    events: list[dict[str, str]]

    @property
    def umx_session_id(self) -> str:
        date = (self.started or "1970-01-01")[:10]
        return f"{date}-amp-{self.thread_id[:8]}"


def _amp_threads_root(source_root: Path | None = None) -> Path:
    root = source_root or (Path.home() / ".local" / "share" / "amp")
    if root.name == "threads":
        return root
    return root / "threads"


def _iso_from_epoch_ms(value: Any) -> str | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            value = int(text)
        except ValueError:
            return None
    if isinstance(value, float):
        value = int(value)
    if not isinstance(value, int):
        return None
    try:
        return (
            datetime.fromtimestamp(value / 1000, tz=UTC)
            .isoformat()
            .replace("+00:00", "Z")
        )
    except (OverflowError, OSError, ValueError):
        return None


def _normalize_directory(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    text = str(Path(value).expanduser()).replace("\\", "/")
    while len(text) > 1 and text.endswith("/"):
        text = text[:-1]
    return text or None


def _file_uri_to_path(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    parsed = urlparse(value)
    if parsed.scheme and parsed.scheme != "file":
        return None
    if parsed.scheme == "file":
        path = unquote(parsed.path or "")
    else:
        path = value
    if not path:
        return None
    return _normalize_directory(path)


def _load_thread_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def _extract_project_roots(data: dict[str, Any]) -> list[str]:
    env = data.get("env")
    if not isinstance(env, dict):
        return []
    initial = env.get("initial")
    if not isinstance(initial, dict):
        return []
    trees = initial.get("trees")
    if not isinstance(trees, list):
        return []

    roots: list[str] = []
    seen: set[str] = set()
    for tree in trees:
        if not isinstance(tree, dict):
            continue
        path = _file_uri_to_path(tree.get("uri"))
        if path and path not in seen:
            seen.add(path)
            roots.append(path)
    return roots


def _thread_matches_project(path: Path, project_root: Path) -> bool:
    data = _load_thread_json(path)
    if data is None:
        return False
    target = _normalize_directory(str(project_root))
    if target is None:
        return False
    prefix = "/" if target == "/" else f"{target}/"
    for root in _extract_project_roots(data):
        if root == target or root.startswith(prefix) or target.startswith(f"{root}/"):
            return True
    return False


def list_amp_threads(
    project_root: Path | None = None,
    source_root: Path | None = None,
) -> list[Path]:
    threads_root = _amp_threads_root(source_root)
    if not threads_root.exists():
        return []
    candidates = sorted(threads_root.glob("*.json"))
    if project_root is None:
        return candidates
    return [path for path in candidates if _thread_matches_project(path, project_root)]


def latest_amp_thread_path(
    project_root: Path | None = None,
    source_root: Path | None = None,
) -> Path | None:
    candidates = list_amp_threads(project_root=project_root, source_root=source_root)
    if not candidates:
        return None

    def sort_key(path: Path) -> tuple[int, int, str]:
        data = _load_thread_json(path) or {}
        created = data.get("created")
        if isinstance(created, str):
            try:
                created_value = int(created.strip())
            except ValueError:
                created_value = 0
        elif isinstance(created, (int, float)) and not isinstance(created, bool):
            created_value = int(created)
        else:
            created_value = 0
        return (created_value, path.stat().st_mtime_ns, str(path))

    return max(candidates, key=sort_key)


def _extract_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "text":
            continue
        text = item.get("text")
        if isinstance(text, str) and text.strip():
            parts.append(text.strip())
    return "\n\n".join(parts).strip()


def parse_amp_thread(path: Path) -> AmpTranscript:
    data = _load_thread_json(path) or {}
    thread_id = str(data.get("id") or path.stem)
    agent_mode = data.get("agentMode") if isinstance(data.get("agentMode"), str) else None
    started = _iso_from_epoch_ms(data.get("created"))

    env = data.get("env")
    client: str | None = None
    client_version: str | None = None
    if isinstance(env, dict):
        initial = env.get("initial")
        if isinstance(initial, dict):
            platform = initial.get("platform")
            if isinstance(platform, dict):
                client = platform.get("client") if isinstance(platform.get("client"), str) else None
                client_version = (
                    platform.get("clientVersion")
                    if isinstance(platform.get("clientVersion"), str)
                    else None
                )

    events: list[dict[str, str]] = []
    seen: set[tuple[str, str | None, str]] = set()
    for message in data.get("messages", []):
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        if role not in {"user", "assistant"}:
            continue
        text = _extract_text(message.get("content"))
        if not text:
            continue
        meta = message.get("meta")
        sent_at = meta.get("sentAt") if isinstance(meta, dict) else None
        ts = _iso_from_epoch_ms(sent_at)
        if started is None and ts is not None:
            started = ts
        key = (role, ts, text)
        if key in seen:
            continue
        seen.add(key)
        event: dict[str, str] = {"role": role, "content": text}
        if ts is not None:
            event["ts"] = ts
        events.append(event)

    return AmpTranscript(
        thread_id=thread_id,
        started=started,
        agent_mode=agent_mode,
        client=client,
        client_version=client_version,
        project_roots=_extract_project_roots(data),
        source_path=path,
        events=events,
    )


def capture_amp_thread(
    cwd: Path,
    thread_path: Path,
    *,
    config: UMXConfig | None = None,
) -> dict[str, Any]:
    cfg = config or default_config()
    transcript = parse_amp_thread(thread_path)
    project_root = find_project_root(cwd)
    repo_dir = project_memory_dir(project_root)

    meta: dict[str, Any] = {
        "session_id": transcript.umx_session_id,
        "tool": "amp",
        "source": "amp-thread",
        "amp_thread_id": transcript.thread_id,
        "amp_thread_path": str(transcript.source_path),
    }
    if transcript.started:
        meta["started"] = transcript.started
    if transcript.agent_mode:
        meta["amp_agent_mode"] = transcript.agent_mode
    if transcript.client:
        meta["amp_client"] = transcript.client
    if transcript.client_version:
        meta["amp_client_version"] = transcript.client_version
    if transcript.project_roots:
        meta["amp_project_roots"] = transcript.project_roots

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
        "tool": "amp",
    }
