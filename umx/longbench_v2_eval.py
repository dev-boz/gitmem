from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from umx import benchmark_llm
from umx.config import UMXConfig

LONGBENCH_V2_PROMPT_VERSION = "v1"


@dataclass(slots=True, frozen=True)
class LongBenchV2EvalCase:
    question_id: str
    domain: str
    sub_domain: str
    difficulty: str
    length: str
    question: str
    choice_a: str
    choice_b: str
    choice_c: str
    choice_d: str
    answer: str
    context: str


def load_longbench_v2_eval_cases(cases_path: Path) -> list[LongBenchV2EvalCase]:
    resolved = _resolve_cases_path(cases_path)
    payload = _load_payload(resolved)
    if not isinstance(payload, list):
        raise RuntimeError("LongBench v2 cases must contain a JSON array")
    cases = [_case_from_payload(item, source=resolved) for item in payload]
    _ensure_unique_ids([case.question_id for case in cases], field="question_id", source=resolved)
    return cases


def run_longbench_v2_eval(
    out_dir: Path,
    cases_path: Path,
    config: UMXConfig,
    *,
    case_id: str | None = None,
    min_accuracy: float = 0.0,
    provider: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    resolved_cases_path = _resolve_cases_path(cases_path)
    cases = load_longbench_v2_eval_cases(resolved_cases_path)
    if case_id is not None:
        cases = [case for case in cases if case.question_id == case_id]
    if not cases:
        raise RuntimeError("no LongBench v2 cases matched the requested selection")
    if not 0 <= min_accuracy <= 1:
        raise RuntimeError("LongBench v2 min_accuracy must be between 0 and 1")

    provider_name = benchmark_llm.normalize_benchmark_provider(provider)
    answer_model = benchmark_llm.resolve_benchmark_model(
        provider_name,
        explicit_model=model,
        config=config,
    )
    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    predictions_path = out_dir / "predictions.jsonl"

    capture_only = min_accuracy == 0
    results: list[dict[str, Any]] = []
    domain_totals: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "passed": 0})
    difficulty_totals: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "passed": 0})
    usage_totals = benchmark_llm.empty_usage()
    passed = 0
    completed = 0

    with predictions_path.open("w", encoding="utf-8") as predictions_file:
        for case in cases:
            domain_totals[case.domain]["total"] += 1
            difficulty_totals[case.difficulty]["total"] += 1
            try:
                response = benchmark_llm.send_benchmark_message_with_retry(
                    provider_name,
                    config=config,
                    model=answer_model,
                    system=_answer_system_prompt(),
                    prompt=_answer_user_prompt(case),
                )
                benchmark_llm.add_usage(usage_totals, response.usage)
                prediction = _extract_answer(response.text)
                ok = prediction == case.answer
                if ok:
                    passed += 1
                    domain_totals[case.domain]["passed"] += 1
                    difficulty_totals[case.difficulty]["passed"] += 1
                completed += 1
                entry = {
                    "question_id": case.question_id,
                    "domain": case.domain,
                    "difficulty": case.difficulty,
                    "expected_answer": case.answer,
                    "prediction": prediction,
                    "response": response.text.strip(),
                    "passed": ok,
                }
                print(json.dumps(entry), file=predictions_file)
                results.append(
                    {
                        "case": case.question_id,
                        "domain": case.domain,
                        "sub_domain": case.sub_domain,
                        "difficulty": case.difficulty,
                        "length": case.length,
                        "expected_answer": case.answer,
                        "predicted_answer": prediction,
                        "raw_response": response.text.strip(),
                        "passed": ok,
                        "context_chars": len(case.context),
                        "model": response.model,
                        "usage": response.usage,
                    }
                )
            except Exception as exc:
                print(
                    json.dumps(
                        {
                            "question_id": case.question_id,
                            "domain": case.domain,
                            "difficulty": case.difficulty,
                            "prediction": None,
                            "error": str(exc),
                        }
                    ),
                    file=predictions_file,
                )
                results.append(
                    {
                        "case": case.question_id,
                        "domain": case.domain,
                        "sub_domain": case.sub_domain,
                        "difficulty": case.difficulty,
                        "length": case.length,
                        "expected_answer": case.answer,
                        "predicted_answer": None,
                        "raw_response": None,
                        "passed": False,
                        "context_chars": len(case.context),
                        "error": str(exc),
                    }
                )

    total = len(cases)
    accuracy = passed / total if total else 0.0
    threshold_passed = accuracy >= min_accuracy
    gate_passed = (not capture_only) and threshold_passed
    errors = [result for result in results if result.get("error")]
    status = "ok" if not errors and (capture_only or threshold_passed) else "error"
    summary = {
        "suite": "longbench-v2",
        "benchmark": {
            "name": "LongBench v2",
            "url": "https://github.com/THUDM/LongBench",
        },
        "cases_path": str(resolved_cases_path),
        "case_filter": case_id,
        "status": status,
        "capture_only": capture_only,
        "gate_passed": gate_passed,
        "provider": provider_name,
        "model": answer_model,
        "generation_prompt_id": _generation_prompt_id(provider_name),
        "generation_prompt_version": LONGBENCH_V2_PROMPT_VERSION,
        "total": total,
        "completed": completed,
        "passed": passed,
        "accuracy": accuracy,
        "pass_rate": accuracy,
        "min_accuracy": min_accuracy,
        "domain_summary": _summary_payload(domain_totals),
        "difficulty_summary": _summary_payload(difficulty_totals),
        "generation_usage_totals": usage_totals,
        "predictions_path": str(predictions_path),
        "failures": [result for result in results if not result.get("passed")],
        "results": results,
    }
    benchmark_llm.write_json(out_dir / "summary.json", summary)
    return summary


