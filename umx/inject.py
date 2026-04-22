from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from umx.budget import BudgetDecision, enforce_budget, estimate_tokens
from umx.config import load_config
from umx.conventions import summarize_conventions
from umx.memory import iter_fact_files, load_all_facts, read_fact_file, read_memory_md
from umx.models import Fact, Scope, TaskStatus
from umx.procedures import Procedure, load_all_procedures, match_procedures
from umx.search_semantic import semantic_similarity_map
from umx.scope import (
    config_path,
    encode_scope_path,
    find_project_root,
    project_memory_dir,
    user_memory_dir,
)
from umx.search import (
    active_working_set,
    attention_refresh_candidates,
    ensure_session_state,
    inject_candidate_ids,
    latest_referenced_turn,
    record_injections,
)
from umx.strength import relevance_score
from umx.tombstones import is_suppressed, load_tombstones


WORD_RE = re.compile(r"[a-zA-Z0-9_]+")
_USER_SCOPES = {Scope.USER, Scope.TOOL, Scope.MACHINE}
_GATHERED_FACT_CACHE: dict[
    tuple[Path, Path],
    tuple[
        tuple[tuple[str, int, int], ...],
        tuple[tuple[str, int, int], ...],
        tuple[Fact, ...],
        str,
    ],
] = {}


def _keywords(
    prompt: str | None,
    file_paths: list[str] | None = None,
    extra_parts: list[str] | None = None,
) -> set[str]:
    parts: list[str] = []
    if prompt:
        parts.append(prompt)
    if file_paths:
        parts.extend(file_paths)
    if extra_parts:
        parts.extend(part for part in extra_parts if part)
    return {match.group(0).lower() for part in parts for match in WORD_RE.finditer(part)}


def _scoped_paths(repo_dir: Path, file_paths: list[str] | None = None) -> list[Path]:
    if not file_paths:
        return []
    paths: list[Path] = []
    for file_path in file_paths:
        encoded_file = encode_scope_path(file_path)
        paths.append(repo_dir / "files" / f"{encoded_file}.md")
        relative = Path(file_path).parent
        if str(relative) not in {".", ""}:
            current = Path()
            for part in relative.parts:
                current /= part
                paths.append(repo_dir / "folders" / f"{encode_scope_path(current)}.md")
    return [path for path in paths if path.exists()]


def _fact_repo(fact: Fact, project_repo: Path, user_repo: Path) -> Path:
    return user_repo if fact.scope in {Scope.USER, Scope.TOOL, Scope.MACHINE} else project_repo


def _procedure_repo(procedure: Procedure, project_repo: Path, user_repo: Path) -> Path:
    return user_repo if procedure.scope == Scope.USER else project_repo


def _inventory_fingerprints(
    project_repo: Path,
    user_repo: Path,
) -> tuple[tuple[tuple[str, int, int], ...], tuple[tuple[str, int, int], ...]]:
    project_fact_paths = [path.resolve() for path in iter_fact_files(project_repo)]
    project_fact_set = {path.as_posix() for path in project_fact_paths}
    paths = [
        *project_fact_paths,
        project_repo / "meta" / "tombstones.jsonl",
        project_repo / "CONVENTIONS.md",
    ]
    if user_repo.exists():
        paths.extend(path.resolve() for path in iter_fact_files(user_repo))
    inventory_fingerprint: list[tuple[str, int, int]] = []
    project_fact_fingerprint: list[tuple[str, int, int]] = []
    for path in sorted({item.resolve() for item in paths}, key=lambda item: item.as_posix()):
        if path.exists():
            stat = path.stat()
            entry = (path.as_posix(), stat.st_mtime_ns, stat.st_size)
        else:
            entry = (path.as_posix(), -1, -1)
        inventory_fingerprint.append(entry)
        if path.as_posix() in project_fact_set:
            project_fact_fingerprint.append((str(path.relative_to(project_repo)), entry[1], entry[2]))
    return tuple(inventory_fingerprint), tuple(project_fact_fingerprint)


