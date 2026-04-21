from __future__ import annotations

from pathlib import Path

import umx.search as search

from umx.budget import estimate_tokens
from umx.conventions import summarize_conventions
from umx.inject import _disclosure_levels, _enforce_rendered_budget, _fact_token_cost, build_injection_block
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
from umx.scope import encode_scope_path, user_memory_dir
from umx.search import query_index, rebuild_index, session_replay, session_snapshot, usage_snapshot
from umx.hooks.session_end import run as session_end_run
from umx.tombstones import forget_fact


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
    conn = search._connect(search.usage_path(project_repo))
    try:
        inject_events = conn.execute(
            "SELECT COUNT(*) AS count FROM usage_events WHERE event_kind = 'inject'"
        ).fetchone()["count"]
    finally:
        conn.close()
    assert inject_events == 0


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


def test_record_injections_batches_usage_updates(project_repo: Path) -> None:
    search.record_injections(
        project_repo,
        [
            {
                "fact_id": "fact-a",
                "session_id": "sess-batch-001",
                "turn_index": 0,
                "session_tokens": 120,
                "token_count": 20,
            },
            {
                "fact_id": "fact-b",
                "session_id": "sess-batch-001",
                "turn_index": 0,
                "session_tokens": 120,
                "token_count": 22,
            },
        ],
    )

    usage = usage_snapshot(project_repo)
    replay = session_replay(project_repo, "sess-batch-001")

    assert usage["fact-a"]["injected_count"] == 1
    assert usage["fact-b"]["injected_count"] == 1
    assert sum(1 for row in replay if row["event_kind"] == "inject") == 2


def test_file_scoped_facts_are_not_duplicated_when_path_targeted(
    project_repo: Path,
    project_dir: Path,
) -> None:
    fact = make_fact(
        "src/app.py uses postgres connection pooling",
        topic=encode_scope_path("src/app.py"),
        fact_id="01TESTFACT0000000000000010",
        scope=Scope.FILE,
        consolidation_status=ConsolidationStatus.STABLE,
    )
    add_fact(project_repo, fact)

    block = build_injection_block(
        project_dir,
        prompt="postgres connection pooling",
        file_paths=["src/app.py"],
        session_id="sess-file-001",
    )
    replay = session_replay(project_repo, "sess-file-001")

    assert block.count("src/app.py uses postgres connection pooling") == 1
    assert sum(1 for row in replay if row["event_kind"] == "inject") == 1


def test_scoped_superseded_fact_stays_hidden_when_path_targeted(
    project_repo: Path,
    project_dir: Path,
) -> None:
    stale = make_fact(
        "src/app.py uses postgres 5432",
        topic=encode_scope_path("src/app.py"),
        fact_id="01TESTFACT0000000000000012",
        scope=Scope.FILE,
        superseded_by="01TESTFACT0000000000000013",
        consolidation_status=ConsolidationStatus.STABLE,
    )
    current = make_fact(
        "src/app.py uses postgres 5433",
        topic=encode_scope_path("src/app.py"),
        fact_id="01TESTFACT0000000000000013",
        scope=Scope.FILE,
        supersedes=stale.fact_id,
        consolidation_status=ConsolidationStatus.STABLE,
    )
    add_fact(project_repo, stale)
    add_fact(project_repo, current)

    block = build_injection_block(
        project_dir,
        prompt="postgres",
        file_paths=["src/app.py"],
    )

    assert "src/app.py uses postgres 5432" not in block
    assert "src/app.py uses postgres 5433" in block


def test_scoped_tombstoned_fact_stays_hidden_when_path_targeted(
    project_repo: Path,
    project_dir: Path,
) -> None:
    fact = make_fact(
        "src/app.py uses deprecated postgres settings",
        topic=encode_scope_path("src/app.py"),
        fact_id="01TESTFACT0000000000000014",
        scope=Scope.FILE,
        consolidation_status=ConsolidationStatus.STABLE,
    )
    add_fact(project_repo, fact)
    forget_fact(project_repo, fact.fact_id)

    block = build_injection_block(
        project_dir,
        prompt="postgres",
        file_paths=["src/app.py"],
    )

    assert "deprecated postgres settings" not in block


