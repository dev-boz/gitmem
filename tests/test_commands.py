from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from umx.cli import main
from umx.config import default_config, save_config
from umx.git_ops import git_add_and_commit
from umx.governance import assert_governance_pr_body
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
from umx.tombstones import load_tombstones
from umx.scope import config_path


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


def test_merge_apply_persists_resolution(project_dir: Path, project_repo: Path) -> None:
    add_fact(project_repo, _make_fact("FACT_D3", "postgres runs on 5433 in dev"))
    add_fact(project_repo, _make_fact("FACT_D4", "postgres runs on 5432 in dev"))

    runner = CliRunner()
    result = runner.invoke(main, ["merge", "--cwd", str(project_dir)])

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert len(data) == 1
    facts = load_all_facts(project_repo, include_superseded=True)
    loser = next(fact for fact in facts if fact.fact_id == data[0]["loser_id"])
    assert loser.superseded_by == data[0]["winner_id"]


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


def test_audit_compare_derived_counts_duplicate_facts() -> None:
    from umx.audit import compare_derived

    existing = [
        _make_fact("E1", "postgres runs on 5433"),
        _make_fact("E2", "postgres runs on 5433"),
    ]
    rederived = [
        _make_fact("R1", "postgres runs on 5433"),
    ]

    result = compare_derived(existing, rederived)
    assert result["matching"] == 1
    assert result["missing_from_existing"] == 0
    assert result["missing_from_rederived"] == 1


