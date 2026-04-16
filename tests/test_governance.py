from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from umx.bridge import END_MARKER, START_MARKER
from umx.cli import main
from umx.config import default_config, save_config
from umx.conventions import ConventionSet
from umx.dream.pipeline import DreamPipeline
from umx.governance import (
    LABEL_CONFIDENCE_MEDIUM,
    LABEL_HUMAN_REVIEW,
    LABEL_IMPACT_GLOBAL,
    LABEL_TYPE_DELETION,
    LABEL_TYPE_PROMOTION,
    PRProposal,
    branch_name_for_dream,
    branch_name_for_proposal,
    build_promotion_pr_proposal_preview,
    classify_pr_labels,
    generate_l1_pr,
    generate_l2_review,
)
from umx.memory import add_fact, load_all_facts
from umx.models import (
    ConsolidationStatus,
    Fact,
    MemoryType,
    Scope,
    SourceType,
    Verification,
)
from umx.scope import config_path


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

    proposal_name = branch_name_for_proposal("shared deploy calendar")
    assert proposal_name == "proposal/shared-deploy-calendar"


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
    result = generate_l2_review(pr, ConventionSet(), [], new_facts=facts)
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
    result = generate_l2_review(pr, ConventionSet(), [], new_facts=facts)
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
    facts = [_make_fact(encoding_strength=4)]
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
    result = generate_l2_review(pr, ConventionSet(), [], new_facts=facts)
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
    result = generate_l2_review(pr, conventions, [], new_facts=facts)
    assert result["action"] == "reject"
    assert len(result["violations"]) > 0
    assert "unknown-topic" in result["violations"][0]


def test_l2_review_deletion_only_ignores_legacy_convention_violations() -> None:
    conventions = ConventionSet(
        topics={"devenv", "architecture"},
        phrasing_rules=["present tense only"],
    )
    pr = PRProposal(
        title="[dream/l1] cleanup",
        body="cleanup",
        branch="dream/l1/20260115-cleanup",
        labels=["confidence:high", "impact:local", LABEL_TYPE_DELETION],
        files_changed=["facts/topics/unknown.md"],
    )
    deleted_facts = [_make_fact(topic="unknown-topic", encoding_strength=2)]

    result = generate_l2_review(pr, conventions, deleted_facts)

    assert result["action"] == "escalate"
    assert "Convention violations" not in result["reason"]
    assert result["violations"] == []


def test_l2_review_deletion_only_does_not_treat_removed_conflict_as_active() -> None:
    pr = PRProposal(
        title="[dream/l1] cleanup",
        body="cleanup",
        branch="dream/l1/20260115-cleanup",
        labels=["confidence:high", "impact:local", LABEL_TYPE_DELETION],
        files_changed=["facts/topics/devenv.md"],
    )
    deleted_facts = [_make_fact(conflicts_with=["other-fact-id"], encoding_strength=2)]

    result = generate_l2_review(pr, ConventionSet(), deleted_facts)

    assert result["action"] == "escalate"
    assert "contradictions" not in result["reason"]


def test_classify_pr_labels() -> None:
    facts = [
        _make_fact(source_type=SourceType.DREAM_CONSOLIDATION, confidence=0.9),
        _make_fact(source_type=SourceType.USER_PROMPT, confidence=0.8),
    ]
    labels = classify_pr_labels(facts)
    assert "type: consolidation" in labels
    assert "type: extraction" in labels
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


def test_build_promotion_pr_proposal_preview_uses_proposal_branch_and_labels() -> None:
    from umx.cross_project import CrossProjectCandidate, CrossProjectOccurrence

    candidate = CrossProjectCandidate(
        key="shared deploy calendar lives in docs/calendar",
        text="Shared deploy calendar lives in docs/calendar",
        repo_count=3,
        repos=["alpha", "beta", "gamma"],
        eligible=True,
        already_in_user_repo=False,
        blocked_reasons=[],
        occurrences=[
            CrossProjectOccurrence(
                repo="alpha",
                fact_id="FACT_ALPHA",
                text="Shared deploy calendar lives in docs/calendar",
                topic="deploy",
                created="2026-01-01T00:00:00Z",
                encoding_strength=3,
                file_path="facts/topics/deploy.md",
            ),
            CrossProjectOccurrence(
                repo="beta",
                fact_id="FACT_BETA",
                text="Shared deploy calendar lives in docs/calendar",
                topic="deploy",
                created="2026-01-02T00:00:00Z",
                encoding_strength=3,
                file_path="facts/topics/deploy.md",
            ),
            CrossProjectOccurrence(
                repo="gamma",
                fact_id="FACT_GAMMA",
                text="Shared deploy calendar lives in docs/calendar",
                topic="release",
                created="2026-01-03T00:00:00Z",
                encoding_strength=4,
                file_path="facts/topics/release.md",
            ),
        ],
    )

    proposal = build_promotion_pr_proposal_preview(
        candidate,
        target_topic="deploy",
        target_repo=Path("/fake/user"),
    )

    assert proposal.branch == "proposal/shared-deploy-calendar-lives-in-docs-calendar"
    assert LABEL_TYPE_PROMOTION in proposal.labels
    assert LABEL_IMPACT_GLOBAL in proposal.labels
    assert LABEL_HUMAN_REVIEW in proposal.labels
    assert LABEL_CONFIDENCE_MEDIUM in proposal.labels
    assert proposal.files_changed == [
        "facts/topics/deploy.md",
        "facts/topics/deploy.umx.json",
    ]
    assert "project fact into user memory" in proposal.body
    assert "FACT_ALPHA" in proposal.body


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


