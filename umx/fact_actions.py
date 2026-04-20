from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from umx.config import load_config
from umx.git_ops import (
    changed_paths,
    git_add_and_commit,
    git_checkout,
    git_commit_failure_message,
    git_create_branch,
    git_current_branch,
    git_path_exists_at_ref,
    git_reset_paths,
    git_restore_path,
)
from umx.governance import (
    build_tombstone_pr_proposal,
    direct_fact_write_error,
    format_repo_paths,
    is_governed_mode,
)
from umx.memory import (
    add_fact,
    find_fact_by_id,
    remove_fact,
    replace_fact,
    target_path_for_fact,
    topic_path,
)
from umx.merge import merge_all
from umx.models import ConsolidationStatus, Scope, Verification
from umx.scope import config_path, project_memory_dir, user_memory_dir
from umx.tombstones import forget_fact, forget_topic


@dataclass(slots=True)
class ActionResult:
    ok: bool
    action: str
    message: str
    fact_id: str | None = None
    changed: list[str] = field(default_factory=list)
    branch: str | None = None
    pr_number: int | None = None
    pr_url: str | None = None


@dataclass(slots=True)
class MergeActionResult:
    ok: bool
    message: str
    results: list[dict] = field(default_factory=list)


def _cfg():
    return load_config(config_path())


def _guard_direct_action(action: str) -> ActionResult | None:
    cfg = _cfg()
    if is_governed_mode(cfg.dream.mode):
        return ActionResult(
            ok=False,
            action=action,
            message=direct_fact_write_error(cfg.dream.mode, f"umx {action}"),
        )
    return None


def _commit(repo: Path, message: str) -> None:
    result = git_add_and_commit(repo, message=message, config=_cfg())
    if result.failed:
        raise RuntimeError(git_commit_failure_message(result, context="commit failed"))


def forget_fact_action(cwd: Path, fact_id: str) -> ActionResult:
    blocked = _guard_direct_action("forget")
    if blocked:
        return blocked
    repo = project_memory_dir(cwd)
    removed = forget_fact(repo, fact_id)
    if removed is None:
        return ActionResult(ok=False, action="forget", message=f"fact not found: {fact_id}", fact_id=fact_id)
    _commit(repo, f"umx: forget {removed.fact_id}")
    return ActionResult(ok=True, action="forget", message=removed.fact_id, fact_id=removed.fact_id, changed=[removed.fact_id])


def forget_topic_action(cwd: Path, topic: str) -> ActionResult:
    blocked = _guard_direct_action("forget")
    if blocked:
        return blocked
    repo = project_memory_dir(cwd)
    removed = forget_topic(repo, topic)
    if not removed:
        return ActionResult(ok=False, action="forget", message=f"topic not found: {topic}")
    _commit(repo, f"umx: forget topic {topic}")
    return ActionResult(
        ok=True,
        action="forget",
        message=str(len(removed)),
        changed=[fact.fact_id for fact in removed],
    )


def _governed_forget_preflight(repo: Path) -> str | None:
    current_branch = git_current_branch(repo)
    if current_branch != "main":
        return (
            "governed forget must run from main; current branch is "
            f"{current_branch or 'detached'}"
        )
    pending = changed_paths(repo)
    if pending:
        return (
            "governed forget requires a clean working tree; pending paths: "
            f"{format_repo_paths(repo, pending)}"
        )
    return None


