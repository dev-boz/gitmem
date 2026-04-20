from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import umx.git_ops as git_ops
from umx.config import default_config
from umx.git_ops import (
    GitCommitError,
    GitCommitResult,
    GitSignedHistoryError,
    assert_signed_commit_range,
    git_add_and_commit,
    git_init,
    git_pull_rebase,
    inspect_signed_commit_range,
    is_git_repo,
    list_local_branches,
    safety_sweep,
    uncommitted_sessions,
)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    d = tmp_path / "memrepo"
    d.mkdir()
    return d


def test_git_init_creates_repo(repo: Path) -> None:
    git_init(repo)
    assert (repo / ".git").exists()
    assert (repo / ".gitignore").exists()
    content = (repo / ".gitignore").read_text()
    assert "local/" in content
    assert "__pycache__/" in content
    assert "meta/*.sqlite" in content
    assert "!meta/tombstones.jsonl" in content
    assert "!meta/processing.jsonl" in content
    assert ".umx.json" in content
    assert "meta/dream.lock" in content


def test_git_add_and_commit(repo: Path) -> None:
    git_init(repo)
    f = repo / "hello.txt"
    f.write_text("hello world\n")
    committed = git_add_and_commit(repo, paths=[f], message="add hello")
    assert committed.committed is True

    log = subprocess.run(
        ["git", "-C", str(repo), "log", "--oneline"],
        capture_output=True, text=True,
    )
    assert "add hello" in log.stdout

    # No changes → should return False
    assert git_add_and_commit(repo, paths=[f], message="noop").noop is True


def test_list_local_branches_reports_current_branch(repo: Path) -> None:
    git_init(repo)
    head = repo / "hello.txt"
    head.write_text("hello\n")
    assert git_add_and_commit(repo, paths=[head], message="seed").committed is True

    feature = repo / "feature.txt"
    subprocess.run(["git", "-C", str(repo), "checkout", "-b", "proposal/cleanup"], check=True)
    feature.write_text("cleanup\n")
    assert git_add_and_commit(repo, paths=[feature], message="feature").committed is True

    branches = {branch.name: branch for branch in list_local_branches(repo)}

    assert "main" in branches
    assert "proposal/cleanup" in branches
    assert branches["proposal/cleanup"].current is True
    assert branches["proposal/cleanup"].last_commit_ts is not None


