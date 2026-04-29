from __future__ import annotations

import json
import re
import string
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from umx import benchmark_llm
from umx.config import UMXConfig
from umx.long_memory_eval import LongMemorySession, retrieve_long_memory_sessions

LOCOMO_PROMPT_VERSION = "v1"
LOCOMO_CATEGORY_NAMES = {
    1: "multi-hop",
    2: "temporal",
    3: "open-domain",
    4: "single-session",
    5: "abstention",
}


@dataclass(slots=True, frozen=True)
class LoCoMoEvalCase:
    question_id: str
    sample_id: str
    category: int
    question_type: str
    question: str
    answer: str
    evidence_dialog_ids: tuple[str, ...]
    answer_session_ids: tuple[str, ...]
    haystack_sessions: tuple[LongMemorySession, ...]
    question_date: str | None = None


def load_locomo_eval_cases(cases_path: Path) -> list[LoCoMoEvalCase]:
    resolved = _resolve_cases_path(cases_path)
    payload = _load_payload(resolved)
    if isinstance(payload, list) and payload and _looks_like_raw_locomo_sample(payload[0]):
        return _cases_from_raw_payload(payload, source=resolved)
    if isinstance(payload, list) and (not payload or _looks_like_normalized_case(payload[0])):
        cases = [_case_from_payload(item, source=resolved) for item in payload]
        _ensure_unique_ids([case.question_id for case in cases], field="question_id", source=resolved, context="LoCoMo eval cases")
        return cases
    raise RuntimeError("LoCoMo cases must be either the official raw dataset JSON array or a normalized cases array")


