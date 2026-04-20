from __future__ import annotations

import json
import subprocess
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from tests.secret_literals import AWS_ACCESS_KEY_ID
from umx.cli import main
from umx.config import default_config
from umx.cross_project import (
    build_cross_project_promotion_report,
    collect_promotion_candidates,
    cross_project_audit_report,
)
from umx.governance import GovernancePRConflictError
from umx.github_ops import GitHubError
from umx.governance import GovernancePROverlap, assert_governance_pr_body
from umx.git_ops import GitCommitResult
from umx.memory import add_fact, load_all_facts, replace_fact
from umx.models import (
    ConsolidationStatus,
    Fact,
    MemoryType,
    Provenance,
    Scope,
    SourceType,
    Verification,
    utcnow,
)
from umx.scope import init_project_memory, project_memory_dir


def _make_fact(
    fact_id: str,
    text: str,
    *,
    topic: str = "shared",
    scope: Scope = Scope.PROJECT,
    encoding_strength: int = 3,
    consolidation_status: ConsolidationStatus = ConsolidationStatus.STABLE,
    created_days_ago: int = 8,
    conflicts_with: list[str] | None = None,
) -> Fact:
    created = utcnow() - timedelta(days=created_days_ago)
    return Fact(
        fact_id=fact_id,
        text=text,
        scope=scope,
        topic=topic,
        encoding_strength=encoding_strength,
        memory_type=MemoryType.EXPLICIT_SEMANTIC,
        verification=Verification.SELF_REPORTED,
        source_type=SourceType.LLM_INFERENCE,
        source_tool="test",
        source_session=f"session-{fact_id.lower()}",
        consolidation_status=consolidation_status,
        conflicts_with=conflicts_with or [],
        created=created,
        provenance=Provenance(extracted_by="test", sessions=[f"session-{fact_id.lower()}"]),
    )


def _init_project(tmp_path: Path, name: str) -> tuple[Path, Path]:
    project_dir = tmp_path / name
    project_dir.mkdir()
    (project_dir / ".git").mkdir()
    init_project_memory(project_dir)
    return project_dir, project_memory_dir(project_dir)


def _snapshot_tree(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _git_output(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        check=True,
        text=True,
    ).stdout


def _git_commit_all(repo: Path, message: str) -> None:
    if not _git_output(repo, "status", "--short").strip():
        return
    subprocess.run(
        ["git", "-C", str(repo), "add", "-A"],
        capture_output=True,
        text=True,
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", message],
        capture_output=True,
        text=True,
        check=True,
    )


def _connect_origin(repo: Path, remote: Path) -> None:
    subprocess.run(
        ["git", "init", "--bare", str(remote)],
        capture_output=True,
        text=True,
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "remote", "add", "origin", str(remote)],
        capture_output=True,
        text=True,
        check=True,
    )


def test_collect_candidates_returns_eligible_fact_for_three_repos(umx_home: Path, tmp_path: Path) -> None:
    for index, repo_name in enumerate(("alpha", "beta", "gamma"), start=1):
        _, repo = _init_project(tmp_path, repo_name)
        add_fact(repo, _make_fact(f"FACT_{index}", "Shared deploy checklist lives in docs/runbooks."), auto_commit=False)

    candidates = collect_promotion_candidates(umx_home)

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.key == "shared deploy checklist lives in docs/runbooks"
    assert candidate.repo_count == 3
    assert candidate.repos == ["alpha", "beta", "gamma"]
    assert candidate.eligible is True
    assert candidate.already_in_user_repo is False
    assert candidate.blocked_reasons == []


def test_collect_candidates_returns_repeated_but_blocked_fact_for_two_repos(umx_home: Path, tmp_path: Path) -> None:
    for index, repo_name in enumerate(("alpha", "beta"), start=1):
        _, repo = _init_project(tmp_path, repo_name)
        add_fact(repo, _make_fact(f"FACT_TWO_{index}", "Release notes live under docs/releases"), auto_commit=False)

    candidates = collect_promotion_candidates(umx_home)

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.repo_count == 2
    assert candidate.eligible is False
    assert candidate.blocked_reasons == ["below_min_repos"]


def test_collect_candidates_deduplicates_within_single_repo(umx_home: Path, tmp_path: Path) -> None:
    _, alpha_repo = _init_project(tmp_path, "alpha")
    add_fact(alpha_repo, _make_fact("FACT_DUP_1", "CI artifacts are stored in build/out"), auto_commit=False)
    add_fact(alpha_repo, _make_fact("FACT_DUP_2", "CI artifacts are stored in build/out"), auto_commit=False)
    for index, repo_name in enumerate(("beta", "gamma"), start=1):
        _, repo = _init_project(tmp_path, repo_name)
        add_fact(repo, _make_fact(f"FACT_OTHER_{index}", "CI artifacts are stored in build/out"), auto_commit=False)

    candidates = collect_promotion_candidates(umx_home)

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.repo_count == 3
    assert len(candidate.occurrences) == 3
    assert [occurrence.repo for occurrence in candidate.occurrences] == ["alpha", "beta", "gamma"]


def test_collect_candidates_excludes_weak_fragile_new_and_contradicted_facts(umx_home: Path, tmp_path: Path) -> None:
    qualifying = ("alpha", "zeta")
    for index, repo_name in enumerate(qualifying, start=1):
        _, repo = _init_project(tmp_path, repo_name)
        add_fact(repo, _make_fact(f"FACT_OK_{index}", "Staging runbooks live in docs/staging"), auto_commit=False)

    _, weak_repo = _init_project(tmp_path, "beta")
    add_fact(
        weak_repo,
        _make_fact("FACT_WEAK", "Staging runbooks live in docs/staging", encoding_strength=2),
        auto_commit=False,
    )

    _, fragile_repo = _init_project(tmp_path, "gamma")
    add_fact(
        fragile_repo,
        _make_fact(
            "FACT_FRAGILE",
            "Staging runbooks live in docs/staging",
            consolidation_status=ConsolidationStatus.FRAGILE,
        ),
        auto_commit=False,
    )

    _, new_repo = _init_project(tmp_path, "delta")
    add_fact(
        new_repo,
        _make_fact("FACT_NEW", "Staging runbooks live in docs/staging", created_days_ago=2),
        auto_commit=False,
    )

    _, contradicted_repo = _init_project(tmp_path, "epsilon")
    add_fact(
        contradicted_repo,
        _make_fact("FACT_CONFLICT", "Staging runbooks live in docs/staging", conflicts_with=["OTHER_FACT"]),
        auto_commit=False,
    )

    candidates = collect_promotion_candidates(umx_home)

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.repo_count == 2
    assert candidate.repos == ["alpha", "zeta"]
    assert candidate.eligible is False
    assert candidate.blocked_reasons == ["below_min_repos"]


def test_collect_candidates_flags_existing_user_fact(umx_home: Path, tmp_path: Path, user_repo: Path) -> None:
    for index, repo_name in enumerate(("alpha", "beta", "gamma"), start=1):
        _, repo = _init_project(tmp_path, repo_name)
        add_fact(repo, _make_fact(f"FACT_USER_{index}", "Shared onboarding notes live in docs/onboarding"), auto_commit=False)

    add_fact(
        user_repo,
        _make_fact("USER_FACT", "Shared onboarding notes live in docs/onboarding", scope=Scope.USER),
        auto_commit=False,
    )

    candidates = collect_promotion_candidates(umx_home)

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.repo_count == 3
    assert candidate.already_in_user_repo is True
    assert candidate.eligible is False
    assert candidate.blocked_reasons == ["already_in_user_repo"]


def test_audit_cross_project_cli_returns_json_without_mutating_repos(
    umx_home: Path,
    tmp_path: Path,
) -> None:
    project_dir, repo = _init_project(tmp_path, "alpha")
    add_fact(repo, _make_fact("FACT_ALPHA", "Shared release process lives in docs/releases"), auto_commit=False)
    for index, repo_name in enumerate(("beta", "gamma"), start=1):
        _, other_repo = _init_project(tmp_path, repo_name)
        add_fact(
            other_repo,
            _make_fact(f"FACT_{index}", "Shared release process lives in docs/releases"),
            auto_commit=False,
        )

    before = _snapshot_tree(umx_home)
    runner = CliRunner()
    result = runner.invoke(main, ["audit", "--cwd", str(project_dir), "--cross-project"])
    after = _snapshot_tree(umx_home)

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["mode"] == "cross_project"
    assert payload["repos_scanned"] == 3
    assert payload["eligible_candidate_count"] == 1
    assert before == after


def test_audit_cross_project_cli_does_not_normalize_manual_facts_on_read(
    umx_home: Path,
    tmp_path: Path,
) -> None:
    project_dir, repo = _init_project(tmp_path, "alpha")
    manual_path = repo / "facts" / "topics" / "manual.md"
    manual_path.write_text("# manual\n\n## Facts\n- Bare manual fact without metadata\n")

    before = _snapshot_tree(umx_home)
    runner = CliRunner()
    result = runner.invoke(main, ["audit", "--cwd", str(project_dir), "--cross-project"])
    after = _snapshot_tree(umx_home)

    assert result.exit_code == 0, result.output
    assert before == after
    assert manual_path.read_text() == before["projects/alpha/facts/topics/manual.md"].decode()


