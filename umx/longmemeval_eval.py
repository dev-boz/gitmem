from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from umx.config import UMXConfig
from umx.long_memory_eval import load_long_memory_eval_cases, retrieve_long_memory_sessions
from umx.providers import claude_cli as claude_cli_provider

LONGMEMEVAL_GENERATION_PROMPT_VERSION = "v1"
LONGMEMEVAL_JUDGE_PROMPT_VERSION = "v1"
CLAUDE_CLI_MAX_ATTEMPTS = 3


@dataclass(slots=True, frozen=True)
class LongMemEvalMessageResult:
    text: str
    model: str
    usage: dict[str, int]


def normalize_longmemeval_provider(provider: str | None) -> str:
    if provider is None:
        return "claude-cli"
    name = provider.strip().lower()
    if name in {"claude-cli", "claude-code", "cli", "oauth"}:
        return "claude-cli"
    raise RuntimeError(
        f"unknown LongMemEval provider: {provider!r} "
        "(expected `claude-cli`, which uses the local Claude Code OAuth session)"
    )


def run_longmemeval_eval(
    out_dir: Path,
    cases_path: Path,
    config: UMXConfig,
    *,
    case_id: str | None = None,
    min_pass_rate: float = 1.0,
    search_limit: int = 5,
    provider: str | None = None,
    model: str | None = None,
    judge_provider: str | None = None,
    judge_model: str | None = None,
    history_format: str = "json",
) -> dict[str, Any]:
    resolved_cases_path = cases_path / "cases.json" if cases_path.is_dir() else cases_path
    cases = load_long_memory_eval_cases(resolved_cases_path)
    if case_id is not None:
        cases = [case for case in cases if case.question_id == case_id]
    if not cases:
        raise RuntimeError("no LongMemEval cases matched the requested selection")
    if not 0 <= min_pass_rate <= 1:
        raise RuntimeError("LongMemEval min_pass_rate must be between 0 and 1")
    if search_limit <= 0:
        raise RuntimeError("LongMemEval search_limit must be greater than 0")
    provider_name = normalize_longmemeval_provider(provider)
    judge_provider_name = normalize_longmemeval_provider(judge_provider) if judge_provider else provider_name
    answer_model = model or config.dream.l2_model
    judge_model_name = judge_model or answer_model
    if history_format not in {"json", "nl"}:
        raise RuntimeError("LongMemEval history_format must be `json` or `nl`")

    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    hypotheses_path = out_dir / "hypotheses.jsonl"
    judgments_path = out_dir / "judgments.jsonl"

    capture_only = min_pass_rate == 0
    results: list[dict[str, Any]] = []
    type_totals: dict[str, dict[str, int]] = {}
    generation_usage_totals = _empty_usage()
    judge_usage_totals = _empty_usage()
    passed = 0
    completed = 0

    with hypotheses_path.open("w", encoding="utf-8") as hypotheses_file, judgments_path.open(
        "w", encoding="utf-8"
    ) as judgments_file:
        for case in cases:
            type_totals.setdefault(case.question_type, {"total": 0, "passed": 0})
            type_totals[case.question_type]["total"] += 1
            retrieved_session_ids: list[str] | None = None
            matched_session_ids: list[str] = []
            retrieval_recall = 0.0
            actual_answer: str | None = None
            generation_model: str | None = None
            generation_usage: dict[str, int] | None = None
            hypothesis_written = False
            try:
                if case.answer is None:
                    raise RuntimeError(
                        f"LongMemEval case `{case.question_id}` is missing an upstream `answer` field"
                    )
                retrieved_sessions = retrieve_long_memory_sessions(
                    case,
                    config=config,
                    search_limit=search_limit,
                    rerank_mode="full-first",
                )
                retrieved_session_ids = [session.session_id for session in retrieved_sessions]
                matched_session_ids = [
                    session_id
                    for session_id in case.answer_session_ids
                    if session_id in retrieved_session_ids
                ]
                retrieval_recall = (
                    len(matched_session_ids) / len(case.answer_session_ids)
                    if case.answer_session_ids
                    else (1.0 if not retrieved_session_ids else 0.0)
                )

                answer_response = _send_message_with_retry(
                    provider_name,
                    config=config,
                    model=answer_model,
                    system=_answer_system_prompt(),
                    prompt=_answer_user_prompt(
                        retrieved_sessions,
                        question=case.question,
                        question_date=case.question_date,
                        history_format=history_format,
                    ),
                )
                actual_answer = answer_response.text.strip()
                generation_model = answer_response.model
                generation_usage = answer_response.usage
                _add_usage(generation_usage_totals, answer_response.usage)
                print(
                    json.dumps(
                        {
                            "question_id": case.question_id,
                            "hypothesis": actual_answer,
                        }
                    ),
                    file=hypotheses_file,
                )
                hypothesis_written = True

                judge_response = _send_message_with_retry(
                    judge_provider_name,
                    config=config,
                    model=judge_model_name,
                    system=_judge_system_prompt(),
                    prompt=_judge_prompt(
                        question_type=case.question_type,
                        question=case.question,
                        answer=case.answer,
                        response=actual_answer,
                        question_id=case.question_id,
                    ),
                )
                _add_usage(judge_usage_totals, judge_response.usage)
                score = 1.0 if _judge_label(judge_response.text) else 0.0
                ok = score == 1.0
                if ok:
                    passed += 1
                    type_totals[case.question_type]["passed"] += 1
                completed += 1

                judgment_entry = {
                    "question_id": case.question_id,
                    "question_type": case.question_type,
                    "expected_answer": _answer_text(case.answer),
                    "actual_answer": actual_answer,
                    "retrieved_session_ids": retrieved_session_ids,
                    "matched_session_ids": matched_session_ids,
                    "retrieval_recall": retrieval_recall,
                    "autoeval_label": {
                        "provider": judge_provider_name,
                        "model": judge_response.model,
                        "label": ok,
                    },
                }
                print(json.dumps(judgment_entry), file=judgments_file)

                results.append(
                    {
                        "case": case.question_id,
                        "question_type": case.question_type,
                        "question": case.question,
                        "expected_answer": _answer_text(case.answer),
                        "actual_answer": actual_answer,
                        "retrieved_session_ids": retrieved_session_ids,
                        "matched_session_ids": matched_session_ids,
                        "retrieval_recall": retrieval_recall,
                        "answer_score": score,
                        "passed": ok,
                        "generation_model": answer_response.model,
                        "judge_model": judge_response.model,
                        "generation_usage": answer_response.usage,
                        "judge_usage": judge_response.usage,
                    }
                )
            except Exception as exc:
                if not hypothesis_written:
                    print(
                        json.dumps(
                            {
                                "question_id": case.question_id,
                                "hypothesis": actual_answer,
                                "error": str(exc),
                            }
                        ),
                        file=hypotheses_file,
                    )
                print(
                    json.dumps(
                        {
                            "question_id": case.question_id,
                            "question_type": case.question_type,
                            "expected_answer": _answer_text(case.answer),
                            "actual_answer": actual_answer,
                            "retrieved_session_ids": retrieved_session_ids,
                            "matched_session_ids": matched_session_ids,
                            "retrieval_recall": retrieval_recall,
                            "autoeval_label": None,
                            "error": str(exc),
                        }
                    ),
                    file=judgments_file,
                )
                results.append(
                    {
                        "case": case.question_id,
                        "question_type": case.question_type,
                        "question": case.question,
                        "expected_answer": _answer_text(case.answer),
                        "actual_answer": actual_answer,
                        "retrieved_session_ids": retrieved_session_ids,
                        "matched_session_ids": matched_session_ids,
                        "retrieval_recall": retrieval_recall,
                        "answer_score": 0.0,
                        "passed": False,
                        "generation_model": generation_model,
                        "generation_usage": generation_usage,
                        "error": str(exc),
                    }
                )

    total = len(cases)
    pass_rate = passed / total if total else 0.0
    threshold_passed = pass_rate >= min_pass_rate
    gate_passed = (not capture_only) and threshold_passed
    errors = [result for result in results if result.get("error")]
    status = "ok" if not errors and (capture_only or threshold_passed) else "error"
    abstention_scores = [
        result["answer_score"]
        for result in results
        if result.get("question_type") == "abstention" and "answer_score" in result
    ]
    non_abstention_accuracies = [
        summary["passed"] / summary["total"]
        for question_type, summary in type_totals.items()
        if question_type != "abstention" and summary["total"]
    ]
    type_summary = {
        question_type: {
            "total": summary["total"],
            "passed": summary["passed"],
            "accuracy": (summary["passed"] / summary["total"]) if summary["total"] else 0.0,
        }
        for question_type, summary in sorted(type_totals.items())
    }

    summary = {
        "suite": "longmemeval",
        "benchmark": {
            "name": "LongMemEval",
            "url": "https://github.com/xiaowu0162/LongMemEval",
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
        "generation_prompt_id": _generation_prompt_id(provider_name),
        "generation_prompt_version": LONGMEMEVAL_GENERATION_PROMPT_VERSION,
        "judge_prompt_id": _judge_prompt_id(judge_provider_name),
        "judge_prompt_version": LONGMEMEVAL_JUDGE_PROMPT_VERSION,
        "history_format": history_format,
        "total": total,
        "completed": completed,
        "passed": passed,
        "pass_rate": pass_rate,
        "overall_accuracy": pass_rate,
        "task_averaged_accuracy": (
            sum(non_abstention_accuracies) / len(non_abstention_accuracies)
            if non_abstention_accuracies
            else None
        ),
        "abstention_accuracy": (
            sum(abstention_scores) / len(abstention_scores) if abstention_scores else None
        ),
        "min_pass_rate": min_pass_rate,
        "search_limit": search_limit,
        "type_summary": type_summary,
        "generation_usage_totals": generation_usage_totals,
        "judge_usage_totals": judge_usage_totals,
        "hypotheses_path": str(hypotheses_path),
        "judgments_path": str(judgments_path),
        "failures": [result for result in results if not result.get("passed")],
        "results": results,
    }
    _write_json(out_dir / "summary.json", summary)
    return summary


def _empty_usage() -> dict[str, int]:
    return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}


