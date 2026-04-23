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
from umx.memory import add_fact
from umx.models import (
    ConsolidationStatus,
    Fact,
    MemoryType,
    Scope,
    SourceType,
    Verification,
)
from umx.scope import config_path, encode_scope_path, init_local_umx, init_project_memory, project_memory_dir
from umx.search import REFERENCE_STOPWORDS, TERM_RE, query_index, rebuild_index

QUESTION_STOPWORDS = {
    "did",
    "do",
    "does",
    "how",
    "what",
    "when",
    "where",
    "which",
    "who",
    "whom",
    "whose",
}


@dataclass(slots=True, frozen=True)
class RetrievalEvalCase:
    case_id: str
    question: str
    supporting_fact_ids: tuple[str, ...]
    facts: tuple[Fact, ...]


def load_retrieval_eval_cases(cases_path: Path) -> list[RetrievalEvalCase]:
    resolved = _resolve_cases_path(cases_path)
    try:
        payload = json.loads(resolved.read_text())
    except FileNotFoundError as exc:
        raise RuntimeError(f"retrieval eval cases not found: {resolved}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"retrieval eval cases are not valid JSON: {resolved}") from exc
    if not isinstance(payload, list):
        raise RuntimeError("retrieval eval cases file must contain a JSON array")
    return [_case_from_payload(item, source=resolved) for item in payload]


def run_retrieval_eval(
    cases_path: Path,
    config: UMXConfig,
    *,
    case_id: str | None = None,
    min_pass_rate: float = 1.0,
    top_k: int = 5,
) -> dict[str, Any]:
    cases = load_retrieval_eval_cases(cases_path)
    if case_id is not None:
        cases = [case for case in cases if case.case_id == case_id]
    if not cases:
        raise RuntimeError("no retrieval eval cases matched the requested selection")
    if not 0 <= min_pass_rate <= 1:
        raise RuntimeError("retrieval eval min_pass_rate must be between 0 and 1")
    if top_k <= 0:
        raise RuntimeError("retrieval eval top_k must be greater than 0")

    results: list[dict[str, Any]] = []
    passed = 0
    total_recall = 0.0

    for case in cases:
        try:
            actual_top_ids = _run_case(case, config=config, top_k=top_k)
            matched_fact_ids = [
                fact_id for fact_id in case.supporting_fact_ids if fact_id in actual_top_ids
            ]
            recall = len(matched_fact_ids) / len(case.supporting_fact_ids)
            ok = recall == 1.0
            if ok:
                passed += 1
            total_recall += recall
            results.append(
                {
                    "case": case.case_id,
                    "expected_fact_ids": list(case.supporting_fact_ids),
                    "actual_fact_ids": actual_top_ids,
                    "matched_fact_ids": matched_fact_ids,
                    "recall": recall,
                    "passed": ok,
                }
            )
        except Exception as exc:
            results.append(
                {
                    "case": case.case_id,
                    "expected_fact_ids": list(case.supporting_fact_ids),
                    "actual_fact_ids": None,
                    "matched_fact_ids": [],
                    "recall": 0.0,
                    "passed": False,
                    "error": str(exc),
                }
            )

    total = len(cases)
    pass_rate = passed / total if total else 0.0
    average_recall = total_recall / total if total else 0.0
    failures = [result for result in results if not result["passed"]]
    status = "ok" if pass_rate >= min_pass_rate else "error"
    return {
        "status": status,
        "total": total,
        "passed": passed,
        "pass_rate": pass_rate,
        "min_pass_rate": min_pass_rate,
        "average_recall": average_recall,
        "top_k": top_k,
        "failures": failures,
        "results": results,
    }


def _resolve_cases_path(cases_path: Path) -> Path:
    return cases_path / "cases.json" if cases_path.is_dir() else cases_path


def _case_from_payload(payload: Any, *, source: Path) -> RetrievalEvalCase:
    if not isinstance(payload, dict):
        raise RuntimeError(f"invalid retrieval eval case entry in {source}")
    case_id = _required_string(payload.get("id"), field="id", source=source)
    question = _required_string(payload.get("question"), field="question", source=source)
    supporting_fact_ids = payload.get("supporting_fact_ids", [])
    if not isinstance(supporting_fact_ids, list) or not all(
        isinstance(fact_id, str) and fact_id.strip() for fact_id in supporting_fact_ids
    ):
        raise RuntimeError(f"retrieval eval case `{case_id}` has invalid supporting_fact_ids in {source}")
    facts_payload = payload.get("facts", [])
    if not isinstance(facts_payload, list) or not facts_payload:
        raise RuntimeError(f"retrieval eval case `{case_id}` has invalid facts in {source}")
    return RetrievalEvalCase(
        case_id=case_id,
        question=question,
        supporting_fact_ids=tuple(supporting_fact_ids),
        facts=tuple(_fact_from_eval_payload(item) for item in facts_payload),
    )


def _required_string(value: Any, *, field: str, source: Path) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"retrieval eval case is missing `{field}` in {source}")
    return value.strip()


