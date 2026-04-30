"""Synthetic RULER benchmark adapter over headless CLI providers."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from umx import benchmark_llm
from umx.config import UMXConfig, default_config

RULER_PROMPT_VERSION = "ruler-qa-v1"
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")

RulerScorer = Literal["string_match_all", "string_match_part"]


@dataclass(slots=True, frozen=True)
class RulerEvalCase:
    question_id: str
    task: str
    base_task: str
    category: str
    context_length: int
    prompt: str
    outputs: tuple[str, ...]
    scorer: RulerScorer
    weight: float = 1.0
    answer_prefix: str = ""


def load_ruler_eval_cases(path: Path) -> list[RulerEvalCase]:
    resolved = _resolve_cases_path(path)
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"RULER cases are not valid JSON: {resolved}") from exc
    if isinstance(payload, list):
        return _load_normalized_cases(payload, source_path=resolved)
    if isinstance(payload, dict) and payload.get("format") == "ruler-manifest":
        return _load_manifest_cases(payload, manifest_path=resolved)
    raise RuntimeError(
        f"unsupported RULER cases file `{resolved}`; expected a normalized case list "
        "or a `ruler-manifest` JSON object"
    )


def run_ruler_eval(
    *,
    cases_path: Path,
    out_dir: Path,
    config: UMXConfig | None = None,
    case_id: str | None = None,
    task: str | None = None,
    context_length: int | None = None,
    provider: str = "codex-cli",
    model: str | None = None,
    min_average_score: float = 0.0,
) -> dict[str, Any]:
    if not 0 <= min_average_score <= 1:
        raise RuntimeError("RULER min_average_score must be between 0 and 1")
    active_config = config or default_config()
    normalized_provider = benchmark_llm.normalize_benchmark_provider(provider)
    resolved_model = benchmark_llm.resolve_benchmark_model(
        normalized_provider,
        explicit_model=model,
        config=active_config,
    )
    cases = load_ruler_eval_cases(cases_path)
    if case_id:
        cases = [case for case in cases if case.question_id == case_id]
    if task:
        cases = [case for case in cases if case.task == task]
    if context_length is not None:
        cases = [case for case in cases if case.context_length == context_length]
    if not cases:
        raise RuntimeError("no RULER cases matched the requested filters")

    out_dir.mkdir(parents=True, exist_ok=True)
    predictions_path = out_dir / "predictions.jsonl"
    summary_path = out_dir / "summary.json"

    total_score = 0.0
    total_weight = 0.0
    passed = 0
    completed = 0
    usage_total: dict[str, int] = {}
    task_buckets: dict[str, dict[str, Any]] = {}
    category_buckets: dict[str, dict[str, Any]] = {}
    length_buckets: dict[str, dict[str, Any]] = {}

    with predictions_path.open("w", encoding="utf-8") as handle:
        for case in cases:
            row: dict[str, Any] = {
                "question_id": case.question_id,
                "task": case.task,
                "base_task": case.base_task,
                "category": case.category,
                "context_length": case.context_length,
                "scorer": case.scorer,
                "weight": case.weight,
                "outputs": list(case.outputs),
                "answer_prefix": case.answer_prefix,
            }
            score = 0.0
            passed_case = False
            try:
                result = benchmark_llm.send_benchmark_message_with_retry(
                    provider=normalized_provider,
                    config=active_config,
                    model=resolved_model,
                    system=_system_prompt(),
                    prompt=case.prompt,
                )
                prediction = _normalize_prediction(result.text, answer_prefix=case.answer_prefix)
                score = _score_prediction(prediction, outputs=case.outputs, scorer=case.scorer)
                passed_case = score >= 1.0
                row.update(
                    {
                        "prediction": prediction,
                        "raw_response": result.text,
                        "score": score,
                        "passed": passed_case,
                        "provider": normalized_provider,
                        "model": result.model,
                        "usage": result.usage,
                        "generation_prompt_id": _generation_prompt_id(normalized_provider),
                    }
                )
                _merge_usage(usage_total, result.usage)
                completed += 1
            except Exception as exc:  # noqa: BLE001 - summary artifact must preserve failures
                row.update(
                    {
                        "error": str(exc),
                        "score": 0.0,
                        "passed": False,
                        "provider": normalized_provider,
                        "model": resolved_model,
                        "generation_prompt_id": _generation_prompt_id(normalized_provider),
                    }
                )
            total_score += score * case.weight
            total_weight += case.weight
            if passed_case:
                passed += 1
            _update_bucket(task_buckets, case.task, score=score, passed=passed_case, weight=case.weight)
            _update_bucket(
                category_buckets,
                case.category,
                score=score,
                passed=passed_case,
                weight=case.weight,
            )
            _update_bucket(
                length_buckets,
                str(case.context_length),
                score=score,
                passed=passed_case,
                weight=case.weight,
            )
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    total_cases = len(cases)
    average_score = (total_score / total_weight) if total_weight else 0.0
    pass_rate = passed / total_cases if total_cases else 0.0
    capture_only = min_average_score == 0
    threshold_passed = average_score >= min_average_score
    gate_passed = (not capture_only) and completed == total_cases and threshold_passed
    ok = completed == total_cases and (capture_only or threshold_passed)
    status = "ok" if ok else "error"
    summary = {
        "suite": "ruler",
        "benchmark": {
            "name": "RULER",
            "url": "https://github.com/NVIDIA/RULER",
        },
        "status": status,
        "ok": ok,
        "capture_only": capture_only,
        "gate_passed": gate_passed,
        "gate_metric": "average_score",
        "cases_path": str(_resolve_cases_path(cases_path)),
        "out_dir": str(out_dir),
        "provider": normalized_provider,
        "model": resolved_model,
        "generation_prompt_id": _generation_prompt_id(normalized_provider),
        "min_average_score": min_average_score,
        "question_id_filter": case_id,
        "task_filter": task,
        "context_length_filter": context_length,
        "total_cases": total_cases,
        "completed": completed,
        "failed": total_cases - completed,
        "passed": passed,
        "average_score": average_score,
        "pass_rate": pass_rate,
        "usage": usage_total,
        "task_summary": _summarize_buckets(task_buckets),
        "category_summary": _summarize_buckets(category_buckets),
        "context_length_summary": _summarize_buckets(length_buckets),
        "predictions_path": str(predictions_path),
        "summary_path": str(summary_path),
    }
    benchmark_llm.write_json(summary_path, summary)
    return summary


def _resolve_cases_path(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if resolved.is_dir():
        manifest_path = resolved / "manifest.json"
        cases_file = resolved / "cases.json"
        if manifest_path.is_file():
            return manifest_path
        if cases_file.is_file():
            return cases_file
        raise RuntimeError(f"RULER cases directory `{resolved}` does not contain manifest.json or cases.json")
    if not resolved.is_file():
        raise RuntimeError(f"RULER cases file not found: {resolved}")
    return resolved


def _load_normalized_cases(payload: list[Any], *, source_path: Path) -> list[RulerEvalCase]:
    cases: list[RulerEvalCase] = []
    seen_ids: set[str] = set()
    for index, raw_case in enumerate(payload):
        if not isinstance(raw_case, dict):
            raise RuntimeError(f"RULER case #{index} in `{source_path}` is not a JSON object")
        case = _build_case(
            raw_case,
            default_question_id=f"{source_path.stem}-{index}",
            default_base_task=_infer_base_task(str(raw_case.get("task", ""))),
        )
        if case.question_id in seen_ids:
            raise RuntimeError(f"duplicate RULER question_id `{case.question_id}` in `{source_path}`")
        seen_ids.add(case.question_id)
        cases.append(case)
    return cases


def _load_manifest_cases(payload: dict[str, Any], *, manifest_path: Path) -> list[RulerEvalCase]:
    manifest_root = manifest_path.parent.resolve()
    dataset_dir = manifest_root
    raw_dataset_dir = payload.get("dataset_dir")
    if isinstance(raw_dataset_dir, str) and raw_dataset_dir.strip():
        dataset_dir = _resolve_path_within_root(
            manifest_root,
            raw_dataset_dir,
            context=f"RULER manifest `{manifest_path}` dataset_dir",
        )

    tasks = payload.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise RuntimeError(f"RULER manifest `{manifest_path}` is missing a non-empty `tasks` array")

    cases: list[RulerEvalCase] = []
    seen_ids: set[str] = set()
    for task_entry in tasks:
        if not isinstance(task_entry, dict):
            raise RuntimeError(f"RULER manifest `{manifest_path}` contains a non-object task entry")
        task_name = _require_non_empty_string(task_entry, "task", context=f"RULER manifest `{manifest_path}`")
        relative_path = task_entry.get("path")
        if not isinstance(relative_path, str) or not relative_path.strip():
            raise RuntimeError(f"RULER manifest `{manifest_path}` task entry is missing `path`")
        task_path = _resolve_path_within_root(
            dataset_dir,
            relative_path,
            context=f"RULER manifest `{manifest_path}` task `{task_name}` path",
        )
        if not task_path.is_file():
            raise RuntimeError(f"RULER manifest task file not found: {task_path}")
        base_task = str(task_entry.get("base_task") or _infer_base_task(task_name))
        category = _require_non_empty_string(
            task_entry,
            "category",
            context=f"RULER manifest `{manifest_path}` task `{task_name}`",
        )
        scorer = _normalize_scorer(
            task_entry.get("scorer"),
            context=f"RULER manifest `{manifest_path}` task `{task_name}`",
        )
        context_length = _require_positive_int(
            task_entry,
            "context_length",
            context=f"RULER manifest `{manifest_path}` task `{task_name}`",
        )
        weight = _coerce_weight(task_entry.get("weight"), context=f"RULER manifest `{manifest_path}` task `{task_name}`")
        for index, line in enumerate(task_path.read_text(encoding="utf-8").splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                raw_case = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"invalid JSONL in `{task_path}` line {index + 1}") from exc
            if not isinstance(raw_case, dict):
                raise RuntimeError(f"invalid JSON object in `{task_path}` line {index + 1}")
            raw_case.setdefault("task", task_name)
            raw_case.setdefault("base_task", base_task)
            raw_case.setdefault("category", category)
            raw_case.setdefault("context_length", context_length)
            raw_case.setdefault("scorer", scorer)
            raw_case.setdefault("weight", weight)
            raw_case.setdefault("prompt", raw_case.get("input"))
            default_id = f"{task_name}-{index}"
            case = _build_case(raw_case, default_question_id=default_id, default_base_task=base_task)
            if case.question_id in seen_ids:
                raise RuntimeError(f"duplicate RULER question_id `{case.question_id}` across `{manifest_path}`")
            seen_ids.add(case.question_id)
            cases.append(case)
    return cases


def _build_case(
    raw_case: dict[str, Any],
    *,
    default_question_id: str,
    default_base_task: str,
) -> RulerEvalCase:
    question_id = str(raw_case.get("question_id") or raw_case.get("id") or default_question_id).strip()
    if not question_id:
        raise RuntimeError("RULER case is missing a usable question_id")
    task = str(raw_case.get("task") or "").strip()
    if not task:
        raise RuntimeError(f"RULER case `{question_id}` is missing `task`")
    base_task = str(raw_case.get("base_task") or default_base_task).strip()
    if not base_task:
        raise RuntimeError(f"RULER case `{question_id}` is missing `base_task`")
    category = str(raw_case.get("category") or "").strip()
    if not category:
        raise RuntimeError(f"RULER case `{question_id}` is missing `category`")
    prompt = str(raw_case.get("prompt") or raw_case.get("input") or "").strip()
    if not prompt:
        raise RuntimeError(f"RULER case `{question_id}` is missing `prompt`")
    context_length = raw_case.get("context_length", raw_case.get("length"))
    try:
        normalized_context_length = int(context_length)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"RULER case `{question_id}` is missing a valid `context_length`") from exc
    outputs = raw_case.get("outputs", raw_case.get("answers"))
    if not isinstance(outputs, list) or not outputs:
        raise RuntimeError(f"RULER case `{question_id}` is missing non-empty `outputs`")
    normalized_outputs = tuple(str(output).strip() for output in outputs if str(output).strip())
    if not normalized_outputs:
        raise RuntimeError(f"RULER case `{question_id}` has no usable outputs")
    scorer = _normalize_scorer(raw_case.get("scorer"), context=f"RULER case `{question_id}`")
    weight = _coerce_weight(raw_case.get("weight"), context=f"RULER case `{question_id}`")
    answer_prefix = str(raw_case.get("answer_prefix") or "").strip()
    return RulerEvalCase(
        question_id=question_id,
        task=task,
        base_task=base_task,
        category=category,
        context_length=normalized_context_length,
        prompt=prompt,
        outputs=normalized_outputs,
        scorer=scorer,
        weight=weight,
        answer_prefix=answer_prefix,
    )


def _normalize_scorer(raw_scorer: Any, *, context: str) -> RulerScorer:
    scorer = str(raw_scorer or "string_match_all").strip()
    if scorer == "string_match_all":
        return "string_match_all"
    if scorer == "string_match_part":
        return "string_match_part"
    if scorer not in {"string_match_all", "string_match_part"}:
        raise RuntimeError(f"{context} uses unsupported scorer `{scorer}`")
    raise AssertionError("unreachable")


def _coerce_weight(raw_weight: Any, *, context: str) -> float:
    if raw_weight is None:
        return 1.0
    try:
        weight = float(raw_weight)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{context} has invalid `weight`") from exc
    if weight <= 0:
        raise RuntimeError(f"{context} must use a positive `weight`")
    return weight


def _require_non_empty_string(payload: dict[str, Any], key: str, *, context: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"{context} is missing `{key}`")
    return value.strip()


def _require_positive_int(payload: dict[str, Any], key: str, *, context: str) -> int:
    value = payload.get(key)
    try:
        integer = int(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{context} is missing a valid `{key}`") from exc
    if integer <= 0:
        raise RuntimeError(f"{context} must use a positive `{key}`")
    return integer


def _resolve_path_within_root(root: Path, value: str, *, context: str) -> Path:
    candidate = (root / value).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise RuntimeError(f"{context} must stay within `{root}`") from exc
    return candidate


def _infer_base_task(task: str) -> str:
    normalized = task.strip().lower()
    if normalized.startswith("niah"):
        return "niah"
    if normalized in {"vt", "variable_tracking"}:
        return "variable_tracking"
    if normalized in {"qa_1", "qa_2", "qa"}:
        return "qa"
    if normalized in {"cwe", "common_words_extraction"}:
        return "common_words_extraction"
    if normalized in {"fwe", "freq_words_extraction"}:
        return "freq_words_extraction"
    return normalized or "unknown"


def _system_prompt() -> str:
    return (
        "You are running a synthetic long-context benchmark. "
        "Answer the user prompt directly using only the provided context. "
        "Do not explain your reasoning unless the prompt explicitly asks for it."
    )


def _generation_prompt_id(provider: str) -> str:
    return f"{provider}.{RULER_PROMPT_VERSION}"


def _normalize_prediction(text: str, *, answer_prefix: str) -> str:
    normalized = _CONTROL_CHARS_RE.sub("\n", text).strip()
    if answer_prefix:
        lowered = normalized.lower()
        lowered_prefix = answer_prefix.lower()
        if lowered.startswith(lowered_prefix):
            normalized = normalized[len(answer_prefix) :].lstrip(" :\n\t")
    return normalized


def _score_prediction(prediction: str, *, outputs: tuple[str, ...], scorer: RulerScorer) -> float:
    lowered_prediction = prediction.lower()
    lowered_outputs = tuple(output.lower() for output in outputs)
    if scorer == "string_match_part":
        if not lowered_outputs:
            return 0.0
        return max((1.0 if output in lowered_prediction else 0.0) for output in lowered_outputs)
    matches = sum(1 for output in lowered_outputs if output in lowered_prediction)
    return matches / len(lowered_outputs) if lowered_outputs else 0.0


def _merge_usage(target: dict[str, int], usage: dict[str, int]) -> None:
    for key, value in usage.items():
        target[key] = target.get(key, 0) + int(value)


def _update_bucket(bucket: dict[str, dict[str, Any]], key: str, *, score: float, passed: bool, weight: float) -> None:
    entry = bucket.setdefault(
        key,
        {
            "cases": 0,
            "passed": 0,
            "score_total": 0.0,
            "weight_total": 0.0,
        },
    )
    entry["cases"] += 1
    if passed:
        entry["passed"] += 1
    entry["score_total"] += score * weight
    entry["weight_total"] += weight


def _summarize_buckets(bucket: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    summary: dict[str, dict[str, Any]] = {}
    for key, entry in bucket.items():
        cases = int(entry["cases"])
        passed = int(entry["passed"])
        weight_total = float(entry["weight_total"])
        average_score = float(entry["score_total"]) / weight_total if weight_total else 0.0
        summary[key] = {
            "cases": cases,
            "passed": passed,
            "pass_rate": passed / cases if cases else 0.0,
            "average_score": average_score,
        }
    return summary