def test_audit_cross_project_cli_does_not_safety_sweep_dirty_sessions(
    umx_home: Path,
    tmp_path: Path,
) -> None:
    project_dir, repo = _init_project(tmp_path, "alpha")
    add_fact(repo, _make_fact("FACT_ALPHA", "Shared release process lives in docs/releases"), auto_commit=False)
    for index, repo_name in enumerate(("beta", "gamma"), start=1):
        _, other_repo = _init_project(tmp_path, repo_name)
        add_fact(
            other_repo,
            _make_fact(f"FACT_SESSION_{index}", "Shared release process lives in docs/releases"),
            auto_commit=False,
        )

    dirty_session = repo / "sessions" / "2026" / "dirty.jsonl"
    dirty_session.parent.mkdir(parents=True, exist_ok=True)
    dirty_session.write_text('{"session_id":"dirty"}\n')

    before_status = _git_output(repo, "status", "--short")
    before_commits = _git_output(repo, "rev-list", "--count", "HEAD").strip()

    runner = CliRunner()
    audit_result = runner.invoke(main, ["audit", "--cwd", str(project_dir), "--cross-project"])
    proposal_result = runner.invoke(
        main,
        [
            "audit",
            "--cwd",
            str(project_dir),
            "--cross-project",
            "--proposal-key",
            "shared release process lives in docs/releases",
        ],
    )

    after_status = _git_output(repo, "status", "--short")
    after_commits = _git_output(repo, "rev-list", "--count", "HEAD").strip()

    assert audit_result.exit_code == 0, audit_result.output
    assert proposal_result.exit_code == 0, proposal_result.output
    assert before_status == after_status
    assert before_commits == after_commits


def test_audit_cross_project_rejects_rederive_and_session_flags(umx_home: Path, tmp_path: Path) -> None:
    project_dir, _ = _init_project(tmp_path, "alpha")

    runner = CliRunner()
    rederive_result = runner.invoke(main, ["audit", "--cwd", str(project_dir), "--cross-project", "--rederive"])
    session_result = runner.invoke(main, ["audit", "--cwd", str(project_dir), "--cross-project", "--session", "sess-1"])

    assert rederive_result.exit_code != 0
    assert "--cross-project cannot be combined with --rederive" in rederive_result.output
    assert session_result.exit_code != 0
    assert "--cross-project cannot be combined with --session" in session_result.output


def test_cross_project_audit_report_is_json_friendly(umx_home: Path, tmp_path: Path) -> None:
    for index, repo_name in enumerate(("alpha", "beta", "gamma"), start=1):
        _, repo = _init_project(tmp_path, repo_name)
        add_fact(repo, _make_fact(f"FACT_JSON_{index}", "Runbook ownership is tracked in docs/owners"), auto_commit=False)

    report = cross_project_audit_report(umx_home, config=default_config())

    encoded = json.dumps(report, sort_keys=True)

    assert '"mode": "cross_project"' in encoded
    assert report["candidate_count"] == 1


def test_cross_project_report_ignores_stale_source_cache(
    umx_home: Path,
    tmp_path: Path,
) -> None:
    _, alpha_repo = _init_project(tmp_path, "alpha")
    add_fact(
        alpha_repo,
        _make_fact(
            "FACT_SOURCE_ALPHA",
            "Shared deploy checklist lives in docs/runbooks",
            topic="deploy",
        ),
        auto_commit=False,
    )
    source_cache = alpha_repo / "facts" / "topics" / "deploy.umx.json"
    source_cache_payload = json.loads(source_cache.read_text())
    source_cache_payload["facts"]["FACT_SOURCE_ALPHA"]["text"] = "Stale source cache text"
    source_cache.write_text(json.dumps(source_cache_payload, indent=2, sort_keys=True))
    for index, repo_name in enumerate(("beta", "gamma"), start=1):
        _, repo = _init_project(tmp_path, repo_name)
        add_fact(
            repo,
            _make_fact(
                f"FACT_SOURCE_{index}",
                "Shared deploy checklist lives in docs/runbooks",
                topic="deploy",
            ),
            auto_commit=False,
        )

    report = build_cross_project_promotion_report(
        umx_home,
        default_config(),
        candidate_key="shared deploy checklist lives in docs/runbooks",
    )

    alpha_occurrence = next(
        occurrence
        for occurrence in report["candidate"]["occurrences"]
        if occurrence["repo"] == "alpha"
    )
    assert alpha_occurrence["fact_id"] == "FACT_SOURCE_ALPHA"
    assert alpha_occurrence["text"] == "Shared deploy checklist lives in docs/runbooks"


def test_build_cross_project_promotion_report_for_majority_topic(
    umx_home: Path,
    tmp_path: Path,
) -> None:
    facts = (
        ("alpha", "deploy"),
        ("beta", "deploy"),
        ("gamma", "release"),
    )
    for index, (repo_name, topic) in enumerate(facts, start=1):
        _, repo = _init_project(tmp_path, repo_name)
        add_fact(
            repo,
            _make_fact(
                f"FACT_PROMO_{index}",
                "Shared deploy calendar lives in docs/calendar",
                topic=topic,
            ),
            auto_commit=False,
        )

    report = build_cross_project_promotion_report(
        umx_home,
        default_config(),
        candidate_key="shared deploy calendar lives in docs/calendar",
    )

    assert report["mode"] == "cross_project_proposal"
    assert report["proposal_ready"] is True
    assert report["blocked_reasons"] == []
    assert report["candidate"]["occurrences"][0]["fact_id"] == "FACT_PROMO_1"
    assert report["target"]["repo"] == "user"
    assert report["target"]["path"] == "user"
    assert report["target"]["topic"] == "deploy"
    assert report["target"]["file_path"] == "facts/topics/deploy.md"
    assert report["proposal"] is not None
    assert report["proposal"]["title"].startswith("[promotion]")
    assert "FACT_PROMO_1" in report["proposal"]["body"]


def test_build_cross_project_promotion_report_blocks_ambiguous_topic(
    umx_home: Path,
    tmp_path: Path,
) -> None:
    facts = (
        ("alpha", "deploy"),
        ("beta", "release"),
        ("gamma", "ops"),
    )
    for index, (repo_name, topic) in enumerate(facts, start=1):
        _, repo = _init_project(tmp_path, repo_name)
        add_fact(
            repo,
            _make_fact(
                f"FACT_AMBIG_{index}",
                "Shared deploy board lives in docs/board",
                topic=topic,
            ),
            auto_commit=False,
        )

    report = build_cross_project_promotion_report(
        umx_home,
        default_config(),
        candidate_key="shared deploy board lives in docs/board",
    )

    assert report["proposal_ready"] is False
    assert report["candidate"]["eligible"] is False
    assert "ambiguous_target_topic" in report["candidate"]["blocked_reasons"]
    assert "ambiguous_target_topic" in report["blocked_reasons"]
    assert report["target"]["topic"] is None
    assert report["proposal"] is None


def test_cross_project_audit_report_does_not_count_ambiguous_topic_candidate_as_eligible(
    umx_home: Path,
    tmp_path: Path,
) -> None:
    facts = (
        ("alpha", "deploy"),
        ("beta", "release"),
        ("gamma", "ops"),
    )
    for index, (repo_name, topic) in enumerate(facts, start=1):
        _, repo = _init_project(tmp_path, repo_name)
        add_fact(
            repo,
            _make_fact(
                f"FACT_AUDIT_AMBIG_{index}",
                "Shared release board lives in docs/board",
                topic=topic,
            ),
            auto_commit=False,
        )

    report = cross_project_audit_report(umx_home, config=default_config())

    assert report["eligible_candidate_count"] == 0
    assert report["candidates"][0]["eligible"] is False
    assert "ambiguous_target_topic" in report["candidates"][0]["blocked_reasons"]


def test_build_cross_project_promotion_report_blocks_when_one_repo_has_multiple_topics(
    umx_home: Path,
    tmp_path: Path,
) -> None:
    _, alpha_repo = _init_project(tmp_path, "alpha")
    add_fact(
        alpha_repo,
        _make_fact(
            "FACT_MULTI_1",
            "Shared release checklist lives in docs/checklists",
            topic="deploy",
        ),
        auto_commit=False,
    )
    add_fact(
        alpha_repo,
        _make_fact(
            "FACT_MULTI_2",
            "Shared release checklist lives in docs/checklists",
            topic="release",
        ),
        auto_commit=False,
    )
    for index, repo_name in enumerate(("beta", "gamma"), start=1):
        _, repo = _init_project(tmp_path, repo_name)
        add_fact(
            repo,
            _make_fact(
                f"FACT_MULTI_OTHER_{index}",
                "Shared release checklist lives in docs/checklists",
                topic="deploy",
            ),
            auto_commit=False,
        )

    report = build_cross_project_promotion_report(
        umx_home,
        default_config(),
        candidate_key="shared release checklist lives in docs/checklists",
    )

    assert report["proposal_ready"] is False
    assert report["candidate"]["eligible"] is False
    assert "ambiguous_target_topic" in report["blocked_reasons"]
    assert report["target"]["topic"] is None
    assert report["proposal"] is None