def _fact_from_eval_payload(payload: Any) -> Fact:
    if not isinstance(payload, dict):
        raise RuntimeError("retrieval eval fact payload must be an object")
    scope = Scope(str(payload.get("scope", Scope.PROJECT.value)))
    topic = str(payload.get("topic", "general"))
    if scope == Scope.FILE:
        topic = encode_scope_path(topic)
    return Fact(
        fact_id=_required_fact_string(payload.get("fact_id"), field="fact_id"),
        text=_required_fact_string(payload.get("text"), field="text"),
        scope=scope,
        topic=topic,
        encoding_strength=int(payload.get("encoding_strength", 4)),
        memory_type=MemoryType(str(payload.get("memory_type", MemoryType.EXPLICIT_SEMANTIC.value))),
        verification=Verification(str(payload.get("verification", Verification.CORROBORATED.value))),
        source_type=SourceType(str(payload.get("source_type", SourceType.GROUND_TRUTH_CODE.value))),
        source_tool=str(payload.get("source_tool", "retrieval-eval")),
        source_session=str(payload.get("source_session", "retrieval-eval")),
        consolidation_status=ConsolidationStatus(
            str(payload.get("consolidation_status", ConsolidationStatus.STABLE.value))
        ),
    )


def _required_fact_string(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"retrieval eval fact payload is missing `{field}`")
    return value.strip()


def _run_case(case: RetrievalEvalCase, *, config: UMXConfig, top_k: int) -> list[str]:
    with TemporaryDirectory(prefix="gitmem-retrieval-eval-") as temp_dir:
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

            for fact in case.facts:
                add_fact(project_repo, fact, auto_commit=False)
            rebuild_index(project_repo, config=eval_config)

            terms = _search_terms(case.question)
            candidates: dict[str, tuple[Fact, int, int, int]] = {}
            for query_order, query in enumerate(_candidate_queries(case.question, terms=terms)):
                for rank, fact in enumerate(
                    query_index(project_repo, query, limit=top_k, config=eval_config)
                ):
                    score = _fact_overlap_score(fact.text, terms)
                    existing = candidates.get(fact.fact_id)
                    candidate = (fact, score, query_order, rank)
                    if existing is None or _candidate_sort_key(candidate) < _candidate_sort_key(existing):
                        candidates[fact.fact_id] = candidate
            ordered = sorted(candidates.values(), key=_candidate_sort_key)
            return [fact.fact_id for fact, _, _, _ in ordered[:top_k]]


def _search_terms(question: str) -> list[str]:
    terms: list[str] = []
    for match in TERM_RE.finditer(question):
        term = match.group(0).lower()
        if len(term) <= 2 or term in REFERENCE_STOPWORDS or term in QUESTION_STOPWORDS:
            continue
        if term not in terms:
            terms.append(term)
    return terms


def _candidate_queries(question: str, *, terms: list[str]) -> list[str]:
    if not terms:
        return [question]
    strict = " ".join(terms)
    broad = " OR ".join(f'"{term.replace(chr(34), chr(34) * 2)}"' for term in terms)
    queries = [strict]
    if broad != strict:
        queries.append(broad)
    return queries


def _fact_overlap_score(text: str, terms: list[str]) -> int:
    fact_terms = {match.group(0).lower() for match in TERM_RE.finditer(text)}
    return sum(1 for term in terms if term in fact_terms)


def _candidate_sort_key(candidate: tuple[Fact, int, int, int]) -> tuple[int, int, int, str]:
    fact, score, query_order, rank = candidate
    return (-score, query_order, rank, fact.fact_id)


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
