from __future__ import annotations

import json
from pathlib import Path
from typing import Any


DEFAULT_METRICS_BY_SUITE: dict[str, tuple[str, ...]] = {
    "beir": ("ndcg_at_10", "recall_at_10"),
    "inject": ("pass_rate",),
    "long-memory": ("pass_rate", "average_recall"),
    "longbench-v2": ("accuracy",),
    "retrieval": ("pass_rate", "average_recall"),
    "ruler": ("average_score", "pass_rate"),
}


def compare_eval_reports(
    baseline_path: Path,
    candidate_path: Path,
    *,
    metrics: tuple[str, ...] = (),
    tolerance: float = 0.0,
) -> dict[str, Any]:
    if tolerance < 0:
        raise RuntimeError("eval compare tolerance must be greater than or equal to 0")

    baseline = _load_report(baseline_path)
    candidate = _load_report(candidate_path)
    baseline_suite = _suite_name(baseline)
    candidate_suite = _suite_name(candidate)
    if baseline_suite and candidate_suite and baseline_suite != candidate_suite:
        raise RuntimeError(
            f"eval compare requires matching suites: `{baseline_suite}` != `{candidate_suite}`"
        )

    suite = candidate_suite or baseline_suite
    selected_metrics = _select_metrics(metrics, suite=suite, baseline=baseline, candidate=candidate)
    metric_results: list[dict[str, Any]] = []
    regressions: list[dict[str, Any]] = []

    for name in selected_metrics:
        baseline_value = _resolve_numeric_metric(baseline, name)
        candidate_value = _resolve_numeric_metric(candidate, name)
        delta = candidate_value - baseline_value
        passed = candidate_value + tolerance >= baseline_value
        result = {
            "name": name,
            "baseline": baseline_value,
            "candidate": candidate_value,
            "delta": delta,
            "passed": passed,
        }
        metric_results.append(result)
        if not passed:
            regressions.append(result)

    baseline_report_ok = baseline.get("status") == "ok"
    candidate_report_ok = candidate.get("status") == "ok"
    status = "ok" if baseline_report_ok and candidate_report_ok and not regressions else "error"
    return {
        "status": status,
        "suite": suite,
        "benchmark": candidate.get("benchmark") or baseline.get("benchmark"),
        "baseline_path": str(baseline_path),
        "candidate_path": str(candidate_path),
        "baseline_report_ok": baseline_report_ok,
        "candidate_report_ok": candidate_report_ok,
        "tolerance": tolerance,
        "metrics": metric_results,
        "regressions": regressions,
    }


def _load_report(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text())
    except FileNotFoundError as exc:
        raise RuntimeError(f"eval report not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"eval report is not valid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"eval report must be a JSON object: {path}")
    return payload


def _suite_name(payload: dict[str, Any]) -> str | None:
    suite = payload.get("suite")
    if isinstance(suite, str) and suite.strip():
        return suite.strip()
    return None


def _select_metrics(
    metrics: tuple[str, ...],
    *,
    suite: str | None,
    baseline: dict[str, Any],
    candidate: dict[str, Any],
) -> tuple[str, ...]:
    cleaned = tuple(dict.fromkeys(metric.strip() for metric in metrics if metric.strip()))
    if cleaned:
        return cleaned
    if suite is None:
        raise RuntimeError("eval compare needs --metric when reports omit `suite` metadata")
    default_metrics = DEFAULT_METRICS_BY_SUITE.get(suite)
    if default_metrics is None:
        raise RuntimeError(f"no default eval-compare metrics are registered for suite `{suite}`")
    selected = list(default_metrics)
    if suite == "long-memory":
        selected.extend(_long_memory_type_summary_metrics(baseline, candidate))
    if suite == "retrieval" and (
        _has_numeric_metric(baseline, "average_answer_coverage")
        or _has_numeric_metric(candidate, "average_answer_coverage")
    ):
        selected.append("average_answer_coverage")
    return tuple(dict.fromkeys(selected))


def _long_memory_type_summary_metrics(*payloads: dict[str, Any]) -> tuple[str, ...]:
    question_types: set[str] = set()
    for payload in payloads:
        type_summary = payload.get("type_summary")
        if not isinstance(type_summary, dict):
            continue
        for question_type, summary in type_summary.items():
            if isinstance(question_type, str) and question_type.strip() and isinstance(summary, dict):
                question_types.add(question_type.strip())
    return tuple(
        f"type_summary.{question_type}.average_recall" for question_type in sorted(question_types)
    )


def _has_numeric_metric(payload: dict[str, Any], name: str) -> bool:
    try:
        _resolve_numeric_metric(payload, name)
    except RuntimeError:
        return False
    return True


def _resolve_numeric_metric(payload: dict[str, Any], name: str) -> float:
    current: Any = payload
    for part in name.split("."):
        if not isinstance(current, dict) or part not in current:
            raise RuntimeError(f"eval report is missing metric `{name}`")
        current = current[part]
    if isinstance(current, bool) or not isinstance(current, (int, float)):
        raise RuntimeError(f"eval metric `{name}` must resolve to a number")
    return float(current)