def _add_usage(target: dict[str, int], usage: dict[str, int]) -> None:
    for key in ("input_tokens", "output_tokens", "total_tokens"):
        value = usage.get(key)
        if isinstance(value, int):
            target[key] += value


def _answer_system_prompt() -> str:
    return (
        "You are running a LongMemEval benchmark. Answer the question using only the "
        "provided history chats. If the history does not contain enough information, "
        "say that the question is unanswerable from the provided history. Keep the answer concise "
        "but complete, and do not omit disambiguating details such as dates, locations, counts, "
        "or qualifiers that are present in the history. Return only the answer."
    )


def _answer_user_prompt(
    sessions: list[Any],
    *,
    question: str,
    question_date: str | None,
    history_format: str,
) -> str:
    history_chunks: list[str] = []
    for index, session in enumerate(sorted(sessions, key=lambda item: item.started or ""), start=1):
        turns = []
        for turn in session.turns:
            normalized_turn = {key: value for key, value in dict(turn).items() if key != "has_answer"}
            turns.append(normalized_turn)
        if history_format == "json":
            rendered_turns = "\n" + json.dumps(turns)
        else:
            rendered_lines = [f"{turn['role']}: {turn['content'].strip()}" for turn in turns]
            rendered_turns = "\n".join(rendered_lines)
        history_chunks.append(
            f"\n### Session {index}:\n"
            f"Session Date: {session.started or 'unknown'}\n"
            f"Session Content:\n{rendered_turns}\n"
        )
    history_string = "".join(history_chunks).strip()
    current_date = question_date or "unknown"
    return (
        "I will give you several history chats between you and a user. Please answer the "
        "question based on the relevant chat history.\n\n"
        "Return the most specific standalone answer span supported by the history. "
        "Do not shorten place names or drop dates, locations, counts, or other qualifiers "
        "that make the answer complete. Do not add explanation.\n\n"
        f"History Chats:\n\n{history_string}\n\n"
        f"Current Date: {current_date}\n"
        f"Question: {question}\n"
        "Answer:"
    )


