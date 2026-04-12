from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from umx.cli import main
from umx.config import default_config
from umx.conventions import ConventionSet
from umx.dream.pipeline import DreamPipeline
from umx.governance import (
    PRProposal,
    branch_name_for_dream,
    classify_pr_labels,
    generate_l1_pr,
    generate_l2_review,
)
from umx.models import (
    ConsolidationStatus,
    Fact,
    MemoryType,
    Scope,
    SourceType,
    Verification,
)


def _make_fact(
    fact_id: str = "01TEST000000000000000001",
    text: str = "test fact that is long enough to pass validation checks",
    topic: str = "general",
    encoding_strength: int = 3,
    confidence: float = 0.9,
    scope: Scope = Scope.PROJECT,
    source_type: SourceType = SourceType.LLM_INFERENCE,
    source_tool: str = "session-extract",
    source_session: str = "2026-01-15-abc123",
    conflicts_with: list[str] | None = None,
    superseded_by: str | None = None,
    **kwargs,
) -> Fact:
    return Fact(
        fact_id=fact_id,
        text=text,
        scope=scope,
        topic=topic,
        encoding_strength=encoding_strength,
        memory_type=MemoryType.EXPLICIT_SEMANTIC,
        verification=Verification.SELF_REPORTED,
        source_type=source_type,
        confidence=confidence,
        source_tool=source_tool,
        source_session=source_session,
        conflicts_with=conflicts_with or [],
        superseded_by=superseded_by,
        **kwargs,
    )


def _connect_origin(repo_dir: Path, remote_dir: Path) -> None:
    subprocess.run(["git", "init", "--bare", str(remote_dir)], capture_output=True, check=True)
    subprocess.run(
        ["git", "-C", str(repo_dir), "remote", "add", "origin", str(remote_dir)],
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repo_dir), "push", "-u", "origin", "main"],
        capture_output=True,
        check=True,
    )


def test_branch_name_generation() -> None:
    name = branch_name_for_dream("l1", "extract session data")
    assert name.startswith("dream/l1/")
    # Should contain date in YYYYMMDD format
    parts = name.split("/")
    assert len(parts) == 3
    date_desc = parts[2]
    assert re.match(r"\d{8}-extract-session-data", date_desc)

    name_l2 = branch_name_for_dream("l2", "review merge")
    assert name_l2.startswith("dream/l2/")


def test_pr_proposal_format() -> None:
    facts = [_make_fact()]
    pr = generate_l1_pr(facts, ["abc123"], Path("/fake"))
    assert isinstance(pr, PRProposal)
    assert pr.title.startswith("[dream/l1]")
    assert pr.branch.startswith("dream/l1/")
    assert isinstance(pr.labels, list)
    assert isinstance(pr.files_changed, list)
    assert len(pr.body) > 0
    assert "abc123" in pr.body


def test_l1_pr_generation() -> None:
    facts = [
        _make_fact(fact_id="01TEST000000000000000001", text="postgres uses port 5433", topic="devenv", encoding_strength=3),
        _make_fact(fact_id="01TEST000000000000000002", text="redis on port 6379", topic="devenv", encoding_strength=4),
    ]
    pr = generate_l1_pr(facts, ["2026-01-15-abc123", "2026-01-16-def456"], Path("/repo"))

    assert "[dream/l1]" in pr.title
    assert "2026-01-15-abc123" in pr.body
    assert "2026-01-16-def456" in pr.body
    assert "3-4" in pr.body  # strength range
    assert "Facts extracted:** 2" in pr.body
    assert "facts/topics/devenv.md" in pr.files_changed
    assert len(pr.labels) > 0


def test_l2_review_auto_merge() -> None:
    pr = PRProposal(
        title="[dream/l1] test",
        body="test body",
        branch="dream/l1/20260115-test",
        labels=["confidence:high", "impact:local", "type:extraction"],
        files_changed=["facts/topics/devenv.md"],
    )
    facts = [_make_fact(confidence=0.9, encoding_strength=2)]
    result = generate_l2_review(pr, ConventionSet(), facts)
    assert result["action"] == "approve"
    assert "Auto-approved" in result["reason"]
    assert result["violations"] == []