def test_git_add_and_commit_returns_failed_result_on_commit_error(
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _completed(*args: str, returncode: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=list(args), returncode=returncode, stdout=stdout, stderr=stderr)

    def fake_run_git(repo_dir: Path, *args: str, check: bool = False) -> subprocess.CompletedProcess[str]:
        if args == ("rev-parse", "--git-dir"):
            return _completed(*args, returncode=0, stdout=".git\n")
        if args[:2] == ("add", "--force"):
            return _completed(*args, returncode=0)
        if args == ("diff", "--cached", "--quiet"):
            return _completed(*args, returncode=1)
        if args[0] == "commit":
            return _completed(*args, returncode=128, stderr="gpg failed to sign the data")
        raise AssertionError(f"unexpected git args: {args!r}")

    monkeypatch.setattr("umx.git_ops._run_git", fake_run_git)

    result = git_add_and_commit(repo, paths=[repo / "hello.txt"], message="signed commit")

    assert result.failed is True
    assert result.noop is False
    assert "gpg failed to sign" in result.stderr


def test_git_add_and_commit_adds_signing_flag_when_enabled(
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []

    def _completed(*args: str, returncode: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=list(args), returncode=returncode, stdout=stdout, stderr=stderr)

    def fake_run_git(repo_dir: Path, *args: str, check: bool = False) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if args == ("rev-parse", "--git-dir"):
            return _completed(*args, returncode=0, stdout=".git\n")
        if args[:2] == ("add", "--force"):
            return _completed(*args, returncode=0)
        if args == ("diff", "--cached", "--quiet"):
            return _completed(*args, returncode=1)
        if args[0] == "commit":
            return _completed(*args, returncode=0, stdout="[main 1234567] signed\n")
        raise AssertionError(f"unexpected git args: {args!r}")

    cfg = default_config()
    cfg.git.sign_commits = True
    monkeypatch.setattr("umx.git_ops._run_git", fake_run_git)

    result = git_add_and_commit(repo, paths=[repo / "hello.txt"], message="signed commit", config=cfg)

    assert result == GitCommitResult.committed_result(
        returncode=0,
        stdout="[main 1234567] signed\n",
        signed=True,
    )
    assert any(args[:2] == ("commit", "-S") for args in calls)


def test_git_init_raises_when_initial_commit_fails(
    repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "umxhome"
    monkeypatch.setenv("UMX_HOME", str(home))

    from umx.config import save_config
    from umx.scope import config_path

    cfg = default_config()
    cfg.git.require_signed_commits = True
    save_config(config_path(), cfg)

    real_run_git = git_ops._run_git

    def fake_run_git(repo_dir: Path, *args: str, check: bool = False) -> subprocess.CompletedProcess[str]:
        if args and args[0] == "commit":
            return subprocess.CompletedProcess(
                args=list(args),
                returncode=128,
                stdout="",
                stderr="gpg failed to sign the data",
            )
        return real_run_git(repo_dir, *args, check=check)

    monkeypatch.setattr("umx.git_ops._run_git", fake_run_git)

    with pytest.raises(GitCommitError, match="git init commit failed: gpg failed to sign the data"):
        git_init(repo)


def test_inspect_signed_commit_range_accepts_signature_present_statuses(
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run_git(repo_dir: Path, *args: str, check: bool = False) -> subprocess.CompletedProcess[str]:
        if args == ("rev-parse", "--verify", "origin/main"):
            return subprocess.CompletedProcess(args=list(args), returncode=0, stdout="abc123\n", stderr="")
        if args == ("log", "--format=%H %G?", "origin/main..HEAD"):
            return subprocess.CompletedProcess(
                args=list(args),
                returncode=0,
                stdout="aaaa1111 G\nbbbb2222 U\ncccc3333 E\n",
                stderr="",
            )
        raise AssertionError(f"unexpected git args: {args!r}")

    monkeypatch.setattr("umx.git_ops._run_git", fake_run_git)

    check = inspect_signed_commit_range(repo, base_ref="origin/main")

    assert check.ok is True
    assert check.skipped is False
    assert [item.status for item in check.commits] == ["G", "U", "E"]


def test_assert_signed_commit_range_rejects_unsigned_history(
    repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "umxhome"
    monkeypatch.setenv("UMX_HOME", str(home))

    def fake_run_git(repo_dir: Path, *args: str, check: bool = False) -> subprocess.CompletedProcess[str]:
        if args == ("rev-parse", "--verify", "origin/main"):
            return subprocess.CompletedProcess(args=list(args), returncode=0, stdout="abc123\n", stderr="")
        if args == ("log", "--format=%H %G?", "origin/main..HEAD"):
            return subprocess.CompletedProcess(
                args=list(args),
                returncode=0,
                stdout="aaaa1111 N\nbbbb2222 B\ncccc3333 R\n",
                stderr="",
            )
        raise AssertionError(f"unexpected git args: {args!r}")

    cfg = default_config()
    cfg.git.require_signed_commits = True
    monkeypatch.setattr("umx.git_ops._run_git", fake_run_git)

    with pytest.raises(GitSignedHistoryError, match="unsigned or invalid commit signatures"):
        assert_signed_commit_range(repo, base_ref="origin/main", config=cfg, operation="sync")


def test_assert_signed_commit_range_checks_full_history_when_base_ref_is_missing(
    repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "umxhome"
    monkeypatch.setenv("UMX_HOME", str(home))

    def fake_run_git(repo_dir: Path, *args: str, check: bool = False) -> subprocess.CompletedProcess[str]:
        if args == ("log", "--format=%H %G?", "HEAD"):
            return subprocess.CompletedProcess(
                args=list(args),
                returncode=0,
                stdout="aaaa1111 N\n",
                stderr="",
            )
        if args == ("rev-parse", "--verify", "origin/main"):
            return subprocess.CompletedProcess(args=list(args), returncode=128, stdout="", stderr="")
        raise AssertionError(f"unexpected git args: {args!r}")

    cfg = default_config()
    cfg.git.require_signed_commits = True
    monkeypatch.setattr("umx.git_ops._run_git", fake_run_git)

    with pytest.raises(GitSignedHistoryError, match="unsigned or invalid commit signatures in HEAD"):
        assert_signed_commit_range(repo, base_ref="origin/main", config=cfg, operation="bootstrap")


def test_git_pull_rebase_enables_rebase_signing_when_signing_is_enabled(
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []

    def fake_run_git(repo_dir: Path, *args: str, check: bool = False) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args=list(args), returncode=0, stdout="", stderr="")

    cfg = default_config()
    cfg.git.require_signed_commits = True
    monkeypatch.setattr("umx.git_ops._run_git", fake_run_git)

    assert git_pull_rebase(repo, config=cfg) is True
    assert calls == [("-c", "rebase.gpgSign=true", "pull", "--rebase", "origin")]


def test_uncommitted_sessions(repo: Path) -> None:
    git_init(repo)
    sessions = repo / "sessions" / "2025" / "01"
    sessions.mkdir(parents=True)
    s = sessions / "2025-01-01-abc.jsonl"
    s.write_text('{"event": "test"}\n')

    found = uncommitted_sessions(repo)
    assert len(found) == 1
    assert found[0].name == "2025-01-01-abc.jsonl"


def test_safety_sweep(repo: Path) -> None:
    git_init(repo)
    sessions = repo / "sessions" / "2025" / "01"
    sessions.mkdir(parents=True)
    s = sessions / "2025-01-01-abc.jsonl"
    s.write_text('{"event": "test"}\n')

    count = safety_sweep(repo)
    assert count == 1

    # After sweep, no more uncommitted sessions
    assert uncommitted_sessions(repo) == []


def test_auto_commit_on_add_fact(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "umxhome"
    monkeypatch.setenv("UMX_HOME", str(home))
    from umx.config import default_config, save_config
    from umx.scope import config_path, init_local_umx, init_project_memory, project_memory_dir

    init_local_umx()
    save_config(config_path(), default_config())

    project = tmp_path / "project"
    project.mkdir()
    (project / ".git").mkdir()
    init_project_memory(project)
    repo_dir = project_memory_dir(project)

    from umx.identity import generate_fact_id
    from umx.memory import add_fact
    from umx.models import Fact, MemoryType, Scope, SourceType, Verification

    fact = Fact(
        fact_id=generate_fact_id(),
        text="git commit test fact",
        scope=Scope.PROJECT,
        topic="general",
        encoding_strength=5,
        memory_type=MemoryType.EXPLICIT_SEMANTIC,
        verification=Verification.SELF_REPORTED,
        source_type=SourceType.TOOL_OUTPUT,
    )
    add_fact(repo_dir, fact, auto_commit=True)

    log = subprocess.run(
        ["git", "-C", str(repo_dir), "log", "--oneline"],
        capture_output=True, text=True,
    )
    assert "add fact" in log.stdout