def _collect_base_facts_cached(
    project_repo: Path,
    user_repo: Path,
) -> tuple[list[Fact], str, tuple[tuple[str, int, int], ...]]:
    cache_key = (project_repo.resolve(), user_repo.resolve())
    fingerprint, project_fact_fingerprint = _inventory_fingerprints(project_repo, user_repo)
    cached = _GATHERED_FACT_CACHE.get(cache_key)
    if cached is not None and cached[0] == fingerprint:
        return list(cached[2]), cached[3], cached[1]

    tombstones = load_tombstones(project_repo)
    facts: list[Fact] = []
    if user_repo.exists():
        facts.extend(load_all_facts(user_repo, include_superseded=False))
    facts.extend(load_all_facts(project_repo, include_superseded=False))
    deduped = {fact.fact_id: fact for fact in facts}
    filtered = [
        fact
        for fact in deduped.values()
        if not is_suppressed(fact, tombstones, phase="gather")
    ]
    base_facts = tuple(
        fact
        for fact in filtered
        if fact.superseded_by is None and fact.scope != Scope.PROJECT_SECRET
    )
    convention_summary = summarize_conventions(project_repo / "CONVENTIONS.md")
    _GATHERED_FACT_CACHE[cache_key] = (
        fingerprint,
        project_fact_fingerprint,
        base_facts,
        convention_summary,
    )
    return list(base_facts), convention_summary, project_fact_fingerprint


def _load_scoped_facts(project_repo: Path, file_paths: list[str] | None = None) -> list[Fact]:
    tombstones = load_tombstones(project_repo)
    scoped_facts: list[Fact] = []
    for scoped_path in _scoped_paths(project_repo, file_paths):
        scoped_facts.extend(
            fact
            for fact in read_fact_file(scoped_path, repo_dir=project_repo)
            if fact.superseded_by is None
            and fact.scope != Scope.PROJECT_SECRET
            and not is_suppressed(fact, tombstones, phase="gather")
        )
    return scoped_facts


def _render_fact(fact: Fact, disclosure_level: str) -> str:
    prefix = "[fragile] " if fact.is_fragile else ""
    if disclosure_level == "l0":
        return f"- {fact.topic} · {fact.fact_id}"
    if disclosure_level == "l2":
        conflicts = f" conflicts={','.join(fact.conflicts_with)}" if fact.conflicts_with else ""
        source = fact.source_type.value
        status = fact.consolidation_status.value
        return (
            f"- {prefix}{fact.text} "
            f"[id:{fact.fact_id} S:{fact.encoding_strength} {fact.verification.value} "
            f"src:{source} status:{status}{conflicts}]"
        )
    return (
        f"- {prefix}{fact.text} "
        f"[id:{fact.fact_id} S:{fact.encoding_strength} {fact.verification.value} src:{fact.source_type.value}]"
    )


def _fact_token_cost(fact: Fact, disclosure_level: str) -> int:
    return estimate_tokens(_render_fact(fact, disclosure_level))


def _render_procedure(procedure: Procedure) -> list[str]:
    lines = [f"### {procedure.title}", procedure.steps_markdown.strip()]
    return [line for line in lines if line]


def _procedure_token_cost(procedure: Procedure) -> int:
    return estimate_tokens("\n".join(_render_procedure(procedure)))


def _attention_refresh_ids(
    project_repo: Path,
    user_repo: Path,
    *,
    session_id: str,
    session_tokens: int,
    context_window_tokens: int,
    refresh_window_pct: float,
    max_refreshes_per_fact: int,
) -> set[str]:
    refresh_ids: set[str] = set()
    for repo_dir in [project_repo, user_repo]:
        if not repo_dir.exists():
            continue
        rows = attention_refresh_candidates(
            repo_dir,
            session_id,
            context_window_tokens=context_window_tokens,
            current_session_tokens=session_tokens,
            refresh_window_pct=refresh_window_pct,
            max_refreshes_per_fact=max_refreshes_per_fact,
        )
        refresh_ids.update(row["fact_id"] for row in rows)
    return refresh_ids


