from __future__ import annotations

import html
import json
import socket
import threading
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, quote, urlencode, urlparse

from umx.models import SourceType, Verification


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return default


def _gap_rows(repo: Path) -> list[dict]:
    path = repo / "meta" / "gaps.jsonl"
    if not path.exists():
        return []
    rows: list[dict] = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _lint_rows(repo: Path) -> list[str]:
    path = repo / "meta" / "lint-report.md"
    if not path.exists():
        return []
    return [
        line[2:].strip()
        for line in path.read_text().splitlines()
        if line.startswith("- ")
    ]


def _processing_rows(repo: Path) -> list[dict]:
    from umx.dream.processing import read_processing_log

    return read_processing_log(repo)[-20:]


def _summary_card(label: str, value: str) -> str:
    return (
        "<div class='card'>"
        f"<div class='card-label'>{html.escape(label)}</div>"
        f"<div class='card-value'>{html.escape(value)}</div>"
        "</div>"
    )


def _display_path(path: Path | None, *roots: Path) -> str:
    if path is None:
        return ""
    for root in roots:
        try:
            return path.relative_to(root).as_posix()
        except ValueError:
            continue
    return str(path)


def _coverage_signal_text(item: object) -> str:
    if isinstance(item, dict):
        topic = item.get("topic")
        reason = item.get("reason")
        if topic and reason:
            return f"{topic}: {reason}"
        if topic:
            return str(topic)
        return json.dumps(item, sort_keys=True)
    return str(item)


def _session_sort_key(entry: tuple[str, list[dict]] | tuple[str, list[dict], str]) -> tuple[str, str]:
    session_id = entry[0]
    payload = entry[1]
    meta = dict(payload[0].get("_meta", {})) if payload and "_meta" in payload[0] else {}
    return str(meta.get("started", "")), session_id


def _session_source_label(meta: dict[str, object], store_source: str) -> str:
    capture_source = str(meta.get("source", "")).strip()
    if not capture_source:
        return store_source
    return f"{store_source} · {capture_source}"


def _viewer_template(name: str) -> str:
    return (Path(__file__).with_name("templates") / name).read_text(encoding="utf-8")


def _render_quarantine_section(body: str) -> str:
    return _viewer_template("quarantine.html").format(quarantine_body=body)


def _normalize_min_strength(value: str | int | None) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if 1 <= parsed <= 5 else None


def _normalize_choice(value: str | None, allowed: set[str]) -> str | None:
    if value is None:
        return None
    candidate = value.strip()
    return candidate if candidate in allowed else None


def _apply_fact_filters(
    facts: list,
    *,
    min_strength: int | None,
    verification: str | None,
    source_type: str | None,
) -> list:
    filtered = list(facts)
    if min_strength is not None:
        filtered = [fact for fact in filtered if fact.encoding_strength >= min_strength]
    if verification is not None:
        filtered = [fact for fact in filtered if fact.verification.value == verification]
    if source_type is not None:
        filtered = [fact for fact in filtered if fact.source_type.value == source_type]
    return filtered


def _selected_attr(current: str | None, option: str) -> str:
    return " selected" if current == option else ""


def _filter_value(value: int | str | None) -> str:
    return "any" if value is None else str(value)


