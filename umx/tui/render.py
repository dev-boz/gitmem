from __future__ import annotations

from dataclasses import dataclass
from io import StringIO
from typing import Any

from rich import box
from rich.align import Align
from rich.columns import Columns
from rich.console import Console, Group, RenderableType
from rich.padding import Padding
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from umx.dream.lint import read_last_lint

from .data import (
    NAV_ITEMS,
    SUBNAV_ITEMS,
    ConflictSummary,
    GitmemTUISnapshot,
    PageName,
    SearchResult,
    current_search_results,
    fact_history,
    fact_score_components,
    facts_by_strength,
    filter_facts,
    most_recent_runs,
    next_dream_run_text,
    run_throughput_series,
    selected_fact,
    selected_pr,
    selected_search_result,
    selected_session,
    session_stats,
    subnav_counts,
    top_scope,
)

TERM_BG = "#0b0e14"
TERM_BG_ALT = "#10141c"
TERM_BG_PANEL = "#0d1117"
TERM_FG = "#c9d1d9"
TERM_FG_BRIGHT = "#f0f6fc"
TERM_FG_DIM = "#6e7681"
TERM_FG_FAINT = "#484f58"
TERM_LINE = "#30363d"
TERM_LINE_BRIGHT = "#484f58"
ANSI_RED = "#f85149"
ANSI_GREEN = "#3fb950"
ANSI_YELLOW = "#d29922"
ANSI_BLUE = "#58a6ff"
ANSI_MAGENTA = "#bc8cff"
ANSI_CYAN = "#39c5cf"
ANSI_ORANGE = "#ff8c42"
HERO_BG = {"ok": "#0a1f0e", "warn": "#1f1a08", "bad": "#1f0a0a", "idle": TERM_BG}
STATE_COLOR = {"ok": ANSI_GREEN, "warn": ANSI_YELLOW, "bad": ANSI_RED, "idle": TERM_FG_DIM}
STRENGTH_COLOR = {1: ANSI_RED, 2: ANSI_ORANGE, 3: ANSI_YELLOW, 4: ANSI_GREEN, 5: "#56d364"}


@dataclass(slots=True)
class RenderState:
    current_page: PageName
    active_pane: int
    fact_index: int
    session_index: int
    pr_index: int
    search_index: int
    facts_query: str
    search_query: str
    input_mode: str | None
    selected_conflict_option: str
    notice: str | None = None


def render_to_plain(renderable: RenderableType, *, width: int) -> str:
    console = Console(file=StringIO(), width=width, color_system=None, force_terminal=False, record=True)
    console.print(renderable)
    return console.export_text(clear=False)


def render_nav(snapshot: GitmemTUISnapshot, state: RenderState, *, width: int) -> RenderableType:
    line = Text(style=f"on {TERM_BG_PANEL}")
    for key, page, label in NAV_ITEMS:
        if line:
            line.append(" ")
        if page == state.current_page:
            line.append(f" {key} {label} ", style=f"bold {TERM_BG} on {TERM_FG}")
        else:
            line.append(f" {key} ", style=TERM_FG_DIM)
            line.append(label, style=TERM_FG)
            line.append(" ", style=TERM_FG_DIM)
    right = Text(f"{snapshot.mode} · {snapshot.slug}", style=TERM_FG_DIM)
    if line.cell_len < width - right.cell_len - 1:
        line.append(" " * (width - right.cell_len - line.cell_len))
    line.append_text(right)
    return line


def render_subnav(snapshot: GitmemTUISnapshot, state: RenderState, *, width: int) -> RenderableType:
    line = Text(style=f"on {TERM_BG_ALT}")
    counts = subnav_counts(snapshot, state.current_page)
    for idx, (key, label) in enumerate(SUBNAV_ITEMS[state.current_page]):
        if idx:
            line.append("  ", style=TERM_FG_DIM)
        style = f"bold {TERM_FG_BRIGHT}" if idx == 0 else TERM_FG_DIM
        line.append(label, style=style)
        count = counts.get(key)
        if count is not None:
            tone = _subnav_tone(state.current_page, key)
            line.append(f" ({count})", style=STATE_COLOR[tone] if tone != "idle" else TERM_FG_FAINT)
    right = _subnav_right(snapshot, state)
    if line.cell_len < width - right.cell_len - 1:
        line.append(" " * (width - right.cell_len - line.cell_len))
    line.append_text(right)
    return line


