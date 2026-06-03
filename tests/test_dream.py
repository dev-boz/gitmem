from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from tests.secret_literals import ANTHROPIC_KEY_FAKE
from umx.config import default_config
from umx.dream.extract import mark_sessions_gathered, session_records_to_facts
from umx.dream.gates import UserDreamLock, read_dream_state
from umx.dream.pipeline import DreamPipeline
from umx.dream.processing import read_processing_log, start_processing_run
from umx.inject import emit_gap_signal
from umx.memory import add_fact, find_fact_by_id, load_all_facts
from umx.models import ConsolidationStatus, Fact, MemoryType, Scope, SourceType, Verification
from umx.search import search_sessions
from umx.sessions import archive_path, archive_state_path, session_path, write_session
from umx.scope import init_project_memory, project_memory_dir
from umx.tombstones import forget_fact


def test_available_provider_notice_reports_detected_backends(monkeypatch) -> None:
    from umx.dream import providers

    monkeypatch.setattr(
        providers,
        "detected_capture_backends",
        lambda home=None: ["codex", "amp"],
    )
    monkeypatch.setattr(
        providers,
        "detected_external_dream_agents",
        lambda which=None: ["amp", "qodo"],
    )
    monkeypatch.setattr(
        providers,
        "missing_external_dream_agents",
        lambda which=None: ["jules"],
    )

    notice = providers.available_provider_notice()

    assert "native-only dream" in notice
    assert "Detected local capture sources: codex, amp." in notice
    assert "Detected external dream-agent CLIs: amp, qodo." in notice
    assert "Not installed here: jules." in notice


def test_session_gather_extracts_facts(project_dir: Path, project_repo: Path) -> None:
    write_session(
        project_repo,
        meta={"session_id": "2026-01-15-abc123"},
        events=[
            {"role": "user", "content": "How does the server start?"},
            {
                "role": "assistant",
                "content": (
                    "The server uses port 8080 by default. "
                    "Let me check the config. "
                    "PostgreSQL stores data in the /var/lib/pg directory. "
                    f"The API key is {ANTHROPIC_KEY_FAKE}."
                ),
            },
            {"role": "user", "content": "Thanks"},
        ],
    )

    facts = session_records_to_facts(project_repo)
    assert len(facts) >= 2

    for fact in facts:
        assert fact.encoding_strength == 2
        assert fact.consolidation_status == ConsolidationStatus.FRAGILE
        assert fact.verification == Verification.SELF_REPORTED
        assert fact.source_type == SourceType.LLM_INFERENCE
        assert fact.confidence == 0.5
        assert fact.source_tool == "session-extract"
        assert fact.source_session == "2026-01-15-abc123"
        assert fact.scope == Scope.PROJECT
        assert fact.memory_type == MemoryType.IMPLICIT
        assert fact.provenance.extracted_by == "native:session-heuristic"
        assert "2026-01-15-abc123" in fact.provenance.sessions

    # "Let me check the config" should be filtered out
    texts = [f.text for f in facts]
    assert all("Let me" not in t for t in texts)

    # Redaction was applied — the Anthropic key should be redacted
    all_text = " ".join(texts)
    assert ANTHROPIC_KEY_FAKE not in all_text
    assert "[REDACTED:" in all_text

    # Second call with same sessions: nothing new
    mark_sessions_gathered(project_repo, ["2026-01-15-abc123"])
    facts2 = session_records_to_facts(project_repo)
    assert len(facts2) == 0

    # Verify state was persisted
    state = read_dream_state(project_repo)
    assert "2026-01-15-abc123" in state.get("last_gathered_sessions", [])


def test_dream_run_records_processing_lifecycle(project_dir: Path, project_repo: Path) -> None:
    result = DreamPipeline(project_dir).run(force=True)

    assert result.status == "ok"
    records = read_processing_log(project_repo)
    assert [record["event"] for record in records] == ["started", "completed"]
    assert records[-1]["status"] == "completed"


def test_dream_run_skips_when_processing_is_active(project_dir: Path, project_repo: Path) -> None:
    start_processing_run(project_repo, mode="local", force=True, branch="main")

    result = DreamPipeline(project_dir).run(force=True)

    assert result.status == "skipped"
    assert result.message == "dream processing held"


