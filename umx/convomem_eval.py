from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from umx import benchmark_llm
from umx.config import UMXConfig
from umx.long_memory_eval import LongMemorySession, retrieve_long_memory_sessions

CONVOMEM_GENERATION_PROMPT_VERSION = "v1"
CONVOMEM_JUDGE_PROMPT_VERSION = "v1"
ALLOWED_EVIDENCE_TYPES = {
    "abstention",
    "assistant_facts",
    "changing",
    "implicit_connections",
    "preferences",
    "user_facts",
}
EVIDENCE_TYPE_ALIASES = {
    "abstention": "abstention",
    "abstention_evidence": "abstention",
    "assistant_facts": "assistant_facts",
    "assistant_facts_evidence": "assistant_facts",
    "changing": "changing",
    "changing_evidence": "changing",
    "implicit_connection": "implicit_connections",
    "implicit_connection_evidence": "implicit_connections",
    "implicit_connections": "implicit_connections",
    "preferences": "preferences",
    "preferences_evidence": "preferences",
    "preference": "preferences",
    "preference_evidence": "preferences",
    "user_evidence": "user_facts",
    "user_fact": "user_facts",
    "user_facts": "user_facts",
    "user_facts_evidence": "user_facts",
}


@dataclass(slots=True, frozen=True)
class ConvoMemEvalCase:
    question_id: str
    evidence_type: str
    question_type: str
    question: str
    answer: str
    answer_session_ids: tuple[str, ...]
    haystack_sessions: tuple[LongMemorySession, ...]
    message_evidences: tuple[dict[str, str], ...]
    evidence_count: int
    persona: str | None = None
    question_date: str | None = None


def load_convomem_eval_cases(cases_path: Path) -> list[ConvoMemEvalCase]:
    if cases_path.is_dir() and (cases_path / "cases.json").exists():
        return load_convomem_eval_cases(cases_path / "cases.json")
    if cases_path.is_dir():
        return _cases_from_raw_directory(cases_path)
    payload = _load_payload(cases_path)
    if isinstance(payload, dict) and isinstance(payload.get("evidence_items"), list):
        return _cases_from_raw_file_payload(
            payload,
            source=cases_path,
            evidence_type=_infer_evidence_type(cases_path, payload),
        )
    if isinstance(payload, list) and (not payload or _looks_like_normalized_case(payload[0])):
        cases = [_case_from_payload(item, source=cases_path) for item in payload]
        _ensure_unique_ids([case.question_id for case in cases], field="question_id", source=cases_path, context="ConvoMem eval cases")
        return cases
    raise RuntimeError("ConvoMem cases must be a normalized cases array, a raw sample file, or a directory of raw sample files")


