from __future__ import annotations

import html
import json
import socket
import threading
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path


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


def _summary_card(label: str, value: str) -> str:
    return (
        "<div class='card'>"
        f"<div class='card-label'>{html.escape(label)}</div>"
        f"<div class='card-value'>{html.escape(value)}</div>"
        "</div>"
    )


def _build_html(cwd: Path) -> str:
    from umx.dream.gates import read_dream_state
    from umx.memory import load_all_facts
    from umx.scope import project_memory_dir, user_memory_dir
    from umx.search import session_replay
    from umx.sessions import iter_session_payloads

    repo = project_memory_dir(cwd)
    user_repo = user_memory_dir()
    state = read_dream_state(repo)
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
    sessions = list(iter_session_payloads(repo, include_archived=True)) if repo.exists() else []
    manifest = _load_json(repo / "meta" / "manifest.json", {"topics": {}, "uncertainty_hotspots": [], "knowledge_gaps": []})
    gaps = _gap_rows(repo)
    lint_rows = _lint_rows(repo)

    topics: dict[str, list] = {}
    for fact in facts:
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

    fact_rows = "".join(
        "<tr>"
        f"<td>{html.escape(fact.scope.value)}</td>"
        f"<td>{html.escape(fact.topic)}</td>"
        f"<td>{fact.encoding_strength}</td>"
        f"<td>{html.escape(fact.verification.value)}</td>"
        f"<td>{html.escape(fact.source_type.value)}</td>"
        f"<td>{html.escape(fact.source_session)}</td>"
        f"<td><code>{html.escape(fact.fact_id)}</code></td>"
        f"<td>{html.escape(fact.text)}</td>"
        "</tr>"
        for fact in facts
    )

    conflicts = [fact for fact in facts if fact.conflicts_with]
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
            [fact for fact in facts if fact.task_status is not None],
            key=lambda fact: fact.created,
        )
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
                f"<li>{html.escape(str(item))}</li>"
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

    replay_html = ""
    for session_id, session_payload in sessions[-3:]:
        usage_rows = session_replay(repo, session_id, limit=200)
        if user_repo.exists():
            usage_rows.extend(session_replay(user_repo, session_id, limit=200))
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
  table {{ border-collapse: collapse; width: 100%; margin: 1em 0; background: white; }}
  th, td {{ border: 1px solid var(--line); padding: 0.45em 0.55em; text-align: left; vertical-align: top; }}
  th {{ background: #f1e6d5; }}
  code {{ background: #f4f0e8; padding: 0.1em 0.25em; border-radius: 4px; }}
  h1, h2, h3, h4 {{ margin-top: 0; }}
  .meta {{ color: var(--muted); margin-left: 0.5rem; font-size: 0.92em; }}
  .fact-text {{ font-weight: 600; }}
</style>
</head><body><div class="page">
<div class="hero">
<h1>UMX Memory Viewer</h1>
<p>Governed memory state across project and user scopes, with replay telemetry and derived review surfaces.</p>
<div class="cards">
{_summary_card("Project Facts", str(len(project_facts)))}
{_summary_card("User Facts", str(len(user_facts)))}
{_summary_card("Sessions", str(len(sessions)))}
{_summary_card("Open Tasks", str(len([fact for fact in facts if fact.task_status is not None and fact.task_status.value in ('open', 'blocked')])))}
{_summary_card("Last Dream", str(state.get('last_dream', 'never')))}
</div>
</div>
<div class="section"><h2>Fact Inventory</h2>
{('<table><tr><th>Scope</th><th>Topic</th><th>S</th><th>Verification</th><th>Source</th><th>Session</th><th>ID</th><th>Text</th></tr>' + fact_rows + '</table>') if fact_rows else '<p>No facts found.</p>'}
</div>
<div class="section"><h2>Manifest Coverage</h2>{manifest_html}</div>
<div class="section"><h2>Topic Narratives</h2>{topic_html if topic_html else '<p>No facts found.</p>'}</div>
<div class="section"><h2>Conflict Matrix</h2>{('<table><tr><th>ID</th><th>Topic</th><th>Conflicts With</th></tr>' + conflict_rows + '</table>') if conflict_rows else '<p>No active conflicts.</p>'}</div>
<div class="section"><h2>Task Timeline</h2>{('<table><tr><th>Date</th><th>Scope</th><th>Status</th><th>Task</th></tr>' + task_rows + '</table>') if task_rows else '<p>No task facts found.</p>'}</div>
<div class="section"><h2>Gap Proposals</h2>{gap_html}</div>
<div class="section"><h2>Lint Report</h2>{lint_html}</div>
<div class="section"><h2>Session Replay</h2>{replay_html if replay_html else '<p>No replay telemetry yet.</p>'}</div>
</div></body></html>"""


def start(cwd: Path, port: int | None = None) -> tuple[str, HTTPServer]:
    chosen_port = port or _find_free_port()
    html_content = _build_html(cwd)

    class Handler(SimpleHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(html_content.encode())

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
