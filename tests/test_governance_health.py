from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime, timedelta

from umx.config import default_config, save_config
from umx.dream.pr_render import (
    FACT_DELTA_BLOCK_VERSION,
    FACT_DELTA_END_MARKER,
    FACT_DELTA_START_MARKER,
)
from umx.github_ops import GitHubError
from umx.github_ops import OpenPullRequestSummary
from umx.git_ops import GitLocalBranch
from umx.governance import (
    LABEL_CONFIDENCE_HIGH,
    LABEL_HUMAN_REVIEW,
    LABEL_IMPACT_LOCAL,
    LABEL_STATE_EXTRACTION,
    LABEL_STATE_REVIEWED,
    LABEL_TYPE_EXTRACTION,
)
from umx.governance_health import (
    build_governance_health_payload,
    render_governance_health_human,
)
from umx.scope import config_path


def _configure_remote_mode() -> None:
    cfg = default_config()
    cfg.org = "memory-org"
    cfg.dream.mode = "remote"
    save_config(config_path(), cfg)


def _governance_pr_body() -> str:
    payload = {
        "version": FACT_DELTA_BLOCK_VERSION,
        "added": [],
        "modified": [],
        "superseded": [],
        "tombstoned": [],
    }
    return (
        "Governance summary\n\n"
        f"{FACT_DELTA_START_MARKER}\n"
        "```json\n"
        f"{json.dumps(payload, sort_keys=True)}\n"
        "```\n"
        f"{FACT_DELTA_END_MARKER}\n"
    )


def _branch(name: str, *, age_days: int, current: bool = False) -> GitLocalBranch:
    stamp = (datetime.now(tz=UTC) - timedelta(days=age_days)).isoformat().replace("+00:00", "Z")
    return GitLocalBranch(
        name=name,
        head=f"{age_days:040d}"[-40:],
        last_commit_ts=stamp,
        current=current,
        upstream=None,
    )


def _set_origin(repo_dir, url: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo_dir), "remote", "remove", "origin"],
        capture_output=True,
        check=False,
    )
    subprocess.run(
        ["git", "-C", str(repo_dir), "remote", "add", "origin", url],
        capture_output=True,
        check=True,
    )


def test_governance_health_empty_state(project_dir, monkeypatch) -> None:
    _configure_remote_mode()
    monkeypatch.setattr("umx.governance_health.list_open_pull_requests", lambda org, repo: [])
    monkeypatch.setattr("umx.governance_health.list_local_branches", lambda repo: ())
    monkeypatch.setattr("umx.governance_health.read_processing_log", lambda repo, ref=None: [])
    monkeypatch.setattr("umx.governance_health.git_ref_exists", lambda repo, ref: False)

    payload = build_governance_health_payload(project_dir)

    assert payload["ok"] is True
    assert payload["summary"]["open_governance_prs"] == 0
    assert payload["summary"]["stale_branch_count"] == 0
    assert payload["last_l2_review"] is None
    assert payload["flags"] == []


def test_governance_health_healthy_state(project_dir, monkeypatch) -> None:
    _configure_remote_mode()
    monkeypatch.setattr(
        "umx.governance_health.list_open_pull_requests",
        lambda org, repo: [
            OpenPullRequestSummary(
                number=12,
                title="Reviewed extraction",
                url="https://example.test/pr/12",
                head_ref="dream/l1/20260418-reviewed",
                body=_governance_pr_body(),
                labels=(
                    LABEL_TYPE_EXTRACTION,
                    LABEL_CONFIDENCE_HIGH,
                    LABEL_IMPACT_LOCAL,
                    LABEL_STATE_REVIEWED,
                ),
            )
        ],
    )
    monkeypatch.setattr(
        "umx.governance_health.list_local_branches",
        lambda repo: (_branch("dream/l1/20260418-reviewed", age_days=10),),
    )
    monkeypatch.setattr(
        "umx.governance_health.read_processing_log",
        lambda repo, ref=None: [
            {
                "tier": "l2",
                "event": "review_completed",
                "status": "completed",
                "ts": "2026-04-18T12:00:00Z",
                "pr_number": 12,
                "action": "approve",
                "reviewed_by": "copilot",
                "review_model": "claude-opus-4-7",
                "merge_blocked": False,
            }
        ],
    )
    monkeypatch.setattr("umx.governance_health.git_ref_exists", lambda repo, ref: False)

    payload = build_governance_health_payload(project_dir)
    human = render_governance_health_human(payload)

    assert payload["ok"] is True
    assert payload["summary"]["open_governance_prs"] == 1
    assert payload["summary"]["reviewer_queue_depth"] == 0
    assert payload["summary"]["stale_branch_count"] == 0
    assert payload["label_drift"] == []
    assert payload["last_l2_review"]["action"] == "approve"
    assert "Governance health: ok" in human
    assert "Open governance PRs: 1" in human
    assert "Last L2 review: 2026-04-18T12:00:00Z" in human


