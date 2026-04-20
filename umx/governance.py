from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
import re
from typing import TYPE_CHECKING, Any

from umx.dream.pr_render import (
    GovernancePRBodyError,
    assert_governance_pr_body as assert_rendered_governance_pr_body,
    build_fact_delta_for_promotion,
    build_fact_delta_from_facts,
    build_fact_delta_for_tombstones,
    render_governance_pr_body,
)
from umx.conventions import ConventionSet, validate_fact
from umx.memory import target_path_for_fact
from umx.models import Fact

if TYPE_CHECKING:
    from umx.cross_project import CrossProjectCandidate

REVIEW_ACTION_APPROVE = "approve"
REVIEW_ACTION_REJECT = "reject"
REVIEW_ACTION_ESCALATE = "escalate"

LABEL_TYPE_EXTRACTION = "type: extraction"
LABEL_TYPE_CONSOLIDATION = "type: consolidation"
LABEL_TYPE_DELETION = "type: deletion"
LABEL_TYPE_PROMOTION = "type: promotion"
LABEL_TYPE_HYPOTHESIS = "type: hypothesis"
LABEL_TYPE_GAP_FILL = "type: gap-fill"
LABEL_TYPE_LINT = "type: lint"
LABEL_TYPE_PRINCIPLE = "type: principle"
LABEL_TYPE_SUPERSESSION = "type: supersession"
LABEL_CONFIDENCE_HIGH = "confidence:high"
LABEL_CONFIDENCE_MEDIUM = "confidence:medium"
LABEL_CONFIDENCE_LOW = "confidence:low"
LABEL_IMPACT_LOCAL = "impact:local"
LABEL_IMPACT_GLOBAL = "impact:global"
LABEL_STATE_EXTRACTION = "state: extraction"
LABEL_STATE_REVIEWED = "state: reviewed"
LABEL_STATE_APPROVED = "state: approved"
LABEL_HUMAN_REVIEW = "human-review"
APPROVAL_REQUIRED_LABELS = frozenset({LABEL_STATE_APPROVED})

GOVERNANCE_REVIEW_TRIGGER_LABELS = (
    LABEL_STATE_EXTRACTION,
    LABEL_TYPE_EXTRACTION,
    LABEL_TYPE_CONSOLIDATION,
    LABEL_TYPE_DELETION,
    LABEL_TYPE_GAP_FILL,
    LABEL_TYPE_LINT,
    LABEL_TYPE_PROMOTION,
    LABEL_TYPE_PRINCIPLE,
    LABEL_TYPE_SUPERSESSION,
)

GOVERNANCE_PR_BODY_REQUIRED_LABELS = frozenset({
    *GOVERNANCE_REVIEW_TRIGGER_LABELS,
    LABEL_HUMAN_REVIEW,
})
GOVERNANCE_PR_BRANCH_PREFIXES = ("dream/", "proposal/")
GOVERNANCE_LIFECYCLE_LABELS = frozenset({
    LABEL_STATE_EXTRACTION,
    LABEL_STATE_REVIEWED,
    LABEL_STATE_APPROVED,
})

