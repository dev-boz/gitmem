from __future__ import annotations

import copy
from contextlib import contextmanager
import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Iterator, Literal

from umx.config import UMXConfig, save_config
from umx.scope import config_path, init_local_umx, init_project_memory, project_memory_dir
from umx.search import REFERENCE_STOPWORDS, TERM_RE, search_sessions
from umx.sessions import write_session

QUESTION_STOPWORDS = {
    "did",
    "do",
    "does",
    "how",
    "many",
    "much",
    "what",
    "when",
    "where",
    "which",
    "who",
    "whom",
    "whose",
}


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
    question_date: str | None = None
    answer: Any = None


def load_long_memory_eval_cases(cases_path: Path) -> list[LongMemoryEvalCase]:
    resolved = _resolve_cases_path(cases_path)
    payload = _load_cases_payload(resolved)
    if isinstance(payload, list):
        cases = [_case_from_payload(item, source=resolved) for item in payload]
        _ensure_unique_ids(
            [case.question_id for case in cases],
            field="question_id",
            source=resolved,
            context="long-memory eval cases",
        )
        return cases
    if isinstance(payload, dict) and payload.get("format") == "longmemeval-manifest":
        return _cases_from_manifest(payload, source=resolved)
    raise RuntimeError(
        "long-memory eval cases file must contain a JSON array or a `longmemeval-manifest` object"
    )