def render_footer(snapshot: GitmemTUISnapshot, state: RenderState, *, width: int) -> RenderableType:
    if state.input_mode == "facts":
        body = Text(" / facts filter · type to filter · Enter/Esc to exit ", style=f"bold {ANSI_CYAN} on {TERM_BG_ALT}")
    elif state.input_mode == "search":
        body = Text(" / search · type to search facts and sessions · Enter/Esc to exit ", style=f"bold {ANSI_CYAN} on {TERM_BG_ALT}")
    else:
        body = Text(style=f"on {TERM_BG_PANEL}")
        for key, label in _hint_items(state.current_page):
            body.append(f"{key}", style=f"bold {TERM_FG_BRIGHT}")
            body.append(f" {label} ", style=TERM_FG_DIM)
    notice = state.notice or "preview mode for write actions"
    suffix = Text(notice, style=TERM_FG_DIM)
    if body.cell_len < width - suffix.cell_len - 1:
        body.append(" " * (width - suffix.cell_len - body.cell_len))
    body.append_text(suffix)
    return body


def render_page(snapshot: GitmemTUISnapshot, state: RenderState, *, width: int) -> RenderableType:
    if state.current_page == "overview":
        return render_overview(snapshot, state, width=width)
    if state.current_page == "facts":
        return render_facts(snapshot, state, width=width)
    if state.current_page == "sessions":
        return render_sessions(snapshot, state, width=width)
    if state.current_page == "dream":
        return render_dream(snapshot, state, width=width)
    if state.current_page == "prs":
        return render_prs(snapshot, state, width=width)
    return render_search(snapshot, state, width=width)


def render_overview(snapshot: GitmemTUISnapshot, state: RenderState, *, width: int) -> RenderableType:
    hero = Text(style=f"on {HERO_BG[snapshot.hero.state]}")
    hero.append(f" {snapshot.hero.glyph} ", style=f"bold {STATE_COLOR[snapshot.hero.state]} on {HERO_BG[snapshot.hero.state]}")
    hero.append(snapshot.hero.title, style=f"bold {STATE_COLOR[snapshot.hero.state]} on {HERO_BG[snapshot.hero.state]}")
    hero.append(" — ", style=f"{TERM_FG_DIM} on {HERO_BG[snapshot.hero.state]}")
    hero.append(snapshot.hero.subtitle, style=f"{TERM_FG_DIM} on {HERO_BG[snapshot.hero.state]}")
    pills = Text(
        f"conflicts {snapshot.hero.conflicts} · PRs {snapshot.hero.pending_prs}",
        style=f"bold {STATE_COLOR[snapshot.hero.state]} on {HERO_BG[snapshot.hero.state]}",
    )
    status = Text(style=TERM_FG_DIM)
    status.append(f"umx v{snapshot.version}", style=f"bold {ANSI_MAGENTA}")
    status.append(f"  mode {snapshot.mode}", style=TERM_FG_DIM)
    status.append(f"  facts {len(snapshot.facts)}", style=TERM_FG_BRIGHT)
    status.append(f"  sessions {len(snapshot.sessions)}", style=TERM_FG_BRIGHT)
    status.append(f"  last dream {snapshot.status_payload.get('last_dream') or '-'}", style=TERM_FG_DIM)
    memory = _overview_memory_panel(snapshot)
    dream = _overview_dream_panel(snapshot)
    sessions = _overview_sessions_panel(snapshot)
    action = _overview_action_panel(snapshot)
    panels: list[RenderableType]
    if width >= 120:
        top = Columns([memory, dream], equal=True, expand=True)
        bottom = Columns([sessions, action], equal=True, expand=True)
        panels = [top, bottom]
    else:
        panels = [memory, dream, sessions, action]
    return Group(
        Align.left(Columns([hero, pills], expand=True)),
        status,
        *panels,
    )


