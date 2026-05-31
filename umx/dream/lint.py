from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from umx.conventions import ConventionSet, validate_fact
from umx.dream.anchors import code_anchor_status
from umx.dream.conflict import facts_conflict
from umx.models import Fact, SourceType, parse_datetime
from umx.procedures import load_all_procedures
from umx.scope import find_orphaned_scoped_memory
from umx.search_semantic import load_semantic_cache
from umx.skills import load_all_skills, resolve_skill


_LINT_INTERVALS = {
    "daily": timedelta(days=1),
    "weekly": timedelta(days=7),
    "never": None,
}


def _dream_cache(repo_dir: Path) -> dict[str, Any]:
    payload = load_semantic_cache(repo_dir)
    dream = payload.get("dream")
    if not isinstance(dream, dict):
        payload["dream"] = {}
    return payload


def lint_state_path(repo_dir: Path) -> Path:
    return repo_dir / "meta" / "lint-state.json"


def lint_report_path(repo_dir: Path) -> Path:
    return repo_dir / "meta" / "lint-report.md"


def read_last_lint(repo_dir: Path) -> datetime | None:
    path = lint_state_path(repo_dir)
    value: str | None = None
    if path.exists():
        try:
            payload = json.loads(path.read_text())
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            candidate = payload.get("last_lint")
            if isinstance(candidate, str):
                value = candidate
    if value is None:
        payload = _dream_cache(repo_dir)
        dream = payload.get("dream")
        if not isinstance(dream, dict):
            return None
        legacy = dream.get("last_lint")
        if isinstance(legacy, str):
            value = legacy
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    try:
        parsed = parse_datetime(value)
    except ValueError:
        return None
    if parsed is None or parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def mark_lint_complete(repo_dir: Path, when: datetime) -> None:
    path = lint_state_path(repo_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"last_lint": when.astimezone(UTC).isoformat().replace("+00:00", "Z")},
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


def should_run(
    repo_dir: Path,
    *,
    interval: str,
    force: bool = False,
    now: datetime | None = None,
) -> tuple[bool, str]:
    if force:
        return True, "forced"
    if interval not in _LINT_INTERVALS:
        raise ValueError(f"unsupported dream.lint_interval: {interval}")
    if interval == "never":
        return False, "configured-never"
    last_lint = read_last_lint(repo_dir)
    if last_lint is None:
        return True, "first-run"
    current = now or datetime.now(tz=UTC)
    cutoff = current - _LINT_INTERVALS[interval]
    if last_lint <= cutoff:
        return True, f"{interval}-due"
    return False, f"{interval}-not-due"


def schema_lock_in_findings(
    facts: list[Fact],
    *,
    conventions: ConventionSet,
) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    for fact in facts:
        if fact.topic in conventions.topics or len(fact.text.split()) < 5:
            continue
        findings.append(
            {
                "kind": "schema-lock-in",
                "message": f"{fact.fact_id} introduces unknown topic '{fact.topic}' with durable phrasing",
            }
        )
    return findings


def generate_lint_findings(
    facts: list[Fact],
    *,
    conventions: ConventionSet,
    repo_dir: Path,
    project_root: Path,
) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    reverify_cutoff = datetime.now(tz=UTC) - timedelta(days=90)
    by_id = {fact.fact_id: fact for fact in facts}
    for procedure in load_all_procedures(repo_dir):
        if procedure.triggers:
            continue
        target = procedure.procedure_id
        if procedure.file_path is not None:
            try:
                target = procedure.file_path.relative_to(repo_dir).as_posix()
            except ValueError:
                target = procedure.file_path.as_posix()
        findings.append(
            {
                "kind": "procedure-trigger",
                "message": f"{target} is missing required ## Triggers section",
            }
        )
    for skill in load_all_skills(repo_dir):
        resolution = resolve_skill(skill, repo_dir)
        target = skill.name
        if skill.file_path is not None:
            try:
                target = skill.file_path.relative_to(repo_dir).as_posix()
            except ValueError:
                target = skill.file_path.as_posix()
        for directive in resolution.unsupported_directives:
            findings.append(
                {
                    "kind": "skill-directive",
                    "message": f"{target} uses unsupported retrieval directive {directive}",
                }
            )
        for blocked_path in resolution.blocked_paths:
            findings.append(
                {
                    "kind": "skill-portability",
                    "message": f"{target} uses non-portable load target {blocked_path}",
                }
            )
    for orphan in find_orphaned_scoped_memory(repo_dir, project_root):
        findings.append(
            {
                "kind": "orphaned-scope",
                "message": (
                    f"{orphan.memory_path} targets missing {orphan.scope_kind} path "
                    f"{orphan.scoped_path}"
                ),
            }
        )
    for fact in facts:
        for issue in validate_fact(fact, conventions, all_facts=facts):
            findings.append({"kind": "convention", "message": f"{fact.fact_id}: {issue}"})
        for ref in fact.corroborated_by_facts + fact.conflicts_with + [
            value for value in [fact.supersedes, fact.superseded_by] if value
        ]:
            if ref not in by_id:
                findings.append({"kind": "orphan-id", "message": f"{fact.fact_id} references missing id {ref}"})
        anchor_status = code_anchor_status(project_root, fact)
        if fact.code_anchor and anchor_status == "missing":
            findings.append({"kind": "stale-reference", "message": f"{fact.fact_id} points to missing path {fact.code_anchor.path}"})
        elif fact.code_anchor and anchor_status == "drifted":
            findings.append({"kind": "stale-reference", "message": f"{fact.fact_id} points to stale path {fact.code_anchor.path}"})
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
    findings.extend(schema_lock_in_findings(facts, conventions=conventions))
    return findings


def write_lint_report(repo_dir: Path, findings: list[dict[str, str]]) -> Path:
    path = lint_report_path(repo_dir)
    lines = ["# Lint Report", ""]
    if not findings:
        lines.append("No findings.")
    else:
        for finding in findings:
            lines.append(f"- **{finding['kind']}** {finding['message']}")
    path.write_text("\n".join(lines) + "\n")
    return path
