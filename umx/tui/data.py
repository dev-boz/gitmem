from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Literal

from umx.calibration import build_calibration_advice
from umx.config import load_config
from umx.dream.gates import read_dream_state
from umx.dream.lint import read_last_lint
from umx.dream.processing import read_processing_log, summarize_processing_log
from umx.fact_actions import merge_conflicts_action
from umx.governance_health import build_governance_health_payload
from umx.metrics import compute_metrics, health_flags
from umx.memory import find_fact_by_id, load_all_facts
from umx.models import ConsolidationStatus, Fact, Scope, SourceType, parse_datetime
from umx.scope import config_path, discover_project_slug, project_memory_dir, user_memory_dir
from umx.search import query_index, search_sessions, session_replay
from umx.sessions import iter_session_payloads, list_quarantined_sessions
from umx.status import build_status_payload
from umx.supersession import walk_history
from umx.tombstones import load_tombstones

PageName = Literal["overview", "facts", "sessions", "dream", "prs", "search"]
Tone = Literal["ok", "warn", "bad", "idle"]

NAV_ITEMS: tuple[tuple[str, PageName, str], ...] = (
    ("F2", "overview", "Overview"),
    ("F3", "facts", "Facts"),
    ("F4", "sessions", "Sessions"),
    ("F5", "dream", "Dream"),
    ("F6", "prs", "PRs"),
    ("F7", "search", "Search"),
)

SUBNAV_ITEMS: dict[PageName, tuple[tuple[str, str], ...]] = {
    "overview": (
        ("health", "health"),
        ("activity 24h", "activity 24h"),
        ("activity 7d", "activity 7d"),
        ("activity 30d", "activity 30d"),
    ),
    "facts": (
        ("all", "all"),
        ("conflicted", "conflicted"),
        ("tombstoned", "tombstoned"),
        ("superseded", "superseded"),
        ("procedures", "procedures"),
    ),
    "sessions": (
        ("all", "all"),
        ("claude", "claude"),
        ("codex", "codex"),
        ("copilot", "copilot"),
        ("gemini", "gemini"),
        ("opencode", "opencode"),
        ("live", "live"),
    ),
    "dream": (
        ("live", "live"),
        ("history", "history"),
        ("schedule", "schedule"),
        ("config", "config"),
    ),
    "prs": (
        ("pending", "pending"),
        ("conflicts", "conflicts"),
        ("merged", "merged"),
        ("closed", "closed"),
        ("all", "all"),
    ),
    "search": (
        ("all", "all"),
        ("facts", "facts"),
        ("sessions", "sessions"),
    ),
}


@dataclass(slots=True)
class HeroSnapshot:
    state: Tone
    glyph: str
    title: str
    subtitle: str
    conflicts: int
    pending_prs: int


@dataclass(slots=True)
class FactSummary:
    fact: Fact
    repo_kind: Literal["project", "user"]
    age: str
    location: str
    scope_label: str
    source_label: str

    @property
    def title(self) -> str:
        return self.fact.text.strip() or self.fact.topic

    @property
    def has_conflict(self) -> bool:
        return bool(self.fact.conflicts_with)

    @property
    def stable(self) -> bool:
        return self.fact.consolidation_status == ConsolidationStatus.STABLE


@dataclass(slots=True)
class SessionSummary:
    session_id: str
    repo_kind: Literal["project", "user"]
    tool: str
    source_label: str
    status: Tone
    age: str
    summary: str
    started: datetime | None
    transcript: list[dict[str, Any]]
    usage_events: list[dict[str, Any]]
    proposed_fact_ids: list[str]
    retrieved_fact_ids: list[str]
    sparkline: list[float]


@dataclass(slots=True)
class ConflictSummary:
    winner: FactSummary | None
    loser: FactSummary | None
    reason: str

    @property
    def title(self) -> str:
        if self.winner is None or self.loser is None:
            return "conflict preview"
        return f"{self.winner.fact.topic or 'fact'} conflict"


@dataclass(slots=True)
class SearchResult:
    kind: Literal["fact", "session"]
    title: str
    subtitle: str
    snippet: str
    score: float
    fact: FactSummary | None = None
    session: SessionSummary | None = None