def run_convomem_eval(
    out_dir: Path,
    cases_path: Path,
    config: UMXConfig,
    *,
    case_id: str | None = None,
    min_pass_rate: float = 0.0,
    search_limit: int = 5,
    provider: str | None = None,
    model: str | None = None,
    judge_provider: str | None = None,
    judge_model: str | None = None,
    history_format: str = "json",
) -> dict[str, Any]:
    resolved_cases_path = cases_path / "cases.json" if cases_path.is_dir() and (cases_path / "cases.json").exists() else cases_path
    cases = load_convomem_eval_cases(cases_path)
    if case_id is not None:
        cases = [case for case in cases if case.question_id == case_id]
    if not cases:
        raise RuntimeError("no ConvoMem cases matched the requested selection")
    if not 0 <= min_pass_rate <= 1:
        raise RuntimeError("ConvoMem min_pass_rate must be between 0 and 1")
    if search_limit <= 0:
        raise RuntimeError("ConvoMem search_limit must be greater than 0")
    if history_format not in {"json", "nl"}:
        raise RuntimeError("ConvoMem history_format must be `json` or `nl`")

    provider_name = benchmark_llm.normalize_benchmark_provider(provider)
    judge_provider_name = benchmark_llm.normalize_benchmark_provider(judge_provider) if judge_provider else provider_name
    answer_model = benchmark_llm.resolve_benchmark_model(
        provider_name,
        explicit_model=model,
        config=config,
    )
    if judge_model is not None:
        judge_model_name = judge_model
    elif judge_provider_name == provider_name:
        judge_model_name = answer_model
    else:
        judge_model_name = benchmark_llm.resolve_benchmark_model(
            judge_provider_name,
            explicit_model=None,
            config=config,
        )
    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    predictions_path = out_dir / "predictions.jsonl"
    judgments_path = out_dir / "judgments.jsonl"

    capture_only = min_pass_rate == 0
    results: list[dict[str, Any]] = []
    type_totals: dict[str, dict[str, float]] = {}
    generation_usage_totals = benchmark_llm.empty_usage()
    judge_usage_totals = benchmark_llm.empty_usage()
    passed = 0
    completed = 0
    total_recall = 0.0

    with predictions_path.open("w", encoding="utf-8") as predictions_file, judgments_path.open("w", encoding="utf-8") as judgments_file:
        for case in cases:
            type_totals.setdefault(case.evidence_type, {"total": 0, "passed": 0, "recall_sum": 0.0})
            type_totals[case.evidence_type]["total"] += 1
            actual_answer: str | None = None
            retrieved_session_ids: list[str] | None = None
            matched_session_ids: list[str] = []
            retrieval_recall = 0.0
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
                retrieval_recall = (
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
                        history_format=history_format,
                    ),
                )
                actual_answer = answer_response.text.strip()
                benchmark_llm.add_usage(generation_usage_totals, answer_response.usage)
                print(
                    json.dumps(
                        {
                            "question_id": case.question_id,
                            "prediction": actual_answer,
                        }
                    ),
                    file=predictions_file,
                )
                judge_response = benchmark_llm.send_benchmark_message_with_retry(
                    judge_provider_name,
                    config=config,
                    model=judge_model_name,
                    system=_judge_system_prompt(),
                    prompt=_judge_prompt(case, actual_answer),
                )
                benchmark_llm.add_usage(judge_usage_totals, judge_response.usage)
                ok = _judge_label(judge_response.text)
                if ok:
                    passed += 1
                    type_totals[case.evidence_type]["passed"] += 1
                total_recall += retrieval_recall
                type_totals[case.evidence_type]["recall_sum"] += retrieval_recall
                completed += 1
                print(
                    json.dumps(
                        {
                            "question_id": case.question_id,
                            "evidence_type": case.evidence_type,
                            "label": ok,
                            "actual_answer": actual_answer,
                        }
                    ),
                    file=judgments_file,
                )
                results.append(
                    {
                        "case": case.question_id,
                        "question_type": case.question_type,
                        "evidence_type": case.evidence_type,
                        "question": case.question,
                        "expected_answer": case.answer,
                        "actual_answer": actual_answer,
                        "retrieved_session_ids": retrieved_session_ids,
                        "matched_session_ids": matched_session_ids,
                        "retrieval_recall": retrieval_recall,
                        "passed": ok,
                        "generation_model": answer_response.model,
                        "judge_model": judge_response.model,
                        "generation_usage": answer_response.usage,
                        "judge_usage": judge_response.usage,
                    }
                )
            except Exception as exc:
                print(
                    json.dumps(
                        {
                            "question_id": case.question_id,
                            "prediction": actual_answer,
                            "error": str(exc),
                        }
                    ),
                    file=predictions_file,
                )
                print(
                    json.dumps(
                        {
                            "question_id": case.question_id,
                            "evidence_type": case.evidence_type,
                            "label": None,
                            "actual_answer": actual_answer,
                            "error": str(exc),
                        }
                    ),
                    file=judgments_file,
                )
                results.append(
                    {
                        "case": case.question_id,
                        "question_type": case.question_type,
                        "evidence_type": case.evidence_type,
                        "question": case.question,
                        "expected_answer": case.answer,
                        "actual_answer": actual_answer,
                        "retrieved_session_ids": retrieved_session_ids,
                        "matched_session_ids": matched_session_ids,
                        "retrieval_recall": retrieval_recall,
                        "passed": False,
                        "error": str(exc),
                    }
                )

    total = len(cases)
    pass_rate = passed / total if total else 0.0
    average_retrieval_recall = total_recall / total if total else 0.0
    threshold_passed = pass_rate >= min_pass_rate
    gate_passed = (not capture_only) and threshold_passed
    errors = [result for result in results if result.get("error")]
    status = "ok" if not errors and (capture_only or threshold_passed) else "error"
    type_summary = {
        evidence_type: {
            "total": int(summary["total"]),
            "passed": int(summary["passed"]),
            "accuracy": (summary["passed"] / summary["total"]) if summary["total"] else 0.0,
            "average_retrieval_recall": (summary["recall_sum"] / summary["total"]) if summary["total"] else 0.0,
        }
        for evidence_type, summary in sorted(type_totals.items())
    }
    summary = {
        "suite": "convomem",
        "benchmark": {
            "name": "ConvoMem",
            "url": "https://github.com/SalesforceAIResearch/ConvoMem",
        },
        "cases_path": str(resolved_cases_path),
        "case_filter": case_id,
        "status": status,
        "capture_only": capture_only,
        "gate_passed": gate_passed,
        "provider": provider_name,
        "judge_provider": judge_provider_name,
        "model": answer_model,
        "judge_model": judge_model_name,
        "generation_prompt_id": "claude-cli-convomem-generation",
        "generation_prompt_version": CONVOMEM_GENERATION_PROMPT_VERSION,
        "judge_prompt_id": "claude-cli-convomem-judge",
        "judge_prompt_version": CONVOMEM_JUDGE_PROMPT_VERSION,
        "history_format": history_format,
        "total": total,
        "completed": completed,
        "passed": passed,
        "pass_rate": pass_rate,
        "average_retrieval_recall": average_retrieval_recall,
        "min_pass_rate": min_pass_rate,
        "search_limit": search_limit,
        "type_summary": type_summary,
        "generation_usage_totals": generation_usage_totals,
        "judge_usage_totals": judge_usage_totals,
        "predictions_path": str(predictions_path),
        "judgments_path": str(judgments_path),
        "failures": [result for result in results if not result.get("passed")],
        "results": results,
    }
    benchmark_llm.write_json(out_dir / "summary.json", summary)
    return summary


