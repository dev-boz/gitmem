from __future__ import annotations

from collections import Counter
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from umx.config import UMXConfig
from umx.governance import branch_name_for_proposal, build_promotion_pr_proposal_preview
from umx.identity import generate_fact_id
from umx.memory import append_fact_preserving_existing, cache_path_for, load_all_facts
from umx.models import (
    ConsolidationStatus,
    Fact,
    MemoryType,
    Provenance,
    Scope,
    SourceType,
    Verification,
    isoformat_z,
    utcnow,
)


_WHITESPACE_RE = re.compile(r"\s+")
_TRAILING_PUNCTUATION_RE = re.compile(r"[.!?,;:]+$")


@dataclass(slots=True, frozen=True)
class CrossProjectOccurrence:
    repo: str
    fact_id: str
    text: str
    topic: str
    created: str
    encoding_strength: int
    file_path: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo": self.repo,
            "fact_id": self.fact_id,
            "text": self.text,
            "topic": self.topic,
            "created": self.created,
            "encoding_strength": self.encoding_strength,
            "file_path": self.file_path,
        }


@dataclass(slots=True, frozen=True)
class CrossProjectCandidate:
    key: str
    text: str
    repo_count: int
    repos: list[str]
    eligible: bool
    already_in_user_repo: bool
    blocked_reasons: list[str]
    occurrences: list[CrossProjectOccurrence]

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "text": self.text,
            "repo_count": self.repo_count,
            "repos": list(self.repos),
            "eligible": self.eligible,
            "already_in_user_repo": self.already_in_user_repo,
            "blocked_reasons": list(self.blocked_reasons),
            "occurrences": [occurrence.to_dict() for occurrence in self.occurrences],
        }


def discover_project_repos(home: Path) -> list[Path]:
    projects_dir = home / "projects"
    if not projects_dir.exists():
        return []
    return sorted((path for path in projects_dir.iterdir() if path.is_dir()), key=lambda path: path.name)


def normalize_cross_project_key(fact: Fact | str) -> str:
    text = fact.text if isinstance(fact, Fact) else fact
    normalized = _WHITESPACE_RE.sub(" ", text.strip().lower())
    normalized = _TRAILING_PUNCTUATION_RE.sub("", normalized)
    return normalized.strip()


def _fact_qualifies(fact: Fact, *, min_strength: int, min_age_days: int) -> bool:
    if fact.scope != Scope.PROJECT:
        return False
    if fact.consolidation_status != ConsolidationStatus.STABLE:
        return False
    if fact.encoding_strength < min_strength:
        return False
    if fact.conflicts_with:
        return False
    if (utcnow() - fact.created).days < min_age_days:
        return False
    return True


def _build_occurrence(repo_dir: Path, fact: Fact) -> CrossProjectOccurrence:
    file_path = fact.file_path.relative_to(repo_dir).as_posix() if fact.file_path else None
    return CrossProjectOccurrence(
        repo=repo_dir.name,
        fact_id=fact.fact_id,
        text=fact.text.strip(),
        topic=fact.topic,
        created=isoformat_z(fact.created) or "",
        encoding_strength=fact.encoding_strength,
        file_path=file_path,
    )


