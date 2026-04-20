from __future__ import annotations

from pathlib import Path
import threading
from urllib.parse import urlencode
from urllib.request import urlopen

import json
from unittest.mock import patch

from tests.secret_literals import OPENAI_KEY_SHORT
from umx.config import default_config, save_config
from umx.hooks.assistant_output import run as assistant_output_run
from umx.inject import build_injection_block
from umx.memory import add_fact, load_all_facts
from umx.models import (
    ConsolidationStatus,
    Fact,
    MemoryType,
    Provenance,
    Scope,
    SourceType,
    TaskStatus,
    Verification,
)
from umx.scope import config_path
from umx.sessions import (
    quarantine_decision_log_path,
    quarantine_metadata_path,
    quarantine_path,
    read_session,
    session_path,
    write_session,
)
from umx.tombstones import forget_fact
from umx.viewer.server import _build_html, start as start_viewer


def _start_test_viewer(cwd: Path):
    url, server = start_viewer(cwd)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return url, server, thread


def _write_quarantined_session(
    project_repo: Path,
    *,
    session_id: str,
    content: str,
    reason: str = "invalid redaction pattern '['",
) -> None:
    quarantine = quarantine_path(project_repo, session_id)
    quarantine.parent.mkdir(parents=True, exist_ok=True)
    quarantine.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "_meta": {
                            "session_id": session_id,
                            "tool": "codex",
                            "started": "2026-04-11T00:00:00Z",
                        }
                    },
                    sort_keys=True,
                ),
                json.dumps({"role": "user", "content": content}, sort_keys=True),
            ]
        )
        + "\n"
    )
    quarantine_metadata_path(project_repo, session_id).write_text(
        json.dumps(
            {
                "kind": "session",
                "session_id": session_id,
                "quarantined_at": "2026-04-11T00:00:01Z",
                "reason": reason,
                "tool": "codex",
                "started": "2026-04-11T00:00:00Z",
            },
            sort_keys=True,
        )
        + "\n"
    )


def test_viewer_replay_combines_session_events_and_cross_scope_telemetry(
    project_dir: Path, project_repo: Path, user_repo: Path
) -> None:
    project_fact = Fact(
        fact_id="01TESTFACT0000000000000400",
        text="postgres runs on 5433 in dev",
        scope=Scope.PROJECT,
        topic="devenv",
        encoding_strength=4,
        memory_type=MemoryType.EXPLICIT_SEMANTIC,
        verification=Verification.CORROBORATED,
        source_type=SourceType.GROUND_TRUTH_CODE,
        source_tool="codex",
        source_session="sess-view-001",
        consolidation_status=ConsolidationStatus.STABLE,
    )
    user_fact = Fact(
        fact_id="01TESTFACT0000000000000401",
        text="prefer concise release notes",
        scope=Scope.USER,
        topic="writing",
        encoding_strength=5,
        memory_type=MemoryType.EXPLICIT_SEMANTIC,
        verification=Verification.HUMAN_CONFIRMED,
        source_type=SourceType.USER_PROMPT,
        source_tool="human",
        source_session="sess-view-001",
        consolidation_status=ConsolidationStatus.STABLE,
    )
    add_fact(project_repo, project_fact)
    add_fact(user_repo, user_fact)

    build_injection_block(project_dir, prompt="postgres release notes", session_id="sess-view-001")
    assistant_output_run(
        cwd=project_dir,
        session_id="sess-view-001",
        tool="copilot",
        event={
            "role": "assistant",
            "content": "Use postgres runs on 5433 in dev and prefer concise release notes.",
        },
    )

    html = _build_html(project_dir)

    assert "Session events" in html
    assert "Memory telemetry" in html
    assert "Use postgres runs on 5433 in dev and prefer concise release notes." in html
    assert project_fact.fact_id in html
    assert user_fact.fact_id in html


