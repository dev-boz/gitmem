"""Tests for umx.github_ops — mocked, no real GitHub calls."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import umx.github_ops as github_ops
from umx.dream.pr_render import (
    FactDeltaBlock,
    FactDeltaEntry,
    GovernancePRBodyError,
    render_governance_pr_body,
)
from umx.github_ops import (
    GitHubRepoRef,
    GitHubError,
    add_pr_labels,
    apply_governance_protection,
    close_pr,
    comment_pr,
    create_pr,
    create_repo,
    deploy_workflows,
    ensure_governance_labels,
    ensure_repo,
    gh_available,
    load_governance_protection_reference,
    merge_pr,
    plan_governance_protection,
    push_branch,
    push_main,
    read_pr_labels,
    reconcile_pr_labels,
    repo_exists,
    resolve_repo_ref,
    set_remote,
)
from umx.governance import GovernancePRConflictError
from umx.scope import user_memory_dir


def _governance_pr_body() -> str:
    return render_governance_pr_body(
        heading="Dream L1 Extraction",
        summary_lines=["- governance body"],
        fact_delta=FactDeltaBlock(
            added=(FactDeltaEntry(topic="devenv", path="facts/topics/devenv.md", summary="postgres"),),
        ),
    )


def _governance_pr_body_with_fact_id(fact_id: str, *, summary: str = "postgres") -> str:
    return render_governance_pr_body(
        heading="Dream L1 Extraction",
        summary_lines=["- governance body"],
        fact_delta=FactDeltaBlock(
            added=(
                FactDeltaEntry(
                    fact_id=fact_id,
                    topic="devenv",
                    path="facts/topics/devenv.md",
                    summary=summary,
                ),
            ),
        ),
    )


def _gh_result(returncode: int, *, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=["gh"], returncode=returncode, stdout=stdout, stderr=stderr)


def _ruleset_summary_payload(
    ruleset_id: int,
    *,
    name: str,
    target: str = "branch",
    url: str | None = None,
) -> dict[str, object]:
    return {
        "id": ruleset_id,
        "name": name,
        "target": target,
        "source_type": "Repository",
        "_links": {
            "html": {
                "href": url or f"https://github.com/org/repo/rules/{ruleset_id}",
            }
        },
    }


def _ruleset_detail_payload(
    payload: dict[str, object],
    *,
    ruleset_id: int,
    url: str | None = None,
) -> dict[str, object]:
    response = dict(payload)
    response["id"] = ruleset_id
    response["_links"] = {
        "html": {
            "href": url or f"https://github.com/org/repo/rules/{ruleset_id}",
        }
    }
    return response


@pytest.fixture()
def git_repo(tmp_path: Path) -> Path:
    """Create a minimal git repo for testing."""
    subprocess.run(["git", "init", str(tmp_path)], capture_output=True, check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "--allow-empty", "-m", "init"],
        capture_output=True,
        check=True,
    )
    return tmp_path


class TestRunGhRetry:
    def test_safe_command_retries_transient_then_succeeds(self) -> None:
        results = [
            _gh_result(1, stderr="502 bad gateway"),
            _gh_result(0, stdout='{"name":"repo"}'),
        ]
        calls: list[tuple[str, ...]] = []
        sleeps: list[float] = []

        def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append(tuple(command))
            return results.pop(0)

        result = github_ops._run_gh(
            "repo",
            "view",
            "org/repo",
            "--json",
            "name",
            check=False,
            _policy=github_ops.GhRetryPolicy(),
            _runner=runner,
            _sleep=sleeps.append,
        )

        assert result.returncode == 0
        assert len(calls) == 2
        assert sleeps == [0.5]

    def test_safe_command_retries_rate_limit_then_succeeds(self) -> None:
        results = [
            _gh_result(1, stderr="secondary rate limit exceeded. Retry-After: 7"),
            _gh_result(0, stdout='{"labels":[]}'),
        ]
        sleeps: list[float] = []

        def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            return results.pop(0)

        result = github_ops._run_gh(
            "pr",
            "view",
            "1",
            "--repo",
            "org/repo",
            "--json",
            "labels",
            check=False,
            _policy=github_ops.GhRetryPolicy(),
            _runner=runner,
            _sleep=sleeps.append,
        )

        assert result.returncode == 0
        assert sleeps == [7.0]

    def test_safe_command_retries_eof_transport_failure(self) -> None:
        results = [
            _gh_result(1, stderr='Post "https://api.github.com/graphql": EOF'),
            _gh_result(0, stdout='{"name":"repo"}'),
        ]
        sleeps: list[float] = []

        def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            return results.pop(0)

        result = github_ops._run_gh(
            "repo",
            "view",
            "org/repo",
            "--json",
            "name",
            check=False,
            _policy=github_ops.GhRetryPolicy(),
            _runner=runner,
            _sleep=sleeps.append,
        )

        assert result.returncode == 0
        assert sleeps == [0.5]

    def test_safe_command_retries_open_pr_listing(self) -> None:
        results = [
            _gh_result(1, stderr="502 bad gateway"),
            _gh_result(0, stdout="[]"),
        ]
        sleeps: list[float] = []

        def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            return results.pop(0)

        result = github_ops._run_gh(
            "pr",
            "list",
            "--repo",
            "org/repo",
            "--state",
            "open",
            "--base",
            "main",
            "--json",
            "number,title,url,headRefName,body,labels",
            check=False,
            _policy=github_ops.GhRetryPolicy(),
            _runner=runner,
            _sleep=sleeps.append,
        )

        assert result.returncode == 0
        assert sleeps == [0.5]

    def test_permanent_failure_does_not_retry(self) -> None:
        calls: list[tuple[str, ...]] = []
        sleeps: list[float] = []

        def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append(tuple(command))
            return _gh_result(1, stderr="404 not found")

        result = github_ops._run_gh(
            "repo",
            "view",
            "org/repo",
            "--json",
            "name",
            check=False,
            _policy=github_ops.GhRetryPolicy(),
            _runner=runner,
            _sleep=sleeps.append,
        )

        assert result.returncode == 1
        assert len(calls) == 1
        assert sleeps == []

    def test_unsafe_command_does_not_retry_on_transient_failure(self) -> None:
        calls: list[tuple[str, ...]] = []

        def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append(tuple(command))
            return _gh_result(1, stderr="503 service unavailable")

        result = github_ops._run_gh(
            "pr",
            "create",
            "--repo",
            "org/repo",
            "--head",
            "feature",
            "--base",
            "main",
            "--title",
            "T",
            "--body",
            "B",
            check=False,
            _policy=github_ops.GhRetryPolicy(),
            _runner=runner,
            _sleep=lambda _: None,
        )

        assert result.returncode == 1
        assert len(calls) == 1

    def test_exhausted_retries_raise_error_with_next_steps(self) -> None:
        results = [
            _gh_result(1, stderr="502 bad gateway"),
            _gh_result(1, stderr="503 service unavailable"),
            _gh_result(1, stderr="504 gateway timeout"),
        ]
        sleeps: list[float] = []

        def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            return results.pop(0)

        with pytest.raises(GitHubError, match="Next steps:") as exc:
            github_ops._run_gh(
                "repo",
                "view",
                "org/repo",
                "--json",
                "name",
                _policy=github_ops.GhRetryPolicy(),
                _runner=runner,
                _sleep=sleeps.append,
            )

        assert "after 3 attempts" in str(exc.value)
        assert sleeps == [0.5, 1.0]

    def test_exhausted_retries_return_final_failure_when_check_false(self) -> None:
        results = [
            _gh_result(1, stderr="502 bad gateway"),
            _gh_result(1, stderr="503 service unavailable"),
            _gh_result(1, stderr="504 gateway timeout"),
        ]
        sleeps: list[float] = []

        def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            return results.pop(0)

        result = github_ops._run_gh(
            "repo",
            "view",
            "org/repo",
            "--json",
            "name",
            check=False,
            _policy=github_ops.GhRetryPolicy(),
            _runner=runner,
            _sleep=sleeps.append,
        )

        assert result.returncode == 1
        assert result.stderr == "504 gateway timeout"
        assert sleeps == [0.5, 1.0]

    def test_missing_gh_raises_immediately(self) -> None:
        def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            raise FileNotFoundError

        with pytest.raises(GitHubError, match="gh CLI is not installed"):
            github_ops._run_gh(
                "repo",
                "view",
                "org/repo",
                "--json",
                "name",
                _policy=github_ops.GhRetryPolicy(),
                _runner=runner,
                _sleep=lambda _: None,
            )

    def test_run_gh_uses_env_retry_policy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        results = [
            _gh_result(1, stderr="502 bad gateway"),
            _gh_result(1, stderr="503 service unavailable"),
            _gh_result(0, stdout='{"name":"repo"}'),
        ]
        calls: list[tuple[str, ...]] = []
        sleeps: list[float] = []

        def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append(tuple(command))
            return results.pop(0)

        monkeypatch.setenv("UMX_GH_RETRY_MAX_ATTEMPTS", "2")
        monkeypatch.setattr(github_ops.subprocess, "run", runner)
        monkeypatch.setattr(github_ops.time, "sleep", sleeps.append)

        result = github_ops._run_gh("repo", "view", "org/repo", "--json", "name", check=False)

        assert result.returncode == 1
        assert len(calls) == 2
        assert sleeps == [0.5]


class TestGhAvailable:
    @patch("umx.github_ops._run_gh")
    def test_available(self, mock_run: MagicMock) -> None:
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        assert gh_available() is True

    @patch("umx.github_ops._run_gh")
    def test_not_available(self, mock_run: MagicMock) -> None:
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="not logged in")
        assert gh_available() is False

    @patch(
        "umx.github_ops._run_gh",
        side_effect=GitHubError("gh CLI is not installed; install from https://cli.github.com/"),
    )
    def test_not_installed(self, mock_run: MagicMock) -> None:
        assert gh_available() is False

    @patch("umx.github_ops._run_gh")
    def test_retryable_auth_failure_propagates(self, mock_run: MagicMock) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout="",
            stderr="503 service unavailable",
        )

        with pytest.raises(GitHubError, match="Next steps:"):
            gh_available()

    @patch("umx.github_ops._run_gh")
    def test_unexpected_auth_failure_raises(self, mock_run: MagicMock) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout="",
            stderr="permission denied reading hosts.yml",
        )

        with pytest.raises(GitHubError, match="permission denied reading hosts.yml"):
            gh_available()


class TestRepoExists:
    @patch("umx.github_ops.time.sleep")
    @patch("umx.github_ops.subprocess.run")
    def test_retries_transient_repo_view(self, mock_run: MagicMock, mock_sleep: MagicMock) -> None:
        mock_run.side_effect = [
            _gh_result(1, stderr="502 bad gateway"),
            _gh_result(0, stdout='{"name":"test"}'),
        ]

        assert repo_exists("org", "test") is True

        assert mock_run.call_count == 2
        mock_sleep.assert_called_once_with(0.5)

    @patch("umx.github_ops._run_gh")
    def test_exists(self, mock_run: MagicMock) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout='{"name":"test"}', stderr=""
        )
        assert repo_exists("org", "test") is True

    @patch("umx.github_ops._run_gh")
    def test_not_exists(self, mock_run: MagicMock) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="not found"
        )
        assert repo_exists("org", "test") is False

    @patch("umx.github_ops._run_gh")
    def test_retryable_probe_failure_raises(self, mock_run: MagicMock) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout="",
            stderr="502 bad gateway",
        )

        with pytest.raises(GitHubError, match="Next steps:"):
            repo_exists("org", "test")


class TestCreateRepo:
    @patch("umx.github_ops._run_gh")
    def test_create_private(self, mock_run: MagicMock) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="https://github.com/org/test\n", stderr=""
        )
        url = create_repo("org", "test", private=True)
        assert url == "https://github.com/org/test.git"
        call_args = mock_run.call_args[0]
        assert "--private" in call_args

    @patch("umx.github_ops._run_gh")
    def test_create_public(self, mock_run: MagicMock) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="https://github.com/org/test\n", stderr=""
        )
        url = create_repo("org", "test", private=False)
        assert url == "https://github.com/org/test.git"
        call_args = mock_run.call_args[0]
        assert "--public" in call_args


class TestEnsureRepo:
    @patch("umx.github_ops.create_repo")
    @patch("umx.github_ops.repo_exists", return_value=True)
    def test_already_exists(self, mock_exists: MagicMock, mock_create: MagicMock) -> None:
        url = ensure_repo("org", "test")
        assert url == "https://github.com/org/test.git"
        mock_create.assert_not_called()

    @patch("umx.github_ops.create_repo", return_value="https://github.com/org/new.git")
    @patch("umx.github_ops.repo_exists", return_value=False)
    def test_creates_new(self, mock_exists: MagicMock, mock_create: MagicMock) -> None:
        url = ensure_repo("org", "new")
        assert url == "https://github.com/org/new.git"
        mock_create.assert_called_once()


class TestGovernanceProtectionReference:
    def test_loads_reference_contract(self) -> None:
        reference = load_governance_protection_reference()

        assert reference.default_branch == "main"
        assert reference.require_pull_request is True
        assert reference.required_status_checks == (
            "Governance Approval Gate / approval-gate",
        )
        assert reference.governance_merge_label == "state: approved"
        assert reference.override_cli_flag == "--force"
        assert reference.override_reason_flag == "--force-reason"
        assert reference.override_audit_comment_marker == "<!-- umx:approval-override -->"


class TestGovernanceProtectionPlan:
    def test_remote_plan_is_deferred_while_main_writes_are_direct(self) -> None:
        plan = plan_governance_protection("remote")

        assert plan.eligible is False
        assert "direct pushes to main" in str(plan.deferred_reason)
        assert plan.target_branch == "main"
        assert plan.require_pull_request is True
        assert plan.required_status_checks == ("Governance Approval Gate / approval-gate",)
        assert plan.governance_merge_label == "state: approved"
        assert plan.payload["name"] == plan.ruleset_name
        assert plan.payload["conditions"] == {
            "ref_name": {"include": ["refs/heads/main"], "exclude": []}
        }

    def test_non_remote_plan_stays_deferred_even_without_direct_main_writes(self) -> None:
        plan = plan_governance_protection("hybrid", direct_main_writes=False)

        assert plan.eligible is False
        assert "only supported in remote mode" in str(plan.deferred_reason)

    def test_eligible_plan_builds_expected_ruleset_payload(self) -> None:
        plan = plan_governance_protection("remote", direct_main_writes=False)

        assert plan.eligible is True
        assert plan.deferred_reason is None
        assert plan.payload["enforcement"] == "active"
        assert plan.payload["bypass_actors"] == []
        assert plan.payload["rules"] == [
            {
                "type": "pull_request",
                "parameters": {
                    "dismiss_stale_reviews_on_push": False,
                    "require_code_owner_review": False,
                    "require_last_push_approval": False,
                    "required_approving_review_count": 0,
                    "required_review_thread_resolution": False,
                },
            },
            {
                "type": "required_status_checks",
                "parameters": {
                    "do_not_enforce_on_create": False,
                    "required_status_checks": [
                        {
                            "context": "Governance Approval Gate / approval-gate",
                            "integration_id": None,
                        }
                    ],
                    "strict_required_status_checks_policy": False,
                },
            },
        ]


class TestApplyGovernanceProtection:
    @patch("umx.github_ops._run_gh")
    def test_rejects_deferred_plan(self, mock_run: MagicMock) -> None:
        plan = plan_governance_protection("remote")

        with pytest.raises(GitHubError, match="cannot be enabled"):
            apply_governance_protection("org", "repo", plan)

        mock_run.assert_not_called()

    @patch("umx.github_ops._run_gh")
    def test_creates_ruleset_when_missing(self, mock_run: MagicMock) -> None:
        plan = plan_governance_protection("remote", direct_main_writes=False)
        mock_run.side_effect = [
            _gh_result(0, stdout="[]"),
            _gh_result(0, stdout=json.dumps(_ruleset_detail_payload(plan.payload, ruleset_id=7))),
        ]

        result = apply_governance_protection("org", "repo", plan)

        assert result.action == "created"
        assert result.ruleset_id == 7
        assert mock_run.call_args_list[0][0][:3] == ("api", "--method", "GET")
        assert mock_run.call_args_list[1][0][:3] == ("api", "--method", "POST")
        assert json.loads(mock_run.call_args_list[1][1]["input_text"]) == plan.payload

    @patch("umx.github_ops._run_gh")
    def test_noops_when_matching_ruleset_exists(self, mock_run: MagicMock) -> None:
        plan = plan_governance_protection("remote", direct_main_writes=False)
        mock_run.side_effect = [
            _gh_result(
                0,
                stdout=json.dumps([
                    _ruleset_summary_payload(42, name=plan.ruleset_name),
                ]),
            ),
            _gh_result(0, stdout=json.dumps(_ruleset_detail_payload(plan.payload, ruleset_id=42))),
        ]

        result = apply_governance_protection("org", "repo", plan)

        assert result.action == "unchanged"
        assert result.ruleset_id == 42
        assert mock_run.call_count == 2

    @patch("umx.github_ops._run_gh")
    def test_updates_ruleset_when_payload_drifts(self, mock_run: MagicMock) -> None:
        plan = plan_governance_protection("remote", direct_main_writes=False)
        drifted_payload = json.loads(json.dumps(plan.payload))
        drifted_payload["rules"] = [
            rule
            for rule in drifted_payload["rules"]
            if rule["type"] != "required_status_checks"
        ]
        mock_run.side_effect = [
            _gh_result(
                0,
                stdout=json.dumps([
                    _ruleset_summary_payload(42, name=plan.ruleset_name),
                ]),
            ),
            _gh_result(0, stdout=json.dumps(_ruleset_detail_payload(drifted_payload, ruleset_id=42))),
            _gh_result(0, stdout=json.dumps(_ruleset_detail_payload(plan.payload, ruleset_id=42))),
        ]

        result = apply_governance_protection("org", "repo", plan)

        assert result.action == "updated"
        assert result.ruleset_id == 42
        assert mock_run.call_args_list[2][0][:3] == ("api", "--method", "PATCH")
        assert json.loads(mock_run.call_args_list[2][1]["input_text"]) == plan.payload

    @patch("umx.github_ops._run_gh")
    def test_finds_managed_ruleset_beyond_first_page(self, mock_run: MagicMock) -> None:
        plan = plan_governance_protection("remote", direct_main_writes=False)
        first_page = [
            _ruleset_summary_payload(index, name=f"other-ruleset-{index}")
            for index in range(1, 101)
        ]
        mock_run.side_effect = [
            _gh_result(0, stdout=json.dumps(first_page)),
            _gh_result(
                0,
                stdout=json.dumps([
                    _ruleset_summary_payload(142, name=plan.ruleset_name),
                ]),
            ),
            _gh_result(0, stdout=json.dumps(_ruleset_detail_payload(plan.payload, ruleset_id=142))),
        ]

        result = apply_governance_protection("org", "repo", plan)

        assert result.action == "unchanged"
        assert result.ruleset_id == 142
        assert "page=1" in mock_run.call_args_list[0][0][-1]
        assert "page=2" in mock_run.call_args_list[1][0][-1]
        assert mock_run.call_count == 3

    @patch("umx.github_ops._run_gh")
    def test_fails_closed_when_duplicate_managed_rulesets_exist(self, mock_run: MagicMock) -> None:
        plan = plan_governance_protection("remote", direct_main_writes=False)
        mock_run.return_value = _gh_result(
            0,
            stdout=json.dumps(
                [
                    _ruleset_summary_payload(42, name=plan.ruleset_name),
                    _ruleset_summary_payload(43, name=plan.ruleset_name),
                ]
            ),
        )

        with pytest.raises(GitHubError, match="multiple managed governance rulesets"):
            apply_governance_protection("org", "repo", plan)

        assert mock_run.call_count == 1


class TestSetRemote:
    def test_add_remote(self, git_repo: Path) -> None:
        set_remote(git_repo, "https://github.com/org/test.git")
        result = subprocess.run(
            ["git", "-C", str(git_repo), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
        )
        assert result.stdout.strip() == "https://github.com/org/test.git"

    def test_update_remote(self, git_repo: Path) -> None:
        subprocess.run(
            ["git", "-C", str(git_repo), "remote", "add", "origin", "https://old.com/repo.git"],
            capture_output=True,
        )
        set_remote(git_repo, "https://github.com/org/new.git")
        result = subprocess.run(
            ["git", "-C", str(git_repo), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
        )
        assert result.stdout.strip() == "https://github.com/org/new.git"

    def test_no_update_if_same(self, git_repo: Path) -> None:
        url = "https://github.com/org/test.git"
        subprocess.run(
            ["git", "-C", str(git_repo), "remote", "add", "origin", url],
            capture_output=True,
        )
        set_remote(git_repo, url)
        result = subprocess.run(
            ["git", "-C", str(git_repo), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
        )
        assert result.stdout.strip() == url


class TestResolveRepoRef:
    def test_prefers_https_github_origin(self, git_repo: Path) -> None:
        url = "https://github.com/memory-org/umx-user.git"
        set_remote(git_repo, url)

        repo_ref = resolve_repo_ref(git_repo, config_org="fallback-org")

        assert repo_ref == GitHubRepoRef(
            owner="memory-org",
            name="umx-user",
            url=url,
        )

    def test_prefers_credentialed_https_github_origin(self, git_repo: Path) -> None:
        url = "https://x-access-token:token@github.com/memory-org/umx-user.git"
        set_remote(git_repo, url)

        repo_ref = resolve_repo_ref(git_repo, config_org="fallback-org")

        assert repo_ref == GitHubRepoRef(
            owner="memory-org",
            name="umx-user",
            url=url,
        )

    def test_prefers_ssh_github_origin(self, git_repo: Path) -> None:
        url = "git@github.com:memory-org/umx-user.git"
        set_remote(git_repo, url)

        repo_ref = resolve_repo_ref(git_repo, config_org="fallback-org")

        assert repo_ref == GitHubRepoRef(
            owner="memory-org",
            name="umx-user",
            url=url,
        )

    def test_user_repo_falls_back_to_umx_user_when_origin_is_not_github(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        home = tmp_path / "umxhome"
        monkeypatch.setenv("UMX_HOME", str(home))
        repo = user_memory_dir()
        repo.mkdir(parents=True)
        subprocess.run(["git", "init", str(repo)], capture_output=True, check=True)
        subprocess.run(
            ["git", "-C", str(repo), "commit", "--allow-empty", "-m", "init"],
            capture_output=True,
            check=True,
        )
        local_remote = str(tmp_path / "user-remote.git")
        set_remote(repo, local_remote)

        repo_ref = resolve_repo_ref(repo, config_org="memory-org")

        assert repo_ref == GitHubRepoRef(
            owner="memory-org",
            name="umx-user",
            url=local_remote,
        )

    def test_non_user_repo_falls_back_to_local_repo_name(self, git_repo: Path) -> None:
        local_remote = "/tmp/not-github.git"
        set_remote(git_repo, local_remote)

        repo_ref = resolve_repo_ref(git_repo, config_org="memory-org")

        assert repo_ref == GitHubRepoRef(
            owner="memory-org",
            name=git_repo.name,
            url=local_remote,
        )


class TestCreatePR:
    @patch("umx.github_ops._run_gh")
    def test_success(self, mock_run: MagicMock) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout="https://github.com/org/repo/pull/42\n",
            stderr="",
        )
        pr_num = create_pr("org", "repo", "feature/test", "Title", "Body")
        assert pr_num == 42

    @patch("umx.github_ops._run_gh")
    def test_failure(self, mock_run: MagicMock) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="error creating PR"
        )
        pr_num = create_pr("org", "repo", "feature/test", "Title", "Body")
        assert pr_num is None

    @patch("umx.github_ops._run_gh")
    def test_with_labels(self, mock_run: MagicMock) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout="https://github.com/org/repo/pull/7\n",
            stderr="",
        )
        pr_num = create_pr("org", "repo", "b", "T", _governance_pr_body(), labels=["type: extraction"])
        assert pr_num == 7

    @patch("umx.github_ops._run_gh")
    def test_label_failure_does_not_retry_without_labels(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = [
            subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
            subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="label not found"),
        ]

        pr_num = create_pr("org", "repo", "b", "T", _governance_pr_body(), labels=["human-review"])

        assert pr_num is None
        assert mock_run.call_count == 2

    @patch("umx.github_ops.repo_exists", return_value=False)
    @patch("umx.github_ops._run_gh")
    @patch("umx.github_ops.ensure_governance_labels", return_value=False)
    def test_aborts_when_governance_labels_cannot_be_ensured(
        self,
        mock_ensure: MagicMock,
        mock_run: MagicMock,
        mock_repo_exists: MagicMock,
    ) -> None:
        pr_num = create_pr("org", "repo", "b", "T", _governance_pr_body(), labels=["human-review"])

        assert pr_num is None
        mock_repo_exists.assert_called_once_with("org", "repo")
        mock_ensure.assert_called_once()
        mock_run.assert_not_called()

    @patch("umx.github_ops._run_gh")
    def test_governance_body_validation_runs_before_pr_creation(self, mock_run: MagicMock) -> None:
        with pytest.raises(GovernancePRBodyError, match="required fact-delta block"):
            create_pr("org", "repo", "dream/l1/test", "T", "plain body")
        mock_run.assert_not_called()

    @patch("umx.github_ops.ensure_governance_labels")
    @patch("umx.github_ops._run_gh")
    def test_blocks_when_open_governance_pr_overlaps_fact_ids(
        self,
        mock_run: MagicMock,
        mock_ensure: MagicMock,
    ) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps([
                {
                    "number": 41,
                    "title": "Conflicting governance PR",
                    "url": "https://github.com/org/repo/pull/41",
                    "headRefName": "dream/l1/conflict",
                    "body": _governance_pr_body_with_fact_id("FACT-CONFLICT-1", summary="existing"),
                    "labels": [{"name": "state: extraction"}],
                }
            ]),
            stderr="",
        )

        with pytest.raises(GovernancePRConflictError, match="PR #41 https://github.com/org/repo/pull/41"):
            create_pr(
                "org",
                "repo",
                "dream/l1/new-change",
                "Title",
                _governance_pr_body_with_fact_id("FACT-CONFLICT-1"),
                labels=["type: extraction"],
            )

        mock_ensure.assert_not_called()
        assert mock_run.call_count == 2
        assert mock_run.call_args_list[0][0][:3] == ("repo", "view", "org/repo")
        assert mock_run.call_args_list[1][0][:2] == ("pr", "list")


class TestEnsureGovernanceLabels:
    @patch("umx.github_ops.time.sleep")
    @patch("umx.github_ops.subprocess.run")
    def test_retries_transient_label_creation(self, mock_run: MagicMock, mock_sleep: MagicMock) -> None:
        mock_run.side_effect = [
            _gh_result(1, stderr="503 service unavailable"),
            _gh_result(0, stdout="", stderr=""),
        ]

        assert ensure_governance_labels("org", "repo", ["human-review"]) is True

        assert mock_run.call_count == 2
        mock_sleep.assert_called_once_with(0.5)

    @patch("umx.github_ops._run_gh")
    def test_creates_requested_labels(self, mock_run: MagicMock) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="",
            stderr="",
        )

        assert ensure_governance_labels("org", "repo", ["human-review"]) is True
        call_args = mock_run.call_args[0]
        assert call_args[:3] == ("label", "create", "human-review")
        assert "--force" in call_args

    @patch("umx.github_ops._run_gh")
    def test_ignores_unmanaged_labels(self, mock_run: MagicMock) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="",
            stderr="",
        )

        assert ensure_governance_labels("org", "repo", ["human-review", "needs-docs"]) is True
        assert mock_run.call_count == 1
        assert mock_run.call_args[0][2] == "human-review"


class TestMergePR:
    @patch("umx.github_ops._run_gh")
    def test_merge_success(self, mock_run: MagicMock) -> None:
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        assert merge_pr("org", "repo", 42) is True

    @patch("umx.github_ops._run_gh")
    def test_merge_failure(self, mock_run: MagicMock) -> None:
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="err")
        assert merge_pr("org", "repo", 42) is False

    @patch("umx.github_ops._run_gh")
    def test_merge_can_match_head_commit(self, mock_run: MagicMock) -> None:
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        assert merge_pr("org", "repo", 42, match_head_commit="abc123") is True

        call_args = mock_run.call_args[0]
        assert "--match-head-commit" in call_args
        assert "abc123" in call_args

    @patch("umx.github_ops._run_gh")
    def test_merge_can_use_admin_override(self, mock_run: MagicMock) -> None:
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        assert merge_pr("org", "repo", 42, admin=True) is True

        call_args = mock_run.call_args[0]
        assert "--admin" in call_args


class TestCommentPR:
    @patch("umx.github_ops._run_gh")
    def test_comment(self, mock_run: MagicMock) -> None:
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        assert comment_pr("org", "repo", 1, "LGTM") is True


class TestAddPrLabels:
    @patch("umx.github_ops._run_gh")
    def test_adds_labels_to_pr(self, mock_run: MagicMock) -> None:
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        assert add_pr_labels("org", "repo", 1, ["human-review"]) is True
        call_args = mock_run.call_args_list[-1][0]
        assert call_args[:3] == ("pr", "edit", "1")
        assert "--add-label" in call_args
        assert "human-review" in call_args

    @patch("umx.github_ops._run_gh")
    @patch("umx.github_ops.ensure_governance_labels", return_value=False)
    def test_fails_when_governance_labels_cannot_be_ensured(
        self,
        mock_ensure: MagicMock,
        mock_run: MagicMock,
    ) -> None:
        assert add_pr_labels("org", "repo", 1, ["human-review"]) is False
        mock_ensure.assert_called_once()
        mock_run.assert_not_called()


class TestReadPrLabels:
    @patch("umx.github_ops._run_gh")
    def test_reads_pr_labels(self, mock_run: MagicMock) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout='{"labels":[{"name":"state: extraction"},{"name":"human-review"}]}',
            stderr="",
        )

        assert read_pr_labels("org", "repo", 1) == ["state: extraction", "human-review"]

    @patch("umx.github_ops._run_gh")
    def test_rejects_unknown_governance_like_labels(self, mock_run: MagicMock) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout='{"labels":[{"name":"state: approvd"},{"name":"needs-docs"}]}',
            stderr="",
        )

        assert read_pr_labels("org", "repo", 1) is None


class TestReconcilePrLabels:
    @patch("umx.github_ops.ensure_governance_labels", return_value=True)
    @patch("umx.github_ops.read_pr_labels")
    @patch("umx.github_ops._run_gh")
    def test_adds_and_removes_managed_labels(
        self,
        mock_run: MagicMock,
        mock_read: MagicMock,
        mock_ensure: MagicMock,
    ) -> None:
        mock_read.return_value = [
            "state: extraction",
            "human-review",
            "type: extraction",
            "needs-docs",
        ]
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        assert reconcile_pr_labels(
            "org",
            "repo",
            1,
            ["state: reviewed", "type: extraction"],
        ) is True

        mock_ensure.assert_called_once()
        call_args = mock_run.call_args[0]
        assert call_args[:3] == ("pr", "edit", "1")
        assert "--add-label" in call_args
        assert "state: reviewed" in call_args
        assert "--remove-label" in call_args
        assert "state: extraction" in call_args
        assert "human-review" in call_args
        assert "needs-docs" not in call_args

    @patch("umx.github_ops.ensure_governance_labels", return_value=True)
    @patch("umx.github_ops.read_pr_labels", return_value=["state: reviewed", "type: extraction"])
    @patch("umx.github_ops._run_gh")
    def test_noop_when_labels_already_match(
        self,
        mock_run: MagicMock,
        mock_read: MagicMock,
        mock_ensure: MagicMock,
    ) -> None:
        assert reconcile_pr_labels(
            "org",
            "repo",
            1,
            ["state: reviewed", "type: extraction"],
        ) is True

        mock_ensure.assert_called_once()
        mock_run.assert_not_called()

    @patch("umx.github_ops.ensure_governance_labels", return_value=False)
    @patch("umx.github_ops.read_pr_labels", return_value=["state: extraction", "type: extraction"])
    @patch("umx.github_ops._run_gh")
    def test_fails_when_governance_labels_cannot_be_ensured(
        self,
        mock_run: MagicMock,
        mock_read: MagicMock,
        mock_ensure: MagicMock,
    ) -> None:
        assert reconcile_pr_labels(
            "org",
            "repo",
            1,
            ["state: reviewed", "type: extraction"],
        ) is False

        mock_read.assert_called_once()
        mock_ensure.assert_called_once()
        mock_run.assert_not_called()


class TestClosePR:
    @patch("umx.github_ops._run_gh")
    def test_close(self, mock_run: MagicMock) -> None:
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        assert close_pr("org", "repo", 1) is True


class TestDeployWorkflows:
    def test_writes_files(self, git_repo: Path) -> None:
        paths = deploy_workflows(git_repo)
        assert len(paths) == 3
        for p in paths:
            assert p.exists()
            assert ".github/workflows" in str(p)