def _resolve_cases_path(cases_path: Path) -> Path:
    return cases_path / "cases.json" if cases_path.is_dir() and (cases_path / "cases.json").exists() else cases_path


def _load_payload(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError as exc:
        raise RuntimeError(f"LongBench v2 cases not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"LongBench v2 cases are not valid JSON: {path}") from exc


def _case_from_payload(payload: Any, *, source: Path) -> LongBenchV2EvalCase:
    if not isinstance(payload, dict):
        raise RuntimeError(f"invalid LongBench v2 case entry in {source}")
    answer = _required_answer(payload.get("answer"), source=source)
    return LongBenchV2EvalCase(
        question_id=_required_string(payload.get("_id") or payload.get("question_id"), field="question_id", source=source),
        domain=_required_string(payload.get("domain"), field="domain", source=source),
        sub_domain=_required_string(payload.get("sub_domain"), field="sub_domain", source=source),
        difficulty=_required_string(payload.get("difficulty"), field="difficulty", source=source),
        length=_required_string(payload.get("length"), field="length", source=source),
        question=_required_string(payload.get("question"), field="question", source=source),
        choice_a=_required_string(payload.get("choice_A"), field="choice_A", source=source),
        choice_b=_required_string(payload.get("choice_B"), field="choice_B", source=source),
        choice_c=_required_string(payload.get("choice_C"), field="choice_C", source=source),
        choice_d=_required_string(payload.get("choice_D"), field="choice_D", source=source),
        answer=answer,
        context=_required_string(payload.get("context"), field="context", source=source),
    )


def _required_string(value: Any, *, field: str, source: Path) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"LongBench v2 case is missing `{field}` in {source}")
    return value.strip()


def _required_answer(value: Any, *, source: Path) -> str:
    answer = _required_string(value, field="answer", source=source).upper()
    if answer not in {"A", "B", "C", "D"}:
        raise RuntimeError(f"LongBench v2 case has invalid `answer` `{answer}` in {source}")
    return answer


def _ensure_unique_ids(values: list[str], *, field: str, source: Path) -> None:
    duplicates: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen and value not in duplicates:
            duplicates.append(value)
        seen.add(value)
    if duplicates:
        duplicate_list = ", ".join(f"`{value}`" for value in duplicates)
        raise RuntimeError(f"LongBench v2 cases have duplicate {field} {duplicate_list} in {source}")


def _answer_system_prompt() -> str:
    return (
        "You are running LongBench v2 evaluation. Read the provided context and answer the multiple-choice "
        "question using only the context. Return exactly one line in the format: "
        "\"The correct answer is (A)\" where A is one of A, B, C, or D."
    )


def _answer_user_prompt(case: LongBenchV2EvalCase) -> str:
    return (
        "Please read the following text and answer the question below.\n\n"
        "<text>\n"
        f"{case.context}\n"
        "</text>\n\n"
        f"What is the correct answer to this question: {case.question}\n"
        "Choices:\n"
        f"(A) {case.choice_a}\n"
        f"(B) {case.choice_b}\n"
        f"(C) {case.choice_c}\n"
        f"(D) {case.choice_d}\n\n"
        'Format your response as follows: "The correct answer is (insert answer here)".'
    )


def _extract_answer(text: str) -> str | None:
    match = re.search(r"The correct answer is\s*\(?([A-D])\)?", text, re.IGNORECASE)
    if match is not None:
        return match.group(1).upper()
    match = re.fullmatch(r"\s*\(?([A-D])\)?\s*", text, re.IGNORECASE)
    if match is not None:
        return match.group(1).upper()
    return None


def _summary_payload(totals: dict[str, dict[str, int]]) -> dict[str, dict[str, float]]:
    return {
        key: {
            "total": int(value["total"]),
            "passed": int(value["passed"]),
            "accuracy": (value["passed"] / value["total"]) if value["total"] else 0.0,
        }
        for key, value in sorted(totals.items())
    }


def _generation_prompt_id(provider: str) -> str:
    return f"{provider}-longbench-v2-generation"