def test_audit_rederive_opens_correction_pr(project_dir: Path, project_repo: Path, monkeypatch) -> None:
    from umx.dream.pipeline import DreamPipeline
    from umx.sessions import write_session

    write_session(
        project_repo,
        {"session_id": "2026-01-15-rederive-pr"},
        [
            {"role": "assistant", "content": "The API server runs on port 8080 for local development."},
        ],
        config=default_config(),
    )
    add_fact(project_repo, _make_fact("E2", "redis cache is enabled"), auto_commit=False)
    git_add_and_commit(project_repo, message="seed audit drift baseline")

    cfg = default_config()
    cfg.dream.mode = "remote"
    cfg.org = "memory-org"
    save_config(config_path(), cfg)

    proposals = []
    monkeypatch.setattr(
        DreamPipeline,
        "_push_and_open_pr",
        lambda self, proposal: proposals.append(proposal) or 23,
    )

    result = CliRunner().invoke(main, ["audit", "--cwd", str(project_dir), "--rederive"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["matching"] == 0
    assert payload["missing_from_existing"] >= 1
    assert payload["missing_from_rederived"] == 1
    assert payload["correction_pr"]["status"] == "opened"
    assert payload["correction_pr"]["pr_number"] == 23
    assert len(proposals) == 1
    proposal = proposals[0]
    assert proposal.branch.startswith("proposal/rederive-correction-")
    pr_payload = assert_governance_pr_body(proposal.body)
    assert pr_payload is not None
    assert len(pr_payload["added"]) >= 1
    assert {entry["fact_id"] for entry in pr_payload["tombstoned"]} == {"E2"}

    current_branch = subprocess.run(
        ["git", "-C", str(project_repo), "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert current_branch.stdout.strip() == "main"
    assert {fact.fact_id for fact in load_all_facts(project_repo, include_superseded=False)} == {"E2"}


def test_audit_rederive_opens_correction_pr_for_metadata_only_drift(
    project_dir: Path,
    project_repo: Path,
    monkeypatch,
) -> None:
    from umx.dream.pipeline import DreamPipeline

    add_fact(
        project_repo,
        _make_fact("E1", "postgres runs on 5433", topic="devenv", encoding_strength=3),
        auto_commit=False,
    )
    git_add_and_commit(project_repo, message="seed metadata drift baseline")

    cfg = default_config()
    cfg.dream.mode = "remote"
    cfg.org = "memory-org"
    save_config(config_path(), cfg)

    monkeypatch.setattr(
        "umx.audit.rederive_from_sessions",
        lambda repo_dir, session_ids=None, config=None: [
            _make_fact("R1", "postgres runs on 5433", topic="runtime", encoding_strength=5),
        ],
    )

    proposals = []
    monkeypatch.setattr(
        DreamPipeline,
        "_push_and_open_pr",
        lambda self, proposal: proposals.append(proposal) or 29,
    )

    result = CliRunner().invoke(main, ["audit", "--cwd", str(project_dir), "--rederive"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["matching"] == 0
    assert payload["missing_from_existing"] == 1
    assert payload["missing_from_rederived"] == 1
    assert payload["correction_pr"]["status"] == "opened"
    assert payload["correction_pr"]["pr_number"] == 29
    assert len(proposals) == 1
    proposal = proposals[0]
    assert set(proposal.files_changed) == {
        "facts/topics/devenv.md",
        "facts/topics/runtime.md",
        "meta/tombstones.jsonl",
    }
    pr_payload = assert_governance_pr_body(proposal.body)
    assert pr_payload is not None
    assert len(pr_payload["added"]) == 1
    assert len(pr_payload["tombstoned"]) == 1


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


# ── promotion tests ──────────────────────────────────────────────


def test_promote_to_project_moves_fact_into_project_scope(
    project_dir: Path,
    project_repo: Path,
) -> None:
    fact = _make_fact(
        "FACT_PROMOTE_PROJECT",
        "staging deploy docs live under docs/deploy",
        topic="deploy",
        scope=Scope.FOLDER,
    )
    add_fact(project_repo, fact)

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["promote", "--cwd", str(project_dir), "--fact", fact.fact_id, "--to", "project"],
    )

    assert result.exit_code == 0, result.output
    assert result.output.strip() == f"{fact.fact_id} -> project"

    facts = load_all_facts(project_repo, include_superseded=False)
    promoted = next(item for item in facts if item.fact_id == fact.fact_id)
    assert promoted.scope == Scope.PROJECT
    assert promoted.file_path is not None
    assert promoted.file_path.relative_to(project_repo).as_posix() == "facts/topics/deploy.md"


def test_promote_to_principle_moves_fact_into_principles(
    project_dir: Path,
    project_repo: Path,
) -> None:
    fact = _make_fact(
        "FACT_PROMOTE_PRINCIPLE",
        "prefer additive migrations over destructive rewrites",
        topic="migrations",
    )
    add_fact(project_repo, fact)

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["promote", "--cwd", str(project_dir), "--fact", fact.fact_id, "--to", "principle"],
    )

    assert result.exit_code == 0, result.output
    assert result.output.strip() == f"{fact.fact_id} -> principle"

    facts = load_all_facts(project_repo, include_superseded=False)
    promoted = next(item for item in facts if item.fact_id == fact.fact_id)
    assert promoted.file_path is not None
    assert promoted.file_path.relative_to(project_repo).as_posix() == "principles/topics/migrations.md"


def test_promote_folder_fact_to_principle_moves_into_principles(
    project_dir: Path,
    project_repo: Path,
) -> None:
    fact = _make_fact(
        "FACT_PROMOTE_FOLDER_PRINCIPLE",
        "shared config fragments live under config/shared",
        topic="config",
        scope=Scope.FOLDER,
    )
    add_fact(project_repo, fact)

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["promote", "--cwd", str(project_dir), "--fact", fact.fact_id, "--to", "principle"],
    )

    assert result.exit_code == 0, result.output
    facts = load_all_facts(project_repo, include_superseded=False)
    promoted = next(item for item in facts if item.fact_id == fact.fact_id)
    assert promoted.scope == Scope.PROJECT
    assert promoted.file_path is not None
    assert promoted.file_path.relative_to(project_repo).as_posix() == "principles/topics/config.md"


def test_promote_to_user_moves_fact_into_user_scope(
    project_dir: Path,
    project_repo: Path,
    user_repo: Path,
) -> None:
    fact = _make_fact(
        "FACT_PROMOTE_USER",
        "release notes live in docs/releases",
        topic="docs",
    )
    add_fact(project_repo, fact)

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["promote", "--cwd", str(project_dir), "--fact", fact.fact_id, "--to", "user"],
    )

    assert result.exit_code == 0, result.output
    assert result.output.strip() == f"{fact.fact_id} -> user"
    project_facts = load_all_facts(project_repo, include_superseded=False)
    assert all(item.fact_id != fact.fact_id for item in project_facts)
    user_facts = load_all_facts(user_repo, include_superseded=False)
    promoted = next(item for item in user_facts if item.fact_id == fact.fact_id)
    assert promoted.scope == Scope.USER


def test_promote_invalid_destination_leaves_source_fact_untouched(
    project_dir: Path,
    project_repo: Path,
) -> None:
    fact = _make_fact(
        "FACT_PROMOTE_INVALID",
        "feature flags live in settings.toml",
        topic="config",
    )
    add_fact(project_repo, fact)

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["promote", "--cwd", str(project_dir), "--fact", fact.fact_id, "--to", "invalid"],
    )

    assert result.exit_code != 0
    facts = load_all_facts(project_repo, include_superseded=False)
    assert any(item.fact_id == fact.fact_id for item in facts)


def test_confirm_marks_fact_human_confirmed(
    project_dir: Path,
    project_repo: Path,
) -> None:
    fact = _make_fact("FACT_CONFIRM", "staging uses blue/green cutovers", topic="deploy")
    add_fact(project_repo, fact)

    runner = CliRunner()
    result = runner.invoke(main, ["confirm", "--cwd", str(project_dir), "--fact", fact.fact_id])

    assert result.exit_code == 0, result.output
    assert result.output.strip() == fact.fact_id
    updated = next(item for item in load_all_facts(project_repo, include_superseded=False) if item.fact_id == fact.fact_id)
    assert updated.encoding_strength == 5
    assert updated.verification == Verification.HUMAN_CONFIRMED
    assert updated.consolidation_status == ConsolidationStatus.STABLE


def test_forget_fact_creates_tombstone(
    project_dir: Path,
    project_repo: Path,
) -> None:
    fact = _make_fact("FACT_FORGET", "staging deploys need smoke checks", topic="deploy")
    add_fact(project_repo, fact)
    tombstones_path = project_repo / "meta" / "tombstones.jsonl"
    if tombstones_path.exists():
        git_add_and_commit(project_repo, paths=[tombstones_path], message="baseline tombstones")

    runner = CliRunner()
    result = runner.invoke(main, ["forget", "--cwd", str(project_dir), "--fact", fact.fact_id])

    assert result.exit_code == 0, result.output
    assert result.output.strip() == fact.fact_id
    facts = load_all_facts(project_repo, include_superseded=False)
    assert all(item.fact_id != fact.fact_id for item in facts)
    tombstones = load_tombstones(project_repo)
    assert any(item.fact_id == fact.fact_id for item in tombstones)


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