def collect_promotion_candidates(
    home: Path,
    *,
    min_repos: int = 3,
    min_strength: int = 3,
    min_age_days: int = 7,
) -> list[CrossProjectCandidate]:
    grouped: dict[str, dict[str, CrossProjectOccurrence]] = {}
    ambiguous_repo_topics: set[str] = set()

    for repo_dir in discover_project_repos(home):
        repo_matches: dict[str, list[Fact]] = {}
        for fact in load_all_facts(
            repo_dir,
            include_superseded=False,
            normalize=False,
            use_cache=False,
        ):
            if not _fact_qualifies(fact, min_strength=min_strength, min_age_days=min_age_days):
                continue
            key = normalize_cross_project_key(fact)
            if not key:
                continue
            repo_matches.setdefault(key, []).append(fact)
        for key, repo_facts in sorted(repo_matches.items()):
            if len({fact.topic for fact in repo_facts if fact.topic}) > 1:
                ambiguous_repo_topics.add(key)
            representative = min(
                repo_facts,
                key=lambda fact: (
                    fact.created,
                    fact.fact_id,
                    fact.text.strip().lower(),
                ),
            )
            grouped.setdefault(key, {})[repo_dir.name] = _build_occurrence(repo_dir, representative)

    user_repo = home / "user"
    user_keys = (
        {
            key
            for fact in load_all_facts(
                user_repo,
                include_superseded=False,
                normalize=False,
                use_cache=False,
            )
            if fact.scope == Scope.USER and (key := normalize_cross_project_key(fact))
        }
        if user_repo.exists()
        else set()
    )

    candidates: list[CrossProjectCandidate] = []
    for key, repo_occurrences in sorted(grouped.items()):
        if len(repo_occurrences) < 2:
            continue
        occurrences = sorted(repo_occurrences.values(), key=lambda occurrence: (occurrence.repo, occurrence.fact_id))
        repos = [occurrence.repo for occurrence in occurrences]
        already_in_user_repo = key in user_keys
        blocked_reasons: list[str] = []
        if len(repos) < min_repos:
            blocked_reasons.append("below_min_repos")
        if already_in_user_repo:
            blocked_reasons.append("already_in_user_repo")
        if key in ambiguous_repo_topics:
            blocked_reasons.append("ambiguous_target_topic")
        candidate = CrossProjectCandidate(
            key=key,
            text=occurrences[0].text,
            repo_count=len(repos),
            repos=repos,
            eligible=False,
            already_in_user_repo=already_in_user_repo,
            blocked_reasons=list(blocked_reasons),
            occurrences=occurrences,
        )
        if "ambiguous_target_topic" not in blocked_reasons:
            _, topic_blocked_reasons = resolve_cross_project_target_topic(candidate)
            blocked_reasons.extend(
                reason for reason in topic_blocked_reasons if reason not in blocked_reasons
            )
        candidates.append(
            CrossProjectCandidate(
                key=key,
                text=occurrences[0].text,
                repo_count=len(repos),
                repos=repos,
                eligible=len(blocked_reasons) == 0,
                already_in_user_repo=already_in_user_repo,
                blocked_reasons=blocked_reasons,
                occurrences=occurrences,
            )
        )

    return sorted(candidates, key=lambda candidate: (-candidate.repo_count, candidate.key))


def cross_project_audit_report(
    home: Path,
    config: UMXConfig,
    *,
    min_repos: int = 3,
    min_strength: int = 3,
    min_age_days: int = 7,
) -> dict[str, Any]:
    _ = config
    repos = discover_project_repos(home)
    candidates = collect_promotion_candidates(
        home,
        min_repos=min_repos,
        min_strength=min_strength,
        min_age_days=min_age_days,
    )
    return {
        "mode": "cross_project",
        "repos_scanned": len(repos),
        "repos": [repo.name for repo in repos],
        "thresholds": {
            "min_repos": min_repos,
            "min_strength": min_strength,
            "min_age_days": min_age_days,
        },
        "candidate_count": len(candidates),
        "eligible_candidate_count": sum(1 for candidate in candidates if candidate.eligible),
        "candidates": [candidate.to_dict() for candidate in candidates],
    }


def find_promotion_candidate(
    candidates: list[CrossProjectCandidate],
    candidate_key: str,
) -> CrossProjectCandidate | None:
    normalized_key = normalize_cross_project_key(candidate_key)
    for candidate in candidates:
        if candidate.key == normalized_key:
            return candidate
    return None


def resolve_cross_project_target_topic(candidate: CrossProjectCandidate) -> tuple[str | None, list[str]]:
    topic_counts = Counter(occurrence.topic for occurrence in candidate.occurrences if occurrence.topic)
    if not topic_counts:
        return None, ["ambiguous_target_topic"]
    topic, count = topic_counts.most_common(1)[0]
    if count <= len(candidate.occurrences) / 2:
        return None, ["ambiguous_target_topic"]
    if sum(1 for seen_count in topic_counts.values() if seen_count == count) > 1:
        return None, ["ambiguous_target_topic"]
    return topic, []