def test_dream_run_skips_when_user_level_lock_is_held(
    umx_home: Path,
    tmp_path: Path,
) -> None:
    other_project = tmp_path / "other-project"
    other_project.mkdir()
    (other_project / ".git").mkdir()
    init_project_memory(other_project)
    project_memory_dir(other_project)

    lock = UserDreamLock(umx_home=umx_home)
    assert lock.acquire()
    try:
        result = DreamPipeline(other_project).run(force=True)
    finally:
        lock.release()

    assert result.status == "skipped"
    assert result.message == "dream lock held"


def test_user_dream_lock_reclaims_stale_lock(umx_home: Path) -> None:
    lock = UserDreamLock(umx_home=umx_home)
    lock.path.parent.mkdir(parents=True, exist_ok=True)
    lock.path.write_text(
        json.dumps(
            {
                "pid": 999999,
                "hostname": "stale-host",
                "started": "2000-01-01T00:00:00Z",
                "heartbeat": "2000-01-01T00:00:00Z",
            },
            sort_keys=True,
        )
        + "\n"
    )

    assert lock.acquire()
    payload = json.loads(lock.path.read_text())
    assert payload["pid"] != 999999
    lock.release()


def test_dream_run_archives_due_sessions_and_keeps_them_searchable(
    project_dir: Path, project_repo: Path
) -> None:
    write_session(
        project_repo,
        {
            "session_id": "2020-01-15-dreamarchive",
            "started": "2020-01-15T00:00:00Z",
            "tool": "codex",
        },
        [{"ts": "2020-01-15T00:00:01Z", "role": "assistant", "content": "postgres runs on port 5433 in dev."}],
        auto_commit=False,
    )
    cfg = default_config()
    cfg.sessions.archive_interval = "daily"

    result = DreamPipeline(project_dir, config=cfg).run(force=True)

    assert result.status == "ok"
    assert "1 archived" in (result.message or "")
    assert not session_path(project_repo, "2020-01-15-dreamarchive").exists()
    assert archive_path(project_repo, "2020", "01").exists()
    assert archive_state_path(project_repo).exists()
    assert any(row["session_id"] == "2020-01-15-dreamarchive" for row in search_sessions(project_repo, "postgres"))


def test_session_gather_ignores_progress_only_codex_commentary(
    project_dir: Path, project_repo: Path
) -> None:
    write_session(
        project_repo,
        meta={
            "session_id": "2026-04-13-codex-progress-only",
            "tool": "codex",
            "source": "codex-rollout",
        },
        events=[
            {
                "role": "assistant",
                "content": (
                    "The focused test run is still going in the background. "
                    "While that runs I’m reading `umx/dream/extract.py`, because "
                    "that’s where the current Codex noise problem will be decided. "
                    "The focused suite is green. "
                    "If that generates stored facts after `dream`, the filter still "
                    "needs work. "
                    "The hermetic capture imported the live rollout and, as expected, "
                    "`view --list` is still empty before `dream` finishes. "
                    "The focused slices are clean and the real April 13 rollout now "
                    "produces zero session-extract candidates before consolidation. "
                    "That means the problem is narrower now, so I’m looking for "
                    "remaining edge cases rather than broad suppression."
                ),
            },
        ],
    )

    facts = session_records_to_facts(project_repo)

    assert facts == []


def test_session_gather_ignores_codex_git_workflow_meta(
    project_dir: Path, project_repo: Path
) -> None:
    write_session(
        project_repo,
        meta={
            "session_id": "2026-04-13-codex-git-meta",
            "tool": "codex",
            "source": "codex-rollout",
        },
        events=[
            {
                "role": "assistant",
                "content": (
                    "Only the full-suite tail is left before I update "
                    "`dogfooding_tests_results_changes.md`. "
                    "If this repo is still pinned to a bot identity from earlier "
                    "work, I’ll stop rather than push under the wrong author. "
                    "The repo is using the normal `dev-boz <byronwarnich@gmail.com>` "
                    "identity, so I’m staging only the five files I changed and "
                    "creating a single commit for this extractor tightening. "
                    "The commit is created as `26129aa`. "
                    "It’s a stricter check than the earlier snapshot. "
                    "`BOT_CONTRIBUTOR_NOTES.md` were left untouched."
                ),
            },
        ],
    )

    facts = session_records_to_facts(project_repo)

    assert facts == []