def _disclosure_levels(
    selected: list[Fact],
    packing_scores: dict[str, float],
    *,
    always_ids: set[str],
    token_budget: int,
    disclosure_slack_pct: float,
    expanded_ids: set[str] | None = None,
) -> dict[str, str]:
    expanded = expanded_ids or set()
    levels: dict[str, str] = {}
    total_tokens = 0
    for fact in selected:
        if fact.fact_id in expanded or fact.is_fragile or fact.conflicts_with:
            level = "l2"
        else:
            level = "l1"
        levels[fact.fact_id] = level
        total_tokens += _fact_token_cost(fact, level)
    downgrade_candidates = [
        fact
        for fact in selected
        if fact.fact_id not in always_ids and levels.get(fact.fact_id) == "l1"
    ]
    downgrade_candidates.sort(key=lambda fact: packing_scores.get(fact.fact_id, 0.0))
    slack_pct = max(0.0, min(1.0, float(disclosure_slack_pct)))
    target_slack = int(round(token_budget * slack_pct)) if token_budget > 0 else 0
    while downgrade_candidates and (
        total_tokens > token_budget or token_budget - total_tokens < target_slack
    ):
        fact = downgrade_candidates.pop(0)
        total_tokens -= _fact_token_cost(fact, "l1") - _fact_token_cost(fact, "l0")
        levels[fact.fact_id] = "l0"
    return levels


def _enforce_rendered_budget(
    selected: list[Fact],
    disclosure_levels: dict[str, str],
    packing_scores: dict[str, float],
    *,
    fact_budget: int,
    always_ids: set[str],
) -> tuple[list[Fact], dict[str, str]]:
    total = sum(_fact_token_cost(fact, disclosure_levels.get(fact.fact_id, "l1")) for fact in selected)
    if total <= fact_budget:
        return selected, disclosure_levels
    mutable = sorted(
        [fact for fact in selected if fact.fact_id not in always_ids],
        key=lambda fact: packing_scores.get(fact.fact_id, 0.0),
    )
    for fact in mutable:
        current = disclosure_levels.get(fact.fact_id, "l1")
        if current == "l2":
            disclosure_levels[fact.fact_id] = "l1"
            total -= _fact_token_cost(fact, "l2") - _fact_token_cost(fact, "l1")
        if total <= fact_budget:
            return selected, disclosure_levels
        current = disclosure_levels.get(fact.fact_id, "l1")
        if current == "l1":
            disclosure_levels[fact.fact_id] = "l0"
            total -= _fact_token_cost(fact, "l1") - _fact_token_cost(fact, "l0")
        if total <= fact_budget:
            return selected, disclosure_levels
    while total > fact_budget and mutable:
        victim = mutable.pop(0)
        selected = [fact for fact in selected if fact.fact_id != victim.fact_id]
        total -= _fact_token_cost(victim, disclosure_levels.get(victim.fact_id, "l1"))
        disclosure_levels.pop(victim.fact_id, None)
    return selected, disclosure_levels


def _matched_procedures(
    project_repo: Path,
    user_repo: Path,
    *,
    tool: str | None = None,
    prompt: str | None = None,
    file_paths: list[str] | None = None,
    command_text: str | None = None,
) -> list[Procedure]:
    procedures: list[Procedure] = []
    if user_repo.exists():
        procedures.extend(load_all_procedures(user_repo))
    procedures.extend(load_all_procedures(project_repo))
    return match_procedures(
        procedures,
        tool=tool,
        prompt=prompt,
        file_paths=file_paths,
        command_text=command_text,
    )


def _select_procedures(
    procedures: list[Procedure],
    budget: int,
    *,
    max_items: int | None = None,
) -> tuple[list[Procedure], int]:
    selected: list[Procedure] = []
    used_tokens = 0
    for procedure in procedures:
        if max_items is not None and len(selected) >= max_items:
            break
        cost = _procedure_token_cost(procedure)
        if used_tokens + cost > budget:
            continue
        selected.append(procedure)
        used_tokens += cost
    return selected, used_tokens