@dataclass(slots=True)
class GitmemTUISnapshot:
    cwd: Path
    repo: Path
    user_repo: Path
    slug: str
    version: str
    mode: str
    status_payload: dict[str, Any]
    governance_payload: dict[str, Any]
    metrics: dict[str, dict[str, Any]]
    guidance: list[dict[str, Any]]
    hero: HeroSnapshot
    facts: list[FactSummary]
    superseded_facts: list[FactSummary]
    tombstones: list[Any]
    sessions: list[SessionSummary]
    conflicts: list[ConflictSummary]
    quarantine: list[Any]
    processing_summary: dict[str, Any]
    processing_records: list[dict[str, Any]]
    dream_state: dict[str, Any]
    activity_by_tool: dict[str, list[float]]
    activity_totals: dict[str, int]


def build_tui_snapshot(
    cwd: Path,
    *,
    governance_payload: dict[str, Any] | None = None,
) -> GitmemTUISnapshot:
    cwd = cwd.resolve()
    repo = project_memory_dir(cwd)
    user_repo = user_memory_dir()
    cfg = load_config(config_path())
    status_payload = build_status_payload(cwd)
    governance_payload = governance_payload or build_governance_health_payload(cwd, cfg)
    metrics = compute_metrics(repo, cfg)
    flags = health_flags(metrics)
    guidance = build_calibration_advice(metrics, flags)
    facts = _load_fact_summaries(repo, user_repo)
    active_facts = [fact for fact in facts if fact.fact.superseded_by is None]
    superseded_facts = [fact for fact in facts if fact.fact.superseded_by is not None]
    tombstones = [item for item in load_tombstones(repo) if not item.expired()] if repo.exists() else []
    sessions = _load_sessions(repo, user_repo, active_facts)
    processing_records = read_processing_log(repo)
    processing_summary = summarize_processing_log(repo, refs=("origin/main",))
    dream_state = read_dream_state(repo)
    conflicts = _load_conflict_summaries(cwd, active_facts)
    quarantine = list_quarantined_sessions(repo, config=cfg) if repo.exists() else []
    hero = _build_hero(status_payload, governance_payload, conflicts)
    activity_by_tool, activity_totals = _build_tool_activity(sessions)
    return GitmemTUISnapshot(
        cwd=cwd,
        repo=repo,
        user_repo=user_repo,
        slug=discover_project_slug(cwd),
        version=_package_version(),
        mode=str(governance_payload.get("mode") or cfg.dream.mode or "local"),
        status_payload=status_payload,
        governance_payload=governance_payload,
        metrics=metrics,
        guidance=guidance,
        hero=hero,
        facts=active_facts,
        superseded_facts=superseded_facts,
        tombstones=tombstones,
        sessions=sessions,
        conflicts=conflicts,
        quarantine=quarantine,
        processing_summary=processing_summary,
        processing_records=processing_records,
        dream_state=dream_state,
        activity_by_tool=activity_by_tool,
        activity_totals=activity_totals,
    )


def fact_history(snapshot: GitmemTUISnapshot, fact: Fact) -> list[Fact]:
    repo = _fact_repo(snapshot, fact)
    return walk_history(repo, fact.fact_id) if repo.exists() else []


def fact_score_components(fact: Fact) -> tuple[float, float, float, float]:
    trust = max(0.2, min(1.0, fact.encoding_strength / 5))
    if fact.source_type == SourceType.GROUND_TRUTH_CODE:
        trust = max(trust, 0.92)
    elif fact.source_type == SourceType.USER_PROMPT:
        trust = max(trust, 0.78)
    relevance = min(1.0, 0.55 + (0.15 if fact.scope in {Scope.PROJECT, Scope.USER} else 0.05) + fact.confidence * 0.2)
    retention = 0.85 if fact.consolidation_status == ConsolidationStatus.STABLE else 0.6
    if fact.last_referenced is not None:
        retention = min(1.0, retention + 0.08)
    total = round((trust + relevance + retention) / 3, 2)
    return round(trust, 2), round(relevance, 2), round(retention, 2), total


def facts_by_strength(facts: list[FactSummary]) -> dict[int, int]:
    counts = {level: 0 for level in range(1, 6)}
    for item in facts:
        counts[item.fact.encoding_strength] = counts.get(item.fact.encoding_strength, 0) + 1
    return counts


