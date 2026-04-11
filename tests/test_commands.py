from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from umx.cli import main
from umx.config import default_config
from umx.memory import add_fact, load_all_facts
from umx.models import (
    ConsolidationStatus,
    Fact,
    MemoryType,
    Provenance,
    Scope,
    SourceType,
    Verification,
)


def _make_fact(
    fact_id: str,
    text: str,
    source_type: SourceType = SourceType.LLM_INFERENCE,
    topic: str = "devenv",
    source_session: str = "sess-001",
    **overrides,
) -> Fact:
    values = {
        "fact_id": fact_id,
        "text": text,
        "scope": Scope.PROJECT,
        "topic": topic,
        "encoding_strength": 3,
        "memory_type": MemoryType.EXPLICIT_SEMANTIC,
        "verification": Verification.SELF_REPORTED,
        "source_type": source_type,
        "source_tool": "codex",
        "source_session": source_session,
        "consolidation_status": ConsolidationStatus.FRAGILE,
        "provenance": Provenance(extracted_by="test", sessions=[source_session]),
    }
    values.update(overrides)
    return Fact(**values)


# ── merge tests ──────────────────────────────────────────────────


def test_merge_finds_conflicts(project_repo: Path) -> None:
    from umx.merge import find_conflicts

    # Two facts on same topic with shared terms and differing numbers → conflict
    add_fact(project_repo, _make_fact("FACT_A1", "postgres runs on 5433 in dev"), auto_commit=False)
    add_fact(project_repo, _make_fact("FACT_A2", "postgres runs on 5432 in dev"), auto_commit=False)

    pairs = find_conflicts(project_repo)
    assert len(pairs) == 1
    ids = {pairs[0][0].fact_id, pairs[0][1].fact_id}
    assert ids == {"FACT_A1", "FACT_A2"}


def test_merge_arbitrates_by_trust_score(project_repo: Path) -> None:
    from umx.merge import arbitrate_conflict

    low = _make_fact("FACT_B1", "postgres runs on 5433 in dev", encoding_strength=1)
    high = _make_fact("FACT_B2", "postgres runs on 5432 in dev", encoding_strength=5)

    winner, loser, reason = arbitrate_conflict(low, high, default_config())
    assert winner.fact_id == "FACT_B2"
    assert loser.fact_id == "FACT_B1"
    assert "trust_score" in reason


def test_merge_ground_truth_wins(project_repo: Path) -> None:
    from umx.merge import arbitrate_conflict

    gt = _make_fact(
        "FACT_C1",
        "postgres runs on 5433 in dev",
        source_type=SourceType.GROUND_TRUTH_CODE,
    )
    llm = _make_fact(
        "FACT_C2",
        "postgres runs on 5432 in dev",
        source_type=SourceType.LLM_INFERENCE,
    )

    winner, loser, reason = arbitrate_conflict(gt, llm, default_config())
    assert winner.fact_id == "FACT_C1"
    assert "ground_truth" in reason


def test_merge_dry_run(project_repo: Path) -> None:
    add_fact(project_repo, _make_fact("FACT_D1", "postgres runs on 5433 in dev"), auto_commit=False)
    add_fact(project_repo, _make_fact("FACT_D2", "postgres runs on 5432 in dev"), auto_commit=False)

    runner = CliRunner()
    result = runner.invoke(main, ["merge", "--cwd", str(project_repo.parent.parent.parent / "project"), "--dry-run"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert len(data) == 1
    assert "winner_id" in data[0]
    assert "loser_id" in data[0]

    # Dry run should NOT set superseded_by
    facts = load_all_facts(project_repo, include_superseded=True)
    for fact in facts:
        assert fact.superseded_by is None


# ── audit tests ──────────────────────────────────────────────────


def test_audit_rederive(project_repo: Path) -> None:
    from umx.audit import rederive_from_sessions
    from umx.sessions import write_session

    write_session(
        project_repo,
        {"session_id": "2026-01-15-rederive01"},
        [
            {"role": "assistant", "content": "The API server runs on port 8080 for local development."},
        ],
        config=default_config(),
    )

    facts = rederive_from_sessions(project_repo, config=default_config())
    # Should extract at least 1 fact from the session
    assert len(facts) >= 1
    assert any("8080" in f.text for f in facts)


def test_audit_compare_derived() -> None:
    from umx.audit import compare_derived

    existing = [
        _make_fact("E1", "postgres runs on 5433"),
        _make_fact("E2", "redis cache is enabled"),
    ]
    rederived = [
        _make_fact("R1", "postgres runs on 5433"),
        _make_fact("R2", "celery worker count is 4"),
    ]

    result = compare_derived(existing, rederived)
    assert result["matching"] == 1  # "postgres runs on 5433"
    assert result["missing_from_existing"] == 1  # "celery worker count is 4"
    assert result["missing_from_rederived"] == 1  # "redis cache is enabled"


# ── import tests ─────────────────────────────────────────────────


def test_import_adapter(project_dir: Path, project_repo: Path) -> None:
    # Create a CLAUDE.md in the project directory
    claude_md = project_dir / "CLAUDE.md"
    claude_md.write_text(
        "# Project\n\n"
        "## Architecture\n"
        "- The backend uses FastAPI with uvicorn server\n"
        "- The database connection pool has a max size of 20\n"
    )

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["import", "--cwd", str(project_dir), "--adapter", "claude-code"],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["imported"] >= 1


def test_import_dry_run(project_dir: Path, project_repo: Path) -> None:
    claude_md = project_dir / "CLAUDE.md"
    claude_md.write_text(
        "# Project\n\n"
        "## Architecture\n"
        "- The backend uses FastAPI with uvicorn server\n"
    )

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["import", "--cwd", str(project_dir), "--adapter", "claude-code", "--dry-run"],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["dry_run"] is True
    assert data["facts_found"] >= 1

    # Dry run should not add any facts
    facts = load_all_facts(project_repo, include_superseded=False)
    # Filter to adapter-sourced facts only
    adapter_facts = [f for f in facts if f.source_tool == "claude-code"]
    assert len(adapter_facts) == 0


# ── purge tests ──────────────────────────────────────────────────


def test_purge_session_removes_facts(project_repo: Path) -> None:
    from umx.purge import purge_session

    add_fact(
        project_repo,
        _make_fact("FACT_P1", "redis cache port is 6379", source_session="purge-sess"),
        auto_commit=False,
    )
    add_fact(
        project_repo,
        _make_fact("FACT_P2", "celery worker count is 4", source_session="purge-sess"),
        auto_commit=False,
    )
    add_fact(
        project_repo,
        _make_fact("FACT_P3", "nginx runs on port 80", source_session="keep-sess"),
        auto_commit=False,
    )

    result = purge_session(project_repo, "purge-sess")
    assert result["facts_removed"] == 2

    remaining = load_all_facts(project_repo, include_superseded=True)
    remaining_ids = {f.fact_id for f in remaining}
    assert "FACT_P1" not in remaining_ids
    assert "FACT_P2" not in remaining_ids
    assert "FACT_P3" in remaining_ids


def test_purge_session_removes_file(project_repo: Path) -> None:
    from umx.purge import purge_session
    from umx.sessions import write_session

    write_session(
        project_repo,
        {"session_id": "2026-01-15-purgetest01"},
        [{"role": "user", "content": "hello"}],
        config=default_config(),
    )

    from umx.sessions import session_path

    spath = session_path(project_repo, "2026-01-15-purgetest01")
    assert spath.exists()

    result = purge_session(project_repo, "2026-01-15-purgetest01")
    assert result["session_removed"] is True
    assert not spath.exists()
