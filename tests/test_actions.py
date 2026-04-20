from __future__ import annotations

from pathlib import Path

from umx.actions import (
    APPROVAL_GATE_WORKFLOW_TEMPLATE,
    L1_WORKFLOW_TEMPLATE,
    L2_WORKFLOW_TEMPLATE,
    WORKFLOW_INSTALL_COMMAND,
    workflow_templates,
    write_workflow_templates,
)


def test_workflow_templates_cover_l1_l2_and_approval_gate() -> None:
    templates = workflow_templates()

    assert set(templates) == {"approval-gate.yml", "l1-dream.yml", "l2-review.yml"}
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
    assert "contents: write" in templates["l2-review.yml"]
    assert "issues: write" in templates["l2-review.yml"]
    assert "GH_TOKEN: ${{ github.token }}" in templates["l2-review.yml"]
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


def test_write_workflow_templates_creates_files(tmp_path: Path) -> None:
    written = write_workflow_templates(tmp_path)

    assert {path.name for path in written} == {"approval-gate.yml", "l1-dream.yml", "l2-review.yml"}
    gate = (tmp_path / ".github" / "workflows" / "approval-gate.yml").read_text()
    l1 = (tmp_path / ".github" / "workflows" / "l1-dream.yml").read_text()
    l2 = (tmp_path / ".github" / "workflows" / "l2-review.yml").read_text()

    assert gate == APPROVAL_GATE_WORKFLOW_TEMPLATE
    assert l1 == L1_WORKFLOW_TEMPLATE
    assert l2 == L2_WORKFLOW_TEMPLATE
    assert "permissions:" in l1
    assert "concurrency:" in l2
    assert "state: approved" in gate