def _render_hot_summary(project_repo: Path, token_budget: int) -> list[str]:
    if token_budget <= 0:
        return []
    memory_md = read_memory_md(project_repo).strip()
    if not memory_md:
        return []
    lines: list[str] = []
    used_tokens = 0
    for line in memory_md.splitlines():
        token_cost = estimate_tokens(line)
        if used_tokens + token_cost > token_budget:
            break
        lines.append(line)
        used_tokens += token_cost
    return lines


def collect_facts_for_injection(
    cwd: Path,
    *,
    tool: str | None = None,
    prompt: str | None = None,
    file_paths: list[str] | None = None,
    command_text: str | None = None,
) -> tuple[list[Fact], str, set[str], tuple[tuple[str, int, int], ...]]:
    root = find_project_root(cwd)
    project_repo = project_memory_dir(root)
    user_repo = user_memory_dir()
    facts, convention_summary, project_fact_fingerprint = _collect_base_facts_cached(
        project_repo,
        user_repo,
    )
    scoped_facts = _load_scoped_facts(project_repo, file_paths)
    facts.extend(scoped_facts)

    deduped = {fact.fact_id: fact for fact in facts}
    return (
        list(deduped.values()),
        convention_summary,
        {fact.fact_id for fact in scoped_facts},
        project_fact_fingerprint,
    )


def _inject_candidate_limit(config) -> int:
    return max(
        128,
        config.inject.max_concurrent_facts * 8,
        int(config.search.embedding.candidate_limit),
    )


def _inject_candidate_query(keywords: set[str]) -> str:
    tokens = sorted(token for token in keywords if token)
    if not tokens:
        return ""
    return " OR ".join(f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens)


def _candidate_facts_for_scoring(
    facts: list[Fact],
    *,
    config,
    keywords: set[str],
    project_repo: Path,
    user_repo: Path,
    scoped_fact_ids: set[str],
    project_fact_fingerprint: tuple[tuple[str, int, int], ...],
    refresh_ids: set[str],
    handoff_fact_ids: set[str] | None,
    restrict_to_ids: set[str] | None,
) -> list[Fact]:
    if restrict_to_ids is not None or config.search.backend != "fts5":
        return facts
    project_facts = [fact for fact in facts if fact.scope not in _USER_SCOPES]
    project_fact_count = len(project_facts)
    candidate_limit = _inject_candidate_limit(config)
    if project_fact_count <= candidate_limit:
        return facts
    query = _inject_candidate_query(keywords)
    if not query:
        return facts
    shortlisted_ids = set(
        inject_candidate_ids(
            project_repo,
            query,
            limit=candidate_limit,
            config=config,
            stat_fingerprint=project_fact_fingerprint,
        )
    )
    if not shortlisted_ids:
        return facts
    mandatory_project_ids = {
        fact.fact_id
        for fact in project_facts
        if (
            fact.task_status in {TaskStatus.OPEN, TaskStatus.BLOCKED}
            or fact.fact_id in refresh_ids
            or fact.fact_id in scoped_fact_ids
            or (handoff_fact_ids and fact.fact_id in handoff_fact_ids)
        )
    }
    allowed_project_ids = shortlisted_ids | mandatory_project_ids
    return [
        fact
        for fact in facts
        if fact.scope in _USER_SCOPES or fact.fact_id in allowed_project_ids
    ]


