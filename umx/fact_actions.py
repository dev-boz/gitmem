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
    git_read_text_at_ref,
    git_ref_sha,
    git_rev_list_for_path,
    git_revert_head,
    git_reset_paths,
    git_restore_path,
)
from umx.governance import (
    assert_governance_pr_body,
    build_rollback_pr_proposal,
    build_topic_tombstone_pr_proposal,
    build_tombstone_pr_proposal,
    direct_fact_write_error,
    format_repo_paths,
    is_governed_mode,
)
from umx.memory import (
    add_fact,
    cache_path_for,
    find_fact_by_id,
    load_all_facts,
    manual_edit_successor,
    parse_fact_line,
    remove_fact,
    replace_fact,
    target_path_for_fact,
    topic_path,
)
from umx.merge import merge_all
from umx.models import ConsolidationStatus, Fact, Scope, Verification
from umx.scope import config_path, project_memory_dir, user_memory_dir
from umx.tombstones import forget_fact, forget_topic, load_tombstones, remove_tombstones


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


def _commit(repo: Path, message: str, *, paths: list[Path] | None = None) -> bool:
    result = git_add_and_commit(repo, paths=paths, message=message, config=_cfg())
    if result.failed:
        raise RuntimeError(git_commit_failure_message(result, context="commit failed"))
    return result.committed


def _locate_fact(cwd: Path, fact_id: str) -> tuple[Path | None, Fact | None]:
    project_repo = project_memory_dir(cwd)
    if fact := find_fact_by_id(project_repo, fact_id):
        return project_repo, fact
    user_repo = user_memory_dir()
    if fact := find_fact_by_id(user_repo, fact_id):
        return user_repo, fact
    return None, None


def _is_principle_fact(repo: Path, fact: Fact) -> bool:
    if fact.file_path is None:
        return False
    try:
        relative = fact.file_path.relative_to(repo).as_posix()
    except ValueError:
        return False
    return relative.startswith("principles/")


def _capture_file_snapshots(*paths: Path) -> dict[Path, str | None]:
    snapshots: dict[Path, str | None] = {}
    for path in paths:
        for related in _related_fact_paths(path):
            if related in snapshots:
                continue
            snapshots[related] = related.read_text() if related.exists() else None
    return snapshots


def _related_fact_paths(path: Path) -> list[Path]:
    related_paths = [path]
    if path.suffix == ".md" and not path.name.endswith(".umx.json"):
        related_paths.append(cache_path_for(path))
    return related_paths


def _guard_action_paths_clean(action: str, repo: Path, *paths: Path) -> ActionResult | None:
    relevant_paths = {related for path in paths for related in _related_fact_paths(path)}
    dirty_paths = [path for path in changed_paths(repo) if path in relevant_paths]
    if not dirty_paths:
        return None
    return ActionResult(
        ok=False,
        action=action,
        message=f"cannot {action} while target paths have uncommitted changes: {format_repo_paths(repo, dirty_paths)}",
    )


def _restore_file_snapshots(snapshots: dict[Path, str | None]) -> None:
    for path, content in snapshots.items():
        if content is None:
            path.unlink(missing_ok=True)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


def _rollback_repo(
    repo: Path,
    before_head: str | None,
    snapshots: dict[Path, str | None],
    *,
    committed: bool,
) -> str | None:
    error: str | None = None
    current_head = git_ref_sha(repo, "HEAD")
    if committed and before_head and current_head != before_head and not git_revert_head(repo):
        error = f"rollback failed for {repo}"
    reset_paths = [path for path, content in snapshots.items() if content is not None or path.exists()]
    if not git_reset_paths(repo, reset_paths):
        reset_error = f"failed to reset staged paths for {repo}"
        error = f"{error}; {reset_error}" if error else reset_error
    try:
        _restore_file_snapshots(snapshots)
    except OSError as exc:
        restore_error = f"restore failed for {repo}: {exc}"
        error = f"{error}; {restore_error}" if error else restore_error
    return error


