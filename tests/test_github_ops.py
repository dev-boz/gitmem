"""Tests for umx.github_ops — mocked, no real GitHub calls."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from umx.github_ops import (
    GitHubError,
    close_pr,
    comment_pr,
    create_pr,
    create_repo,
    deploy_workflows,
    ensure_repo,
    gh_available,
    merge_pr,
    push_branch,
    push_main,
    repo_exists,
    set_remote,
)


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


class TestMergePR:
    @patch("umx.github_ops._run_gh")
    def test_merge_success(self, mock_run: MagicMock) -> None:
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        assert merge_pr("org", "repo", 42) is True

    @patch("umx.github_ops._run_gh")
    def test_merge_failure(self, mock_run: MagicMock) -> None:
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="err")
        assert merge_pr("org", "repo", 42) is False


class TestCommentPR:
    @patch("umx.github_ops._run_gh")
    def test_comment(self, mock_run: MagicMock) -> None:
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        assert comment_pr("org", "repo", 1, "LGTM") is True


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
