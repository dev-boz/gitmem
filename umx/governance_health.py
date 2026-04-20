from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from umx.config import UMXConfig, load_config
from umx.dream.pr_render import GovernancePRBodyError, touched_fact_ids_from_fact_delta
from umx.dream.processing import read_processing_log
from umx.github_ops import GitHubError, list_open_pull_requests, resolve_repo_ref
from umx.git_ops import git_ref_exists, list_local_branches
from umx.governance import (
    GOVERNED_MODES,
    GOVERNANCE_LIFECYCLE_LABELS,
    LABEL_HUMAN_REVIEW,
    LABEL_STATE_APPROVED,
    LABEL_STATE_EXTRACTION,
    assert_governance_pr_body,
    pr_body_requires_fact_delta,
)
from umx.models import parse_datetime
from umx.scope import config_path, project_memory_dir

_GOVERNANCE_BRANCH_PREFIXES = ("dream/", "proposal/")
_DEFAULT_STALE_BRANCH_DAYS = 7


def _resolved_config(config: UMXConfig | None = None) -> UMXConfig:
    return config if config is not None else load_config(config_path())


def _record_timestamp(record: dict[str, Any]) -> datetime:
    stamp = parse_datetime(str(record.get("ts") or ""))
    if stamp is not None:
        return stamp
    return datetime.min.replace(tzinfo=UTC)


def _governance_open_prs(
    repo_dir: Path,
    config: UMXConfig,
) -> list[dict[str, Any]]:
    repo_ref = resolve_repo_ref(repo_dir, config_org=config.org)
    if repo_ref.owner is None:
        raise GitHubError("governance health requires a GitHub owner (set org or origin remote)")
    governance_prs: list[dict[str, Any]] = []
    for summary in list_open_pull_requests(repo_ref.owner, repo_ref.name):
        if not pr_body_requires_fact_delta(list(summary.labels), branch=summary.head_ref):
            continue
        labels = sorted({label.strip() for label in summary.labels if label.strip()})
        payload: dict[str, Any] | None = None
        body_error: str | None = None
        try:
            payload = assert_governance_pr_body(summary.body, allow_legacy=True)
        except GovernancePRBodyError as exc:
            body_error = str(exc)
        lifecycle_labels = sorted(set(labels) & GOVERNANCE_LIFECYCLE_LABELS)
        governance_prs.append(
            {
                "number": summary.number,
                "title": summary.title,
                "url": summary.url,
                "head_ref": summary.head_ref,
                "labels": labels,
                "state": lifecycle_labels[0] if len(lifecycle_labels) == 1 else None,
                "human_review": LABEL_HUMAN_REVIEW in labels,
                "fact_ids": (
                    sorted(touched_fact_ids_from_fact_delta(payload))
                    if payload is not None
                    else []
                ),
                "body_error": body_error,
            }
        )
    return sorted(governance_prs, key=lambda item: int(item["number"]))