def test_session_gather_preserves_concrete_codex_fact(
    project_dir: Path, project_repo: Path
) -> None:
    write_session(
        project_repo,
        meta={
            "session_id": "2026-04-13-codex-concrete-fact",
            "tool": "codex",
            "source": "codex-rollout",
        },
        events=[
            {
                "role": "assistant",
                "content": (
                    "The CLI supports both gitmem and umx commands. "
                    "The commit is created as `26129aa`."
                ),
            },
        ],
    )

    facts = session_records_to_facts(project_repo)
    texts = [fact.text for fact in facts]

    assert "The CLI supports both gitmem and umx commands" in texts
    assert all("commit is created as" not in text.lower() for text in texts)


def test_gap_fact_stays_fragile_first_cycle_then_stabilizes(project_dir: Path, project_repo: Path) -> None:
    emit_gap_signal(
        project_repo,
        query="devenv postgres",
        resolution_context="agent read config",
        proposed_fact="postgres runs on 5433 in dev",
        session="2026-04-11-gap",
    )

    first = DreamPipeline(project_dir).run(force=True)
    assert first.status == "ok"
    fact = next(f for f in load_all_facts(project_repo, include_superseded=False) if "5433" in f.text)
    assert fact.consolidation_status.value == "fragile"

    second = DreamPipeline(project_dir).run(force=True)
    assert second.status == "ok"
    fact = find_fact_by_id(project_repo, fact.fact_id)
    assert fact is not None
    assert fact.consolidation_status.value == "stable"


def test_consolidate_merges_same_text_scope_topic(project_dir: Path) -> None:
    existing = Fact(
        fact_id="01TESTDREAMMERGE0000000001",
        text="postgres runs on 5433 in dev",
        scope=Scope.PROJECT,
        topic="devenv",
        encoding_strength=3,
        memory_type=MemoryType.EXPLICIT_SEMANTIC,
        verification=Verification.CORROBORATED,
        source_type=SourceType.TOOL_OUTPUT,
        source_tool="codex",
        source_session="sess-project",
        consolidation_status=ConsolidationStatus.FRAGILE,
    )
    # Identical text+scope+topic, different tool+session → independent corroboration.
    candidate = existing.clone(
        fact_id="01TESTDREAMMERGE0000000002",
        source_tool="copilot",
        source_session="sess-other",
    )

    consolidated = DreamPipeline(project_dir).consolidate([existing], [candidate])

    active = [fact for fact in consolidated if fact.superseded_by is None]
    assert len(active) == 1
    assert active[0].fact_id == existing.fact_id
    assert "copilot" in active[0].corroborated_by_tools
    assert candidate.fact_id in active[0].corroborated_by_facts
    assert active[0].consolidation_status == ConsolidationStatus.STABLE


def test_consolidate_keeps_same_text_in_different_scopes(project_dir: Path) -> None:
    existing = Fact(
        fact_id="01TESTDREAMSCOPE0000000001",
        text="postgres runs on 5433 in dev",
        scope=Scope.PROJECT,
        topic="devenv",
        encoding_strength=3,
        memory_type=MemoryType.EXPLICIT_SEMANTIC,
        verification=Verification.CORROBORATED,
        source_type=SourceType.TOOL_OUTPUT,
        source_tool="codex",
        source_session="sess-project",
        consolidation_status=ConsolidationStatus.STABLE,
    )
    candidate = existing.clone(
        fact_id="01TESTDREAMSCOPE0000000002",
        scope=Scope.USER,
        source_tool="copilot",
        source_session="sess-user",
    )

    consolidated = DreamPipeline(project_dir).consolidate([existing], [candidate])

    assert len([fact for fact in consolidated if fact.superseded_by is None]) == 2
    assert all(not fact.corroborated_by_facts for fact in consolidated)