def test_governance_health_uses_rotated_credentialed_origin_for_open_pr_inventory(
    project_dir,
    project_repo,
    monkeypatch,
) -> None:
    _configure_remote_mode()
    _set_origin(
        project_repo,
        f"https://github.com/memory-org/{project_repo.name}.git",
    )
    _set_origin(
        project_repo,
        f"https://x-access-token:rotated-secret@github.com/memory-org/{project_repo.name}.git",
    )
    captured: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "umx.governance_health.list_open_pull_requests",
        lambda org, repo: captured.append((org, repo))
        or [
            OpenPullRequestSummary(
                number=44,
                title="Open governed change",
                url=f"https://github.com/memory-org/{project_repo.name}/pull/44",
                head_ref="proposal/cleanup",
                body=_governance_pr_body(),
                labels=(
                    LABEL_TYPE_EXTRACTION,
                    LABEL_CONFIDENCE_HIGH,
                    LABEL_IMPACT_LOCAL,
                    LABEL_STATE_REVIEWED,
                ),
            )
        ],
    )
    monkeypatch.setattr("umx.governance_health.list_local_branches", lambda repo: ())
    monkeypatch.setattr("umx.governance_health.read_processing_log", lambda repo, ref=None: [])
    monkeypatch.setattr("umx.governance_health.git_ref_exists", lambda repo, ref: False)

    payload = build_governance_health_payload(project_dir)

    assert captured == [("memory-org", project_repo.name)]
    assert payload["summary"]["open_governance_prs"] == 1
    assert payload["open_prs"][0]["number"] == 44


def test_governance_health_degraded_state(project_dir, monkeypatch) -> None:
    _configure_remote_mode()
    monkeypatch.setattr(
        "umx.governance_health.list_open_pull_requests",
        lambda org, repo: [
            OpenPullRequestSummary(
                number=21,
                title="Needs review",
                url="https://example.test/pr/21",
                head_ref="dream/l1/20260418-needs-review",
                body=_governance_pr_body(),
                labels=(
                    LABEL_TYPE_EXTRACTION,
                    LABEL_CONFIDENCE_HIGH,
                    LABEL_STATE_EXTRACTION,
                    LABEL_HUMAN_REVIEW,
                ),
            )
        ],
    )
    monkeypatch.setattr(
        "umx.governance_health.list_local_branches",
        lambda repo: (
            _branch("dream/l1/20260418-needs-review", age_days=3),
            _branch("proposal/old-cleanup", age_days=9, current=True),
        ),
    )
    monkeypatch.setattr("umx.governance_health.read_processing_log", lambda repo, ref=None: [])
    monkeypatch.setattr("umx.governance_health.git_ref_exists", lambda repo, ref: False)

    payload = build_governance_health_payload(project_dir)
    human = render_governance_health_human(payload)

    assert payload["ok"] is False
    assert payload["summary"]["reviewer_queue_depth"] == 1
    assert payload["summary"]["human_review_queue_depth"] == 1
    assert payload["summary"]["stale_branch_count"] == 1
    assert payload["summary"]["label_drift_count"] == 1
    assert "missing impact label" in payload["label_drift"][0]["issues"]
    assert "awaiting L2 review" in "\n".join(payload["flags"])
    assert "proposal/old-cleanup" in human
    assert "missing impact label" in human
    assert "Governance health: warn" in human


