from __future__ import annotations

import copy
from contextlib import contextmanager
import json
import os
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Iterator

from umx.config import UMXConfig, save_config
from umx.scope import config_path, init_local_umx, init_project_memory, project_memory_dir
from umx.search import REFERENCE_STOPWORDS, TERM_RE, search_sessions
from umx.sessions import write_session


@dataclass(slots=True, frozen=True)
class LongMemorySession:
    session_id: str
    started: str | None
    turns: tuple[dict[str, Any], ...]


@dataclass(slots=True, frozen=True)
class LongMemoryEvalCase:
    question_id: str
    question_type: str
    question: str
    answer_session_ids: tuple[str, ...]
    haystack_sessions: tuple[LongMemorySession, ...]


def load_long_memory_eval_cases(cases_path: Path) -> list[LongMemoryEvalCase]:
    resolved = _resolve_cases_path(cases_path)
    try:
        payload = json.loads(resolved.read_text())
    except FileNotFoundError as exc:
        raise RuntimeError(f"long-memory eval cases not found: {resolved}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"long-memory eval cases are not valid JSON: {resolved}") from exc
    if not isinstance(payload, list):
        raise RuntimeError("long-memory eval cases file must contain a JSON array")
    return [_case_from_payload(item, source=resolved) for item in payload]


def run_long_memory_eval(
    cases_path: Path,
    config: UMXConfig,
    *,
    case_id: str | None = None,
    min_pass_rate: float = 1.0,
    search_limit: int = 5,
) -> dict[str, Any]:
    cases = load_long_memory_eval_cases(cases_path)
    if case_id is not None:
        cases = [case for case in cases if case.question_id == case_id]
    if not cases:
        raise RuntimeError("no long-memory eval cases matched the requested selection")
    if not 0 <= min_pass_rate <= 1:
        raise RuntimeError("long-memory eval min_pass_rate must be between 0 and 1")
    if search_limit <= 0:
        raise RuntimeError("long-memory eval search_limit must be greater than 0")

    results: list[dict[str, Any]] = []
    type_summary: dict[str, dict[str, float]] = {}
    passed = 0
    total_recall = 0.0

    for case in cases:
        type_summary.setdefault(case.question_type, {"total": 0, "passed": 0, "recall_sum": 0.0})
        type_summary[case.question_type]["total"] += 1
        try:
            actual_session_ids = _run_case(case, config=config, search_limit=search_limit)
            matched_session_ids = [
                session_id for session_id in case.answer_session_ids if session_id in actual_session_ids
            ]
            if case.answer_session_ids:
                recall = len(matched_session_ids) / len(case.answer_session_ids)
                ok = recall == 1.0
            else:
                recall = 1.0 if not actual_session_ids else 0.0
                ok = not actual_session_ids
            if ok:
                passed += 1
                type_summary[case.question_type]["passed"] += 1
            type_summary[case.question_type]["recall_sum"] += recall
            total_recall += recall
            results.append(
                {
                    "case": case.question_id,
                    "question_type": case.question_type,
                    "expected_session_ids": list(case.answer_session_ids),
                    "actual_session_ids": actual_session_ids,
                    "matched_session_ids": matched_session_ids,
                    "recall": recall,
                    "passed": ok,
                }
            )
        except Exception as exc:
            results.append(
                {
                    "case": case.question_id,
                    "question_type": case.question_type,
                    "expected_session_ids": list(case.answer_session_ids),
                    "actual_session_ids": None,
                    "matched_session_ids": [],
                    "recall": 0.0,
                    "passed": False,
                    "error": str(exc),
                }
            )

    total = len(cases)
    pass_rate = passed / total if total else 0.0
    average_recall = total_recall / total if total else 0.0
    failures = [result for result in results if not result["passed"]]
    type_payload = {
        question_type: {
            "total": int(summary["total"]),
            "passed": int(summary["passed"]),
            "average_recall": (summary["recall_sum"] / summary["total"]) if summary["total"] else 0.0,
        }
        for question_type, summary in type_summary.items()
    }
    status = "ok" if pass_rate >= min_pass_rate else "error"
    return {
        "status": status,
        "total": total,
        "passed": passed,
        "pass_rate": pass_rate,
        "min_pass_rate": min_pass_rate,
        "average_recall": average_recall,
        "search_limit": search_limit,
        "type_summary": type_payload,
        "failures": failures,
        "results": results,
    }


def _resolve_cases_path(cases_path: Path) -> Path:
    return cases_path / "cases.json" if cases_path.is_dir() else cases_path


def _case_from_payload(payload: Any, *, source: Path) -> LongMemoryEvalCase:
    if not isinstance(payload, dict):
        raise RuntimeError(f"invalid long-memory eval case entry in {source}")
    question_id = _required_string(payload.get("question_id") or payload.get("id"), field="question_id", source=source)
    question_type = _required_string(payload.get("question_type"), field="question_type", source=source)
    question = _required_string(payload.get("question"), field="question", source=source)

    answer_session_ids = payload.get("answer_session_ids", [])
    if not isinstance(answer_session_ids, list) or not all(
        isinstance(session_id, str) and session_id.strip() for session_id in answer_session_ids
    ):
        raise RuntimeError(f"long-memory eval case `{question_id}` has invalid answer_session_ids in {source}")

    haystack_sessions_payload = payload.get("haystack_sessions", [])
    haystack_session_ids = payload.get("haystack_session_ids", [])
    haystack_dates = payload.get("haystack_dates", [])
    if not isinstance(haystack_sessions_payload, list) or not haystack_sessions_payload:
        raise RuntimeError(f"long-memory eval case `{question_id}` has invalid haystack_sessions in {source}")
    if not isinstance(haystack_session_ids, list) or len(haystack_session_ids) != len(haystack_sessions_payload):
        raise RuntimeError(f"long-memory eval case `{question_id}` has invalid haystack_session_ids in {source}")
    if haystack_dates and (not isinstance(haystack_dates, list) or len(haystack_dates) != len(haystack_sessions_payload)):
        raise RuntimeError(f"long-memory eval case `{question_id}` has invalid haystack_dates in {source}")

    sessions: list[LongMemorySession] = []
    for index, turns in enumerate(haystack_sessions_payload):
        if not isinstance(turns, list) or not all(isinstance(turn, dict) for turn in turns):
            raise RuntimeError(
                f"long-memory eval case `{question_id}` has invalid session payload at index {index} in {source}"
            )
        sessions.append(
            LongMemorySession(
                session_id=_required_string(haystack_session_ids[index], field="haystack_session_ids", source=source),
                started=str(haystack_dates[index]) if haystack_dates else None,
                turns=tuple(dict(turn) for turn in turns),
            )
        )

    return LongMemoryEvalCase(
        question_id=question_id,
        question_type=question_type,
        question=question,
        answer_session_ids=tuple(answer_session_ids),
        haystack_sessions=tuple(sessions),
    )


def _required_string(value: Any, *, field: str, source: Path) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"long-memory eval case is missing `{field}` in {source}")
    return value.strip()