GOVERNANCE_LABEL_SPECS: dict[str, tuple[str, str]] = {
    LABEL_TYPE_EXTRACTION: ("1f6feb", "L1 extracted fact proposal"),
    LABEL_TYPE_CONSOLIDATION: ("0e8a16", "Dream consolidation change"),
    LABEL_TYPE_DELETION: ("d73a4a", "Removes an existing fact"),
    LABEL_TYPE_PROMOTION: ("5319e7", "Promotes memory across scopes"),
    LABEL_TYPE_HYPOTHESIS: ("fbca04", "Experimental memory branch"),
    LABEL_TYPE_GAP_FILL: ("0052cc", "Query-gap follow-up proposal"),
    LABEL_TYPE_LINT: ("c5def5", "Lint or convention cleanup"),
    LABEL_TYPE_PRINCIPLE: ("7f52ff", "Touches principles/ memory"),
    LABEL_TYPE_SUPERSESSION: ("b60205", "Explicit supersession chain"),
    LABEL_CONFIDENCE_HIGH: ("0e8a16", "High-confidence proposal"),
    LABEL_CONFIDENCE_MEDIUM: ("fbca04", "Medium-confidence proposal"),
    LABEL_CONFIDENCE_LOW: ("d73a4a", "Low-confidence proposal"),
    LABEL_IMPACT_LOCAL: ("1d76db", "Project-local impact"),
    LABEL_IMPACT_GLOBAL: ("b60205", "Cross-project or global impact"),
    LABEL_STATE_EXTRACTION: ("1f6feb", "Awaiting automated L2 review"),
    LABEL_STATE_REVIEWED: ("5319e7", "L2 review completed"),
    LABEL_STATE_APPROVED: ("0e8a16", "Approved for merge by human review"),
    LABEL_HUMAN_REVIEW: ("5319e7", "Requires L3 human review"),
}
GOVERNANCE_MANAGED_LABELS = frozenset(GOVERNANCE_LABEL_SPECS)

GOVERNED_MODES = frozenset({"remote", "hybrid"})
GOVERNED_FACT_PREFIXES = ("facts/", "episodic/", "principles/")
GOVERNED_FACT_FILES = frozenset({"MEMORY.md", "meta/tombstones.jsonl"})
SESSION_PREFIX = "sessions/"
OPERATIONAL_SYNC_FILES = frozenset({"meta/processing.jsonl"})


@dataclass(slots=True)
class PRProposal:
    title: str
    body: str
    branch: str
    labels: list[str]
    files_changed: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "body": self.body,
            "branch": self.branch,
            "labels": list(self.labels),
            "files_changed": list(self.files_changed),
        }


@dataclass(slots=True, frozen=True)
class GovernancePROverlap:
    pr_number: int
    pr_url: str
    pr_title: str
    head_ref: str
    overlapping_fact_ids: tuple[str, ...]


class GovernancePRConflictError(RuntimeError):
    def __init__(self, overlaps: list[GovernancePROverlap] | tuple[GovernancePROverlap, ...]) -> None:
        ordered = tuple(sorted(overlaps, key=lambda item: item.pr_number))
        lines = ["governed fact change overlaps an open governance PR:"]
        for overlap in ordered:
            lines.append(
                f"- {', '.join(overlap.overlapping_fact_ids)} -> "
                f"PR #{overlap.pr_number} {overlap.pr_url}"
            )
        lines.append("Close, merge, or retarget the existing PR before opening a new one.")
        self.overlaps = ordered
        super().__init__("\n".join(lines))


def is_governed_mode(mode: str | None) -> bool:
    return mode in GOVERNED_MODES


def _repo_relative_path(path: Path | str, repo_dir: Path | None = None) -> str:
    candidate = Path(path)
    if repo_dir is not None:
        try:
            candidate = candidate.relative_to(repo_dir)
        except ValueError:
            pass
    return candidate.as_posix().lstrip("./")


def is_session_path(path: Path | str, *, repo_dir: Path | None = None) -> bool:
    return _repo_relative_path(path, repo_dir=repo_dir).startswith(SESSION_PREFIX)


def is_operational_sync_path(path: Path | str, *, repo_dir: Path | None = None) -> bool:
    relative = _repo_relative_path(path, repo_dir=repo_dir)
    return is_session_path(relative) or relative in OPERATIONAL_SYNC_FILES


def is_governed_fact_path(path: Path | str, *, repo_dir: Path | None = None) -> bool:
    relative = _repo_relative_path(path, repo_dir=repo_dir)
    return relative in GOVERNED_FACT_FILES or any(
        relative.startswith(prefix) for prefix in GOVERNED_FACT_PREFIXES
    )