def run_long_memory_eval(
    cases_path: Path,
    config: UMXConfig,
    *,
    case_id: str | None = None,
    min_pass_rate: float = 1.0,
    search_limit: int = 5,
) -> dict[str, Any]:
    resolved_cases_path = _resolve_cases_path(cases_path)
    cases = load_long_memory_eval_cases(resolved_cases_path)
    if case_id is not None:
        cases = [case for case in cases if case.question_id == case_id]
    if not cases:
        raise RuntimeError("no long-memory eval cases matched the requested selection")
    if not 0 <= min_pass_rate <= 1:
        raise RuntimeError("long-memory eval min_pass_rate must be between 0 and 1")
    if search_limit <= 0:
        raise RuntimeError("long-memory eval search_limit must be greater than 0")

    capture_only = min_pass_rate == 0
    results: list[dict[str, Any]] = []
    type_summary: dict[str, dict[str, float]] = {}
    passed = 0
    total_recall = 0.0

    for case in cases:
        type_summary.setdefault(case.question_type, {"total": 0, "passed": 0, "recall_sum": 0.0})
        type_summary[case.question_type]["total"] += 1
        try:
            actual_session_ids = _run_case(case, config=config, search_limit=search_limit)
        except Exception as exc:
            raise RuntimeError(f"long-memory eval case `{case.question_id}` failed: {exc}") from exc
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
    threshold_passed = pass_rate >= min_pass_rate
    gate_passed = (not capture_only) and threshold_passed
    status = "ok" if threshold_passed else "error"
    return {
        "suite": "long-memory",
        "benchmark": {
            "name": "LongMemEval",
            "url": "https://github.com/xiaowu0162/LongMemEval",
        },
        "cases_path": str(resolved_cases_path),
        "case_filter": case_id,
        "status": status,
        "capture_only": capture_only,
        "gate_passed": gate_passed,
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


def _load_cases_payload(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError as exc:
        raise RuntimeError(f"long-memory eval cases not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"long-memory eval cases are not valid JSON: {path}") from exc


def _cases_from_manifest(payload: dict[str, Any], *, source: Path) -> list[LongMemoryEvalCase]:
    dataset_path = _resolve_manifest_dataset_path(
        payload.get("dataset_path"),
        source=source,
        manifest_name="long-memory eval manifest",
    )
    question_ids = _required_string_list(
        payload.get("question_ids"),
        field="question_ids",
        source=source,
        context="long-memory eval manifest",
    )
    _ensure_unique_ids(
        question_ids,
        field="question_ids",
        source=source,
        context="long-memory eval manifest",
    )
    dataset_payload = _load_cases_payload(dataset_path)
    if not isinstance(dataset_payload, list):
        raise RuntimeError(f"long-memory eval manifest dataset must contain a JSON array: {dataset_path}")
    cases = [_case_from_payload(item, source=dataset_path) for item in dataset_payload]
    _ensure_unique_ids(
        [case.question_id for case in cases],
        field="question_id",
        source=dataset_path,
        context="long-memory eval dataset",
    )
    cases_by_id = {case.question_id: case for case in cases}
    selected: list[LongMemoryEvalCase] = []
    for question_id in question_ids:
        try:
            selected.append(cases_by_id[question_id])
        except KeyError as exc:
            raise RuntimeError(
                f"long-memory eval manifest references unknown question_id `{question_id}` in {dataset_path}"
            ) from exc
    return selected


def _resolve_manifest_dataset_path(
    value: Any,
    *,
    source: Path,
    manifest_name: str,
) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"{manifest_name} is missing `dataset_path` in {source}")
    dataset_path = Path(value.strip())
    if not dataset_path.is_absolute():
        dataset_path = (source.parent / dataset_path).resolve()
    return dataset_path


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
    normalized_answer_session_ids = [session_id.strip() for session_id in answer_session_ids]

    haystack_sessions_payload = payload.get("haystack_sessions", [])
    haystack_session_ids = payload.get("haystack_session_ids", [])
    haystack_dates = payload.get("haystack_dates", [])
    if not isinstance(haystack_sessions_payload, list) or not haystack_sessions_payload:
        raise RuntimeError(f"long-memory eval case `{question_id}` has invalid haystack_sessions in {source}")
    if not isinstance(haystack_session_ids, list) or len(haystack_session_ids) != len(haystack_sessions_payload):
        raise RuntimeError(f"long-memory eval case `{question_id}` has invalid haystack_session_ids in {source}")
    if haystack_dates and (not isinstance(haystack_dates, list) or len(haystack_dates) != len(haystack_sessions_payload)):
        raise RuntimeError(f"long-memory eval case `{question_id}` has invalid haystack_dates in {source}")
    normalized_haystack_session_ids = [
        _required_string(session_id, field="haystack_session_ids", source=source)
        for session_id in haystack_session_ids
    ]
    missing_answer_session_ids = [
        session_id
        for session_id in normalized_answer_session_ids
        if session_id not in normalized_haystack_session_ids
    ]
    if missing_answer_session_ids:
        missing_ids = ", ".join(f"`{session_id}`" for session_id in missing_answer_session_ids)
        raise RuntimeError(
            f"long-memory eval case `{question_id}` references unknown answer_session_ids {missing_ids} in {source}"
        )

    sessions: list[LongMemorySession] = []
    for index, turns in enumerate(haystack_sessions_payload):
        if not isinstance(turns, list) or not all(isinstance(turn, dict) for turn in turns):
            raise RuntimeError(
                f"long-memory eval case `{question_id}` has invalid session payload at index {index} in {source}"
            )
        sessions.append(
            LongMemorySession(
                session_id=normalized_haystack_session_ids[index],
                started=str(haystack_dates[index]) if haystack_dates else None,
                turns=tuple(dict(turn) for turn in turns),
            )
        )

    return LongMemoryEvalCase(
        question_id=question_id,
        question_type=question_type,
        question=question,
        answer_session_ids=tuple(normalized_answer_session_ids),
        haystack_sessions=tuple(sessions),
        question_date=_optional_string(payload.get("question_date")),
        answer=payload.get("answer"),
    )


def _required_string(value: Any, *, field: str, source: Path) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"long-memory eval case is missing `{field}` in {source}")
    return value.strip()


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return str(value).strip() or None


def _required_string_list(value: Any, *, field: str, source: Path, context: str) -> list[str]:
    if not isinstance(value, list) or not value or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise RuntimeError(f"{context} has invalid `{field}` in {source}")
    return [item.strip() for item in value]


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


def _run_case(case: LongMemoryEvalCase, *, config: UMXConfig, search_limit: int) -> list[str]:
    return [session.session_id for session in retrieve_long_memory_sessions(case, config=config, search_limit=search_limit)]


def retrieve_long_memory_sessions(
    case: LongMemoryEvalCase,
    *,
    config: UMXConfig,
    search_limit: int,
    rerank_mode: Literal["user-first", "full-first"] = "user-first",
) -> list[LongMemorySession]:
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

            original_by_storage: dict[str, str] = {}
            stored_sessions: list[tuple[str, LongMemorySession]] = []
            for index, session in enumerate(case.haystack_sessions):
                storage_session_id = _storage_session_id(
                    session.session_id,
                    started=session.started,
                    index=index,
                )
                original_by_storage[storage_session_id] = session.session_id
                stored_sessions.append((storage_session_id, session))
                meta = {
                    "session_id": storage_session_id,
                    "source_session_id": session.session_id,
                }
                if session.started:
                    meta["started"] = session.started
                write_session(
                    project_repo,
                    meta,
                    [dict(turn) for turn in session.turns],
                    config=eval_config,
                    auto_commit=False,
                )

            results = search_sessions(
                project_repo,
                _search_query(case.question),
                limit=max(search_limit * 20, 100),
            )
            candidate_storage_ids = _unique_session_ids(results, limit=max(search_limit * 20, 100))
            candidate_session_ids = _dedupe_preserve_order(
                [
                    original_by_storage.get(storage_session_id, storage_session_id)
                    for storage_session_id in candidate_storage_ids
                ],
                limit=max(search_limit * 20, 100),
            )
            ranked_session_ids = _rank_sessions(
                case.question,
                stored_sessions=stored_sessions,
                candidate_storage_ids=set(candidate_storage_ids),
                results=results,
                limit=search_limit,
                rerank_mode=rerank_mode,
            )
            if ranked_session_ids:
                return ranked_session_ids
            return _select_sessions_by_id(case.haystack_sessions, candidate_session_ids, limit=search_limit)


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


def _dedupe_preserve_order(values: list[str], *, limit: int) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = value.strip()
        if not item or item in seen:
            continue
        seen.add(item)
        unique.append(item)
        if len(unique) >= limit:
            break
    return unique


def _search_query(question: str) -> str:
    terms = _search_terms(question)
    return " ".join(terms) if terms else question


def _search_terms(question: str) -> list[str]:
    terms: list[str] = []
    for match in TERM_RE.finditer(question):
        term = match.group(0).lower()
        if (
            len(term) <= 2
            or term in REFERENCE_STOPWORDS
            or term in QUESTION_STOPWORDS
        ):
            continue
        if term not in terms:
            terms.append(term)
    return terms


def _rank_sessions(
    question: str,
    *,
    stored_sessions: list[tuple[str, LongMemorySession]],
    candidate_storage_ids: set[str],
    results: list[dict[str, Any]],
    limit: int,
    rerank_mode: Literal["user-first", "full-first"],
) -> list[LongMemorySession]:
    terms = _search_terms(question)
    if not terms:
        return []

    search_score_by_storage: dict[str, float] = {}
    search_rank_by_storage: dict[str, int] = {}
    for rank, row in enumerate(results):
        storage_session_id = str(row.get("session_id", "")).strip()
        if not storage_session_id:
            continue
        try:
            score = float(row.get("score", 0.0))
        except (TypeError, ValueError):
            score = 0.0
        if score > search_score_by_storage.get(storage_session_id, 0.0):
            search_score_by_storage[storage_session_id] = score
        search_rank_by_storage.setdefault(storage_session_id, rank)

    ranked: list[tuple[int, int, float, int, str, LongMemorySession]] = []
    for storage_session_id, session in stored_sessions:
        if storage_session_id not in candidate_storage_ids:
            continue
        user_score, full_score = _session_overlap_scores(session, terms=terms)
        search_score = search_score_by_storage.get(storage_session_id, 0.0)
        search_rank = search_rank_by_storage.get(storage_session_id, len(results))
        if user_score == 0 and full_score == 0 and search_score == 0.0:
            continue
        ranked.append(
            (user_score, full_score, search_score, search_rank, storage_session_id, session)
        )

    if rerank_mode == "full-first":
        ranked.sort(key=lambda item: (-item[1], -item[0], -item[2], item[3], item[4], item[5].session_id))
    else:
        ranked.sort(key=lambda item: (-item[0], -item[1], -item[2], item[3], item[4], item[5].session_id))
    selected: list[LongMemorySession] = []
    seen: set[str] = set()
    for _, _, _, _, _, session in ranked:
        if session.session_id in seen:
            continue
        seen.add(session.session_id)
        selected.append(session)
        if len(selected) >= limit:
            break
    return selected


def _select_sessions_by_id(
    sessions: tuple[LongMemorySession, ...],
    ordered_ids: list[str],
    *,
    limit: int,
) -> list[LongMemorySession]:
    selected: list[LongMemorySession] = []
    seen: set[str] = set()
    for session_id in ordered_ids:
        if session_id in seen:
            continue
        for session in sessions:
            if session.session_id == session_id:
                selected.append(session)
                seen.add(session_id)
                break
        if len(selected) >= limit:
            break
    return selected


def _session_overlap_scores(session: LongMemorySession, *, terms: list[str]) -> tuple[int, int]:
    user_text = " ".join(
        turn["content"]
        for turn in session.turns
        if turn.get("role") == "user" and isinstance(turn.get("content"), str)
    )
    full_text = " ".join(
        turn["content"]
        for turn in session.turns
        if isinstance(turn.get("content"), str)
    )
    return _text_overlap_score(user_text, terms=terms), _text_overlap_score(full_text, terms=terms)


def _text_overlap_score(text: str, *, terms: list[str]) -> int:
    lowered = text.lower()
    return sum(1 for term in terms if term in lowered)


def _storage_session_id(original_id: str, *, started: str | None, index: int) -> str:
    digest = hashlib.sha1(original_id.encode("utf-8")).hexdigest()[:12]
    year, month, day = _session_date_parts(started)
    return f"{year}-{month}-{day}-longmem-{index:03d}-{digest}"


def _session_date_parts(started: str | None) -> tuple[str, str, str]:
    if isinstance(started, str):
        match = re.search(r"(\d{4})[/-](\d{2})[/-](\d{2})", started)
        if match is not None:
            return match.group(1), match.group(2), match.group(3)
    return "2000", "01", "01"


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
