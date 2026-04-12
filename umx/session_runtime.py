from __future__ import annotations

from pathlib import Path
from typing import Any

from umx.budget import estimate_tokens
from umx.memory import load_all_facts
from umx.scope import find_project_root, project_memory_dir, user_memory_dir
from umx.search import advance_session_state, detect_referenced_fact_ids, record_reference
from umx.sessions import append_session_event


def record_session_event(
    cwd: Path,
    session_id: str,
    event: dict[str, Any],
    *,
    tool: str | None = None,
    context_window_tokens: int | None = None,
    persist: bool = True,
    auto_commit: bool = False,
) -> dict[str, Any] | None:
    project_repo = project_memory_dir(find_project_root(cwd))
    if persist:
        append_session_event(
            project_repo,
            session_id,
            event,
            tool=tool,
            auto_commit=auto_commit,
        )
    content = event.get("content")
    snapshot: dict[str, Any] | None = None
    if isinstance(content, str) and content.strip():
        snapshot = advance_session_state(
            project_repo,
            session_id,
            tool=tool,
            observed_tokens=estimate_tokens(content),
            context_window_tokens=context_window_tokens,
        )
    if event.get("role") != "assistant" or not isinstance(content, str) or not content.strip():
        return snapshot
    turn_index = int(snapshot["turn_index"]) if snapshot else None
    session_tokens = int(snapshot["estimated_tokens"]) if snapshot else None
    preview = " ".join(content.split())[:160]
    for repo_dir in [project_repo, user_memory_dir()]:
        if not repo_dir.exists():
            continue
        facts_by_id = {fact.fact_id: fact for fact in load_all_facts(repo_dir, include_superseded=False)}
        for fact_id in detect_referenced_fact_ids(
            repo_dir,
            session_id,
            content,
            facts_by_id=facts_by_id,
        ):
            record_reference(
                repo_dir,
                fact_id,
                session_id=session_id,
                turn_index=turn_index,
                session_tokens=session_tokens,
                referenced_at=event.get("ts"),
                content_preview=preview,
            )
    return snapshot
