from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from umx.config import UMXConfig, default_config
from umx.scope import find_project_root, project_memory_dir
from umx.sessions import write_session

# Record types emitted by Claude Code that we care about
_META_TYPES = {"system"}
_SKIP_TYPES = {"file-history-snapshot", "progress", "last-prompt"}


@dataclass(slots=True)
class ClaudeCodeTranscript:
    session_id: str  # UUID from the filename
    cwd: str | None  # cwd from system records
    version: str | None  # Claude Code version
    slug: str | None  # human-readable session slug
    started: str | None  # ISO timestamp of first turn
    source_path: Path
    events: list[dict[str, str]]  # [{role, content, ts?}]

    @property
    def umx_session_id(self) -> str:
        date = (self.started or "1970-01-01")[:10]
        return f"{date}-claude-code-{self.session_id[:8]}"


def _claude_projects_root(source_root: Path | None = None) -> Path:
    return source_root or (Path.home() / ".claude" / "projects")


def _project_hash(project_root: Path) -> str:
    """Convert a project root path to the Claude Code directory hash.

    Claude Code replaces all '/' with '-', so '/home/foo' → '-home-foo'.
    """
    return project_root.as_posix().replace("/", "-")


def _project_sessions_dir(
    project_root: Path,
    source_root: Path | None = None,
) -> Path | None:
    """Return the Claude Code sessions directory for *project_root*, or None."""
    projects_root = _claude_projects_root(source_root)
    candidate = projects_root / _project_hash(project_root)
    return candidate if candidate.is_dir() else None


def list_claude_code_sessions(
    project_root: Path | None = None,
    source_root: Path | None = None,
) -> list[Path]:
    """Return all Claude Code JSONL session files for *project_root*.

    If *project_root* is None, returns all sessions across all project dirs.
    """
    projects_root = _claude_projects_root(source_root)
    if not projects_root.exists():
        return []
    if project_root is not None:
        sessions_dir = _project_sessions_dir(project_root, source_root)
        if sessions_dir is None:
            return []
        return sorted(sessions_dir.glob("*.jsonl"))
    # All sessions across all project directories
    return sorted(projects_root.glob("**/*.jsonl"))


def latest_claude_code_session_path(
    project_root: Path | None = None,
    source_root: Path | None = None,
) -> Path | None:
    candidates = list_claude_code_sessions(project_root=project_root, source_root=source_root)
    if not candidates:
        return None
    return max(candidates, key=lambda p: (p.stat().st_mtime_ns, str(p)))


def _iter_session_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return records
    for raw_line in raw.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            # Active session files can end with a partially-written line.
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def _extract_text(content: Any) -> str:
    """Extract readable text from a Claude Code message content field."""
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "text":
            text = item.get("text", "")
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())
        # Skip: thinking, tool_use, tool_result, image
    return "\n\n".join(parts).strip()


def _is_tool_result_only(content: Any) -> bool:
    """Return True if content is entirely tool_result items (no human text)."""
    if isinstance(content, str):
        return False
    if not isinstance(content, list):
        return False
    return all(
        isinstance(item, dict) and item.get("type") == "tool_result"
        for item in content
        if isinstance(item, dict)
    )


def parse_claude_code_session(path: Path) -> ClaudeCodeTranscript:
    """Parse a Claude Code JSONL session file into a transcript."""
    session_id = path.stem  # UUID filename without extension
    cwd: str | None = None
    version: str | None = None
    slug: str | None = None
    started: str | None = None
    events: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    for record in _iter_session_records(path):
        rec_type = record.get("type")
        if rec_type in _SKIP_TYPES:
            continue

        # Extract session metadata from system records
        if rec_type == "system":
            if cwd is None:
                cwd = record.get("cwd") or None
            if version is None:
                version = record.get("version") or None
            if slug is None:
                slug = record.get("slug") or None
            ts = record.get("timestamp")
            if isinstance(ts, str) and ts and started is None:
                started = ts
            continue

        if rec_type not in ("user", "assistant"):
            continue

        # Skip sidechain (e.g. compact summaries, tool sub-chains)
        if record.get("isSidechain"):
            continue

        message = record.get("message")
        if not isinstance(message, dict):
            continue

        role = message.get("role")
        if role not in ("user", "assistant"):
            continue

        content = message.get("content")
        ts = record.get("timestamp")
        if not isinstance(ts, str):
            ts = None

        # Set started from the first user message
        if ts and started is None:
            started = ts

        # User messages that are entirely tool results → skip (no human text)
        if role == "user" and _is_tool_result_only(content):
            continue

        text = _extract_text(content)
        if not text:
            continue

        key = (role, text)
        if key in seen:
            continue
        seen.add(key)

        event: dict[str, str] = {"role": role, "content": text}
        if ts:
            event["ts"] = ts
        events.append(event)

    return ClaudeCodeTranscript(
        session_id=session_id,
        cwd=cwd,
        version=version,
        slug=slug,
        started=started,
        source_path=path,
        events=events,
    )


def capture_claude_code_session(
    cwd: Path,
    session_path: Path,
    *,
    config: UMXConfig | None = None,
) -> dict[str, Any]:
    """Parse one Claude Code session file and write it into the UMX repo."""
    cfg = config or default_config()
    project_root = find_project_root(cwd)
    repo_dir = project_memory_dir(project_root)
    prepared = prepare_claude_code_capture(session_path)
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


def prepare_claude_code_capture(session_path: Path) -> dict[str, Any]:
    transcript = parse_claude_code_session(session_path)
    meta: dict[str, Any] = {
        "session_id": transcript.umx_session_id,
        "tool": "claude-code",
        "source": "claude-code-session",
        "claude_code_session_id": transcript.session_id,
        "claude_code_session_path": str(transcript.source_path),
    }
    if transcript.started:
        meta["started"] = transcript.started
    if transcript.cwd:
        meta["claude_code_cwd"] = transcript.cwd
    if transcript.version:
        meta["claude_code_version"] = transcript.version
    if transcript.slug:
        meta["claude_code_slug"] = transcript.slug
    return {
        "meta": meta,
        "events": transcript.events,
        "result": {
            "source_file": str(transcript.source_path),
            "umx_session_id": transcript.umx_session_id,
            "events_imported": len(transcript.events),
            "tool": "claude-code",
        },
    }