def test_build_cross_project_promotion_report_returns_blocked_candidate(
    umx_home: Path,
    tmp_path: Path,
) -> None:
    for index, repo_name in enumerate(("alpha", "beta"), start=1):
        _, repo = _init_project(tmp_path, repo_name)
        add_fact(
            repo,
            _make_fact(
                f"FACT_BLOCKED_{index}",
                "Shared release checklist lives in docs/checklists",
                topic="release",
            ),
            auto_commit=False,
        )

    report = build_cross_project_promotion_report(
        umx_home,
        default_config(),
        candidate_key="shared release checklist lives in docs/checklists",
    )

    assert report["proposal_ready"] is False
    assert report["blocked_reasons"] == ["below_min_repos"]
    assert report["candidate"]["eligible"] is False
    assert report["proposal"] is None


def test_audit_cross_project_cli_requires_cross_project_for_proposal_key(
    umx_home: Path,
    tmp_path: Path,
) -> None:
    project_dir, _ = _init_project(tmp_path, "alpha")

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["audit", "--cwd", str(project_dir), "--proposal-key", "shared deploy calendar"],
    )

    assert result.exit_code != 0
    assert "--proposal-key requires --cross-project" in result.output


def test_audit_cross_project_cli_returns_proposal_preview_without_mutating_repos(
    umx_home: Path,
    tmp_path: Path,
) -> None:
    project_dir, repo = _init_project(tmp_path, "alpha")
    add_fact(
        repo,
        _make_fact("FACT_ALPHA_PROMO", "Shared incident runbook lives in docs/incidents", topic="ops"),
        auto_commit=False,
    )
    for index, repo_name in enumerate(("beta", "gamma"), start=1):
        _, other_repo = _init_project(tmp_path, repo_name)
        add_fact(
            other_repo,
            _make_fact(
                f"FACT_PROMO_{index}",
                "Shared incident runbook lives in docs/incidents",
                topic="ops",
            ),
            auto_commit=False,
        )

    before = _snapshot_tree(umx_home)
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "audit",
            "--cwd",
            str(project_dir),
            "--cross-project",
            "--proposal-key",
            "shared incident runbook lives in docs/incidents",
        ],
    )
    after = _snapshot_tree(umx_home)

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["mode"] == "cross_project_proposal"
    assert payload["proposal_ready"] is True
    assert payload["target"]["topic"] == "ops"
    assert payload["proposal"]["branch"].startswith("proposal/")
    assert before == after


def test_audit_cross_project_cli_rejects_unknown_proposal_key(
    umx_home: Path,
    tmp_path: Path,
) -> None:
    project_dir, _ = _init_project(tmp_path, "alpha")

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "audit",
            "--cwd",
            str(project_dir),
            "--cross-project",
            "--proposal-key",
            "missing candidate key",
        ],
    )

    assert result.exit_code != 0
    assert "cross-project candidate not found: missing candidate key" in result.output


def test_propose_cross_project_materializes_local_user_branch(
    umx_home: Path,
    tmp_path: Path,
    user_repo: Path,
) -> None:
    _git_commit_all(user_repo, "test: clean user baseline")
    project_dir, repo = _init_project(tmp_path, "alpha")
    add_fact(
        repo,
        _make_fact("FACT_ALPHA_PROPOSE", "Shared incident runbook lives in docs/incidents", topic="ops"),
        auto_commit=False,
    )
    for index, repo_name in enumerate(("beta", "gamma"), start=1):
        _, other_repo = _init_project(tmp_path, repo_name)
        add_fact(
            other_repo,
            _make_fact(
                f"FACT_PROPOSE_{index}",
                "Shared incident runbook lives in docs/incidents",
                topic="ops",
            ),
            auto_commit=False,
        )

    before_projects = _snapshot_tree(umx_home / "projects")
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "propose",
            "--cwd",
            str(project_dir),
            "--cross-project",
            "--proposal-key",
            "shared incident runbook lives in docs/incidents",
        ],
    )
    after_projects = _snapshot_tree(umx_home / "projects")

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "ok"
    assert payload["mode"] == "cross_project_proposal_materialized"
    assert payload["branch"].startswith("proposal/")
    assert payload["target_repo"] == str(user_repo)
    assert payload["target_file"] == "facts/topics/ops.md"
    assert payload["proposal"]["proposal_ready"] is True
    assert payload["proposal"]["proposal"]["files_changed"] == [
        "facts/topics/ops.md",
        "facts/topics/ops.umx.json",
    ]
    assert "No branch, commit, push, or pull request has been created." not in payload["proposal"]["proposal"]["body"]
    assert "A local proposal branch and commit have been created." in payload["proposal"]["proposal"]["body"]
    assert before_projects == after_projects
    assert _git_output(user_repo, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"
    assert _git_output(user_repo, "status", "--short") == ""
    assert (
        subprocess.run(
            ["git", "-C", str(user_repo), "rev-parse", "--verify", f"refs/heads/{payload['branch']}"],
            capture_output=True,
            text=True,
            check=False,
        ).returncode
        == 0
    )

    diff_names = {
        line
        for line in _git_output(user_repo, "diff", "--name-only", f"main...{payload['branch']}").splitlines()
        if line
    }
    assert diff_names == {"facts/topics/ops.md", "facts/topics/ops.umx.json"}

    subprocess.run(
        ["git", "-C", str(user_repo), "checkout", payload["branch"]],
        capture_output=True,
        text=True,
        check=True,
    )
    try:
        facts = load_all_facts(user_repo, include_superseded=False)
        assert len(facts) == 1
        fact = facts[0]
        assert fact.scope == Scope.USER
        assert fact.topic == "ops"
        assert fact.text == "Shared incident runbook lives in docs/incidents"
        assert fact.verification == Verification.CORROBORATED
        assert fact.consolidation_status == ConsolidationStatus.STABLE
        assert fact.source_tool == "cross-project-promotion"
    finally:
        subprocess.run(
            ["git", "-C", str(user_repo), "checkout", "main"],
            capture_output=True,
            text=True,
            check=True,
        )


def test_propose_cross_project_pushes_materialized_user_branch(
    umx_home: Path,
    tmp_path: Path,
    user_repo: Path,
) -> None:
    _git_commit_all(user_repo, "test: clean user baseline")
    remote = tmp_path / "user-remote.git"
    _connect_origin(user_repo, remote)
    subprocess.run(
        ["git", "-C", str(user_repo), "push", "--set-upstream", "origin", "main"],
        capture_output=True,
        text=True,
        check=True,
    )

    project_dir, repo = _init_project(tmp_path, "alpha")
    add_fact(
        repo,
        _make_fact("FACT_PUSH_ALPHA", "Shared incident runbook lives in docs/incidents", topic="ops"),
        auto_commit=False,
    )
    for index, repo_name in enumerate(("beta", "gamma"), start=1):
        _, other_repo = _init_project(tmp_path, repo_name)
        add_fact(
            other_repo,
            _make_fact(
                f"FACT_PUSH_{index}",
                "Shared incident runbook lives in docs/incidents",
                topic="ops",
            ),
            auto_commit=False,
        )

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "propose",
            "--cwd",
            str(project_dir),
            "--cross-project",
            "--proposal-key",
            "shared incident runbook lives in docs/incidents",
            "--push",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "ok"
    assert payload["mode"] == "cross_project_proposal_pushed"
    assert payload["branch"].startswith("proposal/")
    assert payload["target_repo"] == str(user_repo)
    assert payload["target_file"] == "facts/topics/ops.md"
    assert payload["remote"] == str(remote)
    assert payload["proposal"]["proposal_ready"] is True
    assert "A local proposal branch and commit have been created and pushed to" in payload["proposal"]["proposal"]["body"]
    assert "No pull request has been created." in payload["proposal"]["proposal"]["body"]
    assert "No push or pull request has been created." not in payload["proposal"]["proposal"]["body"]
    assert "pull_request" not in payload
    assert "pr_number" not in payload
    assert _git_output(user_repo, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"
    assert _git_output(user_repo, "status", "--short") == ""
    assert (
        subprocess.run(
            ["git", "--git-dir", str(remote), "rev-parse", "--verify", f"refs/heads/{payload['branch']}"],
            capture_output=True,
            text=True,
            check=False,
        ).returncode
        == 0
    )


def test_propose_cross_project_push_redacts_credentialed_remote_output(
    umx_home: Path,
    tmp_path: Path,
    user_repo: Path,
) -> None:
    _git_commit_all(user_repo, "test: clean user baseline")
    remote = tmp_path / "user-remote-redacted.git"
    _connect_origin(user_repo, remote)
    subprocess.run(
        ["git", "-C", str(user_repo), "push", "--set-upstream", "origin", "main"],
        capture_output=True,
        text=True,
        check=True,
    )

    project_dir, repo = _init_project(tmp_path, "alpha")
    add_fact(
        repo,
        _make_fact("FACT_PUSH_REDACT_ALPHA", "Shared release train lives in docs/releases", topic="release"),
        auto_commit=False,
    )
    for index, repo_name in enumerate(("beta", "gamma"), start=1):
        _, other_repo = _init_project(tmp_path, repo_name)
        add_fact(
            other_repo,
            _make_fact(
                f"FACT_PUSH_REDACT_{index}",
                "Shared release train lives in docs/releases",
                topic="release",
            ),
            auto_commit=False,
        )

    runner = CliRunner()
    with patch(
        "umx.git_ops.git_remote_url",
        return_value="https://x-access-token:secret@github.com/memory-org/umx-user.git",
    ):
        result = runner.invoke(
            main,
            [
                "propose",
                "--cwd",
                str(project_dir),
                "--cross-project",
                "--proposal-key",
                "shared release train lives in docs/releases",
                "--push",
            ],
        )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["remote"] == "https://github.com/memory-org/umx-user.git"
    assert "secret@" not in payload["proposal"]["proposal"]["body"]


def test_propose_cross_project_rejects_push_and_open_pr_together(
    umx_home: Path,
    tmp_path: Path,
) -> None:
    project_dir, repo = _init_project(tmp_path, "alpha")
    add_fact(
        repo,
        _make_fact("FACT_COMBO_ALPHA", "Shared deploy checklist lives in docs/runbooks", topic="deploy"),
        auto_commit=False,
    )
    for index, repo_name in enumerate(("beta", "gamma"), start=1):
        _, other_repo = _init_project(tmp_path, repo_name)
        add_fact(
            other_repo,
            _make_fact(
                f"FACT_COMBO_{index}",
                "Shared deploy checklist lives in docs/runbooks",
                topic="deploy",
            ),
            auto_commit=False,
        )

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "propose",
            "--cwd",
            str(project_dir),
            "--cross-project",
            "--proposal-key",
            "shared deploy checklist lives in docs/runbooks",
            "--push",
            "--open-pr",
        ],
    )

    assert result.exit_code != 0
    assert "--push and --open-pr are separate steps" in result.output


