from __future__ import annotations

from pathlib import Path

import yaml

from umx.actions import (
    APPROVAL_GATE_WORKFLOW_TEMPLATE,
    L1_WORKFLOW_TEMPLATE,
    L2_WORKFLOW_TEMPLATE,
    MAIN_GUARD_WORKFLOW_TEMPLATE,
    WORKFLOW_INSTALL_COMMAND,
    workflow_templates,
    write_workflow_templates,
)


def test_workflow_templates_cover_l1_l2_guard_and_approval_gate() -> None:
    templates = workflow_templates()

    assert set(templates) == {"approval-gate.yml", "l1-dream.yml", "l2-review.yml", "main-guard.yml"}
    assert "umx dream --mode remote --tier l1" in templates["l1-dream.yml"]
    assert "umx dream --mode remote --tier l2 --pr" in templates["l2-review.yml"]
    assert "actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683" in templates["l1-dream.yml"]
    assert "actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683" in templates["l2-review.yml"]
    assert WORKFLOW_INSTALL_COMMAND in templates["l1-dream.yml"]
    assert WORKFLOW_INSTALL_COMMAND in templates["l2-review.yml"]
    assert "state: extraction" in templates["l2-review.yml"]
    assert "type: extraction" in templates["l2-review.yml"]
    assert "type: consolidation" in templates["l2-review.yml"]
    assert "type: promotion" in templates["l2-review.yml"]
    assert "fetch-depth: 0" in templates["l2-review.yml"]
    assert "github.event.pull_request.head.sha" in templates["l2-review.yml"]
    assert "Prepare PR head branch" in templates["l2-review.yml"]
    assert 'PR_HEAD_REF: ${{ github.event.pull_request.head.ref }}' in templates["l2-review.yml"]
    assert 'PR_HEAD_SHA: ${{ github.event.pull_request.head.sha }}' in templates["l2-review.yml"]
    assert 'git checkout -B "$PR_HEAD_REF" "$PR_HEAD_SHA"' in templates["l2-review.yml"]
    assert "--head-sha ${{ github.event.pull_request.head.sha }}" in templates["l2-review.yml"]
    assert "--provider nvidia" in templates["l2-review.yml"]
    assert "contents: write" in templates["l2-review.yml"]
    assert "issues: write" in templates["l2-review.yml"]
    assert "GH_TOKEN: ${{ github.token }}" in templates["l2-review.yml"]
    assert "NVIDIA_API_KEY: ${{ secrets.NVIDIA_API_KEY }}" in templates["l2-review.yml"]
    assert "state: approved" in templates["approval-gate.yml"]
    assert "- labeled" in templates["approval-gate.yml"]
    assert "- unlabeled" in templates["approval-gate.yml"]
    assert "GITHUB_API_URL" in templates["approval-gate.yml"]
    assert "GOVERNED_FACT_PREFIXES" in templates["approval-gate.yml"]
    assert "previous_filename" in templates["approval-gate.yml"]
    assert "unexpected PR files payload from GitHub API" in templates["approval-gate.yml"]
    assert "malformed PR files payload from GitHub API" in templates["approval-gate.yml"]
    assert 'item.get("status") == "renamed"' in templates["approval-gate.yml"]
    assert "pulls/{pr_number}/files" in templates["approval-gate.yml"]
    assert "PR_LABELS_JSON" in templates["approval-gate.yml"]
    assert "Governance Approval Gate" in templates["approval-gate.yml"]
    assert "Governance Main Guard" in templates["main-guard.yml"]
    assert "fetch-depth: 0" in templates["main-guard.yml"]
    assert "commits/{commit}/pulls" in templates["main-guard.yml"]
    assert 'run_git("revert", "--abort", check=False)' in templates["main-guard.yml"]
    assert 'run_git("diff", "--name-only", f"{before}..{after}")' in templates["main-guard.yml"]
    assert "umx: revert unauthorized main push" in templates["main-guard.yml"]
    assert "GOVERNANCE_MODE: 'remote'" in templates["main-guard.yml"]
    assert "PROCESSING_LOG_PATH: 'meta/processing.jsonl'" in templates["main-guard.yml"]
    assert "REVERT_SOURCE_TRAILER: 'UMX-Main-Guard-Source'" in templates["main-guard.yml"]
    assert "REVERT_BASE_TRAILER: 'UMX-Main-Guard-Base'" in templates["main-guard.yml"]
    assert '"event": audit_event' in templates["main-guard.yml"]
    assert "governance_auto_revert" in templates["main-guard.yml"]
    assert "state: approved" in templates["main-guard.yml"]
    assert "initial main bootstrap; main guard skipped" in templates["main-guard.yml"]
    assert "main guard revert commit detected; skipping" in templates["main-guard.yml"]
    assert "all governed commits matched approved PRs" in templates["main-guard.yml"]
    assert "git_tree(after)" in templates["main-guard.yml"]
    assert "revert_source == before" in templates["main-guard.yml"]
    assert "approved_pr_numbers" in templates["main-guard.yml"]
    assert 'with processing_log_path.open("a", encoding="utf-8") as handle:' in templates["main-guard.yml"]


def test_workflow_templates_render_hybrid_guard_mode() -> None:
    templates = workflow_templates(mode="hybrid")

    assert "GOVERNANCE_MODE: 'hybrid'" in templates["main-guard.yml"]


def test_write_workflow_templates_creates_files(tmp_path: Path) -> None:
    written = write_workflow_templates(tmp_path)

    assert {path.name for path in written} == {"approval-gate.yml", "l1-dream.yml", "l2-review.yml", "main-guard.yml"}
    gate = (tmp_path / ".github" / "workflows" / "approval-gate.yml").read_text()
    l1 = (tmp_path / ".github" / "workflows" / "l1-dream.yml").read_text()
    l2 = (tmp_path / ".github" / "workflows" / "l2-review.yml").read_text()
    guard = (tmp_path / ".github" / "workflows" / "main-guard.yml").read_text()

    assert gate == APPROVAL_GATE_WORKFLOW_TEMPLATE
    assert l1 == L1_WORKFLOW_TEMPLATE
    assert l2 == L2_WORKFLOW_TEMPLATE
    assert guard == MAIN_GUARD_WORKFLOW_TEMPLATE
    assert "permissions:" in l1
    assert "concurrency:" in l2
    assert "state: approved" in gate
    assert "umx: revert unauthorized main push" in guard
    assert "GOVERNANCE_MODE: 'remote'" in guard


def test_write_workflow_templates_writes_hybrid_guard_mode(tmp_path: Path) -> None:
    write_workflow_templates(tmp_path, mode="hybrid")

    guard = (tmp_path / ".github" / "workflows" / "main-guard.yml").read_text()
    assert "GOVERNANCE_MODE: 'hybrid'" in guard


def test_workflow_templates_parse_as_yaml() -> None:
    for mode in ("remote", "hybrid"):
        templates = workflow_templates(mode=mode)
        for name, content in templates.items():
            parsed = yaml.safe_load(content)
            assert parsed["name"]
            assert "jobs" in parsed, name
