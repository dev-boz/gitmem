from __future__ import annotations

from pathlib import Path

from umx.budget import estimate_tokens
from umx.conventions import summarize_conventions
from umx.inject import _fact_token_cost, build_injection_block
from umx.memory import add_fact
from umx.models import (
    ConsolidationStatus,
    Fact,
    MemoryType,
    Scope,
    SourceType,
    TaskStatus,
    Verification,
)
from umx.scope import user_memory_dir
from umx.search import query_index, rebuild_index, session_replay, session_snapshot, usage_snapshot
from umx.hooks.session_end import run as session_end_run


def make_fact(text: str, topic: str = "general", **overrides) -> Fact:
    values = {
        "fact_id": overrides.pop("fact_id", "01TESTFACT0000000000000001"),
        "text": text,
        "scope": Scope.PROJECT,
        "topic": topic,
        "encoding_strength": 3,
        "memory_type": MemoryType.EXPLICIT_SEMANTIC,
        "verification": Verification.SELF_REPORTED,
        "source_type": SourceType.TOOL_OUTPUT,
        "source_tool": "codex",
        "source_session": "2026-04-11-session",
        "consolidation_status": ConsolidationStatus.FRAGILE,
    }
    values.update(overrides)
    return Fact(**values)


def test_injection_marks_fragile_and_tracks_injected_not_referenced(project_repo: Path, user_repo: Path, project_dir: Path) -> None:
    user_fact = make_fact(
        "always use snake_case",
        topic="style",
        fact_id="01TESTFACT0000000000000002",
        scope=Scope.USER,
        encoding_strength=5,
        verification=Verification.HUMAN_CONFIRMED,
        source_type=SourceType.USER_PROMPT,
        consolidation_status=ConsolidationStatus.STABLE,
    )
    project_fact = make_fact("postgres runs on 5433 in dev", topic="devenv")
    task_fact = make_fact(
        "finish database migration",
        topic="tasks",
        fact_id="01TESTFACT0000000000000003",
        task_status=TaskStatus.OPEN,
        consolidation_status=ConsolidationStatus.STABLE,
    )
    add_fact(user_repo, user_fact)
    add_fact(project_repo, project_fact)
    add_fact(project_repo, task_fact)

    block = build_injection_block(project_dir, prompt="debug postgres")

    assert "## Conventions" in block
    assert "[fragile] postgres runs on 5433 in dev" in block
    assert "## Open Tasks" in block

    usage = usage_snapshot(project_repo)
    assert usage[project_fact.fact_id]["injected_count"] >= 1
    assert usage[project_fact.fact_id]["last_referenced"] is None


def test_search_rebuild_excludes_superseded(project_repo: Path) -> None:
    old_fact = make_fact(
        "postgres runs on 5432 in dev",
        topic="devenv",
        fact_id="01TESTFACT0000000000000004",
        superseded_by="01TESTFACT0000000000000005",
        consolidation_status=ConsolidationStatus.STABLE,
    )
    new_fact = make_fact(
        "postgres runs on 5433 in dev",
        topic="devenv",
        fact_id="01TESTFACT0000000000000005",
        supersedes=old_fact.fact_id,
        consolidation_status=ConsolidationStatus.STABLE,
    )
    add_fact(project_repo, old_fact)
    add_fact(project_repo, new_fact)

    rebuild_index(project_repo)
    results = query_index(project_repo, "postgres", limit=10)

    assert [fact.fact_id for fact in results] == [new_fact.fact_id]


def test_session_reference_updates_usage_and_replay(project_repo: Path, project_dir: Path) -> None:
    fact = make_fact(
        "postgres runs on 5433 in dev",
        topic="devenv",
        fact_id="01TESTFACT0000000000000006",
    )
    add_fact(project_repo, fact)

    block = build_injection_block(project_dir, prompt="postgres", session_id="sess-ref-001")
    assert "postgres runs on 5433 in dev" in block

    session_end_run(
        cwd=project_dir,
        session_id="sess-ref-001",
        events=[
            {
                "role": "assistant",
                "content": "Use the project default: postgres runs on 5433 in dev.",
            }
        ],
    )

    usage = usage_snapshot(project_repo)
    assert usage[fact.fact_id]["cited_count"] >= 1
    replay = session_replay(project_repo, "sess-ref-001")
    assert any(row["event_kind"] == "inject" for row in replay)
    assert any(row["event_kind"] == "reference" for row in replay)


def test_repeated_injection_does_not_advance_session_without_events(project_repo: Path, project_dir: Path) -> None:
    fact = make_fact(
        "postgres runs on 5433 in dev",
        topic="devenv",
        fact_id="01TESTFACT0000000000000007",
    )
    add_fact(project_repo, fact)

    build_injection_block(
        project_dir,
        prompt="postgres",
        session_id="sess-static-001",
        context_window_tokens=16000,
    )
    first = session_snapshot(project_repo, "sess-static-001")
    build_injection_block(
        project_dir,
        prompt="postgres",
        session_id="sess-static-001",
        context_window_tokens=16000,
    )
    second = session_snapshot(project_repo, "sess-static-001")

    assert first is not None
    assert second is not None
    assert first["turn_index"] == 0
    assert second["turn_index"] == 0
    assert first["estimated_tokens"] == 0
    assert second["estimated_tokens"] == 0


def test_l1_fact_render_includes_source_type(project_repo: Path, project_dir: Path) -> None:
    fact = make_fact(
        "postgres runs on 5433 in dev",
        topic="devenv",
        fact_id="01TESTFACT0000000000000008",
        verification=Verification.CORROBORATED,
        source_type=SourceType.GROUND_TRUTH_CODE,
        consolidation_status=ConsolidationStatus.STABLE,
    )
    add_fact(project_repo, fact)

    block = build_injection_block(project_dir, prompt="postgres")

    assert "src:ground_truth_code" in block
    assert "status:" not in block


def test_expanded_fact_downgrades_when_rendered_cost_exceeds_budget(project_repo: Path, project_dir: Path) -> None:
    fact = make_fact(
        "postgres runs on 5433 in dev",
        topic="devenv",
        fact_id="01TESTFACT0000000000000009",
        verification=Verification.CORROBORATED,
        source_type=SourceType.GROUND_TRUTH_CODE,
        consolidation_status=ConsolidationStatus.STABLE,
    )
    add_fact(project_repo, fact)

    block = build_injection_block(
        project_dir,
        prompt="postgres",
        max_tokens=(
            estimate_tokens(summarize_conventions(project_repo / "CONVENTIONS.md"))
            + _fact_token_cost(fact, "l1")
        ),
        expanded_ids={fact.fact_id},
    )

    assert "src:ground_truth_code" in block
    assert "status:" not in block