def forget_fact_action(cwd: Path, fact_id: str) -> ActionResult:
    blocked = _guard_direct_action("forget")
    if blocked:
        return blocked
    repo = project_memory_dir(cwd)
    fact = find_fact_by_id(repo, fact_id)
    if not fact:
        return ActionResult(ok=False, action="forget", message=f"fact not found: {fact_id}", fact_id=fact_id)
    source_path = fact.file_path or target_path_for_fact(repo, fact)
    tombstones_path = repo / "meta" / "tombstones.jsonl"
    if blocked := _guard_action_paths_clean("forget", repo, source_path, tombstones_path):
        return blocked
    snapshots = _capture_file_snapshots(source_path, tombstones_path)
    before_head = git_ref_sha(repo, "HEAD")
    committed = False
    try:
        removed = forget_fact(repo, fact_id)
        if removed is None:
            return ActionResult(ok=False, action="forget", message=f"fact not found: {fact_id}", fact_id=fact_id)
        committed = _commit(repo, f"umx: forget {removed.fact_id}", paths=list(snapshots))
    except Exception as exc:
        rollback_error = _rollback_repo(repo, before_head, snapshots, committed=committed)
        message = str(exc)
        if rollback_error:
            message = f"{message}; {rollback_error}"
        return ActionResult(ok=False, action="forget", message=message, fact_id=fact_id)
    return ActionResult(ok=True, action="forget", message=removed.fact_id, fact_id=removed.fact_id, changed=[removed.fact_id])


def forget_topic_action(cwd: Path, topic: str) -> ActionResult:
    blocked = _guard_direct_action("forget")
    if blocked:
        return blocked
    repo = project_memory_dir(cwd)
    facts = [fact for fact in load_all_facts(repo, include_superseded=False) if fact.topic == topic]
    if not facts:
        return ActionResult(ok=False, action="forget", message=f"topic not found: {topic}")
    affected_paths = [fact.file_path or target_path_for_fact(repo, fact) for fact in facts]
    tombstones_path = repo / "meta" / "tombstones.jsonl"
    if blocked := _guard_action_paths_clean("forget", repo, *[*affected_paths, tombstones_path]):
        return blocked
    snapshots = _capture_file_snapshots(*affected_paths, tombstones_path)
    before_head = git_ref_sha(repo, "HEAD")
    committed = False
    try:
        removed = forget_topic(repo, topic)
        if not removed:
            return ActionResult(ok=False, action="forget", message=f"topic not found: {topic}")
        committed = _commit(repo, f"umx: forget topic {topic}", paths=list(snapshots))
    except Exception as exc:
        rollback_error = _rollback_repo(repo, before_head, snapshots, committed=committed)
        message = str(exc)
        if rollback_error:
            message = f"{message}; {rollback_error}"
        return ActionResult(ok=False, action="forget", message=message)
    return ActionResult(
        ok=True,
        action="forget",
        message=str(len(removed)),
        changed=[fact.fact_id for fact in removed],
    )


def _governed_proposal_preflight(repo: Path) -> str | None:
    current_branch = git_current_branch(repo)
    if current_branch != "main":
        return (
            "governed proposal must run from main; current branch is "
            f"{current_branch or 'detached'}"
        )
    pending = changed_paths(repo)
    if pending:
        return (
            "governed proposal requires a clean working tree; pending paths: "
            f"{format_repo_paths(repo, pending)}"
        )
    return None


def _materialize_governed_forget_branch(
    repo: Path,
    branch: str,
    *,
    fact_id: str | None = None,
    topic: str | None = None,
) -> tuple[list[Fact] | None, str | None]:
    if (fact_id is None) == (topic is None):
        raise ValueError("pass exactly one of fact_id or topic")
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
        if fact_id is not None:
            removed = forget_fact(
                repo,
                fact_id,
                author="human",
                reason=f"governed forget proposal for {fact_id}",
            )
            if removed is None:
                return None, f"fact not found: {fact_id}"
            removed_facts = [removed]
            commit_message = f"umx: forget {removed.fact_id}"
            noop_message = f"no tombstone changes recorded for {removed.fact_id}"
        else:
            removed_facts = forget_topic(
                repo,
                topic,
                author="human",
                reason=f"governed forget proposal for topic {topic}",
            )
            if not removed_facts:
                return None, f"topic not found: {topic}"
            commit_message = f"umx: forget topic {topic}"
            noop_message = f"no tombstone changes recorded for topic {topic}"
        commit_result = git_add_and_commit(
            repo,
            message=commit_message,
            config=_cfg(),
        )
        if commit_result.failed:
            return None, git_commit_failure_message(commit_result, context="commit failed")
        if commit_result.noop:
            return None, noop_message
        committed = True
        return removed_facts, None
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