def test_collect_facts_for_injection_reuses_base_inventory_cache(
    project_repo: Path,
    project_dir: Path,
    monkeypatch,
) -> None:
    fact = make_fact(
        "postgres runs on 5433 in dev",
        topic="devenv",
        fact_id="01TESTFACT0000000000000011",
        consolidation_status=ConsolidationStatus.STABLE,
    )
    add_fact(project_repo, fact)
    calls = 0
    original = build_injection_block.__globals__["load_all_facts"]

    def counted_load_all_facts(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setitem(
        build_injection_block.__globals__,
        "load_all_facts",
        counted_load_all_facts,
    )

    first = build_injection_block(project_dir, prompt="postgres")
    second = build_injection_block(project_dir, prompt="postgres")

    assert "postgres runs on 5433 in dev" in first
    assert "postgres runs on 5433 in dev" in second
    assert calls == 2


def test_project_preselection_limits_scoring_to_shortlist_and_user_facts(
    project_repo: Path,
    user_repo: Path,
    project_dir: Path,
    monkeypatch,
) -> None:
    shortlisted = make_fact(
        "postgres connection pooling is enabled",
        topic="devenv",
        fact_id="01TESTFACT0000000000000015",
        consolidation_status=ConsolidationStatus.STABLE,
    )
    omitted = make_fact(
        "redis queue names must stay stable",
        topic="queues",
        fact_id="01TESTFACT0000000000000016",
        consolidation_status=ConsolidationStatus.STABLE,
    )
    user_fact = make_fact(
        "always prefer concise status handoffs",
        topic="style",
        fact_id="01TESTFACT0000000000000017",
        scope=Scope.USER,
        encoding_strength=5,
        verification=Verification.HUMAN_CONFIRMED,
        source_type=SourceType.USER_PROMPT,
        consolidation_status=ConsolidationStatus.STABLE,
    )
    add_fact(project_repo, shortlisted)
    add_fact(project_repo, omitted)
    add_fact(user_repo, user_fact)

    scored: list[str] = []
    original = build_injection_block.__globals__["relevance_score"]

    def tracked_relevance(fact, *args, **kwargs):
        scored.append(fact.fact_id)
        return original(fact, *args, **kwargs)

    monkeypatch.setitem(build_injection_block.__globals__, "_inject_candidate_limit", lambda cfg: 1)
    monkeypatch.setitem(
        build_injection_block.__globals__,
        "inject_candidate_ids",
        lambda *args, **kwargs: [shortlisted.fact_id],
    )
    monkeypatch.setitem(build_injection_block.__globals__, "relevance_score", tracked_relevance)

    block = build_injection_block(project_dir, prompt="postgres")

    assert "postgres connection pooling is enabled" in block
    assert omitted.fact_id not in scored
    assert set(scored) == {shortlisted.fact_id, user_fact.fact_id}


def test_project_preselection_preserves_scoped_facts_and_open_tasks(
    project_repo: Path,
    project_dir: Path,
    monkeypatch,
) -> None:
    shortlisted = make_fact(
        "postgres service owns the shared connection pool",
        topic="devenv",
        fact_id="01TESTFACT0000000000000018",
        consolidation_status=ConsolidationStatus.STABLE,
    )
    open_task = make_fact(
        "finish postgres pooling migration",
        topic="tasks",
        fact_id="01TESTFACT0000000000000019",
        task_status=TaskStatus.OPEN,
        consolidation_status=ConsolidationStatus.STABLE,
    )
    scoped = make_fact(
        "src/app.py handles postgres connection pooling",
        topic=encode_scope_path("src/app.py"),
        fact_id="01TESTFACT0000000000000020",
        scope=Scope.FILE,
        consolidation_status=ConsolidationStatus.STABLE,
    )
    omitted = make_fact(
        "redis cache warmup is optional",
        topic="caching",
        fact_id="01TESTFACT0000000000000021",
        consolidation_status=ConsolidationStatus.STABLE,
    )
    add_fact(project_repo, shortlisted)
    add_fact(project_repo, open_task)
    add_fact(project_repo, scoped)
    add_fact(project_repo, omitted)

    monkeypatch.setitem(build_injection_block.__globals__, "_inject_candidate_limit", lambda cfg: 1)
    monkeypatch.setitem(
        build_injection_block.__globals__,
        "inject_candidate_ids",
        lambda *args, **kwargs: [shortlisted.fact_id],
    )

    block = build_injection_block(
        project_dir,
        prompt="postgres pooling",
        file_paths=["src/app.py"],
    )

    assert "src/app.py handles postgres connection pooling" in block
    assert "## Open Tasks" in block
    assert "- finish postgres pooling migration" in block


def test_project_preselection_falls_back_to_full_scan_without_shortlist(
    project_repo: Path,
    project_dir: Path,
    monkeypatch,
) -> None:
    first = make_fact(
        "postgres runs on 5433 in dev",
        topic="devenv",
        fact_id="01TESTFACT0000000000000022",
        consolidation_status=ConsolidationStatus.STABLE,
    )
    second = make_fact(
        "redis queue stays in a separate worker",
        topic="queues",
        fact_id="01TESTFACT0000000000000023",
        consolidation_status=ConsolidationStatus.STABLE,
    )
    add_fact(project_repo, first)
    add_fact(project_repo, second)

    scored: list[str] = []
    original = build_injection_block.__globals__["relevance_score"]

    def tracked_relevance(fact, *args, **kwargs):
        scored.append(fact.fact_id)
        return original(fact, *args, **kwargs)

    monkeypatch.setitem(build_injection_block.__globals__, "_inject_candidate_limit", lambda cfg: 1)
    monkeypatch.setitem(
        build_injection_block.__globals__,
        "inject_candidate_ids",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setitem(build_injection_block.__globals__, "relevance_score", tracked_relevance)

    build_injection_block(project_dir, prompt="postgres redis")

    assert {first.fact_id, second.fact_id}.issubset(set(scored))


def test_project_preselection_preserves_attention_refresh_facts(
    project_repo: Path,
    project_dir: Path,
    monkeypatch,
) -> None:
    refreshed = make_fact(
        "vault stores postgres credentials for staging",
        topic="security",
        fact_id="01TESTFACT0000000000000024",
        consolidation_status=ConsolidationStatus.STABLE,
    )
    shortlisted = make_fact(
        "redis queue settings control worker throughput",
        topic="queues",
        fact_id="01TESTFACT0000000000000025",
        consolidation_status=ConsolidationStatus.STABLE,
    )
    add_fact(project_repo, refreshed)
    add_fact(project_repo, shortlisted)

    monkeypatch.setitem(build_injection_block.__globals__, "_inject_candidate_limit", lambda cfg: 1)
    monkeypatch.setitem(
        build_injection_block.__globals__,
        "inject_candidate_ids",
        lambda *args, **kwargs: [shortlisted.fact_id],
    )
    monkeypatch.setitem(
        build_injection_block.__globals__,
        "_attention_refresh_ids",
        lambda *args, **kwargs: {refreshed.fact_id},
    )

    block = build_injection_block(
        project_dir,
        prompt="redis workers",
        session_id="sess-refresh-001",
        context_window_tokens=16000,
    )

    assert "vault stores postgres credentials for staging" in block


def test_restrict_to_ids_bypasses_project_preselection(
    project_repo: Path,
    project_dir: Path,
    monkeypatch,
) -> None:
    kept = make_fact(
        "postgres runs on 5433 in dev",
        topic="devenv",
        fact_id="01TESTFACT0000000000000026",
        consolidation_status=ConsolidationStatus.STABLE,
    )
    dropped = make_fact(
        "redis queue names must stay stable",
        topic="queues",
        fact_id="01TESTFACT0000000000000027",
        consolidation_status=ConsolidationStatus.STABLE,
    )
    add_fact(project_repo, kept)
    add_fact(project_repo, dropped)

    def fail_if_called(*args, **kwargs):
        raise AssertionError("project preselection should be bypassed when restrict_to_ids is set")

    monkeypatch.setitem(build_injection_block.__globals__, "_inject_candidate_limit", lambda cfg: 1)
    monkeypatch.setitem(build_injection_block.__globals__, "inject_candidate_ids", fail_if_called)

    block = build_injection_block(
        project_dir,
        prompt="postgres redis",
        restrict_to_ids={kept.fact_id},
    )

    assert "postgres runs on 5433 in dev" in block
    assert "redis queue names must stay stable" not in block


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


def test_ensure_usage_db_skips_repeat_bootstrap(project_repo: Path, monkeypatch) -> None:
    original_connect = search._connect
    calls: list[Path] = []

    def counted_connect(path: Path):
        calls.append(path)
        return original_connect(path)

    monkeypatch.setattr(search, "_connect", counted_connect)

    search.ensure_usage_db(project_repo)
    search.ensure_usage_db(project_repo)

    assert len(calls) == 1


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


def test_disclosure_slack_keeps_l1_when_headroom_exceeds_configured_threshold() -> None:
    fact = make_fact(
        "postgres runs on 5433 in dev",
        topic="devenv",
        fact_id="01TESTFACT0000000000000010",
        verification=Verification.CORROBORATED,
        source_type=SourceType.GROUND_TRUTH_CODE,
        consolidation_status=ConsolidationStatus.STABLE,
    )
    l1_cost = _fact_token_cost(fact, "l1")
    extra = next(
        slack
        for slack in range(1, l1_cost + 10)
        if 0.20 < (slack / (l1_cost + slack)) < 0.30
    )
    budget = l1_cost + extra
    packing_scores = {fact.fact_id: 1.0}

    relaxed_levels = _disclosure_levels(
        [fact],
        packing_scores,
        always_ids=set(),
        token_budget=budget,
        disclosure_slack_pct=0.20,
    )
    tight_levels = _disclosure_levels(
        [fact],
        packing_scores,
        always_ids=set(),
        token_budget=budget,
        disclosure_slack_pct=0.30,
    )

    assert relaxed_levels[fact.fact_id] == "l1"
    assert tight_levels[fact.fact_id] == "l0"

    selected, relaxed_levels = _enforce_rendered_budget(
        [fact],
        relaxed_levels,
        packing_scores,
        fact_budget=budget,
        always_ids=set(),
    )

    assert selected == [fact]
    assert sum(_fact_token_cost(item, relaxed_levels.get(item.fact_id, "l1")) for item in selected) <= budget