def _build_html(
    cwd: Path,
    *,
    notice: str | None = None,
    notice_kind: str = "info",
    min_strength: str | int | None = None,
    verification: str | None = None,
    source_type: str | None = None,
    history_fact: str | None = None,
    edit_fact: str | None = None,
) -> str:
    from umx.audit import audit_report
    from umx.calibration import build_calibration_advice
    from umx.config import load_config
    from umx.dream.gates import read_dream_state
    from umx.dream.processing import summarize_processing_log
    from umx.fact_actions import merge_conflicts_action
    from umx.governance_health import build_governance_health_payload
    from umx.metrics import compute_metrics, health_flags
    from umx.memory import find_fact_by_id, load_all_facts
    from umx.scope import config_path, project_memory_dir, user_memory_dir
    from umx.search import session_replay
    from umx.sessions import iter_session_payloads, list_quarantined_sessions
    from umx.tombstones import load_tombstones

    repo = project_memory_dir(cwd)
    user_repo = user_memory_dir()
    state = read_dream_state(repo)
    cfg = load_config(config_path())
    metrics = compute_metrics(repo, cfg)
    flags = health_flags(metrics)
    advice = build_calibration_advice(metrics, flags)
    governance = build_governance_health_payload(cwd, cfg)
    project_facts = load_all_facts(repo, include_superseded=False) if repo.exists() else []
    user_facts = load_all_facts(user_repo, include_superseded=False) if user_repo.exists() else []
    facts = sorted(
        [*project_facts, *user_facts],
        key=lambda fact: (
            fact.scope.value,
            fact.topic,
            fact.created,
            fact.fact_id,
        ),
    )
    selected_min_strength = _normalize_min_strength(min_strength)
    selected_verification = _normalize_choice(
        verification,
        {item.value for item in Verification},
    )
    selected_source_type = _normalize_choice(
        source_type,
        {item.value for item in SourceType},
    )
    filtered_facts = _apply_fact_filters(
        facts,
        min_strength=selected_min_strength,
        verification=selected_verification,
        source_type=selected_source_type,
    )
    active_query_params: dict[str, str] = {}
    if selected_min_strength is not None:
        active_query_params["min_strength"] = str(selected_min_strength)
    if selected_verification is not None:
        active_query_params["verification"] = selected_verification
    if selected_source_type is not None:
        active_query_params["source_type"] = selected_source_type
    project_sessions = list(iter_session_payloads(repo, include_archived=True)) if repo.exists() else []
    user_sessions = list(iter_session_payloads(user_repo, include_archived=True)) if user_repo.exists() else []
    sessions = sorted(
        [(session_id, payload, "project") for session_id, payload in project_sessions]
        + [(session_id, payload, "user") for session_id, payload in user_sessions],
        key=_session_sort_key,
        reverse=True,
    )
    quarantined = list_quarantined_sessions(repo, config=cfg) if repo.exists() else []
    manifest = _load_json(repo / "meta" / "manifest.json", {"topics": {}, "uncertainty_hotspots": [], "knowledge_gaps": []})
    gaps = _gap_rows(repo)
    lint_rows = _lint_rows(repo)
    processing_rows = _processing_rows(repo)
    processing = summarize_processing_log(repo, refs=("origin/main",))
    tombstones = [item for item in load_tombstones(repo) if not item.expired()] if repo.exists() else []
    audit = audit_report(repo, cfg) if repo.exists() else {"total_facts": 0, "total_sessions": 0, "sessions_with_derived_facts": 0, "source_types": {}}
    merge_preview = merge_conflicts_action(cwd, dry_run=True).results
    history_chain = []
    if history_fact:
        from umx.supersession import walk_history

        history_chain = walk_history(repo, history_fact) if repo.exists() else []
        if not history_chain and user_repo.exists():
            history_chain = walk_history(user_repo, history_fact)
    editable_fact = None
    if edit_fact:
        editable_fact = find_fact_by_id(repo, edit_fact) if repo.exists() else None
        if editable_fact is None and user_repo.exists():
            editable_fact = find_fact_by_id(user_repo, edit_fact)

    topics: dict[str, list] = {}
    for fact in filtered_facts:
        topics.setdefault(fact.topic, []).append(fact)

    topic_html = ""
    for topic, topic_facts in sorted(topics.items()):
        ordered = sorted(topic_facts, key=lambda fact: fact.created)
        items = "".join(
            "<li>"
            f"<span class='fact-text'>{html.escape(fact.text)}</span>"
            f"<span class='meta'>[{html.escape(fact.scope.value)} · S:{fact.encoding_strength} · {html.escape(fact.source_type.value)}]</span>"
            "</li>"
            for fact in ordered
        )
        topic_html += f"<h3>{html.escape(topic)}</h3><ol>{items}</ol>"

    def _fact_actions(fact) -> str:
        is_principle = (
            fact.file_path is not None
            and repo in fact.file_path.parents
            and fact.file_path.relative_to(repo).as_posix().startswith("principles/")
        )
        if fact.scope.value == "user":
            history_params = {**active_query_params, "history_fact": fact.fact_id}
            edit_params = {**active_query_params, "edit_fact": fact.fact_id}
            return (
                "<div class='action-stack'>"
                f"<form method='post'><input type='hidden' name='action' value='demote' />"
                f"<input type='hidden' name='fact_id' value='{html.escape(fact.fact_id)}' />"
                "<button type='submit'>Demote</button></form>"
                f"<a href='/?{html.escape(urlencode(history_params))}'>History</a>"
                f"<a href='/?{html.escape(urlencode(edit_params))}'>Edit</a>"
                "</div>"
            )
        fact_id = html.escape(fact.fact_id)
        history_href = f"/?{html.escape(urlencode({**active_query_params, 'history_fact': fact.fact_id}))}"
        edit_href = f"/?{html.escape(urlencode({**active_query_params, 'edit_fact': fact.fact_id}))}"
        return (
            "<div class='action-stack'>"
            f"<form method='post'><input type='hidden' name='action' value='confirm' />"
            f"<input type='hidden' name='fact_id' value='{fact_id}' />"
            "<button type='submit'>Confirm</button></form>"
            f"<form method='post'><input type='hidden' name='action' value='forget' />"
            f"<input type='hidden' name='fact_id' value='{fact_id}' />"
            "<button type='submit'>Forget</button></form>"
            f"<form method='post'><input type='hidden' name='action' value='promote' />"
            f"<input type='hidden' name='fact_id' value='{fact_id}' />"
            "<select name='destination'>"
            "<option value='user'>user</option>"
            "<option value='project'>project</option>"
            "<option value='principle'>principle</option>"
            "</select>"
            "<button type='submit'>Promote</button></form>"
            + (
                f"<form method='post'><input type='hidden' name='action' value='demote' />"
                f"<input type='hidden' name='fact_id' value='{fact_id}' />"
                "<button type='submit'>Demote</button></form>"
                if is_principle
                else ""
            )
            + f"<a href='{history_href}'>History</a>"
            + f"<a href='{edit_href}'>Edit</a>"
            + "</div>"
        )

    fact_rows = "".join(
        "<tr>"
        f"<td>{html.escape(fact.scope.value)}</td>"
        f"<td>{html.escape(fact.topic)}</td>"
        f"<td>{fact.encoding_strength}</td>"
        f"<td>{html.escape(fact.verification.value)}</td>"
        f"<td>{html.escape(fact.consolidation_status.value)}</td>"
        f"<td>{html.escape(fact.task_status.value if fact.task_status else '')}</td>"
        f"<td>{html.escape(fact.source_type.value)}</td>"
        f"<td>{html.escape(fact.source_session)}</td>"
        f"<td>{html.escape(_display_path(fact.file_path, repo, user_repo))}</td>"
        f"<td><code>{html.escape(fact.fact_id)}</code></td>"
        f"<td>{html.escape(fact.text)}</td>"
        f"<td>{_fact_actions(fact)}</td>"
        "</tr>"
        for fact in filtered_facts
    )

    conflicts = [fact for fact in filtered_facts if fact.conflicts_with]
    conflict_rows = "".join(
        "<tr>"
        f"<td>{html.escape(fact.fact_id)}</td>"
        f"<td>{html.escape(fact.topic)}</td>"
        f"<td>{html.escape(', '.join(fact.conflicts_with))}</td>"
        "</tr>"
        for fact in conflicts
    )

    task_rows = "".join(
        "<tr>"
        f"<td>{html.escape(fact.created.date().isoformat())}</td>"
        f"<td>{html.escape(fact.scope.value)}</td>"
        f"<td>{html.escape(fact.task_status.value if fact.task_status else '')}</td>"
        f"<td>{html.escape(fact.text)}</td>"
        "</tr>"
        for fact in sorted(
            [fact for fact in filtered_facts if fact.task_status is not None],
            key=lambda fact: fact.created,
        )
    )

    task_board_html = "<div class='board'>" + "".join(
        "<div class='board-column'>"
        f"<h3>{html.escape(status.title())} ({len(status_facts)})</h3>"
        + (
            "<ul>"
            + "".join(
                "<li>"
                f"<code>{html.escape(fact.fact_id)}</code> "
                f"{html.escape(fact.text)}"
                "</li>"
                for fact in status_facts
            )
            + "</ul>"
            if status_facts
            else "<p>No facts.</p>"
        )
        + "</div>"
        for status, status_facts in (
            (
                status,
                sorted(
                    [fact for fact in filtered_facts if fact.task_status and fact.task_status.value == status],
                    key=lambda fact: fact.created,
                    reverse=True,
                ),
            )
            for status in ("open", "blocked", "resolved", "abandoned")
        )
    ) + "</div>"

    filter_form_html = (
        "<form method='get' class='filter-form'>"
        "<label>Min strength "
        "<select name='min_strength'>"
        f"<option value='any'{_selected_attr(_filter_value(selected_min_strength), 'any')}>any</option>"
        + "".join(
            f"<option value='{value}'{_selected_attr(_filter_value(selected_min_strength), str(value))}>{value}</option>"
            for value in range(1, 6)
        )
        + "</select></label>"
        "<label>Verification "
        "<select name='verification'>"
        f"<option value='any'{_selected_attr(_filter_value(selected_verification), 'any')}>any</option>"
        + "".join(
            f"<option value='{html.escape(item.value)}'{_selected_attr(_filter_value(selected_verification), item.value)}>{html.escape(item.value)}</option>"
            for item in Verification
        )
        + "</select></label>"
        "<label>Source "
        "<select name='source_type'>"
        f"<option value='any'{_selected_attr(_filter_value(selected_source_type), 'any')}>any</option>"
        + "".join(
            f"<option value='{html.escape(item.value)}'{_selected_attr(_filter_value(selected_source_type), item.value)}>{html.escape(item.value)}</option>"
            for item in SourceType
        )
        + "</select></label>"
        "<button type='submit'>Filter</button> "
        "<a href='/'>Reset</a>"
        "</form>"
        f"<p class='meta'>Showing {len(filtered_facts)} of {len(facts)} facts.</p>"
    )

    history_html = "<p>Select a fact from the inventory to inspect its supersession chain.</p>"
    if history_fact:
        if history_chain:
            history_html = (
                "<ol>"
                + "".join(
                    "<li>"
                    f"<code>{html.escape(fact.fact_id)}</code> "
                    f"[{html.escape(fact.consolidation_status.value)}] "
                    f"{html.escape(fact.text)}"
                    "</li>"
                    for fact in history_chain
                )
                + "</ol>"
            )
        else:
            history_html = "<p>No fact history found for that selection.</p>"

    edit_html = "<p>Select a fact from the inventory to edit it and create an S:5 superseding version.</p>"
    if edit_fact:
        if editable_fact is None:
            edit_html = "<p>No fact found for that selection.</p>"
        else:
            edit_html = (
                "<form method='post' class='edit-form'>"
                "<input type='hidden' name='action' value='edit' />"
                f"<input type='hidden' name='fact_id' value='{html.escape(editable_fact.fact_id)}' />"
                f"<p><code>{html.escape(editable_fact.fact_id)}</code> "
                f"[{html.escape(editable_fact.scope.value)} · {html.escape(editable_fact.topic)}]</p>"
                f"<textarea name='updated_text'>{html.escape(editable_fact.text)}</textarea>"
                "<button type='submit'>Save as S:5 edit</button>"
                "</form>"
            )

    manifest_html = (
        "<div class='split'>"
        "<div>"
        "<h3>Topics</h3>"
        + (
            "<ul>" + "".join(f"<li>{html.escape(topic)}</li>" for topic in sorted(manifest.get("topics", {}))) + "</ul>"
            if manifest.get("topics")
            else "<p>No manifest topics yet.</p>"
        )
        + "</div>"
        "<div>"
        "<h3>Coverage Signals</h3>"
        + (
            "<ul>"
            + "".join(
                f"<li>{html.escape(_coverage_signal_text(item))}</li>"
                for item in [*manifest.get("uncertainty_hotspots", []), *manifest.get("knowledge_gaps", [])]
            )
            + "</ul>"
            if manifest.get("uncertainty_hotspots") or manifest.get("knowledge_gaps")
            else "<p>No coverage signals recorded.</p>"
        )
        + "</div>"
        "</div>"
    )

    gap_html = (
        "<table><tr><th>Query</th><th>Resolution Context</th><th>Proposed Fact</th><th>Session</th></tr>"
        + "".join(
            "<tr>"
            f"<td>{html.escape(str(row.get('query', '')))}</td>"
            f"<td>{html.escape(str(row.get('resolution_context', '')))}</td>"
            f"<td>{html.escape(str(row.get('proposed_fact', '')))}</td>"
            f"<td>{html.escape(str(row.get('session', '')))}</td>"
            "</tr>"
            for row in gaps
        )
        + "</table>"
        if gaps
        else "<p>No gap proposals.</p>"
    )

    lint_html = (
        "<ul>" + "".join(f"<li>{html.escape(row)}</li>" for row in lint_rows) + "</ul>"
        if lint_rows
        else "<p>No lint findings.</p>"
    )
    processing_html = (
        "<div class='cards'>"
        + _summary_card("Active Runs", str(processing.get("active_runs", 0)))
        + _summary_card("Last Completed", str((processing.get("last_completed") or {}).get("ts", "never")))
        + "</div>"
        + (
            "<table><tr><th>Time</th><th>Event</th><th>Mode</th><th>Branch</th><th>Run</th><th>Message</th></tr>"
            + "".join(
                "<tr>"
                f"<td>{html.escape(str(row.get('ts', '')))}</td>"
                f"<td>{html.escape(str(row.get('event', '')))}</td>"
                f"<td>{html.escape(str(row.get('mode', '')))}</td>"
                f"<td>{html.escape(str(row.get('branch', '')))}</td>"
                f"<td>{html.escape(str(row.get('run_id', '')))}</td>"
                f"<td>{html.escape(str(row.get('message', row.get('error', ''))))}</td>"
                "</tr>"
                for row in reversed(processing_rows)
            )
            + "</table>"
        )
        if processing_rows
        else "<p>No processing history yet.</p>"
    )
    health_rows = "".join(
        "<tr>"
        f"<td>{html.escape(metric_name.replace('_', ' '))}</td>"
        f"<td>{html.escape(str(metric.get('value', '')))}</td>"
        f"<td>{html.escape(str(metric.get('status', '')))}</td>"
        f"<td>{html.escape(str(metric.get('signal', '')))}</td>"
        "</tr>"
        for metric_name, metric in metrics.items()
    )
    health_html = (
        "<div class='cards'>"
        + _summary_card("Health", "ok" if not flags else "warn")
        + _summary_card("Flags", str(len(flags)))
        + _summary_card(
            "Hot Tier",
            f"{int(round(metrics['hot_tier_utilisation']['value'] * 100))}%",
        )
        + "</div>"
        + (
            "<ul>" + "".join(f"<li>{html.escape(flag)}</li>" for flag in flags) + "</ul>"
            if flags
            else "<p>No active health warnings.</p>"
        )
        + (
            "<h3>Recommended Actions</h3><ul>"
            + "".join(
                "<li>"
                f"<strong>{html.escape(item['metric'].replace('_', ' '))}</strong>: "
                + "; ".join(html.escape(action) for action in item["recommended_actions"])
                + "</li>"
                for item in advice
            )
            + "</ul>"
            if advice
            else ""
        )
        + "<table><tr><th>Metric</th><th>Value</th><th>Status</th><th>Signal</th></tr>"
        + health_rows
        + "</table>"
    )
    governance_summary = governance.get("summary", {})
    governance_pr_rows = "".join(
        "<tr>"
        f"<td><a href='{html.escape(str(item.get('url', '')), quote=True)}'>"
        f"#{html.escape(str(item.get('number', '')))}</a></td>"
        f"<td>{html.escape(str(item.get('title', '')))}</td>"
        f"<td><code>{html.escape(str(item.get('head_ref', '')))}</code></td>"
        f"<td>{html.escape(str(item.get('state') or 'unknown'))}</td>"
        f"<td>{html.escape(', '.join(str(label) for label in item.get('labels', [])))}</td>"
        "</tr>"
        for item in governance.get("open_prs", [])
    )
    governance_branch_rows = "".join(
        "<tr>"
        f"<td><code>{html.escape(str(item.get('name', '')))}</code></td>"
        f"<td>{html.escape(str(item.get('age_days', '')))}</td>"
        f"<td>{html.escape(str(item.get('last_commit_ts', '')))}</td>"
        f"<td>{'yes' if item.get('current') else 'no'}</td>"
        "</tr>"
        for item in governance.get("stale_branches", [])
    )
    governance_drift_rows = "".join(
        "<tr>"
        f"<td><a href='{html.escape(str(item.get('url', '')), quote=True)}'>"
        f"#{html.escape(str(item.get('number', '')))}</a></td>"
        f"<td><code>{html.escape(str(item.get('head_ref', '')))}</code></td>"
        f"<td>{html.escape('; '.join(str(issue) for issue in item.get('issues', [])))}</td>"
        "</tr>"
        for item in governance.get("label_drift", [])
    )
    governance_last_l2 = governance.get("last_l2_review") or {}
    governance_flags = governance.get("flags", [])
    governance_errors = governance.get("errors", [])
    pr_inventory_available = bool(governance_summary.get("pr_inventory_available", True))
    governance_pr_count = (
        str(governance_summary.get("open_governance_prs", 0))
        if pr_inventory_available
        else "unknown"
    )
    governance_reviewer_count = (
        str(governance_summary.get("reviewer_queue_depth", 0))
        if pr_inventory_available
        else "unknown"
    )
    governance_human_count = (
        str(governance_summary.get("human_review_queue_depth", 0))
        if pr_inventory_available
        else "unknown"
    )
    governance_stale_count = (
        str(governance_summary.get("stale_branch_count", 0))
        if pr_inventory_available
        else "unknown"
    )
    governance_drift_count = (
        str(governance_summary.get("label_drift_count", 0))
        if pr_inventory_available
        else "unknown"
    )
    governance_html = (
        "<div class='cards'>"
        + _summary_card("Governance", "ok" if governance.get("ok") else "warn")
        + _summary_card("Open PRs", governance_pr_count)
        + _summary_card("Awaiting L2", governance_reviewer_count)
        + _summary_card("Human Review", governance_human_count)
        + _summary_card("Stale Branches", governance_stale_count)
        + _summary_card("Label Drift", governance_drift_count)
        + "</div>"
    )
    if not governance.get("governed"):
        governance_html += (
            f"<p>Governance health is inactive for sync mode "
            f"{html.escape(str(governance.get('mode', 'unknown')))}.</p>"
        )
    else:
        governance_html += (
            "<ul>" + "".join(f"<li>{html.escape(flag)}</li>" for flag in governance_flags) + "</ul>"
            if governance_flags
            else "<p>No active governance warnings.</p>"
        )
        governance_html += (
            "<h3>Errors</h3><ul>"
            + "".join(f"<li>{html.escape(item)}</li>" for item in governance_errors)
            + "</ul>"
            if governance_errors
            else ""
        )
        governance_html += (
            "<h3>Last L2 Review</h3><table><tr><th>Time</th><th>Action</th><th>Status</th>"
            "<th>PR</th><th>Reviewer</th><th>Model</th></tr><tr>"
            f"<td>{html.escape(str(governance_last_l2.get('ts', 'never')))}</td>"
            f"<td>{html.escape(str(governance_last_l2.get('action', '')))}</td>"
            f"<td>{html.escape(str(governance_last_l2.get('status', '')))}</td>"
            f"<td>{html.escape(str(governance_last_l2.get('pr_number', '')))}</td>"
            f"<td>{html.escape(str(governance_last_l2.get('reviewed_by', '')))}</td>"
            f"<td>{html.escape(str(governance_last_l2.get('review_model', '')))}</td>"
            "</tr></table>"
            if governance_last_l2
            else "<p>No L2 review completions recorded.</p>"
        )
        governance_html += (
            "<h3>Open Governance PRs</h3><table><tr><th>PR</th><th>Title</th><th>Branch</th>"
            "<th>State</th><th>Labels</th></tr>"
            + governance_pr_rows
            + "</table>"
            if governance_pr_rows
            else "<p>No open governance PRs.</p>"
        )
        governance_html += (
            "<h3>Stale Local Branches</h3><table><tr><th>Branch</th><th>Age (days)</th>"
            "<th>Last Commit</th><th>Current</th></tr>"
            + governance_branch_rows
            + "</table>"
            if governance_branch_rows
            else "<p>No stale local governance branches.</p>"
        )
        governance_html += (
            "<h3>Label Drift</h3><table><tr><th>PR</th><th>Branch</th><th>Issues</th></tr>"
            + governance_drift_rows
            + "</table>"
            if governance_drift_rows
            else "<p>No open governance PR label drift detected.</p>"
        )
    merge_preview_html = (
        "<table><tr><th>Winner</th><th>Loser</th><th>Reason</th></tr>"
        + "".join(
            "<tr>"
            f"<td><code>{html.escape(str(item.get('winner_id', '')))}</code></td>"
            f"<td><code>{html.escape(str(item.get('loser_id', '')))}</code></td>"
            f"<td>{html.escape(str(item.get('reason', '')))}</td>"
            "</tr>"
            for item in merge_preview
        )
        + "</table>"
        + "<form method='post'><input type='hidden' name='action' value='merge' />"
        "<button type='submit'>Apply suggested resolutions</button></form>"
        if merge_preview
        else "<p>No merge suggestions.</p>"
    )
    notice_html = (
        f"<div class='notice notice-{html.escape(notice_kind)}'>{html.escape(notice)}</div>"
        if notice
        else ""
    )

    tombstone_rows = "".join(
        "<tr>"
        f"<td>{html.escape(item.fact_id or '')}</td>"
        f"<td>{html.escape(item.match or '')}</td>"
        f"<td>{html.escape(item.reason)}</td>"
        f"<td>{html.escape(item.author)}</td>"
        f"<td>{html.escape(item.created)}</td>"
        f"<td>{html.escape(item.expires_at or '')}</td>"
        f"<td>{html.escape(', '.join(item.suppress_from))}</td>"
        "</tr>"
        for item in tombstones
    )

    audit_source_rows = "".join(
        "<tr>"
        f"<td>{html.escape(source_type)}</td>"
        f"<td>{count}</td>"
        "</tr>"
        for source_type, count in sorted(audit.get("source_types", {}).items())
    )

    audit_fact_rows = "".join(
        "<tr>"
        f"<td><code>{html.escape(fact.fact_id)}</code></td>"
        f"<td>{html.escape(fact.topic)}</td>"
        f"<td>{html.escape(fact.scope.value)}</td>"
        f"<td>{html.escape(fact.source_session)}</td>"
        f"<td>{html.escape(fact.provenance.extracted_by)}</td>"
        f"<td>{html.escape(fact.provenance.approval_tier or '')}</td>"
        f"<td>{html.escape(fact.provenance.pr or '')}</td>"
        f"<td>{html.escape(_display_path(fact.file_path, repo, user_repo))}</td>"
        "</tr>"
        for fact in sorted(facts, key=lambda fact: (fact.created, fact.fact_id), reverse=True)
    )

    session_browser_rows = "".join(
        "<tr>"
        f"<td><code>{html.escape(session_id)}</code></td>"
        f"<td>{html.escape(str(meta.get('tool', '')))}</td>"
        f"<td>{html.escape(str(meta.get('machine', '')))}</td>"
        f"<td>{html.escape(_session_source_label(meta, _source))}</td>"
        f"<td>{html.escape(str(meta.get('started', '')))}</td>"
        f"<td>{len(events)}</td>"
        f"<td>{html.escape(snippet)}</td>"
        "</tr>"
        for session_id, payload, _source in sessions
        for meta, events, snippet in [(
            dict(payload[0].get("_meta", {})) if payload and "_meta" in payload[0] else {},
            [event for event in payload if "_meta" not in event],
            next(
                (
                    str(event.get("content", ""))[:120]
                    for event in payload
                    if "_meta" not in event and str(event.get("content", "")).strip()
                ),
                "",
            ),
        )]
    )

    quarantine_rows = "".join(
        "<tr>"
        f"<td><code>{html.escape(entry.session_id)}</code></td>"
        f"<td>{html.escape(entry.tool or '')}</td>"
        f"<td>{html.escape(entry.started or '')}</td>"
        f"<td>{html.escape(entry.quarantined_at or '')}</td>"
        f"<td>{html.escape(entry.reason)}</td>"
        f"<td>{html.escape(entry.snippet)}</td>"
        "<td><div class='action-stack'>"
        f"<form method='post'><input type='hidden' name='action' value='release-quarantine' />"
        f"<input type='hidden' name='session_id' value='{html.escape(entry.session_id)}' />"
        "<label><input type='checkbox' name='confirm_release' value='yes' /> confirm release</label>"
        "<button type='submit'>Release</button></form>"
        f"<form method='post'><input type='hidden' name='action' value='discard-quarantine' />"
        f"<input type='hidden' name='session_id' value='{html.escape(entry.session_id)}' />"
        "<button type='submit'>Discard</button></form>"
        "</div></td>"
        "</tr>"
        for entry in quarantined
    )
    quarantine_html = _render_quarantine_section(
        (
            "<table><tr><th>Session</th><th>Tool</th><th>Started</th><th>Quarantined</th><th>Reason</th><th>Masked Preview</th><th>Actions</th></tr>"
            + quarantine_rows
            + "</table>"
        )
        if quarantine_rows
        else "<p>No quarantined sessions.</p>"
    )

    conventions_path = repo / "CONVENTIONS.md"
    conventions_html = (
        f"<pre class='viewer-pre'>{html.escape(conventions_path.read_text())}</pre>"
        if conventions_path.exists()
        else "<p>No project conventions file found.</p>"
    )

    replay_html = ""
    for session_id, session_payload, session_source in sessions[:3]:
        replay_repo = repo if session_source == "project" else user_repo
        usage_rows = session_replay(replay_repo, session_id, limit=200) if replay_repo.exists() else []
        usage_rows.sort(key=lambda row: (str(row.get("created_at", "")), str(row.get("event_id", ""))))
        session_rows = [
            event
            for event in session_payload
            if "_meta" not in event
        ]
        replay_rows = "".join(
            "<tr>"
            f"<td>{html.escape(str(row.get('ts', '')))}</td>"
            f"<td>{html.escape(str(row.get('role', '')))}</td>"
            f"<td colspan='4'>{html.escape(str(row.get('content', ''))[:200])}</td>"
            "</tr>"
            for row in session_rows
        )
        usage_table = "".join(
            "<tr>"
            f"<td>{html.escape(str(row.get('turn_index', 0)))}</td>"
            f"<td>{html.escape(str(row.get('event_kind', '')))}</td>"
            f"<td>{html.escape(str(row.get('injection_point', '')))}</td>"
            f"<td>{html.escape(str(row.get('fact_id', '')))}</td>"
            f"<td>{html.escape(str(row.get('disclosure_level', '')))}</td>"
            f"<td>{'yes' if row.get('used_in_output') else 'no'}</td>"
            "</tr>"
            for row in usage_rows
        )
        replay_html += (
            f"<h3>{html.escape(session_id)}</h3>"
            "<h4>Session events</h4>"
            + (
                "<table><tr><th>Time</th><th>Role</th><th colspan='4'>Content</th></tr>"
                f"{replay_rows}</table>"
                if replay_rows
                else "<p>No session events.</p>"
            )
            + "<h4>Memory telemetry</h4>"
            + (
                "<table><tr><th>Turn</th><th>Event</th><th>Point</th><th>ID</th><th>Disclosure</th><th>Used</th></tr>"
                f"{usage_table}</table>"
                if usage_table
                else "<p>No replay telemetry yet.</p>"
            )
        )

    return f"""<!DOCTYPE html>
<html><head><title>UMX Memory Viewer</title>
<style>
  :root {{
    --bg: #f5f1e8;
    --panel: #fffaf2;
    --ink: #1e2a24;
    --muted: #647067;
    --line: #d6ccbd;
    --accent: #2e6f57;
    --accent-soft: #d9efe6;
  }}
  * {{ box-sizing: border-box; }}
  body {{ font-family: Georgia, "Iowan Old Style", serif; background: linear-gradient(180deg, #f2ecdf 0%, #fbf8f2 100%); color: var(--ink); margin: 0; }}
  .page {{ max-width: 1180px; margin: 0 auto; padding: 24px; }}
  .hero {{ background: radial-gradient(circle at top left, #ffffff 0%, #f8f3ea 65%, #eee4d5 100%); border: 1px solid var(--line); border-radius: 20px; padding: 24px; box-shadow: 0 10px 30px rgba(72, 54, 27, 0.08); }}
  .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; margin: 18px 0 8px; }}
  .card {{ background: var(--panel); border: 1px solid var(--line); border-radius: 16px; padding: 14px; }}
  .card-label {{ color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: 0.08em; }}
  .card-value {{ font-size: 28px; margin-top: 6px; }}
  .section {{ background: rgba(255, 250, 242, 0.92); border: 1px solid var(--line); border-radius: 18px; padding: 18px; margin-top: 16px; }}
  .split {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 18px; }}
  .board {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px; }}
  .board-column {{ background: var(--panel); border: 1px solid var(--line); border-radius: 16px; padding: 14px; }}
  .board-column ul {{ margin: 0; padding-left: 1.1rem; }}
  .action-stack {{ display: flex; flex-direction: column; gap: 6px; min-width: 140px; }}
  form {{ margin: 0; }}
  button, select {{ font: inherit; }}
  button {{ border: 1px solid var(--line); border-radius: 8px; background: white; padding: 0.25rem 0.55rem; cursor: pointer; }}
  select {{ border: 1px solid var(--line); border-radius: 8px; padding: 0.2rem 0.35rem; background: white; }}
  .filter-form {{ display: flex; flex-wrap: wrap; gap: 12px; align-items: end; margin-bottom: 10px; }}
  .filter-form label {{ display: flex; flex-direction: column; gap: 4px; }}
  .edit-form {{ display: flex; flex-direction: column; gap: 10px; }}
  .edit-form textarea {{ width: 100%; min-height: 7rem; font: inherit; border: 1px solid var(--line); border-radius: 8px; padding: 0.6rem; background: white; }}
  .notice {{ margin-top: 16px; padding: 12px 14px; border-radius: 12px; border: 1px solid var(--line); }}
  .notice-success {{ background: var(--accent-soft); }}
  .notice-error {{ background: #fde8e8; }}
  .notice-info {{ background: #eef4ff; }}
  table {{ border-collapse: collapse; width: 100%; margin: 1em 0; background: white; }}
  th, td {{ border: 1px solid var(--line); padding: 0.45em 0.55em; text-align: left; vertical-align: top; }}
  th {{ background: #f1e6d5; }}
  code {{ background: #f4f0e8; padding: 0.1em 0.25em; border-radius: 4px; }}
  h1, h2, h3, h4 {{ margin-top: 0; }}
  .meta {{ color: var(--muted); margin-left: 0.5rem; font-size: 0.92em; }}
  .fact-text {{ font-weight: 600; }}
  .viewer-pre {{ white-space: pre-wrap; overflow-x: auto; background: white; border: 1px solid var(--line); border-radius: 12px; padding: 12px; }}
</style>
</head><body><div class="page">
<div class="hero">
<h1>UMX Memory Viewer</h1>
<p>Governed memory state across project and user scopes, with replay telemetry and derived review surfaces.</p>
{notice_html}
<div class="cards">
{_summary_card("Project Facts", str(len(project_facts)))}
{_summary_card("User Facts", str(len(user_facts)))}
{_summary_card("Sessions", str(len(sessions)))}
{_summary_card("Open Tasks", str(len([fact for fact in facts if fact.task_status is not None and fact.task_status.value in ('open', 'blocked')])))}
{_summary_card("Tombstones", str(len(tombstones)))}
{_summary_card("Health", 'ok' if not flags else 'warn')}
{_summary_card("Last Dream", str(state.get('last_dream', 'never')))}
</div>
</div>
<div class="section"><h2>Fact Inventory</h2>
{filter_form_html}
{('<table><tr><th>Scope</th><th>Topic</th><th>S</th><th>Verification</th><th>State</th><th>Task</th><th>Source</th><th>Session</th><th>File</th><th>ID</th><th>Text</th><th>Actions</th></tr>' + fact_rows + '</table>') if fact_rows else '<p>No facts found.</p>'}
</div>
<div class="section"><h2>Fact History</h2>{history_html}</div>
<div class="section"><h2>Inline Edit</h2>{edit_html}</div>
<div class="section"><h2>Manifest Coverage</h2>{manifest_html}</div>
<div class="section"><h2>Topic Narratives</h2>{topic_html if topic_html else '<p>No facts found.</p>'}</div>
<div class="section"><h2>Conflict Matrix</h2>{('<table><tr><th>ID</th><th>Topic</th><th>Conflicts With</th></tr>' + conflict_rows + '</table>') if conflict_rows else '<p>No active conflicts.</p>'}</div>
<div class="section"><h2>Conflict Actions</h2>{merge_preview_html}</div>
<div class="section"><h2>Task Board</h2>{task_board_html}</div>
<div class="section"><h2>Task Timeline</h2>{('<table><tr><th>Date</th><th>Scope</th><th>Status</th><th>Task</th></tr>' + task_rows + '</table>') if task_rows else '<p>No task facts found.</p>'}</div>
<div class="section"><h2>Tombstones</h2>{('<table><tr><th>Fact ID</th><th>Match</th><th>Reason</th><th>Author</th><th>Created</th><th>Expires</th><th>Suppress From</th></tr>' + tombstone_rows + '</table>') if tombstone_rows else '<p>No active tombstones.</p>'}</div>
<div class="section"><h2>Audit View</h2>
<div class="cards">
{_summary_card("Audit Facts", str(audit.get('total_facts', 0)))}
{_summary_card("Audit Sessions", str(audit.get('total_sessions', 0)))}
{_summary_card("Derived Sessions", str(audit.get('sessions_with_derived_facts', 0)))}
</div>
{('<table><tr><th>Source Type</th><th>Count</th></tr>' + audit_source_rows + '</table>') if audit_source_rows else '<p>No audit source breakdown yet.</p>'}
{('<table><tr><th>ID</th><th>Topic</th><th>Scope</th><th>Session</th><th>Extracted By</th><th>Tier</th><th>PR</th><th>File</th></tr>' + audit_fact_rows + '</table>') if audit_fact_rows else '<p>No facts available for audit.</p>'}
</div>
{quarantine_html}
<div class="section"><h2>Session Browser</h2>{('<table><tr><th>Session</th><th>Tool</th><th>Machine</th><th>Source</th><th>Started</th><th>Events</th><th>Preview</th></tr>' + session_browser_rows + '</table>') if session_browser_rows else '<p>No sessions recorded yet.</p>'}</div>
<div class="section"><h2>Gap Proposals</h2>{gap_html}</div>
<div class="section"><h2>Lint Report</h2>{lint_html}</div>
<div class="section"><h2>Processing Log</h2>{processing_html}</div>
<div class="section"><h2>Health Signals</h2>{health_html}</div>
<div class="section"><h2>Governance Health</h2>{governance_html}</div>
<div class="section"><h2>Conventions</h2>{conventions_html}</div>
<div class="section"><h2>Session Replay</h2>{replay_html if replay_html else '<p>No replay telemetry yet.</p>'}</div>
</div></body></html>"""


