from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import umx.fact_actions as fact_actions
from umx.git_ops import GitCommitResult
from umx.memory import add_fact, cache_path_for, find_fact_by_id, load_all_facts
from umx.models import ConsolidationStatus, Fact, MemoryType, Scope, SourceType, Verification


def test_edit_fact_action_rolls_back_on_commit_failure(project_dir: Path, project_repo: Path) -> None:
    fact = Fact(
        fact_id="01TESTACTIONEDIT000000001",
        text="postgres runs on 5432 in dev",
        scope=Scope.PROJECT,
        topic="devenv",
        encoding_strength=3,
        memory_type=MemoryType.EXPLICIT_SEMANTIC,
        verification=Verification.SELF_REPORTED,
        source_type=SourceType.TOOL_OUTPUT,
        source_tool="codex",
        source_session="sess-action-edit",
        consolidation_status=ConsolidationStatus.FRAGILE,
    )
    add_fact(project_repo, fact)
    fact_path = project_repo / "facts" / "topics" / "devenv.md"
    cache_path = cache_path_for(fact_path)
    baseline_fact = fact_path.read_text()
    baseline_cache = cache_path.read_text()

    def staged_commit_failure(repo: Path, paths=None, message: str = "", config=None) -> GitCommitResult:
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True, text=True)
        return GitCommitResult.failed_result(stderr="simulated commit failure")

    with patch("umx.fact_actions.git_add_and_commit", side_effect=staged_commit_failure):
        result = fact_actions.edit_fact_action(project_dir, fact.fact_id, "postgres runs on 5433 in dev")

    assert result.ok is False
    facts = load_all_facts(project_repo, include_superseded=True)
    assert [item.fact_id for item in facts] == [fact.fact_id]
    restored = facts[0]
    assert restored.text == fact.text
    assert restored.superseded_by is None
    assert fact_path.read_text() == baseline_fact
    assert cache_path.read_text() == baseline_cache
    cached_diff = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--", "facts/topics/devenv.md", "facts/topics/devenv.umx.json"],
        cwd=project_repo,
        check=True,
        capture_output=True,
        text=True,
    )
    worktree_diff = subprocess.run(
        ["git", "diff", "--name-only", "--", "facts/topics/devenv.md", "facts/topics/devenv.umx.json"],
        cwd=project_repo,
        check=True,
        capture_output=True,
        text=True,
    )
    assert cached_diff.stdout.strip() == ""
    assert worktree_diff.stdout.strip() == ""


def test_demote_fact_action_rolls_back_cross_repo_commit_failure(
    project_dir: Path,
    project_repo: Path,
    user_repo: Path,
) -> None:
    fact = Fact(
        fact_id="01TESTACTIONDEMOTE000001",
        text="prefer concise release notes",
        scope=Scope.USER,
        topic="writing",
        encoding_strength=5,
        memory_type=MemoryType.EXPLICIT_SEMANTIC,
        verification=Verification.HUMAN_CONFIRMED,
        source_type=SourceType.USER_PROMPT,
        source_tool="human",
        source_session="sess-action-demote",
        consolidation_status=ConsolidationStatus.STABLE,
    )
    add_fact(user_repo, fact)

    original_commit = fact_actions._commit

    def flaky_commit(repo: Path, message: str, *, paths=None) -> bool:
        if repo == project_repo:
            return original_commit(repo, message, paths=paths)
        raise RuntimeError("commit failed")

    with patch("umx.fact_actions._commit", side_effect=flaky_commit):
        result = fact_actions.demote_fact_action(project_dir, fact.fact_id)

    assert result.ok is False
    restored = find_fact_by_id(user_repo, fact.fact_id)
    assert restored is not None
    assert restored.scope == Scope.USER
    assert find_fact_by_id(project_repo, fact.fact_id) is None


def test_confirm_fact_action_does_not_commit_unrelated_staged_paths(project_dir: Path, project_repo: Path) -> None:
    fact = Fact(
        fact_id="01TESTACTIONCONFIRM00001",
        text="staging uses blue/green cutovers",
        scope=Scope.PROJECT,
        topic="deploy",
        encoding_strength=3,
        memory_type=MemoryType.EXPLICIT_SEMANTIC,
        verification=Verification.SELF_REPORTED,
        source_type=SourceType.TOOL_OUTPUT,
        source_tool="codex",
        source_session="sess-action-confirm",
        consolidation_status=ConsolidationStatus.FRAGILE,
    )
    add_fact(project_repo, fact)
    unrelated = project_repo / "unrelated.txt"
    unrelated.write_text("leave me staged\n")
    subprocess.run(["git", "add", "unrelated.txt"], cwd=project_repo, check=True, capture_output=True, text=True)

    result = fact_actions.confirm_fact_action(project_dir, fact.fact_id)

    assert result.ok is True
    updated = next(item for item in load_all_facts(project_repo, include_superseded=False) if item.fact_id == fact.fact_id)
    assert updated.verification == Verification.HUMAN_CONFIRMED
    cached_diff = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--", "unrelated.txt"],
        cwd=project_repo,
        check=True,
        capture_output=True,
        text=True,
    )
    assert cached_diff.stdout.strip() == "unrelated.txt"