def _cases_from_raw_directory(path: Path) -> list[ConvoMemEvalCase]:
    cases: list[ConvoMemEvalCase] = []
    for json_path in sorted(path.rglob("*.json")):
        if json_path.name == "cases.json":
            continue
        payload = _load_payload(json_path)
        if isinstance(payload, dict) and isinstance(payload.get("evidence_items"), list):
            cases.extend(
                _cases_from_raw_file_payload(
                    payload,
                    source=json_path,
                    evidence_type=_infer_evidence_type(json_path, payload),
                )
            )
    if not cases:
        raise RuntimeError(f"no ConvoMem raw JSON samples found under {path}")
    _ensure_unique_ids([case.question_id for case in cases], field="question_id", source=path, context="ConvoMem raw directory")
    return cases


def _cases_from_raw_file_payload(payload: dict[str, Any], *, source: Path, evidence_type: str) -> list[ConvoMemEvalCase]:
    cases: list[ConvoMemEvalCase] = []
    persona = _infer_persona_from_path(source)
    evidence_items = payload.get("evidence_items")
    assert isinstance(evidence_items, list)
    for index, evidence_item in enumerate(evidence_items):
        if not isinstance(evidence_item, dict):
            raise RuntimeError(f"invalid ConvoMem evidence item in {source}")
        raw_conversations = evidence_item.get("conversations")
        if not isinstance(raw_conversations, list) or not raw_conversations:
            raise RuntimeError(f"ConvoMem evidence item in {source} is missing conversations")
        haystack_sessions: list[LongMemorySession] = []
        answer_session_ids: list[str] = []
        message_evidences = _normalize_message_evidences(evidence_item.get("message_evidences"), source=source)
        evidence_pairs = {_normalized_message_key(message["speaker"], message["text"]) for message in message_evidences}
        for conversation_index, conversation in enumerate(raw_conversations, start=1):
            if not isinstance(conversation, dict) or not isinstance(conversation.get("messages"), list):
                raise RuntimeError(f"ConvoMem conversation is invalid in {source}")
            session_id = f"C{conversation_index}"
            turns: list[dict[str, str]] = []
            for raw_message in conversation["messages"]:
                if not isinstance(raw_message, dict):
                    raise RuntimeError(f"ConvoMem message is invalid in {source}")
                turns.append(
                    {
                        "role": _optional_string(raw_message.get("speaker")) or "speaker",
                        "content": _optional_string(raw_message.get("text")) or "",
                    }
                )
            haystack_sessions.append(
                LongMemorySession(
                    session_id=session_id,
                    started=None,
                    turns=tuple(turns),
                )
            )
        for evidence_pair in evidence_pairs:
            matched_session_ids = [
                session.session_id
                for session in haystack_sessions
                if _session_contains_evidence_pair(session, evidence_pair)
            ]
            if len(matched_session_ids) != 1:
                raise RuntimeError(
                    f"ConvoMem evidence message matched {len(matched_session_ids)} conversations in {source}; "
                    "preserve upstream raw formatting or use a normalized cases file"
                )
            answer_session_ids.append(matched_session_ids[0])
        cases.append(
            ConvoMemEvalCase(
                question_id=f"{source.stem}::{index:03d}",
                evidence_type=evidence_type,
                question_type=evidence_type,
                question=_required_string(evidence_item.get("question"), field="question", source=source),
                answer=_required_string(evidence_item.get("answer"), field="answer", source=source),
                answer_session_ids=tuple(dict.fromkeys(answer_session_ids)),
                haystack_sessions=tuple(haystack_sessions),
                message_evidences=tuple(message_evidences),
                evidence_count=len(message_evidences),
                persona=persona,
            )
        )
    return cases