def test_l2_review_escalate() -> None:
    pr = PRProposal(
        title="[dream/l1] test",
        body="test body",
        branch="dream/l1/20260115-test",
        labels=["confidence:high", "impact:global", "type:extraction"],
        files_changed=["facts/topics/devenv.md"],
    )
    facts = [_make_fact(scope=Scope.USER)]
    result = generate_l2_review(pr, ConventionSet(), facts)
    assert result["action"] == "escalate"
    assert "impact:global" in result["reason"]


def test_l2_review_escalate_strong_deletions() -> None:
    pr = PRProposal(
        title="[dream/l1] test",
        body="test body",
        branch="dream/l1/20260115-test",
        labels=["confidence:high", "impact:local", "type:extraction"],
        files_changed=["facts/topics/devenv.md"],
    )
    facts = [_make_fact(encoding_strength=4, superseded_by="some-other-fact")]
    result = generate_l2_review(pr, ConventionSet(), facts)
    assert result["action"] == "escalate"
    assert "S:>=3" in result["reason"]


def test_l2_review_escalate_contradictions() -> None:
    pr = PRProposal(
        title="[dream/l1] test",
        body="test body",
        branch="dream/l1/20260115-test",
        labels=["confidence:high", "impact:local", "type:extraction"],
        files_changed=["facts/topics/devenv.md"],
    )
    facts = [_make_fact(conflicts_with=["other-fact-id"])]
    result = generate_l2_review(pr, ConventionSet(), facts)
    assert result["action"] == "escalate"
    assert "contradictions" in result["reason"]


def test_l2_review_reject_convention_violation() -> None:
    conventions = ConventionSet(
        topics={"devenv", "architecture"},
        phrasing_rules=["present tense only"],
    )
    pr = PRProposal(
        title="[dream/l1] test",
        body="test body",
        branch="dream/l1/20260115-test",
        labels=["confidence:high", "impact:local", "type:extraction"],
        files_changed=["facts/topics/unknown.md"],
    )
    # Fact with topic not in conventions
    facts = [_make_fact(topic="unknown-topic")]
    result = generate_l2_review(pr, conventions, facts)
    assert result["action"] == "reject"
    assert len(result["violations"]) > 0
    assert "unknown-topic" in result["violations"][0]


def test_classify_pr_labels() -> None:
    facts = [
        _make_fact(source_type=SourceType.DREAM_CONSOLIDATION, confidence=0.9),
        _make_fact(source_type=SourceType.USER_PROMPT, confidence=0.8),
    ]
    labels = classify_pr_labels(facts)
    assert "type:consolidation" in labels
    assert "type:extraction" in labels
    assert "confidence:high" in labels
    assert "impact:local" in labels

    # Test low confidence
    low_facts = [_make_fact(confidence=0.3)]
    low_labels = classify_pr_labels(low_facts)
    assert "confidence:low" in low_labels

    # Test global impact
    global_facts = [_make_fact(scope=Scope.USER)]
    global_labels = classify_pr_labels(global_facts)
    assert "impact:global" in global_labels


def test_sync_command_local_mode(project_dir: Path, umx_home: Path) -> None:
    runner = CliRunner()
    runner.invoke(main, ["init", "--org", "test-org"])
    runner.invoke(main, ["init-project", "--cwd", str(project_dir)])
    result = runner.invoke(main, ["sync", "--cwd", str(project_dir)])
    assert result.exit_code == 0
    assert "local mode: nothing to sync" in result.output


