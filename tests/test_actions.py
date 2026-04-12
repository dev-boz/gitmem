from __future__ import annotations

from pathlib import Path

from umx.actions import (
    L1_WORKFLOW_TEMPLATE,
    L2_WORKFLOW_TEMPLATE,
    workflow_templates,
    write_workflow_templates,
)


def test_workflow_templates_cover_l1_and_l2() -> None:
    templates = workflow_templates()

    assert set(templates) == {"l1-dream.yml", "l2-review.yml"}
    assert "umx dream --mode remote --tier l1" in templates["l1-dream.yml"]
    assert "umx dream --mode remote --tier l2 --pr" in templates["l2-review.yml"]
    assert "type: consolidation" in templates["l2-review.yml"]


def test_write_workflow_templates_creates_files(tmp_path: Path) -> None:
    written = write_workflow_templates(tmp_path)

    assert {path.name for path in written} == {"l1-dream.yml", "l2-review.yml"}
    l1 = (tmp_path / ".github" / "workflows" / "l1-dream.yml").read_text()
    l2 = (tmp_path / ".github" / "workflows" / "l2-review.yml").read_text()

    assert l1 == L1_WORKFLOW_TEMPLATE
    assert l2 == L2_WORKFLOW_TEMPLATE
    assert "permissions:" in l1
    assert "concurrency:" in l2