def build_injection_block(
    cwd: Path,
    *,
    tool: str | None = None,
    prompt: str | None = None,
    file_paths: list[str] | None = None,
    max_tokens: int = 4000,
    session_id: str | None = None,
    injection_point: str = "prompt",
    parent_session_id: str | None = None,
    command_text: str | None = None,
    expanded_ids: set[str] | None = None,
    include_hot_summary: bool = False,
    hot_summary_tokens: int | None = None,
    handoff_fact_ids: set[str] | None = None,
    restrict_to_ids: set[str] | None = None,
    allow_procedures: bool = True,
    context_window_tokens: int | None = None,
) -> str:
    root = find_project_root(cwd)
    project_repo = project_memory_dir(root)
    user_repo = user_memory_dir()
    cfg = load_config(config_path())
    keywords = _keywords(prompt, file_paths, extra_parts=[tool or "", command_text or ""])
    target_scope = Scope.FILE if file_paths else Scope.PROJECT
    facts, convention_summary, scoped_fact_ids, project_fact_fingerprint = collect_facts_for_injection(
        cwd,
        tool=tool,
        prompt=prompt,
        file_paths=file_paths,
        command_text=command_text,
    )
    if restrict_to_ids is not None:
        facts = [fact for fact in facts if fact.fact_id in restrict_to_ids]
    session_state = None
    if session_id:
        session_state = ensure_session_state(
            project_repo,
            session_id,
            tool=tool,
            parent_session_id=parent_session_id,
            avg_tokens_per_turn=cfg.inject.turn_token_estimate,
            context_window_tokens=context_window_tokens,
        )

    refresh_ids: set[str] = set()
    effective_window = int(
        context_window_tokens
        if context_window_tokens is not None
        else (session_state["context_window_tokens"] if session_state else 0)
    )
    if session_id and session_state and effective_window > 0:
        refresh_ids = _attention_refresh_ids(
            project_repo,
            user_repo,
            session_id=session_id,
            session_tokens=int(session_state["estimated_tokens"]),
            context_window_tokens=effective_window,
            refresh_window_pct=cfg.inject.refresh_window_pct,
            max_refreshes_per_fact=cfg.inject.max_refreshes_per_fact,
        )
    facts = _candidate_facts_for_scoring(
        facts,
        config=cfg,
        keywords=keywords,
        project_repo=project_repo,
        user_repo=user_repo,
        scoped_fact_ids=scoped_fact_ids,
        project_fact_fingerprint=project_fact_fingerprint,
        refresh_ids=refresh_ids,
        handoff_fact_ids=handoff_fact_ids,
        restrict_to_ids=restrict_to_ids,
    )
    semantic_scores: dict[str, float] = {}
    semantic_query = " ".join(
        part for part in [tool or "", command_text or "", prompt or "", *(file_paths or [])] if part
    ).strip()
    if cfg.search.backend == "hybrid" and semantic_query:
        project_facts = [
            fact for fact in facts if _fact_repo(fact, project_repo, user_repo) == project_repo
        ]
        user_facts = [
            fact for fact in facts if _fact_repo(fact, project_repo, user_repo) == user_repo
        ]
        if project_facts:
            semantic_scores.update(
                semantic_similarity_map(project_repo, project_facts, semantic_query, config=cfg)
            )
        if user_repo.exists() and user_facts:
            semantic_scores.update(
                semantic_similarity_map(user_repo, user_facts, semantic_query, config=cfg)
            )

    scored = {}
    for fact in facts:
        if fact.scope == Scope.PROJECT_SECRET:
            continue
        refresh_bonus = 1.5 if fact.fact_id in refresh_ids else 0.0
        scored[fact.fact_id] = (
            relevance_score(
                fact,
                target_scope=target_scope,
                keywords=keywords,
                recent_retrieval=1.0 if fact.fact_id in refresh_ids else 0.0,
                semantic_similarity=semantic_scores.get(fact.fact_id, 0.0),
                config=cfg,
            )
            + refresh_bonus
        )
    always = {
        fact.fact_id
        for fact in facts
        if (fact.scope == Scope.USER and fact.encoding_strength >= 4)
        or fact.task_status in {TaskStatus.OPEN, TaskStatus.BLOCKED}
        or (handoff_fact_ids and fact.fact_id in handoff_fact_ids)
    }
    reserved_tokens = estimate_tokens(convention_summary) if convention_summary else 0
    hot_summary_lines = (
        _render_hot_summary(project_repo, hot_summary_tokens or cfg.inject.subagent_hot_tokens)
        if include_hot_summary
        else []
    )
    hot_summary_cost = estimate_tokens("\n".join(hot_summary_lines)) if hot_summary_lines else 0
    if allow_procedures:
        selected_procedures, procedure_tokens = _select_procedures(
            _matched_procedures(
                project_repo,
                user_repo,
                tool=tool,
                prompt=prompt,
                file_paths=file_paths,
                command_text=command_text,
            ),
            max(0, max_tokens - reserved_tokens - hot_summary_cost),
            max_items=cfg.inject.max_concurrent_facts,
        )
    else:
        selected_procedures, procedure_tokens = [], 0
    non_secret_facts = [fact for fact in facts if fact.scope != Scope.PROJECT_SECRET]
    remaining_budget = max(1, max_tokens - reserved_tokens - procedure_tokens - hot_summary_cost)
    budget_decision: BudgetDecision = enforce_budget(
        non_secret_facts,
        max_tokens=remaining_budget,
        relevance_scores=scored,
        always_include_ids=always,
        config=cfg,
    )
    selected = budget_decision.selected
    removable = [
        fact
        for fact in selected
        if fact.fact_id not in always
    ]
    removable.sort(key=lambda fact: budget_decision.packing_scores.get(fact.fact_id, 0.0))
    while len(selected_procedures) + len(selected) > cfg.inject.max_concurrent_facts and removable:
        victim = removable.pop(0)
        selected = [fact for fact in selected if fact.fact_id != victim.fact_id]
    disclosure_levels = _disclosure_levels(
        selected,
        budget_decision.packing_scores,
        always_ids=always,
        token_budget=remaining_budget,
        disclosure_slack_pct=cfg.inject.disclosure_slack_pct,
        expanded_ids=expanded_ids,
    )
    selected, disclosure_levels = _enforce_rendered_budget(
        selected,
        disclosure_levels,
        budget_decision.packing_scores,
        fact_budget=remaining_budget,
        always_ids=always,
    )

    lines = ["# UMX Memory"]
    pending_injections: dict[Path, list[dict[str, object]]] = {}
    if convention_summary:
        lines.extend(["", "## Conventions", convention_summary])
    if hot_summary_lines:
        lines.extend(["", "## Hot Summary", *hot_summary_lines])
    if selected_procedures:
        lines.append("")
        lines.append("## Procedures")
        for procedure in selected_procedures:
            lines.extend(_render_procedure(procedure))
            repo_dir = _procedure_repo(procedure, project_repo, user_repo)
            pending_injections.setdefault(repo_dir, []).append(
                {
                    "fact_id": procedure.procedure_id,
                    "session_id": session_id,
                    "turn_index": int(session_state["turn_index"]) if session_state else None,
                    "session_tokens": int(session_state["estimated_tokens"]) if session_state else None,
                    "injection_point": injection_point,
                    "disclosure_level": "l2",
                    "tool": tool,
                    "parent_session_id": parent_session_id,
                    "token_count": _procedure_token_cost(procedure),
                    "item_kind": "procedure",
                }
            )
    open_task_lines = []
    for fact in selected:
        if fact.task_status in {TaskStatus.OPEN, TaskStatus.BLOCKED}:
            open_task_lines.append(f"- {fact.text}")
    if open_task_lines:
        lines.extend(["", "## Open Tasks", *open_task_lines])
    current_topic: str | None = None
    lines.append("")
    lines.append("## Facts")
    for fact in selected:
        repo_dir = _fact_repo(fact, project_repo, user_repo)
        disclosure_level = disclosure_levels.get(fact.fact_id, "l1")
        pending_injections.setdefault(repo_dir, []).append(
            {
                "fact_id": fact.fact_id,
                "session_id": session_id,
                "turn_index": int(session_state["turn_index"]) if session_state else None,
                "session_tokens": int(session_state["estimated_tokens"]) if session_state else None,
                "injection_point": (
                    "attention_refresh" if fact.fact_id in refresh_ids else injection_point
                ),
                "disclosure_level": disclosure_level,
                "tool": tool,
                "parent_session_id": parent_session_id,
                "token_count": _fact_token_cost(fact, disclosure_level),
            }
        )
        if fact.task_status in {TaskStatus.OPEN, TaskStatus.BLOCKED}:
            continue
        if fact.topic != current_topic:
            current_topic = fact.topic
            lines.append(f"### {current_topic}")
        lines.append(_render_fact(fact, disclosure_level))
    for repo_dir, events in pending_injections.items():
        record_injections(repo_dir, events)
    return "\n".join(lines).strip() + "\n"