def test_propose_cross_project_opens_pull_request_for_pushed_branch(
    umx_home: Path,
    tmp_path: Path,
    user_repo: Path,
) -> None:
    _git_commit_all(user_repo, "test: clean user baseline")
    remote = tmp_path / "user-remote-pr-open.git"
    _connect_origin(user_repo, remote)
    subprocess.run(
        ["git", "-C", str(user_repo), "push", "--set-upstream", "origin", "main"],
        capture_output=True,
        text=True,
        check=True,
    )

    project_dir, repo = _init_project(tmp_path, "alpha")
    add_fact(
        repo,
        _make_fact("FACT_OPEN_PR_ALPHA", "Shared incident runbook lives in docs/incidents", topic="ops"),
        auto_commit=False,
    )
    for index, repo_name in enumerate(("beta", "gamma"), start=1):
        _, other_repo = _init_project(tmp_path, repo_name)
        add_fact(
            other_repo,
            _make_fact(
                f"FACT_OPEN_PR_{index}",
                "Shared incident runbook lives in docs/incidents",
                topic="ops",
            ),
            auto_commit=False,
        )

    runner = CliRunner()
    pushed = runner.invoke(
        main,
        [
            "propose",
            "--cwd",
            str(project_dir),
            "--cross-project",
            "--proposal-key",
            "shared incident runbook lives in docs/incidents",
            "--push",
        ],
    )
    assert pushed.exit_code == 0, pushed.output
    pushed_payload = json.loads(pushed.output)

    with patch(
        "umx.git_ops.git_remote_url",
        return_value="https://x-access-token:secret@github.com/memory-org/umx-user.git",
    ), patch(
        "umx.github_ops.gh_available",
        return_value=True,
    ), patch(
        "umx.github_ops.create_pr",
        return_value=42,
    ) as mock_create_pr:
        opened = runner.invoke(
            main,
            [
                "propose",
                "--cwd",
                str(project_dir),
                "--cross-project",
                "--proposal-key",
                "shared incident runbook lives in docs/incidents",
                "--open-pr",
            ],
        )

    assert opened.exit_code == 0, opened.output
    payload = json.loads(opened.output)
    assert payload["status"] == "ok"
    assert payload["mode"] == "cross_project_proposal_pull_request_opened"
    assert payload["branch"] == pushed_payload["branch"]
    assert payload["target_repo"] == str(user_repo)
    assert payload["target_file"] == "facts/topics/ops.md"
    assert payload["remote"] == "https://github.com/memory-org/umx-user.git"
    assert "secret@" not in json.dumps(payload)
    assert payload["pr_number"] == 42
    assert payload["pull_request"] == {
        "number": 42,
        "url": "https://github.com/memory-org/umx-user/pull/42",
        "base": "main",
        "head": pushed_payload["branch"],
        "repo": "memory-org/umx-user",
    }
    assert "Pull request #42 has been created" in payload["proposal"]["proposal"]["body"]
    assert "No branch, commit, push, or pull request has been created." not in payload["proposal"]["proposal"]["body"]

    create_args = mock_create_pr.call_args.args
    assert create_args[:3] == ("memory-org", "umx-user", pushed_payload["branch"])
    assert "Cross-project promotion proposal" in create_args[4]
    assert "read-only preview" not in create_args[4]
    assert str(user_repo) not in create_args[4]
    assert "Target repo: `user memory repo`" in create_args[4]
    payload = assert_governance_pr_body(create_args[4])
    assert payload is not None
    assert payload["added"][0]["path"] == "facts/topics/ops.md"
    assert isinstance(payload["added"][0]["fact_id"], str)
    assert payload["added"][0]["fact_id"]


def test_propose_cross_project_open_pr_uses_pushed_branch_even_if_candidate_is_now_blocked(
    umx_home: Path,
    tmp_path: Path,
    user_repo: Path,
) -> None:
    _git_commit_all(user_repo, "test: clean user baseline")
    remote = tmp_path / "user-remote-pr-blocked.git"
    _connect_origin(user_repo, remote)
    subprocess.run(
        ["git", "-C", str(user_repo), "push", "--set-upstream", "origin", "main"],
        capture_output=True,
        text=True,
        check=True,
    )

    project_dir, repo = _init_project(tmp_path, "alpha")
    add_fact(
        repo,
        _make_fact("FACT_OPEN_BLOCKED_ALPHA", "Shared incident runbook lives in docs/incidents", topic="ops"),
        auto_commit=False,
    )
    for index, repo_name in enumerate(("beta", "gamma"), start=1):
        _, other_repo = _init_project(tmp_path, repo_name)
        add_fact(
            other_repo,
            _make_fact(
                f"FACT_OPEN_BLOCKED_{index}",
                "Shared incident runbook lives in docs/incidents",
                topic="ops",
            ),
            auto_commit=False,
        )

    runner = CliRunner()
    pushed = runner.invoke(
        main,
        [
            "propose",
            "--cwd",
            str(project_dir),
            "--cross-project",
            "--proposal-key",
            "shared incident runbook lives in docs/incidents",
            "--push",
        ],
    )
    assert pushed.exit_code == 0, pushed.output
    pushed_payload = json.loads(pushed.output)

    report = build_cross_project_promotion_report(
        umx_home,
        default_config(),
        candidate_key="shared incident runbook lives in docs/incidents",
    )
    assert report["proposal_ready"] is True

    add_fact(
        user_repo,
        _make_fact(
            "USER_BLOCKING_FACT",
            "Shared incident runbook lives in docs/incidents",
            scope=Scope.USER,
            topic="ops",
        ),
        auto_commit=False,
    )
    _git_commit_all(user_repo, "test: block candidate after push")

    with patch("umx.git_ops.git_remote_url", return_value="https://github.com/memory-org/umx-user.git"), patch(
        "umx.github_ops.gh_available",
        return_value=True,
    ), patch(
        "umx.github_ops.create_pr",
        return_value=43,
    ):
        opened = runner.invoke(
            main,
            [
                "propose",
                "--cwd",
                str(project_dir),
                "--cross-project",
                "--proposal-key",
                "shared incident runbook lives in docs/incidents",
                "--open-pr",
            ],
        )

    assert opened.exit_code == 0, opened.output
    payload = json.loads(opened.output)
    assert payload["pr_number"] == 43
    assert payload["branch"] == pushed_payload["branch"]