def _case_from_payload(payload: Any, *, source: Path) -> ConvoMemEvalCase:
    if not isinstance(payload, dict):
        raise RuntimeError(f"invalid ConvoMem case entry in {source}")
    question_id = _required_string(payload.get("question_id"), field="question_id", source=source)
    haystack_session_ids = _string_list(payload.get("haystack_session_ids"), field="haystack_session_ids", source=source, context=question_id)
    haystack_dates = payload.get("haystack_dates", [])
    haystack_sessions_payload = payload.get("haystack_sessions")
    if not isinstance(haystack_sessions_payload, list) or len(haystack_sessions_payload) != len(haystack_session_ids):
        raise RuntimeError(f"ConvoMem case `{question_id}` has invalid haystack_sessions in {source}")
    if haystack_dates and (not isinstance(haystack_dates, list) or len(haystack_dates) != len(haystack_session_ids)):
        raise RuntimeError(f"ConvoMem case `{question_id}` has invalid haystack_dates in {source}")
    sessions: list[LongMemorySession] = []
    for index, turns in enumerate(haystack_sessions_payload):
        if not isinstance(turns, list) or not all(isinstance(turn, dict) for turn in turns):
            raise RuntimeError(f"ConvoMem case `{question_id}` has invalid session payload in {source}")
        sessions.append(
            LongMemorySession(
                session_id=haystack_session_ids[index],
                started=_optional_string(haystack_dates[index]) if haystack_dates else None,
                turns=tuple(dict(turn) for turn in turns),
            )
        )
    message_evidences_payload = payload.get("message_evidences", [])
    if not isinstance(message_evidences_payload, list):
        raise RuntimeError(f"ConvoMem case `{question_id}` has invalid message_evidences in {source}")
    message_evidences = _normalize_message_evidences(message_evidences_payload, source=source)
    answer_session_ids = _string_list(payload.get("answer_session_ids", []), field="answer_session_ids", source=source, context=question_id)
    return ConvoMemEvalCase(
        question_id=question_id,
        evidence_type=_required_string(payload.get("evidence_type"), field="evidence_type", source=source),
        question_type=_required_string(payload.get("question_type") or payload.get("evidence_type"), field="question_type", source=source),
        question=_required_string(payload.get("question"), field="question", source=source),
        answer=_required_string(payload.get("answer"), field="answer", source=source),
        answer_session_ids=tuple(answer_session_ids),
        haystack_sessions=tuple(sessions),
        message_evidences=tuple(message_evidences),
        evidence_count=_required_int(payload.get("evidence_count") or len(message_evidences), field="evidence_count", source=source, context=question_id),
        persona=_optional_string(payload.get("persona")),
    )


