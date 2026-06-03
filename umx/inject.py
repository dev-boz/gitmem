from __future__ import annotations

from dataclasses import dataclass
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from umx.artifacts import ReasoningArtifact, artifact_relative_path, load_reasoning_artifacts
from umx.budget import BudgetDecision, enforce_budget, estimate_tokens
from umx.chronicles import ContextLayer, infer_task_class, select_context_layers
from umx.config import load_config
from umx.conventions import summarize_conventions
from umx.memory import iter_fact_files, load_all_facts, read_fact_file, read_memory_md
from umx.models import Fact, Scope, TaskStatus
from umx.procedures import Procedure, load_all_procedures, match_procedures
from umx.skills import (
    Skill,
    SkillResolution,
    SkillRetrieval,
    load_all_skills,
    match_skills_by_name,
    match_skills_by_trigger,
    resolve_skill_with_attribution,
)
from umx.search_semantic import semantic_similarity_map
from umx.scope import (
    config_path,
    decode_scope_path,
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
    query_reasoning_artifacts,
    record_injections,
    record_skill_load,
    record_skill_retrievals,
)
from umx.strength import relevance_score
from umx.tombstones import is_suppressed, load_tombstones


WORD_RE = re.compile(r"[a-zA-Z0-9_]+")
_SKILL_REF_RE = re.compile(r"@skill:(?P<name>[A-Za-z0-9._-]+)")
_TRIGGER_ROUTE_BOOST = 2.0
_EXPLICIT_ROUTE_BOOST = 4.0
_SKILL_HINT_BUDGET_RATIO = 0.2
_SKILL_HINT_MAX_TOKENS = 160
_USER_SCOPES = {Scope.USER, Scope.TOOL, Scope.MACHINE}
_DEFAULT_TOOL_MAX_TOKENS = 4000
_TOOL_LIMIT_ALIASES = {
    "claude": "claude-code",
    "claude-cli": "claude-code",
    "codex-cli": "codex",
    "gemini-cli": "gemini",
    "github-copilot": "copilot",
    "opencode-cli": "opencode",
}
_SUBAGENT_CONTAMINATION_NOTICE = (
    "> contamination_risk: this handoff may include second-hand or externally derived memory. "
    "Treat facts as unverified until corroborated against raw sessions or code."
)
_GATHERED_FACT_CACHE: dict[
    tuple[Path, Path],
    tuple[
        tuple[tuple[str, int, int], ...],
        tuple[tuple[str, int, int], ...],
        tuple[Fact, ...],
        str,
    ],
] = {}


@dataclass(slots=True)
class ActivatedSkill:
    skill: Skill
    load_trigger: str
    route_boost: float


@dataclass(slots=True)
class ResolvedSkillActivation:
    activation: ActivatedSkill
    resolution: SkillResolution
    retrievals: list[SkillRetrieval]


@dataclass(slots=True)
class InjectionTrace:
    retrieval_fidelity: str
    source_path: str
    reason: str
    relevance_score: float


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


def _fact_terms(fact: Fact) -> set[str]:
    terms = {match.group(0).lower() for match in WORD_RE.finditer(fact.text)}
    terms.update(tag.lower() for tag in fact.tags if tag)
    return terms


def _fact_source_path(fact: Fact, project_repo: Path, user_repo: Path) -> str:
    if fact.scope == Scope.FILE:
        return f"files/{decode_scope_path(fact.topic)}.md"
    if fact.scope == Scope.FOLDER:
        return f"folders/{decode_scope_path(fact.topic)}.md"
    if fact.file_path is not None:
        resolved = fact.file_path.resolve()
        for base in (project_repo.resolve(), user_repo.resolve()):
            try:
                return str(resolved.relative_to(base))
            except ValueError:
                continue
        return fact.file_path.name
    return f"facts/topics/{fact.topic}.md"


def _render_retrieval_fidelity_comment(
    fact: Fact,
    trace: InjectionTrace | None,
) -> str | None:
    if trace is None:
        return None
    return (
        f"<!-- retrieval_fidelity: {trace.retrieval_fidelity} "
        f"source: {trace.source_path} strength: {fact.encoding_strength} -->"
    )