def test_propose_cross_project_open_pr_uses_saved_pushed_preview_even_if_candidate_text_changes(
    umx_home: Path,
    tmp_path: Path,
    user_repo: Path,
) -> None:
    original_text = "Shared pager rotation lives in docs/oncall"
    replacement_text = "Shared escalation map lives in docs/escalations"

    _git_commit_all(user_repo, "test: clean user baseline")
    remote = tmp_path / "user-remote-pr-drift.git"
    _connect_origin(user_repo, remote)
    subprocess.run(
        ["git", "-C", str(user_repo), "push", "--set-upstream", "origin", "main"],
        capture_output=True,
        text=True,
        check=True,
    )

    project_dir, repo = _init_project(tmp_path, "alpha")
    add_fact(repo, _make_fact("FACT_DRIFT_ALPHA", original_text, topic="ops"), auto_commit=False)
    repo_facts = [(repo, "FACT_DRIFT_ALPHA")]
    for index, repo_name in enumerate(("beta", "gamma"), start=1):
        _, other_repo = _init_project(tmp_path, repo_name)
        fact_id = f"FACT_DRIFT_{index}"
        add_fact(other_repo, _make_fact(fact_id, original_text, topic="ops"), auto_commit=False)
        repo_facts.append((other_repo, fact_id))

    runner = CliRunner()
    pushed = runner.invoke(
        main,
        [
            "propose",
            "--cwd",
            str(project_dir),
            "--cross-project",
            "--proposal-key",
            original_text,
            "--push",
        ],
    )
    assert pushed.exit_code == 0, pushed.output
    pushed_payload = json.loads(pushed.output)

    for repo_dir, fact_id in repo_facts:
        fact = next(fact for fact in load_all_facts(repo_dir, include_superseded=False) if fact.fact_id == fact_id)
        assert replace_fact(repo_dir, fact.clone(text=replacement_text)) is True
        _git_commit_all(repo_dir, f"test: drift candidate in {repo_dir.name}")

    with patch("umx.git_ops.git_remote_url", return_value="https://github.com/memory-org/umx-user.git"), patch(
        "umx.github_ops.gh_available",
        return_value=True,
    ), patch(
        "umx.github_ops.create_pr",
        return_value=44,
    ) as mock_create_pr:
        opened = runner.invoke(
            main,
            [
                "propose",
                "--cwd",
                str(project_dir),
                "--cross-project",
                "--proposal-key",
                original_text,
                "--open-pr",
            ],
        )

    assert opened.exit_code == 0, opened.output
    payload = json.loads(opened.output)
    assert payload["pr_number"] == 44
    assert payload["branch"] == pushed_payload["branch"]

    create_args = mock_create_pr.call_args.args
    assert original_text in create_args[4]
    assert replacement_text not in create_args[4]
    fact_delta = assert_governance_pr_body(create_args[4])
    assert fact_delta is not None
    assert fact_delta["added"][0]["summary"] == original_text
    assert isinstance(fact_delta["added"][0]["fact_id"], str)
    assert fact_delta["added"][0]["fact_id"]


def test_propose_cross_project_open_pr_surfaces_conflicting_pr_url(
    umx_home: Path,
    tmp_path: Path,
    user_repo: Path,
) -> None:
    _git_commit_all(user_repo, "test: clean user baseline")
    remote = tmp_path / "user-remote-pr-conflict.git"
    _connect_origin(user_repo, remote)
    subprocess.run(
        ["git", "-C", str(user_repo), "push", "--set-upstream", "origin", "main"],
        capture_output=True,
        text=True,
        check=True,
    )

    project_dir, repo = _init_project(tmp_path, "alpha")
    add_fact(
        repo,
        _make_fact("FACT_OPEN_CONFLICT_ALPHA", "Shared incident runbook lives in docs/incidents", topic="ops"),
        auto_commit=False,
    )
    for index, repo_name in enumerate(("beta", "gamma"), start=1):
        _, other_repo = _init_project(tmp_path, repo_name)
        add_fact(
            other_repo,
            _make_fact(
                f"FACT_OPEN_CONFLICT_{index}",
                "Shared incident runbook lives in docs/incidents",
                topic="ops",
            ),
            auto_commit=False,
        )

    runner = CliRunner()
    pushed = runner.invoke(
        main,
        [
            "propose",
            "--cwd",
            str(project_dir),
            "--cross-project",
            "--proposal-key",
            "shared incident runbook lives in docs/incidents",
            "--push",
        ],
    )
    assert pushed.exit_code == 0, pushed.output

    conflict = GovernancePRConflictError(
        [
            GovernancePROverlap(
                pr_number=41,
                pr_url="https://github.com/memory-org/umx-user/pull/41",
                pr_title="Existing governance PR",
                head_ref="proposal/existing",
                overlapping_fact_ids=("FACT-CONFLICT-1",),
            )
        ]
    )

    with patch("umx.git_ops.git_remote_url", return_value="https://github.com/memory-org/umx-user.git"), patch(
        "umx.github_ops.gh_available",
        return_value=True,
    ), patch(
        "umx.github_ops.create_pr",
        side_effect=conflict,
    ):
        opened = runner.invoke(
            main,
            [
                "propose",
                "--cwd",
                str(project_dir),
                "--cross-project",
                "--proposal-key",
                "shared incident runbook lives in docs/incidents",
                "--open-pr",
            ],
        )

    assert opened.exit_code != 0
    assert "PR #41 https://github.com/memory-org/umx-user/pull/41" in opened.output


def test_propose_cross_project_open_pr_requires_pushed_remote_branch(
    umx_home: Path,
    tmp_path: Path,
    user_repo: Path,
) -> None:
    _git_commit_all(user_repo, "test: clean user baseline")
    remote = tmp_path / "user-remote-pr-missing.git"
    _connect_origin(user_repo, remote)
    subprocess.run(
        ["git", "-C", str(user_repo), "push", "--set-upstream", "origin", "main"],
        capture_output=True,
        text=True,
        check=True,
    )

    project_dir, repo = _init_project(tmp_path, "alpha")
    add_fact(
        repo,
        _make_fact("FACT_OPEN_MISSING_ALPHA", "Shared release checklist lives in docs/checklists", topic="release"),
        auto_commit=False,
    )
    for index, repo_name in enumerate(("beta", "gamma"), start=1):
        _, other_repo = _init_project(tmp_path, repo_name)
        add_fact(
            other_repo,
            _make_fact(
                f"FACT_OPEN_MISSING_{index}",
                "Shared release checklist lives in docs/checklists",
                topic="release",
            ),
            auto_commit=False,
        )

    runner = CliRunner()
    with patch("umx.git_ops.git_remote_url", return_value="https://github.com/memory-org/umx-user.git"):
        result = runner.invoke(
            main,
            [
                "propose",
                "--cwd",
                str(project_dir),
                "--cross-project",
                "--proposal-key",
                "shared release checklist lives in docs/checklists",
                "--open-pr",
            ],
        )

    assert result.exit_code != 0
    assert "proposal branch is not pushed to origin" in result.output


def test_propose_cross_project_open_pr_requires_gh(
    umx_home: Path,
    tmp_path: Path,
    user_repo: Path,
) -> None:
    _git_commit_all(user_repo, "test: clean user baseline")
    remote = tmp_path / "user-remote-pr-no-gh.git"
    _connect_origin(user_repo, remote)
    subprocess.run(
        ["git", "-C", str(user_repo), "push", "--set-upstream", "origin", "main"],
        capture_output=True,
        text=True,
        check=True,
    )

    project_dir, repo = _init_project(tmp_path, "alpha")
    add_fact(
        repo,
        _make_fact("FACT_OPEN_GH_ALPHA", "Shared release train lives in docs/releases", topic="release"),
        auto_commit=False,
    )
    for index, repo_name in enumerate(("beta", "gamma"), start=1):
        _, other_repo = _init_project(tmp_path, repo_name)
        add_fact(
            other_repo,
            _make_fact(
                f"FACT_OPEN_GH_{index}",
                "Shared release train lives in docs/releases",
                topic="release",
            ),
            auto_commit=False,
        )

    runner = CliRunner()
    pushed = runner.invoke(
        main,
        [
            "propose",
            "--cwd",
            str(project_dir),
            "--cross-project",
            "--proposal-key",
            "shared release train lives in docs/releases",
            "--push",
        ],
    )
    assert pushed.exit_code == 0, pushed.output

    with patch("umx.git_ops.git_remote_url", return_value="https://github.com/memory-org/umx-user.git"), patch(
        "umx.github_ops.gh_available",
        return_value=False,
    ):
        result = runner.invoke(
            main,
            [
                "propose",
                "--cwd",
                str(project_dir),
                "--cross-project",
                "--proposal-key",
                "shared release train lives in docs/releases",
                "--open-pr",
            ],
        )

    assert result.exit_code != 0
    assert "gh CLI is not available or not authenticated" in result.output


def test_propose_cross_project_open_pr_surfaces_gh_retry_next_steps(
    umx_home: Path,
    tmp_path: Path,
    user_repo: Path,
) -> None:
    _git_commit_all(user_repo, "test: clean user baseline")
    remote = tmp_path / "user-remote-pr-gh-error.git"
    _connect_origin(user_repo, remote)
    subprocess.run(
        ["git", "-C", str(user_repo), "push", "--set-upstream", "origin", "main"],
        capture_output=True,
        text=True,
        check=True,
    )

    project_dir, repo = _init_project(tmp_path, "alpha")
    add_fact(
        repo,
        _make_fact("FACT_OPEN_GH_ERROR_ALPHA", "Shared release train lives in docs/releases", topic="release"),
        auto_commit=False,
    )
    for index, repo_name in enumerate(("beta", "gamma"), start=1):
        _, other_repo = _init_project(tmp_path, repo_name)
        add_fact(
            other_repo,
            _make_fact(
                f"FACT_OPEN_GH_ERROR_{index}",
                "Shared release train lives in docs/releases",
                topic="release",
            ),
            auto_commit=False,
        )

    runner = CliRunner()
    pushed = runner.invoke(
        main,
        [
            "propose",
            "--cwd",
            str(project_dir),
            "--cross-project",
            "--proposal-key",
            "shared release train lives in docs/releases",
            "--push",
        ],
    )
    assert pushed.exit_code == 0, pushed.output

    with patch("umx.git_ops.git_remote_url", return_value="https://github.com/memory-org/umx-user.git"), patch(
        "umx.github_ops.gh_available",
        side_effect=GitHubError(
            "gh auth status: 503 service unavailable (after 3 attempts). "
            "Next steps: verify network connectivity, gh auth, and GitHub rate limits, then retry."
        ),
    ):
        result = runner.invoke(
            main,
            [
                "propose",
                "--cwd",
                str(project_dir),
                "--cross-project",
                "--proposal-key",
                "shared release train lives in docs/releases",
                "--open-pr",
            ],
        )

    assert result.exit_code != 0
    assert "Next steps: verify network connectivity, gh auth, and GitHub rate limits, then retry." in result.output