def build_subagent_handoff(
    cwd: Path,
    *,
    parent_session_id: str,
    subagent_session_id: str | None = None,
    tool: str | None = None,
    objective: str | None = None,
    max_tokens: int | None = None,
) -> str:
    root = find_project_root(cwd)
    project_repo = project_memory_dir(root)
    user_repo = user_memory_dir()
    cfg = load_config(config_path())
    handoff_ids = set()
    latest_turn = max(
        latest_referenced_turn(repo_dir, parent_session_id)
        for repo_dir in [project_repo, user_repo]
        if repo_dir.exists()
    ) if any(repo_dir.exists() for repo_dir in [project_repo, user_repo]) else 0
    for repo_dir in [project_repo, user_repo]:
        if not repo_dir.exists():
            continue
        handoff_ids.update(
            row["fact_id"]
            for row in active_working_set(
                repo_dir,
                parent_session_id,
                exact_turn=latest_turn,
            )
        )
        handoff_ids.update(
            fact.fact_id
            for fact in load_all_facts(repo_dir, include_superseded=False)
            if fact.task_status in {TaskStatus.OPEN, TaskStatus.BLOCKED}
        )
    return build_injection_block(
        cwd,
        tool=tool,
        prompt=objective,
        max_tokens=max_tokens or cfg.inject.subagent_max_tokens,
        session_id=subagent_session_id,
        injection_point="subagent",
        parent_session_id=parent_session_id,
        include_hot_summary=True,
        hot_summary_tokens=cfg.inject.subagent_hot_tokens,
        handoff_fact_ids=handoff_ids,
        restrict_to_ids=handoff_ids,
        allow_procedures=False,
        context_window_tokens=None,
    )