def test_viewer_surfaces_manifest_gaps_and_lint(project_dir: Path, project_repo: Path) -> None:
    (project_repo / "meta" / "manifest.json").write_text(
        json.dumps(
            {
                "topics": {"devenv": {"facts": 1}},
                "uncertainty_hotspots": ["deploys"],
                "knowledge_gaps": ["postgres backups"],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    (project_repo / "meta" / "gaps.jsonl").write_text(
        json.dumps(
            {
                "query": "postgres backups",
                "resolution_context": "agent inspected ops docs",
                "proposed_fact": "backups run nightly at 02:00 UTC",
                "session": "sess-gap-001",
            },
            sort_keys=True,
        )
        + "\n"
    )
    (project_repo / "meta" / "lint-report.md").write_text(
        "# Lint Report\n\n- **reverify** fact-123 has not been re-grounded to code in over 90 days\n"
    )
    (project_repo / "meta" / "processing.jsonl").write_text(
        json.dumps(
            {
                "run_id": "dream-test-run",
                "event": "started",
                "status": "running",
                "mode": "remote",
                "branch": "main",
                "ts": "2026-04-15T01:00:00Z",
            },
            sort_keys=True,
        )
        + "\n"
    )

    html = _build_html(project_dir)

    assert "Manifest Coverage" in html
    assert "Gap Proposals" in html
    assert "Lint Report" in html
    assert "Processing Log" in html
    assert "postgres backups" in html
    assert "dream-test-run" in html
    assert "fact-123 has not been re-grounded to code" in html


def test_viewer_surfaces_health_flags(project_dir: Path, project_repo: Path) -> None:
    cfg = default_config()
    cfg.memory.hot_tier_max_tokens = 1
    save_config(config_path(), cfg)
    (project_repo / "meta" / "MEMORY.md").write_text("# Memory\n\n" + ("hot token\n" * 40))

    html = _build_html(project_dir)

    assert "Health Signals" in html
    assert "Hot tier utilisation out of range" in html
    assert "Recommended Actions" in html
    assert "Metric" in html


def test_viewer_surfaces_governance_health_panel(project_dir: Path, project_repo: Path) -> None:
    payload = {
        "repo": str(project_repo),
        "mode": "remote",
        "governed": True,
        "ok": False,
        "flags": [
            "1 governance PR(s) awaiting L2 review",
            "1 stale local governance branch(es) older than 7d without an open PR",
        ],
        "errors": [
            "PR #14 dream/l1/20260418-needs-review: governance PR body is missing the required fact-delta block"
        ],
        "summary": {
            "open_governance_prs": 1,
            "reviewer_queue_depth": 1,
            "human_review_queue_depth": 0,
            "stale_branch_count": 1,
            "label_drift_count": 1,
            "stale_branch_days": 7,
            "pr_inventory_available": True,
        },
        "open_prs": [
            {
                "number": 14,
                "title": "Needs review",
                "url": "https://example.test/pr/14",
                "head_ref": "dream/l1/20260418-needs-review",
                "labels": ["type: extraction", "confidence:high", "state: extraction"],
                "state": "state: extraction",
                "human_review": False,
                "fact_ids": [],
                "body_error": None,
            }
        ],
        "stale_branches": [
            {
                "name": "proposal/old-cleanup",
                "head": "abc123",
                "last_commit_ts": "2026-04-01T10:00:00Z",
                "age_days": 12,
                "current": False,
                "upstream": None,
            }
        ],
        "last_l2_review": {
            "ts": "2026-04-18T12:00:00Z",
            "status": "completed",
            "action": "approve",
            "pr_number": 14,
            "reviewed_by": "copilot",
            "review_model": "claude-opus-4-7",
            "merge_blocked": False,
        },
        "label_drift": [
            {
                "number": 14,
                "title": "Needs review",
                "url": "https://example.test/pr/14",
                "head_ref": "dream/l1/20260418-needs-review",
                "labels": ["type: extraction", "confidence:high", "state: extraction"],
                "issues": ["missing impact label"],
            }
        ],
    }

    with patch("umx.governance_health.build_governance_health_payload", return_value=payload):
        html = _build_html(project_dir)

    assert "Governance Health" in html
    assert "Needs review" in html
    assert "proposal/old-cleanup" in html
    assert "missing impact label" in html
    assert "2026-04-18T12:00:00Z" in html
    assert "governance PR body is missing the required fact-delta block" in html


def test_viewer_surfaces_tombstones_audit_sessions_tasks_and_conventions(
    project_dir: Path,
    project_repo: Path,
) -> None:
    audit_fact = Fact(
        fact_id="01TESTFACT0000000000000500",
        text="staging deploys require a smoke check",
        scope=Scope.PROJECT,
        topic="deploy",
        encoding_strength=4,
        memory_type=MemoryType.EXPLICIT_SEMANTIC,
        verification=Verification.CORROBORATED,
        source_type=SourceType.TOOL_OUTPUT,
        source_tool="claude-code",
        source_session="sess-audit-001",
        consolidation_status=ConsolidationStatus.STABLE,
        task_status=TaskStatus.OPEN,
        provenance=Provenance(
            extracted_by="dream/l1",
            approved_by="claude-sonnet-4.6",
            approval_tier="l2",
            pr="42",
            sessions=["sess-audit-001"],
        ),
    )
    blocked_fact = Fact(
        fact_id="01TESTFACT0000000000000501",
        text="database backfill is blocked on ops sign-off",
        scope=Scope.PROJECT,
        topic="ops",
        encoding_strength=3,
        memory_type=MemoryType.EXPLICIT_SEMANTIC,
        verification=Verification.SELF_REPORTED,
        source_type=SourceType.LLM_INFERENCE,
        source_tool="copilot",
        source_session="sess-audit-002",
        consolidation_status=ConsolidationStatus.FRAGILE,
        task_status=TaskStatus.BLOCKED,
    )
    obsolete_fact = Fact(
        fact_id="01TESTFACT0000000000000502",
        text="legacy deploys used rsync over bastion",
        scope=Scope.PROJECT,
        topic="deploy",
        encoding_strength=2,
        memory_type=MemoryType.EXPLICIT_SEMANTIC,
        verification=Verification.SELF_REPORTED,
        source_type=SourceType.LLM_INFERENCE,
        source_tool="codex",
        source_session="sess-audit-003",
        consolidation_status=ConsolidationStatus.FRAGILE,
    )
    add_fact(project_repo, audit_fact)
    add_fact(project_repo, blocked_fact)
    add_fact(project_repo, obsolete_fact)
    forget_fact(project_repo, obsolete_fact.fact_id, reason="deploy flow changed")

    write_session(
        project_repo,
        {
            "session_id": "sess-audit-001",
            "tool": "claude-code",
            "machine": "workstation-1",
            "source": "claude-live-hook",
            "started": "2026-01-15T12:00:00Z",
        },
        [{"role": "assistant", "content": "Remember to run the staging smoke check."}],
        config=default_config(),
        auto_commit=False,
    )
    (project_repo / "CONVENTIONS.md").write_text(
        "# Project Conventions\n\n- Prefer small batch PRs.\n"
    )

    html = _build_html(project_dir)

    assert "Task Board" in html
    assert "Tombstones" in html
    assert "deploy flow changed" in html
    assert "Audit View" in html
    assert "dream/l1" in html
    assert "42" in html
    assert "Session Browser" in html
    assert "claude-live-hook" in html
    assert "workstation-1" in html
    assert "Conventions" in html
    assert "Prefer small batch PRs." in html


def test_viewer_post_confirm_updates_fact(project_dir: Path, project_repo: Path) -> None:
    fact = Fact(
        fact_id="01TESTVIEWACTION0000000001",
        text="staging deploys use blue/green cutovers",
        scope=Scope.PROJECT,
        topic="deploy",
        encoding_strength=3,
        memory_type=MemoryType.EXPLICIT_SEMANTIC,
        verification=Verification.SELF_REPORTED,
        source_type=SourceType.TOOL_OUTPUT,
        source_tool="codex",
        source_session="sess-view-action",
        consolidation_status=ConsolidationStatus.FRAGILE,
    )
    add_fact(project_repo, fact, auto_commit=False)

    url, server, thread = _start_test_viewer(project_dir)
    try:
        html = urlopen(
            url,
            data=urlencode({"action": "confirm", "fact_id": fact.fact_id}).encode(),
        ).read().decode()
    finally:
        server.shutdown()
        thread.join()
        server.server_close()

    updated = next(item for item in load_all_facts(project_repo, include_superseded=False) if item.fact_id == fact.fact_id)
    assert updated.verification == Verification.HUMAN_CONFIRMED
    assert updated.consolidation_status == ConsolidationStatus.STABLE
    assert "notice-success" in html


def test_viewer_post_rejects_governed_mutation(project_dir: Path, project_repo: Path) -> None:
    fact = Fact(
        fact_id="01TESTVIEWBLOCK0000000001",
        text="release docs live in docs/releases",
        scope=Scope.PROJECT,
        topic="docs",
        encoding_strength=3,
        memory_type=MemoryType.EXPLICIT_SEMANTIC,
        verification=Verification.SELF_REPORTED,
        source_type=SourceType.TOOL_OUTPUT,
        source_tool="codex",
        source_session="sess-view-block",
        consolidation_status=ConsolidationStatus.FRAGILE,
    )
    add_fact(project_repo, fact, auto_commit=False)
    cfg = default_config()
    cfg.dream.mode = "remote"
    save_config(config_path(), cfg)

    url, server, thread = _start_test_viewer(project_dir)
    try:
        html = urlopen(
            url,
            data=urlencode({"action": "confirm", "fact_id": fact.fact_id}).encode(),
        ).read().decode()
    finally:
        server.shutdown()
        thread.join()
        server.server_close()

    unchanged = next(item for item in load_all_facts(project_repo, include_superseded=False) if item.fact_id == fact.fact_id)
    assert unchanged.verification == Verification.SELF_REPORTED
    assert "notice-error" in html
    assert "fact changes must go through Dream PR branches" in html


def test_viewer_post_rejects_promote_without_destination(project_dir: Path, project_repo: Path) -> None:
    fact = Fact(
        fact_id="01TESTVIEWPROMOTE000000001",
        text="release docs live in docs/releases",
        scope=Scope.PROJECT,
        topic="docs",
        encoding_strength=3,
        memory_type=MemoryType.EXPLICIT_SEMANTIC,
        verification=Verification.SELF_REPORTED,
        source_type=SourceType.TOOL_OUTPUT,
        source_tool="codex",
        source_session="sess-view-promote",
        consolidation_status=ConsolidationStatus.FRAGILE,
    )
    add_fact(project_repo, fact, auto_commit=False)

    url, server, thread = _start_test_viewer(project_dir)
    try:
        html = urlopen(
            url,
            data=urlencode({"action": "promote", "fact_id": fact.fact_id}).encode(),
        ).read().decode()
    finally:
        server.shutdown()
        thread.join()
        server.server_close()

    facts = load_all_facts(project_repo, include_superseded=False)
    assert any(item.fact_id == fact.fact_id for item in facts)
    assert "notice-error" in html
    assert "invalid promotion target" in html


def test_viewer_post_merge_applies_resolution(project_dir: Path, project_repo: Path) -> None:
    add_fact(
        project_repo,
        Fact(
            fact_id="01TESTVIEWMERGE0000000001",
            text="postgres runs on 5433 in dev",
            scope=Scope.PROJECT,
            topic="devenv",
            encoding_strength=3,
            memory_type=MemoryType.EXPLICIT_SEMANTIC,
            verification=Verification.SELF_REPORTED,
            source_type=SourceType.TOOL_OUTPUT,
            source_tool="codex",
            source_session="sess-view-merge-a",
            consolidation_status=ConsolidationStatus.FRAGILE,
        ),
        auto_commit=False,
    )
    add_fact(
        project_repo,
        Fact(
            fact_id="01TESTVIEWMERGE0000000002",
            text="postgres runs on 5432 in dev",
            scope=Scope.PROJECT,
            topic="devenv",
            encoding_strength=5,
            memory_type=MemoryType.EXPLICIT_SEMANTIC,
            verification=Verification.CORROBORATED,
            source_type=SourceType.TOOL_OUTPUT,
            source_tool="codex",
            source_session="sess-view-merge-b",
            consolidation_status=ConsolidationStatus.FRAGILE,
        ),
        auto_commit=False,
    )

    url, server, thread = _start_test_viewer(project_dir)
    try:
        html = urlopen(url, data=urlencode({"action": "merge"}).encode()).read().decode()
    finally:
        server.shutdown()
        thread.join()
        server.server_close()

    facts = load_all_facts(project_repo, include_superseded=True)
    assert any(item.superseded_by for item in facts)
    assert "resolved 1 conflicts" in html


def test_viewer_surfaces_quarantine_queue(project_dir: Path, project_repo: Path) -> None:
    session_id = "2026-04-11-quarantine-viewer"
    _write_quarantined_session(
        project_repo,
        session_id=session_id,
        content=f'api_key = "{OPENAI_KEY_SHORT}"',
    )

    html = _build_html(project_dir)

    assert "Quarantine Queue" in html
    assert session_id in html
    assert "invalid redaction pattern" in html
    assert "[REDACTED:openai-key]" in html
    assert OPENAI_KEY_SHORT not in html


def test_viewer_ignores_push_safety_reports_in_quarantine_queue(project_dir: Path, project_repo: Path) -> None:
    quarantine_dir = project_repo / "local" / "quarantine"
    quarantine_dir.mkdir(parents=True, exist_ok=True)
    report = quarantine_dir / "push-safety-20260411T000000Z.json"
    report.write_text(json.dumps({"findings": []}, sort_keys=True) + "\n")

    html = _build_html(project_dir)

    assert "Quarantine Queue" in html
    assert "No quarantined sessions." in html
    assert report.name not in html


def test_viewer_post_release_requires_confirm(project_dir: Path, project_repo: Path) -> None:
    session_id = "2026-04-11-quarantine-confirm"
    _write_quarantined_session(
        project_repo,
        session_id=session_id,
        content=f'api_key = "{OPENAI_KEY_SHORT}"',
    )

    url, server, thread = _start_test_viewer(project_dir)
    try:
        html = urlopen(
            url,
            data=urlencode({"action": "release-quarantine", "session_id": session_id}).encode(),
        ).read().decode()
    finally:
        server.shutdown()
        thread.join()
        server.server_close()

    assert "notice-error" in html
    assert "release requires explicit confirm" in html
    assert quarantine_path(project_repo, session_id).exists()
    assert not session_path(project_repo, session_id).exists()


def test_viewer_post_release_moves_session_out_of_quarantine(project_dir: Path, project_repo: Path) -> None:
    session_id = "2026-04-11-quarantine-release"
    _write_quarantined_session(
        project_repo,
        session_id=session_id,
        content=f'api_key = "{OPENAI_KEY_SHORT}"',
    )

    url, server, thread = _start_test_viewer(project_dir)
    try:
        html = urlopen(
            url,
            data=urlencode(
                {
                    "action": "release-quarantine",
                    "session_id": session_id,
                    "confirm_release": "yes",
                }
            ).encode(),
        ).read().decode()
    finally:
        server.shutdown()
        thread.join()
        server.server_close()

    released = read_session(session_path(project_repo, session_id))
    assert released[1]["content"] == 'api_key = "[REDACTED:openai-key]"'
    assert not quarantine_path(project_repo, session_id).exists()
    assert not quarantine_metadata_path(project_repo, session_id).exists()
    decision_log = quarantine_decision_log_path(project_repo)
    assert decision_log.exists()
    decisions = [json.loads(line) for line in decision_log.read_text().splitlines() if line.strip()]
    assert any(item["action"] == "release" and item["session_id"] == session_id for item in decisions)
    assert "notice-success" in html
    assert f"released {session_id}" in html


def test_viewer_post_release_remains_fail_closed_when_redaction_disabled(
    project_dir: Path,
    project_repo: Path,
) -> None:
    session_id = "2026-04-11-quarantine-fail-closed"
    _write_quarantined_session(
        project_repo,
        session_id=session_id,
        content=f'api_key = "{OPENAI_KEY_SHORT}"',
    )
    cfg = default_config()
    cfg.sessions.redaction = "none"
    save_config(config_path(), cfg)

    url, server, thread = _start_test_viewer(project_dir)
    try:
        html = urlopen(
            url,
            data=urlencode(
                {
                    "action": "release-quarantine",
                    "session_id": session_id,
                    "confirm_release": "yes",
                }
            ).encode(),
        ).read().decode()
    finally:
        server.shutdown()
        thread.join()
        server.server_close()

    assert "notice-error" in html
    assert "sessions.redaction to stay enabled" in html
    assert quarantine_path(project_repo, session_id).exists()
    assert not session_path(project_repo, session_id).exists()


def test_viewer_post_discard_removes_quarantine_and_logs_decision(project_dir: Path, project_repo: Path) -> None:
    session_id = "2026-04-11-quarantine-discard"
    _write_quarantined_session(
        project_repo,
        session_id=session_id,
        content=f'api_key = "{OPENAI_KEY_SHORT}"',
    )

    url, server, thread = _start_test_viewer(project_dir)
    try:
        html = urlopen(
            url,
            data=urlencode({"action": "discard-quarantine", "session_id": session_id}).encode(),
        ).read().decode()
    finally:
        server.shutdown()
        thread.join()
        server.server_close()

    decision_log = quarantine_decision_log_path(project_repo)
    assert decision_log.exists()
    decisions = [json.loads(line) for line in decision_log.read_text().splitlines() if line.strip()]
    assert any(item["action"] == "discard" and item["session_id"] == session_id for item in decisions)
    assert not quarantine_path(project_repo, session_id).exists()
    assert not quarantine_metadata_path(project_repo, session_id).exists()
    assert not session_path(project_repo, session_id).exists()
    assert "notice-success" in html
    assert f"discarded {session_id}" in html