def test_propose_cross_project_open_pr_requires_github_origin(
    umx_home: Path,
    tmp_path: Path,
    user_repo: Path,
) -> None:
    _git_commit_all(user_repo, "test: clean user baseline")
    remote = tmp_path / "user-remote-non-github.git"
    _connect_origin(user_repo, remote)
    subprocess.run(
        ["git", "-C", str(user_repo), "push", "--set-upstream", "origin", "main"],
        capture_output=True,
        text=True,
        check=True,
    )

    project_dir, repo = _init_project(tmp_path, "alpha")
    add_fact(
        repo,
        _make_fact("FACT_OPEN_LOCAL_ALPHA", "Shared rollback guide lives in docs/rollback", topic="ops"),
        auto_commit=False,
    )
    for index, repo_name in enumerate(("beta", "gamma"), start=1):
        _, other_repo = _init_project(tmp_path, repo_name)
        add_fact(
            other_repo,
            _make_fact(
                f"FACT_OPEN_LOCAL_{index}",
                "Shared rollback guide lives in docs/rollback",
                topic="ops",
            ),
            auto_commit=False,
        )

    runner = CliRunner()
    pushed = runner.invoke(
        main,
        [
            "propose",
            "--cwd",
            str(project_dir),
            "--cross-project",
            "--proposal-key",
            "shared rollback guide lives in docs/rollback",
            "--push",
        ],
    )
    assert pushed.exit_code == 0, pushed.output

    result = runner.invoke(
        main,
        [
            "propose",
            "--cwd",
            str(project_dir),
            "--cross-project",
            "--proposal-key",
            "shared rollback guide lives in docs/rollback",
            "--open-pr",
        ],
    )

    assert result.exit_code != 0
    assert "user repo origin must be a GitHub remote to open a PR" in result.output


def test_propose_cross_project_preserves_existing_user_topic_content(
    umx_home: Path,
    tmp_path: Path,
    user_repo: Path,
) -> None:
    manual_path = user_repo / "facts" / "topics" / "ops.md"
    manual_path.parent.mkdir(parents=True, exist_ok=True)
    manual_path.write_text("# ops\n\n## Facts\n- Bare user ops fact without metadata\n")
    _git_commit_all(user_repo, "test: seed user ops topic")

    project_dir, repo = _init_project(tmp_path, "alpha")
    add_fact(
        repo,
        _make_fact("FACT_PRESERVE_ALPHA", "Shared ops guide lives in docs/ops", topic="ops"),
        auto_commit=False,
    )
    for index, repo_name in enumerate(("beta", "gamma"), start=1):
        _, other_repo = _init_project(tmp_path, repo_name)
        add_fact(
            other_repo,
            _make_fact(
                f"FACT_PRESERVE_{index}",
                "Shared ops guide lives in docs/ops",
                topic="ops",
            ),
            auto_commit=False,
        )

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "propose",
            "--cwd",
            str(project_dir),
            "--cross-project",
            "--proposal-key",
            "shared ops guide lives in docs/ops",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    subprocess.run(
        ["git", "-C", str(user_repo), "checkout", payload["branch"]],
        capture_output=True,
        text=True,
        check=True,
    )
    try:
        content = manual_path.read_text()
        assert "- Bare user ops fact without metadata" in content
        assert "Shared ops guide lives in docs/ops" in content
    finally:
        subprocess.run(
            ["git", "-C", str(user_repo), "checkout", "main"],
            capture_output=True,
            text=True,
            check=True,
        )


def test_propose_cross_project_ignores_stale_user_cache_when_appending(
    umx_home: Path,
    tmp_path: Path,
    user_repo: Path,
) -> None:
    existing = _make_fact(
        "USER_EXISTING_FACT",
        "Existing ops guidance lives in docs/current-ops",
        scope=Scope.USER,
        topic="ops",
    )
    add_fact(user_repo, existing, auto_commit=False)
    cache_path = user_repo / "facts" / "topics" / "ops.umx.json"
    cache_payload = json.loads(cache_path.read_text())
    cache_payload["facts"]["USER_EXISTING_FACT"]["text"] = "Stale cached ops guidance"
    cache_path.write_text(json.dumps(cache_payload, indent=2, sort_keys=True))
    _git_commit_all(user_repo, "test: seed stale user cache")

    project_dir, repo = _init_project(tmp_path, "alpha")
    add_fact(
        repo,
        _make_fact("FACT_CACHE_ALPHA", "Shared ops guide lives in docs/ops", topic="ops"),
        auto_commit=False,
    )
    for index, repo_name in enumerate(("beta", "gamma"), start=1):
        _, other_repo = _init_project(tmp_path, repo_name)
        add_fact(
            other_repo,
            _make_fact(
                f"FACT_CACHE_{index}",
                "Shared ops guide lives in docs/ops",
                topic="ops",
            ),
            auto_commit=False,
        )

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "propose",
            "--cwd",
            str(project_dir),
            "--cross-project",
            "--proposal-key",
            "shared ops guide lives in docs/ops",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    subprocess.run(
        ["git", "-C", str(user_repo), "checkout", payload["branch"]],
        capture_output=True,
        text=True,
        check=True,
    )
    try:
        branch_cache = json.loads(cache_path.read_text())
        cached_texts = {
            item["text"]
            for item in branch_cache["facts"].values()
        }
        assert "Stale cached ops guidance" not in cached_texts
        assert "Existing ops guidance lives in docs/current-ops" in cached_texts
        assert "Shared ops guide lives in docs/ops" in cached_texts
        assert len(branch_cache["facts"]) == 2
    finally:
        subprocess.run(
            ["git", "-C", str(user_repo), "checkout", "main"],
            capture_output=True,
            text=True,
            check=True,
        )


def test_propose_cross_project_fails_for_blocked_candidate(
    umx_home: Path,
    tmp_path: Path,
) -> None:
    _git_commit_all(umx_home / "user", "test: clean user baseline")
    project_dir, repo = _init_project(tmp_path, "alpha")
    add_fact(
        repo,
        _make_fact("FACT_BLOCKED_ALPHA", "Shared release checklist lives in docs/checklists", topic="release"),
        auto_commit=False,
    )
    _, beta_repo = _init_project(tmp_path, "beta")
    add_fact(
        beta_repo,
        _make_fact("FACT_BLOCKED_BETA", "Shared release checklist lives in docs/checklists", topic="release"),
        auto_commit=False,
    )

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "propose",
            "--cwd",
            str(project_dir),
            "--cross-project",
            "--proposal-key",
            "shared release checklist lives in docs/checklists",
        ],
    )

    assert result.exit_code != 0
    assert "cross-project proposal is not ready: below_min_repos" in result.output


def test_propose_cross_project_fails_if_branch_exists(
    umx_home: Path,
    tmp_path: Path,
    user_repo: Path,
) -> None:
    _git_commit_all(user_repo, "test: clean user baseline")
    project_dir, repo = _init_project(tmp_path, "alpha")
    add_fact(
        repo,
        _make_fact("FACT_EXIST_ALPHA", "Shared deploy checklist lives in docs/runbooks", topic="deploy"),
        auto_commit=False,
    )
    for index, repo_name in enumerate(("beta", "gamma"), start=1):
        _, other_repo = _init_project(tmp_path, repo_name)
        add_fact(
            other_repo,
            _make_fact(
                f"FACT_EXIST_{index}",
                "Shared deploy checklist lives in docs/runbooks",
                topic="deploy",
            ),
            auto_commit=False,
        )

    preview = build_cross_project_promotion_report(
        umx_home,
        default_config(),
        candidate_key="shared deploy checklist lives in docs/runbooks",
    )
    branch = preview["proposal"]["branch"]
    subprocess.run(
        ["git", "-C", str(user_repo), "branch", branch],
        capture_output=True,
        text=True,
        check=True,
    )

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "propose",
            "--cwd",
            str(project_dir),
            "--cross-project",
            "--proposal-key",
            "shared deploy checklist lives in docs/runbooks",
        ],
    )

    assert result.exit_code != 0
    assert f"proposal branch already exists: {branch}" in result.output


