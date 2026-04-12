from __future__ import annotations

from pathlib import Path

import json

from umx.hooks.assistant_output import run as assistant_output_run
from umx.inject import build_injection_block
from umx.memory import add_fact
from umx.models import (
    ConsolidationStatus,
    Fact,
    MemoryType,
    Scope,
    SourceType,
    Verification,
)
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
