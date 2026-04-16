"""Tests for umx.github_ops — mocked, no real GitHub calls."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from umx.github_ops import (
    GitHubRepoRef,
    GitHubError,
    add_pr_labels,
    close_pr,
    comment_pr,
    create_pr,
    create_repo,
    deploy_workflows,
    ensure_governance_labels,
    ensure_repo,
    gh_available,
    merge_pr,
    push_branch,
    push_main,
    repo_exists,
    resolve_repo_ref,
    set_remote,
)
from umx.scope import user_memory_dir


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


class TestGhAvailable:
    @patch("umx.github_ops._run_gh")
    def test_available(self, mock_run: MagicMock) -> None:
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        assert gh_available() is True

    @patch("umx.github_ops._run_gh")
    def test_not_available(self, mock_run: MagicMock) -> None:
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="not logged in")
        assert gh_available() is False

    @patch("umx.github_ops._run_gh", side_effect=GitHubError("not installed"))
    def test_not_installed(self, mock_run: MagicMock) -> None:
        assert gh_available() is False


class TestRepoExists:
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
        pr_num = create_pr("org", "repo", "dream/l1/test", "Title", "Body")
        assert pr_num == 42

    @patch("umx.github_ops._run_gh")
    def test_failure(self, mock_run: MagicMock) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="error creating PR"
        )
        pr_num = create_pr("org", "repo", "dream/l1/test", "Title", "Body")
        assert pr_num is None

    @patch("umx.github_ops._run_gh")
    def test_with_labels(self, mock_run: MagicMock) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout="https://github.com/org/repo/pull/7\n",
            stderr="",
        )
        pr_num = create_pr("org", "repo", "b", "T", "B", labels=["type:extraction"])
        assert pr_num == 7

    @patch("umx.github_ops._run_gh")
    def test_label_failure_does_not_retry_without_labels(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = [
            subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
            subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="label not found"),
        ]

        pr_num = create_pr("org", "repo", "b", "T", "B", labels=["human-review"])

        assert pr_num is None
        assert mock_run.call_count == 2


class TestEnsureGovernanceLabels:
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


class TestClosePR:
    @patch("umx.github_ops._run_gh")
    def test_close(self, mock_run: MagicMock) -> None:
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        assert close_pr("org", "repo", 1) is True


class TestDeployWorkflows:
    def test_writes_files(self, git_repo: Path) -> None:
        paths = deploy_workflows(git_repo)
        assert len(paths) == 2
        for p in paths:
            assert p.exists()
            assert ".github/workflows" in str(p)