def emit_gap_signal(
    repo_dir: Path,
    *,
    query: str,
    resolution_context: str,
    proposed_fact: str,
    session: str,
) -> dict[str, str]:
    query_text = query.strip()
    resolution_text = resolution_context.strip()
    proposed_text = proposed_fact.strip()
    session_text = session.strip()
    if not query_text:
        raise ValueError("gap query must not be empty")
    if not resolution_text:
        raise ValueError("gap resolution_context must not be empty")
    if not proposed_text:
        raise ValueError("gap proposed_fact must not be empty")
    if not session_text:
        raise ValueError("gap session must not be empty")
    path = repo_dir / "meta" / "gaps.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "type": "gap",
        "query": query_text,
        "resolution_context": resolution_text,
        "proposed_fact": proposed_text,
        "session": session_text,
        "ts": datetime.now(tz=UTC).isoformat().replace("+00:00", "Z"),
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")
    return record


def inject_for_tool(
    cwd: Path,
    *,
    tool: str | None = None,
    prompt: str | None = None,
    file_paths: list[str] | None = None,
    max_tokens: int = 4000,
    session_id: str | None = None,
    injection_point: str = "prompt",
    parent_session_id: str | None = None,
    command_text: str | None = None,
    expanded_ids: set[str] | None = None,
    context_window_tokens: int | None = None,
) -> str:
    return build_injection_block(
        cwd,
        tool=tool,
        prompt=prompt,
        file_paths=file_paths,
        max_tokens=max_tokens,
        session_id=session_id,
        injection_point=injection_point,
        parent_session_id=parent_session_id,
        command_text=command_text,
        expanded_ids=expanded_ids,
        context_window_tokens=context_window_tokens,
    )