def top_scope(facts: list[FactSummary]) -> tuple[str, int]:
    counts = Counter(item.fact.scope.value for item in facts)
    if not counts:
        return ("n/a", 0)
    label, count = counts.most_common(1)[0]
    return label, count


def filter_facts(snapshot: GitmemTUISnapshot, query: str) -> list[FactSummary]:
    text = query.strip().lower()
    if not text:
        return snapshot.facts
    return [
        item
        for item in snapshot.facts
        if text in item.fact.text.lower()
        or text in item.fact.topic.lower()
        or text in item.scope_label.lower()
        or text in item.source_label.lower()
        or text in item.location.lower()
    ]


def current_search_results(snapshot: GitmemTUISnapshot, query: str, limit: int = 12) -> list[SearchResult]:
    if not query.strip():
        return [
            SearchResult(
                kind="fact",
                title=item.title,
                subtitle=f"{item.scope_label} · {item.age}",
                snippet=item.fact.text,
                score=1.0,
                fact=item,
            )
            for item in snapshot.facts[:limit]
        ]

    results: list[SearchResult] = []
    seen_fact_ids: set[str] = set()
    for repo_kind, repo_dir in (("project", snapshot.repo), ("user", snapshot.user_repo)):
        if not repo_dir.exists():
            continue
        for fact in query_index(repo_dir, query, limit=limit):
            if fact.fact_id in seen_fact_ids:
                continue
            summary = _fact_to_summary(fact, repo_kind)
            results.append(
                SearchResult(
                    kind="fact",
                    title=summary.title,
                    subtitle=f"{summary.scope_label} · {summary.age}",
                    snippet=summary.fact.text,
                    score=1.0,
                    fact=summary,
                )
            )
            seen_fact_ids.add(fact.fact_id)
    if not results:
        text = query.strip().lower()
        for item in snapshot.facts:
            if item.fact.fact_id in seen_fact_ids:
                continue
            if text in item.fact.text.lower() or text in item.fact.topic.lower():
                results.append(
                    SearchResult(
                        kind="fact",
                        title=item.title,
                        subtitle=f"{item.scope_label} · {item.age}",
                        snippet=item.fact.text,
                        score=0.5,
                        fact=item,
                    )
                )
                seen_fact_ids.add(item.fact.fact_id)
                if len(results) >= limit:
                    break
    session_hits: list[SearchResult] = []
    for repo_kind, repo_dir in (("project", snapshot.repo), ("user", snapshot.user_repo)):
        if not repo_dir.exists():
            continue
        summaries = {item.session_id: item for item in snapshot.sessions if item.repo_kind == repo_kind}
        for row in search_sessions(repo_dir, query, limit=limit):
            session = summaries.get(str(row["session_id"]))
            title = session.summary if session is not None else str(row["session_id"])
            subtitle = f"{session.tool if session else 'session'} · {session.age if session else ''}".strip()
            session_hits.append(
                SearchResult(
                    kind="session",
                    title=title,
                    subtitle=subtitle,
                    snippet=str(row["content_snippet"]),
                    score=float(row["score"]),
                    session=session,
                )
            )
    session_hits.sort(key=lambda item: item.score, reverse=True)
    results.extend(session_hits[:limit])
    return results[:limit]


def most_recent_runs(snapshot: GitmemTUISnapshot, limit: int = 7) -> list[dict[str, Any]]:
    completed = [row for row in snapshot.processing_records if row.get("event") == "completed"]
    completed.sort(key=lambda row: parse_datetime(str(row.get("ts") or "")) or datetime.min.replace(tzinfo=UTC), reverse=True)
    return completed[:limit]


def run_throughput_series(snapshot: GitmemTUISnapshot, limit: int = 10) -> list[float]:
    completed = list(reversed(most_recent_runs(snapshot, limit=limit)))
    if not completed:
        return [0.0] * limit
    peak = max(int(item.get("added") or 0) for item in completed) or 1
    values = [min(1.0, int(item.get("added") or 0) / peak) for item in completed]
    return _pad_left(values, limit=limit)


