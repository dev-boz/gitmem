from __future__ import annotations

from umx.dream.procedure_pr import (
    ProcedureDelta,
    build_procedure_delta_from_eval_trigger,
    parse_procedure_revision_pr_body,
    render_procedure_revision_pr_body,
)


def _make_delta(**kwargs) -> ProcedureDelta:
    defaults = dict(
        procedure_id="proc-abc123",
        title="Some Procedure",
        change_type="modified",
        old_strength=2,
        new_strength=3,
        affected_task_classes=["implementation"],
        rationale="Test rationale",
        eval_trigger_ref="task-42",
    )
    defaults.update(kwargs)
    return ProcedureDelta(**defaults)


def test_render_procedure_revision_pr_body_basic():
    delta = _make_delta()
    body = render_procedure_revision_pr_body([delta])
    assert "Procedure Revisions" in body
    assert "proc-abc123" in body


def test_render_includes_umx_pr_type_comment():
    delta = _make_delta()
    body = render_procedure_revision_pr_body([delta])
    assert "<!-- umx-pr-type: procedure_revision -->" in body


def test_render_includes_umx_procedure_delta_comment():
    delta = _make_delta()
    body = render_procedure_revision_pr_body([delta])
    assert "<!-- umx-procedure-delta:" in body


def test_parse_procedure_revision_pr_body_roundtrip():
    delta1 = _make_delta(procedure_id="proc-one", title="Proc One")
    delta2 = _make_delta(procedure_id="proc-two", title="Proc Two", change_type="added")
    body = render_procedure_revision_pr_body([delta1, delta2])
    parsed = parse_procedure_revision_pr_body(body)
    assert parsed is not None
    assert len(parsed) == 2
    ids = {d.procedure_id for d in parsed}
    assert ids == {"proc-one", "proc-two"}


def test_parse_returns_none_on_no_comment():
    plain = "## Just some markdown\n\nNo procedure delta comment here."
    result = parse_procedure_revision_pr_body(plain)
    assert result is None


def test_build_from_procedure_regression_trigger():
    trigger = {
        "trigger_type": "procedure_regression",
        "query": "something failed",
        "context": {"task_class": "implementation", "task_id": "task-99"},
    }
    result = build_procedure_delta_from_eval_trigger(trigger, procedures_dir=None)
    assert result == []