def test_governance_health_skips_stale_branches_when_pr_inventory_fails(
    project_dir,
    monkeypatch,
) -> None:
    _configure_remote_mode()

    def _raise_inventory_error(org: str, repo: str) -> list[OpenPullRequestSummary]:
        raise GitHubError("gh auth unavailable")

    monkeypatch.setattr(
        "umx.governance_health.list_open_pull_requests",
        _raise_inventory_error,
    )
    monkeypatch.setattr(
        "umx.governance_health.list_local_branches",
        lambda repo: (_branch("proposal/old-cleanup", age_days=9),),
    )
    monkeypatch.setattr("umx.governance_health.read_processing_log", lambda repo, ref=None: [])
    monkeypatch.setattr("umx.governance_health.git_ref_exists", lambda repo, ref: False)

    payload = build_governance_health_payload(project_dir)

    assert payload["ok"] is False
    assert payload["summary"]["pr_inventory_available"] is False
    assert payload["summary"]["stale_branch_count"] == 0
    assert payload["errors"] == ["gh auth unavailable"]
    assert all("stale local governance" not in flag for flag in payload["flags"])


def test_governance_health_invalid_body_fails_closed(project_dir, monkeypatch) -> None:
    _configure_remote_mode()
    monkeypatch.setattr(
        "umx.governance_health.list_open_pull_requests",
        lambda org, repo: [
            OpenPullRequestSummary(
                number=31,
                title="Malformed body",
                url="https://example.test/pr/31",
                head_ref="dream/l1/20260418-malformed",
                body="not a governance body",
                labels=(
                    LABEL_TYPE_EXTRACTION,
                    LABEL_CONFIDENCE_HIGH,
                    LABEL_IMPACT_LOCAL,
                    LABEL_STATE_REVIEWED,
                ),
            )
        ],
    )
    monkeypatch.setattr("umx.governance_health.list_local_branches", lambda repo: ())
    monkeypatch.setattr("umx.governance_health.read_processing_log", lambda repo, ref=None: [])
    monkeypatch.setattr("umx.governance_health.git_ref_exists", lambda repo, ref: False)

    payload = build_governance_health_payload(project_dir)
    human = render_governance_health_human(payload)

    assert payload["ok"] is False
    assert "invalid fact-delta bodies" in "\n".join(payload["flags"])
    assert "PR #31 dream/l1/20260418-malformed" in "\n".join(payload["errors"])
    assert "PR #31 dream/l1/20260418-malformed" in human


def test_governance_health_drifted_lifecycle_does_not_inflate_reviewer_queue(
    project_dir,
    monkeypatch,
) -> None:
    _configure_remote_mode()
    monkeypatch.setattr(
        "umx.governance_health.list_open_pull_requests",
        lambda org, repo: [
            OpenPullRequestSummary(
                number=41,
                title="Drifted labels",
                url="https://example.test/pr/41",
                head_ref="dream/l1/20260418-drifted",
                body=_governance_pr_body(),
                labels=(
                    LABEL_TYPE_EXTRACTION,
                    LABEL_CONFIDENCE_HIGH,
                    LABEL_IMPACT_LOCAL,
                    LABEL_STATE_EXTRACTION,
                    LABEL_STATE_REVIEWED,
                ),
            )
        ],
    )
    monkeypatch.setattr("umx.governance_health.list_local_branches", lambda repo: ())
    monkeypatch.setattr("umx.governance_health.read_processing_log", lambda repo, ref=None: [])
    monkeypatch.setattr("umx.governance_health.git_ref_exists", lambda repo, ref: False)

    payload = build_governance_health_payload(project_dir)

    assert payload["summary"]["reviewer_queue_depth"] == 0
    assert payload["summary"]["label_drift_count"] == 1