def build_cross_project_promotion_report(
    home: Path,
    config: UMXConfig,
    *,
    candidate_key: str,
    min_repos: int = 3,
    min_strength: int = 3,
    min_age_days: int = 7,
) -> dict[str, Any]:
    _ = config
    repos = discover_project_repos(home)
    candidates = collect_promotion_candidates(
        home,
        min_repos=min_repos,
        min_strength=min_strength,
        min_age_days=min_age_days,
    )
    candidate = find_promotion_candidate(candidates, candidate_key)
    if candidate is None:
        raise LookupError(candidate_key)

    blocked_reasons = list(candidate.blocked_reasons)
    target_topic: str | None = None
    if "ambiguous_target_topic" not in blocked_reasons:
        target_topic, topic_blocked_reasons = resolve_cross_project_target_topic(candidate)
        blocked_reasons.extend(
            reason for reason in topic_blocked_reasons if reason not in blocked_reasons
        )
    candidate_payload = candidate.to_dict()
    if blocked_reasons != candidate.blocked_reasons:
        candidate_payload["blocked_reasons"] = blocked_reasons
        candidate_payload["eligible"] = len(blocked_reasons) == 0

    target_repo = home / "user"
    target = {
        "repo": target_repo.name,
        "path": target_repo.relative_to(home).as_posix(),
        "topic": target_topic,
        "file_path": f"facts/topics/{target_topic}.md" if target_topic else None,
    }

    proposal_ready = candidate.eligible and not blocked_reasons and target_topic is not None
    proposal = (
        build_promotion_pr_proposal_preview(
            candidate,
            target_topic=target_topic,
            target_repo=target_repo,
        ).to_dict()
        if proposal_ready and target_topic is not None
        else None
    )
    return {
        "mode": "cross_project_proposal",
        "repos_scanned": len(repos),
        "repos": [repo.name for repo in repos],
        "thresholds": {
            "min_repos": min_repos,
            "min_strength": min_strength,
            "min_age_days": min_age_days,
        },
        "proposal_ready": proposal_ready,
        "blocked_reasons": blocked_reasons,
        "candidate": candidate_payload,
        "target": target,
        "proposal": proposal,
    }


def build_cross_project_promotion_fact(report: dict[str, Any]) -> Fact:
    candidate = report["candidate"]
    target = report["target"]
    occurrences = list(candidate["occurrences"])
    target_topic = target["topic"]
    if not target_topic:
        raise ValueError("cross-project proposal target topic is not resolved")
    if not occurrences:
        raise ValueError("cross-project proposal candidate has no supporting occurrences")
    return Fact(
        fact_id=generate_fact_id(),
        text=candidate["text"],
        scope=Scope.USER,
        topic=target_topic,
        encoding_strength=min(int(occurrence["encoding_strength"]) for occurrence in occurrences),
        memory_type=MemoryType.EXPLICIT_SEMANTIC,
        verification=Verification.CORROBORATED,
        source_type=SourceType.DREAM_CONSOLIDATION,
        confidence=0.7,
        tags=["cross-project"],
        source_tool="cross-project-promotion",
        source_session="cross-project-promotion",
        corroborated_by_facts=[str(occurrence["fact_id"]) for occurrence in occurrences],
        consolidation_status=ConsolidationStatus.STABLE,
        provenance=Provenance(extracted_by="cross-project-promotion"),
        encoding_context={
            "cross_project_candidate_key": candidate["key"],
            "cross_project_repos": list(candidate["repos"]),
            "cross_project_occurrences": occurrences,
        },
    )


def _format_relative_paths(repo_dir: Path, paths: list[Path], *, limit: int = 5) -> str:
    rendered: list[str] = []
    for path in paths[:limit]:
        try:
            rendered.append(path.relative_to(repo_dir).as_posix())
        except ValueError:
            rendered.append(path.as_posix())
    if len(paths) > limit:
        rendered.append(f"... (+{len(paths) - limit} more)")
    return ", ".join(rendered)


