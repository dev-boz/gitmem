from __future__ import annotations

from pathlib import Path

from umx.config import UMXConfig, default_config
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
from umx.governance import generate_rederive_correction_pr
from umx.dream.extract import session_records_to_facts
from umx.memory import add_fact
from umx.memory import load_all_facts
from umx.models import Fact
from umx.sessions import iter_session_payloads
from umx.tombstones import forget_fact


def rederive_from_sessions(
    repo_dir: Path,
    session_ids: list[str] | None = None,
    config: UMXConfig | None = None,
) -> list[Fact]:
    """Re-extract facts from raw sessions.

    If session_ids is None, re-derive from all sessions.
    Returns newly extracted facts (does not commit — caller decides).
    """
    return session_records_to_facts(
        repo_dir,
        config,
        include_archived=True,
        session_ids=set(session_ids) if session_ids else None,
        skip_gathered=False,
    )


def compare_derived(existing: list[Fact], rederived: list[Fact]) -> dict:
    """Compare existing facts against rederived ones.

    Uses text-based matching to find correspondences.
    """
    matching, missing_from_existing, missing_from_rederived = compare_derived_facts(existing, rederived)

    return {
        "matching": matching,
        "divergent": [],
        "missing_from_existing": len(missing_from_existing),
        "missing_from_rederived": len(missing_from_rederived),
        "missing_from_existing_facts": [_fact_summary(fact) for fact in missing_from_existing],
        "missing_from_rederived_facts": [_fact_summary(fact) for fact in missing_from_rederived],
    }


def compare_derived_facts(
    existing: list[Fact],
    rederived: list[Fact],
) -> tuple[int, list[Fact], list[Fact]]:
    existing_texts = {f.text: f for f in existing}
    rederived_texts = {f.text: f for f in rederived}
    matching_texts = sorted(set(existing_texts) & set(rederived_texts))
    missing_from_existing = [
        rederived_texts[text] for text in sorted(set(rederived_texts) - set(existing_texts))
    ]
    missing_from_rederived = [
        existing_texts[text] for text in sorted(set(existing_texts) - set(rederived_texts))
    ]
    return len(matching_texts), missing_from_existing, missing_from_rederived


def materialize_rederive_correction_branch(
    repo_dir: Path,
    *,
    added_facts: list[Fact],
    tombstoned_facts: list[Fact],
    session_ids: list[str] | None = None,
    config: UMXConfig | None = None,
) -> tuple[object | None, str | None]:
    if not added_facts and not tombstoned_facts:
        return None, None
    current_branch = git_current_branch(repo_dir)
    if current_branch != "main":
        return None, (
            "governed re-derive correction must run from main; current branch is "
            f"{current_branch or 'detached'}"
        )
    pending = changed_paths(repo_dir)
    if pending:
        pending_paths = ", ".join(path.relative_to(repo_dir).as_posix() for path in pending)
        return None, (
            "governed re-derive correction requires a clean working tree; "
            f"pending paths: {pending_paths}"
        )
    proposal = generate_rederive_correction_pr(
        added_facts=added_facts,
        tombstoned_facts=tombstoned_facts,
        repo_dir=repo_dir,
        session_ids=session_ids,
    )
    if not git_create_branch(repo_dir, proposal.branch):
        return None, (
            f"failed to create proposal branch {proposal.branch}; "
            "delete or rename the existing branch and retry"
        )
    committed = False
    restore_error: str | None = None
    try:
        for fact in added_facts:
            add_fact(repo_dir, fact, auto_commit=False)
        for fact in tombstoned_facts:
            removed = forget_fact(
                repo_dir,
                fact.fact_id,
                author="audit",
                reason=f"re-derive correction proposal for {fact.fact_id}",
            )
            if removed is None:
                return None, f"fact not found: {fact.fact_id}"
        commit_result = git_add_and_commit(
            repo_dir,
            message="umx: audit rederive correction",
            config=config or default_config(),
        )
        if commit_result.failed:
            return None, git_commit_failure_message(commit_result, context="commit failed")
        if commit_result.noop:
            return None, "no re-derive correction changes recorded"
        committed = True
        return proposal, None
    finally:
        if not committed:
            dirty_paths = changed_paths(repo_dir)
            git_reset_paths(repo_dir, dirty_paths)
            for path in dirty_paths:
                relative = path.relative_to(repo_dir).as_posix()
                if git_path_exists_at_ref(repo_dir, "HEAD", relative):
                    git_restore_path(repo_dir, "HEAD", relative)
                else:
                    path.unlink(missing_ok=True)
        if current_branch and not git_checkout(repo_dir, current_branch):
            restore_error = (
                f"proposal branch {proposal.branch} was created but the repo could not be restored "
                f"to {current_branch}; run `git checkout {current_branch}` manually"
            )
        if restore_error is not None:
            raise RuntimeError(restore_error)


def audit_report(repo_dir: Path, config: UMXConfig) -> dict:
    """Full audit: rederive all facts and compare.

    Returns report dict with statistics and divergences.
    """
    existing = load_all_facts(repo_dir, include_superseded=False)
    sessions = list(iter_session_payloads(repo_dir, include_archived=True))

    # Source type breakdown
    source_types: dict[str, int] = {}
    for fact in existing:
        key = fact.source_type.value
        source_types[key] = source_types.get(key, 0) + 1

    # Provenance stats
    sessions_with_facts: set[str] = set()
    for fact in existing:
        if fact.source_session and fact.source_session != "manual":
            sessions_with_facts.add(fact.source_session)

    report: dict = {
        "total_facts": len(existing),
        "total_sessions": len(sessions),
        "sessions_with_derived_facts": len(sessions_with_facts),
        "source_types": source_types,
    }
    return report


def _fact_summary(fact: Fact) -> dict[str, str]:
    path = fact.file_path.as_posix() if fact.file_path is not None else ""
    return {
        "fact_id": fact.fact_id,
        "path": path,
        "source_session": fact.source_session,
        "text": fact.text,
        "topic": fact.topic,
    }