def render_facts(snapshot: GitmemTUISnapshot, state: RenderState, *, width: int) -> RenderableType:
    filtered = filter_facts(snapshot, state.facts_query)
    selected = selected_fact(snapshot, state.fact_index, state.facts_query)
    filter_panel = _facts_filter_panel(snapshot, state, len(filtered))
    list_panel = _facts_list_panel(filtered, state.fact_index)
    detail_panel = _facts_detail_panel(snapshot, selected)
    if width < 110:
        return Group(filter_panel, list_panel, detail_panel)
    layout = Table.grid(expand=True)
    layout.add_column(width=max(24, width // 5))
    layout.add_column(width=max(38, width // 3))
    layout.add_column(ratio=1)
    layout.add_row(filter_panel, list_panel, detail_panel)
    return layout


def render_sessions(snapshot: GitmemTUISnapshot, state: RenderState, *, width: int) -> RenderableType:
    session = selected_session(snapshot, state.session_index)
    list_panel = _sessions_list_panel(snapshot, state.session_index)
    transcript_panel = _sessions_transcript_panel(snapshot, session)
    contrib_panel = _sessions_contrib_panel(snapshot, session)
    if width < 120:
        return Group(list_panel, transcript_panel, contrib_panel)
    layout = Table.grid(expand=True)
    layout.add_column(width=max(28, width // 4))
    layout.add_column(ratio=1)
    layout.add_column(width=max(30, width // 4))
    layout.add_row(list_panel, transcript_panel, contrib_panel)
    return layout


def render_dream(snapshot: GitmemTUISnapshot, state: RenderState, *, width: int) -> RenderableType:
    top = Columns([_dream_pipeline_panel(snapshot), _dream_results_panel(snapshot)], expand=True, equal=True)
    bottom = _dream_footer_strip(snapshot)
    return Group(top, bottom)


def render_prs(snapshot: GitmemTUISnapshot, state: RenderState, *, width: int) -> RenderableType:
    pr_panel = _prs_panel(snapshot, state)
    resolver = _conflict_resolver_panel(snapshot, state)
    if width < 110:
        return Group(pr_panel, resolver)
    layout = Table.grid(expand=True)
    layout.add_column(ratio=1)
    layout.add_column(width=max(40, width // 3))
    layout.add_row(pr_panel, resolver)
    return layout


def render_search(snapshot: GitmemTUISnapshot, state: RenderState, *, width: int) -> RenderableType:
    results = current_search_results(snapshot, state.search_query)
    selected = selected_search_result(snapshot, state.search_index, state.search_query)
    filter_panel = _search_filter_panel(state, results)
    list_panel = _search_list_panel(results, state.search_index)
    detail_panel = _search_detail_panel(snapshot, selected)
    if width < 110:
        return Group(filter_panel, list_panel, detail_panel)
    layout = Table.grid(expand=True)
    layout.add_column(width=max(24, width // 5))
    layout.add_column(width=max(38, width // 3))
    layout.add_column(ratio=1)
    layout.add_row(filter_panel, list_panel, detail_panel)
    return layout


def spark(values: list[float]) -> Text:
    glyphs = "▁▂▃▄▅▆▇█"
    text = Text()
    for value in values:
        index = max(0, min(len(glyphs) - 1, int(round(value * (len(glyphs) - 1)))))
        text.append(glyphs[index], style=ANSI_CYAN)
    return text


def progress(value: float, *, width: int = 18, color: str = ANSI_GREEN) -> Text:
    whole = max(0, min(width, int(round(value * width))))
    text = Text()
    text.append("█" * whole, style=color)
    text.append("░" * max(0, width - whole), style=TERM_LINE_BRIGHT)
    return text


def strength_badge(value: int) -> Text:
    return Text(f"▮{value}", style=f"bold {STRENGTH_COLOR.get(value, TERM_FG)}")


def state_glyph(state: str) -> Text:
    glyph = {"ok": "●", "warn": "◉", "bad": "✗", "idle": "○"}.get(state, "○")
    return Text(glyph, style=f"bold {STATE_COLOR.get(state, TERM_FG_DIM)}")


def _overview_memory_panel(snapshot: GitmemTUISnapshot) -> RenderableType:
    counts = facts_by_strength(snapshot.facts)
    lines = []
    total = max(1, len(snapshot.facts))
    for level in range(5, 0, -1):
        ratio = counts[level] / total if total else 0
        row = Text()
        row.append(f"S:{level} ", style=f"bold {STRENGTH_COLOR[level]}")
        row.append_text(progress(ratio, width=20, color=STRENGTH_COLOR[level]))
        row.append(f" {counts[level]}", style=TERM_FG_BRIGHT)
        lines.append(row)
    scope_label, scope_count = top_scope(snapshot.facts)
    footer = Text(f"top scope: {scope_label} · {scope_count} facts", style=TERM_FG_DIM)
    return _panel("memory", Group(*lines, footer), subtitle=f"{len(snapshot.facts)} facts")


def _overview_dream_panel(snapshot: GitmemTUISnapshot) -> RenderableType:
    lines = []
    last_completed = snapshot.processing_summary.get("last_completed")
    last_failed = snapshot.processing_summary.get("last_failed")
    active_runs = int(snapshot.processing_summary.get("active_runs") or 0)
    last_run_state = "warn" if active_runs else ("bad" if last_failed and not last_completed else "ok")
    last_line = Text()
    last_line.append_text(state_glyph(last_run_state))
    if last_completed:
        last_line.append(f" last run added {int(last_completed.get('added') or 0)}", style=TERM_FG)
        last_line.append(f" · pruned {int(last_completed.get('pruned') or 0)}", style=TERM_FG_DIM)
    elif last_failed:
        last_line.append(f" last run failed: {last_failed.get('error')}", style=ANSI_RED)
    else:
        last_line.append(" no dream runs yet", style=TERM_FG_DIM)
    lines.append(last_line)
    merged = Text()
    merged.append_text(state_glyph("ok"))
    merged.append(f" merged {len(most_recent_runs(snapshot))}", style=TERM_FG)
    pending = Text()
    pending.append_text(state_glyph("warn" if snapshot.hero.pending_prs else "ok"))
    pending.append(f" pending {snapshot.hero.pending_prs}", style=TERM_FG)
    conflicts = Text()
    conflicts.append_text(state_glyph("bad" if snapshot.hero.conflicts else "ok"))
    conflicts.append(f" conflicts {snapshot.hero.conflicts}", style=TERM_FG)
    next_run = Text(f"next auto-run {next_dream_run_text(snapshot)}", style=TERM_FG_DIM)
    throughput = Text("throughput ", style=TERM_FG_DIM)
    throughput.append_text(spark(run_throughput_series(snapshot)))
    return _panel("dream pipeline", Group(*lines, merged, pending, conflicts, next_run, throughput), subtitle="live")


def _overview_sessions_panel(snapshot: GitmemTUISnapshot) -> RenderableType:
    body: list[RenderableType] = []
    for item in snapshot.sessions[:5]:
        line = Text()
        line.append_text(state_glyph(item.status))
        line.append(" ")
        line.append(item.tool, style=ANSI_CYAN)
        line.append(f" {item.age} ", style=TERM_FG_DIM)
        line.append(item.summary[:42], style=TERM_FG_BRIGHT if item.status == "warn" else TERM_FG)
        body.append(line)
    body.append(Text("activity (last 24h) by tool", style=TERM_FG_DIM))
    for tool, values in list(snapshot.activity_by_tool.items())[:3]:
        row = Text(f"{tool:<8}", style=TERM_FG_DIM)
        row.append_text(spark(values))
        row.append(f"  {snapshot.activity_totals.get(tool, 0)}", style=TERM_FG_DIM)
        body.append(row)
    return _panel("sessions", Group(*body), subtitle=f"{len(snapshot.sessions)} total")


def _overview_action_panel(snapshot: GitmemTUISnapshot) -> RenderableType:
    rows: list[RenderableType] = []
    if snapshot.hero.state == "ok":
        rows.append(Text("● nothing · inbox zero", style=ANSI_GREEN))
        if (last_completed := snapshot.processing_summary.get("last_completed")) is not None:
            rows.append(Text(f"last cleared by Dream · {last_completed.get('ts')}", style=TERM_FG_DIM))
    else:
        for item in snapshot.conflicts[:4]:
            rows.append(Text(f"✗ {item.title} · {item.reason}", style=ANSI_RED))
        for pr in list(snapshot.governance_payload.get("open_prs") or [])[:3]:
            rows.append(Text(f"◐ PR #{pr.get('number')} · {pr.get('title')}", style=ANSI_YELLOW))
        for quarantine in snapshot.quarantine[:2]:
            rows.append(Text(f"◐ quarantine · {quarantine.session_id}", style=ANSI_YELLOW))
        for flag in list(snapshot.status_payload.get("flags") or [])[:2]:
            rows.append(Text(flag, style=TERM_FG_DIM))
    rows.append(Text("press F6 for PRs · c for conflict resolver · d for Dream", style=TERM_FG_DIM))
    return _panel("what needs you", Group(*rows))


def _facts_filter_panel(snapshot: GitmemTUISnapshot, state: RenderState, match_count: int) -> RenderableType:
    procedures = sum(1 for item in snapshot.facts if item.fact.task_status)
    rows = [
        Text("FILTERS", style=f"bold {TERM_FG_BRIGHT}"),
        Text(f"/ {state.facts_query or ''}", style=TERM_FG_DIM),
        Text(f"scope  project {sum(1 for item in snapshot.facts if item.repo_kind == 'project')} · user {sum(1 for item in snapshot.facts if item.repo_kind == 'user')}", style=TERM_FG),
        Text(f"conflicted  {sum(1 for item in snapshot.facts if item.has_conflict)}", style=ANSI_RED if any(item.has_conflict for item in snapshot.facts) else TERM_FG_DIM),
        Text(f"superseded  {len(snapshot.superseded_facts)}", style=TERM_FG_DIM),
        Text(f"tombstoned  {len(snapshot.tombstones)}", style=TERM_FG_DIM),
        Text(f"procedures  {procedures}", style=ANSI_CYAN if procedures else TERM_FG_DIM),
        Text(f"matches     {match_count}", style=f"bold {TERM_FG_BRIGHT}"),
    ]
    return _panel("filters", Group(*rows))


def _facts_list_panel(facts: list[Any], selected_index: int) -> RenderableType:
    rows: list[RenderableType] = []
    if not facts:
        rows.append(Text("no facts match the current filter", style=TERM_FG_DIM))
    for idx, item in enumerate(facts[:18]):
        prefix = "> " if idx == selected_index else "  "
        title = Text(prefix)
        title.append_text(strength_badge(item.fact.encoding_strength))
        title.append(" ")
        title.append(item.title[:38], style=f"bold {TERM_FG_BRIGHT}" if idx == selected_index else TERM_FG)
        if item.has_conflict:
            title.append(" ⚑", style=ANSI_RED)
        subtitle = Text(f"   {item.scope_label} · {item.age} · {item.location}", style=TERM_FG_DIM)
        rows.extend([title, subtitle])
    return _panel("facts", Group(*rows), subtitle=f"{len(facts)} shown")


def _facts_detail_panel(snapshot: GitmemTUISnapshot, item: Any) -> RenderableType:
    if item is None:
        return _panel("detail", Text("select a fact to inspect", style=TERM_FG_DIM))
    fact = item.fact
    trust, relevance, retention, total = fact_score_components(fact)
    lines: list[RenderableType] = [
        Text(f"id {fact.fact_id} · S:{fact.encoding_strength} · {item.scope_label}", style=TERM_FG_DIM),
        Panel(Text(fact.text, style=f"bold {TERM_FG_BRIGHT}"), border_style=ANSI_GREEN, box=box.SQUARE),
        Text("composite score", style=TERM_FG_DIM),
        _metric_line("trust", trust, ANSI_GREEN),
        _metric_line("relevance", relevance, ANSI_CYAN),
        _metric_line("retention", retention, ANSI_YELLOW),
        Text(f"total {total:.2f}", style=f"bold {TERM_FG_BRIGHT}"),
        Text("provenance", style=f"bold {TERM_FG_BRIGHT}"),
        Text(f"source  {item.source_label}", style=TERM_FG_DIM),
        Text(f"session {fact.source_session}", style=ANSI_CYAN),
    ]
    history = fact_history(snapshot, fact)
    if history:
        lines.append(Text("history", style=f"bold {TERM_FG_BRIGHT}"))
        for prior in history[:4]:
            lines.append(Text(f"● {prior.fact_id} · {prior.text[:56]}", style=TERM_FG_DIM))
    return _panel("detail", Group(*lines))


def _sessions_list_panel(snapshot: GitmemTUISnapshot, selected_index: int) -> RenderableType:
    rows: list[RenderableType] = []
    if not snapshot.sessions:
        rows.append(Text("no sessions yet", style=TERM_FG_DIM))
    for idx, item in enumerate(snapshot.sessions[:16]):
        prefix = "> " if idx == selected_index else "  "
        line = Text(prefix)
        line.append_text(state_glyph(item.status))
        line.append(" ")
        line.append(item.tool, style=ANSI_CYAN)
        line.append(f" · {item.age}", style=TERM_FG_DIM)
        summary = Text(f"  {item.summary[:28]}", style=TERM_FG_DIM)
        rows.extend([line, summary])
    return _panel("sessions", Group(*rows), subtitle=f"{len(snapshot.sessions)} total")


def _sessions_transcript_panel(snapshot: GitmemTUISnapshot, session: Any) -> RenderableType:
    if session is None:
        return _panel("transcript", Text("select a session to inspect", style=TERM_FG_DIM))
    rows: list[RenderableType] = [
        Text(f"{session.session_id} · {session.tool} · {session.source_label}", style=f"bold {TERM_FG_BRIGHT}"),
    ]
    if not session.transcript:
        rows.append(Text("no transcript events stored", style=TERM_FG_DIM))
    for idx, event in enumerate(session.transcript[:18]):
        prefix = str(event.get("ts") or event.get("timestamp") or f"{idx:02d}")
        line = Text(f"{prefix:>6} ", style=TERM_FG_FAINT)
        role = str(event.get("role") or "event")
        if role == "user":
            line.append("user", style=ANSI_MAGENTA)
        elif role == "assistant":
            line.append(session.tool, style=ANSI_GREEN)
        elif role == "tool":
            line.append("tool", style=ANSI_CYAN)
            tool_name = str(event.get("tool_name") or event.get("name") or "")
            if tool_name:
                line.append(f" · {tool_name}", style=ANSI_YELLOW)
        else:
            line.append(role, style=TERM_FG_DIM)
        content = str(event.get("content") or event.get("text") or event.get("message") or "").strip()
        if content:
            line.append(" › ", style=TERM_FG_DIM)
            line.append(" ".join(content.split())[:72], style=TERM_FG)
        rows.append(line)
    if session.proposed_fact_ids:
        rows.append(Text("▸ extracted", style=ANSI_GREEN))
        for fact_id in session.proposed_fact_ids[:4]:
            rows.append(Text(f"  {fact_id}", style=TERM_FG_DIM))
    return _panel("transcript", Group(*rows))


def _sessions_contrib_panel(snapshot: GitmemTUISnapshot, session: Any) -> RenderableType:
    if session is None:
        return _panel("contributions", Text("select a session to inspect", style=TERM_FG_DIM))
    stats = session_stats(session)
    rows: list[RenderableType] = [Text("proposed", style=TERM_FG_DIM)]
    if session.proposed_fact_ids:
        for fact_id in session.proposed_fact_ids[:5]:
            rows.append(Text(f"▸ {fact_id}", style=TERM_FG))
    else:
        rows.append(Text("none captured yet", style=TERM_FG_DIM))
    rows.append(Text("retrieved", style=TERM_FG_DIM))
    if session.retrieved_fact_ids:
        for fact_id in session.retrieved_fact_ids[:5]:
            rows.append(Text(f"▸ {fact_id}", style=TERM_FG))
    else:
        rows.append(Text("no recorded injections", style=TERM_FG_DIM))
    rows.append(Text("stats", style=TERM_FG_DIM))
    rows.append(Text(f"events {stats['events']}", style=TERM_FG_BRIGHT))
    rows.append(Text(f"files read {stats['files_read']}", style=TERM_FG_BRIGHT))
    rows.append(Text(f"files edited {stats['files_edited']}", style=TERM_FG_BRIGHT))
    return _panel("contributions", Group(*rows))


def _dream_pipeline_panel(snapshot: GitmemTUISnapshot) -> RenderableType:
    active_runs = int(snapshot.processing_summary.get("active_runs") or 0)
    lint_at = read_last_lint(snapshot.repo)
    stages = [
        ("orient", "ok" if snapshot.processing_records else "idle", 1.0 if snapshot.processing_records else 0.0),
        ("gather", "ok" if snapshot.processing_records else "idle", 1.0 if snapshot.processing_records else 0.0),
        ("consolidate", "warn" if active_runs else ("ok" if snapshot.processing_summary.get("last_completed") else "idle"), 0.64 if active_runs else (1.0 if snapshot.processing_summary.get("last_completed") else 0.0)),
        ("lint", "ok" if lint_at else "idle", 1.0 if lint_at else 0.0),
        ("prune", "ok" if snapshot.processing_summary.get("last_completed") else "idle", 1.0 if snapshot.processing_summary.get("last_completed") else 0.0),
    ]
    rows: list[RenderableType] = []
    for name, tone, completion in stages:
        row = Text(f"{name:<12}", style=STATE_COLOR[tone])
        row.append_text(progress(completion, width=22, color=STATE_COLOR[tone]))
        if tone == "warn":
            row.append(" running", style=ANSI_YELLOW)
        elif tone == "ok" and completion > 0:
            row.append(" complete", style=TERM_FG_DIM)
        rows.append(row)
    sparkline = Text("throughput ", style=TERM_FG_DIM)
    sparkline.append_text(spark(run_throughput_series(snapshot)))
    rows.append(sparkline)
    return _panel("pipeline", Group(*rows), subtitle=f"{snapshot.processing_summary.get('active_runs', 0)} active")


def _dream_results_panel(snapshot: GitmemTUISnapshot) -> RenderableType:
    last_completed = snapshot.processing_summary.get("last_completed") or {}
    rows = [
        Text(f"proposed   {int(last_completed.get('added') or 0)}", style=ANSI_GREEN),
        Text(f"auto-merged {len(most_recent_runs(snapshot))}", style=ANSI_GREEN),
        Text(f"deduped    {len(snapshot.superseded_facts)}", style=ANSI_YELLOW),
        Text(f"conflicts  {len(snapshot.conflicts)}", style=ANSI_RED if snapshot.conflicts else TERM_FG_DIM),
        Text("opened PRs", style=TERM_FG_DIM),
    ]
    prs = list(snapshot.governance_payload.get("open_prs") or [])
    if prs:
        for pr in prs[:4]:
            rows.append(Text(f"PR #{pr.get('number')} · {pr.get('title')}", style=ANSI_YELLOW))
    else:
        rows.append(Text("none", style=TERM_FG_DIM))
    rows.append(Text("sessions consumed", style=TERM_FG_DIM))
    for session in snapshot.sessions[:3]:
        rows.append(Text(f"{session.session_id} · {session.summary[:36]}", style=TERM_FG))
    return _panel("results so far", Group(*rows))


def _dream_footer_strip(snapshot: GitmemTUISnapshot) -> RenderableType:
    strip = Table.grid(expand=True)
    strip.add_column(ratio=1)
    strip.add_column(ratio=1)
    strip.add_column(width=20)
    left = Text(f"next auto-run {next_dream_run_text(snapshot)}", style=TERM_FG_BRIGHT)
    middle = Text("last 7 runs ", style=TERM_FG_DIM)
    middle.append_text(spark(run_throughput_series(snapshot, limit=7)))
    button = Text("[ d  run now ]", style=f"bold {ANSI_CYAN}")
    strip.add_row(left, middle, Align.right(button))
    return Panel(strip, box=box.SQUARE, border_style=TERM_LINE_BRIGHT)


def _prs_panel(snapshot: GitmemTUISnapshot, state: RenderState) -> RenderableType:
    pr = selected_pr(snapshot, state.pr_index)
    rows: list[RenderableType] = []
    if pr is None:
        rows.append(Text("no open governance PRs", style=TERM_FG_DIM))
        if snapshot.conflicts:
            rows.append(Text(f"local conflict previews: {len(snapshot.conflicts)}", style=ANSI_RED))
            for item in snapshot.conflicts[:4]:
                rows.append(Text(f"⚑ {item.title} · {item.reason}", style=ANSI_RED))
        return _panel("PRs", Group(*rows))
    rows.append(Text(f"#{pr.get('number')} {pr.get('title')}", style=f"bold {TERM_FG_BRIGHT}"))
    rows.append(Text(f"{pr.get('head_ref')} · {pr.get('state') or 'unknown'}", style=TERM_FG_DIM))
    rows.append(Text(str(pr.get("url") or ""), style=ANSI_CYAN))
    fact_ids = list(pr.get("fact_ids") or [])
    if fact_ids:
        rows.append(Text("fact delta", style=TERM_FG_DIM))
        for fact_id in fact_ids[:8]:
            rows.append(Text(f"+ {fact_id}", style=ANSI_GREEN))
    else:
        rows.append(Text("no parsed fact delta body", style=TERM_FG_DIM))
    if pr.get("body_error"):
        rows.append(Text(f"body error: {pr['body_error']}", style=ANSI_RED))
    if pr.get("human_review"):
        rows.append(Text("human review requested", style=ANSI_YELLOW))
    return _panel("pending PR", Group(*rows), subtitle=f"{state.pr_index + 1}/{max(1, len(snapshot.governance_payload.get('open_prs') or []))}")


def _conflict_resolver_panel(snapshot: GitmemTUISnapshot, state: RenderState) -> RenderableType:
    conflict = snapshot.conflicts[0] if snapshot.conflicts else None
    if conflict is None:
        return _panel("resolver", Text("no local conflicts to resolve", style=TERM_FG_DIM))
    options = _conflict_options(conflict)
    rows: list[RenderableType] = [Text(conflict.title, style=f"bold {ANSI_RED}"), Text(conflict.reason, style=TERM_FG_DIM)]
    for key, label, tone in options:
        marker = "(●)" if state.selected_conflict_option == key else "( )"
        style = f"bold {ANSI_CYAN}" if state.selected_conflict_option == key else STATE_COLOR[tone]
        rows.append(Text(f"{marker} {key} {label}", style=style))
    rows.append(Text("[ ↵ apply & merge ]", style=f"bold {ANSI_GREEN}"))
    rows.append(Text("preview only for manual conflict choices", style=TERM_FG_DIM))
    return _panel("conflict resolver", Group(*rows))


def _search_filter_panel(state: RenderState, results: list[SearchResult]) -> RenderableType:
    fact_hits = sum(1 for item in results if item.kind == "fact")
    session_hits = sum(1 for item in results if item.kind == "session")
    rows = [
        Text("SEARCH", style=f"bold {TERM_FG_BRIGHT}"),
        Text(f"/ {state.search_query or ''}", style=TERM_FG_DIM),
        Text(f"all      {len(results)}", style=TERM_FG),
        Text(f"facts    {fact_hits}", style=TERM_FG),
        Text(f"sessions {session_hits}", style=TERM_FG),
    ]
    return _panel("filters", Group(*rows))


def _search_list_panel(results: list[SearchResult], selected_index: int) -> RenderableType:
    rows: list[RenderableType] = []
    if not results:
        rows.append(Text("type / to search facts and sessions", style=TERM_FG_DIM))
    for idx, item in enumerate(results[:18]):
        prefix = "> " if idx == selected_index else "  "
        row = Text(prefix)
        if item.kind == "fact" and item.fact is not None:
            row.append_text(strength_badge(item.fact.fact.encoding_strength))
        else:
            row.append("▸", style=ANSI_CYAN)
        row.append(" ")
        row.append(item.title[:36], style=f"bold {TERM_FG_BRIGHT}" if idx == selected_index else TERM_FG)
        rows.append(row)
        rows.append(Text(f"   {item.subtitle}", style=TERM_FG_DIM))
    return _panel("results", Group(*rows), subtitle=f"{len(results)} hits")


def _search_detail_panel(snapshot: GitmemTUISnapshot, item: SearchResult | None) -> RenderableType:
    if item is None:
        return _panel("detail", Text("select a result to inspect", style=TERM_FG_DIM))
    if item.kind == "fact" and item.fact is not None:
        return _facts_detail_panel(snapshot, item.fact)
    rows = [
        Text(item.title, style=f"bold {TERM_FG_BRIGHT}"),
        Text(item.subtitle, style=TERM_FG_DIM),
        Panel(Text(item.snippet, style=TERM_FG), border_style=ANSI_CYAN, box=box.SQUARE),
    ]
    if item.session is not None:
        rows.append(Text(f"session {item.session.session_id}", style=ANSI_CYAN))
        rows.append(Text(f"tool {item.session.tool}", style=TERM_FG_DIM))
    return _panel("detail", Group(*rows))


def _conflict_options(conflict: ConflictSummary) -> list[tuple[str, str, str]]:
    winner_text = conflict.winner.fact.text if conflict.winner is not None else "winner"
    loser_text = conflict.loser.fact.text if conflict.loser is not None else "loser"
    return [
        ("A", winner_text[:44], "ok"),
        ("B", loser_text[:44], "warn"),
        ("C", "scope-split / keep both with narrower applies-to", "idle"),
        ("D", "tombstone both", "bad"),
    ]


def _metric_line(label: str, value: float, color: str) -> Text:
    line = Text(f"{label:<9}", style=TERM_FG_DIM)
    line.append_text(progress(value, width=24, color=color))
    line.append(f" {value:.2f}", style=TERM_FG_DIM)
    return line


def _panel(title: str, body: RenderableType, subtitle: str | None = None) -> RenderableType:
    panel_title = Text(title, style=f"bold {TERM_FG_BRIGHT}")
    if subtitle:
        panel_title.append(" · ", style=TERM_FG_DIM)
        panel_title.append(subtitle, style=TERM_FG_DIM)
    return Panel(Padding(body, (0, 1)), box=box.SQUARE, title=panel_title, border_style=TERM_LINE_BRIGHT)


def _subnav_tone(page: PageName, key: str) -> str:
    if page == "facts" and key == "conflicted":
        return "bad"
    if page == "facts" and key == "procedures":
        return "idle"
    if page == "sessions" and key == "live":
        return "warn"
    if page == "prs" and key == "pending":
        return "warn"
    if page == "prs" and key == "conflicts":
        return "bad"
    if page == "prs" and key == "merged":
        return "ok"
    return "idle"


def _subnav_right(snapshot: GitmemTUISnapshot, state: RenderState) -> Text:
    if state.current_page == "overview":
        return Text(f"last dream {snapshot.status_payload.get('last_dream') or '-'}", style=TERM_FG_DIM)
    if state.current_page == "facts":
        return Text(f"scope {snapshot.slug}", style=TERM_FG_DIM)
    if state.current_page == "sessions":
        return Text(f"{len(snapshot.sessions)} total sessions", style=TERM_FG_DIM)
    if state.current_page == "dream":
        return Text(f"mode {snapshot.mode}", style=TERM_FG_DIM)
    if state.current_page == "prs":
        return Text(str(snapshot.governance_payload.get("repo") or snapshot.repo), style=TERM_FG_DIM)
    return Text("FTS5 + session search", style=TERM_FG_DIM)


def _hint_items(page: PageName) -> tuple[tuple[str, str], ...]:
    if page == "overview":
        return (("F2", "overview"), ("F3", "facts"), ("F4", "sessions"), ("F5", "dream"), ("F6", "PRs"), ("q", "quit"))
    if page == "facts":
        return (("↑↓", "select"), ("←→", "pane"), ("/", "filter"), ("Esc", "back"), ("q", "quit"))
    if page == "sessions":
        return (("↑↓", "select"), ("←→", "pane"), ("r", "replay"), ("Esc", "back"), ("q", "quit"))
    if page == "dream":
        return (("d", "run"), ("←→", "pane"), ("l", "logs"), ("Esc", "back"), ("q", "quit"))
    if page == "prs":
        return (("↑↓", "PR"), ("a/b/c/d", "pick"), ("↵", "apply"), ("Esc", "back"), ("q", "quit"))
    return (("/", "search"), ("↑↓", "select"), ("←→", "pane"), ("Esc", "back"), ("q", "quit"))