def _judge_system_prompt() -> str:
    return "Return only yes or no."


def _judge_prompt(
    *,
    question_type: str,
    question: str,
    answer: Any,
    response: str,
    question_id: str,
) -> str:
    abstention = question_type == "abstention" or question_id.endswith("_abs")
    answer_text = _answer_text(answer)
    if abstention:
        return (
            "I will give you an unanswerable question, an explanation, and a response from a model. "
            "Please answer yes if the model correctly identifies the question as unanswerable. "
            "The model could say that the information is incomplete, or some other information is "
            "given but the asked information is not.\n\n"
            f"Question: {question}\n\n"
            f"Explanation: {answer_text}\n\n"
            f"Model Response: {response}\n\n"
            "Does the model correctly identify the question as unanswerable? Answer yes or no only."
        )
    if question_type in {"single-session-user", "single-session-assistant", "multi-session"}:
        return (
            "I will give you a question, a correct answer, and a response from a model. "
            "Please answer yes if the response contains the correct answer. Otherwise, answer no. "
            "If the response is equivalent to the correct answer or contains all the intermediate "
            "steps to get the correct answer, you should also answer yes. If the response only "
            "contains a subset of the information required by the answer, answer no.\n\n"
            f"Question: {question}\n\n"
            f"Correct Answer: {answer_text}\n\n"
            f"Model Response: {response}\n\n"
            "Is the model response correct? Answer yes or no only."
        )
    if question_type == "temporal-reasoning":
        return (
            "I will give you a question, a correct answer, and a response from a model. "
            "Please answer yes if the response contains the correct answer. Otherwise, answer no. "
            "If the response is equivalent to the correct answer or contains all the intermediate "
            "steps to get the correct answer, you should also answer yes. If the response only "
            "contains a subset of the information required by the answer, answer no. In addition, "
            "do not penalize off-by-one errors for the number of days. If the question asks for the "
            "number of days/weeks/months, etc., and the model makes off-by-one errors, the model's "
            "response is still correct.\n\n"
            f"Question: {question}\n\n"
            f"Correct Answer: {answer_text}\n\n"
            f"Model Response: {response}\n\n"
            "Is the model response correct? Answer yes or no only."
        )
    if question_type == "knowledge-update":
        return (
            "I will give you a question, a correct answer, and a response from a model. "
            "Please answer yes if the response contains the correct answer. Otherwise, answer no. "
            "If the response contains some previous information along with an updated answer, the "
            "response should be considered correct as long as the updated answer is the required answer.\n\n"
            f"Question: {question}\n\n"
            f"Correct Answer: {answer_text}\n\n"
            f"Model Response: {response}\n\n"
            "Is the model response correct? Answer yes or no only."
        )
    if question_type == "single-session-preference":
        return (
            "I will give you a question, a rubric for desired personalized response, and a response "
            "from a model. Please answer yes if the response satisfies the desired response. "
            "Otherwise, answer no. The model does not need to reflect all the points in the rubric. "
            "The response is correct as long as it recalls and utilizes the user's personal "
            "information correctly.\n\n"
            f"Question: {question}\n\n"
            f"Rubric: {answer_text}\n\n"
            f"Model Response: {response}\n\n"
            "Is the model response correct? Answer yes or no only."
        )
    raise RuntimeError(f"unsupported LongMemEval question_type `{question_type}`")


