from __future__ import annotations

import json
import re
from pathlib import Path

from umx.budget import BudgetDecision, enforce_budget, estimate_tokens
from umx.config import load_config
from umx.conventions import summarize_conventions
from umx.memory import load_all_facts, read_fact_file, read_memory_md
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
    latest_referenced_turn,
    record_injection,
)
from umx.strength import relevance_score
from umx.tombstones import is_suppressed, load_tombstones


WORD_RE = re.compile(r"[a-zA-Z0-9_]+")


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
    target_slack = int(round(token_budget * 0.30)) if token_budget > 0 else 0
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
) -> tuple[list[Fact], str]:
    root = find_project_root(cwd)
    project_repo = project_memory_dir(root)
    user_repo = user_memory_dir()
    cfg = load_config(config_path())
    keywords = _keywords(prompt, file_paths, extra_parts=[tool or "", command_text or ""])
    target_scope = Scope.FILE if file_paths else Scope.PROJECT
    tombstones = load_tombstones(project_repo)

    facts: list[Fact] = []
    if user_repo.exists():
        facts.extend(load_all_facts(user_repo, include_superseded=False))
    facts.extend(load_all_facts(project_repo, include_superseded=False))
    for scoped_path in _scoped_paths(project_repo, file_paths):
        facts.extend(read_fact_file(scoped_path, repo_dir=project_repo))

    filtered = [fact for fact in facts if not is_suppressed(fact, tombstones, phase="gather")]
    non_secret = [fact for fact in filtered if fact.superseded_by is None and fact.scope != Scope.PROJECT_SECRET]
    scored = {
        fact.fact_id: relevance_score(fact, target_scope=target_scope, keywords=keywords, config=cfg)
        for fact in non_secret
    }
    convention_summary = summarize_conventions(project_repo / "CONVENTIONS.md")
    return (
        sorted(
            non_secret,
            key=lambda fact: (scored.get(fact.fact_id, 0.0), fact.encoding_strength),
            reverse=True,
        ),
        convention_summary,
    )


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
    facts, convention_summary = collect_facts_for_injection(
        cwd,
        tool=tool,
        prompt=prompt,
        file_paths=file_paths,
        command_text=command_text,
    )
    if restrict_to_ids is not None:
        facts = [fact for fact in facts if fact.fact_id in restrict_to_ids]
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
            record_injection(
                repo_dir,
                procedure.procedure_id,
                session_id=session_id,
                turn_index=int(session_state["turn_index"]) if session_state else None,
                session_tokens=int(session_state["estimated_tokens"]) if session_state else None,
                injection_point=injection_point,
                disclosure_level="l2",
                tool=tool,
                parent_session_id=parent_session_id,
                token_count=_procedure_token_cost(procedure),
                item_kind="procedure",
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
        record_injection(
            repo_dir,
            fact.fact_id,
            session_id=session_id,
            turn_index=int(session_state["turn_index"]) if session_state else None,
            session_tokens=int(session_state["estimated_tokens"]) if session_state else None,
            injection_point="attention_refresh" if fact.fact_id in refresh_ids else injection_point,
            disclosure_level=disclosure_level,
            tool=tool,
            parent_session_id=parent_session_id,
            token_count=_fact_token_cost(fact, disclosure_level),
        )
        if fact.task_status in {TaskStatus.OPEN, TaskStatus.BLOCKED}:
            continue
        if fact.topic != current_topic:
            current_topic = fact.topic
            lines.append(f"### {current_topic}")
        lines.append(_render_fact(fact, disclosure_level))
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
) -> None:
    path = repo_dir / "meta" / "gaps.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "type": "gap",
        "query": query,
        "resolution_context": resolution_context,
        "proposed_fact": proposed_fact,
        "session": session,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


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