def selected_fact(snapshot: GitmemTUISnapshot, index: int, query: str) -> FactSummary | None:
    facts = filter_facts(snapshot, query)
    if not facts:
        return None
    return facts[max(0, min(index, len(facts) - 1))]


def selected_session(snapshot: GitmemTUISnapshot, index: int) -> SessionSummary | None:
    if not snapshot.sessions:
        return None
    return snapshot.sessions[max(0, min(index, len(snapshot.sessions) - 1))]


def selected_pr(snapshot: GitmemTUISnapshot, index: int) -> dict[str, Any] | None:
    prs = list(snapshot.governance_payload.get("open_prs") or [])
    if not prs:
        return None
    return prs[max(0, min(index, len(prs) - 1))]


def selected_search_result(snapshot: GitmemTUISnapshot, index: int, query: str) -> SearchResult | None:
    results = current_search_results(snapshot, query)
    if not results:
        return None
    return results[max(0, min(index, len(results) - 1))]


def subnav_counts(snapshot: GitmemTUISnapshot, page: PageName) -> dict[str, int]:
    if page == "facts":
        procedures = sum(1 for item in snapshot.facts if (item.fact.task_status or "").strip())
        conflicted = sum(1 for item in snapshot.facts if item.has_conflict)
        return {
            "all": len(snapshot.facts),
            "conflicted": conflicted,
            "tombstoned": len(snapshot.tombstones),
            "superseded": len(snapshot.superseded_facts),
            "procedures": procedures,
        }
    if page == "sessions":
        counts = Counter(item.tool.partition("-")[0] or item.tool for item in snapshot.sessions)
        return {
            "all": len(snapshot.sessions),
            "codex": counts.get("codex", 0),
            "claude": counts.get("claude", 0) + counts.get("claude-code", 0),
            "copilot": counts.get("copilot", 0),
            "gemini": counts.get("gemini", 0) + counts.get("gemini-cli", 0),
            "opencode": counts.get("opencode", 0),
            "live": sum(1 for item in snapshot.sessions if item.status == "warn"),
        }
    if page == "dream":
        return {"history": len(most_recent_runs(snapshot))}
    if page == "prs":
        open_prs = list(snapshot.governance_payload.get("open_prs") or [])
        return {
            "pending": len(open_prs),
            "conflicts": len(snapshot.conflicts),
            "merged": 0,
            "closed": 0,
            "all": len(open_prs),
        }
    if page == "search":
        return {"all": len(snapshot.facts) + len(snapshot.sessions), "facts": len(snapshot.facts), "sessions": len(snapshot.sessions)}
    return {}


def session_stats(session: SessionSummary) -> dict[str, int]:
    files_read = sum(1 for event in session.transcript if "file" in str(event.get("tool_name", "")).lower())
    files_edited = sum(1 for event in session.transcript if "edit" in str(event.get("tool_name", "")).lower())
    return {
        "events": len(session.transcript),
        "files_read": files_read,
        "files_edited": files_edited,
        "retrieved": len(session.retrieved_fact_ids),
    }


def next_dream_run_text(snapshot: GitmemTUISnapshot) -> str:
    last = parse_datetime(str(snapshot.dream_state.get("last_dream") or "")) if snapshot.dream_state.get("last_dream") else None
    if last is None:
        return "now"
    due = last + timedelta(hours=24)
    now = datetime.now(tz=UTC)
    if due <= now:
        return "due now"
    return relative_age(due, future=True)


def _package_version() -> str:
    try:
        return version("umx")
    except PackageNotFoundError:
        return "dev"


def _build_hero(
    status_payload: dict[str, Any],
    governance_payload: dict[str, Any],
    conflicts: list[ConflictSummary],
) -> HeroSnapshot:
    conflict_count = len(conflicts)
    pending_prs = int(governance_payload.get("summary", {}).get("open_governance_prs", 0))
    flags = list(status_payload.get("flags") or []) + list(governance_payload.get("flags") or [])
    errors = list(governance_payload.get("errors") or [])
    if conflict_count or pending_prs or errors:
        return HeroSnapshot(
            state="bad",
            glyph="✗",
            title="ACTION REQUIRED",
            subtitle=_hero_subtitle(conflict_count, pending_prs, flags, errors),
            conflicts=conflict_count,
            pending_prs=pending_prs,
        )
    if flags:
        return HeroSnapshot(
            state="warn",
            glyph="◐",
            title="NEEDS ATTENTION",
            subtitle=_hero_subtitle(conflict_count, pending_prs, flags, errors),
            conflicts=conflict_count,
            pending_prs=pending_prs,
        )
    return HeroSnapshot(
        state="ok",
        glyph="✓",
        title="ALL GREEN",
        subtitle="nothing to review · memory is healthy · you can close this window",
        conflicts=0,
        pending_prs=0,
    )