def test_propose_cross_project_push_requires_origin_remote(
    umx_home: Path,
    tmp_path: Path,
    user_repo: Path,
) -> None:
    _git_commit_all(user_repo, "test: clean user baseline")
    project_dir, repo = _init_project(tmp_path, "alpha")
    add_fact(
        repo,
        _make_fact("FACT_REMOTE_ALPHA", "Shared deploy guide lives in docs/guide", topic="deploy"),
        auto_commit=False,
    )
    for index, repo_name in enumerate(("beta", "gamma"), start=1):
        _, other_repo = _init_project(tmp_path, repo_name)
        add_fact(
            other_repo,
            _make_fact(
                f"FACT_REMOTE_{index}",
                "Shared deploy guide lives in docs/guide",
                topic="deploy",
            ),
            auto_commit=False,
        )

    preview = build_cross_project_promotion_report(
        umx_home,
        default_config(),
        candidate_key="shared deploy guide lives in docs/guide",
    )
    branch = preview["proposal"]["branch"]

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "propose",
            "--cwd",
            str(project_dir),
            "--cross-project",
            "--proposal-key",
            "shared deploy guide lives in docs/guide",
            "--push",
        ],
    )

    assert result.exit_code != 0
    assert "user repo has no origin remote configured" in result.output
    assert _git_output(user_repo, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"
    assert _git_output(user_repo, "status", "--short") == ""
    assert (
        subprocess.run(
            ["git", "-C", str(user_repo), "rev-parse", "--verify", f"refs/heads/{branch}"],
            capture_output=True,
            text=True,
            check=False,
        ).returncode
        == 0
    )


def test_propose_cross_project_push_requires_remote_main_baseline(
    umx_home: Path,
    tmp_path: Path,
    user_repo: Path,
) -> None:
    _git_commit_all(user_repo, "test: clean user baseline")
    remote = tmp_path / "user-missing-main.git"
    _connect_origin(user_repo, remote)

    project_dir, repo = _init_project(tmp_path, "alpha")
    add_fact(
        repo,
        _make_fact("FACT_REMOTE_MAIN_ALPHA", "Shared deploy map lives in docs/map", topic="deploy"),
        auto_commit=False,
    )
    for index, repo_name in enumerate(("beta", "gamma"), start=1):
        _, other_repo = _init_project(tmp_path, repo_name)
        add_fact(
            other_repo,
            _make_fact(
                f"FACT_REMOTE_MAIN_{index}",
                "Shared deploy map lives in docs/map",
                topic="deploy",
            ),
            auto_commit=False,
        )

    preview = build_cross_project_promotion_report(
        umx_home,
        default_config(),
        candidate_key="shared deploy map lives in docs/map",
    )
    branch = preview["proposal"]["branch"]

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "propose",
            "--cwd",
            str(project_dir),
            "--cross-project",
            "--proposal-key",
            "shared deploy map lives in docs/map",
            "--push",
        ],
    )

    assert result.exit_code != 0
    assert "user repo origin/main is missing; push main first" in result.output
    assert _git_output(user_repo, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"
    assert _git_output(user_repo, "status", "--short") == ""
    assert (
        subprocess.run(
            ["git", "-C", str(user_repo), "rev-parse", "--verify", f"refs/heads/{branch}"],
            capture_output=True,
            text=True,
            check=False,
        ).returncode
        == 0
    )
    assert (
        subprocess.run(
            ["git", "--git-dir", str(remote), "rev-parse", "--verify", f"refs/heads/{branch}"],
            capture_output=True,
            text=True,
            check=False,
        ).returncode
        != 0
    )


def test_propose_cross_project_cleans_up_branch_when_commit_fails(
    umx_home: Path,
    tmp_path: Path,
    user_repo: Path,
) -> None:
    _git_commit_all(user_repo, "test: clean user baseline")
    project_dir, repo = _init_project(tmp_path, "alpha")
    add_fact(
        repo,
        _make_fact("FACT_FAIL_ALPHA", "Shared deploy matrix lives in docs/matrix", topic="deploy"),
        auto_commit=False,
    )
    for index, repo_name in enumerate(("beta", "gamma"), start=1):
        _, other_repo = _init_project(tmp_path, repo_name)
        add_fact(
            other_repo,
            _make_fact(
                f"FACT_FAIL_{index}",
                "Shared deploy matrix lives in docs/matrix",
                topic="deploy",
            ),
            auto_commit=False,
        )

    preview = build_cross_project_promotion_report(
        umx_home,
        default_config(),
        candidate_key="shared deploy matrix lives in docs/matrix",
    )
    branch = preview["proposal"]["branch"]

    runner = CliRunner()
    with patch(
        "umx.git_ops.git_add_and_commit",
        return_value=GitCommitResult.failed_result(stderr="boom"),
    ):
        result = runner.invoke(
            main,
            [
                "propose",
                "--cwd",
                str(project_dir),
                "--cross-project",
                "--proposal-key",
                "shared deploy matrix lives in docs/matrix",
            ],
        )

    assert result.exit_code != 0
    assert "proposal commit failed: boom" in result.output
    assert _git_output(user_repo, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"
    assert _git_output(user_repo, "status", "--short") == ""
    assert (
        subprocess.run(
            ["git", "-C", str(user_repo), "rev-parse", "--verify", f"refs/heads/{branch}"],
            capture_output=True,
            text=True,
            check=False,
        ).returncode
        != 0
    )


def test_propose_cross_project_preserves_local_branch_when_push_safety_blocks(
    umx_home: Path,
    tmp_path: Path,
    user_repo: Path,
) -> None:
    _git_commit_all(user_repo, "test: clean user baseline")
    remote = tmp_path / "user-push-safety.git"
    _connect_origin(user_repo, remote)
    subprocess.run(
        ["git", "-C", str(user_repo), "push", "--set-upstream", "origin", "main"],
        capture_output=True,
        text=True,
        check=True,
    )

    project_dir, repo = _init_project(tmp_path, "alpha")
    add_fact(
        repo,
        _make_fact(
            "FACT_PUSH_BLOCK_ALPHA",
            f"Shared aws key {AWS_ACCESS_KEY_ID} lives in docs/secrets",
            topic="deploy",
        ),
        auto_commit=False,
    )
    for index, repo_name in enumerate(("beta", "gamma"), start=1):
        _, other_repo = _init_project(tmp_path, repo_name)
        add_fact(
            other_repo,
            _make_fact(
                f"FACT_PUSH_BLOCK_{index}",
                f"Shared aws key {AWS_ACCESS_KEY_ID} lives in docs/secrets",
                topic="deploy",
            ),
            auto_commit=False,
        )

    preview = build_cross_project_promotion_report(
        umx_home,
        default_config(),
        candidate_key=f"shared aws key {AWS_ACCESS_KEY_ID.lower()} lives in docs/secrets",
    )
    branch = preview["proposal"]["branch"]

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "propose",
            "--cwd",
            str(project_dir),
            "--cross-project",
            "--proposal-key",
            "shared aws key akia1234567890abcdef lives in docs/secrets",
            "--push",
        ],
    )

    assert result.exit_code != 0
    assert "push safety blocked" in result.output
    assert _git_output(user_repo, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"
    assert _git_output(user_repo, "status", "--short") == ""
    assert (
        subprocess.run(
            ["git", "-C", str(user_repo), "rev-parse", "--verify", f"refs/heads/{branch}"],
            capture_output=True,
            text=True,
            check=False,
        ).returncode
        == 0
    )
    assert (
        subprocess.run(
            ["git", "--git-dir", str(remote), "rev-parse", "--verify", f"refs/heads/{branch}"],
            capture_output=True,
            text=True,
            check=False,
        ).returncode
        != 0
    )


def test_propose_cross_project_cleans_up_branch_when_append_fails(
    umx_home: Path,
    tmp_path: Path,
    user_repo: Path,
) -> None:
    _git_commit_all(user_repo, "test: clean user baseline")
    project_dir, repo = _init_project(tmp_path, "alpha")
    add_fact(
        repo,
        _make_fact("FACT_APPEND_ALPHA", "Shared deploy map lives in docs/map", topic="deploy"),
        auto_commit=False,
    )
    for index, repo_name in enumerate(("beta", "gamma"), start=1):
        _, other_repo = _init_project(tmp_path, repo_name)
        add_fact(
            other_repo,
            _make_fact(
                f"FACT_APPEND_{index}",
                "Shared deploy map lives in docs/map",
                topic="deploy",
            ),
            auto_commit=False,
        )

    preview = build_cross_project_promotion_report(
        umx_home,
        default_config(),
        candidate_key="shared deploy map lives in docs/map",
    )
    branch = preview["proposal"]["branch"]

    with patch("umx.memory._save_cache", side_effect=RuntimeError("cache write failed")):
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "propose",
                "--cwd",
                str(project_dir),
                "--cross-project",
                "--proposal-key",
                "shared deploy map lives in docs/map",
            ],
        )

    assert result.exit_code != 0
    assert "cache write failed" in result.output
    assert _git_output(user_repo, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"
    assert _git_output(user_repo, "status", "--short") == ""
    assert (
        subprocess.run(
            ["git", "-C", str(user_repo), "rev-parse", "--verify", f"refs/heads/{branch}"],
            capture_output=True,
            text=True,
            check=False,
        ).returncode
        != 0
    )


def test_propose_cross_project_preserves_local_branch_when_push_fails(
    umx_home: Path,
    tmp_path: Path,
    user_repo: Path,
) -> None:
    _git_commit_all(user_repo, "test: clean user baseline")
    remote = tmp_path / "user-push-fail.git"
    _connect_origin(user_repo, remote)
    subprocess.run(
        ["git", "-C", str(user_repo), "push", "--set-upstream", "origin", "main"],
        capture_output=True,
        text=True,
        check=True,
    )

    project_dir, repo = _init_project(tmp_path, "alpha")
    add_fact(
        repo,
        _make_fact("FACT_PUSH_FAIL_ALPHA", "Shared deploy matrix lives in docs/matrix", topic="deploy"),
        auto_commit=False,
    )
    for index, repo_name in enumerate(("beta", "gamma"), start=1):
        _, other_repo = _init_project(tmp_path, repo_name)
        add_fact(
            other_repo,
            _make_fact(
                f"FACT_PUSH_FAIL_{index}",
                "Shared deploy matrix lives in docs/matrix",
                topic="deploy",
            ),
            auto_commit=False,
        )

    preview = build_cross_project_promotion_report(
        umx_home,
        default_config(),
        candidate_key="shared deploy matrix lives in docs/matrix",
    )
    branch = preview["proposal"]["branch"]

    runner = CliRunner()
    with patch("umx.git_ops.git_push", return_value=False):
        result = runner.invoke(
            main,
            [
                "propose",
                "--cwd",
                str(project_dir),
                "--cross-project",
                "--proposal-key",
                "shared deploy matrix lives in docs/matrix",
                "--push",
            ],
        )

    assert result.exit_code != 0
    assert f"failed to push proposal branch: {branch}" in result.output
    assert _git_output(user_repo, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"
    assert _git_output(user_repo, "status", "--short") == ""
    assert (
        subprocess.run(
            ["git", "-C", str(user_repo), "rev-parse", "--verify", f"refs/heads/{branch}"],
            capture_output=True,
            text=True,
            check=False,
        ).returncode
        == 0
    )
    assert (
        subprocess.run(
            ["git", "--git-dir", str(remote), "rev-parse", "--verify", f"refs/heads/{branch}"],
            capture_output=True,
            text=True,
            check=False,
        ).returncode
        != 0
    )


def test_propose_cross_project_push_requires_main_in_sync_with_origin(
    umx_home: Path,
    tmp_path: Path,
    user_repo: Path,
) -> None:
    _git_commit_all(user_repo, "test: clean user baseline")
    remote = tmp_path / "user-main-ahead.git"
    _connect_origin(user_repo, remote)
    subprocess.run(
        ["git", "-C", str(user_repo), "push", "--set-upstream", "origin", "main"],
        capture_output=True,
        text=True,
        check=True,
    )

    local_only = user_repo / "LOCAL_ONLY.txt"
    local_only.write_text("not yet pushed\n")
    _git_commit_all(user_repo, "test: local main ahead")

    project_dir, repo = _init_project(tmp_path, "alpha")
    add_fact(
        repo,
        _make_fact("FACT_SYNC_ALPHA", "Shared deploy board lives in docs/board", topic="deploy"),
        auto_commit=False,
    )
    for index, repo_name in enumerate(("beta", "gamma"), start=1):
        _, other_repo = _init_project(tmp_path, repo_name)
        add_fact(
            other_repo,
            _make_fact(
                f"FACT_SYNC_{index}",
                "Shared deploy board lives in docs/board",
                topic="deploy",
            ),
            auto_commit=False,
        )

    preview = build_cross_project_promotion_report(
        umx_home,
        default_config(),
        candidate_key="shared deploy board lives in docs/board",
    )
    branch = preview["proposal"]["branch"]

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "propose",
            "--cwd",
            str(project_dir),
            "--cross-project",
            "--proposal-key",
            "shared deploy board lives in docs/board",
            "--push",
        ],
    )

    assert result.exit_code != 0
    assert "user repo main is not in sync with origin/main; push or pull main first" in result.output
    assert _git_output(user_repo, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"
    assert _git_output(user_repo, "status", "--short") == ""
    assert (
        subprocess.run(
            ["git", "-C", str(user_repo), "rev-parse", "--verify", f"refs/heads/{branch}"],
            capture_output=True,
            text=True,
            check=False,
        ).returncode
        == 0
    )
    assert (
        subprocess.run(
            ["git", "--git-dir", str(remote), "rev-parse", "--verify", f"refs/heads/{branch}"],
            capture_output=True,
            text=True,
            check=False,
        ).returncode
        != 0
    )


def test_propose_cross_project_push_ignores_shadowing_local_origin_main_branch(
    umx_home: Path,
    tmp_path: Path,
    user_repo: Path,
) -> None:
    _git_commit_all(user_repo, "test: clean user baseline")
    remote = tmp_path / "user-shadow-origin-main.git"
    _connect_origin(user_repo, remote)
    subprocess.run(
        ["git", "-C", str(user_repo), "push", "--set-upstream", "origin", "main"],
        capture_output=True,
        text=True,
        check=True,
    )

    subprocess.run(
        ["git", "-C", str(user_repo), "checkout", "-b", "origin/main"],
        capture_output=True,
        text=True,
        check=True,
    )
    try:
        shadow_file = user_repo / "SHADOW.txt"
        shadow_file.write_text("shadow branch\n")
        _git_commit_all(user_repo, "test: shadow origin/main")
    finally:
        subprocess.run(
            ["git", "-C", str(user_repo), "checkout", "main"],
            capture_output=True,
            text=True,
            check=True,
        )

    project_dir, repo = _init_project(tmp_path, "alpha")
    add_fact(
        repo,
        _make_fact("FACT_SHADOW_ALPHA", "Shared deploy checklist lives in docs/runbooks", topic="deploy"),
        auto_commit=False,
    )
    for index, repo_name in enumerate(("beta", "gamma"), start=1):
        _, other_repo = _init_project(tmp_path, repo_name)
        add_fact(
            other_repo,
            _make_fact(
                f"FACT_SHADOW_{index}",
                "Shared deploy checklist lives in docs/runbooks",
                topic="deploy",
            ),
            auto_commit=False,
        )

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "propose",
            "--cwd",
            str(project_dir),
            "--cross-project",
            "--proposal-key",
            "shared deploy checklist lives in docs/runbooks",
            "--push",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["mode"] == "cross_project_proposal_pushed"
    assert _git_output(user_repo, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"
    assert _git_output(user_repo, "status", "--short") == ""
    assert (
        subprocess.run(
            ["git", "--git-dir", str(remote), "rev-parse", "--verify", f"refs/heads/{payload['branch']}"],
            capture_output=True,
            text=True,
            check=False,
        ).returncode
        == 0
    )


def test_propose_cross_project_fails_if_user_repo_not_on_main(
    umx_home: Path,
    tmp_path: Path,
    user_repo: Path,
) -> None:
    _git_commit_all(user_repo, "test: clean user baseline")
    project_dir, repo = _init_project(tmp_path, "alpha")
    add_fact(
        repo,
        _make_fact("FACT_BRANCH_ALPHA", "Shared deploy board lives in docs/board", topic="deploy"),
        auto_commit=False,
    )
    for index, repo_name in enumerate(("beta", "gamma"), start=1):
        _, other_repo = _init_project(tmp_path, repo_name)
        add_fact(
            other_repo,
            _make_fact(
                f"FACT_BRANCH_{index}",
                "Shared deploy board lives in docs/board",
                topic="deploy",
            ),
            auto_commit=False,
        )

    subprocess.run(
        ["git", "-C", str(user_repo), "checkout", "-b", "feature/test-proposal"],
        capture_output=True,
        text=True,
        check=True,
    )
    try:
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "propose",
                "--cwd",
                str(project_dir),
                "--cross-project",
                "--proposal-key",
                "shared deploy board lives in docs/board",
            ],
        )
    finally:
        subprocess.run(
            ["git", "-C", str(user_repo), "checkout", "main"],
            capture_output=True,
            text=True,
            check=True,
        )

    assert result.exit_code != 0
    assert "user repo must be on main; current branch is feature/test-proposal" in result.output


