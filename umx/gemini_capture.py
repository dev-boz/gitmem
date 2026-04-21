from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from umx.config import UMXConfig, default_config
from umx.scope import find_project_root, project_memory_dir
from umx.sessions import write_session


@dataclass(slots=True)
class GeminiTranscript:
    session_id: str
    project_slug: str
    start_time: str | None
    last_updated: str | None
    source_path: Path
    events: list[dict[str, str]]

    @property
    def umx_session_id(self) -> str:
        date = (self.start_time or "1970-01-01")[:10]
        return f"{date}-gemini-{self.session_id[:8]}"


def _gemini_projects_root(source_root: Path | None = None) -> Path:
    return source_root or (Path.home() / ".gemini")


def _project_slug_for_cwd(cwd: Path, source_root: Path | None = None) -> str | None:
    """Find the project slug for a given cwd by matching paths in projects.json."""
    gemini_root = _gemini_projects_root(source_root)
    projects_json = gemini_root / "projects.json"
    if not projects_json.exists():
        return None

    try:
        data = json.loads(projects_json.read_text(encoding="utf-8"))
        projects = data.get("projects", {})
    except (json.JSONDecodeError, OSError):
        return None

    # Find longest-prefix match
    cwd_str = cwd.resolve().as_posix()
    best_match = None
    best_prefix_len = -1

    for path_str, slug in projects.items():
        # Ensure path_str is an absolute path for matching
        try:
            abs_path = Path(path_str).resolve().as_posix()
            if cwd_str == abs_path or cwd_str.startswith(abs_path + "/"):
                if len(abs_path) > best_prefix_len:
                    best_prefix_len = len(abs_path)
                    best_match = slug
        except OSError:
            continue

    return best_match


def _gemini_chats_dir(project_slug: str, source_root: Path | None = None) -> Path:
    return _gemini_projects_root(source_root) / "tmp" / project_slug / "chats"


def list_gemini_sessions(
    project_root: Path | None = None,
    source_root: Path | None = None,
) -> list[Path]:
    """Return all Gemini JSON session files for *project_root*."""
    gemini_root = _gemini_projects_root(source_root)
    if not gemini_root.exists():
        return []

    if project_root is not None:
        slug = _project_slug_for_cwd(project_root, source_root)
        if not slug:
            return []
        chats_dir = _gemini_chats_dir(slug, source_root)
        if not chats_dir.is_dir():
            return []
        return sorted(chats_dir.glob("session-*.json"))

    # All sessions across all projects
    return sorted((gemini_root / "tmp").glob("*/chats/session-*.json"))


def latest_gemini_session_path(
    project_root: Path | None = None,
    source_root: Path | None = None,
) -> Path | None:
    candidates = list_gemini_sessions(project_root=project_root, source_root=source_root)
    if not candidates:
        return None
    # Use lastUpdated if possible, but fallback to file mtime
    return max(candidates, key=lambda p: (p.stat().st_mtime_ns, str(p)))


def parse_gemini_session(path: Path) -> GeminiTranscript:
    """Parse a Gemini JSON session file into a transcript."""
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (json.JSONDecodeError, OSError):
        return GeminiTranscript(
            session_id=path.stem,
            project_slug="unknown",
            start_time=None,
            last_updated=None,
            source_path=path,
            events=[],
        )

    session_id = data.get("sessionId", path.stem)
    # Extract project slug from path if not easily available elsewhere
    # Path is usually .../tmp/<slug>/chats/session-*.json
    # We look for the parent of the 'chats' directory if it exists.
    project_slug = "unknown"
    if path.parent.name == "chats":
        project_slug = path.parent.parent.name
    else:
        # Fallback to looking for 'tmp' in parts from the end
        parts = path.parts
        for i in range(len(parts) - 2, -1, -1):
            if parts[i] == "tmp" and i + 1 < len(parts):
                project_slug = parts[i + 1]
                break

    start_time = data.get("startTime")
    last_updated = data.get("lastUpdated")
    events: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    for msg in data.get("messages", []):
        msg_type = msg.get("type")
        if msg_type == "info":
            continue

        role = "user" if msg_type == "user" else "assistant" if msg_type == "gemini" else None
        if not role:
            continue

        content = msg.get("content")
        text = ""
        if msg_type == "user" and isinstance(content, list):
            text = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        elif msg_type == "gemini" and isinstance(content, str):
            text = content

        text = text.strip()
        if not text:
            continue

        ts = msg.get("timestamp")
        key = (role, text)
        if key in seen:
            continue
        seen.add(key)

        event = {"role": role, "content": text}
        if ts:
            event["ts"] = ts
        events.append(event)

    return GeminiTranscript(
        session_id=session_id,
        project_slug=project_slug,
        start_time=start_time,
        last_updated=last_updated,
        source_path=path,
        events=events,
    )


def capture_gemini_session(
    cwd: Path,
    session_path: Path,
    *,
    config: UMXConfig | None = None,
) -> dict[str, Any]:
    """Parse one Gemini session file and write it into the UMX repo."""
    cfg = config or default_config()
    project_root = find_project_root(cwd)
    repo_dir = project_memory_dir(project_root)
    prepared = prepare_gemini_capture(session_path)
    session_file = write_session(
        repo_dir,
        meta=dict(prepared["meta"]),
        events=prepared["events"],
        config=cfg,
        auto_commit=False,
    )
    result = dict(prepared["result"])
    result["session_file"] = str(session_file)
    return result


def prepare_gemini_capture(session_path: Path) -> dict[str, Any]:
    transcript = parse_gemini_session(session_path)
    meta: dict[str, Any] = {
        "session_id": transcript.umx_session_id,
        "tool": "gemini",
        "source": "gemini-chat",
        "gemini_session_id": transcript.session_id,
        "gemini_project_slug": transcript.project_slug,
        "gemini_session_path": str(transcript.source_path),
    }
    if transcript.start_time:
        meta["started"] = transcript.start_time
    if transcript.last_updated:
        meta["last_updated"] = transcript.last_updated
    return {
        "meta": meta,
        "events": transcript.events,
        "result": {
            "source_file": str(transcript.source_path),
            "umx_session_id": transcript.umx_session_id,
            "events_imported": len(transcript.events),
            "tool": "gemini",
        },
    }