def _materialize_governed_forget_branch(
    repo: Path,
    fact_id: str,
    branch: str,
) -> tuple[str | None, str | None]:
    original_branch = git_current_branch(repo)
    if original_branch != "main":
        return None, (
            "governed forget must run from main; current branch is "
            f"{original_branch or 'detached'}"
        )
    if not git_create_branch(repo, branch):
        return None, (
            f"failed to create proposal branch {branch}; "
            "delete or rename the existing branch and retry"
        )
    committed = False
    restore_error: str | None = None
    try:
        removed = forget_fact(
            repo,
            fact_id,
            author="human",
            reason=f"governed forget proposal for {fact_id}",
        )
        if removed is None:
            return None, f"fact not found: {fact_id}"
        commit_result = git_add_and_commit(
            repo,
            message=f"umx: forget {removed.fact_id}",
            config=_cfg(),
        )
        if commit_result.failed:
            return None, git_commit_failure_message(commit_result, context="commit failed")
        if commit_result.noop:
            return None, f"no tombstone changes recorded for {removed.fact_id}"
        committed = True
        return removed.fact_id, None
    finally:
        if not committed:
            dirty_paths = changed_paths(repo)
            git_reset_paths(repo, dirty_paths)
            for path in dirty_paths:
                relative = (
                    path.relative_to(repo).as_posix()
                    if repo in path.parents
                    else path.as_posix()
                )
                if git_path_exists_at_ref(repo, "HEAD", relative):
                    git_restore_path(repo, "HEAD", relative)
                else:
                    path.unlink(missing_ok=True)
        if original_branch and not git_checkout(repo, original_branch):
            restore_error = (
                f"proposal branch {branch} was created but the repo could not be restored "
                f"to {original_branch}; run `git checkout {original_branch}` manually"
            )
        if restore_error is not None:
            raise RuntimeError(restore_error)


def forget_fact_governed_action(cwd: Path, fact_id: str) -> ActionResult:
    cfg = _cfg()
    if not is_governed_mode(cfg.dream.mode):
        return ActionResult(
            ok=False,
            action="forget",
            message="--governed requires remote or hybrid mode",
            fact_id=fact_id,
        )
    repo = project_memory_dir(cwd)
    preflight_error = _governed_forget_preflight(repo)
    if preflight_error:
        return ActionResult(
            ok=False,
            action="forget",
            message=preflight_error,
            fact_id=fact_id,
        )
    fact = find_fact_by_id(repo, fact_id)
    if not fact:
        return ActionResult(
            ok=False,
            action="forget",
            message=f"fact not found: {fact_id}",
            fact_id=fact_id,
        )
    proposal = build_tombstone_pr_proposal(fact, repo)
    try:
        materialized_fact_id, materialize_error = _materialize_governed_forget_branch(
            repo,
            fact.fact_id,
            proposal.branch,
        )
    except RuntimeError as exc:
        return ActionResult(
            ok=False,
            action="forget",
            message=str(exc),
            fact_id=fact.fact_id,
            branch=proposal.branch,
        )
    if materialize_error:
        return ActionResult(
            ok=False,
            action="forget",
            message=materialize_error,
            fact_id=fact.fact_id,
            branch=proposal.branch,
        )
    from umx.dream.pipeline import DreamPipeline
    from umx.github_ops import resolve_repo_ref

    pipeline = DreamPipeline(cwd, config=cfg)
    pr_number = pipeline._push_and_open_pr(proposal)
    if pr_number is None:
        return ActionResult(
            ok=False,
            action="forget",
            message=pipeline._push_block_reason or "failed to open governed tombstone PR",
            fact_id=fact.fact_id,
            branch=proposal.branch,
            changed=list(proposal.files_changed),
        )
    repo_ref = resolve_repo_ref(repo, config_org=cfg.org)
    repo_owner = repo_ref.owner or cfg.org
    pr_url = (
        f"https://github.com/{repo_owner}/{repo_ref.name}/pull/{pr_number}"
        if repo_owner
        else None
    )
    message = f"opened governed tombstone PR #{pr_number} for {fact.fact_id}"
    if pr_url:
        message += f" ({pr_url})"
    return ActionResult(
        ok=True,
        action="forget",
        message=message,
        fact_id=materialized_fact_id,
        changed=list(proposal.files_changed),
        branch=proposal.branch,
        pr_number=pr_number,
        pr_url=pr_url,
    )