def _cross_project_proposal_with_status(
    preview: dict[str, Any],
    *,
    summary_line: str,
    outcome_line: str,
) -> dict[str, Any]:
    proposal = preview.get("proposal")
    if not isinstance(proposal, dict):
        raise ValueError("cross-project proposal preview is missing proposal payload")
    updated_proposal = dict(proposal)
    body_lines = str(updated_proposal["body"]).splitlines()
    if len(body_lines) < 5:
        raise ValueError("cross-project proposal preview body is incomplete")
    body_lines[:4] = [
        "## Cross-project promotion proposal",
        "",
        summary_line,
        outcome_line,
    ]
    updated_proposal["body"] = "\n".join(body_lines)
    updated_preview = dict(preview)
    updated_preview["proposal"] = updated_proposal
    return updated_preview


def _candidate_from_payload(payload: dict[str, Any]) -> CrossProjectCandidate:
    return CrossProjectCandidate(
        key=str(payload["key"]),
        text=str(payload["text"]),
        repo_count=int(payload["repo_count"]),
        repos=[str(repo) for repo in payload["repos"]],
        eligible=bool(payload["eligible"]),
        already_in_user_repo=bool(payload["already_in_user_repo"]),
        blocked_reasons=[str(reason) for reason in payload["blocked_reasons"]],
        occurrences=[
            CrossProjectOccurrence(
                repo=str(occurrence["repo"]),
                fact_id=str(occurrence["fact_id"]),
                text=str(occurrence["text"]),
                topic=str(occurrence["topic"]),
                created=str(occurrence["created"]),
                encoding_strength=int(occurrence["encoding_strength"]),
                file_path=str(occurrence["file_path"]) if occurrence["file_path"] is not None else None,
            )
            for occurrence in payload["occurrences"]
        ],
    )


def _proposal_preview_from_report(
    report: dict[str, Any],
    *,
    home: Path,
    allow_blocked: bool = False,
) -> dict[str, Any]:
    proposal = report.get("proposal")
    if isinstance(proposal, dict):
        return report
    if not allow_blocked:
        blocked = ", ".join(report["blocked_reasons"]) or "proposal is not ready"
        raise RuntimeError(f"cross-project proposal is not ready: {blocked}")
    target_topic = report["target"]["topic"]
    if target_topic is None:
        blocked = ", ".join(report["blocked_reasons"]) or "proposal target topic is unresolved"
        raise RuntimeError(f"cross-project proposal cannot be reopened from the pushed branch: {blocked}")
    rebuilt = dict(report)
    rebuilt["proposal"] = build_promotion_pr_proposal_preview(
        _candidate_from_payload(report["candidate"]),
        target_topic=target_topic,
        target_repo=home / "user",
    ).to_dict()
    return rebuilt


def _require_cross_project_promotion_preview(
    home: Path,
    config: UMXConfig,
    *,
    candidate_key: str,
) -> dict[str, Any]:
    report = build_cross_project_promotion_report(
        home,
        config,
        candidate_key=candidate_key,
    )
    return _proposal_preview_from_report(report, home=home)


