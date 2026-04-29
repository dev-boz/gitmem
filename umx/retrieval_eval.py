from __future__ import annotations

import copy
from contextlib import contextmanager
import hashlib
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
    answer: str | None = None


def load_retrieval_eval_cases(cases_path: Path) -> list[RetrievalEvalCase]:
    resolved = _resolve_cases_path(cases_path)
    payload = _load_cases_payload(resolved)
    if isinstance(payload, list):
        if _is_hotpotqa_payload_list(payload):
            cases = [_case_from_hotpotqa_payload(item, source=resolved) for item in payload]
        else:
            cases = [_case_from_payload(item, source=resolved) for item in payload]
        _ensure_unique_ids(
            [case.case_id for case in cases],
            field="question_id",
            source=resolved,
            context="retrieval eval cases",
        )
        return cases
    if isinstance(payload, dict) and payload.get("format") == "hotpotqa-manifest":
        return _cases_from_hotpotqa_manifest(payload, source=resolved)
    raise RuntimeError(
        "retrieval eval cases file must contain a JSON array or a `hotpotqa-manifest` object"
    )


def run_retrieval_eval(
    cases_path: Path,
    config: UMXConfig,
    *,
    case_id: str | None = None,
    min_pass_rate: float = 1.0,
    top_k: int = 5,
) -> dict[str, Any]:
    resolved_cases_path = _resolve_cases_path(cases_path)
    cases = load_retrieval_eval_cases(resolved_cases_path)
    if case_id is not None:
        cases = [case for case in cases if case.case_id == case_id]
    if not cases:
        raise RuntimeError("no retrieval eval cases matched the requested selection")
    if not 0 <= min_pass_rate <= 1:
        raise RuntimeError("retrieval eval min_pass_rate must be between 0 and 1")
    if top_k <= 0:
        raise RuntimeError("retrieval eval top_k must be greater than 0")

    capture_only = min_pass_rate == 0
    results: list[dict[str, Any]] = []
    passed = 0
    total_recall = 0.0
    total_answer_coverage = 0.0
    answer_coverage_cases = 0

    for case in cases:
        try:
            actual_top_ids = _run_case(case, config=config, top_k=top_k)
        except Exception as exc:
            raise RuntimeError(f"retrieval eval case `{case.case_id}` failed: {exc}") from exc
        matched_fact_ids = [
            fact_id for fact_id in case.supporting_fact_ids if fact_id in actual_top_ids
        ]
        recall = len(matched_fact_ids) / len(case.supporting_fact_ids)
        ok = recall == 1.0
        fact_text_by_id = {fact.fact_id: fact.text for fact in case.facts}
        answer_coverage = None
        if case.answer is not None:
            answer_coverage = 1.0 if _answer_present(case.answer, actual_top_ids, fact_text_by_id) else 0.0
            total_answer_coverage += answer_coverage
            answer_coverage_cases += 1
        if ok:
            passed += 1
        total_recall += recall
        result = {
            "case": case.case_id,
            "expected_fact_ids": list(case.supporting_fact_ids),
            "actual_fact_ids": actual_top_ids,
            "matched_fact_ids": matched_fact_ids,
            "recall": recall,
            "passed": ok,
        }
        if case.answer is not None:
            result["expected_answer"] = case.answer
            result["answer_coverage"] = answer_coverage
        results.append(result)

    total = len(cases)
    pass_rate = passed / total if total else 0.0
    average_recall = total_recall / total if total else 0.0
    average_answer_coverage = (
        total_answer_coverage / answer_coverage_cases if answer_coverage_cases else None
    )
    failures = [result for result in results if not result["passed"]]
    threshold_passed = pass_rate >= min_pass_rate
    gate_passed = (not capture_only) and threshold_passed
    status = "ok" if threshold_passed else "error"
    return {
        "suite": "retrieval",
        "benchmark": {
            "name": "HotpotQA",
            "url": "https://hotpotqa.github.io/",
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
        "average_answer_coverage": average_answer_coverage,
        "top_k": top_k,
        "failures": failures,
        "results": results,
    }


def _resolve_cases_path(cases_path: Path) -> Path:
    return cases_path / "cases.json" if cases_path.is_dir() else cases_path


def _load_cases_payload(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError as exc:
        raise RuntimeError(f"retrieval eval cases not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"retrieval eval cases are not valid JSON: {path}") from exc


def _is_hotpotqa_payload_list(payload: list[Any]) -> bool:
    if not payload:
        return False
    first = payload[0]
    return isinstance(first, dict) and (
        "_id" in first or "context" in first or "supporting_facts" in first
    )


def _cases_from_hotpotqa_manifest(payload: dict[str, Any], *, source: Path) -> list[RetrievalEvalCase]:
    dataset_path = _resolve_manifest_dataset_path(
        payload.get("dataset_path"),
        source=source,
        manifest_name="retrieval eval manifest",
    )
    question_ids = _required_string_list(
        payload.get("question_ids"),
        field="question_ids",
        source=source,
        context="retrieval eval manifest",
    )
    _ensure_unique_ids(
        question_ids,
        field="question_ids",
        source=source,
        context="retrieval eval manifest",
    )
    dataset_payload = _load_cases_payload(dataset_path)
    if not isinstance(dataset_payload, list):
        raise RuntimeError(f"retrieval eval manifest dataset must contain a JSON array: {dataset_path}")
    cases = [_case_from_hotpotqa_payload(item, source=dataset_path) for item in dataset_payload]
    _ensure_unique_ids(
        [case.case_id for case in cases],
        field="question_id",
        source=dataset_path,
        context="retrieval eval dataset",
    )
    cases_by_id = {case.case_id: case for case in cases}
    selected: list[RetrievalEvalCase] = []
    for question_id in question_ids:
        try:
            selected.append(cases_by_id[question_id])
        except KeyError as exc:
            raise RuntimeError(
                f"retrieval eval manifest references unknown question_id `{question_id}` in {dataset_path}"
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
    normalized_supporting_fact_ids = [fact_id.strip() for fact_id in supporting_fact_ids]
    facts = tuple(_fact_from_eval_payload(item) for item in facts_payload)
    known_fact_ids = {fact.fact_id for fact in facts}
    missing_fact_ids = [
        fact_id for fact_id in normalized_supporting_fact_ids if fact_id not in known_fact_ids
    ]
    if missing_fact_ids:
        missing_ids = ", ".join(f"`{fact_id}`" for fact_id in missing_fact_ids)
        raise RuntimeError(
            f"retrieval eval case `{case_id}` references unknown supporting_fact_ids {missing_ids} in {source}"
        )
    return RetrievalEvalCase(
        case_id=case_id,
        question=question,
        supporting_fact_ids=tuple(normalized_supporting_fact_ids),
        facts=facts,
        answer=_optional_string(payload.get("answer"), field="answer", source=source),
    )


def _required_string(value: Any, *, field: str, source: Path) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"retrieval eval case is missing `{field}` in {source}")
    return value.strip()


def _optional_string(value: Any, *, field: str, source: Path) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"retrieval eval case has invalid `{field}` in {source}")
    return value.strip()


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


def _case_from_hotpotqa_payload(payload: Any, *, source: Path) -> RetrievalEvalCase:
    if not isinstance(payload, dict):
        raise RuntimeError(f"invalid HotpotQA payload in {source}")
    case_id = _required_string(payload.get("_id"), field="_id", source=source)
    question = _required_string(payload.get("question"), field="question", source=source)
    answer = _optional_string(payload.get("answer"), field="answer", source=source)
    supporting_facts = payload.get("supporting_facts", [])
    context = payload.get("context", [])
    if not isinstance(supporting_facts, list) or not supporting_facts:
        raise RuntimeError(f"HotpotQA case `{case_id}` has invalid supporting_facts in {source}")
    if not isinstance(context, list) or not context:
        raise RuntimeError(f"HotpotQA case `{case_id}` has invalid context in {source}")

    facts: list[Fact] = []
    fact_ids_by_key: dict[tuple[str, int], str] = {}
    for paragraph in context:
        if (
            not isinstance(paragraph, list)
            or len(paragraph) != 2
            or not isinstance(paragraph[0], str)
            or not paragraph[0].strip()
            or not isinstance(paragraph[1], list)
            or not all(isinstance(sentence, str) and sentence.strip() for sentence in paragraph[1])
        ):
            raise RuntimeError(f"HotpotQA case `{case_id}` has invalid context entry in {source}")
        title = paragraph[0].strip()
        sentences = paragraph[1]
        for sent_id, sentence in enumerate(sentences):
            fact_id = _hotpot_fact_id(case_id, title, sent_id)
            fact_ids_by_key[(title, sent_id)] = fact_id
            facts.append(
                Fact(
                    fact_id=fact_id,
                    text=f"{title}: {sentence.strip()}",
                    scope=Scope.PROJECT,
                    topic="hotpotqa",
                    encoding_strength=4,
                    memory_type=MemoryType.EXPLICIT_SEMANTIC,
                    verification=Verification.CORROBORATED,
                    source_type=SourceType.GROUND_TRUTH_CODE,
                    source_tool="hotpotqa-manifest",
                    source_session=case_id,
                    consolidation_status=ConsolidationStatus.STABLE,
                )
            )

    supporting_fact_ids: list[str] = []
    for item in supporting_facts:
        if (
            not isinstance(item, list)
            or len(item) != 2
            or not isinstance(item[0], str)
            or not item[0].strip()
            or not isinstance(item[1], int)
            or item[1] < 0
        ):
            raise RuntimeError(f"HotpotQA case `{case_id}` has invalid supporting_facts entry in {source}")
        key = (item[0].strip(), item[1])
        try:
            fact_id = fact_ids_by_key[key]
        except KeyError as exc:
            raise RuntimeError(
                f"HotpotQA case `{case_id}` references missing supporting fact {key!r} in {source}"
            ) from exc
        if fact_id not in supporting_fact_ids:
            supporting_fact_ids.append(fact_id)

    return RetrievalEvalCase(
        case_id=case_id,
        question=question,
        supporting_fact_ids=tuple(supporting_fact_ids),
        facts=tuple(facts),
        answer=answer,
    )


def _hotpot_fact_id(case_id: str, title: str, sent_id: int) -> str:
    digest = hashlib.sha1(f"{case_id}\0{title}\0{sent_id}".encode("utf-8")).hexdigest()[:18].upper()
    return f"01HOTPOT{digest}"


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


def _answer_present(answer: str, actual_top_ids: list[str], fact_text_by_id: dict[str, str]) -> bool:
    normalized = answer.strip().lower()
    return any(normalized in fact_text_by_id.get(fact_id, "").lower() for fact_id in actual_top_ids)


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
