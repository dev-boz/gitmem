from __future__ import annotations

import json
from collections import defaultdict
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

    Uses a normalized semantic fingerprint so metadata-only drift is preserved.
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
    existing_by_key = _facts_grouped_by_audit_key(existing)
    rederived_by_key = _facts_grouped_by_audit_key(rederived)
    matching = 0
    missing_from_existing: list[Fact] = []
    missing_from_rederived: list[Fact] = []

    for key in sorted(set(existing_by_key) | set(rederived_by_key)):
        current_existing = existing_by_key.get(key, [])
        current_rederived = rederived_by_key.get(key, [])
        shared = min(len(current_existing), len(current_rederived))
        matching += shared
        missing_from_existing.extend(current_rederived[shared:])
        missing_from_rederived.extend(current_existing[shared:])
    return matching, missing_from_existing, missing_from_rederived


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
        "scope": fact.scope.value,
        "text": fact.text,
        "topic": fact.topic,
    }


def _audit_comparison_payload(fact: Fact) -> dict[str, object]:
    payload = fact.to_dict()
    for key in (
        "fact_id",
        "created",
        "last_retrieved",
        "last_referenced",
        "expires_at",
        "repo",
        "file_path",
        "task_status",
        "consolidation_status",
        "supersedes",
        "superseded_by",
        "corroborated_by_facts",
        "conflicts_with",
    ):
        payload.pop(key, None)
    payload["tags"] = sorted(str(tag) for tag in payload.get("tags", []))
    payload["corroborated_by_tools"] = sorted(
        str(tool) for tool in payload.get("corroborated_by_tools", [])
    )
    provenance = payload.get("provenance")
    if isinstance(provenance, dict):
        cleaned_provenance = dict(provenance)
        for key in ("approved_by", "approval_tier", "pr"):
            cleaned_provenance.pop(key, None)
        sessions = cleaned_provenance.get("sessions")
        if isinstance(sessions, list):
            cleaned_provenance["sessions"] = sorted(str(session) for session in sessions)
        payload["provenance"] = cleaned_provenance
    return payload


def _audit_fact_key(fact: Fact) -> str:
    return json.dumps(_audit_comparison_payload(fact), sort_keys=True, separators=(",", ":"))


def _sorted_facts_for_audit(facts: list[Fact]) -> list[Fact]:
    return sorted(
        facts,
        key=lambda fact: (
            _audit_fact_key(fact),
            fact.topic,
            fact.text,
            fact.fact_id,
        ),
    )


def _facts_grouped_by_audit_key(facts: list[Fact]) -> dict[str, list[Fact]]:
    grouped: dict[str, list[Fact]] = defaultdict(list)
    for fact in _sorted_facts_for_audit(facts):
        grouped[_audit_fact_key(fact)].append(fact)
    return grouped