def _default_trace_for_fact(fact: Fact) -> InjectionTrace:
    return InjectionTrace(
        retrieval_fidelity="fallback",
        source_path=(
            f"files/{decode_scope_path(fact.topic)}.md"
            if fact.scope == Scope.FILE
            else (
                f"folders/{decode_scope_path(fact.topic)}.md"
                if fact.scope == Scope.FOLDER
                else f"facts/topics/{fact.topic}.md"
            )
        ),
        reason=f"scope:{fact.scope.value}",
        relevance_score=0.0,
    )


def _build_injection_traces(
    facts: list[Fact],
    *,
    keywords: set[str],
    semantic_scores: dict[str, float],
    scoped_fact_ids: set[str],
    routed_retrievals: dict[str, list[str]],
    refresh_ids: set[str],
    tool: str | None,
    restrict_to_ids: set[str] | None,
    handoff_fact_ids: set[str] | None,
    relevance_scores: dict[str, float],
    project_repo: Path,
    user_repo: Path,
) -> dict[str, InjectionTrace]:
    exact_ids = set(scoped_fact_ids)
    exact_ids.update(routed_retrievals)
    if restrict_to_ids:
        exact_ids.update(restrict_to_ids)
    if handoff_fact_ids:
        exact_ids.update(handoff_fact_ids)
    traces: dict[str, InjectionTrace] = {}
    normalized_tool = tool.strip().casefold() if tool else None
    for fact in facts:
        matched_keywords = sorted(keywords & _fact_terms(fact))
        semantic_score = semantic_scores.get(fact.fact_id, 0.0)
        if fact.fact_id in exact_ids:
            fidelity = "exact"
        elif matched_keywords:
            fidelity = "lexical"
        elif semantic_score > 0:
            fidelity = "semantic"
        else:
            fidelity = "fallback"
        reason_parts: list[str] = []
        seen_parts: set[str] = set()

        def add_reason(part: str | None) -> None:
            if part and part not in seen_parts:
                seen_parts.add(part)
                reason_parts.append(part)

        add_reason(f"tool:{normalized_tool}" if normalized_tool else None)
        if fact.fact_id in scoped_fact_ids and fact.scope in {Scope.FILE, Scope.FOLDER}:
            scoped_path = decode_scope_path(fact.topic)
            add_reason(f"path:{scoped_path}" if scoped_path else None)
        for directive_value in routed_retrievals.get(fact.fact_id, [])[:2]:
            add_reason(f"load:{directive_value}")
        for token in matched_keywords[:3]:
            add_reason(f"keyword:{token}")
        if fact.fact_id in refresh_ids:
            add_reason("refresh:attention")
        if fidelity == "semantic":
            add_reason("semantic:embedding")
        if fact.task_status in {TaskStatus.OPEN, TaskStatus.BLOCKED}:
            add_reason(f"task:{fact.task_status.value}")
        add_reason(f"scope:{fact.scope.value}")
        traces[fact.fact_id] = InjectionTrace(
            retrieval_fidelity=fidelity,
            source_path=_fact_source_path(fact, project_repo, user_repo),
            reason=",".join(reason_parts),
            relevance_score=float(relevance_scores.get(fact.fact_id, 0.0)),
        )
    return traces


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


def _fact_token_cost(
    fact: Fact,
    disclosure_level: str,
    trace: InjectionTrace | None = None,
) -> int:
    effective_trace = trace or _default_trace_for_fact(fact)
    lines = [_render_fact(fact, disclosure_level)]
    comment = _render_retrieval_fidelity_comment(fact, effective_trace)
    if comment:
        lines.insert(0, comment)
    return estimate_tokens("\n".join(lines))


def _render_procedure(procedure: Procedure) -> list[str]:
    lines = [f"### {procedure.title}", procedure.steps_markdown.strip()]
    return [line for line in lines if line]


def _procedure_token_cost(procedure: Procedure) -> int:
    return estimate_tokens("\n".join(_render_procedure(procedure)))


def _artifact_terms(artifact: ReasoningArtifact) -> set[str]:
    text = " ".join([artifact.conclusion, *artifact.evidence])
    return {
        match.group(0).lower()
        for match in WORD_RE.finditer(text)
        if len(match.group(0)) > 2
    }


def _artifact_score(artifact: ReasoningArtifact, keywords: set[str]) -> float:
    terms = _artifact_terms(artifact)
    if not terms or not keywords:
        return 0.0
    overlap = terms & keywords
    if not overlap:
        return 0.0
    return len(overlap) / max(1, len(keywords)) + float(artifact.confidence)