def materialize_cross_project_promotion_branch(
    home: Path,
    config: UMXConfig,
    *,
    candidate_key: str,
) -> dict[str, Any]:
    from umx.git_ops import (
        changed_paths,
        git_add_and_commit,
        git_checkout,
        git_commit_failure_message,
        git_create_branch,
        git_current_branch,
        git_delete_branch,
        git_path_exists_at_ref,
        git_reset_paths,
        git_ref_exists,
        git_restore_path,
        is_git_repo,
    )

    preview = _require_cross_project_promotion_preview(
        home,
        config,
        candidate_key=candidate_key,
    )
    user_repo = home / "user"
    if not user_repo.exists():
        raise RuntimeError("user repo is not initialized; run `umx init` first")
    if not is_git_repo(user_repo):
        raise RuntimeError("user repo is not a git repository")

    current_branch = git_current_branch(user_repo)
    if current_branch != "main":
        raise RuntimeError(
            f"user repo must be on main; current branch is {current_branch or 'detached'}"
        )

    pending = changed_paths(user_repo)
    if pending:
        raise RuntimeError(
            f"user repo has pending changes: {_format_relative_paths(user_repo, pending)}"
        )

    proposal_payload = preview["proposal"]
    branch = str(proposal_payload["branch"])
    if git_ref_exists(user_repo, f"refs/heads/{branch}"):
        raise RuntimeError(f"proposal branch already exists: {branch}")
    if not git_create_branch(user_repo, branch):
        raise RuntimeError(f"failed to create proposal branch: {branch}")

    target_file = str(preview["target"]["file_path"])
    touched_paths = [
        user_repo / target_file,
        cache_path_for(user_repo / target_file),
    ]
    commit_succeeded = False
    try:
        materialized_fact = build_cross_project_promotion_fact(preview)
        target_path = append_fact_preserving_existing(
            user_repo,
            materialized_fact,
        )
        materialized_target = target_path.relative_to(user_repo).as_posix()
        expected_target = target_file
        if materialized_target != expected_target:
            raise RuntimeError(
                f"materialized target file did not match preview: {materialized_target} != {expected_target}"
            )

        commit_result = git_add_and_commit(
            user_repo,
            paths=touched_paths,
            message="umx: materialize cross-project proposal",
            config=config,
        )
        if commit_result.failed:
            raise RuntimeError(
                git_commit_failure_message(commit_result, context="proposal commit failed")
            )
        if not commit_result.committed:
            raise RuntimeError("proposal commit produced no changes")
        commit_succeeded = True
    finally:
        cleanup_errors: list[str] = []
        if not commit_succeeded and touched_paths:
            for path in touched_paths:
                relative = path.relative_to(user_repo).as_posix()
                if git_path_exists_at_ref(user_repo, current_branch, relative):
                    if not git_restore_path(user_repo, current_branch, relative):
                        cleanup_errors.append(f"failed to restore user repo path: {relative}")
                else:
                    if not git_reset_paths(user_repo, [path]):
                        cleanup_errors.append(f"failed to reset user repo path: {relative}")
                    path.unlink(missing_ok=True)
        if not git_checkout(user_repo, current_branch):
            cleanup_errors.append(f"failed to restore user repo branch: {current_branch}")
        if not commit_succeeded:
            if not git_delete_branch(user_repo, branch, force=True):
                cleanup_errors.append(f"failed to delete proposal branch: {branch}")
        if cleanup_errors:
            raise RuntimeError("; ".join(cleanup_errors))

    materialized_preview = _cross_project_proposal_with_status(
        preview,
        summary_line="This proposal has been materialized locally into the user repo on a proposal branch.",
        outcome_line="A local proposal branch and commit have been created. No push or pull request has been created.",
    )

    return {
        "status": "ok",
        "mode": "cross_project_proposal_materialized",
        "branch": branch,
        "target_repo": str(user_repo),
        "target_file": target_file,
        "proposal": materialized_preview,
    }


def materialize_and_push_cross_project_promotion_branch(
    home: Path,
    config: UMXConfig,
    *,
    candidate_key: str,
) -> dict[str, Any]:
    from umx.git_ops import git_fetch, git_push, git_ref_exists, git_ref_sha, git_remote_url
    from umx.github_ops import redact_url_credentials
    from umx.push_safety import assert_push_safe

    materialized = materialize_cross_project_promotion_branch(
        home,
        config,
        candidate_key=candidate_key,
    )
    user_repo = Path(str(materialized["target_repo"]))
    branch = str(materialized["branch"])
    target_file = str(materialized["target_file"])
    main_ref = "refs/heads/main"
    remote_main_ref = "refs/remotes/origin/main"

    remote = git_remote_url(user_repo)
    if not remote:
        raise RuntimeError("user repo has no origin remote configured")
    display_remote = redact_url_credentials(remote) or remote
    if not git_fetch(user_repo):
        raise RuntimeError("failed to fetch user repo origin")
    if not git_ref_exists(user_repo, remote_main_ref):
        raise RuntimeError("user repo origin/main is missing; push main first")
    if git_ref_sha(user_repo, main_ref) != git_ref_sha(user_repo, remote_main_ref):
        raise RuntimeError("user repo main is not in sync with origin/main; push or pull main first")

    assert_push_safe(
        user_repo,
        base_ref=remote_main_ref,
        branch=branch,
        head_ref=branch,
        config=config,
    )
    if not git_push(user_repo, branch=branch, set_upstream=True):
        raise RuntimeError(f"failed to push proposal branch: {branch}")

    pushed_preview = _cross_project_proposal_with_status(
        materialized["proposal"],
        summary_line=(
            "This proposal has been materialized locally into the user repo on a proposal branch "
            "and pushed to the configured remote."
        ),
        outcome_line=(
            f"A local proposal branch and commit have been created and pushed to `{display_remote}`. "
            "No pull request has been created."
        ),
    )

    return {
        "status": "ok",
        "mode": "cross_project_proposal_pushed",
        "branch": branch,
        "target_repo": str(user_repo),
        "target_file": target_file,
        "remote": display_remote,
        "proposal": pushed_preview,
    }