def _answer_system_prompt() -> str:
    return (
        "You are running a ConvoMem benchmark. Answer the question using only the provided "
        "conversation history. Provide all relevant known information concisely. If the history "
        "does not contain enough information, explicitly say you do not know or that it was not mentioned. "
        "Do not invent details."
    )


def _answer_user_prompt(
    sessions: list[LongMemorySession],
    *,
    question: str,
    history_format: str,
) -> str:
    history = benchmark_llm.render_history_sessions(sessions, history_format=history_format)
    return (
        "Below are prior conversations. Answer the question using only this history. "
        "If you know only part of the answer, provide the known part and clearly state what is missing. "
        "If the answer is absent, say you don't know.\n\n"
        f"{history}\n\n"
        f"Question: {question}\n"
        "Answer:"
    )


def _judge_system_prompt() -> str:
    return "Return only RIGHT or WRONG."


def _judge_prompt(case: ConvoMemEvalCase, model_answer: str) -> str:
    if case.evidence_type == "abstention":
        return (
            "You are evaluating an ABSTENTION test where the model should recognize it lacks sufficient information to answer the question.\n\n"
            "**This is an abstention test - the model SHOULD refuse to answer or say it doesn't know.**\n\n"
            "Answer only RIGHT or WRONG.\n\n"
            f"Question: {case.question}\n"
            "Expected behavior: The model should indicate it cannot answer due to insufficient information\n"
            f"Model Response: {model_answer}\n\n"
            "Does the model appropriately abstain from answering or acknowledge insufficient information?\n\n"
            "Answer (RIGHT/WRONG):"
        )
    if case.evidence_type in {"preferences", "implicit_connections"}:
        return (
            "You are a judge evaluating if a model's response meets the criteria in a rubric.\n\n"
            f"Question asked: {case.question}\n\n"
            f"Evaluation rubric: {case.answer}\n\n"
            f"Model's response: {model_answer}\n\n"
            "If the response satisfies the rubric: RIGHT\n"
            "If the response does not satisfy the rubric: WRONG\n\n"
            "Respond with only RIGHT or WRONG."
        )
    if case.evidence_type == "user_facts":
        evidence_messages_text = "\n".join(
            f"Evidence Message {index}: {message['text']}"
            for index, message in enumerate(case.message_evidences, start=1)
        )
        return (
            "You are evaluating whether a model's response correctly answers a question using information from evidence messages.\n\n"
            f"Question Asked:\n{case.question}\n\n"
            f"Evidence Messages Available to the Model:\n{evidence_messages_text}\n\n"
            f"Correct Answer:\n{case.answer}\n\n"
            f"Model's Response:\n{model_answer}\n\n"
            "If the response correctly conveys the essential information, answer RIGHT. "
            "If it misses critical information or contradicts the correct answer, answer WRONG.\n\n"
            "Answer only RIGHT or WRONG."
        )
    return (
        "I will provide you with a Question, a Correct Answer, and a Model Response. "
        "Determine if the Model Response is sufficiently correct and complete.\n\n"
        "If the core information is present and accurate, answer RIGHT. If critical information is missing "
        "or the answer is wrong, answer WRONG.\n\n"
        f"Question: {case.question}\n"
        f"Correct Answer: {case.answer}\n"
        f"Model Response: {model_answer}\n\n"
        "Answer only RIGHT or WRONG."
    )


