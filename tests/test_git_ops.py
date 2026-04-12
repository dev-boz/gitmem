from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from umx.git_ops import (
    git_add_and_commit,
    git_init,
    is_git_repo,
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
    assert ".umx.json" in content
    assert "meta/dream.lock" in content


def test_git_add_and_commit(repo: Path) -> None:
    git_init(repo)
    f = repo / "hello.txt"
    f.write_text("hello world\n")
    committed = git_add_and_commit(repo, paths=[f], message="add hello")
    assert committed is True

    log = subprocess.run(
        ["git", "-C", str(repo), "log", "--oneline"],
        capture_output=True, text=True,
    )
    assert "add hello" in log.stdout

    # No changes → should return False
    assert git_add_and_commit(repo, paths=[f], message="noop") is False


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