def test_dream_remote_mode_creates_branch(project_dir: Path, project_repo: Path) -> None:
    from umx.inject import emit_gap_signal

    emit_gap_signal(
        project_repo,
        query="test remote",
        resolution_context="testing",
        proposed_fact="remote mode creates branches",
        session="2026-01-15-remote",
    )

    runner = CliRunner()
    result = runner.invoke(main, ["dream", "--cwd", str(project_dir), "--force", "--mode", "remote"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["status"] == "ok"
    if data.get("added", 0) > 0:
        assert "pr_proposal" in data
        assert data["pr_proposal"]["branch"].startswith("dream/l1/")


def test_dream_hybrid_sessions_direct(project_dir: Path, project_repo: Path) -> None:
    from umx.inject import emit_gap_signal

    emit_gap_signal(
        project_repo,
        query="test hybrid",
        resolution_context="testing",
        proposed_fact="hybrid mode writes sessions directly",
        session="2026-01-15-hybrid",
    )

    runner = CliRunner()
    result = runner.invoke(main, ["dream", "--cwd", str(project_dir), "--force", "--mode", "hybrid"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["status"] == "ok"
    # Hybrid mode still completes and reports the retained snapshot.
    assert "facts retained" in data.get("message", "")


def test_dream_remote_branch_excludes_session_history(
    project_dir: Path,
    project_repo: Path,
    tmp_path: Path,
) -> None:
    from umx.inject import emit_gap_signal
    from umx.sessions import write_session

    remote = tmp_path / "remote.git"
    _connect_origin(project_repo, remote)

    write_session(
        project_repo,
        {"session_id": "2026-01-15-remote-session", "tool": "codex"},
        [{"role": "assistant", "content": "remote-only session context"}],
        auto_commit=True,
    )
    emit_gap_signal(
        project_repo,
        query="devenv postgres",
        resolution_context="testing",
        proposed_fact="remote branch keeps sessions out of PRs",
        session="2026-01-15-remote-gap",
    )

    cfg = default_config()
    cfg.dream.mode = "remote"
    cfg.org = "memory-org"
    result = DreamPipeline(project_dir, config=cfg).run(force=True)

    assert result.status == "ok"
    assert result.pr_proposal is not None

    diff = subprocess.run(
        [
            "git",
            "-C",
            str(project_repo),
            "diff",
            "--name-only",
            f"origin/main...{result.pr_proposal.branch}",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "sessions/" not in diff.stdout

    status = subprocess.run(
        ["git", "-C", str(project_repo), "status", "--short"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert status.stdout.strip() == ""


def test_dream_hybrid_pushes_sessions_but_not_facts_to_main(
    project_dir: Path,
    project_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from umx.inject import emit_gap_signal
    from umx.sessions import write_session

    remote = tmp_path / "hybrid-remote.git"
    _connect_origin(project_repo, remote)

    write_session(
        project_repo,
        {"session_id": "2026-01-15-hybrid-session", "tool": "codex"},
        [{"role": "assistant", "content": "hybrid session context"}],
        auto_commit=True,
    )
    emit_gap_signal(
        project_repo,
        query="devenv redis",
        resolution_context="testing",
        proposed_fact="hybrid keeps facts off main",
        session="2026-01-15-hybrid-gap",
    )

    monkeypatch.setattr("umx.github_ops.gh_available", lambda: True)
    monkeypatch.setattr(DreamPipeline, "_push_and_open_pr", lambda self, proposal: None)

    cfg = default_config()
    cfg.dream.mode = "hybrid"
    cfg.org = "memory-org"
    result = DreamPipeline(project_dir, config=cfg).run(force=True)

    assert result.status == "ok"
    assert result.pr_proposal is not None

    tree = subprocess.run(
        ["git", "--git-dir", str(remote), "ls-tree", "-r", "--name-only", "main"],
        capture_output=True,
        text=True,
        check=True,
    )
    names = tree.stdout.splitlines()
    assert any(name.startswith("sessions/") for name in names)
    assert not any(name.startswith("facts/topics/devenv.md") for name in names)

    status = subprocess.run(
        ["git", "-C", str(project_repo), "status", "--short"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert status.stdout.strip() == ""