def filter_governed_fact_paths(paths: list[Path], repo_dir: Path) -> list[Path]:
    return [path for path in paths if is_governed_fact_path(path, repo_dir=repo_dir)]


def filter_non_operational_sync_paths(paths: list[Path], repo_dir: Path) -> list[Path]:
    return [path for path in paths if not is_operational_sync_path(path, repo_dir=repo_dir)]


def format_repo_paths(repo_dir: Path, paths: list[Path], *, limit: int = 5) -> str:
    displayed = [
        _repo_relative_path(path, repo_dir=repo_dir)
        for path in paths[:limit]
    ]
    if len(paths) > limit:
        displayed.append(f"... (+{len(paths) - limit} more)")
    return ", ".join(displayed)


def direct_fact_write_error(mode: str, operation: str) -> str:
    return (
        f"{operation} writes governed fact state directly. "
        f"In {mode} mode, fact changes must go through Dream PR branches; "
        "switch to local mode for direct edits."
    )


def session_sync_error(mode: str, repo_dir: Path, paths: list[Path]) -> str:
    return (
        f"{mode} mode sync only pushes session history and coordination state to main; "
        f"pending other paths must be handled separately: "
        f"{format_repo_paths(repo_dir, paths)}"
    )


def review_audit_note(action: str, pr_number: int, reason: str) -> str:
    verb = {
        REVIEW_ACTION_APPROVE: "approved",
        REVIEW_ACTION_REJECT: "rejected",
        REVIEW_ACTION_ESCALATE: "escalated",
    }.get(action, action)
    return f"L2 review {verb} PR #{pr_number}: {reason}"


def approval_gate_missing_labels(labels: list[str] | tuple[str, ...]) -> list[str]:
    return sorted(APPROVAL_REQUIRED_LABELS - set(labels))


def approval_gate_satisfied(labels: list[str] | tuple[str, ...]) -> bool:
    return not approval_gate_missing_labels(labels)


def approval_override_audit_note(pr_number: int, reason: str) -> str:
    return f"<!-- umx:approval-override -->\nApproval gate override for PR #{pr_number}: {reason}"


def pr_body_requires_fact_delta(
    labels: list[str] | None = None,
    *,
    branch: str | None = None,
) -> bool:
    if branch is not None and any(branch.startswith(prefix) for prefix in GOVERNANCE_PR_BRANCH_PREFIXES):
        return True
    return bool(set(labels or ()) & GOVERNANCE_PR_BODY_REQUIRED_LABELS)


def assert_governance_pr_body(body: str, *, allow_legacy: bool = False) -> dict[str, Any] | None:
    return assert_rendered_governance_pr_body(body, allow_legacy=allow_legacy)


def desired_governance_labels(
    base_labels: list[str] | tuple[str, ...],
    *,
    lifecycle_label: str,
    human_review: bool | None = None,
) -> list[str]:
    if lifecycle_label not in GOVERNANCE_LIFECYCLE_LABELS:
        raise ValueError(f"unknown governance lifecycle label: {lifecycle_label}")
    labels = set(base_labels)
    labels.difference_update(GOVERNANCE_LIFECYCLE_LABELS)
    labels.add(lifecycle_label)
    if human_review is True:
        labels.add(LABEL_HUMAN_REVIEW)
    elif human_review is False:
        labels.discard(LABEL_HUMAN_REVIEW)
    return sorted(labels)


def reconcile_governance_label_set(
    current_labels: list[str] | tuple[str, ...],
    desired_labels: list[str] | tuple[str, ...],
) -> tuple[list[str], list[str]]:
    current = set(current_labels)
    desired = set(desired_labels)
    add = sorted(desired - current)
    remove = sorted((current - desired) & GOVERNANCE_MANAGED_LABELS)
    return add, remove