def _answer_text(answer: Any) -> str:
    if answer is None:
        return ""
    if isinstance(answer, str):
        return answer
    if isinstance(answer, (int, float, bool)):
        return str(answer)
    return json.dumps(answer, sort_keys=True)


def _judge_label(text: str) -> bool:
    match = re.match(r"^\s*(yes|no)\b", text, re.IGNORECASE)
    if match is None:
        raise RuntimeError("LongMemEval judge did not return a yes/no answer")
    if match.group(1).lower() == "yes":
        return True
    return False


def _send_message_with_provider(
    provider: str,
    *,
    config: UMXConfig,
    model: str,
    system: str,
    prompt: str,
) -> LongMemEvalMessageResult:
    if provider != "claude-cli":
        raise RuntimeError(
            f"unsupported LongMemEval provider `{provider}` "
            "(only `claude-cli` is available in this environment)"
        )
    response = claude_cli_provider.send_claude_cli_message(
        model=model,
        system=system,
        prompt=prompt,
    )
    return LongMemEvalMessageResult(text=response.text, model=response.model, usage=response.usage)


def _send_message_with_retry(
    provider: str,
    *,
    config: UMXConfig,
    model: str,
    system: str,
    prompt: str,
) -> LongMemEvalMessageResult:
    last_error: RuntimeError | None = None
    for attempt in range(1, CLAUDE_CLI_MAX_ATTEMPTS + 1):
        try:
            return _send_message_with_provider(
                provider,
                config=config,
                model=model,
                system=system,
                prompt=prompt,
            )
        except RuntimeError as exc:
            last_error = exc
            if provider != "claude-cli" or not _is_retryable_claude_error(exc) or attempt >= CLAUDE_CLI_MAX_ATTEMPTS:
                raise
            time.sleep(attempt * 2)
    assert last_error is not None
    raise last_error


def _is_retryable_claude_error(exc: RuntimeError) -> bool:
    message = str(exc)
    return message.startswith("Claude CLI ")


def _generation_prompt_id(provider: str) -> str:
    return "claude-cli-longmemeval-generation"


def _judge_prompt_id(provider: str) -> str:
    return "claude-cli-longmemeval-judge"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