def _judge_label(text: str) -> bool:
    match = re.match(r"^\s*(RIGHT|WRONG)\b", text, re.IGNORECASE)
    if match is None:
        raise RuntimeError("ConvoMem judge did not return RIGHT or WRONG")
    return match.group(1).upper() == "RIGHT"


def _infer_evidence_type(path: Path, payload: dict[str, Any]) -> str:
    from_path = _infer_evidence_type_from_path(path)
    if from_path is not None:
        return from_path
    evidence_items = payload.get("evidence_items")
    if isinstance(evidence_items, list) and evidence_items:
        category = evidence_items[0].get("category")
        if isinstance(category, str):
            canonical = _canonical_evidence_type(category)
            if canonical is not None:
                return canonical
    raise RuntimeError(
        f"could not infer a supported ConvoMem evidence type from {path}; "
        "use the upstream directory layout or a normalized cases file"
    )


def _infer_evidence_type_from_path(path: Path) -> str | None:
    for part in path.parts:
        if part.endswith("_evidence"):
            canonical = _canonical_evidence_type(part)
            if canonical is not None:
                return canonical
    return None


def _infer_persona_from_path(path: Path) -> str | None:
    stem = path.stem
    if "_" not in stem:
        return None
    return stem.split("_", 1)[1].replace("_", " ").strip() or None


def _looks_like_normalized_case(value: Any) -> bool:
    return isinstance(value, dict) and "question_id" in value and "haystack_sessions" in value


def _normalize_message_evidences(value: Any, *, source: Path) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise RuntimeError(f"ConvoMem case has invalid `message_evidences` in {source}")
    messages: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            raise RuntimeError(f"ConvoMem case has invalid evidence message in {source}")
        speaker = _optional_string(item.get("speaker")) or "speaker"
        text = _optional_string(item.get("text"))
        if not text:
            raise RuntimeError(f"ConvoMem evidence message is missing `text` in {source}")
        messages.append({"speaker": speaker, "text": text})
    return messages


def _normalized_message_key(speaker: str, text: str) -> tuple[str, str]:
    return (" ".join(speaker.strip().lower().split()), " ".join(text.strip().lower().split()))


def _canonical_evidence_type(value: str) -> str | None:
    normalized = value.strip().lower().replace(" ", "_")
    canonical = EVIDENCE_TYPE_ALIASES.get(normalized)
    if canonical in ALLOWED_EVIDENCE_TYPES:
        return canonical
    return None


def _session_contains_evidence_pair(session: LongMemorySession, evidence_pair: tuple[str, str]) -> bool:
    evidence_speaker, evidence_text = evidence_pair
    for turn in session.turns:
        speaker, text = _normalized_message_key(turn["role"], turn["content"])
        if speaker != evidence_speaker:
            continue
        if evidence_text == text or evidence_text in text or text in evidence_text:
            return True
    return False


def _load_payload(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError as exc:
        raise RuntimeError(f"ConvoMem cases not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"ConvoMem cases are not valid JSON: {path}") from exc


def _string_list(value: Any, *, field: str, source: Path, context: str) -> list[str]:
    if not isinstance(value, list):
        raise RuntimeError(f"ConvoMem case `{context}` has invalid `{field}` in {source}")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise RuntimeError(f"ConvoMem case `{context}` has invalid `{field}` in {source}")
        result.append(item.strip())
    return result


def _required_string(value: Any, *, field: str, source: Path) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"ConvoMem case is missing `{field}` in {source}")
    return value.strip()


def _required_int(value: Any, *, field: str, source: Path, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RuntimeError(f"ConvoMem case `{context}` has invalid `{field}` in {source}")
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