def _slugify_branch_description(description: str, *, limit: int = 40) -> str:
    return re.sub(r"[^a-z0-9]+", "-", description.lower()).strip("-")[:limit] or "change"


def branch_name_for_dream(tier: str, description: str) -> str:
    """Generate branch name: dream/l1/<date>-<description> or dream/l2/<date>-<description>"""
    date_str = datetime.now(tz=UTC).strftime("%Y%m%d")
    slug = _slugify_branch_description(description)
    return f"dream/{tier}/{date_str}-{slug}"


def branch_name_for_proposal(description: str) -> str:
    """Generate branch name: proposal/<slug>."""
    return f"proposal/{_slugify_branch_description(description, limit=60)}"


def classify_pr_labels(facts: list[Fact]) -> list[str]:
    """Classify facts into PR label categories per spec §12."""
    labels: set[str] = set()

    def _is_cross_project_promotion(fact: Fact) -> bool:
        extracted_by = getattr(fact.provenance, "extracted_by", "")
        return (
            fact.source_tool == "cross-project-promotion"
            or fact.source_session == "cross-project-promotion"
            or extracted_by == "cross-project-promotion"
        )

    has_principle = any(
        fact.file_path and "principles/topics/" in fact.file_path.as_posix()
        for fact in facts
    )
    has_promotion = any(_is_cross_project_promotion(f) for f in facts)
    has_consolidation = any(
        f.source_type.value == "dream_consolidation" and not _is_cross_project_promotion(f)
        for f in facts
    )
    has_gap_fill = any("gap" in (f.source_tool or "") for f in facts)
    has_extraction = any(f.source_type.value in ("user_prompt", "tool_output", "ground_truth_code") for f in facts)
    has_supersession = any(f.superseded_by is not None for f in facts)

    if has_principle:
        labels.add(LABEL_TYPE_PRINCIPLE)
    if has_promotion:
        labels.add(LABEL_TYPE_PROMOTION)
    if has_consolidation:
        labels.add(LABEL_TYPE_CONSOLIDATION)
    if has_gap_fill:
        labels.add(LABEL_TYPE_GAP_FILL)
    if has_supersession:
        labels.add(LABEL_TYPE_SUPERSESSION)
    if has_extraction:
        labels.add(LABEL_TYPE_EXTRACTION)
    if not labels:
        labels.add(LABEL_TYPE_EXTRACTION)

    confidences = [f.confidence for f in facts]
    avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0
    if avg_confidence >= 0.8:
        labels.add(LABEL_CONFIDENCE_HIGH)
    elif avg_confidence >= 0.5:
        labels.add(LABEL_CONFIDENCE_MEDIUM)
    else:
        labels.add(LABEL_CONFIDENCE_LOW)

    scopes = {f.scope.value for f in facts}
    global_scopes = {"user", "machine", "tool"}
    if scopes & global_scopes:
        labels.add(LABEL_IMPACT_GLOBAL)
    else:
        labels.add(LABEL_IMPACT_LOCAL)

    return sorted(labels)