def _render_reasoning_artifact(artifact: ReasoningArtifact, repo_dir: Path) -> list[str]:
    lines = [
        f"### {artifact.artifact_id}",
        f"- Conclusion: {artifact.conclusion}",
    ]
    if artifact.evidence:
        lines.append("- Evidence:")
        lines.extend(f"  - {item}" for item in artifact.evidence[:5])
    lines.append(
        f"- Source: {artifact_relative_path(repo_dir, artifact)} "
        f"[confidence:{artifact.confidence:.2f}]"
    )
    return lines


def _artifact_token_cost(artifact: ReasoningArtifact, repo_dir: Path) -> int:
    return estimate_tokens("\n".join(_render_reasoning_artifact(artifact, repo_dir)))


def _select_reasoning_artifacts(
    repo_dir: Path,
    *,
    keywords: set[str],
    budget: int,
    max_items: int = 3,
) -> tuple[list[ReasoningArtifact], int]:
    if budget <= 0 or not keywords:
        return [], 0
    artifacts_by_id = {
        artifact.artifact_id: artifact
        for artifact in load_reasoning_artifacts(repo_dir, active_only=True)
    }
    if not artifacts_by_id:
        return [], 0
    # Spec §3b: reasoning artifacts are indexed in SQLite and injected on
    # conclusion/evidence match. Use the FTS index to find candidates, falling
    # back to a full active-artifact scan if the index yields nothing (covers
    # tokenization-edge matches and unbuilt indexes).
    candidates: list[ReasoningArtifact] = []
    try:
        rows = query_reasoning_artifacts(
            repo_dir, _inject_candidate_query(keywords), active_only=True
        )
        candidates = [
            artifacts_by_id[str(row["id"])]
            for row in rows
            if str(row.get("id")) in artifacts_by_id
        ]
    except Exception:
        candidates = []
    if not candidates:
        candidates = list(artifacts_by_id.values())
    scored = [
        (score, artifact)
        for artifact in candidates
        if (score := _artifact_score(artifact, keywords)) > 0
    ]
    scored.sort(key=lambda item: (item[0], item[1].created_at or datetime.min.replace(tzinfo=UTC)), reverse=True)
    selected: list[ReasoningArtifact] = []
    used_tokens = 0
    for _, artifact in scored:
        if len(selected) >= max_items:
            break
        cost = _artifact_token_cost(artifact, repo_dir)
        if used_tokens + cost > budget:
            continue
        selected.append(artifact)
        used_tokens += cost
    return selected, used_tokens


def _render_context_layer(layer: ContextLayer, project_repo: Path) -> list[str]:
    try:
        source = layer.path.relative_to(project_repo).as_posix()
    except ValueError:
        source = layer.path.as_posix()
    return [
        f"### {layer.name}",
        f"<!-- context_layer: {layer.name} source: {source} -->",
        layer.content.strip(),
    ]


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
    traces: dict[str, InjectionTrace] | None = None,
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
        total_tokens += _fact_token_cost(fact, level, (traces or {}).get(fact.fact_id))
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
        trace = (traces or {}).get(fact.fact_id)
        total_tokens -= _fact_token_cost(fact, "l1", trace) - _fact_token_cost(fact, "l0", trace)
        levels[fact.fact_id] = "l0"
    return levels


def _enforce_rendered_budget(
    selected: list[Fact],
    disclosure_levels: dict[str, str],
    packing_scores: dict[str, float],
    *,
    fact_budget: int,
    always_ids: set[str],
    traces: dict[str, InjectionTrace] | None = None,
) -> tuple[list[Fact], dict[str, str]]:
    total = sum(
        _fact_token_cost(fact, disclosure_levels.get(fact.fact_id, "l1"), (traces or {}).get(fact.fact_id))
        for fact in selected
    )
    if total <= fact_budget:
        return selected, disclosure_levels
    mutable = sorted(
        [fact for fact in selected if fact.fact_id not in always_ids],
        key=lambda fact: packing_scores.get(fact.fact_id, 0.0),
    )
    for fact in mutable:
        trace = (traces or {}).get(fact.fact_id)
        current = disclosure_levels.get(fact.fact_id, "l1")
        if current == "l2":
            disclosure_levels[fact.fact_id] = "l1"
            total -= _fact_token_cost(fact, "l2", trace) - _fact_token_cost(fact, "l1", trace)
        if total <= fact_budget:
            return selected, disclosure_levels
        current = disclosure_levels.get(fact.fact_id, "l1")
        if current == "l1":
            disclosure_levels[fact.fact_id] = "l0"
            total -= _fact_token_cost(fact, "l1", trace) - _fact_token_cost(fact, "l0", trace)
        if total <= fact_budget:
            return selected, disclosure_levels
    while total > fact_budget and mutable:
        victim = mutable.pop(0)
        selected = [fact for fact in selected if fact.fact_id != victim.fact_id]
        total -= _fact_token_cost(
            victim,
            disclosure_levels.get(victim.fact_id, "l1"),
            (traces or {}).get(victim.fact_id),
        )
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