def run_locomo_eval(
    out_dir: Path,
    cases_path: Path,
    config: UMXConfig,
    *,
    case_id: str | None = None,
    min_average_f1: float = 0.0,
    search_limit: int = 5,
    provider: str | None = None,
    model: str | None = None,
    history_format: str = "json",
) -> dict[str, Any]:
    resolved_cases_path = _resolve_cases_path(cases_path)
    cases = load_locomo_eval_cases(resolved_cases_path)
    if case_id is not None:
        cases = [case for case in cases if case.question_id == case_id]
    if not cases:
        raise RuntimeError("no LoCoMo cases matched the requested selection")
    if not 0 <= min_average_f1 <= 1:
        raise RuntimeError("LoCoMo min_average_f1 must be between 0 and 1")
    if search_limit <= 0:
        raise RuntimeError("LoCoMo search_limit must be greater than 0")
    if history_format not in {"json", "nl"}:
        raise RuntimeError("LoCoMo history_format must be `json` or `nl`")

    provider_name = benchmark_llm.normalize_benchmark_provider(provider)
    answer_model = benchmark_llm.resolve_benchmark_model(
        provider_name,
        explicit_model=model,
        config=config,
    )
    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    predictions_path = out_dir / "predictions.jsonl"

    capture_only = min_average_f1 == 0
    results: list[dict[str, Any]] = []
    category_totals: dict[str, dict[str, float]] = {}
    generation_usage_totals = benchmark_llm.empty_usage()
    exact_matches = 0
    total_f1 = 0.0
    total_recall = 0.0
    completed = 0

    with predictions_path.open("w", encoding="utf-8") as predictions_file:
        for case in cases:
            category_totals.setdefault(case.question_type, {"total": 0, "exact": 0, "f1_sum": 0.0, "recall_sum": 0.0})
            category_totals[case.question_type]["total"] += 1
            try:
                retrieved_sessions = retrieve_long_memory_sessions(
                    case,
                    config=config,
                    search_limit=search_limit,
                    rerank_mode="full-first",
                )
                retrieved_session_ids = [session.session_id for session in retrieved_sessions]
                matched_session_ids = [
                    session_id for session_id in case.answer_session_ids if session_id in retrieved_session_ids
                ]
                evidence_recall = (
                    len(matched_session_ids) / len(case.answer_session_ids)
                    if case.answer_session_ids
                    else (1.0 if not retrieved_session_ids else 0.0)
                )
                answer_response = benchmark_llm.send_benchmark_message_with_retry(
                    provider_name,
                    config=config,
                    model=answer_model,
                    system=_answer_system_prompt(),
                    prompt=_answer_user_prompt(
                        retrieved_sessions,
                        question=case.question,
                        category=case.category,
                        question_date=case.question_date,
                        history_format=history_format,
                    ),
                )
                actual_answer = answer_response.text.strip()
                benchmark_llm.add_usage(generation_usage_totals, answer_response.usage)
                score = _score_locomo_answer(case, actual_answer)
                passed = score == 1.0
                if passed:
                    exact_matches += 1
                    category_totals[case.question_type]["exact"] += 1
                total_f1 += score
                total_recall += evidence_recall
                category_totals[case.question_type]["f1_sum"] += score
                category_totals[case.question_type]["recall_sum"] += evidence_recall
                completed += 1
                print(
                    json.dumps(
                        {
                            "question_id": case.question_id,
                            "prediction": actual_answer,
                        }
                    ),
                    file=predictions_file,
                )
                results.append(
                    {
                        "case": case.question_id,
                        "sample_id": case.sample_id,
                        "category": case.category,
                        "question_type": case.question_type,
                        "question": case.question,
                        "expected_answer": case.answer,
                        "actual_answer": actual_answer,
                        "retrieved_session_ids": retrieved_session_ids,
                        "matched_session_ids": matched_session_ids,
                        "evidence_recall": evidence_recall,
                        "f1": score,
                        "passed": passed,
                        "generation_model": answer_response.model,
                        "generation_usage": answer_response.usage,
                    }
                )
            except Exception as exc:
                print(
                    json.dumps(
                        {
                            "question_id": case.question_id,
                            "prediction": None,
                            "error": str(exc),
                        }
                    ),
                    file=predictions_file,
                )
                results.append(
                    {
                        "case": case.question_id,
                        "sample_id": case.sample_id,
                        "category": case.category,
                        "question_type": case.question_type,
                        "question": case.question,
                        "expected_answer": case.answer,
                        "actual_answer": None,
                        "retrieved_session_ids": None,
                        "matched_session_ids": [],
                        "evidence_recall": 0.0,
                        "f1": 0.0,
                        "passed": False,
                        "error": str(exc),
                    }
                )

    total = len(cases)
    exact_match_rate = exact_matches / total if total else 0.0
    average_f1 = total_f1 / total if total else 0.0
    average_evidence_recall = total_recall / total if total else 0.0
    threshold_passed = average_f1 >= min_average_f1
    gate_passed = (not capture_only) and threshold_passed
    errors = [result for result in results if result.get("error")]
    status = "ok" if not errors and (capture_only or threshold_passed) else "error"
    category_summary = {
        question_type: {
            "total": int(summary["total"]),
            "passed": int(summary["exact"]),
            "exact_match_rate": (summary["exact"] / summary["total"]) if summary["total"] else 0.0,
            "average_f1": (summary["f1_sum"] / summary["total"]) if summary["total"] else 0.0,
            "average_evidence_recall": (summary["recall_sum"] / summary["total"]) if summary["total"] else 0.0,
        }
        for question_type, summary in sorted(category_totals.items())
    }
    summary = {
        "suite": "locomo",
        "benchmark": {
            "name": "LoCoMo",
            "url": "https://github.com/snap-research/locomo",
        },
        "cases_path": str(resolved_cases_path),
        "case_filter": case_id,
        "status": status,
        "capture_only": capture_only,
        "gate_passed": gate_passed,
        "gate_metric": "average_f1",
        "provider": provider_name,
        "model": answer_model,
        "generation_prompt_id": "claude-cli-locomo-generation",
        "generation_prompt_version": LOCOMO_PROMPT_VERSION,
        "history_format": history_format,
        "total": total,
        "completed": completed,
        "passed": exact_matches,
        "pass_rate": exact_match_rate,
        "exact_match_rate": exact_match_rate,
        "average_f1": average_f1,
        "average_evidence_recall": average_evidence_recall,
        "min_average_f1": min_average_f1,
        "search_limit": search_limit,
        "category_summary": category_summary,
        "generation_usage_totals": generation_usage_totals,
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
        raise RuntimeError(f"LoCoMo cases not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"LoCoMo cases are not valid JSON: {path}") from exc


def _looks_like_raw_locomo_sample(value: Any) -> bool:
    return isinstance(value, dict) and isinstance(value.get("conversation"), dict) and isinstance(value.get("qa"), list)


def _looks_like_normalized_case(value: Any) -> bool:
    return isinstance(value, dict) and "question_id" in value and "haystack_sessions" in value


def _cases_from_raw_payload(payload: list[dict[str, Any]], *, source: Path) -> list[LoCoMoEvalCase]:
    cases: list[LoCoMoEvalCase] = []
    for sample in payload:
        if not isinstance(sample, dict):
            raise RuntimeError(f"invalid LoCoMo sample entry in {source}")
        sample_id = _required_string(sample.get("sample_id"), field="sample_id", source=source)
        conversation = sample.get("conversation")
        if not isinstance(conversation, dict):
            raise RuntimeError(f"LoCoMo sample `{sample_id}` is missing conversation data in {source}")
        session_numbers = sorted(
            int(key.split("_")[-1])
            for key in conversation
            if key.startswith("session_") and not key.endswith("_date_time")
        )
        haystack_sessions: list[LongMemorySession] = []
        session_id_by_number: dict[int, str] = {}
        for session_number in session_numbers:
            session_key = f"session_{session_number}"
            started = _optional_string(conversation.get(f"{session_key}_date_time"))
            session_id = f"S{session_number}"
            session_id_by_number[session_number] = session_id
            raw_turns = conversation.get(session_key)
            if not isinstance(raw_turns, list):
                raise RuntimeError(f"LoCoMo sample `{sample_id}` has invalid `{session_key}` in {source}")
            turns: list[dict[str, str]] = []
            for raw_turn in raw_turns:
                if not isinstance(raw_turn, dict):
                    raise RuntimeError(f"LoCoMo sample `{sample_id}` has invalid turn in `{session_key}`")
                speaker = _optional_string(raw_turn.get("speaker")) or "speaker"
                text = _optional_string(raw_turn.get("text")) or ""
                caption = _optional_string(raw_turn.get("blip_caption"))
                if caption:
                    text = f"{text}\n[Image caption: {caption}]".strip()
                turns.append({"role": speaker, "content": text})
            haystack_sessions.append(
                LongMemorySession(
                    session_id=session_id,
                    started=started,
                    turns=tuple(turns),
                )
            )
        qa_payload = sample.get("qa")
        if not isinstance(qa_payload, list):
            raise RuntimeError(f"LoCoMo sample `{sample_id}` has invalid qa list in {source}")
        for index, qa in enumerate(qa_payload):
            if not isinstance(qa, dict):
                raise RuntimeError(f"LoCoMo sample `{sample_id}` has invalid QA entry in {source}")
            category = _required_int(qa.get("category"), field="category", source=source, context=sample_id)
            evidence_dialog_ids = tuple(
                _expand_evidence_dialog_ids(
                    _string_list(qa.get("evidence"), field="evidence", source=source, context=sample_id)
                )
            )
            answer_session_ids = []
            for evidence_dialog_id in evidence_dialog_ids:
                session_number = _session_number_from_dialog_id(evidence_dialog_id)
                if session_number in session_id_by_number:
                    answer_session_ids.append(session_id_by_number[session_number])
            cases.append(
                LoCoMoEvalCase(
                    question_id=f"{sample_id}::{index:04d}",
                    sample_id=sample_id,
                    category=category,
                    question_type=LOCOMO_CATEGORY_NAMES.get(category, f"category-{category}"),
                    question=_required_string(qa.get("question"), field="question", source=source),
                    answer=_raw_answer_text(qa, source=source),
                    evidence_dialog_ids=evidence_dialog_ids,
                    answer_session_ids=tuple(dict.fromkeys(answer_session_ids)),
                    haystack_sessions=tuple(haystack_sessions),
                )
            )
    _ensure_unique_ids([case.question_id for case in cases], field="question_id", source=source, context="LoCoMo raw dataset")
    return cases


def _case_from_payload(payload: Any, *, source: Path) -> LoCoMoEvalCase:
    if not isinstance(payload, dict):
        raise RuntimeError(f"invalid LoCoMo case entry in {source}")
    question_id = _required_string(payload.get("question_id"), field="question_id", source=source)
    question_type = _required_string(payload.get("question_type"), field="question_type", source=source)
    category = _required_int(payload.get("category"), field="category", source=source, context=question_id)
    answer_session_ids = _string_list(payload.get("answer_session_ids", []), field="answer_session_ids", source=source, context=question_id)
    haystack_session_ids = _string_list(payload.get("haystack_session_ids"), field="haystack_session_ids", source=source, context=question_id)
    haystack_dates = payload.get("haystack_dates", [])
    haystack_sessions_payload = payload.get("haystack_sessions")
    if not isinstance(haystack_sessions_payload, list) or len(haystack_sessions_payload) != len(haystack_session_ids):
        raise RuntimeError(f"LoCoMo case `{question_id}` has invalid haystack_sessions in {source}")
    if haystack_dates and (not isinstance(haystack_dates, list) or len(haystack_dates) != len(haystack_session_ids)):
        raise RuntimeError(f"LoCoMo case `{question_id}` has invalid haystack_dates in {source}")
    sessions: list[LongMemorySession] = []
    for index, turns in enumerate(haystack_sessions_payload):
        if not isinstance(turns, list) or not all(isinstance(turn, dict) for turn in turns):
            raise RuntimeError(f"LoCoMo case `{question_id}` has invalid session payload in {source}")
        sessions.append(
            LongMemorySession(
                session_id=haystack_session_ids[index],
                started=_optional_string(haystack_dates[index]) if haystack_dates else None,
                turns=tuple(dict(turn) for turn in turns),
            )
        )
    return LoCoMoEvalCase(
        question_id=question_id,
        sample_id=_required_string(payload.get("sample_id") or question_id.split("::")[0], field="sample_id", source=source),
        category=category,
        question_type=question_type,
        question=_required_string(payload.get("question"), field="question", source=source),
        answer=_required_text(payload.get("answer"), field="answer", source=source),
        evidence_dialog_ids=tuple(_string_list(payload.get("evidence_dialog_ids", []), field="evidence_dialog_ids", source=source, context=question_id)),
        answer_session_ids=tuple(answer_session_ids),
        haystack_sessions=tuple(sessions),
        question_date=_optional_string(payload.get("question_date")),
    )


def _answer_system_prompt() -> str:
    return (
        "You are running a LoCoMo QA benchmark. Answer the question using only the provided "
        "conversation history. Return a short phrase, use exact words from the conversations "
        "whenever possible, and do not add explanation. If the history does not contain the "
        "answer, say 'No information available'."
    )


def _answer_user_prompt(
    sessions: list[LongMemorySession],
    *,
    question: str,
    category: int,
    question_date: str | None,
    history_format: str,
) -> str:
    instructions = ""
    if category == 2:
        instructions = (
            "\nIf the answer depends on conversation dates, answer with an approximate date "
            "grounded in the conversation history."
        )
    current_date = f"Current date: {question_date}\n\n" if question_date else ""
    history = benchmark_llm.render_history_sessions(sessions, history_format=history_format)
    return (
        "Below is a conversation between two people that takes place over multiple days. "
        "Use only this history to answer the question.\n\n"
        f"{current_date}"
        f"{history}\n\n"
        f"Question: {question}{instructions}\n"
        "Short answer:"
    )


def _score_locomo_answer(case: LoCoMoEvalCase, prediction: str) -> float:
    answer = case.answer
    if case.category == 3:
        answer = answer.split(";")[0].strip()
    if case.category in {2, 3, 4}:
        return _token_f1(prediction, answer)
    if case.category == 1:
        return _multi_answer_f1(prediction, answer)
    if case.category == 5:
        lowered = prediction.strip().lower()
        return 1.0 if any(
            marker in lowered
            for marker in (
                "no information available",
                "not mentioned",
                "i don't know",
                "i do not know",
                "cannot determine",
                "can't determine",
                "insufficient information",
            )
        ) else 0.0
    raise RuntimeError(f"unsupported LoCoMo category `{case.category}`")


def _multi_answer_f1(prediction: str, ground_truth: str) -> float:
    predictions = [item.strip() for item in prediction.split(",") if item.strip()]
    ground_truths = [item.strip() for item in ground_truth.split(",") if item.strip()]
    if not predictions or not ground_truths:
        return 0.0
    matched_predictions: set[int] = set()
    matched_scores: list[float] = []
    for truth in ground_truths:
        best_index: int | None = None
        best_score = 0.0
        for index, candidate in enumerate(predictions):
            if index in matched_predictions:
                continue
            score = _token_f1(candidate, truth)
            if score > best_score:
                best_index = index
                best_score = score
        if best_index is not None:
            matched_predictions.add(best_index)
        matched_scores.append(best_score)
    precision = sum(matched_scores) / len(predictions)
    recall = sum(matched_scores) / len(ground_truths)
    if precision + recall == 0:
        return 0.0
    return (2 * precision * recall) / (precision + recall)


def _token_f1(prediction: str, ground_truth: str) -> float:
    prediction_tokens = _normalized_tokens(prediction)
    ground_truth_tokens = _normalized_tokens(ground_truth)
    if not prediction_tokens or not ground_truth_tokens:
        return 0.0
    common = Counter(prediction_tokens) & Counter(ground_truth_tokens)
    num_same = sum(common.values())
    if num_same == 0:
        return 0.0
    precision = num_same / len(prediction_tokens)
    recall = num_same / len(ground_truth_tokens)
    return (2 * precision * recall) / (precision + recall)


def _normalized_tokens(text: str) -> list[str]:
    normalized = text.lower().replace(",", "")
    normalized = normalized.translate(str.maketrans("", "", string.punctuation))
    normalized = re.sub(r"\b(a|an|the|and)\b", " ", normalized)
    normalized = " ".join(normalized.split())
    return normalized.split()


def _session_number_from_dialog_id(dialog_id: str) -> int:
    match = re.match(r"^D(\d+):\d+$", dialog_id.strip())
    if match is None:
        raise RuntimeError(f"invalid LoCoMo dialog evidence id `{dialog_id}`")
    return int(match.group(1))


def _expand_evidence_dialog_ids(values: list[str]) -> list[str]:
    result: list[str] = []
    invalid_parts: list[str] = []
    for value in values:
        matches = re.findall(r"D\d+:\d+", value)
        if matches:
            result.extend(matches)
            continue
        stripped = value.strip()
        if stripped:
            invalid_parts.append(stripped)
    if not result and invalid_parts:
        invalid_list = ", ".join(f"`{part}`" for part in invalid_parts)
        raise RuntimeError(f"LoCoMo evidence list has no valid dialog ids (saw {invalid_list})")
    return result


def _string_list(value: Any, *, field: str, source: Path, context: str) -> list[str]:
    if not isinstance(value, list):
        raise RuntimeError(f"LoCoMo case `{context}` has invalid `{field}` in {source}")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise RuntimeError(f"LoCoMo case `{context}` has invalid `{field}` in {source}")
        result.append(item.strip())
    return result


def _required_string(value: Any, *, field: str, source: Path) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"LoCoMo case is missing `{field}` in {source}")
    return value.strip()


def _required_text(value: Any, *, field: str, source: Path) -> str:
    if value is None:
        raise RuntimeError(f"LoCoMo case is missing `{field}` in {source}")
    if isinstance(value, str):
        stripped = value.strip()
        if stripped:
            return stripped
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    raise RuntimeError(f"LoCoMo case is missing `{field}` in {source}")


def _raw_answer_text(payload: dict[str, Any], *, source: Path) -> str:
    answer = payload.get("answer")
    if answer is None:
        adversarial_answer = payload.get("adversarial_answer")
        if isinstance(adversarial_answer, str) and adversarial_answer.strip():
            return "No information available"
    return _required_text(answer, field="answer", source=source)


def _required_int(value: Any, *, field: str, source: Path, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RuntimeError(f"LoCoMo case `{context}` has invalid `{field}` in {source}")
    return value


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return str(value).strip() or None


def _ensure_unique_ids(values: list[str], *, field: str, source: Path, context: str) -> None:
    duplicates: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen and value not in duplicates:
            duplicates.append(value)
        seen.add(value)
    if duplicates:
        duplicate_list = ", ".join(f"`{value}`" for value in duplicates)
        raise RuntimeError(f"{context} has duplicate {field} {duplicate_list} in {source}")
