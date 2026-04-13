from __future__ import annotations

from pathlib import Path

from umx.dream.extract import mark_sessions_gathered, session_records_to_facts
from umx.dream.gates import read_dream_state
from umx.dream.pipeline import DreamPipeline
from umx.inject import emit_gap_signal
from umx.memory import find_fact_by_id, load_all_facts
from umx.models import ConsolidationStatus, MemoryType, Scope, SourceType, Verification
from umx.sessions import write_session
from umx.tombstones import forget_fact


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
                    "The API key is sk-ant-fake1234567890abcdef."
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
        assert fact.provenance.extracted_by == "dream-gather"
        assert "2026-01-15-abc123" in fact.provenance.sessions

    # "Let me check the config" should be filtered out
    texts = [f.text for f in facts]
    assert all("Let me" not in t for t in texts)

    # Redaction was applied — the Anthropic key should be redacted
    all_text = " ".join(texts)
    assert "sk-ant-fake1234567890abcdef" not in all_text
    assert "[REDACTED:" in all_text

    # Second call with same sessions: nothing new
    mark_sessions_gathered(project_repo, ["2026-01-15-abc123"])
    facts2 = session_records_to_facts(project_repo)
    assert len(facts2) == 0

    # Verify state was persisted
    state = read_dream_state(project_repo)
    assert "2026-01-15-abc123" in state.get("last_gathered_sessions", [])


def test_session_gather_ignores_progress_only_codex_commentary(
    project_dir: Path, project_repo: Path
) -> None:
    write_session(
        project_repo,
        meta={"session_id": "2026-04-13-codex-progress-only"},
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