def _hero_subtitle(
    conflict_count: int,
    pending_prs: int,
    flags: list[str],
    errors: list[str],
) -> str:
    if conflict_count and pending_prs:
        return f"{conflict_count} conflict(s) block merges · {pending_prs} PR(s) awaiting review"
    if conflict_count:
        return f"{conflict_count} conflict(s) need a human decision before merge"
    if pending_prs:
        return f"{pending_prs} PR(s) awaiting review · no merge blockers yet"
    if errors:
        return errors[0]
    if flags:
        return flags[0]
    return "attention needed"


def _load_fact_summaries(repo: Path, user_repo: Path) -> list[FactSummary]:
    items: list[FactSummary] = []
    if repo.exists():
        items.extend(_fact_to_summary(fact, "project") for fact in load_all_facts(repo, include_superseded=True))
    if user_repo.exists():
        items.extend(_fact_to_summary(fact, "user") for fact in load_all_facts(user_repo, include_superseded=True))
    items.sort(key=lambda item: (item.fact.created, item.fact.fact_id), reverse=True)
    return items


def _fact_to_summary(fact: Fact, repo_kind: Literal["project", "user"]) -> FactSummary:
    location = (
        fact.file_path.name
        if fact.file_path is not None
        else fact.topic or fact.scope.value
    )
    source_label = fact.source_tool or fact.source_type.value.replace("_", " ")
    return FactSummary(
        fact=fact,
        repo_kind=repo_kind,
        age=relative_age(fact.created),
        location=location,
        scope_label=fact.scope.value,
        source_label=source_label,
    )


def _load_sessions(repo: Path, user_repo: Path, facts: list[FactSummary]) -> list[SessionSummary]:
    facts_by_session: dict[str, list[str]] = defaultdict(list)
    for item in facts:
        facts_by_session[item.fact.source_session].append(item.fact.fact_id)

    sessions: list[SessionSummary] = []
    for repo_kind, repo_dir in (("project", repo), ("user", user_repo)):
        if not repo_dir.exists():
            continue
        for session_id, payload in iter_session_payloads(repo_dir, include_archived=True):
            meta = dict(payload[0].get("_meta", {})) if payload and "_meta" in payload[0] else {}
            events = [event for event in payload if "_meta" not in event]
            started = parse_datetime(str(meta.get("started") or "")) if meta.get("started") else None
            tool = str(meta.get("tool") or meta.get("source") or "unknown")
            usage_events = session_replay(repo_dir, session_id)
            sessions.append(
                SessionSummary(
                    session_id=session_id,
                    repo_kind=repo_kind,
                    tool=tool,
                    source_label=_session_source_label(meta, repo_kind),
                    status=_session_status(meta, started),
                    age=relative_age(started),
                    summary=_session_summary(meta, events),
                    started=started,
                    transcript=events,
                    usage_events=usage_events,
                    proposed_fact_ids=sorted(set(facts_by_session.get(session_id, []))),
                    retrieved_fact_ids=_retrieved_fact_ids(usage_events),
                    sparkline=_session_event_series(events),
                )
            )
    sessions.sort(key=lambda item: (item.started or datetime.min.replace(tzinfo=UTC), item.session_id), reverse=True)
    return sessions


def _load_conflict_summaries(cwd: Path, facts: list[FactSummary]) -> list[ConflictSummary]:
    by_id = {item.fact.fact_id: item for item in facts}
    result = merge_conflicts_action(cwd, dry_run=True)
    if not result.ok:
        return []
    conflicts = [
        ConflictSummary(
            winner=by_id.get(str(item.get("winner_id") or "")),
            loser=by_id.get(str(item.get("loser_id") or "")),
            reason=str(item.get("reason") or ""),
        )
        for item in result.results
    ]
    conflicts.sort(key=lambda item: item.title)
    return conflicts


