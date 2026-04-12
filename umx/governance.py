from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from umx.conventions import ConventionSet, validate_fact
from umx.models import Fact


@dataclass(slots=True)
class PRProposal:
    title: str
    body: str
    branch: str
    labels: list[str]
    files_changed: list[str]


def branch_name_for_dream(tier: str, description: str) -> str:
    """Generate branch name: dream/l1/<date>-<description> or dream/l2/<date>-<description>"""
    date_str = datetime.now(tz=UTC).strftime("%Y%m%d")
    slug = description.lower().replace(" ", "-")[:40]
    return f"dream/{tier}/{date_str}-{slug}"


def classify_pr_labels(facts: list[Fact]) -> list[str]:
    """Classify facts into PR label categories per spec §12."""
    labels: set[str] = set()

    has_consolidation = any(f.source_type.value == "dream_consolidation" for f in facts)
    has_gap_fill = any("gap" in (f.source_tool or "") for f in facts)
    has_extraction = any(f.source_type.value in ("user_prompt", "tool_output", "ground_truth_code") for f in facts)

    if has_consolidation:
        labels.add("type:consolidation")
    if has_gap_fill:
        labels.add("type:gap-fill")
    if has_extraction:
        labels.add("type:extraction")
    if not labels:
        labels.add("type:extraction")

    confidences = [f.confidence for f in facts]
    avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0
    if avg_confidence >= 0.8:
        labels.add("confidence:high")
    elif avg_confidence >= 0.5:
        labels.add("confidence:medium")
    else:
        labels.add("confidence:low")

    scopes = {f.scope.value for f in facts}
    global_scopes = {"user", "machine", "tool"}
    if scopes & global_scopes:
        labels.add("impact:global")
    else:
        labels.add("impact:local")

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

    body_lines = [
        "## Dream L1 Extraction",
        "",
        f"**Date:** {date_str}",
        f"**Source sessions:** {', '.join(session_ids)}",
        f"**Facts extracted:** {len(facts)}",
        f"**Encoding strength range:** {strength_range}",
        "",
        "### Facts",
        "",
    ]
    for fact in facts:
        body_lines.append(
            f"- `{fact.fact_id}` [{fact.topic}] (S:{fact.encoding_strength}, "
            f"C:{fact.confidence:.1f}) {fact.text}"
        )

    body_lines.extend([
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
        body="\n".join(body_lines),
        branch=branch,
        labels=labels,
        files_changed=files_changed,
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

    # Validate NEW facts against conventions (not existing ones)
    facts_to_validate = new_facts if new_facts is not None else existing_facts
    for fact in facts_to_validate:
        issues = validate_fact(fact, conventions)
        violations.extend(issues)

    labels = set(pr.labels)
    is_high_confidence = "confidence:high" in labels
    is_local = "impact:local" in labels
    is_global = "impact:global" in labels

    # Check for destructive operations (deletions of strong facts)
    has_strong_deletions = any(
        f.encoding_strength >= 3 and f.superseded_by is not None
        for f in existing_facts
    )

    # Check for contradictions
    has_contradictions = any(
        bool(f.conflicts_with) for f in existing_facts
    )

    # Reject if convention violations found
    if violations:
        return {
            "action": "reject",
            "reason": f"Convention violations: {'; '.join(violations)}",
            "violations": violations,
        }

    # Escalate conditions
    if is_global or has_strong_deletions or has_contradictions:
        reasons = []
        if is_global:
            reasons.append("impact:global")
        if has_strong_deletions:
            reasons.append("deletions of S:>=3 facts")
        if has_contradictions:
            reasons.append("contradictions detected")
        return {
            "action": "escalate",
            "reason": f"Requires human review: {', '.join(reasons)}",
            "violations": [],
        }

    # Auto-merge: confidence:high + impact:local + non-destructive
    if is_high_confidence and is_local:
        return {
            "action": "approve",
            "reason": "Auto-approved: high confidence, local impact, non-destructive",
            "violations": [],
        }

    # Default: escalate when conditions don't clearly match
    return {
        "action": "escalate",
        "reason": "Does not meet auto-merge criteria",
        "violations": [],
    }
