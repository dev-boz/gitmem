from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from umx.cli import main
from umx.eval_compare import compare_eval_reports


def _write_report(path: Path, payload: dict[str, object]) -> Path:
    path.write_text(json.dumps(payload))
    return path


def test_compare_eval_reports_uses_long_memory_defaults(tmp_path: Path) -> None:
    baseline = _write_report(
        tmp_path / "baseline.json",
        {
            "suite": "long-memory",
            "status": "ok",
            "pass_rate": 0.8,
            "average_recall": 0.84,
            "type_summary": {
                "single-session-preference": {"average_recall": 0.7},
                "single-session-user": {"average_recall": 1.0},
                "multi-session": {"average_recall": 0.75},
                "knowledge-update": {"average_recall": 0.75},
                "temporal-reasoning": {"average_recall": 0.75},
                "abstention": {"average_recall": 0.95},
            },
        },
    )
    candidate = _write_report(
        tmp_path / "candidate.json",
        {
            "suite": "long-memory",
            "status": "ok",
            "pass_rate": 0.9,
            "average_recall": 0.9,
            "type_summary": {
                "single-session-preference": {"average_recall": 0.8},
                "single-session-user": {"average_recall": 1.0},
                "multi-session": {"average_recall": 0.8},
                "knowledge-update": {"average_recall": 0.8},
                "temporal-reasoning": {"average_recall": 0.9},
                "abstention": {"average_recall": 1.0},
            },
        },
    )

    payload = compare_eval_reports(baseline, candidate)

    assert payload["status"] == "ok"
    assert payload["suite"] == "long-memory"
    assert [entry["name"] for entry in payload["metrics"]] == [
        "pass_rate",
        "average_recall",
        "type_summary.abstention.average_recall",
        "type_summary.knowledge-update.average_recall",
        "type_summary.multi-session.average_recall",
        "type_summary.single-session-preference.average_recall",
        "type_summary.single-session-user.average_recall",
        "type_summary.temporal-reasoning.average_recall",
    ]


def test_compare_eval_reports_flags_metric_regressions(tmp_path: Path) -> None:
    baseline = _write_report(
        tmp_path / "baseline.json",
        {
            "suite": "retrieval",
            "status": "ok",
            "pass_rate": 1.0,
            "average_recall": 1.0,
        },
    )
    candidate = _write_report(
        tmp_path / "candidate.json",
        {
            "suite": "retrieval",
            "status": "error",
            "pass_rate": 0.5,
            "average_recall": 0.75,
        },
    )

    payload = compare_eval_reports(baseline, candidate)

    assert payload["status"] == "error"
    assert payload["candidate_report_ok"] is False
    assert [entry["name"] for entry in payload["regressions"]] == ["pass_rate", "average_recall"]


def test_compare_eval_reports_rejects_failed_baseline(tmp_path: Path) -> None:
    baseline = _write_report(
        tmp_path / "baseline.json",
        {
            "suite": "inject",
            "status": "error",
            "pass_rate": 0.5,
        },
    )
    candidate = _write_report(
        tmp_path / "candidate.json",
        {
            "suite": "inject",
            "status": "ok",
            "pass_rate": 1.0,
        },
    )

    payload = compare_eval_reports(baseline, candidate)

    assert payload["status"] == "error"
    assert payload["baseline_report_ok"] is False
    assert payload["candidate_report_ok"] is True
    assert payload["regressions"] == []


def test_compare_eval_reports_uses_retrieval_answer_coverage_when_available(tmp_path: Path) -> None:
    baseline = _write_report(
        tmp_path / "baseline.json",
        {
            "suite": "retrieval",
            "status": "ok",
            "pass_rate": 1.0,
            "average_recall": 1.0,
            "average_answer_coverage": 1.0,
        },
    )
    candidate = _write_report(
        tmp_path / "candidate.json",
        {
            "suite": "retrieval",
            "status": "ok",
            "pass_rate": 1.0,
            "average_recall": 1.0,
            "average_answer_coverage": 0.5,
        },
    )

    payload = compare_eval_reports(baseline, candidate)

    assert payload["status"] == "error"
    assert [entry["name"] for entry in payload["regressions"]] == ["average_answer_coverage"]


def test_compare_eval_reports_uses_longbench_v2_defaults(tmp_path: Path) -> None:
    baseline = _write_report(
        tmp_path / "baseline.json",
        {
            "suite": "longbench-v2",
            "status": "ok",
            "accuracy": 0.5,
        },
    )
    candidate = _write_report(
        tmp_path / "candidate.json",
        {
            "suite": "longbench-v2",
            "status": "ok",
            "accuracy": 0.75,
        },
    )

    payload = compare_eval_reports(baseline, candidate)

    assert payload["status"] == "ok"
    assert [entry["name"] for entry in payload["metrics"]] == ["accuracy"]


def test_compare_eval_reports_uses_beir_defaults(tmp_path: Path) -> None:
    baseline = _write_report(
        tmp_path / "baseline.json",
        {
            "suite": "beir",
            "status": "ok",
            "ndcg_at_10": 0.42,
            "recall_at_10": 0.61,
        },
    )
    candidate = _write_report(
        tmp_path / "candidate.json",
        {
            "suite": "beir",
            "status": "ok",
            "ndcg_at_10": 0.5,
            "recall_at_10": 0.7,
        },
    )

    payload = compare_eval_reports(baseline, candidate)

    assert payload["status"] == "ok"
    assert [entry["name"] for entry in payload["metrics"]] == ["ndcg_at_10", "recall_at_10"]


def test_compare_eval_reports_uses_ruler_defaults(tmp_path: Path) -> None:
    baseline = _write_report(
        tmp_path / "baseline.json",
        {
            "suite": "ruler",
            "status": "ok",
            "average_score": 0.5,
            "pass_rate": 0.25,
        },
    )
    candidate = _write_report(
        tmp_path / "candidate.json",
        {
            "suite": "ruler",
            "status": "ok",
            "average_score": 0.75,
            "pass_rate": 0.5,
        },
    )

    payload = compare_eval_reports(baseline, candidate)

    assert payload["status"] == "ok"
    assert [entry["name"] for entry in payload["metrics"]] == ["average_score", "pass_rate"]


def test_cli_eval_compare_exits_nonzero_on_regression(tmp_path: Path) -> None:
    baseline = _write_report(
        tmp_path / "baseline.json",
        {
            "suite": "inject",
            "status": "ok",
            "pass_rate": 1.0,
        },
    )
    candidate = _write_report(
        tmp_path / "candidate.json",
        {
            "suite": "inject",
            "status": "ok",
            "pass_rate": 0.5,
        },
    )

    runner = CliRunner()
    result = runner.invoke(main, ["eval", "compare", str(baseline), str(candidate)])

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["status"] == "error"
    assert payload["regressions"][0]["name"] == "pass_rate"