def _load_skills(project_repo: Path, user_repo: Path) -> list[Skill]:
    skills: list[Skill] = []
    if user_repo.exists():
        skills.extend(load_all_skills(user_repo))
    skills.extend(load_all_skills(project_repo))
    return skills


def _skill_repo(skill: Skill, project_repo: Path, user_repo: Path) -> Path:
    return user_repo if skill.scope == Scope.USER else project_repo


def _explicit_skill_names(prompt: str | None) -> list[str]:
    if not prompt:
        return []
    names: list[str] = []
    seen: set[str] = set()
    for match in _SKILL_REF_RE.finditer(prompt):
        name = match.group("name")
        if name in seen:
            continue
        seen.add(name)
        names.append(name)
    return names


def _activated_skills(
    skills: list[Skill],
    *,
    tool: str | None = None,
    prompt: str | None = None,
    file_paths: list[str] | None = None,
    command_text: str | None = None,
    config=None,
) -> list[ActivatedSkill]:
    if not bool(getattr(getattr(config, "skills", None), "enabled", True)):
        return []
    activated: dict[str, ActivatedSkill] = {}
    ordered_skill_ids: list[str] = []
    for name in _explicit_skill_names(prompt):
        skill = match_skills_by_name(skills, name, activatable_only=True)
        if skill is None:
            continue
        if skill.skill_id not in activated:
            ordered_skill_ids.append(skill.skill_id)
        activated[skill.skill_id] = ActivatedSkill(
            skill=skill,
            load_trigger="explicit",
            route_boost=_EXPLICIT_ROUTE_BOOST,
        )
    for skill in match_skills_by_trigger(
        skills,
        tool=tool,
        prompt=prompt,
        file_paths=file_paths,
        command_text=command_text,
    ):
        if skill.skill_id in activated:
            continue
        activated[skill.skill_id] = ActivatedSkill(
            skill=skill,
            load_trigger="trigger",
            route_boost=_TRIGGER_ROUTE_BOOST,
        )
        ordered_skill_ids.append(skill.skill_id)
    max_skills = int(getattr(getattr(config, "skills", None), "max_concurrent_skills", 0) or 0)
    if max_skills > 0:
        ordered_skill_ids = ordered_skill_ids[:max_skills]
    return [activated[skill_id] for skill_id in ordered_skill_ids]


def _filter_skill_resolution(
    resolution: SkillResolution,
    retrievals: list[SkillRetrieval],
    fact_index: dict[str, Fact],
    restrict_to_ids: set[str] | None,
) -> tuple[SkillResolution, list[SkillRetrieval]]:
    routed_fact_ids = {fact_id for fact_id in resolution.routed_fact_ids if fact_id in fact_index}
    if restrict_to_ids is not None:
        routed_fact_ids &= restrict_to_ids
    filtered_retrievals = [item for item in retrievals if item.fact_id in routed_fact_ids]
    return (
        SkillResolution(
            skill=resolution.skill,
            routed_fact_ids=routed_fact_ids,
            hints=list(resolution.hints),
            directives_resolved=resolution.directives_resolved,
            missing_paths=list(resolution.missing_paths),
            blocked_paths=list(resolution.blocked_paths),
            unsupported_directives=list(resolution.unsupported_directives),
        ),
        filtered_retrievals,
    )


def _resolve_activated_skills(
    activations: list[ActivatedSkill],
    *,
    project_repo: Path,
    user_repo: Path,
    fact_index: dict[str, Fact],
    config,
    restrict_to_ids: set[str] | None,
) -> list[ResolvedSkillActivation]:
    resolved: list[ResolvedSkillActivation] = []
    for activation in activations:
        resolution, retrievals = resolve_skill_with_attribution(
            activation.skill,
            _skill_repo(activation.skill, project_repo, user_repo),
            config=config,
        )
        filtered_resolution, filtered_retrievals = _filter_skill_resolution(
            resolution,
            retrievals,
            fact_index,
            restrict_to_ids,
        )
        resolved.append(
            ResolvedSkillActivation(
                activation=activation,
                resolution=filtered_resolution,
                retrievals=filtered_retrievals,
            )
        )
    return resolved