def generate_l1_pr(
    facts: list[Fact],
    session_ids: list[str],
    repo_dir: Path,
) -> PRProposal:
    """Generate an L1 dream PR proposal."""
    date_str = datetime.now(tz=UTC).strftime("%Y-%m-%d")
    session_tag = session_ids[0][:8] if session_ids else "batch"
    title = f"[dream/l1] Extract facts from session {date_str}-{session_tag}"

    strengths = [f.encoding_strength for f in facts]
    strength_range = f"{min(strengths)}-{max(strengths)}" if strengths else "0-0"

    summary_lines = [
        f"**Date:** {date_str}",
        f"**Source sessions:** {', '.join(session_ids)}",
        f"**Facts extracted:** {len(facts)}",
        f"**Encoding strength range:** {strength_range}",
        "",
        "### Facts",
        "",
    ]
    for fact in facts:
        summary_lines.append(
            f"- `{fact.fact_id}` [{fact.topic}] (S:{fact.encoding_strength}, "
            f"C:{fact.confidence:.1f}) {fact.text}"
        )

    summary_lines.extend([
        "",
        "### Provenance",
        "",
        f"- Extracted by: dream/l1",
        f"- Approval tier: L1",
    ])

    labels = classify_pr_labels(facts)
    description = f"extract-{session_tag}"
    branch = branch_name_for_dream("l1", description)

    files_changed = sorted({
        f"facts/topics/{fact.topic}.md" for fact in facts
    })

    return PRProposal(
        title=title,
        body=render_governance_pr_body(
            heading="Dream L1 Extraction",
            summary_lines=summary_lines,
            fact_delta=build_fact_delta_from_facts(facts, repo_dir),
        ),
        branch=branch,
        labels=desired_governance_labels(labels, lifecycle_label=LABEL_STATE_EXTRACTION),
        files_changed=files_changed,
    )


def build_promotion_pr_proposal_preview(
    candidate: CrossProjectCandidate,
    *,
    target_topic: str,
    target_repo: Path,
    fact_id: str | None = None,
) -> PRProposal:
    _ = target_repo
    title_text = candidate.text.rstrip(".")
    if len(title_text) > 72:
        title_text = f"{title_text[:69].rstrip()}..."
    title = f"[promotion] Promote shared project fact to user memory: {title_text}"
    summary_lines = [
        "This is a read-only preview for promoting a repeated project fact into user memory.",
        "No branch, commit, push, or pull request has been created.",
        "",
        "### Candidate",
        "",
        f"- Key: `{candidate.key}`",
        f"- Text: {candidate.text}",
        f"- Seen in {candidate.repo_count} project repos: {', '.join(candidate.repos)}",
        "- Target repo: `user memory repo`",
        f"- Target topic: `{target_topic}`",
        f"- Target file: `facts/topics/{target_topic}.md`",
        "",
        "### Evidence",
        "",
    ]
    for occurrence in candidate.occurrences:
        evidence = (
            f"- `{occurrence.repo}` / `{occurrence.fact_id}` "
            f"[topic: `{occurrence.topic}`; strength: {occurrence.encoding_strength}; created: {occurrence.created}]"
        )
        if occurrence.file_path:
            evidence = f"{evidence} `{occurrence.file_path}`"
        summary_lines.append(evidence)

    labels = [
        LABEL_TYPE_PROMOTION,
        LABEL_CONFIDENCE_MEDIUM,
        LABEL_IMPACT_GLOBAL,
        LABEL_HUMAN_REVIEW,
    ]
    target_file = Path("facts").joinpath("topics", f"{target_topic}.md")
    return PRProposal(
        title=title,
        body=render_governance_pr_body(
            heading="Cross-project promotion proposal preview",
            summary_lines=summary_lines,
            fact_delta=build_fact_delta_for_promotion(
                topic=target_topic,
                path=target_file.as_posix(),
                summary=candidate.text,
                source_fact_ids=[occurrence.fact_id for occurrence in candidate.occurrences],
                fact_id=fact_id,
            ),
        ),
        branch=branch_name_for_proposal(candidate.key),
        labels=desired_governance_labels(
            labels,
            lifecycle_label=LABEL_STATE_EXTRACTION,
            human_review=True,
        ),
        files_changed=[
            target_file.as_posix(),
            target_file.with_suffix(".umx.json").as_posix(),
        ],
    )