def test_tombstone_suppresses_gap_resurrection(project_dir: Path, project_repo: Path) -> None:
    emit_gap_signal(
        project_repo,
        query="devenv postgres",
        resolution_context="agent read config",
        proposed_fact="postgres runs on 5433 in dev",
        session="2026-04-11-gap-a",
    )
    DreamPipeline(project_dir).run(force=True)
    fact = next(f for f in load_all_facts(project_repo, include_superseded=False) if "5433" in f.text)
    forget_fact(project_repo, fact.fact_id)

    emit_gap_signal(
        project_repo,
        query="devenv postgres",
        resolution_context="agent read config",
        proposed_fact="postgres runs on 5433 in dev",
        session="2026-04-11-gap-b",
    )
    DreamPipeline(project_dir).run(force=True)

    assert all("5433" not in fact.text for fact in load_all_facts(project_repo, include_superseded=False))


def test_dream_weekly_lint_skips_when_recent(project_dir: Path, project_repo: Path) -> None:
    recent = datetime.now(tz=UTC) - timedelta(days=6)
    encoded_recent = recent.isoformat().replace("+00:00", "Z")
    (project_repo / ".umx.json").write_text(
        json.dumps(
            {"dream": {"last_lint": encoded_recent}, "facts": {}},
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    cfg = default_config()
    cfg.dream.lint_interval = "weekly"

    result = DreamPipeline(project_dir, config=cfg).run(force=True)

    assert result.status == "ok"
    assert result.lint == {"ran": False, "reason": "weekly-not-due"}
    payload = json.loads((project_repo / ".umx.json").read_text())
    assert payload["dream"]["last_lint"] == encoded_recent


def test_dream_invalid_last_lint_is_treated_as_due(project_dir: Path, project_repo: Path) -> None:
    (project_repo / ".umx.json").write_text(
        json.dumps(
            {"dream": {"last_lint": "2026-04-17T00:00:00"}, "facts": {}},
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    cfg = default_config()
    cfg.dream.lint_interval = "weekly"

    result = DreamPipeline(project_dir, config=cfg).run(force=True)

    assert result.status == "ok"
    assert result.lint == {"ran": True, "reason": "first-run"}
    payload = json.loads((project_repo / "meta" / "lint-state.json").read_text())
    assert payload["last_lint"].endswith("Z")


def test_dream_hybrid_search_prewarms_embeddings_for_final_fact_ids(
    monkeypatch,
    project_dir: Path,
    project_repo: Path,
) -> None:
    existing = Fact(
        fact_id="01TESTFACT0000000000000901",
        text="deploy uses the shared staging cluster",
        scope=Scope.PROJECT,
        topic="general",
        encoding_strength=4,
        memory_type=MemoryType.EXPLICIT_SEMANTIC,
        verification=Verification.CORROBORATED,
        source_type=SourceType.GROUND_TRUTH_CODE,
        source_tool="manual",
        source_session="2026-04-10-manual",
        consolidation_status=ConsolidationStatus.STABLE,
    )
    add_fact(project_repo, existing, auto_commit=False)
    emit_gap_signal(
        project_repo,
        query="deploy flow",
        resolution_context="agent inspected deployment docs",
        proposed_fact="deploy uses the shared staging cluster",
        session="2026-04-17-hybrid-prewarm",
    )
    cfg = default_config()
    cfg.search.backend = "hybrid"
    monkeypatch.setattr("umx.dream.pipeline.embeddings_available", lambda config=None: True)
    monkeypatch.setattr(
        "umx.search_semantic.embed_fact",
        lambda fact, config=None: [1.0, 0.0, 0.0],
    )

    result = DreamPipeline(project_dir, config=cfg).run(force=True)

    assert result.status == "ok"
    payload = json.loads((project_repo / ".umx.json").read_text())
    assert payload["facts"][existing.fact_id]["embedding"] == [1.0, 0.0, 0.0]


def test_dream_hybrid_search_warns_when_embeddings_unavailable(
    monkeypatch,
    caplog,
    project_dir: Path,
    project_repo: Path,
) -> None:
    emit_gap_signal(
        project_repo,
        query="service owner",
        resolution_context="agent checked the runbook",
        proposed_fact="service ownership is tracked in deploy docs",
        session="2026-04-17-hybrid-warning",
    )
    cfg = default_config()
    cfg.search.backend = "hybrid"
    monkeypatch.setattr("umx.dream.pipeline.embeddings_available", lambda config=None: False)
    monkeypatch.setattr("umx.search_semantic.embed_fact", lambda fact, config=None: None)
    caplog.set_level("WARNING", logger="umx.dream.pipeline")

    result = DreamPipeline(project_dir, config=cfg).run(force=True)

    assert result.status == "ok"
    assert "embedding prewarm skipped" in caplog.text


def test_procedure_regression_trigger_writes_draft(
    monkeypatch,
    project_dir: Path,
    project_repo: Path,
    tmp_path: Path,
) -> None:
    from umx.dream import imx_triggers as imx_mod
    from umx.dream.imx_triggers import ImxDreamTrigger

    # Set up a procedures dir in the project with a matching procedure file
    procedures_dir = project_dir / "procedures"
    procedures_dir.mkdir()
    (procedures_dir / "code-review.md").write_text(
        "# Code Review\ntask_class: implementation\nReview all changes carefully.",
        encoding="utf-8",
    )

    raw_trigger = {
        "trigger_type": "procedure_regression",
        "query": "code review skipped in fast-path",
        "source": "imx",
        "ts": "2026-05-22T00:00:00Z",
        "context": {"task_class": "implementation", "task_id": "task-99"},
    }
    fake_trigger = ImxDreamTrigger(
        trigger_type="procedure_regression",
        source="imx",
        query="code review skipped in fast-path",
        ts="2026-05-22T00:00:00Z",
        context={"task_class": "implementation", "task_id": "task-99"},
        raw=raw_trigger,
    )
    monkeypatch.setattr(imx_mod, "read_imx_triggers", lambda *a, **kw: [fake_trigger])

    drafts_dir = tmp_path / "drafts"
    pipeline = DreamPipeline(project_dir)
    # Patch _write_procedure_revision_drafts to use our tmp drafts_dir
    original_method = pipeline._write_procedure_revision_drafts

    def patched_write(now, **kwargs):
        return original_method(now, drafts_dir=drafts_dir)

    monkeypatch.setattr(pipeline, "_write_procedure_revision_drafts", patched_write)

    result = pipeline.run(force=True)

    assert result.status == "ok"

    draft_files = list(drafts_dir.glob("procedure-revision-*.md"))
    assert len(draft_files) == 1, f"expected 1 draft file, found: {draft_files}"
    body = draft_files[0].read_text(encoding="utf-8")
    assert "<!-- umx-pr-type: procedure_revision -->" in body
    assert "code-review" in body


def test_dream_candidate_dicts_to_facts_converts_trigger_dicts(project_repo: Path) -> None:
    from umx.dream.extract import dream_candidate_dicts_to_facts

    facts = dream_candidate_dicts_to_facts(
        project_repo,
        [
            {
                "source": "imx:detector",
                "trigger_type": "query_gap",
                "content": "Recurring query gap about deployment rollback steps.",
                "task_class": "ops",
                "metadata": {"query": "rollback"},
            },
            {"trigger_type": "policy_drift", "content": ""},  # empty content -> skipped
            {"not": "a candidate"},  # no content -> skipped
        ],
    )

    assert len(facts) == 1
    fact = facts[0]
    assert "deployment rollback" in fact.text
    assert fact.encoding_strength == 1
    assert fact.consolidation_status == ConsolidationStatus.FRAGILE
    assert fact.source_type == SourceType.LLM_INFERENCE
    assert fact.source_tool == "imx:detector"
    assert fact.encoding_context.get("trigger_type") == "query_gap"
    assert fact.encoding_context.get("task_class") == "ops"


def test_dream_candidate_chain_depth_decays_confidence(project_repo: Path) -> None:
    from umx.dream.decay import decay_confidence_by_chain_depth
    from umx.dream.extract import dream_candidate_dicts_to_facts

    facts = dream_candidate_dicts_to_facts(
        project_repo,
        [
            {
                "source": "imx:handoff",
                "trigger_type": "large_task_completion",
                "content": "Three-hop handoff concluded the cache layer should use redis.",
                "metadata": {"imx_context": {"chain_depth": 3}},
            },
            {
                "source": "imx:direct",
                "trigger_type": "query_gap",
                "content": "Direct observation: the build pins node 20.",
            },
        ],
    )

    by_text = {f.text[:20]: f for f in facts}
    decayed = next(f for f in facts if "Three-hop" in f.text)
    direct = next(f for f in facts if "Direct observation" in f.text)

    # chain_depth=3 decays the base 0.5 confidence; direct (no chain) stays at 0.5.
    assert decayed.confidence == round(decay_confidence_by_chain_depth(0.5, 3), 4)
    assert decayed.confidence < 0.5
    assert decayed.encoding_context.get("chain_depth") == 3
    assert direct.confidence == 0.5
    assert "chain_depth" not in direct.encoding_context


def test_gather_ingests_imx_triggers_excluding_procedure_regression(
    monkeypatch,
    project_dir: Path,
    project_repo: Path,
) -> None:
    from umx.dream import imx_triggers as imx_mod
    from umx.dream.imx_triggers import ImxDreamTrigger

    triggers = [
        ImxDreamTrigger(
            trigger_type="entrenchment_risk",
            source="imx",
            query="route card never challenged",
            ts="2026-05-22T00:00:00Z",
            context={"task_class": "ops"},
        ),
        ImxDreamTrigger(
            trigger_type="procedure_regression",
            source="imx",
            query="should be excluded — has its own draft-PR path",
            ts="2026-05-22T00:00:00Z",
            context={},
        ),
    ]
    monkeypatch.setattr(imx_mod, "read_imx_triggers", lambda *a, **kw: triggers)

    candidates = DreamPipeline(project_dir).gather()
    texts = " ".join(c.text for c in candidates)

    assert "entrenchment_risk" in texts
    assert "route card never challenged" in texts
    assert "should be excluded" not in texts


def test_gather_ingests_entrenchment_risk_from_local_procedures(
    project_dir: Path,
    project_repo: Path,
) -> None:
    procedures_dir = project_repo / "procedures"
    procedures_dir.mkdir(parents=True, exist_ok=True)
    # Non-human source + high confidence => two reasons => high entrenchment risk.
    (procedures_dir / "auto-deploy.md").write_text(
        "# Auto Deploy\n"
        "<!-- id:p-auto conf:0.95 src:copilot -->\n\n"
        "## Triggers\n- deploy to production\n\n"
        "## Steps\nRun the deploy script without review.\n",
        encoding="utf-8",
    )

    candidates = DreamPipeline(project_dir).gather()
    entrenchment_facts = [c for c in candidates if "Entrenchment risk" in c.text]

    assert entrenchment_facts, "expected an entrenchment-risk candidate from the procedure"
    fact = entrenchment_facts[0]
    assert "p-auto" in fact.text
    assert fact.source_tool == "imx:entrenchment-detector"
    assert fact.consolidation_status == ConsolidationStatus.FRAGILE


def test_gather_ingests_completed_workspace_task_audit(
    project_dir: Path,
    project_repo: Path,
) -> None:
    done = project_dir / "workspace" / "tasks" / "task-done"
    done.mkdir(parents=True, exist_ok=True)
    (done / "plan.yaml").write_text("status: completed\n", encoding="utf-8")
    (done / "audit.jsonl").write_text(
        json.dumps({"summary": "The migration runner retries failed batches three times."}) + "\n",
        encoding="utf-8",
    )

    # In-progress task: audit present but plan not complete -> skipped.
    wip = project_dir / "workspace" / "tasks" / "task-wip"
    wip.mkdir(parents=True, exist_ok=True)
    (wip / "plan.yaml").write_text("status: in_progress\n", encoding="utf-8")
    (wip / "audit.jsonl").write_text(
        json.dumps({"summary": "Half-finished refactor that should not be ingested yet."}) + "\n",
        encoding="utf-8",
    )

    candidates = DreamPipeline(project_dir).gather()
    texts = " ".join(c.text for c in candidates)

    assert "migration runner retries failed batches" in texts
    assert "Half-finished refactor" not in texts

    audit_fact = next(c for c in candidates if "migration runner" in c.text)
    assert audit_fact.source_tool == "workspace-task-audit"
    assert audit_fact.source_session == "task-done"
    assert audit_fact.consolidation_status == ConsolidationStatus.FRAGILE