def start(cwd: Path, port: int | None = None) -> tuple[str, HTTPServer]:
    chosen_port = port or _find_free_port()

    class Handler(SimpleHTTPRequestHandler):
        def _render(
            self,
            *,
            notice: str | None = None,
            notice_kind: str = "info",
            min_strength: str | None = None,
            verification: str | None = None,
            source_type: str | None = None,
            history_fact: str | None = None,
            edit_fact: str | None = None,
        ) -> None:
            html_content = _build_html(
                cwd,
                notice=notice,
                notice_kind=notice_kind,
                min_strength=min_strength,
                verification=verification,
                source_type=source_type,
                history_fact=history_fact,
                edit_fact=edit_fact,
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(html_content.encode())

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            self._render(
                notice=params.get("notice", [None])[0],
                notice_kind=params.get("kind", ["info"])[0],
                min_strength=params.get("min_strength", [None])[0],
                verification=params.get("verification", [None])[0],
                source_type=params.get("source_type", [None])[0],
                history_fact=params.get("history_fact", [None])[0],
                edit_fact=params.get("edit_fact", [None])[0],
            )

        def do_POST(self) -> None:
            from umx.fact_actions import (
                confirm_fact_action,
                demote_fact_action,
                edit_fact_action,
                forget_fact_action,
                merge_conflicts_action,
                promote_fact_action,
            )
            from umx.scope import project_memory_dir
            from umx.sessions import discard_quarantined_session, release_quarantined_session

            length = int(self.headers.get("Content-Length", "0"))
            payload = parse_qs(self.rfile.read(length).decode())
            current_params = parse_qs(urlparse(self.path).query)
            action = payload.get("action", [""])[0]
            notice = "Unknown action"
            kind = "error"

            if action == "confirm":
                result = confirm_fact_action(cwd, payload.get("fact_id", [""])[0])
                notice = result.message
                kind = "success" if result.ok else "error"
            elif action == "forget":
                result = forget_fact_action(cwd, payload.get("fact_id", [""])[0])
                notice = result.message
                kind = "success" if result.ok else "error"
            elif action == "promote":
                result = promote_fact_action(
                    cwd,
                    payload.get("fact_id", [""])[0],
                    payload.get("destination", [""])[0],
                )
                notice = result.message
                kind = "success" if result.ok else "error"
            elif action == "merge":
                result = merge_conflicts_action(cwd, dry_run=False)
                notice = result.message
                kind = "success" if result.ok else "error"
            elif action == "edit":
                result = edit_fact_action(
                    cwd,
                    payload.get("fact_id", [""])[0],
                    payload.get("updated_text", [""])[0],
                )
                notice = result.message
                kind = "success" if result.ok else "error"
            elif action == "demote":
                result = demote_fact_action(cwd, payload.get("fact_id", [""])[0])
                notice = result.message
                kind = "success" if result.ok else "error"
            elif action == "release-quarantine":
                result = release_quarantined_session(
                    project_memory_dir(cwd),
                    payload.get("session_id", [""])[0],
                    confirm=payload.get("confirm_release", [""])[0] == "yes",
                )
                notice = result.message
                kind = "success" if result.ok else "error"
            elif action == "discard-quarantine":
                result = discard_quarantined_session(
                    project_memory_dir(cwd),
                    payload.get("session_id", [""])[0],
                )
                notice = result.message
                kind = "success" if result.ok else "error"

            redirect_params = {
                key: current_params[key][0]
                for key in ("min_strength", "verification", "source_type", "history_fact", "edit_fact")
                if current_params.get(key)
            }
            if action == "edit" and result.ok and result.fact_id:
                redirect_params["history_fact"] = result.fact_id
                redirect_params["edit_fact"] = result.fact_id
            redirect_params["notice"] = notice
            redirect_params["kind"] = kind
            self.send_response(303)
            self.send_header("Location", f"/?{urlencode(redirect_params)}")
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            pass  # silence logs

    server = HTTPServer(("127.0.0.1", chosen_port), Handler)
    url = f"http://127.0.0.1:{chosen_port}"
    return url, server


def run(cwd: Path, port: int | None = None) -> str:
    url, server = start(cwd, port)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return url