def _stale_local_branches(
    repo_dir: Path,
    open_prs: list[dict[str, Any]],
    *,
    stale_branch_days: int,
) -> list[dict[str, Any]]:
    now = datetime.now(tz=UTC)
    stale_before = now - timedelta(days=stale_branch_days)
    open_heads = {str(item.get("head_ref") or "") for item in open_prs}
    stale: list[dict[str, Any]] = []
    for branch in list_local_branches(repo_dir):
        if not branch.name.startswith(_GOVERNANCE_BRANCH_PREFIXES):
            continue
        if branch.name in open_heads:
            continue
        stamp = parse_datetime(str(branch.last_commit_ts or ""))
        if stamp is None or stamp > stale_before:
            continue
        age_days = max(0, int((now - stamp).total_seconds() // 86400))
        stale.append(
            {
                "name": branch.name,
                "head": branch.head,
                "last_commit_ts": branch.last_commit_ts,
                "age_days": age_days,
                "current": branch.current,
                "upstream": branch.upstream,
            }
        )
    return sorted(stale, key=lambda item: (-int(item["age_days"]), str(item["name"])))


def _label_drift(open_prs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    drift: list[dict[str, Any]] = []
    for pr in open_prs:
        labels = {str(label) for label in pr.get("labels", [])}
        lifecycle = sorted(labels & GOVERNANCE_LIFECYCLE_LABELS)
        confidence = sorted(label for label in labels if label.startswith("confidence:"))
        impact = sorted(label for label in labels if label.startswith("impact:"))
        type_labels = sorted(label for label in labels if label.startswith("type:"))

        issues: list[str] = []
        if not lifecycle:
            issues.append("missing lifecycle label")
        elif len(lifecycle) > 1:
            issues.append(f"multiple lifecycle labels: {', '.join(lifecycle)}")
        if not confidence:
            issues.append("missing confidence label")
        elif len(confidence) > 1:
            issues.append(f"multiple confidence labels: {', '.join(confidence)}")
        if not impact:
            issues.append("missing impact label")
        elif len(impact) > 1:
            issues.append(f"multiple impact labels: {', '.join(impact)}")
        if not type_labels:
            issues.append("missing type label")
        if LABEL_STATE_APPROVED in labels and LABEL_HUMAN_REVIEW in labels:
            issues.append("approved PR still carries human-review")
        if issues:
            drift.append(
                {
                    "number": pr.get("number"),
                    "title": pr.get("title"),
                    "url": pr.get("url"),
                    "head_ref": pr.get("head_ref"),
                    "labels": sorted(labels),
                    "issues": issues,
                }
            )
    return drift


def _processing_records(repo_dir: Path) -> list[dict[str, Any]]:
    records = read_processing_log(repo_dir)
    seen = {json.dumps(record, sort_keys=True) for record in records}
    if git_ref_exists(repo_dir, "origin/main"):
        for record in read_processing_log(repo_dir, ref="origin/main"):
            key = json.dumps(record, sort_keys=True)
            if key in seen:
                continue
            seen.add(key)
            records.append(record)
    return records


def _last_l2_review(repo_dir: Path) -> dict[str, Any] | None:
    review_records = [
        record
        for record in _processing_records(repo_dir)
        if record.get("tier") == "l2" and record.get("event") == "review_completed"
    ]
    if not review_records:
        return None
    latest = max(review_records, key=_record_timestamp)
    return {
        "ts": latest.get("ts"),
        "status": latest.get("status"),
        "action": latest.get("action"),
        "pr_number": latest.get("pr_number"),
        "reviewed_by": latest.get("reviewed_by"),
        "review_model": latest.get("review_model"),
        "merge_blocked": latest.get("merge_blocked"),
    }


def build_governance_health_payload(
    cwd: Path,
    config: UMXConfig | None = None,
    *,
    stale_branch_days: int = _DEFAULT_STALE_BRANCH_DAYS,
) -> dict[str, Any]:
    repo_dir = project_memory_dir(cwd)
    cfg = _resolved_config(config)
    mode = str(cfg.dream.mode or "local")
    governed = mode in GOVERNED_MODES
    open_prs: list[dict[str, Any]] = []
    errors: list[str] = []
    pr_inventory_available = False
    if governed:
        try:
            open_prs = _governance_open_prs(repo_dir, cfg)
            pr_inventory_available = True
        except GitHubError as exc:
            errors.append(str(exc))

    stale_branches = _stale_local_branches(
        repo_dir,
        open_prs,
        stale_branch_days=stale_branch_days,
    ) if governed and pr_inventory_available else []
    last_l2_review = _last_l2_review(repo_dir) if governed else None
    label_drift = _label_drift(open_prs) if governed and pr_inventory_available else []
    body_errors = [
        f"PR #{pr.get('number')} {pr.get('head_ref')}: {pr.get('body_error')}"
        for pr in open_prs
        if pr.get("body_error")
    ]
    errors.extend(body_errors)

    reviewer_queue_depth = sum(
        1 for pr in open_prs if pr.get("state") == LABEL_STATE_EXTRACTION
    )
    human_review_queue_depth = sum(
        1
        for pr in open_prs
        if pr.get("human_review")
        and pr.get("state") is not None
        and pr.get("state") != LABEL_STATE_APPROVED
    )

    summary = {
        "open_governance_prs": len(open_prs),
        "reviewer_queue_depth": reviewer_queue_depth,
        "human_review_queue_depth": human_review_queue_depth,
        "stale_branch_count": len(stale_branches),
        "label_drift_count": len(label_drift),
        "stale_branch_days": stale_branch_days,
        "pr_inventory_available": pr_inventory_available or not governed,
    }

    flags: list[str] = []
    if governed and reviewer_queue_depth:
        flags.append(f"{reviewer_queue_depth} governance PR(s) awaiting L2 review")
    if governed and human_review_queue_depth:
        flags.append(f"{human_review_queue_depth} governance PR(s) awaiting human review")
    if governed and stale_branches:
        flags.append(
            f"{len(stale_branches)} stale local governance branch(es) older than "
            f"{stale_branch_days}d without an open PR"
        )
    if governed and label_drift:
        flags.append(f"{len(label_drift)} open governance PR(s) have label drift")
    if governed and body_errors:
        flags.append(
            f"{len(body_errors)} open governance PR(s) have invalid fact-delta bodies"
        )

    return {
        "repo": str(repo_dir),
        "mode": mode,
        "governed": governed,
        "ok": governed is False or (not flags and not errors),
        "flags": flags,
        "errors": errors,
        "summary": summary,
        "open_prs": open_prs,
        "stale_branches": stale_branches,
        "last_l2_review": last_l2_review,
        "label_drift": label_drift,
    }


def render_governance_health_human(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    pr_inventory_available = bool(summary.get("pr_inventory_available", True))
    open_pr_count = summary.get("open_governance_prs", 0) if pr_inventory_available else "unknown"
    reviewer_queue = summary.get("reviewer_queue_depth", 0) if pr_inventory_available else "unknown"
    human_queue = (
        summary.get("human_review_queue_depth", 0) if pr_inventory_available else "unknown"
    )
    stale_branch_count = summary.get("stale_branch_count", 0) if pr_inventory_available else "unknown"
    label_drift_count = summary.get("label_drift_count", 0) if pr_inventory_available else "unknown"
    lines = [
        f"Governance health: {'ok' if payload.get('ok') else 'warn'}",
        f"Repo: {payload.get('repo', '')}",
        f"Mode: {payload.get('mode', 'unknown')}",
    ]
    if not payload.get("governed"):
        lines.append(f"Governance mode: inactive ({payload.get('mode', 'unknown')})")
        return "\n".join(lines)

    lines.extend(
        [
            f"Open governance PRs: {open_pr_count}",
            (
                "Reviewer queue: "
                f"{reviewer_queue} awaiting L2, "
                f"{human_queue} awaiting human review"
            ),
            (
                "Stale local branches: "
                f"{stale_branch_count} "
                f"(>{summary.get('stale_branch_days', _DEFAULT_STALE_BRANCH_DAYS)}d without open PR)"
            ),
            f"Label drift: {label_drift_count}",
        ]
    )
    if not pr_inventory_available:
        lines.append("Open PR inventory: unavailable")

    last_l2_review = payload.get("last_l2_review")
    if last_l2_review:
        pr_number = last_l2_review.get("pr_number")
        pr_suffix = f" · PR #{pr_number}" if pr_number is not None else ""
        lines.append(
            "Last L2 review: "
            f"{last_l2_review.get('ts')} · {last_l2_review.get('action', 'unknown')}"
            f"{pr_suffix}"
        )
    else:
        lines.append("Last L2 review: none")

    flags = payload.get("flags") or []
    if flags:
        lines.append("")
        lines.append("Flags:")
        lines.extend(f"- {flag}" for flag in flags)

    errors = payload.get("errors") or []
    if errors:
        lines.append("")
        lines.append("Errors:")
        lines.extend(f"- {item}" for item in errors)

    open_prs = payload.get("open_prs") or []
    if open_prs:
        lines.append("")
        lines.append("Open governance PRs:")
        for item in open_prs:
            state = item.get("state") or "unknown"
            lines.append(
                f"- #{item.get('number')} {item.get('head_ref')} [{state}] "
                f"{item.get('title')}"
            )

    stale_branches = payload.get("stale_branches") or []
    if stale_branches:
        lines.append("")
        lines.append("Stale local branches:")
        for item in stale_branches:
            lines.append(
                f"- {item.get('name')} ({item.get('age_days')}d old; "
                f"last commit {item.get('last_commit_ts')})"
            )

    label_drift = payload.get("label_drift") or []
    if label_drift:
        lines.append("")
        lines.append("Label drift:")
        for item in label_drift:
            lines.append(
                f"- #{item.get('number')} {item.get('head_ref')}: "
                + "; ".join(str(issue) for issue in item.get("issues", []))
            )

    return "\n".join(lines)