def test_dream_l2_review_approves_and_merges(
    project_dir: Path,
    project_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from umx.git_ops import git_add_and_commit, git_push

    remote = tmp_path / "review-approve.git"
    _connect_origin(project_repo, remote)
    git_add_and_commit(project_repo, message="seed review baseline")
    git_push(project_repo)
    subprocess.run(
        ["git", "-C", str(project_repo), "checkout", "-b", "dream/l1/review-approve"],
        capture_output=True,
        check=True,
    )
    add_fact(
        project_repo,
        _make_fact(
            fact_id="01TESTL2APPROVE000000001",
            text="deploys require a smoke check before release",
            topic="general",
            source_type=SourceType.GROUND_TRUTH_CODE,
        ),
        auto_commit=False,
    )
    git_add_and_commit(project_repo, message="review candidate")

    merged: list[tuple[str, str, int]] = []
    monkeypatch.setattr(
        "umx.github_ops.merge_pr",
        lambda org, repo, pr, method="squash", **kwargs: merged.append((org, repo, pr)) or True,
    )
    monkeypatch.setattr("umx.github_ops.comment_pr", lambda *args, **kwargs: False)
    monkeypatch.setattr("umx.github_ops.close_pr", lambda *args, **kwargs: False)
    monkeypatch.setattr("umx.dream.pipeline.git_fetch", lambda *args, **kwargs: True)
    monkeypatch.setattr("umx.dream.pipeline.git_push", lambda *args, **kwargs: True)

    cfg = default_config()
    cfg.dream.mode = "remote"
    cfg.org = "memory-org"
    save_config(config_path(), cfg)
    result = CliRunner().invoke(
        main,
        ["dream", "--cwd", str(project_dir), "--mode", "remote", "--tier", "l2", "--pr", "7"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "ok"
    assert payload["action"] == "approve"
    assert payload["audit_note"] == "L2 review approved PR #7: Auto-approved: high confidence, local impact, non-destructive"
    assert payload["reviewed_by"] == "native:l2-rules"
    assert merged == [("memory-org", project_repo.name, 7)]
    facts = load_all_facts(project_repo, include_superseded=False)
    approved = next(fact for fact in facts if fact.fact_id == "01TESTL2APPROVE000000001")
    assert approved.provenance.approved_by == "native:l2-rules"
    assert approved.provenance.approval_tier == "l2-auto"
    assert approved.provenance.pr == "7"


def test_dream_l2_review_approves_only_changed_fact_in_existing_topic_file(
    project_dir: Path,
    project_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from umx.git_ops import git_add_and_commit, git_push

    remote = tmp_path / "review-approve-existing-topic.git"
    _connect_origin(project_repo, remote)
    add_fact(
        project_repo,
        _make_fact(
            fact_id="01TESTL2UNCHANGED000001",
            text="existing release notes live in docs/releases",
            topic="general",
            source_type=SourceType.GROUND_TRUTH_CODE,
        ),
        auto_commit=False,
    )
    git_add_and_commit(project_repo, message="seed existing topic fact")
    git_push(project_repo)
    subprocess.run(
        ["git", "-C", str(project_repo), "checkout", "-b", "dream/l1/review-existing-topic"],
        capture_output=True,
        check=True,
    )
    add_fact(
        project_repo,
        _make_fact(
            fact_id="01TESTL2CHANGED00000001",
            text="deploys require a smoke check before release",
            topic="general",
            source_type=SourceType.GROUND_TRUTH_CODE,
        ),
        auto_commit=False,
    )
    git_add_and_commit(project_repo, message="review candidate in existing topic")

    merged: list[tuple[str, str, int]] = []
    monkeypatch.setattr(
        "umx.github_ops.merge_pr",
        lambda org, repo, pr, method="squash", **kwargs: merged.append((org, repo, pr)) or True,
    )
    monkeypatch.setattr("umx.github_ops.comment_pr", lambda *args, **kwargs: False)
    monkeypatch.setattr("umx.github_ops.close_pr", lambda *args, **kwargs: False)
    monkeypatch.setattr("umx.dream.pipeline.git_fetch", lambda *args, **kwargs: True)
    monkeypatch.setattr("umx.dream.pipeline.git_push", lambda *args, **kwargs: True)

    cfg = default_config()
    cfg.dream.mode = "remote"
    cfg.org = "memory-org"
    save_config(config_path(), cfg)
    result = CliRunner().invoke(
        main,
        ["dream", "--cwd", str(project_dir), "--mode", "remote", "--tier", "l2", "--pr", "19"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "ok"
    assert payload["action"] == "approve"
    assert merged == [("memory-org", project_repo.name, 19)]
    facts = load_all_facts(project_repo, include_superseded=False)
    unchanged = next(fact for fact in facts if fact.fact_id == "01TESTL2UNCHANGED000001")
    changed = next(fact for fact in facts if fact.fact_id == "01TESTL2CHANGED00000001")
    assert unchanged.provenance.approved_by is None
    assert unchanged.provenance.approval_tier is None
    assert unchanged.provenance.pr is None
    assert changed.provenance.approved_by == "native:l2-rules"
    assert changed.provenance.approval_tier == "l2-auto"
    assert changed.provenance.pr == "19"


def test_dream_l2_review_approve_fails_from_detached_head(
    project_dir: Path,
    project_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from umx.git_ops import git_add_and_commit, git_push

    remote = tmp_path / "review-detached-head.git"
    _connect_origin(project_repo, remote)
    git_add_and_commit(project_repo, message="seed review baseline")
    git_push(project_repo)
    subprocess.run(
        ["git", "-C", str(project_repo), "checkout", "-b", "dream/l1/review-detached-head"],
        capture_output=True,
        check=True,
    )
    add_fact(
        project_repo,
        _make_fact(
            fact_id="01TESTL2DETACHED000001",
            text="deploys require a smoke check before release",
            topic="general",
            source_type=SourceType.GROUND_TRUTH_CODE,
        ),
        auto_commit=False,
    )
    git_add_and_commit(project_repo, message="review candidate")
    subprocess.run(
        ["git", "-C", str(project_repo), "checkout", "HEAD~0"],
        capture_output=True,
        check=True,
    )
    head_before = subprocess.run(
        ["git", "-C", str(project_repo), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    merge_calls: list[int] = []
    monkeypatch.setattr(
        "umx.github_ops.merge_pr",
        lambda *args, **kwargs: merge_calls.append(1) or True,
    )
    monkeypatch.setattr("umx.github_ops.comment_pr", lambda *args, **kwargs: False)
    monkeypatch.setattr("umx.github_ops.close_pr", lambda *args, **kwargs: False)

    cfg = default_config()
    cfg.dream.mode = "remote"
    cfg.org = "memory-org"
    save_config(config_path(), cfg)
    result = CliRunner().invoke(
        main,
        ["dream", "--cwd", str(project_dir), "--mode", "remote", "--tier", "l2", "--pr", "18"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "error"
    assert payload["action"] == "approve"
    assert payload["reason"] == "cannot push review provenance from detached HEAD"
    assert merge_calls == []
    head_after = subprocess.run(
        ["git", "-C", str(project_repo), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert head_after == head_before
    facts = load_all_facts(project_repo, include_superseded=False)
    approved = next(fact for fact in facts if fact.fact_id == "01TESTL2DETACHED000001")
    assert approved.provenance.approved_by is None
    assert approved.provenance.approval_tier is None


def test_dream_l2_review_uses_origin_owner_when_config_org_is_unset(
    project_dir: Path,
    project_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from umx.git_ops import git_add_and_commit, git_push

    remote = tmp_path / "review-origin-owner.git"
    _connect_origin(project_repo, remote)
    git_add_and_commit(project_repo, message="seed review baseline")
    git_push(project_repo)
    subprocess.run(
        [
            "git",
            "-C",
            str(project_repo),
            "remote",
            "set-url",
            "origin",
            f"https://github.com/memory-org/{project_repo.name}.git",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(project_repo), "checkout", "-b", "dream/l1/review-origin-owner"],
        capture_output=True,
        check=True,
    )
    add_fact(
        project_repo,
        _make_fact(
            fact_id="01TESTL2ORIGINOWNER001",
            text="deploys require a smoke check before release",
            topic="general",
            source_type=SourceType.GROUND_TRUTH_CODE,
        ),
        auto_commit=False,
    )
    git_add_and_commit(project_repo, message="review candidate")

    merged: list[tuple[str, str, int]] = []
    monkeypatch.setattr(
        "umx.github_ops.merge_pr",
        lambda org, repo, pr, method="squash", **kwargs: merged.append((org, repo, pr)) or True,
    )
    monkeypatch.setattr("umx.github_ops.comment_pr", lambda *args, **kwargs: False)
    monkeypatch.setattr("umx.github_ops.close_pr", lambda *args, **kwargs: False)
    monkeypatch.setattr("umx.dream.pipeline.git_fetch", lambda *args, **kwargs: True)
    monkeypatch.setattr("umx.dream.pipeline.git_push", lambda *args, **kwargs: True)

    cfg = default_config()
    cfg.dream.mode = "remote"
    cfg.org = None
    save_config(config_path(), cfg)
    result = CliRunner().invoke(
        main,
        ["dream", "--cwd", str(project_dir), "--mode", "remote", "--tier", "l2", "--pr", "17"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "ok"
    assert payload["action"] == "approve"
    assert merged == [("memory-org", project_repo.name, 17)]


def test_dream_l2_review_can_target_user_repo_checkout(
    user_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from umx.git_ops import git_add_and_commit, git_push

    remote = tmp_path / "review-user-repo.git"
    _connect_origin(user_repo, remote)
    subprocess.run(
        ["git", "-C", str(user_repo), "checkout", "-b", "dream/l1/user-review"],
        capture_output=True,
        check=True,
    )
    add_fact(
        user_repo,
        _make_fact(
            fact_id="01TESTL2USERCHECKOUT01",
            text="shared deploy checklist lives in docs/runbooks",
            topic="release",
            scope=Scope.USER,
            source_type=SourceType.GROUND_TRUTH_CODE,
        ),
        auto_commit=False,
    )
    git_add_and_commit(user_repo, message="user review candidate")

    commented: list[tuple[str, str, int, str]] = []
    labeled: list[tuple[str, str, int, tuple[str, ...]]] = []
    monkeypatch.setattr("umx.github_ops.merge_pr", lambda *args, **kwargs: False)
    monkeypatch.setattr("umx.github_ops.close_pr", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        "umx.github_ops.comment_pr",
        lambda org, repo, pr, body: commented.append((org, repo, pr, body)) or True,
    )
    monkeypatch.setattr(
        "umx.github_ops.add_pr_labels",
        lambda org, repo, pr, labels: labeled.append((org, repo, pr, tuple(labels))) or True,
    )

    cfg = default_config()
    cfg.dream.mode = "remote"
    cfg.org = "memory-org"
    save_config(config_path(), cfg)
    subprocess.run(
        ["git", "-C", str(user_repo), "update-ref", "-d", "refs/remotes/origin/main"],
        capture_output=True,
        text=True,
        check=True,
    )
    result = CliRunner().invoke(
        main,
        ["dream", "--cwd", str(user_repo), "--mode", "remote", "--tier", "l2", "--pr", "33"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "ok"
    assert payload["action"] == "escalate"
    assert commented
    assert commented[0][0:3] == ("memory-org", "umx-user", 33)
    assert labeled == [("memory-org", "umx-user", 33, (LABEL_HUMAN_REVIEW,))]
    assert (
        subprocess.run(
            ["git", "-C", str(user_repo), "rev-parse", "--verify", "refs/remotes/origin/main"],
            capture_output=True,
            text=True,
            check=False,
        ).returncode
        == 0
    )


def test_dream_l2_review_rejects_and_closes(
    project_dir: Path,
    project_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from umx.git_ops import git_add_and_commit, git_push

    remote = tmp_path / "review-reject.git"
    _connect_origin(project_repo, remote)
    (project_repo / "CONVENTIONS.md").write_text(
        "# Project Conventions\n\n## Topic taxonomy\n- architecture: allowed topic\n"
    )
    git_add_and_commit(project_repo, message="tighten conventions")
    git_push(project_repo)
    subprocess.run(
        ["git", "-C", str(project_repo), "checkout", "-b", "dream/l1/review-reject"],
        capture_output=True,
        check=True,
    )
    add_fact(
        project_repo,
        _make_fact(
            fact_id="01TESTL2REJECT000000001",
            text="queue depth alerts route to pagerduty",
            topic="ops",
            source_type=SourceType.GROUND_TRUTH_CODE,
        ),
        auto_commit=False,
    )
    git_add_and_commit(project_repo, message="reject candidate")

    closed: list[tuple[str, str, int, str | None]] = []
    monkeypatch.setattr("umx.github_ops.merge_pr", lambda *args, **kwargs: False)
    monkeypatch.setattr("umx.github_ops.comment_pr", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        "umx.github_ops.close_pr",
        lambda org, repo, pr, comment=None: closed.append((org, repo, pr, comment)) or True,
    )

    cfg = default_config()
    cfg.dream.mode = "remote"
    cfg.org = "memory-org"
    save_config(config_path(), cfg)
    result = CliRunner().invoke(
        main,
        ["dream", "--cwd", str(project_dir), "--mode", "remote", "--tier", "l2", "--pr", "8"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "ok"
    assert payload["action"] == "reject"
    assert closed
    assert payload["audit_note"] == closed[0][3]
    assert closed[0][0:3] == ("memory-org", project_repo.name, 8)
    assert "Convention violations" in payload["reason"]


def test_dream_l2_review_escalates_and_comments(
    project_dir: Path,
    project_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from umx.git_ops import git_add_and_commit, git_push

    remote = tmp_path / "review-escalate.git"
    _connect_origin(project_repo, remote)
    git_add_and_commit(project_repo, message="seed review baseline")
    git_push(project_repo)
    subprocess.run(
        ["git", "-C", str(project_repo), "checkout", "-b", "dream/l1/review-escalate"],
        capture_output=True,
        check=True,
    )
    add_fact(
        project_repo,
        _make_fact(
            fact_id="01TESTL2ESCALATE0000001",
            text="production postgres runs on 5434",
            topic="general",
            source_type=SourceType.GROUND_TRUTH_CODE,
            conflicts_with=["FACT-OLD-0001"],
        ),
        auto_commit=False,
    )
    git_add_and_commit(project_repo, message="escalate candidate")

    commented: list[tuple[str, str, int, str]] = []
    labeled: list[tuple[str, str, int, tuple[str, ...]]] = []
    monkeypatch.setattr("umx.github_ops.merge_pr", lambda *args, **kwargs: False)
    monkeypatch.setattr("umx.github_ops.close_pr", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        "umx.github_ops.comment_pr",
        lambda org, repo, pr, body: commented.append((org, repo, pr, body)) or True,
    )
    monkeypatch.setattr(
        "umx.github_ops.add_pr_labels",
        lambda org, repo, pr, labels: labeled.append((org, repo, pr, tuple(labels))) or True,
    )

    cfg = default_config()
    cfg.dream.mode = "remote"
    cfg.org = "memory-org"
    save_config(config_path(), cfg)
    result = CliRunner().invoke(
        main,
        ["dream", "--cwd", str(project_dir), "--mode", "remote", "--tier", "l2", "--pr", "9"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "ok"
    assert payload["action"] == "escalate"
    assert commented
    assert commented[0][0:3] == ("memory-org", project_repo.name, 9)
    assert labeled == [("memory-org", project_repo.name, 9, (LABEL_HUMAN_REVIEW,))]
    assert payload["audit_note"] == commented[0][3]
    assert "contradictions" in payload["reason"]


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


def _set_governed_mode(mode: str = "remote", org: str = "memory-org") -> None:
    cfg = default_config()
    cfg.dream.mode = mode
    cfg.org = org
    save_config(config_path(), cfg)


@pytest.mark.parametrize(
    ("command", "setup"),
    [
        (
            lambda project_dir, fact_id: ["confirm", "--cwd", str(project_dir), "--fact", fact_id],
            lambda project_dir, project_repo: (
                add_fact(
                    project_repo,
                    _make_fact(
                        fact_id="FACT_CONFIRM_BLOCK",
                        text="confirmable fact for governance guard",
                    ),
                    auto_commit=False,
                ),
                "FACT_CONFIRM_BLOCK",
            )[-1],
        ),
        (
            lambda project_dir, fact_id: ["forget", "--cwd", str(project_dir), "--fact", fact_id],
            lambda project_dir, project_repo: (
                add_fact(
                    project_repo,
                    _make_fact(
                        fact_id="FACT_FORGET_BLOCK",
                        text="forgettable fact for governance guard",
                    ),
                    auto_commit=False,
                ),
                "FACT_FORGET_BLOCK",
            )[-1],
        ),
        (
            lambda project_dir, fact_id: ["promote", "--cwd", str(project_dir), "--fact", fact_id, "--to", "project"],
            lambda project_dir, project_repo: (
                add_fact(
                    project_repo,
                    _make_fact(
                        fact_id="FACT_PROMOTE_BLOCK",
                        text="promotable fact for governance guard",
                        scope=Scope.FOLDER,
                    ),
                    auto_commit=False,
                ),
                "FACT_PROMOTE_BLOCK",
            )[-1],
        ),
        (
            lambda project_dir, _fact_id: ["merge", "--cwd", str(project_dir)],
            lambda project_dir, project_repo: (
                add_fact(project_repo, _make_fact("FACT_MERGE_A", "postgres runs on 5433 in dev"), auto_commit=False),
                add_fact(project_repo, _make_fact("FACT_MERGE_B", "postgres runs on 5432 in dev"), auto_commit=False),
                "FACT_MERGE_A",
            )[-1],
        ),
        (
            lambda project_dir, _fact_id: ["purge", "--cwd", str(project_dir), "--session", "purge-block"],
            lambda project_dir, project_repo: (
                add_fact(
                    project_repo,
                    _make_fact("FACT_PURGE_BLOCK", "purgeable fact", source_session="purge-block"),
                    auto_commit=False,
                ),
                (project_repo / "sessions" / "2026" / "01").mkdir(parents=True, exist_ok=True),
                (project_repo / "sessions" / "2026" / "01" / "purge-block.jsonl").write_text('{"_meta":{"session_id":"purge-block"}}\n'),
                "FACT_PURGE_BLOCK",
            )[-1],
        ),
        (
            lambda project_dir, _fact_id: ["import", "--cwd", str(project_dir), "--adapter", "claude-code"],
            lambda project_dir, project_repo: (
                (project_dir / "CLAUDE.md").write_text(
                    "# Project\n\n## Architecture\n- The backend uses FastAPI with uvicorn server\n"
                ),
                "CLAUDE",
            )[-1],
        ),
        (
            lambda project_dir, _fact_id: ["bridge", "import", "--cwd", str(project_dir), "--target", "CLAUDE.md"],
            lambda project_dir, project_repo: (
                (project_dir / "CLAUDE.md").write_text(
                    f"{START_MARKER}\n- postgres runs on 5433 in dev\n{END_MARKER}\n"
                ),
                "BRIDGE",
            )[-1],
        ),
        (
            lambda project_dir, _fact_id: ["migrate-scope", "--cwd", str(project_dir), "--from", "facts/topics/old.md", "--to", "facts/topics/new.md"],
            lambda project_dir, project_repo: (
                (project_repo / "facts" / "topics").mkdir(parents=True, exist_ok=True),
                (project_repo / "facts" / "topics" / "old.md").write_text("# old\n"),
                "MIGRATE",
            )[-1],
        ),
    ],
)
def test_direct_fact_mutators_are_blocked_in_governed_mode(
    project_dir: Path,
    project_repo: Path,
    command,
    setup,
) -> None:
    _set_governed_mode()
    fact_id = setup(project_dir, project_repo)

    result = CliRunner().invoke(main, command(project_dir, fact_id))

    assert result.exit_code != 0
    assert "fact changes must go through Dream PR branches" in result.output


def test_sync_remote_mode_rejects_non_session_changes(
    project_dir: Path,
    project_repo: Path,
    tmp_path: Path,
) -> None:
    remote = tmp_path / "sync-remote.git"
    _connect_origin(project_repo, remote)
    _set_governed_mode()
    add_fact(
        project_repo,
        _make_fact(
            fact_id="FACT_SYNC_BLOCK",
            text="sync should block direct governed fact pushes",
        ),
        auto_commit=False,
    )

    result = CliRunner().invoke(main, ["sync", "--cwd", str(project_dir)])

    assert result.exit_code != 0
    assert "sync only pushes session history and coordination state to main" in result.output


def test_sync_remote_mode_pushes_sessions_and_processing(
    project_dir: Path,
    project_repo: Path,
    tmp_path: Path,
) -> None:
    from umx.git_ops import git_add_and_commit, git_push

    remote = tmp_path / "sync-sessions.git"
    _connect_origin(project_repo, remote)
    git_add_and_commit(project_repo, message="sync baseline")
    git_push(project_repo)
    _set_governed_mode()

    sessions_dir = project_repo / "sessions" / "2026" / "01"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    session_file = sessions_dir / "2026-01-15-sync.jsonl"
    session_file.write_text('{"_meta":{"session_id":"2026-01-15-sync"}}\n')
    processing_file = project_repo / "meta" / "processing.jsonl"
    processing_file.write_text(
        '{"branch":"main","event":"completed","mode":"remote","run_id":"dream-sync","status":"completed","ts":"2026-04-15T01:00:00Z"}\n'
    )

    result = CliRunner().invoke(main, ["sync", "--cwd", str(project_dir)])

    assert result.exit_code == 0, result.output
    assert "synced with" in result.output

    tree = subprocess.run(
        ["git", "--git-dir", str(remote), "ls-tree", "-r", "--name-only", "main"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert session_file.relative_to(project_repo).as_posix() in tree.stdout.splitlines()
    assert processing_file.relative_to(project_repo).as_posix() in tree.stdout.splitlines()


def test_sync_remote_mode_rejects_committed_governed_fact_changes(
    project_dir: Path,
    project_repo: Path,
    tmp_path: Path,
) -> None:
    from umx.git_ops import git_add_and_commit, git_push

    remote = tmp_path / "sync-committed-facts.git"
    _connect_origin(project_repo, remote)
    git_add_and_commit(project_repo, message="sync baseline")
    git_push(project_repo)
    _set_governed_mode()
    add_fact(
        project_repo,
        _make_fact(
            fact_id="FACT_SYNC_COMMITTED_BLOCK",
            text="committed governed fact should not push to main",
        ),
        auto_commit=False,
    )
    git_add_and_commit(project_repo, message="committed governed fact")

    result = CliRunner().invoke(main, ["sync", "--cwd", str(project_dir)])

    assert result.exit_code != 0
    assert "sync only pushes session history and coordination state to main" in result.output


def test_sync_remote_mode_blocks_raw_session_push(
    project_dir: Path,
    project_repo: Path,
    tmp_path: Path,
) -> None:
    from umx.git_ops import git_add_and_commit, git_push

    remote = tmp_path / "sync-raw-session.git"
    _connect_origin(project_repo, remote)
    git_add_and_commit(project_repo, message="sync raw baseline")
    git_push(project_repo)

    cfg = default_config()
    cfg.dream.mode = "remote"
    cfg.org = "memory-org"
    cfg.sessions.redaction = "none"
    save_config(config_path(), cfg)

    sessions_dir = project_repo / "sessions" / "2026" / "01"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    session_file = sessions_dir / "2026-01-15-raw-sync.jsonl"
    session_file.write_text('{"_meta":{"session_id":"2026-01-15-raw-sync"},"content":"raw"}\n')

    result = CliRunner().invoke(main, ["sync", "--cwd", str(project_dir)])

    assert result.exit_code != 0
    assert "raw-session-push" in result.output
    assert session_file.exists()


def test_sync_remote_mode_requires_main_branch(
    project_dir: Path,
    project_repo: Path,
    tmp_path: Path,
) -> None:
    from umx.git_ops import git_add_and_commit, git_push

    remote = tmp_path / "sync-branch.git"
    _connect_origin(project_repo, remote)
    git_add_and_commit(project_repo, message="sync baseline")
    git_push(project_repo)
    _set_governed_mode()
    subprocess.run(
        ["git", "-C", str(project_repo), "checkout", "-b", "dream/l1/sync-test"],
        capture_output=True,
        check=True,
    )

    result = CliRunner().invoke(main, ["sync", "--cwd", str(project_dir)])

    assert result.exit_code != 0
    assert "sync must run from main" in result.output


def test_remote_dream_requires_main_branch(
    project_dir: Path,
    project_repo: Path,
    tmp_path: Path,
) -> None:
    from umx.git_ops import git_add_and_commit, git_push

    remote = tmp_path / "dream-branch-guard.git"
    _connect_origin(project_repo, remote)
    git_add_and_commit(project_repo, message="dream baseline")
    git_push(project_repo)
    _set_governed_mode()
    subprocess.run(
        ["git", "-C", str(project_repo), "checkout", "-b", "feature/not-main"],
        capture_output=True,
        check=True,
    )

    result = CliRunner().invoke(main, ["dream", "--cwd", str(project_dir), "--mode", "remote", "--force"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "error"
    assert "dream must run from main" in payload["message"]


def test_remote_dream_errors_when_processing_start_cannot_sync(
    project_dir: Path,
    project_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from umx.git_ops import git_add_and_commit, git_push

    remote = tmp_path / "dream-sync-fail.git"
    _connect_origin(project_repo, remote)
    git_add_and_commit(project_repo, message="dream sync baseline")
    git_push(project_repo)

    cfg = default_config()
    cfg.dream.mode = "remote"
    cfg.org = "memory-org"
    monkeypatch.setattr("umx.dream.pipeline.git_push", lambda *args, **kwargs: False)

    result = DreamPipeline(project_dir, config=cfg).run(force=True)

    assert result.status == "error"
    assert result.message == "failed to publish dream processing start"


def test_remote_dream_blocks_unsafe_pr_push(
    project_dir: Path,
    project_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from umx.git_ops import git_add_and_commit, git_push

    remote = tmp_path / "dream-push-safety.git"
    _connect_origin(project_repo, remote)
    git_add_and_commit(project_repo, message="dream safety baseline")
    git_push(project_repo)

    fact_path = project_repo / "facts" / "topics" / "deploy.md"
    fact_path.parent.mkdir(parents=True, exist_ok=True)
    fact_path.write_text("# deploy\n\n## Facts\n- aws key AKIA1234567890ABCDEF\n")

    create_calls: list[int] = []
    monkeypatch.setattr("umx.github_ops.gh_available", lambda: True)
    monkeypatch.setattr(
        "umx.github_ops.create_pr",
        lambda *args, **kwargs: create_calls.append(1) or 7,
    )

    cfg = default_config()
    cfg.dream.mode = "remote"
    cfg.org = "memory-org"

    result = DreamPipeline(project_dir, config=cfg).run(force=True)

    assert result.status == "error"
    assert "push safety blocked" in (result.message or "")
    assert create_calls == []


def test_dream_l2_review_escalates_deleted_fact_file(
    project_dir: Path,
    project_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from umx.git_ops import git_add_and_commit, git_push

    remote = tmp_path / "review-delete.git"
    _connect_origin(project_repo, remote)
    add_fact(
        project_repo,
        _make_fact(
            fact_id="01TESTL2DELETE000000001",
            text="delete candidate fact",
            topic="general",
            source_type=SourceType.GROUND_TRUTH_CODE,
        ),
        auto_commit=False,
    )
    git_add_and_commit(project_repo, message="seed governed fact")
    git_push(project_repo)
    subprocess.run(
        ["git", "-C", str(project_repo), "checkout", "-b", "dream/l1/review-delete"],
        capture_output=True,
        check=True,
    )
    (project_repo / "facts" / "topics" / "general.md").unlink()
    git_add_and_commit(project_repo, message="delete governed fact")

    commented: list[tuple[str, str, int, str]] = []
    labeled: list[tuple[str, str, int, tuple[str, ...]]] = []
    monkeypatch.setattr("umx.github_ops.merge_pr", lambda *args, **kwargs: False)
    monkeypatch.setattr("umx.github_ops.close_pr", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        "umx.github_ops.comment_pr",
        lambda org, repo, pr, body: commented.append((org, repo, pr, body)) or True,
    )
    monkeypatch.setattr(
        "umx.github_ops.add_pr_labels",
        lambda org, repo, pr, labels: labeled.append((org, repo, pr, tuple(labels))) or True,
    )

    _set_governed_mode()
    result = CliRunner().invoke(
        main,
        ["dream", "--cwd", str(project_dir), "--mode", "remote", "--tier", "l2", "--pr", "10"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "ok"
    assert payload["action"] == "escalate"
    assert "type: deletion" in payload["reason"]
    assert payload["labels"]
    assert "type: deletion" in payload["labels"]
    assert labeled == [("memory-org", project_repo.name, 10, (LABEL_HUMAN_REVIEW,))]
    assert commented


def test_dream_l2_review_escalates_tombstone_only_change(
    project_dir: Path,
    project_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from umx.git_ops import git_add_and_commit, git_push

    remote = tmp_path / "review-tombstone.git"
    _connect_origin(project_repo, remote)
    git_add_and_commit(project_repo, message="seed tombstone baseline")
    git_push(project_repo)
    subprocess.run(
        ["git", "-C", str(project_repo), "checkout", "-b", "dream/l1/review-tombstone"],
        capture_output=True,
        check=True,
    )
    tombstones = project_repo / "meta" / "tombstones.jsonl"
    tombstones.write_text(
        '{"fact_id":"FACT-TOMBSTONE","match":"delete me","reason":"test","author":"human","created":"2026-01-15T00:00:00Z","suppress_from":["gather"],"expires_at":null}\n'
    )
    git_add_and_commit(project_repo, message="add tombstone")

    commented: list[tuple[str, str, int, str]] = []
    labeled: list[tuple[str, str, int, tuple[str, ...]]] = []
    monkeypatch.setattr("umx.github_ops.merge_pr", lambda *args, **kwargs: False)
    monkeypatch.setattr("umx.github_ops.close_pr", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        "umx.github_ops.comment_pr",
        lambda org, repo, pr, body: commented.append((org, repo, pr, body)) or True,
    )
    monkeypatch.setattr(
        "umx.github_ops.add_pr_labels",
        lambda org, repo, pr, labels: labeled.append((org, repo, pr, tuple(labels))) or True,
    )

    _set_governed_mode()
    result = CliRunner().invoke(
        main,
        ["dream", "--cwd", str(project_dir), "--mode", "remote", "--tier", "l2", "--pr", "11"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "ok"
    assert payload["action"] == "escalate"
    assert payload["reason"] == "governed non-fact changes require human review"
    assert "meta/tombstones.jsonl" in payload["files_changed"]
    assert labeled == [("memory-org", project_repo.name, 11, (LABEL_HUMAN_REVIEW,))]
    assert commented


def test_dream_l2_review_escalates_mixed_governed_and_non_governed_changes(
    project_dir: Path,
    project_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from umx.git_ops import git_add_and_commit, git_push

    remote = tmp_path / "review-mixed-governed.git"
    _connect_origin(project_repo, remote)
    git_add_and_commit(project_repo, message="seed mixed baseline")
    git_push(project_repo)
    subprocess.run(
        ["git", "-C", str(project_repo), "checkout", "-b", "dream/l1/review-mixed-governed"],
        capture_output=True,
        check=True,
    )
    add_fact(
        project_repo,
        _make_fact(
            fact_id="01TESTL2MIXED000000001",
            text="deploys require a smoke check before release",
            topic="general",
            source_type=SourceType.GROUND_TRUTH_CODE,
        ),
        auto_commit=False,
    )
    extra = project_repo / "tools" / "review-note.txt"
    extra.parent.mkdir(parents=True, exist_ok=True)
    extra.write_text("not a governed fact path\n")
    git_add_and_commit(project_repo, message="mixed governed candidate")

    commented: list[tuple[str, str, int, str]] = []
    labeled: list[tuple[str, str, int, tuple[str, ...]]] = []
    monkeypatch.setattr("umx.github_ops.merge_pr", lambda *args, **kwargs: False)
    monkeypatch.setattr("umx.github_ops.close_pr", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        "umx.github_ops.comment_pr",
        lambda org, repo, pr, body: commented.append((org, repo, pr, body)) or True,
    )
    monkeypatch.setattr(
        "umx.github_ops.add_pr_labels",
        lambda org, repo, pr, labels: labeled.append((org, repo, pr, tuple(labels))) or True,
    )

    _set_governed_mode()
    result = CliRunner().invoke(
        main,
        ["dream", "--cwd", str(project_dir), "--mode", "remote", "--tier", "l2", "--pr", "12"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "ok"
    assert payload["action"] == "escalate"
    assert payload["reason"] == "mixed governed and non-governed changes require human review"
    assert "facts/topics/general.md" in payload["files_changed"]
    assert "tools/review-note.txt" in payload["files_changed"]
    assert labeled == [("memory-org", project_repo.name, 12, (LABEL_HUMAN_REVIEW,))]
    assert commented


def test_dream_l2_review_escalates_in_place_strong_supersession(
    project_dir: Path,
    project_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from umx.git_ops import git_add_and_commit, git_push
    from umx.memory import find_fact_by_id, replace_fact

    remote = tmp_path / "review-supersession.git"
    _connect_origin(project_repo, remote)
    add_fact(
        project_repo,
        _make_fact(
            fact_id="01TESTL2SUPERSEDE0001",
            text="deploys require a smoke check before release",
            topic="general",
            encoding_strength=4,
            source_type=SourceType.GROUND_TRUTH_CODE,
        ),
        auto_commit=False,
    )
    git_add_and_commit(project_repo, message="seed supersession baseline")
    git_push(project_repo)
    subprocess.run(
        ["git", "-C", str(project_repo), "checkout", "-b", "dream/l1/review-supersession"],
        capture_output=True,
        check=True,
    )
    existing = find_fact_by_id(project_repo, "01TESTL2SUPERSEDE0001")
    assert existing is not None
    assert replace_fact(
        project_repo,
        existing.clone(superseded_by="01TESTL2SUPERSEDE0002"),
    )
    git_add_and_commit(project_repo, message="supersede strong fact in place")

    commented: list[tuple[str, str, int, str]] = []
    labeled: list[tuple[str, str, int, tuple[str, ...]]] = []
    monkeypatch.setattr("umx.github_ops.merge_pr", lambda *args, **kwargs: False)
    monkeypatch.setattr("umx.github_ops.close_pr", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        "umx.github_ops.comment_pr",
        lambda org, repo, pr, body: commented.append((org, repo, pr, body)) or True,
    )
    monkeypatch.setattr(
        "umx.github_ops.add_pr_labels",
        lambda org, repo, pr, labels: labeled.append((org, repo, pr, tuple(labels))) or True,
    )

    _set_governed_mode()
    result = CliRunner().invoke(
        main,
        ["dream", "--cwd", str(project_dir), "--mode", "remote", "--tier", "l2", "--pr", "13"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "ok"
    assert payload["action"] == "escalate"
    assert "type: deletion" in payload["reason"]
    assert labeled == [("memory-org", project_repo.name, 13, (LABEL_HUMAN_REVIEW,))]
    assert commented


def test_dream_l2_review_escalates_in_place_strong_rewrite(
    project_dir: Path,
    project_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from umx.git_ops import git_add_and_commit, git_push
    from umx.memory import find_fact_by_id, replace_fact

    remote = tmp_path / "review-strong-rewrite.git"
    _connect_origin(project_repo, remote)
    add_fact(
        project_repo,
        _make_fact(
            fact_id="01TESTL2REWRITE00001",
            text="deploys require a smoke check before release",
            topic="general",
            encoding_strength=4,
            source_type=SourceType.GROUND_TRUTH_CODE,
        ),
        auto_commit=False,
    )
    git_add_and_commit(project_repo, message="seed rewrite baseline")
    git_push(project_repo)
    subprocess.run(
        ["git", "-C", str(project_repo), "checkout", "-b", "dream/l1/review-strong-rewrite"],
        capture_output=True,
        check=True,
    )
    existing = find_fact_by_id(project_repo, "01TESTL2REWRITE00001")
    assert existing is not None
    assert replace_fact(
        project_repo,
        existing.clone(text="deploys require a dry run before release"),
    )
    git_add_and_commit(project_repo, message="rewrite strong fact in place")

    commented: list[tuple[str, str, int, str]] = []
    labeled: list[tuple[str, str, int, tuple[str, ...]]] = []
    monkeypatch.setattr("umx.github_ops.merge_pr", lambda *args, **kwargs: False)
    monkeypatch.setattr("umx.github_ops.close_pr", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        "umx.github_ops.comment_pr",
        lambda org, repo, pr, body: commented.append((org, repo, pr, body)) or True,
    )
    monkeypatch.setattr(
        "umx.github_ops.add_pr_labels",
        lambda org, repo, pr, labels: labeled.append((org, repo, pr, tuple(labels))) or True,
    )

    _set_governed_mode()
    result = CliRunner().invoke(
        main,
        ["dream", "--cwd", str(project_dir), "--mode", "remote", "--tier", "l2", "--pr", "16"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "ok"
    assert payload["action"] == "escalate"
    assert "type: deletion" in payload["reason"]
    assert labeled == [("memory-org", project_repo.name, 16, (LABEL_HUMAN_REVIEW,))]
    assert commented


def test_dream_l2_review_approves_when_edit_removes_conflict(
    project_dir: Path,
    project_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from umx.git_ops import git_add_and_commit, git_push
    from umx.memory import find_fact_by_id, replace_fact

    remote = tmp_path / "review-remove-conflict.git"
    _connect_origin(project_repo, remote)
    add_fact(
        project_repo,
        _make_fact(
            fact_id="01TESTL2CONFLICT0001",
            text="deploys require a smoke check before release",
            topic="general",
            conflicts_with=["FACT-OLD-0001"],
            source_type=SourceType.GROUND_TRUTH_CODE,
        ),
        auto_commit=False,
    )
    git_add_and_commit(project_repo, message="seed conflicting fact")
    git_push(project_repo)
    subprocess.run(
        ["git", "-C", str(project_repo), "checkout", "-b", "dream/l1/review-remove-conflict"],
        capture_output=True,
        check=True,
    )
    existing = find_fact_by_id(project_repo, "01TESTL2CONFLICT0001")
    assert existing is not None
    assert replace_fact(
        project_repo,
        existing.clone(conflicts_with=[]),
    )
    git_add_and_commit(project_repo, message="resolve fact conflict")

    merged: list[tuple[str, str, int]] = []
    monkeypatch.setattr(
        "umx.github_ops.merge_pr",
        lambda org, repo, pr, method="squash", **kwargs: merged.append((org, repo, pr)) or True,
    )
    monkeypatch.setattr("umx.github_ops.comment_pr", lambda *args, **kwargs: False)
    monkeypatch.setattr("umx.github_ops.close_pr", lambda *args, **kwargs: False)
    monkeypatch.setattr("umx.dream.pipeline.git_fetch", lambda *args, **kwargs: True)
    monkeypatch.setattr("umx.dream.pipeline.git_push", lambda *args, **kwargs: True)

    _set_governed_mode()
    result = CliRunner().invoke(
        main,
        ["dream", "--cwd", str(project_dir), "--mode", "remote", "--tier", "l2", "--pr", "14"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "ok"
    assert payload["action"] == "approve"
    assert merged == [("memory-org", project_repo.name, 14)]


def test_dream_l2_review_approves_in_place_weak_supersession(
    project_dir: Path,
    project_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from umx.git_ops import git_add_and_commit, git_push
    from umx.memory import find_fact_by_id, replace_fact

    remote = tmp_path / "review-weak-supersession.git"
    _connect_origin(project_repo, remote)
    add_fact(
        project_repo,
        _make_fact(
            fact_id="01TESTL2WEAKSUPER0001",
            text="deploy reminders live in docs/reminders",
            topic="general",
            encoding_strength=2,
            source_type=SourceType.GROUND_TRUTH_CODE,
        ),
        auto_commit=False,
    )
    git_add_and_commit(project_repo, message="seed weak fact")
    git_push(project_repo)
    subprocess.run(
        ["git", "-C", str(project_repo), "checkout", "-b", "dream/l1/review-weak-supersession"],
        capture_output=True,
        check=True,
    )
    existing = find_fact_by_id(project_repo, "01TESTL2WEAKSUPER0001")
    assert existing is not None
    assert replace_fact(
        project_repo,
        existing.clone(superseded_by="01TESTL2WEAKSUPER0002"),
    )
    git_add_and_commit(project_repo, message="weak supersession in place")

    merged: list[tuple[str, str, int]] = []
    monkeypatch.setattr(
        "umx.github_ops.merge_pr",
        lambda org, repo, pr, method="squash", **kwargs: merged.append((org, repo, pr)) or True,
    )
    monkeypatch.setattr("umx.github_ops.comment_pr", lambda *args, **kwargs: False)
    monkeypatch.setattr("umx.github_ops.close_pr", lambda *args, **kwargs: False)
    monkeypatch.setattr("umx.dream.pipeline.git_fetch", lambda *args, **kwargs: True)
    monkeypatch.setattr("umx.dream.pipeline.git_push", lambda *args, **kwargs: True)

    _set_governed_mode()
    result = CliRunner().invoke(
        main,
        ["dream", "--cwd", str(project_dir), "--mode", "remote", "--tier", "l2", "--pr", "15"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "ok"
    assert payload["action"] == "approve"
    assert merged == [("memory-org", project_repo.name, 15)]