def test_propose_cross_project_fails_if_user_repo_has_pending_changes(
    umx_home: Path,
    tmp_path: Path,
    user_repo: Path,
) -> None:
    _git_commit_all(user_repo, "test: clean user baseline")
    project_dir, repo = _init_project(tmp_path, "alpha")
    add_fact(
        repo,
        _make_fact("FACT_DIRTY_ALPHA", "Shared deploy policy lives in docs/policy", topic="deploy"),
        auto_commit=False,
    )
    for index, repo_name in enumerate(("beta", "gamma"), start=1):
        _, other_repo = _init_project(tmp_path, repo_name)
        add_fact(
            other_repo,
            _make_fact(
                f"FACT_DIRTY_{index}",
                "Shared deploy policy lives in docs/policy",
                topic="deploy",
            ),
            auto_commit=False,
        )

    pending = user_repo / "PENDING.txt"
    pending.write_text("dirty\n")

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "propose",
            "--cwd",
            str(project_dir),
            "--cross-project",
            "--proposal-key",
            "shared deploy policy lives in docs/policy",
        ],
    )

    assert result.exit_code != 0
    assert "user repo has pending changes: PENDING.txt" in result.output


def test_propose_cross_project_requires_cross_project_and_proposal_key(
    umx_home: Path,
    tmp_path: Path,
) -> None:
    project_dir, _ = _init_project(tmp_path, "alpha")

    runner = CliRunner()
    missing_flag = runner.invoke(
        main,
        ["propose", "--cwd", str(project_dir), "--proposal-key", "shared deploy calendar"],
    )
    missing_key = runner.invoke(
        main,
        ["propose", "--cwd", str(project_dir), "--cross-project"],
    )

    assert missing_flag.exit_code != 0
    assert "--cross-project is required for propose" in missing_flag.output
    assert missing_key.exit_code != 0
    assert "--proposal-key is required for propose" in missing_key.output
