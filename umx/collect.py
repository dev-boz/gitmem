from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from umx.config import UMXConfig, default_config
from umx.dream.gates import increment_session_count
from umx.git_ops import git_add_and_commit, git_commit_failure_message, git_path_exists_at_ref
from umx.scope import find_project_root, project_memory_dir
from umx.sessions import generate_session_id, session_path, write_session


@dataclass(slots=True)
class CollectedSession:
    meta: dict[str, Any]
    events: list[dict[str, Any]]
    input_format: str


def _parse_jsonl_record(
    record: dict[str, Any],
    *,
    default_role: str,
) -> dict[str, Any]:
    event: dict[str, Any]
    nested = record.get("event")
    if isinstance(nested, dict):
        event = dict(nested)
        for key in ("ts", "tool", "type"):
            if key in record and key not in event:
                event[key] = record[key]
    else:
        event = dict(record)
    content = event.get("content")
    if not isinstance(content, str):
        message = event.get("message")
        if isinstance(message, str):
            event["content"] = message
        else:
            raise ValueError("Collected JSONL events must include string content.")
    role = event.get("role")
    if not isinstance(role, str) or not role.strip():
        event["role"] = default_role
    return event


def _parse_jsonl_input(raw_text: str, *, default_role: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    records: list[dict[str, Any]] = []
    for line_number, raw_line in enumerate(raw_text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Collected JSONL is invalid on line {line_number}: {exc.msg}."
            ) from exc
        if not isinstance(record, dict):
            raise ValueError(
                f"Collected JSONL is invalid on line {line_number}: expected an object."
            )
        records.append(record)
    if not records:
        raise ValueError("No session input provided.")

    meta: dict[str, Any] = {}
    event_records = records
    if "_meta" in records[0]:
        meta_record = records[0].get("_meta")
        if not isinstance(meta_record, dict):
            raise ValueError("Collected JSONL meta record must be an object.")
        meta = dict(meta_record)
        event_records = records[1:]

    events = [
        _parse_jsonl_record(record, default_role=default_role)
        for record in event_records
    ]
    if not events:
        raise ValueError("Collected JSONL did not include any session events.")
    return meta, events


def parse_collected_session(
    raw_text: str,
    *,
    tool: str,
    input_format: str,
    default_role: str = "assistant",
    session_id: str | None = None,
    extra_meta: dict[str, str] | None = None,
) -> CollectedSession:
    text = raw_text.strip()
    if not text:
        raise ValueError("No session input provided.")

    resolved_format = input_format
    if input_format == "auto":
        try:
            meta, events = _parse_jsonl_input(text, default_role=default_role)
            resolved_format = "jsonl"
        except ValueError:
            meta = {}
            events = [{"role": default_role, "content": text}]
            resolved_format = "text"
    elif input_format == "jsonl":
        meta, events = _parse_jsonl_input(text, default_role=default_role)
    else:
        meta = {}
        events = [{"role": default_role, "content": text}]

    normalized_meta = dict(meta)
    normalized_meta.setdefault("session_id", session_id or normalized_meta.get("session_id") or generate_session_id())
    normalized_meta["tool"] = tool
    normalized_meta.setdefault(
        "source",
        "manual-collect-jsonl" if resolved_format == "jsonl" else "manual-collect",
    )
    if extra_meta:
        normalized_meta.update(extra_meta)
    return CollectedSession(
        meta=normalized_meta,
        events=events,
        input_format=resolved_format,
    )


def parse_meta_pairs(values: tuple[str, ...]) -> dict[str, str]:
    meta: dict[str, str] = {}
    for value in values:
        key, separator, raw = value.partition("=")
        if not separator or not key.strip() or not raw.strip():
            raise ValueError(f"Invalid --meta value: {value!r}. Expected key=value.")
        meta[key.strip()] = raw.strip()
    return meta


def collect_session(
    cwd: Path,
    raw_text: str,
    *,
    tool: str,
    input_format: str = "auto",
    default_role: str = "assistant",
    session_id: str | None = None,
    extra_meta: dict[str, str] | None = None,
    source_file: Path | None = None,
    config: UMXConfig | None = None,
) -> dict[str, Any]:
    cfg = config or default_config()
    root = find_project_root(cwd)
    repo = project_memory_dir(root)
    collected = parse_collected_session(
        raw_text,
        tool=tool,
        input_format=input_format,
        default_role=default_role,
        session_id=session_id,
        extra_meta=extra_meta,
    )
    path_target = session_path(repo, str(collected.meta["session_id"]))
    relative_path = path_target.relative_to(repo).as_posix()
    new_session = not git_path_exists_at_ref(repo, "HEAD", relative_path)
    path = write_session(
        repo,
        meta=collected.meta,
        events=collected.events,
        config=cfg,
        auto_commit=False,
    )
    commit_result = git_add_and_commit(
        repo,
        paths=[path],
        message=f"umx: collect {tool} session",
        config=cfg,
    )
    if commit_result.failed:
        raise RuntimeError(
            git_commit_failure_message(
                commit_result,
                context="Failed to commit collected session.",
            )
        )
    session_count = increment_session_count(repo) if new_session else None
    payload: dict[str, Any] = {
        "tool": tool,
        "input_format": collected.input_format,
        "umx_session_id": collected.meta["session_id"],
        "events_imported": len(collected.events),
        "new_session": new_session,
        "session_count": session_count if new_session else None,
    }
    if source_file is not None:
        payload["source_file"] = str(source_file)
    return payload


def summarize_collected_session(
    cwd: Path,
    raw_text: str,
    *,
    tool: str,
    input_format: str = "auto",
    default_role: str = "assistant",
    session_id: str | None = None,
    extra_meta: dict[str, str] | None = None,
    source_file: Path | None = None,
) -> dict[str, Any]:
    root = find_project_root(cwd)
    repo = project_memory_dir(root)
    collected = parse_collected_session(
        raw_text,
        tool=tool,
        input_format=input_format,
        default_role=default_role,
        session_id=session_id,
        extra_meta=extra_meta,
    )
    path_target = session_path(repo, str(collected.meta["session_id"]))
    relative_path = path_target.relative_to(repo).as_posix()
    payload: dict[str, Any] = {
        "dry_run": True,
        "tool": tool,
        "input_format": collected.input_format,
        "umx_session_id": collected.meta["session_id"],
        "events_imported": len(collected.events),
        "new_session": not git_path_exists_at_ref(repo, "HEAD", relative_path),
    }
    if source_file is not None:
        payload["source_file"] = str(source_file)
    return payload