def _open_governed_proposal_pr(
    cwd: Path,
    repo: Path,
    proposal,
    *,
    action: str,
    failure_message: str,
    success_message: str,
    changed: list[str],
    fact_id: str | None = None,
) -> ActionResult:
    cfg = _cfg()
    from umx.dream.pipeline import DreamPipeline
    from umx.github_ops import resolve_repo_ref

    pipeline = DreamPipeline(cwd, config=cfg)
    pr_number = pipeline._push_and_open_pr(proposal)
    if pr_number is None:
        return ActionResult(
            ok=False,
            action=action,
            message=pipeline._push_block_reason or failure_message,
            fact_id=fact_id,
            changed=changed,
            branch=proposal.branch,
        )
    repo_ref = resolve_repo_ref(repo, config_org=cfg.org)
    repo_owner = repo_ref.owner or cfg.org
    pr_url = (
        f"https://github.com/{repo_owner}/{repo_ref.name}/pull/{pr_number}"
        if repo_owner
        else None
    )
    message = success_message.format(pr_number=f"#{pr_number}")
    if pr_url:
        message += f" ({pr_url})"
    return ActionResult(
        ok=True,
        action=action,
        message=message,
        fact_id=fact_id,
        changed=changed,
        branch=proposal.branch,
        pr_number=pr_number,
        pr_url=pr_url,
    )


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
    preflight_error = _governed_proposal_preflight(repo)
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
        removed_facts, materialize_error = _materialize_governed_forget_branch(
            repo,
            proposal.branch,
            fact_id=fact.fact_id,
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
    changed = [item.fact_id for item in (removed_facts or [fact])]
    return _open_governed_proposal_pr(
        cwd,
        repo,
        proposal,
        action="forget",
        failure_message="failed to open governed tombstone PR",
        success_message=f"opened governed tombstone PR {{pr_number}} for {fact.fact_id}",
        changed=changed,
        fact_id=fact.fact_id,
    )


def forget_topic_governed_action(cwd: Path, topic: str) -> ActionResult:
    cfg = _cfg()
    if not is_governed_mode(cfg.dream.mode):
        return ActionResult(
            ok=False,
            action="forget",
            message="--governed requires remote or hybrid mode",
        )
    repo = project_memory_dir(cwd)
    preflight_error = _governed_proposal_preflight(repo)
    if preflight_error:
        return ActionResult(
            ok=False,
            action="forget",
            message=preflight_error,
        )
    facts = [fact for fact in load_all_facts(repo, include_superseded=False) if fact.topic == topic]
    if not facts:
        return ActionResult(ok=False, action="forget", message=f"topic not found: {topic}")
    proposal = build_topic_tombstone_pr_proposal(facts, topic, repo)
    try:
        removed_facts, materialize_error = _materialize_governed_forget_branch(
            repo,
            proposal.branch,
            topic=topic,
        )
    except RuntimeError as exc:
        return ActionResult(
            ok=False,
            action="forget",
            message=str(exc),
            branch=proposal.branch,
        )
    if materialize_error:
        return ActionResult(
            ok=False,
            action="forget",
            message=materialize_error,
            branch=proposal.branch,
        )
    changed = [fact.fact_id for fact in (removed_facts or facts)]
    count = len(changed)
    return _open_governed_proposal_pr(
        cwd,
        repo,
        proposal,
        action="forget",
        failure_message="failed to open governed tombstone PR",
        success_message=f"opened governed tombstone PR {{pr_number}} for topic {topic}"
        + (f" ({count} facts)" if count else ""),
        changed=changed,
    )


def _find_fact_in_history(repo: Path, *, fact_id: str, relative_path: str) -> Fact | None:
    fact_path = repo / relative_path
    for ref in git_rev_list_for_path(repo, relative_path):
        text = git_read_text_at_ref(repo, ref, relative_path)
        if text is None:
            continue
        for line in text.splitlines():
            fact = parse_fact_line(line, repo_dir=repo, path=fact_path)
            if fact is not None and fact.fact_id == fact_id:
                return fact
    return None


def _resolve_governed_rollback_facts(repo: Path, *, source_pr_number: int) -> tuple[list[Fact], str | None]:
    from umx.github_ops import read_pr_body, resolve_repo_ref

    repo_ref = resolve_repo_ref(repo, config_org=_cfg().org)
    repo_owner = repo_ref.owner or _cfg().org
    if not repo_owner:
        return [], "governed rollback requires a GitHub owner (set org or origin remote)"
    body = read_pr_body(repo_owner, repo_ref.name, source_pr_number)
    if body is None:
        return [], f"could not read governance PR #{source_pr_number}"
    payload = assert_governance_pr_body(body)
    if payload is None:
        return [], f"governance PR #{source_pr_number} does not contain a fact-delta block"
    tombstoned = payload.get("tombstoned")
    if not isinstance(tombstoned, list) or not tombstoned:
        return [], f"governance PR #{source_pr_number} does not tombstone any facts"

    current_tombstones = {item.fact_id for item in load_tombstones(repo) if item.fact_id}
    facts: list[Fact] = []
    expected_fact_ids: set[str] = set()
    for entry in tombstoned:
        if not isinstance(entry, dict):
            return [], f"governance PR #{source_pr_number} contains malformed tombstone entries"
        fact_id = entry.get("fact_id")
        relative_path = entry.get("path")
        if not isinstance(fact_id, str) or not fact_id.strip():
            return [], f"governance PR #{source_pr_number} tombstone entries must include fact_id"
        if not isinstance(relative_path, str) or not relative_path.strip():
            return [], f"governance PR #{source_pr_number} tombstone entries must include path"
        fact_id = fact_id.strip()
        relative_path = relative_path.strip()
        expected_fact_ids.add(fact_id)
        if find_fact_by_id(repo, fact_id) is not None:
            return [], f"fact already exists on main: {fact_id}"
        fact = _find_fact_in_history(repo, fact_id=fact_id, relative_path=relative_path)
        if fact is None:
            return [], f"could not reconstruct {fact_id} from git history"
        facts.append(fact)

    missing_tombstones = sorted(expected_fact_ids - current_tombstones)
    if missing_tombstones:
        return [], (
            "rollback requires active tombstones for all source facts; missing: "
            + ", ".join(missing_tombstones)
        )
    return facts, None


def _materialize_governed_rollback_branch(
    repo: Path,
    *,
    source_pr_number: int,
    facts: list[Fact],
    branch: str,
) -> str | None:
    original_branch = git_current_branch(repo)
    if original_branch != "main":
        return (
            "governed proposal must run from main; current branch is "
            f"{original_branch or 'detached'}"
        )
    if not git_create_branch(repo, branch):
        return (
            f"failed to create proposal branch {branch}; "
            "delete or rename the existing branch and retry"
        )
    committed = False
    restore_error: str | None = None
    try:
        removed = remove_tombstones(repo, fact_ids={fact.fact_id for fact in facts})
        removed_ids = {item.fact_id for item in removed if item.fact_id}
        missing = sorted({fact.fact_id for fact in facts} - removed_ids)
        if missing:
            return "rollback requires active tombstones for all source facts; missing: " + ", ".join(missing)
        for fact in facts:
            add_fact(repo, fact, auto_commit=False)
        commit_result = git_add_and_commit(
            repo,
            message=f"umx: rollback PR #{source_pr_number}",
            config=_cfg(),
        )
        if commit_result.failed:
            return git_commit_failure_message(commit_result, context="commit failed")
        if commit_result.noop:
            return f"no rollback changes recorded for PR #{source_pr_number}"
        committed = True
        return None
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


def rollback_governed_action(cwd: Path, source_pr_number: int) -> ActionResult:
    cfg = _cfg()
    if not is_governed_mode(cfg.dream.mode):
        return ActionResult(
            ok=False,
            action="rollback",
            message="rollback requires remote or hybrid mode",
        )
    repo = project_memory_dir(cwd)
    preflight_error = _governed_proposal_preflight(repo)
    if preflight_error:
        return ActionResult(ok=False, action="rollback", message=preflight_error)
    facts, resolve_error = _resolve_governed_rollback_facts(repo, source_pr_number=source_pr_number)
    if resolve_error:
        return ActionResult(ok=False, action="rollback", message=resolve_error)
    proposal = build_rollback_pr_proposal(facts, source_pr_number=source_pr_number, repo_dir=repo)
    try:
        materialize_error = _materialize_governed_rollback_branch(
            repo,
            source_pr_number=source_pr_number,
            facts=facts,
            branch=proposal.branch,
        )
    except RuntimeError as exc:
        return ActionResult(
            ok=False,
            action="rollback",
            message=str(exc),
            branch=proposal.branch,
        )
    if materialize_error:
        return ActionResult(
            ok=False,
            action="rollback",
            message=materialize_error,
            branch=proposal.branch,
        )
    count = len(facts)
    return _open_governed_proposal_pr(
        cwd,
        repo,
        proposal,
        action="rollback",
        failure_message="failed to open governed rollback PR",
        success_message=f"opened governed rollback PR {{pr_number}} for source PR #{source_pr_number}"
        + (f" ({count} facts)" if count else ""),
        changed=[fact.fact_id for fact in facts],
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

    source_path = fact.file_path or target_path_for_fact(repo, fact)
    target_path = target_path_for_fact(target_repo, promoted) if kind == "facts" else topic_path(target_repo, promoted.topic, kind="principles")
    same_repo_paths = (source_path, target_path) if target_repo == repo else (source_path,)
    if blocked := _guard_action_paths_clean("promote", repo, *same_repo_paths):
        return blocked
    if target_repo != repo and (blocked := _guard_action_paths_clean("promote", target_repo, target_path)):
        return blocked
    source_snapshots = _capture_file_snapshots(source_path)
    target_snapshots = _capture_file_snapshots(target_path)
    repo_snapshots = _capture_file_snapshots(source_path, target_path)
    source_before_head = git_ref_sha(repo, "HEAD")
    target_before_head = git_ref_sha(target_repo, "HEAD")
    source_committed = False
    target_committed = False

    if target_repo == repo:
        try:
            removed = remove_fact(repo, fact_id)
            if removed is None:
                return ActionResult(
                    ok=False,
                    action="promote",
                    message=f"failed to remove source fact during promotion: {fact_id}",
                    fact_id=fact_id,
                )
            add_fact(target_repo, promoted, kind=kind, auto_commit=False)
            source_committed = _commit(
                repo,
                f"umx: promote {fact.fact_id} to {destination}",
                paths=list(repo_snapshots),
            )
        except Exception as exc:
            rollback_error = _rollback_repo(repo, source_before_head, repo_snapshots, committed=source_committed)
            message = str(exc)
            if rollback_error:
                message = f"{message}; {rollback_error}"
            return ActionResult(ok=False, action="promote", message=message, fact_id=fact_id)
    else:
        try:
            add_fact(target_repo, promoted, kind=kind, auto_commit=False)
            removed = remove_fact(repo, fact_id)
            if removed is None:
                raise RuntimeError(f"failed to remove source fact during promotion: {fact_id}")
            target_committed = _commit(
                target_repo,
                f"umx: promote {fact.fact_id} to {destination}",
                paths=list(target_snapshots),
            )
            source_committed = _commit(
                repo,
                f"umx: promote {fact.fact_id} to {destination}",
                paths=list(source_snapshots),
            )
        except Exception as exc:
            rollback_errors = [
                error
                for error in (
                    _rollback_repo(target_repo, target_before_head, target_snapshots, committed=target_committed),
                    _rollback_repo(repo, source_before_head, source_snapshots, committed=source_committed),
                )
                if error
            ]
            message = str(exc)
            if rollback_errors:
                message = f"{message}; {'; '.join(rollback_errors)}"
            return ActionResult(ok=False, action="promote", message=message, fact_id=fact_id)

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
    source_path = fact.file_path or target_path_for_fact(repo, fact)
    if blocked := _guard_action_paths_clean("confirm", repo, source_path):
        return blocked
    updated = fact.clone(
        encoding_strength=5,
        verification=Verification.HUMAN_CONFIRMED,
        consolidation_status=ConsolidationStatus.STABLE,
    )
    snapshots = _capture_file_snapshots(source_path)
    before_head = git_ref_sha(repo, "HEAD")
    committed = False
    try:
        replace_fact(repo, updated)
        committed = _commit(repo, f"umx: confirm {updated.fact_id}", paths=list(snapshots))
    except Exception as exc:
        rollback_error = _rollback_repo(repo, before_head, snapshots, committed=committed)
        message = str(exc)
        if rollback_error:
            message = f"{message}; {rollback_error}"
        return ActionResult(ok=False, action="confirm", message=message, fact_id=fact_id)
    return ActionResult(ok=True, action="confirm", message=updated.fact_id, fact_id=updated.fact_id, changed=[updated.fact_id])


def edit_fact_action(cwd: Path, fact_id: str, text: str) -> ActionResult:
    blocked = _guard_direct_action("edit")
    if blocked:
        return blocked
    repo, fact = _locate_fact(cwd, fact_id)
    if repo is None or fact is None:
        return ActionResult(ok=False, action="edit", message=f"fact not found: {fact_id}", fact_id=fact_id)
    new_text = text.strip()
    if not new_text:
        return ActionResult(ok=False, action="edit", message="edited fact text must not be empty", fact_id=fact_id)
    if new_text == fact.text:
        return ActionResult(ok=True, action="edit", message=f"no changes for {fact_id}", fact_id=fact_id)
    previous, updated = manual_edit_successor(fact, new_text)
    source_path = fact.file_path or target_path_for_fact(repo, fact)
    target_path = target_path_for_fact(repo, updated)
    if blocked := _guard_action_paths_clean("edit", repo, source_path, target_path):
        return blocked
    snapshots = _capture_file_snapshots(source_path, target_path)
    before_head = git_ref_sha(repo, "HEAD")
    committed = False
    try:
        if not replace_fact(repo, previous):
            return ActionResult(ok=False, action="edit", message=f"failed to prepare supersession for {fact_id}", fact_id=fact_id)
        add_fact(repo, updated, auto_commit=False)
        committed = _commit(repo, f"umx: edit {fact_id}", paths=list(snapshots))
    except Exception as exc:
        rollback_error = _rollback_repo(repo, before_head, snapshots, committed=committed)
        message = str(exc)
        if rollback_error:
            message = f"{message}; {rollback_error}"
        return ActionResult(ok=False, action="edit", message=message, fact_id=fact_id)
    return ActionResult(
        ok=True,
        action="edit",
        message=updated.fact_id,
        fact_id=updated.fact_id,
        changed=[fact_id, updated.fact_id],
    )


def demote_fact_action(cwd: Path, fact_id: str) -> ActionResult:
    blocked = _guard_direct_action("demote")
    if blocked:
        return blocked
    project_repo = project_memory_dir(cwd)
    user_repo = user_memory_dir()
    repo, fact = _locate_fact(cwd, fact_id)
    if repo is None or fact is None:
        return ActionResult(ok=False, action="demote", message=f"fact not found: {fact_id}", fact_id=fact_id)

    if repo == user_repo and fact.scope == Scope.USER:
        demoted = fact.clone(scope=Scope.PROJECT, file_path=None, repo=project_repo.name)
        source_path = fact.file_path or target_path_for_fact(user_repo, fact)
        target_path = target_path_for_fact(project_repo, demoted)
        if blocked := _guard_action_paths_clean("demote", user_repo, source_path):
            return blocked
        if blocked := _guard_action_paths_clean("demote", project_repo, target_path):
            return blocked
        source_snapshots = _capture_file_snapshots(source_path)
        target_snapshots = _capture_file_snapshots(target_path)
        source_before_head = git_ref_sha(user_repo, "HEAD")
        target_before_head = git_ref_sha(project_repo, "HEAD")
        source_committed = False
        target_committed = False
        try:
            add_fact(project_repo, demoted, kind="facts", auto_commit=False)
            removed = remove_fact(user_repo, fact_id)
            if removed is None:
                raise RuntimeError(f"failed to remove source fact during demotion: {fact_id}")
            target_committed = _commit(
                project_repo,
                f"umx: demote {fact.fact_id} to project",
                paths=list(target_snapshots),
            )
            source_committed = _commit(
                user_repo,
                f"umx: demote {fact.fact_id} to project",
                paths=list(source_snapshots),
            )
        except Exception as exc:
            rollback_errors = [
                error
                for error in (
                    _rollback_repo(project_repo, target_before_head, target_snapshots, committed=target_committed),
                    _rollback_repo(user_repo, source_before_head, source_snapshots, committed=source_committed),
                )
                if error
            ]
            message = str(exc)
            if rollback_errors:
                message = f"{message}; {'; '.join(rollback_errors)}"
            return ActionResult(ok=False, action="demote", message=message, fact_id=fact_id)
        return ActionResult(
            ok=True,
            action="demote",
            message=f"{fact.fact_id} -> project",
            fact_id=fact.fact_id,
            changed=[fact.fact_id],
        )

    if repo == project_repo and _is_principle_fact(project_repo, fact):
        demoted = fact.clone(scope=Scope.PROJECT, file_path=None, repo=project_repo.name)
        source_path = fact.file_path or target_path_for_fact(project_repo, fact)
        target_path = target_path_for_fact(project_repo, demoted)
        if blocked := _guard_action_paths_clean("demote", project_repo, source_path, target_path):
            return blocked
        snapshots = _capture_file_snapshots(source_path, target_path)
        before_head = git_ref_sha(project_repo, "HEAD")
        committed = False
        try:
            removed = remove_fact(project_repo, fact_id)
            if removed is None:
                return ActionResult(
                    ok=False,
                    action="demote",
                    message=f"failed to remove source fact during demotion: {fact_id}",
                    fact_id=fact_id,
                )
            add_fact(project_repo, demoted, kind="facts", auto_commit=False)
            committed = _commit(
                project_repo,
                f"umx: demote {fact.fact_id} to project",
                paths=list(snapshots),
            )
        except Exception as exc:
            rollback_error = _rollback_repo(project_repo, before_head, snapshots, committed=committed)
            message = str(exc)
            if rollback_error:
                message = f"{message}; {rollback_error}"
            return ActionResult(ok=False, action="demote", message=message, fact_id=fact_id)
        return ActionResult(
            ok=True,
            action="demote",
            message=f"{fact.fact_id} -> project",
            fact_id=fact.fact_id,
            changed=[fact.fact_id],
        )

    return ActionResult(
        ok=False,
        action="demote",
        message=f"no lower supported scope for {fact_id}",
        fact_id=fact_id,
    )


def merge_conflicts_action(cwd: Path, *, dry_run: bool = False) -> MergeActionResult:
    cfg = _cfg()
    if not dry_run and is_governed_mode(cfg.dream.mode):
        return MergeActionResult(
            ok=False,
            message=direct_fact_write_error(cfg.dream.mode, "umx merge"),
        )
    repo = project_memory_dir(cwd)
    preview_results = merge_all(repo, cfg, dry_run=True)
    if dry_run:
        results = preview_results
    else:
        affected_paths = []
        for result in preview_results:
            for fact_id in (result["winner_id"], result["loser_id"]):
                fact = find_fact_by_id(repo, fact_id)
                if fact is None:
                    continue
                affected_paths.append(fact.file_path or target_path_for_fact(repo, fact))
        if affected_paths and (blocked := _guard_action_paths_clean("merge", repo, *affected_paths)):
            return MergeActionResult(ok=False, message=blocked.message)
        snapshots = _capture_file_snapshots(*affected_paths)
        before_head = git_ref_sha(repo, "HEAD")
        committed = False
        try:
            results = merge_all(repo, cfg, dry_run=False)
            if results:
                committed = _commit(repo, "umx: merge conflicts", paths=list(snapshots))
        except Exception as exc:
            rollback_error = _rollback_repo(repo, before_head, snapshots, committed=committed)
            message = str(exc)
            if rollback_error:
                message = f"{message}; {rollback_error}"
            return MergeActionResult(ok=False, message=message)
    return MergeActionResult(
        ok=True,
        message=f"resolved {len(results)} conflicts",
        results=results,
    )
