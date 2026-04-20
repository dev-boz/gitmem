from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from tests.secret_literals import AWS_ACCESS_KEY_ID
from umx.bridge import END_MARKER, START_MARKER
from umx.cli import main
from umx.config import default_config, save_config
from umx.conventions import ConventionSet
from umx.dream.l2_review import REVIEW_COMMENT_MARKER
from umx.dream.pipeline import DreamPipeline
from umx.dream.pr_render import (
    LEGACY_PR_BODY_MARKER,
    FactDeltaBlock,
    FactDeltaEntry,
    render_governance_pr_body,
)
from umx.github_ops import reconcile_pr_labels as github_reconcile_pr_labels
from umx.governance import (
    GovernancePRConflictError,
    GovernancePROverlap,
    LABEL_CONFIDENCE_MEDIUM,
    LABEL_HUMAN_REVIEW,
    LABEL_IMPACT_GLOBAL,
    LABEL_STATE_APPROVED,
    LABEL_STATE_EXTRACTION,
    LABEL_STATE_REVIEWED,
    LABEL_TYPE_DELETION,
    LABEL_TYPE_PROMOTION,
    PRProposal,
    assert_governance_pr_body,
    branch_name_for_dream,
    branch_name_for_proposal,
    build_promotion_pr_proposal_preview,
    classify_pr_labels,
    desired_governance_labels,
    generate_l1_pr,
    generate_l2_review,
    reconcile_governance_label_set,
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
from umx.providers.anthropic import AnthropicMessageResult
from umx.scope import config_path
from umx.tombstones import load_tombstones

L2_FIXTURES_ROOT = Path(__file__).parent / "fixtures" / "l2_review"
L2_APPROVE_FIXTURE = json.loads((L2_FIXTURES_ROOT / "anthropic_approve.json").read_text())


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


def _governance_pr_body() -> str:
    return render_governance_pr_body(
        heading="Dream L1 Extraction",
        summary_lines=["- seeded governance PR body for review tests"],
        fact_delta=FactDeltaBlock(
            added=(
                FactDeltaEntry(
                    fact_id="01TESTGOVERNANCEBODY0001",
                    topic="general",
                    path="facts/topics/general.md",
                    summary="seeded governance body",
                ),
            ),
        ),
    )


@pytest.fixture(autouse=True)
def _stub_pr_body(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("umx.github_ops.read_pr_body", lambda *args, **kwargs: _governance_pr_body())


@pytest.fixture(autouse=True)
def _stub_label_reconciliation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "umx.github_ops.read_pr_labels",
        lambda *args, **kwargs: [
            LABEL_STATE_EXTRACTION,
            "type: extraction",
            "confidence:high",
            "impact:local",
        ],
    )
    monkeypatch.setattr("umx.github_ops.reconcile_pr_labels", lambda *args, **kwargs: True)


def _assert_human_review_reconciliation(
    labeled: list[tuple[str, str, int, tuple[str, ...]]],
    *,
    org: str,
    repo: str,
    pr_number: int,
    required_labels: tuple[str, ...] = (),
) -> None:
    assert len(labeled) == 1
    got_org, got_repo, got_pr, labels = labeled[0]
    assert (got_org, got_repo, got_pr) == (org, repo, pr_number)
    assert LABEL_STATE_REVIEWED in labels
    assert LABEL_HUMAN_REVIEW in labels
    assert LABEL_STATE_EXTRACTION not in labels
    for label in required_labels:
        assert label in labels


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
    assert_governance_pr_body(pr.body)


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
    payload = assert_governance_pr_body(pr.body)
    assert payload is not None
    assert len(payload["added"]) == 2


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

    promotion_facts = [
        _make_fact(
            scope=Scope.USER,
            source_type=SourceType.DREAM_CONSOLIDATION,
            source_tool="cross-project-promotion",
            source_session="cross-project-promotion",
        ),
    ]
    promotion_labels = classify_pr_labels(promotion_facts)
    assert LABEL_TYPE_PROMOTION in promotion_labels
    assert "type: consolidation" not in promotion_labels


def test_desired_governance_labels_manage_lifecycle_state() -> None:
    labels = desired_governance_labels(
        ["type: extraction", LABEL_STATE_EXTRACTION],
        lifecycle_label=LABEL_STATE_REVIEWED,
        human_review=True,
    )

    assert LABEL_STATE_REVIEWED in labels
    assert LABEL_HUMAN_REVIEW in labels
    assert LABEL_STATE_EXTRACTION not in labels

    promoted = desired_governance_labels(labels, lifecycle_label=LABEL_STATE_APPROVED, human_review=False)
    assert LABEL_STATE_APPROVED in promoted
    assert LABEL_HUMAN_REVIEW not in promoted


def test_reconcile_governance_label_set_removes_only_managed_labels() -> None:
    add, remove = reconcile_governance_label_set(
        ["type: extraction", LABEL_STATE_EXTRACTION, LABEL_HUMAN_REVIEW, "needs-docs"],
        ["type: extraction", LABEL_STATE_REVIEWED],
    )

    assert add == [LABEL_STATE_REVIEWED]
    assert remove == [LABEL_HUMAN_REVIEW, LABEL_STATE_EXTRACTION]


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
    assert LABEL_STATE_EXTRACTION in proposal.labels
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


def test_dream_l2_review_approves_but_blocks_merge_without_human_approval(
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
    assert payload["status"] == "blocked"
    assert payload["action"] == "approve"
    assert payload["audit_note"] == "L2 review approved PR #7: Auto-approved: high confidence, local impact, non-destructive"
    assert payload["reviewed_by"] == "native:l2-rules"
    assert payload["merge_blocked"] is True
    assert payload["merge_required_labels"] == ["state: approved"]
    assert merged == []
    facts = load_all_facts(project_repo, include_superseded=False)
    approved = next(fact for fact in facts if fact.fact_id == "01TESTL2APPROVE000000001")
    assert approved.provenance.approved_by == "native:l2-rules"
    assert approved.provenance.approval_tier == "l2-auto"
    assert approved.provenance.pr == "7"


def test_dream_l2_review_force_override_merges_with_audit_reason(
    project_dir: Path,
    project_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from umx.git_ops import git_add_and_commit, git_push

    remote = tmp_path / "review-approve-force.git"
    _connect_origin(project_repo, remote)
    git_add_and_commit(project_repo, message="seed review baseline")
    git_push(project_repo)
    subprocess.run(
        ["git", "-C", str(project_repo), "checkout", "-b", "dream/l1/review-approve-force"],
        capture_output=True,
        check=True,
    )
    add_fact(
        project_repo,
        _make_fact(
            fact_id="01TESTL2APPROVEFORCE0001",
            text="deploys require a smoke check before release",
            topic="general",
            source_type=SourceType.GROUND_TRUTH_CODE,
        ),
        auto_commit=False,
    )
    git_add_and_commit(project_repo, message="review candidate")

    comments: list[tuple[str, str, int, str]] = []
    merged: list[tuple[str, str, int, bool]] = []
    monkeypatch.setattr(
        "umx.github_ops.comment_pr",
        lambda org, repo, pr, body: comments.append((org, repo, pr, body)) or True,
    )
    monkeypatch.setattr(
        "umx.github_ops.merge_pr",
        lambda org, repo, pr, method="squash", **kwargs: merged.append(
            (org, repo, pr, bool(kwargs.get("admin")))
        ) or True,
    )
    monkeypatch.setattr("umx.github_ops.close_pr", lambda *args, **kwargs: False)
    monkeypatch.setattr("umx.dream.pipeline.git_fetch", lambda *args, **kwargs: True)
    monkeypatch.setattr("umx.dream.pipeline.git_push", lambda *args, **kwargs: True)

    cfg = default_config()
    cfg.dream.mode = "remote"
    cfg.org = "memory-org"
    save_config(config_path(), cfg)
    result = CliRunner().invoke(
        main,
        [
            "dream",
            "--cwd",
            str(project_dir),
            "--mode",
            "remote",
            "--tier",
            "l2",
            "--pr",
            "36",
            "--force",
            "--force-reason",
            "manual hotfix release needed before approval label lands",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "ok"
    assert payload["action"] == "approve"
    assert payload["merge_blocked"] is False
    assert payload["merge_override_used"] is True
    assert payload["merge_override_reason"] == "manual hotfix release needed before approval label lands"
    assert merged == [("memory-org", project_repo.name, 36, True)]
    assert any("<!-- umx:approval-override -->" in body for _, _, _, body in comments)


def test_dream_l2_review_blocks_merge_when_approved_label_disappears_before_merge(
    project_dir: Path,
    project_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from umx.git_ops import git_add_and_commit, git_push

    remote = tmp_path / "review-approve-disappears.git"
    _connect_origin(project_repo, remote)
    git_add_and_commit(project_repo, message="seed review baseline")
    git_push(project_repo)
    subprocess.run(
        ["git", "-C", str(project_repo), "checkout", "-b", "dream/l1/review-approve-disappears"],
        capture_output=True,
        check=True,
    )
    add_fact(
        project_repo,
        _make_fact(
            fact_id="01TESTL2APPROVEDROP0001",
            text="deploys require a smoke check before release",
            topic="general",
            source_type=SourceType.GROUND_TRUTH_CODE,
        ),
        auto_commit=False,
    )
    git_add_and_commit(project_repo, message="review candidate")

    label_reads = [
        [
            LABEL_STATE_APPROVED,
            "type: extraction",
            "confidence:high",
            "impact:local",
        ],
        [
            LABEL_STATE_APPROVED,
            "type: extraction",
            "confidence:high",
            "impact:local",
        ],
        [
            LABEL_STATE_REVIEWED,
            "type: extraction",
            "confidence:high",
            "impact:local",
        ],
    ]
    merged: list[tuple[str, str, int]] = []
    monkeypatch.setattr(
        "umx.github_ops.read_pr_labels",
        lambda *args, **kwargs: list(label_reads.pop(0)),
    )
    monkeypatch.setattr("umx.github_ops.reconcile_pr_labels", lambda *args, **kwargs: True)
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
        ["dream", "--cwd", str(project_dir), "--mode", "remote", "--tier", "l2", "--pr", "41"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "blocked"
    assert payload["action"] == "approve"
    assert payload["merge_blocked"] is True
    assert payload["reason"] == "awaiting approval label before merge: state: approved"
    assert LABEL_STATE_APPROVED not in payload["labels"]
    assert LABEL_STATE_REVIEWED in payload["labels"]
    assert merged == []


def test_dream_l2_review_force_reason_does_not_audit_unused_override(
    project_dir: Path,
    project_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from umx.git_ops import git_add_and_commit, git_push

    remote = tmp_path / "review-approve-force-noop.git"
    _connect_origin(project_repo, remote)
    git_add_and_commit(project_repo, message="seed review baseline")
    git_push(project_repo)
    subprocess.run(
        ["git", "-C", str(project_repo), "checkout", "-b", "dream/l1/review-approve-force-noop"],
        capture_output=True,
        check=True,
    )
    add_fact(
        project_repo,
        _make_fact(
            fact_id="01TESTL2APPROVEFORCENOOP",
            text="deploys require a smoke check before release",
            topic="general",
            source_type=SourceType.GROUND_TRUTH_CODE,
        ),
        auto_commit=False,
    )
    git_add_and_commit(project_repo, message="review candidate")

    comments: list[tuple[str, str, int, str]] = []
    merged: list[tuple[str, str, int, bool]] = []
    monkeypatch.setattr(
        "umx.github_ops.read_pr_labels",
        lambda *args, **kwargs: [
            LABEL_STATE_APPROVED,
            "type: extraction",
            "confidence:high",
            "impact:local",
        ],
    )
    monkeypatch.setattr(
        "umx.github_ops.comment_pr",
        lambda org, repo, pr, body: comments.append((org, repo, pr, body)) or True,
    )
    monkeypatch.setattr(
        "umx.github_ops.merge_pr",
        lambda org, repo, pr, method="squash", **kwargs: merged.append(
            (org, repo, pr, bool(kwargs.get("admin")))
        ) or True,
    )
    monkeypatch.setattr("umx.github_ops.close_pr", lambda *args, **kwargs: False)
    monkeypatch.setattr("umx.dream.pipeline.git_fetch", lambda *args, **kwargs: True)
    monkeypatch.setattr("umx.dream.pipeline.git_push", lambda *args, **kwargs: True)

    cfg = default_config()
    cfg.dream.mode = "remote"
    cfg.org = "memory-org"
    save_config(config_path(), cfg)
    result = CliRunner().invoke(
        main,
        [
            "dream",
            "--cwd",
            str(project_dir),
            "--mode",
            "remote",
            "--tier",
            "l2",
            "--pr",
            "42",
            "--force",
            "--force-reason",
            "manual hotfix release needed before approval label lands",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "ok"
    assert payload["action"] == "approve"
    assert payload["merge_override_used"] is False
    assert payload["merge_override_reason"] is None
    assert merged == [("memory-org", project_repo.name, 42, False)]
    assert comments == []


def test_dream_l2_review_override_audit_failure_does_not_report_override_reason(
    project_dir: Path,
    project_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from umx.git_ops import git_add_and_commit, git_push

    remote = tmp_path / "review-approve-force-audit-fail.git"
    _connect_origin(project_repo, remote)
    git_add_and_commit(project_repo, message="seed review baseline")
    git_push(project_repo)
    subprocess.run(
        ["git", "-C", str(project_repo), "checkout", "-b", "dream/l1/review-approve-force-audit-fail"],
        capture_output=True,
        check=True,
    )
    add_fact(
        project_repo,
        _make_fact(
            fact_id="01TESTL2APPROVEFORCEFAIL",
            text="deploys require a smoke check before release",
            topic="general",
            source_type=SourceType.GROUND_TRUTH_CODE,
        ),
        auto_commit=False,
    )
    git_add_and_commit(project_repo, message="review candidate")

    merged: list[tuple[str, str, int, bool]] = []
    monkeypatch.setattr("umx.github_ops.comment_pr", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        "umx.github_ops.merge_pr",
        lambda org, repo, pr, method="squash", **kwargs: merged.append(
            (org, repo, pr, bool(kwargs.get("admin")))
        ) or True,
    )
    monkeypatch.setattr("umx.github_ops.close_pr", lambda *args, **kwargs: False)
    monkeypatch.setattr("umx.dream.pipeline.git_fetch", lambda *args, **kwargs: True)
    monkeypatch.setattr("umx.dream.pipeline.git_push", lambda *args, **kwargs: True)

    cfg = default_config()
    cfg.dream.mode = "remote"
    cfg.org = "memory-org"
    save_config(config_path(), cfg)
    result = CliRunner().invoke(
        main,
        [
            "dream",
            "--cwd",
            str(project_dir),
            "--mode",
            "remote",
            "--tier",
            "l2",
            "--pr",
            "44",
            "--force",
            "--force-reason",
            "manual hotfix release needed before approval label lands",
        ],
    )

    assert result.exit_code != 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "error"
    assert payload["action"] == "approve"
    assert payload["reason"] == "failed to persist approval override audit for PR #44"
    assert payload["merge_blocked"] is True
    assert payload["merge_override_used"] is False
    assert payload["merge_override_reason"] is None
    assert merged == []


def test_dream_l2_review_provider_approval_persists_comment_and_blocks_merge(
    project_dir: Path,
    project_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from umx.git_ops import git_add_and_commit, git_push

    reviewed_fact_id = "01TESTL2PROVIDER0000001"
    remote = tmp_path / "review-provider-approve.git"
    _connect_origin(project_repo, remote)
    git_add_and_commit(project_repo, message="seed review baseline")
    git_push(project_repo)
    subprocess.run(
        ["git", "-C", str(project_repo), "checkout", "-b", "dream/l1/review-provider-approve"],
        capture_output=True,
        check=True,
    )
    add_fact(
        project_repo,
        _make_fact(
            fact_id=reviewed_fact_id,
            text="deploy approvals stay local to the service owner",
            topic="general",
            source_type=SourceType.GROUND_TRUTH_CODE,
        ),
        auto_commit=False,
    )
    git_add_and_commit(project_repo, message="provider review candidate")

    comments: list[tuple[str, str, int, str]] = []
    merged: list[tuple[str, str, int]] = []
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(
        "umx.providers.anthropic.send_anthropic_message",
        lambda **kwargs: AnthropicMessageResult(
            text=str(L2_APPROVE_FIXTURE["text"]).replace("01TESTL2FIXTURE0000000001", reviewed_fact_id),
            model=str(L2_APPROVE_FIXTURE["model"]),
            usage=dict(L2_APPROVE_FIXTURE["usage"]),
        ),
    )
    monkeypatch.setattr(
        "umx.github_ops.comment_pr",
        lambda org, repo, pr, body: comments.append((org, repo, pr, body)) or True,
    )
    monkeypatch.setattr(
        "umx.github_ops.read_pr_labels",
        lambda *args, **kwargs: [
            LABEL_STATE_EXTRACTION,
            "type: extraction",
            "confidence:high",
            "impact:local",
            "needs-docs",
        ],
    )
    monkeypatch.setattr(
        "umx.github_ops.merge_pr",
        lambda org, repo, pr, method="squash", **kwargs: merged.append((org, repo, pr)) or True,
    )
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
    assert payload["status"] == "blocked"
    assert payload["action"] == "approve"
    assert payload["reviewed_by"] == "provider:anthropic/anthropic"
    assert payload["model_backed"] is True
    assert payload["review_model"] == "claude-opus-4-7"
    assert payload["review_usage"] == {"input_tokens": 321, "output_tokens": 87, "total_tokens": 408}
    assert payload["review_prompt_id"] == "anthropic-l2-review"
    assert payload["review_prompt_version"] == "v1"
    assert payload["fact_notes"][0]["fact_id"] == reviewed_fact_id
    assert LABEL_STATE_REVIEWED in payload["labels"]
    assert "needs-docs" in payload["labels"]
    assert payload["merge_blocked"] is True
    assert payload["merge_required_labels"] == ["state: approved"]
    assert merged == []
    assert len(comments) == 1
    assert comments[0][:3] == ("memory-org", project_repo.name, 7)
    assert REVIEW_COMMENT_MARKER in comments[0][3]
    assert "- Model: `claude-opus-4-7`" in comments[0][3]
    assert "- Tokens: in 321, out 87, total 408" in comments[0][3]

    processing_rows = [
        json.loads(line)
        for line in (project_repo / "meta" / "processing.jsonl").read_text().splitlines()
        if line.strip()
    ]
    assert processing_rows[-1]["tier"] == "l2"
    assert processing_rows[-1]["event"] == "review_completed"
    assert processing_rows[-1]["status"] == "blocked"
    assert processing_rows[-1]["review_usage"] == {"input_tokens": 321, "output_tokens": 87, "total_tokens": 408}
    assert processing_rows[-1]["review_prompt_id"] == "anthropic-l2-review"
    assert processing_rows[-1]["review_prompt_version"] == "v1"


def test_dream_l2_review_provider_detached_head_does_not_persist_comment(
    project_dir: Path,
    project_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from umx.git_ops import git_add_and_commit, git_push

    reviewed_fact_id = "01TESTL2PROVIDERDETACHED1"
    remote = tmp_path / "review-provider-detached.git"
    _connect_origin(project_repo, remote)
    git_add_and_commit(project_repo, message="seed review baseline")
    git_push(project_repo)
    subprocess.run(
        ["git", "-C", str(project_repo), "checkout", "-b", "dream/l1/review-provider-detached"],
        capture_output=True,
        check=True,
    )
    add_fact(
        project_repo,
        _make_fact(
            fact_id=reviewed_fact_id,
            text="deploy approvals stay local to the service owner",
            topic="general",
            source_type=SourceType.GROUND_TRUTH_CODE,
        ),
        auto_commit=False,
    )
    git_add_and_commit(project_repo, message="provider review candidate")
    subprocess.run(
        ["git", "-C", str(project_repo), "checkout", "HEAD~0"],
        capture_output=True,
        check=True,
    )

    comments: list[tuple[str, str, int, str]] = []
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(
        "umx.providers.anthropic.send_anthropic_message",
        lambda **kwargs: AnthropicMessageResult(
            text=str(L2_APPROVE_FIXTURE["text"]).replace("01TESTL2FIXTURE0000000001", reviewed_fact_id),
            model=str(L2_APPROVE_FIXTURE["model"]),
            usage=dict(L2_APPROVE_FIXTURE["usage"]),
        ),
    )
    monkeypatch.setattr(
        "umx.github_ops.comment_pr",
        lambda org, repo, pr, body: comments.append((org, repo, pr, body)) or True,
    )
    monkeypatch.setattr("umx.github_ops.merge_pr", lambda *args, **kwargs: True)
    monkeypatch.setattr("umx.github_ops.close_pr", lambda *args, **kwargs: False)
    monkeypatch.setattr("umx.dream.pipeline.git_fetch", lambda *args, **kwargs: True)
    monkeypatch.setattr("umx.dream.pipeline.git_push", lambda *args, **kwargs: True)

    cfg = default_config()
    cfg.dream.mode = "remote"
    cfg.org = "memory-org"
    save_config(config_path(), cfg)
    result = CliRunner().invoke(
        main,
        ["dream", "--cwd", str(project_dir), "--mode", "remote", "--tier", "l2", "--pr", "37"],
    )

    assert result.exit_code != 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "error"
    assert payload["action"] == "approve"
    assert payload["reason"] == "cannot push review provenance from detached HEAD"
    assert comments == []


def test_dream_l2_review_provider_reconcile_failure_does_not_persist_comment(
    project_dir: Path,
    project_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from umx.git_ops import git_add_and_commit, git_push

    reviewed_fact_id = "01TESTL2PROVIDERRECON001"
    remote = tmp_path / "review-provider-reconcile-fail.git"
    _connect_origin(project_repo, remote)
    git_add_and_commit(project_repo, message="seed review baseline")
    git_push(project_repo)
    subprocess.run(
        ["git", "-C", str(project_repo), "checkout", "-b", "dream/l1/review-provider-reconcile-fail"],
        capture_output=True,
        check=True,
    )
    add_fact(
        project_repo,
        _make_fact(
            fact_id=reviewed_fact_id,
            text="deploy approvals stay local to the service owner",
            topic="general",
            source_type=SourceType.GROUND_TRUTH_CODE,
        ),
        auto_commit=False,
    )
    git_add_and_commit(project_repo, message="provider review candidate")

    comments: list[tuple[str, str, int, str]] = []
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(
        "umx.providers.anthropic.send_anthropic_message",
        lambda **kwargs: AnthropicMessageResult(
            text=str(L2_APPROVE_FIXTURE["text"]).replace("01TESTL2FIXTURE0000000001", reviewed_fact_id),
            model=str(L2_APPROVE_FIXTURE["model"]),
            usage=dict(L2_APPROVE_FIXTURE["usage"]),
        ),
    )
    monkeypatch.setattr(
        "umx.github_ops.comment_pr",
        lambda org, repo, pr, body: comments.append((org, repo, pr, body)) or True,
    )
    monkeypatch.setattr("umx.github_ops.reconcile_pr_labels", lambda *args, **kwargs: False)
    monkeypatch.setattr("umx.github_ops.merge_pr", lambda *args, **kwargs: True)
    monkeypatch.setattr("umx.github_ops.close_pr", lambda *args, **kwargs: False)
    monkeypatch.setattr("umx.dream.pipeline.git_fetch", lambda *args, **kwargs: True)
    monkeypatch.setattr("umx.dream.pipeline.git_push", lambda *args, **kwargs: True)

    cfg = default_config()
    cfg.dream.mode = "remote"
    cfg.org = "memory-org"
    save_config(config_path(), cfg)
    result = CliRunner().invoke(
        main,
        ["dream", "--cwd", str(project_dir), "--mode", "remote", "--tier", "l2", "--pr", "38"],
    )

    assert result.exit_code != 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "error"
    assert payload["action"] == "approve"
    assert payload["reason"] == "failed to reconcile review labels for PR #38"
    assert comments == []


def test_dream_l2_review_provider_escalate_reconcile_failure_logs_and_skips_comment(
    project_dir: Path,
    project_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from umx.git_ops import git_add_and_commit, git_push

    reviewed_fact_id = "01TESTL2PROVIDERESCALATE1"
    remote = tmp_path / "review-provider-escalate-fail.git"
    _connect_origin(project_repo, remote)
    git_add_and_commit(project_repo, message="seed review baseline")
    git_push(project_repo)
    subprocess.run(
        ["git", "-C", str(project_repo), "checkout", "-b", "dream/l1/review-provider-escalate-fail"],
        capture_output=True,
        check=True,
    )
    add_fact(
        project_repo,
        _make_fact(
            fact_id=reviewed_fact_id,
            text="production postgres runs on 5434",
            topic="general",
            source_type=SourceType.GROUND_TRUTH_CODE,
            conflicts_with=["FACT-OLD-0001"],
        ),
        auto_commit=False,
    )
    git_add_and_commit(project_repo, message="provider review candidate")

    comments: list[tuple[str, str, int, str]] = []
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(
        "umx.providers.anthropic.send_anthropic_message",
        lambda **kwargs: AnthropicMessageResult(
            text=json.dumps(
                {
                    "action": "escalate",
                    "fact_notes": [
                        {
                            "fact_id": reviewed_fact_id,
                            "note": "Potential contradiction needs human review.",
                            "summary": "provider escalate fixture",
                        }
                    ],
                    "reason": "Potential contradiction requires human review.",
                    "violations": ["Requires human review"],
                }
            ),
            model=str(L2_APPROVE_FIXTURE["model"]),
            usage=dict(L2_APPROVE_FIXTURE["usage"]),
        ),
    )
    monkeypatch.setattr(
        "umx.github_ops.comment_pr",
        lambda org, repo, pr, body: comments.append((org, repo, pr, body)) or True,
    )
    monkeypatch.setattr("umx.github_ops.reconcile_pr_labels", lambda *args, **kwargs: False)
    monkeypatch.setattr("umx.github_ops.merge_pr", lambda *args, **kwargs: False)
    monkeypatch.setattr("umx.github_ops.close_pr", lambda *args, **kwargs: False)
    monkeypatch.setattr("umx.dream.pipeline.git_fetch", lambda *args, **kwargs: True)
    monkeypatch.setattr("umx.dream.pipeline.git_push", lambda *args, **kwargs: True)

    cfg = default_config()
    cfg.dream.mode = "remote"
    cfg.org = "memory-org"
    save_config(config_path(), cfg)
    result = CliRunner().invoke(
        main,
        ["dream", "--cwd", str(project_dir), "--mode", "remote", "--tier", "l2", "--pr", "39"],
    )

    assert result.exit_code != 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "error"
    assert payload["action"] == "escalate"
    assert payload["reason"] == "failed to reconcile review labels for PR #39"
    assert comments == []

    processing_rows = [
        json.loads(line)
        for line in (project_repo / "meta" / "processing.jsonl").read_text().splitlines()
        if line.strip()
    ]
    assert processing_rows[-1]["event"] == "review_completed"
    assert processing_rows[-1]["status"] == "error"
    assert processing_rows[-1]["action"] == "escalate"


def test_dream_l2_review_provider_comment_failure_reports_reconciled_labels(
    project_dir: Path,
    project_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from umx.git_ops import git_add_and_commit, git_push

    reviewed_fact_id = "01TESTL2COMMENTFAIL00001"
    remote = tmp_path / "review-provider-comment-fail.git"
    _connect_origin(project_repo, remote)
    git_add_and_commit(project_repo, message="seed review baseline")
    git_push(project_repo)
    subprocess.run(
        ["git", "-C", str(project_repo), "checkout", "-b", "dream/l1/review-provider-comment-fail"],
        capture_output=True,
        check=True,
    )
    add_fact(
        project_repo,
        _make_fact(
            fact_id=reviewed_fact_id,
            text="deploy approvals stay local to the service owner",
            topic="general",
            source_type=SourceType.GROUND_TRUTH_CODE,
        ),
        auto_commit=False,
    )
    git_add_and_commit(project_repo, message="provider review candidate")

    merge_calls: list[tuple[str, str, int]] = []
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(
        "umx.providers.anthropic.send_anthropic_message",
        lambda **kwargs: AnthropicMessageResult(
            text=str(L2_APPROVE_FIXTURE["text"]).replace("01TESTL2FIXTURE0000000001", reviewed_fact_id),
            model=str(L2_APPROVE_FIXTURE["model"]),
            usage=dict(L2_APPROVE_FIXTURE["usage"]),
        ),
    )
    monkeypatch.setattr("umx.github_ops.comment_pr", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        "umx.github_ops.read_pr_labels",
        lambda *args, **kwargs: [
            LABEL_STATE_EXTRACTION,
            "type: extraction",
            "confidence:high",
            "impact:local",
            "needs-docs",
        ],
    )
    monkeypatch.setattr(
        "umx.github_ops.merge_pr",
        lambda org, repo, pr, method="squash", **kwargs: merge_calls.append((org, repo, pr)) or True,
    )
    monkeypatch.setattr("umx.github_ops.close_pr", lambda *args, **kwargs: False)
    monkeypatch.setattr("umx.dream.pipeline.git_fetch", lambda *args, **kwargs: True)
    monkeypatch.setattr("umx.dream.pipeline.git_push", lambda *args, **kwargs: True)

    cfg = default_config()
    cfg.dream.mode = "remote"
    cfg.org = "memory-org"
    save_config(config_path(), cfg)
    result = CliRunner().invoke(
        main,
        ["dream", "--cwd", str(project_dir), "--mode", "remote", "--tier", "l2", "--pr", "27"],
    )

    assert result.exit_code != 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "error"
    assert payload["action"] == "approve"
    assert payload["reason"] == "failed to persist review comment for PR #27"
    assert LABEL_STATE_REVIEWED in payload["labels"]
    assert "needs-docs" in payload["labels"]
    assert merge_calls == []


def test_dream_l2_review_rejects_unrecognized_legacy_pr_body(
    project_dir: Path,
    project_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from umx.git_ops import git_add_and_commit, git_push

    remote = tmp_path / "review-legacy-body.git"
    _connect_origin(project_repo, remote)
    git_add_and_commit(project_repo, message="seed review baseline")
    git_push(project_repo)
    monkeypatch.setattr(
        "umx.github_ops.read_pr_body",
        lambda *args, **kwargs: f"plain body\n\n{LEGACY_PR_BODY_MARKER}",
    )

    cfg = default_config()
    cfg.dream.mode = "remote"
    cfg.org = "memory-org"
    save_config(config_path(), cfg)
    result = CliRunner().invoke(
        main,
        ["dream", "--cwd", str(project_dir), "--mode", "remote", "--tier", "l2", "--pr", "7"],
    )

    assert result.exit_code != 0
    assert result.exception is not None
    assert "recognized pre-fact-delta template" in str(result.exception)


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
    assert payload["status"] == "blocked"
    assert payload["action"] == "approve"
    assert payload["merge_blocked"] is True
    assert merged == []
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

    assert result.exit_code != 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "error"
    assert payload["action"] == "approve"
    assert payload["reason"] == "cannot push review provenance from detached HEAD"
    assert LABEL_STATE_EXTRACTION in payload["labels"]
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


def test_dream_l2_review_approve_reconcile_failure_preserves_current_labels(
    project_dir: Path,
    project_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from umx.git_ops import git_add_and_commit, git_push

    remote = tmp_path / "review-approve-reconcile-fail.git"
    _connect_origin(project_repo, remote)
    git_add_and_commit(project_repo, message="seed review baseline")
    git_push(project_repo)
    subprocess.run(
        ["git", "-C", str(project_repo), "checkout", "-b", "dream/l1/review-approve-reconcile-fail"],
        capture_output=True,
        check=True,
    )
    add_fact(
        project_repo,
        _make_fact(
            fact_id="01TESTL2RECONFAILAPPROVE1",
            text="deploys require a smoke check before release",
            topic="general",
            source_type=SourceType.GROUND_TRUTH_CODE,
        ),
        auto_commit=False,
    )
    git_add_and_commit(project_repo, message="review candidate")

    current_labels = [
        LABEL_STATE_EXTRACTION,
        "type: extraction",
        "confidence:high",
        "impact:local",
        "needs-docs",
    ]
    merge_calls: list[int] = []
    monkeypatch.setattr("umx.github_ops.read_pr_labels", lambda *args, **kwargs: list(current_labels))
    monkeypatch.setattr("umx.github_ops.reconcile_pr_labels", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        "umx.github_ops.merge_pr",
        lambda *args, **kwargs: merge_calls.append(1) or True,
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
        ["dream", "--cwd", str(project_dir), "--mode", "remote", "--tier", "l2", "--pr", "29"],
    )

    assert result.exit_code != 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "error"
    assert payload["action"] == "approve"
    assert payload["reason"] == "failed to reconcile review labels for PR #29"
    assert payload["labels"] == current_labels
    assert merge_calls == []


def test_dream_l2_review_approve_label_read_failure_reports_unknown_labels(
    project_dir: Path,
    project_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from umx.git_ops import git_add_and_commit, git_push

    remote = tmp_path / "review-approve-label-read-fail.git"
    _connect_origin(project_repo, remote)
    git_add_and_commit(project_repo, message="seed review baseline")
    git_push(project_repo)
    subprocess.run(
        ["git", "-C", str(project_repo), "checkout", "-b", "dream/l1/review-approve-label-read-fail"],
        capture_output=True,
        check=True,
    )
    add_fact(
        project_repo,
        _make_fact(
            fact_id="01TESTL2LABELREADFAILAPR",
            text="deploys require a smoke check before release",
            topic="general",
            source_type=SourceType.GROUND_TRUTH_CODE,
        ),
        auto_commit=False,
    )
    git_add_and_commit(project_repo, message="review candidate")

    merge_calls: list[int] = []
    monkeypatch.setattr("umx.github_ops.read_pr_labels", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "umx.github_ops.merge_pr",
        lambda *args, **kwargs: merge_calls.append(1) or True,
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
        ["dream", "--cwd", str(project_dir), "--mode", "remote", "--tier", "l2", "--pr", "31"],
    )

    assert result.exit_code != 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "error"
    assert payload["action"] == "approve"
    assert payload["reason"] == "failed to read current labels for PR #31"
    assert payload["labels"] == []
    assert merge_calls == []


def test_dream_l2_review_preserves_approved_state_on_rerun(
    project_dir: Path,
    project_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from umx.git_ops import git_add_and_commit, git_push

    remote = tmp_path / "review-approve-rerun.git"
    _connect_origin(project_repo, remote)
    git_add_and_commit(project_repo, message="seed review baseline")
    git_push(project_repo)
    subprocess.run(
        ["git", "-C", str(project_repo), "checkout", "-b", "dream/l1/review-approve-rerun"],
        capture_output=True,
        check=True,
    )
    add_fact(
        project_repo,
        _make_fact(
            fact_id="01TESTL2APPROVEDRERUN001",
            text="deploys require a smoke check before release",
            topic="general",
            source_type=SourceType.GROUND_TRUTH_CODE,
        ),
        auto_commit=False,
    )
    git_add_and_commit(project_repo, message="review candidate")

    labeled: list[tuple[str, str, int, tuple[str, ...]]] = []
    merged: list[tuple[str, str, int]] = []
    monkeypatch.setattr(
        "umx.github_ops.read_pr_labels",
        lambda *args, **kwargs: [
            LABEL_STATE_APPROVED,
            "type: extraction",
            "confidence:high",
            "impact:local",
        ],
    )
    monkeypatch.setattr(
        "umx.github_ops.reconcile_pr_labels",
        lambda org, repo, pr, labels, **kwargs: labeled.append((org, repo, pr, tuple(labels))) or True,
    )
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
        ["dream", "--cwd", str(project_dir), "--mode", "remote", "--tier", "l2", "--pr", "32"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "ok"
    assert payload["action"] == "approve"
    assert LABEL_STATE_APPROVED in payload["labels"]
    assert LABEL_STATE_REVIEWED not in payload["labels"]
    assert payload["merge_blocked"] is False
    assert labeled
    assert LABEL_STATE_APPROVED in labeled[0][3]
    assert LABEL_STATE_REVIEWED not in labeled[0][3]
    assert merged == [("memory-org", project_repo.name, 32)]


def test_dream_l2_review_approve_rerun_cleans_stale_human_review_label(
    project_dir: Path,
    project_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from umx.git_ops import git_add_and_commit, git_push

    remote = tmp_path / "review-approve-rerun-cleanup.git"
    _connect_origin(project_repo, remote)
    git_add_and_commit(project_repo, message="seed review baseline")
    git_push(project_repo)
    subprocess.run(
        ["git", "-C", str(project_repo), "checkout", "-b", "dream/l1/review-approve-rerun-cleanup"],
        capture_output=True,
        check=True,
    )
    add_fact(
        project_repo,
        _make_fact(
            fact_id="01TESTL2APPROVERERUNCLEAN",
            text="deploys require a smoke check before release",
            topic="general",
            source_type=SourceType.GROUND_TRUTH_CODE,
        ),
        auto_commit=False,
    )
    git_add_and_commit(project_repo, message="review candidate")

    labeled: list[tuple[str, str, int, tuple[str, ...]]] = []
    monkeypatch.setattr(
        "umx.github_ops.read_pr_labels",
        lambda *args, **kwargs: [
            LABEL_STATE_APPROVED,
            LABEL_HUMAN_REVIEW,
            "type: extraction",
            "confidence:high",
            "impact:local",
        ],
    )
    monkeypatch.setattr(
        "umx.github_ops.reconcile_pr_labels",
        lambda org, repo, pr, labels, **kwargs: labeled.append((org, repo, pr, tuple(labels))) or True,
    )
    monkeypatch.setattr("umx.github_ops.merge_pr", lambda *args, **kwargs: True)
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
        ["dream", "--cwd", str(project_dir), "--mode", "remote", "--tier", "l2", "--pr", "35"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "ok"
    assert payload["action"] == "approve"
    assert LABEL_STATE_APPROVED in payload["labels"]
    assert LABEL_HUMAN_REVIEW not in payload["labels"]
    assert labeled
    assert LABEL_STATE_APPROVED in labeled[0][3]
    assert LABEL_HUMAN_REVIEW not in labeled[0][3]


def test_dream_l2_review_escalation_demotes_approved_state_on_rerun(
    project_dir: Path,
    project_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from umx.git_ops import git_add_and_commit, git_push

    remote = tmp_path / "review-approved-escalate-rerun.git"
    _connect_origin(project_repo, remote)
    git_add_and_commit(project_repo, message="seed review baseline")
    git_push(project_repo)
    subprocess.run(
        ["git", "-C", str(project_repo), "checkout", "-b", "dream/l1/review-approved-escalate-rerun"],
        capture_output=True,
        check=True,
    )
    add_fact(
        project_repo,
        _make_fact(
            fact_id="01TESTL2APPROVEDESCAL001",
            text="production postgres runs on 5434",
            topic="general",
            source_type=SourceType.GROUND_TRUTH_CODE,
            conflicts_with=["FACT-OLD-0001"],
        ),
        auto_commit=False,
    )
    git_add_and_commit(project_repo, message="escalate candidate")

    labeled: list[tuple[str, str, int, tuple[str, ...]]] = []
    monkeypatch.setattr(
        "umx.github_ops.read_pr_labels",
        lambda *args, **kwargs: [
            LABEL_STATE_APPROVED,
            "type: extraction",
            "confidence:high",
            "impact:local",
        ],
    )
    monkeypatch.setattr(
        "umx.github_ops.reconcile_pr_labels",
        lambda org, repo, pr, labels, **kwargs: labeled.append((org, repo, pr, tuple(labels))) or True,
    )
    monkeypatch.setattr("umx.github_ops.merge_pr", lambda *args, **kwargs: False)
    monkeypatch.setattr("umx.github_ops.close_pr", lambda *args, **kwargs: False)
    monkeypatch.setattr("umx.github_ops.comment_pr", lambda *args, **kwargs: True)

    cfg = default_config()
    cfg.dream.mode = "remote"
    cfg.org = "memory-org"
    save_config(config_path(), cfg)
    result = CliRunner().invoke(
        main,
        ["dream", "--cwd", str(project_dir), "--mode", "remote", "--tier", "l2", "--pr", "34"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "ok"
    assert payload["action"] == "escalate"
    assert LABEL_STATE_REVIEWED in payload["labels"]
    assert LABEL_HUMAN_REVIEW in payload["labels"]
    assert LABEL_STATE_APPROVED not in payload["labels"]
    assert labeled
    assert LABEL_STATE_REVIEWED in labeled[0][3]
    assert LABEL_HUMAN_REVIEW in labeled[0][3]
    assert LABEL_STATE_APPROVED not in labeled[0][3]


def test_dream_l2_review_escalation_removes_live_approved_label(
    project_dir: Path,
    project_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from umx.git_ops import git_add_and_commit, git_push

    remote = tmp_path / "review-approved-escalate-live.git"
    _connect_origin(project_repo, remote)
    git_add_and_commit(project_repo, message="seed review baseline")
    git_push(project_repo)
    subprocess.run(
        ["git", "-C", str(project_repo), "checkout", "-b", "dream/l1/review-approved-escalate-live"],
        capture_output=True,
        check=True,
    )
    add_fact(
        project_repo,
        _make_fact(
            fact_id="01TESTL2APPROVEDLIVE0001",
            text="production postgres runs on 5434",
            topic="general",
            source_type=SourceType.GROUND_TRUTH_CODE,
            conflicts_with=["FACT-OLD-0001"],
        ),
        auto_commit=False,
    )
    git_add_and_commit(project_repo, message="escalate candidate")

    gh_calls: list[tuple[str, ...]] = []
    label_reads = [
        [
            LABEL_STATE_EXTRACTION,
            "type: extraction",
            "confidence:high",
            "impact:local",
        ],
        [
            LABEL_STATE_APPROVED,
            LABEL_STATE_EXTRACTION,
            "type: extraction",
            "confidence:high",
            "impact:local",
        ],
    ]

    def _run_gh(*args: str, **kwargs: object) -> subprocess.CompletedProcess[str]:
        gh_calls.append(tuple(args))
        return subprocess.CompletedProcess(args=list(args), returncode=0, stdout="", stderr="")

    monkeypatch.setattr("umx.github_ops.read_pr_labels", lambda *args, **kwargs: list(label_reads.pop(0)))
    monkeypatch.setattr("umx.github_ops.reconcile_pr_labels", github_reconcile_pr_labels)
    monkeypatch.setattr("umx.github_ops.ensure_governance_labels", lambda *args, **kwargs: True)
    monkeypatch.setattr("umx.github_ops._run_gh", _run_gh)
    monkeypatch.setattr("umx.github_ops.merge_pr", lambda *args, **kwargs: False)
    monkeypatch.setattr("umx.github_ops.close_pr", lambda *args, **kwargs: False)
    monkeypatch.setattr("umx.github_ops.comment_pr", lambda *args, **kwargs: True)

    cfg = default_config()
    cfg.dream.mode = "remote"
    cfg.org = "memory-org"
    save_config(config_path(), cfg)
    result = CliRunner().invoke(
        main,
        ["dream", "--cwd", str(project_dir), "--mode", "remote", "--tier", "l2", "--pr", "43"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "ok"
    assert payload["action"] == "escalate"
    assert LABEL_STATE_APPROVED not in payload["labels"]
    assert gh_calls
    edit_args = gh_calls[0]
    assert "--remove-label" in edit_args
    assert LABEL_STATE_APPROVED in edit_args


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
    assert payload["status"] == "blocked"
    assert payload["action"] == "approve"
    assert payload["merge_blocked"] is True
    assert merged == []


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
        "umx.github_ops.reconcile_pr_labels",
        lambda org, repo, pr, labels, **kwargs: labeled.append((org, repo, pr, tuple(labels))) or True,
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
    _assert_human_review_reconciliation(
        labeled,
        org="memory-org",
        repo="umx-user",
        pr_number=33,
    )
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
        "umx.github_ops.reconcile_pr_labels",
        lambda org, repo, pr, labels, **kwargs: labeled.append((org, repo, pr, tuple(labels))) or True,
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
    _assert_human_review_reconciliation(
        labeled,
        org="memory-org",
        repo=project_repo.name,
        pr_number=9,
    )
    assert payload["audit_note"] == commented[0][3]
    assert "contradictions" in payload["reason"]


def test_dream_l2_review_escalate_reconcile_failure_preserves_current_labels(
    project_dir: Path,
    project_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from umx.git_ops import git_add_and_commit, git_push

    remote = tmp_path / "review-escalate-reconcile-fail.git"
    _connect_origin(project_repo, remote)
    git_add_and_commit(project_repo, message="seed review baseline")
    git_push(project_repo)
    subprocess.run(
        ["git", "-C", str(project_repo), "checkout", "-b", "dream/l1/review-escalate-reconcile-fail"],
        capture_output=True,
        check=True,
    )
    add_fact(
        project_repo,
        _make_fact(
            fact_id="01TESTL2RECONFAILESCAL01",
            text="production postgres runs on 5434",
            topic="general",
            source_type=SourceType.GROUND_TRUTH_CODE,
            conflicts_with=["FACT-OLD-0001"],
        ),
        auto_commit=False,
    )
    git_add_and_commit(project_repo, message="escalate candidate")

    current_labels = [
        LABEL_STATE_EXTRACTION,
        "type: extraction",
        "confidence:high",
        "impact:local",
        "needs-docs",
    ]
    commented: list[tuple[str, str, int, str]] = []
    monkeypatch.setattr("umx.github_ops.read_pr_labels", lambda *args, **kwargs: list(current_labels))
    monkeypatch.setattr("umx.github_ops.reconcile_pr_labels", lambda *args, **kwargs: False)
    monkeypatch.setattr("umx.github_ops.merge_pr", lambda *args, **kwargs: False)
    monkeypatch.setattr("umx.github_ops.close_pr", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        "umx.github_ops.comment_pr",
        lambda org, repo, pr, body: commented.append((org, repo, pr, body)) or True,
    )

    cfg = default_config()
    cfg.dream.mode = "remote"
    cfg.org = "memory-org"
    save_config(config_path(), cfg)
    result = CliRunner().invoke(
        main,
        ["dream", "--cwd", str(project_dir), "--mode", "remote", "--tier", "l2", "--pr", "30"],
    )

    assert result.exit_code != 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "error"
    assert payload["action"] == "escalate"
    assert payload["reason"] == "failed to reconcile review labels for PR #30"
    assert payload["labels"] == current_labels
    assert commented == []


def test_dream_l2_review_escalate_label_read_failure_reports_unknown_labels(
    project_dir: Path,
    project_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from umx.git_ops import git_add_and_commit, git_push

    remote = tmp_path / "review-escalate-label-read-fail.git"
    _connect_origin(project_repo, remote)
    git_add_and_commit(project_repo, message="seed review baseline")
    git_push(project_repo)
    subprocess.run(
        ["git", "-C", str(project_repo), "checkout", "-b", "dream/l1/review-escalate-label-read-fail"],
        capture_output=True,
        check=True,
    )
    add_fact(
        project_repo,
        _make_fact(
            fact_id="01TESTL2LABELREADFAILESC",
            text="production postgres runs on 5434",
            topic="general",
            source_type=SourceType.GROUND_TRUTH_CODE,
            conflicts_with=["FACT-OLD-0001"],
        ),
        auto_commit=False,
    )
    git_add_and_commit(project_repo, message="escalate candidate")

    commented: list[tuple[str, str, int, str]] = []
    monkeypatch.setattr("umx.github_ops.read_pr_labels", lambda *args, **kwargs: None)
    monkeypatch.setattr("umx.github_ops.merge_pr", lambda *args, **kwargs: False)
    monkeypatch.setattr("umx.github_ops.close_pr", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        "umx.github_ops.comment_pr",
        lambda org, repo, pr, body: commented.append((org, repo, pr, body)) or True,
    )

    cfg = default_config()
    cfg.dream.mode = "remote"
    cfg.org = "memory-org"
    save_config(config_path(), cfg)
    result = CliRunner().invoke(
        main,
        ["dream", "--cwd", str(project_dir), "--mode", "remote", "--tier", "l2", "--pr", "33"],
    )

    assert result.exit_code != 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "error"
    assert payload["action"] == "escalate"
    assert payload["reason"] == "failed to read current labels for PR #33"
    assert payload["labels"] == []
    assert commented == []


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


def test_dream_push_and_open_pr_blocks_conflicting_governance_pr_before_push(
    project_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from umx.github_ops import GitHubRepoRef

    push_calls: list[str] = []
    conflict = GovernancePRConflictError(
        [
            GovernancePROverlap(
                pr_number=41,
                pr_url="https://github.com/memory-org/project/pull/41",
                pr_title="Existing governance PR",
                head_ref="dream/l1/existing",
                overlapping_fact_ids=("FACT-CONFLICT-1",),
            )
        ]
    )

    def raise_conflict(*args, **kwargs) -> None:
        raise conflict

    monkeypatch.setattr(
        "umx.github_ops.assert_expected_github_origin",
        lambda *args, **kwargs: GitHubRepoRef(
            owner="memory-org",
            name="project",
            url="https://github.com/memory-org/project.git",
        ),
    )
    monkeypatch.setattr("umx.github_ops.gh_available", lambda: True)
    monkeypatch.setattr("umx.github_ops.assert_no_open_governance_pr_overlap", raise_conflict)
    monkeypatch.setattr(
        "umx.github_ops.push_branch",
        lambda *args, **kwargs: push_calls.append("pushed") or True,
    )
    monkeypatch.setattr("umx.dream.pipeline.assert_push_safe", lambda *args, **kwargs: None)
    monkeypatch.setattr("umx.dream.pipeline.assert_signed_commit_range", lambda *args, **kwargs: None)

    cfg = default_config()
    cfg.org = "memory-org"
    cfg.dream.mode = "remote"
    pipeline = DreamPipeline(project_dir, config=cfg)
    proposal = PRProposal(
        title="Conflict test",
        body=render_governance_pr_body(
            heading="Dream L1 Extraction",
            summary_lines=["- conflict test"],
            fact_delta=FactDeltaBlock(
                added=(
                    FactDeltaEntry(
                        fact_id="FACT-CONFLICT-1",
                        topic="ops",
                        path="facts/topics/ops.md",
                        summary="conflicting fact",
                    ),
                ),
            ),
        ),
        branch="dream/l1/conflict-test",
        labels=desired_governance_labels(["type: extraction"], lifecycle_label=LABEL_STATE_EXTRACTION),
        files_changed=["facts/topics/ops.md"],
    )

    result = pipeline._push_and_open_pr(proposal)

    assert result is None
    assert push_calls == []
    assert pipeline._push_block_reason is not None
    assert "PR #41 https://github.com/memory-org/project/pull/41" in pipeline._push_block_reason


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


def test_forget_governed_requires_fact_not_topic(project_dir: Path) -> None:
    _set_governed_mode()

    result = CliRunner().invoke(
        main,
        ["forget", "--cwd", str(project_dir), "--topic", "devenv", "--governed"],
    )

    assert result.exit_code != 0
    assert "--governed currently supports --fact only" in result.output


def test_forget_governed_opens_tombstone_pr_and_merge_removes_fact(
    project_dir: Path,
    project_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from umx.git_ops import git_add_and_commit, git_push

    remote = tmp_path / "forget-governed.git"
    _connect_origin(project_repo, remote)
    fact = _make_fact(
        fact_id="FACT_FORGET_GOVERNED",
        text="legacy deploy runbook moved to docs/operations",
        topic="ops",
    )
    add_fact(project_repo, fact, auto_commit=False)
    git_add_and_commit(project_repo, message="seed governed forget fact")
    git_push(project_repo)
    _set_governed_mode()

    pr_calls: list[dict[str, object]] = []
    monkeypatch.setattr("umx.github_ops.gh_available", lambda: True)
    monkeypatch.setattr("umx.github_ops.push_branch", lambda *args, **kwargs: True)

    def _create_pr(
        org: str,
        repo_name: str,
        branch: str,
        title: str,
        body: str,
        labels: list[str] | None = None,
    ) -> int:
        pr_calls.append(
            {
                "org": org,
                "repo_name": repo_name,
                "branch": branch,
                "title": title,
                "body": body,
                "labels": list(labels or []),
            }
        )
        return 17

    monkeypatch.setattr("umx.github_ops.create_pr", _create_pr)

    result = CliRunner().invoke(
        main,
        ["forget", "--cwd", str(project_dir), "--fact", fact.fact_id, "--governed"],
    )

    assert result.exit_code == 0, result.output
    assert "opened governed tombstone PR #17" in result.output
    assert len(pr_calls) == 1
    proposal = pr_calls[0]
    assert str(proposal["branch"]).startswith("proposal/")
    assert set(proposal["labels"]) >= {
        LABEL_TYPE_DELETION,
        LABEL_STATE_EXTRACTION,
        LABEL_HUMAN_REVIEW,
    }
    payload = assert_governance_pr_body(str(proposal["body"]))
    assert payload is not None
    assert payload["tombstoned"][0]["fact_id"] == fact.fact_id
    assert payload["tombstoned"][0]["path"] == "facts/topics/ops.md"

    current_branch = subprocess.run(
        ["git", "-C", str(project_repo), "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert current_branch.stdout.strip() == "main"
    assert [item.fact_id for item in load_all_facts(project_repo, include_superseded=False)] == [
        fact.fact_id
    ]
    assert load_tombstones(project_repo) == []

    subprocess.run(
        ["git", "-C", str(project_repo), "merge", "--ff-only", str(proposal["branch"])],
        capture_output=True,
        text=True,
        check=True,
    )

    assert load_all_facts(project_repo, include_superseded=False) == []
    assert [item.fact_id for item in load_tombstones(project_repo)] == [fact.fact_id]


def test_forget_governed_restores_main_when_branch_commit_fails(
    project_dir: Path,
    project_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from umx.git_ops import GitCommitResult, git_add_and_commit, git_push

    remote = tmp_path / "forget-governed-fail.git"
    _connect_origin(project_repo, remote)
    fact = _make_fact(
        fact_id="FACT_FORGET_GOVERNED_FAIL",
        text="obsolete fact that should stay on main after failure",
        topic="ops",
    )
    add_fact(project_repo, fact, auto_commit=False)
    git_add_and_commit(project_repo, message="seed governed forget failure fact")
    git_push(project_repo)
    _set_governed_mode()
    monkeypatch.setattr(
        "umx.fact_actions.git_add_and_commit",
        lambda *args, **kwargs: GitCommitResult.failed_result(stderr="simulated commit failure"),
    )

    result = CliRunner().invoke(
        main,
        ["forget", "--cwd", str(project_dir), "--fact", fact.fact_id, "--governed"],
    )

    assert result.exit_code != 0
    assert "commit failed: simulated commit failure" in result.output
    current_branch = subprocess.run(
        ["git", "-C", str(project_repo), "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert current_branch.stdout.strip() == "main"
    status = subprocess.run(
        ["git", "-C", str(project_repo), "status", "--short"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert status.stdout.strip() == ""
    assert [item.fact_id for item in load_all_facts(project_repo, include_superseded=False)] == [
        fact.fact_id
    ]
    assert load_tombstones(project_repo) == []


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
    fact_path.write_text(f"# deploy\n\n## Facts\n- aws key {AWS_ACCESS_KEY_ID}\n")

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
        "umx.github_ops.reconcile_pr_labels",
        lambda org, repo, pr, labels, **kwargs: labeled.append((org, repo, pr, tuple(labels))) or True,
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
    _assert_human_review_reconciliation(
        labeled,
        org="memory-org",
        repo=project_repo.name,
        pr_number=10,
        required_labels=(LABEL_TYPE_DELETION,),
    )
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
        "umx.github_ops.reconcile_pr_labels",
        lambda org, repo, pr, labels, **kwargs: labeled.append((org, repo, pr, tuple(labels))) or True,
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
    _assert_human_review_reconciliation(
        labeled,
        org="memory-org",
        repo=project_repo.name,
        pr_number=11,
    )
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
        "umx.github_ops.reconcile_pr_labels",
        lambda org, repo, pr, labels, **kwargs: labeled.append((org, repo, pr, tuple(labels))) or True,
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
    _assert_human_review_reconciliation(
        labeled,
        org="memory-org",
        repo=project_repo.name,
        pr_number=12,
    )
    assert commented


def test_dream_l2_review_mixed_changes_reconcile_failure_skips_comment(
    project_dir: Path,
    project_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from umx.git_ops import git_add_and_commit, git_push

    remote = tmp_path / "review-mixed-governed-reconcile-fail.git"
    _connect_origin(project_repo, remote)
    git_add_and_commit(project_repo, message="seed mixed baseline")
    git_push(project_repo)
    subprocess.run(
        ["git", "-C", str(project_repo), "checkout", "-b", "dream/l1/review-mixed-governed-reconcile-fail"],
        capture_output=True,
        check=True,
    )
    add_fact(
        project_repo,
        _make_fact(
            fact_id="01TESTL2MIXEDRECONFAIL01",
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

    current_labels = [
        LABEL_STATE_EXTRACTION,
        "type: extraction",
        "confidence:high",
        "impact:local",
    ]
    commented: list[tuple[str, str, int, str]] = []
    monkeypatch.setattr("umx.github_ops.read_pr_labels", lambda *args, **kwargs: list(current_labels))
    monkeypatch.setattr("umx.github_ops.reconcile_pr_labels", lambda *args, **kwargs: False)
    monkeypatch.setattr("umx.github_ops.merge_pr", lambda *args, **kwargs: False)
    monkeypatch.setattr("umx.github_ops.close_pr", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        "umx.github_ops.comment_pr",
        lambda org, repo, pr, body: commented.append((org, repo, pr, body)) or True,
    )

    _set_governed_mode()
    result = CliRunner().invoke(
        main,
        ["dream", "--cwd", str(project_dir), "--mode", "remote", "--tier", "l2", "--pr", "40"],
    )

    assert result.exit_code != 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "error"
    assert payload["action"] == "escalate"
    assert payload["reason"] == "failed to reconcile review labels for PR #40"
    assert payload["labels"] == current_labels
    assert commented == []


def test_dream_l2_review_mixed_changes_error_when_label_read_fails(
    project_dir: Path,
    project_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from umx.git_ops import git_add_and_commit, git_push

    remote = tmp_path / "review-mixed-label-read-fail.git"
    _connect_origin(project_repo, remote)
    git_add_and_commit(project_repo, message="seed mixed baseline")
    git_push(project_repo)
    subprocess.run(
        ["git", "-C", str(project_repo), "checkout", "-b", "dream/l1/review-mixed-label-read-fail"],
        capture_output=True,
        check=True,
    )
    add_fact(
        project_repo,
        _make_fact(
            fact_id="01TESTL2MIXEDFAILREAD001",
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
    monkeypatch.setattr("umx.github_ops.read_pr_labels", lambda *args, **kwargs: None)
    monkeypatch.setattr("umx.github_ops.merge_pr", lambda *args, **kwargs: False)
    monkeypatch.setattr("umx.github_ops.close_pr", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        "umx.github_ops.comment_pr",
        lambda org, repo, pr, body: commented.append((org, repo, pr, body)) or True,
    )
    monkeypatch.setattr(
        "umx.github_ops.reconcile_pr_labels",
        lambda org, repo, pr, labels, **kwargs: labeled.append((org, repo, pr, tuple(labels))) or True,
    )

    _set_governed_mode()
    result = CliRunner().invoke(
        main,
        ["dream", "--cwd", str(project_dir), "--mode", "remote", "--tier", "l2", "--pr", "28"],
    )

    assert result.exit_code != 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "error"
    assert payload["action"] == "escalate"
    assert payload["reason"] == "failed to read current labels for PR #28"
    assert payload["labels"] == []
    assert labeled == []
    assert commented == []


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
        "umx.github_ops.reconcile_pr_labels",
        lambda org, repo, pr, labels, **kwargs: labeled.append((org, repo, pr, tuple(labels))) or True,
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
    _assert_human_review_reconciliation(
        labeled,
        org="memory-org",
        repo=project_repo.name,
        pr_number=13,
        required_labels=(LABEL_TYPE_DELETION,),
    )
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
        "umx.github_ops.reconcile_pr_labels",
        lambda org, repo, pr, labels, **kwargs: labeled.append((org, repo, pr, tuple(labels))) or True,
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
    _assert_human_review_reconciliation(
        labeled,
        org="memory-org",
        repo=project_repo.name,
        pr_number=16,
    )
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
    assert payload["status"] == "blocked"
    assert payload["action"] == "approve"
    assert payload["merge_blocked"] is True
    assert merged == []


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
    assert payload["status"] == "blocked"
    assert payload["action"] == "approve"
    assert payload["merge_blocked"] is True
    assert merged == []