def open_cross_project_promotion_pull_request(
    home: Path,
    config: UMXConfig,
    *,
    candidate_key: str,
) -> dict[str, Any]:
    from umx.git_ops import git_fetch, git_ref_exists, git_remote_url, is_git_repo
    from umx.github_ops import (
        create_pr,
        gh_available,
        is_github_repo_url,
        redact_url_credentials,
        resolve_repo_ref,
    )

    user_repo = home / "user"
    if not user_repo.exists():
        raise RuntimeError("user repo is not initialized; run `umx init` first")
    if not is_git_repo(user_repo):
        raise RuntimeError("user repo is not a git repository")

    remote = git_remote_url(user_repo)
    if not remote:
        raise RuntimeError("user repo has no origin remote configured")
    if not is_github_repo_url(remote):
        raise RuntimeError("user repo origin must be a GitHub remote to open a PR")
    display_remote = redact_url_credentials(remote) or remote
    if not git_fetch(user_repo):
        raise RuntimeError("failed to fetch user repo origin")
    branch = branch_name_for_proposal(candidate_key)
    if not git_ref_exists(user_repo, f"refs/remotes/origin/{branch}"):
        raise RuntimeError(f"proposal branch is not pushed to origin; run `umx propose --cross-project --proposal-key {candidate_key} --push` first")

    preview = _proposal_preview_from_report(
        build_cross_project_promotion_report(
            home,
            config,
            candidate_key=candidate_key,
        ),
        home=home,
        allow_blocked=True,
    )

    proposal_preview = _cross_project_proposal_with_status(
        preview,
        summary_line=(
            "This proposal has been materialized into the user repo, pushed to the configured remote, "
            "and is ready to open as a pull request."
        ),
        outcome_line="A pull request will be opened against `main` from the pushed proposal branch.",
    )
    proposal_payload = proposal_preview["proposal"]
    if not gh_available():
        raise RuntimeError("gh CLI is not available or not authenticated")

    repo_ref = resolve_repo_ref(user_repo, config_org=config.org)
    if not repo_ref.owner:
        raise RuntimeError("user repo GitHub owner could not be resolved for PR creation")

    pr_number = create_pr(
        repo_ref.owner,
        repo_ref.name,
        branch,
        str(proposal_payload["title"]),
        str(proposal_payload["body"]),
        labels=list(proposal_payload["labels"]),
    )
    if pr_number is None:
        raise RuntimeError(f"failed to create proposal pull request for branch: {branch}")

    pr_url = f"https://github.com/{repo_ref.owner}/{repo_ref.name}/pull/{pr_number}"
    opened_preview = _cross_project_proposal_with_status(
        preview,
        summary_line=(
            "This proposal has been materialized into the user repo, pushed to the configured remote, "
            "and opened as a pull request."
        ),
        outcome_line=f"Pull request #{pr_number} has been created: {pr_url}",
    )
    return {
        "status": "ok",
        "mode": "cross_project_proposal_pull_request_opened",
        "branch": branch,
        "target_repo": str(user_repo),
        "target_file": str(preview["target"]["file_path"]),
        "remote": display_remote,
        "pr_number": pr_number,
        "pull_request": {
            "number": pr_number,
            "url": pr_url,
            "base": "main",
            "head": branch,
            "repo": f"{repo_ref.owner}/{repo_ref.name}",
        },
        "proposal": opened_preview,
    }