def _select_skill_hints(
    resolved_skills: list[ResolvedSkillActivation],
    *,
    budget: int,
) -> tuple[list[str], int, dict[str, int]]:
    selected: list[str] = []
    selected_tokens = 0
    tokens_by_skill: dict[str, int] = {}
    seen_hints: set[str] = set()
    for resolved in resolved_skills:
        skill_tokens = 0
        for hint in resolved.resolution.hints:
            if hint in seen_hints:
                continue
            cost = estimate_tokens(f"- {hint}")
            if selected_tokens + cost > budget:
                continue
            seen_hints.add(hint)
            selected.append(hint)
            selected_tokens += cost
            skill_tokens += cost
        if skill_tokens > 0:
            tokens_by_skill[resolved.activation.skill.skill_id] = skill_tokens
    return selected, selected_tokens, tokens_by_skill


def _selected_skill_fact_tokens(
    selected: list[Fact],
    disclosure_levels: dict[str, str],
    resolved_skills: list[ResolvedSkillActivation],
    traces: dict[str, InjectionTrace] | None = None,
) -> tuple[dict[str, int], dict[str, set[str]]]:
    selected_by_id = {fact.fact_id: fact for fact in selected}
    tokens_by_skill: dict[str, int] = {}
    selected_fact_ids_by_skill: dict[str, set[str]] = {}
    for resolved in resolved_skills:
        skill_id = resolved.activation.skill.skill_id
        skill_selected_ids = {
            fact_id for fact_id in resolved.resolution.routed_fact_ids if fact_id in selected_by_id
        }
        selected_fact_ids_by_skill[skill_id] = skill_selected_ids
        tokens = 0
        for fact_id in skill_selected_ids:
            fact = selected_by_id[fact_id]
            if fact.task_status in {TaskStatus.OPEN, TaskStatus.BLOCKED}:
                tokens += estimate_tokens(f"- {fact.text}")
                continue
            tokens += _fact_token_cost(fact, disclosure_levels.get(fact_id, "l1"), (traces or {}).get(fact_id))
        tokens_by_skill[skill_id] = tokens
    return tokens_by_skill, selected_fact_ids_by_skill

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
    routed_fact_ids: set[str],
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
            or fact.fact_id in routed_fact_ids
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
    task_class = infer_task_class(tool, prompt, command_text, " ".join(file_paths or []))
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
    resolved_skills = _resolve_activated_skills(
        _activated_skills(
            _load_skills(project_repo, user_repo),
            tool=tool,
            prompt=prompt,
            file_paths=file_paths,
            command_text=command_text,
            config=cfg,
        ),
        project_repo=project_repo,
        user_repo=user_repo,
        fact_index={fact.fact_id: fact for fact in facts},
        config=cfg,
        restrict_to_ids=restrict_to_ids,
    )
    routed_fact_ids = {
        fact_id
        for resolved in resolved_skills
        for fact_id in resolved.resolution.routed_fact_ids
    }
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
        routed_fact_ids=routed_fact_ids,
        restrict_to_ids=restrict_to_ids,
    )
    candidate_fact_index = {fact.fact_id: fact for fact in facts}
    refiltered_skills: list[ResolvedSkillActivation] = []
    for resolved in resolved_skills:
        filtered_resolution, filtered_retrievals = _filter_skill_resolution(
            resolved.resolution,
            resolved.retrievals,
            candidate_fact_index,
            None,
        )
        refiltered_skills.append(
            ResolvedSkillActivation(
                activation=resolved.activation,
                resolution=filtered_resolution,
                retrievals=filtered_retrievals,
            )
        )
    resolved_skills = refiltered_skills
    route_boosts: dict[str, float] = {}
    routed_retrievals: dict[str, list[str]] = {}
    for resolved in resolved_skills:
        for fact_id in resolved.resolution.routed_fact_ids:
            route_boosts[fact_id] = max(
                route_boosts.get(fact_id, 0.0),
                resolved.activation.route_boost,
            )
        for retrieval in resolved.retrievals:
            routed_retrievals.setdefault(retrieval.fact_id, []).append(
                retrieval.directive_value
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
        route_bonus = route_boosts.get(fact.fact_id, 0.0)
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
            + route_bonus
        )
    always = {
        fact.fact_id
        for fact in facts
        if (fact.scope == Scope.USER and fact.encoding_strength >= 4)
        or fact.task_status in {TaskStatus.OPEN, TaskStatus.BLOCKED}
        or (handoff_fact_ids and fact.fact_id in handoff_fact_ids)
    }
    traces = _build_injection_traces(
        facts,
        keywords=keywords,
        semantic_scores=semantic_scores,
        scoped_fact_ids=scoped_fact_ids,
        routed_retrievals=routed_retrievals,
        refresh_ids=refresh_ids,
        tool=tool,
        restrict_to_ids=restrict_to_ids,
        handoff_fact_ids=handoff_fact_ids,
        relevance_scores=scored,
        project_repo=project_repo,
        user_repo=user_repo,
    )
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
    remaining_after_sections = max(0, max_tokens - reserved_tokens - procedure_tokens - hot_summary_cost)
    selected_layers = select_context_layers(
        project_repo,
        task_class=task_class,
        budget=remaining_after_sections,
    )
    layer_tokens = sum(layer.token_count for layer in selected_layers)
    remaining_after_sections = max(0, remaining_after_sections - layer_tokens)
    selected_artifacts, artifact_tokens = _select_reasoning_artifacts(
        project_repo,
        keywords=keywords,
        budget=remaining_after_sections,
    )
    remaining_after_sections = max(0, remaining_after_sections - artifact_tokens)
    hint_budget = min(
        _SKILL_HINT_MAX_TOKENS,
        int(remaining_after_sections * _SKILL_HINT_BUDGET_RATIO),
    )
    selected_skill_hints, hint_tokens, hint_tokens_by_skill = _select_skill_hints(
        resolved_skills,
        budget=max(0, hint_budget),
    )
    remaining_budget = max(1, max_tokens - reserved_tokens - procedure_tokens - hot_summary_cost - hint_tokens)
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
        traces=traces,
    )
    selected, disclosure_levels = _enforce_rendered_budget(
        selected,
        disclosure_levels,
        budget_decision.packing_scores,
        fact_budget=remaining_budget,
        always_ids=always,
        traces=traces,
    )
    selected_skill_fact_tokens, selected_fact_ids_by_skill = _selected_skill_fact_tokens(
        selected,
        disclosure_levels,
        resolved_skills,
        traces=traces,
    )

    lines = ["# UMX Memory"]
    if budget_decision.warning:
        lines.extend(["", f"> Warning: {budget_decision.warning}"])
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
    if selected_layers:
        lines.append("")
        lines.append("## Context Layers")
        for layer in selected_layers:
            lines.extend(_render_context_layer(layer, project_repo))
            pending_injections.setdefault(project_repo, []).append(
                {
                    "fact_id": f"context_layer:{layer.path.parent.name}/{layer.name}",
                    "session_id": session_id,
                    "turn_index": int(session_state["turn_index"]) if session_state else None,
                    "session_tokens": int(session_state["estimated_tokens"]) if session_state else None,
                    "injection_point": injection_point,
                    "disclosure_level": "digest" if layer.name == "digest" else "l2",
                    "tool": tool,
                    "parent_session_id": parent_session_id,
                    "token_count": layer.token_count,
                    "item_kind": "context_layer",
                }
            )
    if selected_artifacts:
        lines.append("")
        lines.append("## Reasoning Artifacts")
        for artifact in selected_artifacts:
            lines.extend(_render_reasoning_artifact(artifact, project_repo))
            pending_injections.setdefault(project_repo, []).append(
                {
                    "fact_id": f"artifact:{artifact.artifact_id}",
                    "session_id": session_id,
                    "turn_index": int(session_state["turn_index"]) if session_state else None,
                    "session_tokens": int(session_state["estimated_tokens"]) if session_state else None,
                    "injection_point": injection_point,
                    "disclosure_level": "l2",
                    "tool": tool,
                    "parent_session_id": parent_session_id,
                    "token_count": _artifact_token_cost(artifact, project_repo),
                    "item_kind": "reasoning_artifact",
                    "reason": "keyword:reasoning-artifact",
                    "source_path": artifact_relative_path(project_repo, artifact),
                }
            )
    if selected_skill_hints:
        lines.append("")
        lines.append("## Skill Hints")
        lines.extend(f"- {hint}" for hint in selected_skill_hints)
    open_task_facts = [
        fact
        for fact in selected
        if fact.task_status in {TaskStatus.OPEN, TaskStatus.BLOCKED}
    ]
    if open_task_facts:
        lines.extend(["", "## Open Tasks"])
        for fact in open_task_facts:
            comment = _render_retrieval_fidelity_comment(
                fact,
                traces.get(fact.fact_id),
            )
            if comment:
                lines.append(comment)
            lines.append(f"- {fact.text}")
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
                "token_count": _fact_token_cost(
                    fact,
                    disclosure_level,
                    traces.get(fact.fact_id),
                ),
                "reason": traces.get(fact.fact_id).reason if traces.get(fact.fact_id) else None,
                "relevance_score": (
                    traces.get(fact.fact_id).relevance_score
                    if traces.get(fact.fact_id)
                    else None
                ),
                "retrieval_fidelity": (
                    traces.get(fact.fact_id).retrieval_fidelity
                    if traces.get(fact.fact_id)
                    else None
                ),
                "source_path": (
                    traces.get(fact.fact_id).source_path
                    if traces.get(fact.fact_id)
                    else None
                ),
            }
        )
        if fact.task_status in {TaskStatus.OPEN, TaskStatus.BLOCKED}:
            continue
        if fact.topic != current_topic:
            current_topic = fact.topic
            lines.append(f"### {current_topic}")
        comment = _render_retrieval_fidelity_comment(
            fact,
            traces.get(fact.fact_id),
        )
        if comment:
            lines.append(comment)
        lines.append(_render_fact(fact, disclosure_level))
    for repo_dir, events in pending_injections.items():
        record_injections(repo_dir, events)
    if session_id:
        for resolved in resolved_skills:
            skill = resolved.activation.skill
            skill_id = skill.skill_id
            repo_dir = _skill_repo(skill, project_repo, user_repo)
            load_id = record_skill_load(
                repo_dir,
                skill_id=skill.skill_id,
                skill_name=skill.name,
                version=skill.version,
                session_id=session_id,
                load_trigger=resolved.activation.load_trigger,
                directives_resolved=resolved.resolution.directives_resolved,
                facts_retrieved=len(resolved.resolution.routed_fact_ids),
                tokens_used=hint_tokens_by_skill.get(skill_id, 0) + selected_skill_fact_tokens.get(skill_id, 0),
            )
            if resolved.retrievals:
                record_skill_retrievals(
                    repo_dir,
                    load_id,
                    resolved.retrievals,
                    selected_fact_ids=selected_fact_ids_by_skill.get(skill_id, set()),
                )
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
    block = build_injection_block(
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
    lines = block.rstrip("\n").splitlines()
    if lines and lines[0] == "# UMX Memory":
        return "\n".join([lines[0], "", _SUBAGENT_CONTAMINATION_NOTICE, *lines[1:]]) + "\n"
    return f"{_SUBAGENT_CONTAMINATION_NOTICE}\n\n{block}"


def inferred_max_tokens_for_tool(tool: str | None, *, config=None) -> int:
    cfg = config or load_config(config_path())
    if not tool:
        return _DEFAULT_TOOL_MAX_TOKENS
    normalized = tool.strip().casefold().replace("_", "-")
    canonical = _TOOL_LIMIT_ALIASES.get(normalized, normalized)
    configured = cfg.inject.tool_max_tokens
    return int(configured.get(canonical, configured.get(normalized, _DEFAULT_TOOL_MAX_TOKENS)))


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
    max_tokens: int | None = None,
    session_id: str | None = None,
    injection_point: str = "prompt",
    parent_session_id: str | None = None,
    command_text: str | None = None,
    expanded_ids: set[str] | None = None,
    context_window_tokens: int | None = None,
) -> str:
    cfg = load_config(config_path())
    return build_injection_block(
        cwd,
        tool=tool,
        prompt=prompt,
        file_paths=file_paths,
        max_tokens=max_tokens if max_tokens is not None else inferred_max_tokens_for_tool(tool, config=cfg),
        session_id=session_id,
        injection_point=injection_point,
        parent_session_id=parent_session_id,
        command_text=command_text,
        expanded_ids=expanded_ids,
        context_window_tokens=context_window_tokens,
    )
