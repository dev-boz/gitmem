from __future__ import annotations

from pathlib import Path

import json

from umx.config import default_config
from umx.hooks.assistant_output import run as assistant_output_run
from umx.inject import build_injection_block
from umx.memory import add_fact
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
from umx.sessions import write_session
from umx.tombstones import forget_fact
from umx.viewer.server import _build_html


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

    html = _build_html(project_dir)

    assert "Manifest Coverage" in html
    assert "Gap Proposals" in html
    assert "Lint Report" in html
    assert "postgres backups" in html
    assert "fact-123 has not been re-grounded to code" in html


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
