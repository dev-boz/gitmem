from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from umx.conventions import ConventionSet, validate_fact
from umx.models import SourceType
from umx.dream.conflict import facts_conflict
from umx.models import Fact


def generate_lint_findings(
    facts: list[Fact],
    *,
    conventions: ConventionSet,
    project_root: Path,
) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    reverify_cutoff = datetime.now(tz=UTC) - timedelta(days=90)
    by_id = {fact.fact_id: fact for fact in facts}
    for fact in facts:
        for issue in validate_fact(fact, conventions, all_facts=facts):
            findings.append({"kind": "convention", "message": f"{fact.fact_id}: {issue}"})
        for ref in fact.corroborated_by_facts + fact.conflicts_with + [
            value for value in [fact.supersedes, fact.superseded_by] if value
        ]:
            if ref not in by_id:
                findings.append({"kind": "orphan-id", "message": f"{fact.fact_id} references missing id {ref}"})
        if fact.code_anchor and not (project_root / fact.code_anchor.path).exists():
            findings.append({"kind": "stale-reference", "message": f"{fact.fact_id} points to missing path {fact.code_anchor.path}"})
        if (
            fact.encoding_strength >= 4
            and fact.source_type != SourceType.GROUND_TRUTH_CODE
            and fact.created <= reverify_cutoff
        ):
            findings.append(
                {
                    "kind": "reverify",
                    "message": f"{fact.fact_id} has not been re-grounded to code in over 90 days",
                }
            )
    active = [fact for fact in facts if fact.superseded_by is None]
    for index, left in enumerate(active):
        for right in active[index + 1 :]:
            if facts_conflict(left, right) and right.fact_id not in left.conflicts_with:
                findings.append(
                    {
                        "kind": "contradiction",
                        "message": f"{left.fact_id} and {right.fact_id} appear contradictory",
                    }
                )
    return findings


def write_lint_report(repo_dir: Path, findings: list[dict[str, str]]) -> Path:
    path = repo_dir / "meta" / "lint-report.md"
    lines = ["# Lint Report", ""]
    if not findings:
        lines.append("No findings.")
    else:
        for finding in findings:
            lines.append(f"- **{finding['kind']}** {finding['message']}")
    path.write_text("\n".join(lines) + "\n")
    return path