def promote_fact_action(cwd: Path, fact_id: str, destination: str) -> ActionResult:
    blocked = _guard_direct_action("promote")
    if blocked:
        return blocked
    if destination not in {"user", "project", "principle"}:
        return ActionResult(ok=False, action="promote", message=f"invalid promotion target: {destination}", fact_id=fact_id)
    repo = project_memory_dir(cwd)
    fact = find_fact_by_id(repo, fact_id)
    if not fact:
        return ActionResult(ok=False, action="promote", message=f"fact not found: {fact_id}", fact_id=fact_id)
    if destination == "project":
        if fact.scope == Scope.PROJECT and fact.file_path == target_path_for_fact(repo, fact.clone(scope=Scope.PROJECT, file_path=None)):
            return ActionResult(ok=False, action="promote", message=f"fact already in project scope: {fact.fact_id}", fact_id=fact.fact_id)
        promoted = fact.clone(scope=Scope.PROJECT, file_path=None, repo=repo.name)
        target_repo = repo
        kind = "facts"
    elif destination == "principle":
        target_repo = repo
        promoted = fact.clone(scope=Scope.PROJECT, file_path=None, repo=repo.name)
        target_path = topic_path(target_repo, promoted.topic, kind="principles")
        kind = "principles"
        if fact.file_path == target_path:
            return ActionResult(ok=False, action="promote", message=f"fact already in principles: {fact.fact_id}", fact_id=fact.fact_id)
    else:
        target_repo = user_memory_dir()
        target_repo.mkdir(parents=True, exist_ok=True)
        promoted = fact.clone(scope=Scope.USER, file_path=None, repo=target_repo.name)
        kind = "facts"

    if target_repo == repo:
        removed = remove_fact(repo, fact_id)
        if removed is None:
            return ActionResult(ok=False, action="promote", message=f"failed to remove source fact during promotion: {fact_id}", fact_id=fact_id)
        try:
            add_fact(target_repo, promoted, kind=kind, auto_commit=False)
        except Exception as exc:
            add_fact(repo, fact, auto_commit=False)
            return ActionResult(ok=False, action="promote", message=str(exc), fact_id=fact_id)
        _commit(repo, f"umx: promote {fact.fact_id} to {destination}")
    else:
        try:
            add_fact(target_repo, promoted, kind=kind, auto_commit=False)
        except Exception as exc:
            return ActionResult(ok=False, action="promote", message=str(exc), fact_id=fact_id)
        removed = remove_fact(repo, fact_id)
        if removed is None:
            remove_fact(target_repo, fact_id)
            return ActionResult(ok=False, action="promote", message=f"failed to remove source fact during promotion: {fact_id}", fact_id=fact_id)
        _commit(target_repo, f"umx: promote {fact.fact_id} to {destination}")
        _commit(repo, f"umx: promote {fact.fact_id} to {destination}")

    return ActionResult(
        ok=True,
        action="promote",
        message=f"{fact.fact_id} -> {destination}",
        fact_id=fact.fact_id,
        changed=[fact.fact_id],
    )


def confirm_fact_action(cwd: Path, fact_id: str) -> ActionResult:
    blocked = _guard_direct_action("confirm")
    if blocked:
        return blocked
    repo = project_memory_dir(cwd)
    fact = find_fact_by_id(repo, fact_id)
    if not fact:
        return ActionResult(ok=False, action="confirm", message=f"fact not found: {fact_id}", fact_id=fact_id)
    updated = fact.clone(
        encoding_strength=5,
        verification=Verification.HUMAN_CONFIRMED,
        consolidation_status=ConsolidationStatus.STABLE,
    )
    replace_fact(repo, updated)
    _commit(repo, f"umx: confirm {updated.fact_id}")
    return ActionResult(ok=True, action="confirm", message=updated.fact_id, fact_id=updated.fact_id, changed=[updated.fact_id])


def merge_conflicts_action(cwd: Path, *, dry_run: bool = False) -> MergeActionResult:
    cfg = _cfg()
    if not dry_run and is_governed_mode(cfg.dream.mode):
        return MergeActionResult(
            ok=False,
            message=direct_fact_write_error(cfg.dream.mode, "umx merge"),
        )
    repo = project_memory_dir(cwd)
    results = merge_all(repo, cfg, dry_run=dry_run)
    if not dry_run and results:
        _commit(repo, "umx: merge conflicts")
    return MergeActionResult(
        ok=True,
        message=f"resolved {len(results)} conflicts",
        results=results,
    )