def _run_case(case: LongMemoryEvalCase, *, config: UMXConfig, search_limit: int) -> list[str]:
    with TemporaryDirectory(prefix="gitmem-long-memory-eval-") as temp_dir:
        temp_path = Path(temp_dir)
        with _temporary_umx_home(temp_path / "umxhome"):
            init_local_umx()
            eval_config = copy.deepcopy(config)
            save_config(config_path(), eval_config)

            project_dir = temp_path / "project"
            project_dir.mkdir()
            (project_dir / ".git").mkdir()
            init_project_memory(project_dir)
            project_repo = project_memory_dir(project_dir)

            for session in case.haystack_sessions:
                meta = {"session_id": session.session_id}
                if session.started:
                    meta["started"] = session.started
                write_session(project_repo, meta, [dict(turn) for turn in session.turns], auto_commit=False)

            results = search_sessions(
                project_repo,
                _search_query(case.question),
                limit=max(search_limit * 3, search_limit),
            )
            return _unique_session_ids(results, limit=search_limit)


def _unique_session_ids(results: list[dict[str, Any]], *, limit: int) -> list[str]:
    session_ids: list[str] = []
    seen: set[str] = set()
    for row in results:
        session_id = str(row.get("session_id", "")).strip()
        if not session_id or session_id in seen:
            continue
        seen.add(session_id)
        session_ids.append(session_id)
        if len(session_ids) >= limit:
            break
    return session_ids


def _search_query(question: str) -> str:
    terms = [
        match.group(0).lower()
        for match in TERM_RE.finditer(question)
        if len(match.group(0)) > 2 and match.group(0).lower() not in REFERENCE_STOPWORDS
    ]
    return " ".join(terms) if terms else question


@contextmanager
def _temporary_umx_home(home: Path) -> Iterator[None]:
    original = os.environ.get("UMX_HOME")
    os.environ["UMX_HOME"] = str(home)
    try:
        yield
    finally:
        if original is None:
            os.environ.pop("UMX_HOME", None)
        else:
            os.environ["UMX_HOME"] = original