def build_tombstone_pr_proposal(fact: Fact, repo_dir: Path) -> PRProposal:
    target_path = fact.file_path or target_path_for_fact(repo_dir, fact)
    relative_path = _repo_relative_path(target_path, repo_dir=repo_dir)
    summary_lines = [
        "This proposal records a governed tombstone for an existing fact.",
        "Main stays unchanged until the proposal branch is approved and merged.",
        "",
        "### Tombstone target",
        "",
        f"- Fact ID: `{fact.fact_id}`",
        f"- Topic: `{fact.topic}`",
        f"- File: `{relative_path}`",
        f"- Text: {fact.text}",
        "",
        "### Provenance",
        "",
        "- Proposed by: `umx forget --governed`",
    ]
    labels = [label for label in classify_pr_labels([fact]) if not label.startswith("type:")]
    labels.append(LABEL_TYPE_DELETION)
    title = f"[forget] Tombstone fact {fact.fact_id}"
    return PRProposal(
        title=title,
        body=render_governance_pr_body(
            heading="Governed fact tombstone proposal",
            summary_lines=summary_lines,
            fact_delta=build_fact_delta_for_tombstones([fact], repo_dir),
        ),
        branch=branch_name_for_proposal(f"forget-{fact.fact_id}"),
        labels=desired_governance_labels(
            labels,
            lifecycle_label=LABEL_STATE_EXTRACTION,
            human_review=True,
        ),
        files_changed=[
            relative_path,
            "meta/tombstones.jsonl",
        ],
    )


def generate_l2_review(
    pr: PRProposal,
    conventions: ConventionSet,
    existing_facts: list[Fact],
    new_facts: list[Fact] | None = None,
) -> dict:
    """Generate L2 review decision.

    Returns: {action: 'approve'|'reject'|'escalate', reason: str, violations: list}
    L2 auto-merges: confidence:high + impact:local + non-destructive
    L2 escalates: impact:global + principle rewrites + deletions of S:>=3 + contradictions
    """
    violations: list[str] = []

    # Validate only the facts being proposed to remain after review.
    # existing_facts are destructive prior facts (for example, true deletions).
    facts_to_validate = list(new_facts or [])
    for fact in facts_to_validate:
        issues = validate_fact(fact, conventions)
        violations.extend(issues)

    labels = set(pr.labels)
    is_high_confidence = LABEL_CONFIDENCE_HIGH in labels
    is_local = LABEL_IMPACT_LOCAL in labels
    is_global = LABEL_IMPACT_GLOBAL in labels
    is_principle = LABEL_TYPE_PRINCIPLE in labels
    has_deletion_label = LABEL_TYPE_DELETION in labels

    # Check for destructive operations (deletions of strong facts)
    has_strong_deletions = any(
        f.encoding_strength >= 3
        for f in existing_facts
    )

    # Check for contradictions
    has_contradictions = any(
        bool(f.conflicts_with) for f in (new_facts or [])
    )

    # Reject if convention violations found
    if violations:
        return {
            "action": REVIEW_ACTION_REJECT,
            "reason": f"Convention violations: {'; '.join(violations)}",
            "violations": violations,
        }

    # Escalate conditions
    if is_global or is_principle or has_deletion_label or has_strong_deletions or has_contradictions:
        reasons = []
        if is_global:
            reasons.append(LABEL_IMPACT_GLOBAL)
        if is_principle:
            reasons.append(LABEL_TYPE_PRINCIPLE)
        if has_deletion_label:
            reasons.append(LABEL_TYPE_DELETION)
        if has_strong_deletions:
            reasons.append("deletions of S:>=3 facts")
        if has_contradictions:
            reasons.append("contradictions detected")
        return {
            "action": REVIEW_ACTION_ESCALATE,
            "reason": f"Requires human review: {', '.join(reasons)}",
            "violations": [],
        }

    # Auto-merge: confidence:high + impact:local + non-destructive
    if is_high_confidence and is_local:
        return {
            "action": REVIEW_ACTION_APPROVE,
            "reason": "Auto-approved: high confidence, local impact, non-destructive",
            "violations": [],
        }

    # Default: escalate when conditions don't clearly match
    return {
        "action": REVIEW_ACTION_ESCALATE,
        "reason": "Does not meet auto-merge criteria",
        "violations": [],
    }