def _build_tool_activity(sessions: list[SessionSummary]) -> tuple[dict[str, list[float]], dict[str, int]]:
    grouped: dict[str, list[datetime]] = defaultdict(list)
    totals: dict[str, int] = defaultdict(int)
    for session in sessions:
        if session.started is None:
            continue
        tool = session.tool.partition("-")[0] or session.tool
        grouped[tool].append(session.started)
        totals[tool] += 1
    series = {tool: _bucket_series(times) for tool, times in grouped.items()}
    return series, dict(totals)


def _session_status(meta: dict[str, Any], started: datetime | None) -> Tone:
    ended = parse_datetime(str(meta.get("ended") or "")) if meta.get("ended") else None
    if started is None:
        return "idle"
    now = datetime.now(tz=UTC)
    if ended is None and started >= now - timedelta(hours=2):
        return "warn"
    if started >= now - timedelta(hours=24):
        return "ok"
    return "idle"


def _session_source_label(meta: dict[str, Any], repo_kind: Literal["project", "user"]) -> str:
    source = str(meta.get("source") or "").strip()
    if not source:
        return repo_kind
    return f"{repo_kind} · {source}"


def _session_summary(meta: dict[str, Any], events: list[dict[str, Any]]) -> str:
    summary = str(meta.get("summary") or "").strip()
    if summary:
        return summary
    for event in events:
        content = event.get("content")
        if isinstance(content, str) and content.strip():
            return " ".join(content.split())[:64]
    return "session activity"


def _retrieved_fact_ids(events: list[dict[str, Any]]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for event in events:
        fact_id = str(event.get("fact_id") or "").strip()
        if not fact_id or fact_id in seen:
            continue
        seen.add(fact_id)
        ordered.append(fact_id)
    return ordered


def _session_event_series(events: list[dict[str, Any]], limit: int = 10) -> list[float]:
    if not events:
        return [0.0] * limit
    counts: list[int] = []
    bucket_size = max(1, len(events) // limit)
    for idx in range(0, len(events), bucket_size):
        counts.append(len(events[idx: idx + bucket_size]))
    peak = max(counts) or 1
    values = [min(1.0, count / peak) for count in counts[:limit]]
    return _pad_left(values, limit=limit)


def _bucket_series(times: list[datetime], buckets: int = 12, window_hours: int = 24) -> list[float]:
    if not times:
        return [0.0] * buckets
    now = datetime.now(tz=UTC)
    start = now - timedelta(hours=window_hours)
    counts = [0] * buckets
    width = (window_hours * 3600) / buckets
    for stamp in times:
        if stamp < start or stamp > now:
            continue
        delta = max(0.0, (stamp - start).total_seconds())
        index = min(buckets - 1, int(delta // width))
        counts[index] += 1
    peak = max(counts) or 1
    return [round(count / peak, 2) for count in counts]


def _pad_left(values: list[float], *, limit: int) -> list[float]:
    if len(values) >= limit:
        return values[-limit:]
    return [0.0] * (limit - len(values)) + values


def _fact_repo(snapshot: GitmemTUISnapshot, fact: Fact) -> Path:
    if fact.scope == Scope.USER:
        return snapshot.user_repo
    repo = snapshot.repo
    if repo.exists() and find_fact_by_id(repo, fact.fact_id) is not None:
        return repo
    if snapshot.user_repo.exists() and find_fact_by_id(snapshot.user_repo, fact.fact_id) is not None:
        return snapshot.user_repo
    return repo


def relative_age(value: datetime | None, *, future: bool = False) -> str:
    if value is None:
        return "-"
    now = datetime.now(tz=UTC)
    delta = (value - now) if future else (now - value)
    seconds = int(abs(delta.total_seconds()))
    if seconds < 60:
        unit = "s"
        amount = seconds
    elif seconds < 3600:
        unit = "m"
        amount = seconds // 60
    elif seconds < 86400:
        unit = "h"
        amount = seconds // 3600
    else:
        unit = "d"
        amount = seconds // 86400
    if future:
        return f"in {amount}{unit}"
    return f"{amount}{unit}"
