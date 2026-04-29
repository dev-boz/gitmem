from __future__ import annotations

import copy
from contextlib import contextmanager
import csv
import hashlib
import json
import math
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
from umx.scope import config_path, init_local_umx, init_project_memory, project_memory_dir
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
class BeirDocument:
    doc_id: str
    title: str
    text: str


@dataclass(slots=True, frozen=True)
class BeirQuery:
    query_id: str
    text: str
    qrels: dict[str, float]


@dataclass(slots=True, frozen=True)
class BeirEvalDataset:
    dataset_name: str
    split: str
    input_path: Path
    corpus_path: Path
    queries_path: Path
    qrels_path: Path
    corpus: dict[str, BeirDocument]
    queries: tuple[BeirQuery, ...]


@dataclass(slots=True, frozen=True)
class _BeirManifest:
    dataset_name: str
    split: str
    input_path: Path
    corpus_path: Path
    queries_path: Path
    qrels_path: Path
    query_ids: tuple[str, ...] | None


def load_beir_eval_dataset(cases_path: Path) -> BeirEvalDataset:
    manifest = _load_manifest(cases_path)
    corpus = _load_corpus(manifest.corpus_path)
    queries_by_id, query_order = _load_queries(manifest.queries_path)
    qrels_by_query = _load_qrels(manifest.qrels_path)
    selected_queries = _select_queries(
        query_ids=manifest.query_ids,
        queries_by_id=queries_by_id,
        query_order=query_order,
        qrels_by_query=qrels_by_query,
        source=manifest.input_path,
    )
    return BeirEvalDataset(
        dataset_name=manifest.dataset_name,
        split=manifest.split,
        input_path=manifest.input_path,
        corpus_path=manifest.corpus_path,
        queries_path=manifest.queries_path,
        qrels_path=manifest.qrels_path,
        corpus=corpus,
        queries=tuple(
            BeirQuery(
                query_id=query_id,
                text=queries_by_id[query_id],
                qrels=dict(qrels_by_query[query_id]),
            )
            for query_id in selected_queries
        ),
    )


def run_beir_eval(
    cases_path: Path,
    config: UMXConfig,
    *,
    query_id: str | None = None,
    min_ndcg_at_10: float = 0.0,
    top_k: int = 10,
) -> dict[str, Any]:
    dataset = load_beir_eval_dataset(cases_path)
    queries = list(dataset.queries)
    if query_id is not None:
        queries = [query for query in queries if query.query_id == query_id]
    if not queries:
        raise RuntimeError("no BEIR queries matched the requested selection")
    if not 0 <= min_ndcg_at_10 <= 1:
        raise RuntimeError("BEIR eval min_ndcg_at_10 must be between 0 and 1")
    if top_k <= 0:
        raise RuntimeError("BEIR eval top_k must be greater than 0")

    capture_only = min_ndcg_at_10 == 0.0
    results: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    total_ndcg_at_10 = 0.0
    total_recall_at_10 = 0.0
    completed = 0

    with _temporary_beir_repo(dataset=dataset, config=config) as repo_state:
        for query in queries:
            try:
                result = _run_query(
                    query,
                    repo_dir=repo_state["repo_dir"],
                    config=repo_state["config"],
                    corpus=dataset.corpus,
                    doc_id_by_fact_id=repo_state["doc_id_by_fact_id"],
                    top_k=top_k,
                )
                completed += 1
            except Exception as exc:
                result = {
                    "query_id": query.query_id,
                    "query": query.text,
                    "relevant_doc_ids": sorted(_positive_qrel_doc_ids(query.qrels)),
                    "retrieved_doc_ids": [],
                    "top_docs": [],
                    "ndcg_at_10": 0.0,
                    "recall_at_10": 0.0,
                    "error": str(exc),
                }
                failures.append(
                    {
                        "query_id": query.query_id,
                        "query": query.text,
                        "error": str(exc),
                    }
                )
            total_ndcg_at_10 += float(result["ndcg_at_10"])
            total_recall_at_10 += float(result["recall_at_10"])
            results.append(result)

    total = len(queries)
    ndcg_at_10 = total_ndcg_at_10 / total if total else 0.0
    recall_at_10 = total_recall_at_10 / total if total else 0.0
    threshold_passed = ndcg_at_10 >= min_ndcg_at_10
    gate_passed = (not capture_only) and threshold_passed and not failures
    status = "ok" if not failures and (capture_only or threshold_passed) else "error"
    return {
        "suite": "beir",
        "benchmark": {
            "name": "BEIR",
            "url": "https://github.com/beir-cellar/beir",
        },
        "dataset_name": dataset.dataset_name,
        "dataset_split": dataset.split,
        "input_path": str(dataset.input_path),
        "corpus_path": str(dataset.corpus_path),
        "queries_path": str(dataset.queries_path),
        "qrels_path": str(dataset.qrels_path),
        "query_filter": query_id,
        "status": status,
        "capture_only": capture_only,
        "gate_passed": gate_passed,
        "total": total,
        "total_queries": total,
        "completed_queries": completed,
        "failed_queries": len(failures),
        "corpus_size": len(dataset.corpus),
        "top_k": top_k,
        "ndcg_at_10": ndcg_at_10,
        "recall_at_10": recall_at_10,
        "metrics": {
            "ndcg@10": ndcg_at_10,
            "recall@10": recall_at_10,
        },
        "min_ndcg_at_10": min_ndcg_at_10,
        "failures": failures,
        "results": results,
    }


def _load_manifest(cases_path: Path) -> _BeirManifest:
    resolved = cases_path.resolve()
    if resolved.is_dir():
        split = "test"
        return _BeirManifest(
            dataset_name=resolved.name,
            split=split,
            input_path=resolved,
            corpus_path=resolved / "corpus.jsonl",
            queries_path=resolved / "queries.jsonl",
            qrels_path=resolved / "qrels" / f"{split}.tsv",
            query_ids=None,
        )

    payload = _load_json_object(resolved)
    format_name = payload.get("format")
    if format_name != "beir-manifest":
        raise RuntimeError(
            "BEIR eval input must be a dataset directory or a `beir-manifest` JSON object"
        )
    split = _optional_string(payload.get("split"), field="split", source=resolved) or "test"
    dataset_name = _optional_string(payload.get("dataset"), field="dataset", source=resolved)
    dataset_dir = payload.get("dataset_dir")
    dataset_dir_path = _resolve_optional_path(
        dataset_dir,
        source=resolved,
        field="dataset_dir",
    )
    if dataset_name is None and dataset_dir_path is not None:
        dataset_name = dataset_dir_path.name
    if dataset_name is None:
        dataset_name = "beir"
    corpus_path = _resolve_data_path(
        payload.get("corpus_path"),
        source=resolved,
        field="corpus_path",
        default=(dataset_dir_path / "corpus.jsonl") if dataset_dir_path is not None else None,
    )
    queries_path = _resolve_data_path(
        payload.get("queries_path"),
        source=resolved,
        field="queries_path",
        default=(dataset_dir_path / "queries.jsonl") if dataset_dir_path is not None else None,
    )
    qrels_path = _resolve_data_path(
        payload.get("qrels_path"),
        source=resolved,
        field="qrels_path",
        default=(dataset_dir_path / "qrels" / f"{split}.tsv") if dataset_dir_path is not None else None,
    )
    query_ids = _optional_string_list(
        payload.get("query_ids"),
        field="query_ids",
        source=resolved,
        context="BEIR manifest",
    )
    if query_ids is not None:
        _ensure_unique_ids(
            query_ids,
            field="query_ids",
            source=resolved,
            context="BEIR manifest",
        )
    return _BeirManifest(
        dataset_name=dataset_name,
        split=split,
        input_path=resolved,
        corpus_path=corpus_path,
        queries_path=queries_path,
        qrels_path=qrels_path,
        query_ids=tuple(query_ids) if query_ids is not None else None,
    )


def _load_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text())
    except FileNotFoundError as exc:
        raise RuntimeError(f"BEIR eval input not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"BEIR eval input is not valid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"BEIR eval manifest must be a JSON object: {path}")
    return payload


def _resolve_optional_path(value: Any, *, source: Path, field: str) -> Path | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"BEIR manifest has invalid `{field}` in {source}")
    path = Path(value.strip())
    if not path.is_absolute():
        path = (source.parent / path).resolve()
    return path


def _resolve_data_path(
    value: Any,
    *,
    source: Path,
    field: str,
    default: Path | None,
) -> Path:
    if value is None:
        if default is None:
            raise RuntimeError(f"BEIR manifest is missing `{field}` in {source}")
        return default
    path = _resolve_optional_path(value, source=source, field=field)
    assert path is not None
    return path


def _load_corpus(path: Path) -> dict[str, BeirDocument]:
    corpus: dict[str, BeirDocument] = {}
    try:
        with path.open(encoding="utf-8") as handle:
            for line_no, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise RuntimeError(
                        f"BEIR corpus is not valid JSONL at {path}:{line_no}"
                    ) from exc
                if not isinstance(payload, dict):
                    raise RuntimeError(f"BEIR corpus row must be an object at {path}:{line_no}")
                doc_id = _required_string(payload.get("_id"), field="_id", source=path, line_no=line_no)
                title = _string_or_empty(payload.get("title"), field="title", source=path, line_no=line_no)
                text = _string_or_empty(payload.get("text"), field="text", source=path, line_no=line_no)
                if not title and not text:
                    raise RuntimeError(
                        f"BEIR corpus row `{doc_id}` must contain non-empty `title` or `text` in {path}:{line_no}"
                    )
                if doc_id in corpus:
                    raise RuntimeError(f"BEIR corpus has duplicate _id `{doc_id}` in {path}")
                corpus[doc_id] = BeirDocument(doc_id=doc_id, title=title, text=text)
    except FileNotFoundError as exc:
        raise RuntimeError(f"BEIR corpus not found: {path}") from exc
    if not corpus:
        raise RuntimeError(f"BEIR corpus is empty: {path}")
    return corpus


def _load_queries(path: Path) -> tuple[dict[str, str], list[str]]:
    queries_by_id: dict[str, str] = {}
    query_order: list[str] = []
    try:
        with path.open(encoding="utf-8") as handle:
            for line_no, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise RuntimeError(
                        f"BEIR queries are not valid JSONL at {path}:{line_no}"
                    ) from exc
                if not isinstance(payload, dict):
                    raise RuntimeError(f"BEIR query row must be an object at {path}:{line_no}")
                query_id = _required_string(payload.get("_id"), field="_id", source=path, line_no=line_no)
                text = _required_string(payload.get("text"), field="text", source=path, line_no=line_no)
                if query_id in queries_by_id:
                    raise RuntimeError(f"BEIR queries have duplicate _id `{query_id}` in {path}")
                queries_by_id[query_id] = text
                query_order.append(query_id)
    except FileNotFoundError as exc:
        raise RuntimeError(f"BEIR queries not found: {path}") from exc
    if not queries_by_id:
        raise RuntimeError(f"BEIR queries are empty: {path}")
    return queries_by_id, query_order


def _load_qrels(path: Path) -> dict[str, dict[str, float]]:
    qrels: dict[str, dict[str, float]] = {}
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.reader(handle, delimiter="\t")
            for line_no, row in enumerate(reader, start=1):
                if not row:
                    continue
                if line_no == 1 and row[0].strip().lower() in {"query-id", "query_id"}:
                    continue
                if len(row) != 3:
                    raise RuntimeError(f"BEIR qrels row must have 3 columns at {path}:{line_no}")
                query_id = row[0].strip()
                doc_id = row[1].strip()
                if not query_id or not doc_id:
                    raise RuntimeError(f"BEIR qrels row has blank ids at {path}:{line_no}")
                try:
                    score = float(row[2])
                except ValueError as exc:
                    raise RuntimeError(
                        f"BEIR qrels row has invalid score `{row[2]}` at {path}:{line_no}"
                    ) from exc
                qrels.setdefault(query_id, {})[doc_id] = score
    except FileNotFoundError as exc:
        raise RuntimeError(f"BEIR qrels not found: {path}") from exc
    if not qrels:
        raise RuntimeError(f"BEIR qrels are empty: {path}")
    return qrels


def _select_queries(
    *,
    query_ids: tuple[str, ...] | None,
    queries_by_id: dict[str, str],
    query_order: list[str],
    qrels_by_query: dict[str, dict[str, float]],
    source: Path,
) -> list[str]:
    if query_ids is None:
        selected = [query_id for query_id in query_order if query_id in qrels_by_query]
    else:
        selected = list(query_ids)
    if not selected:
        raise RuntimeError(f"BEIR eval selected no queries in {source}")
    for query_id in selected:
        if query_id not in queries_by_id:
            raise RuntimeError(f"BEIR manifest references unknown query_id `{query_id}` in {source}")
        if query_id not in qrels_by_query:
            raise RuntimeError(f"BEIR eval query `{query_id}` is missing qrels in {source}")
        if not _positive_qrel_doc_ids(qrels_by_query[query_id]):
            raise RuntimeError(f"BEIR eval query `{query_id}` has no positive qrels in {source}")
    return selected


@contextmanager
def _temporary_beir_repo(
    *,
    dataset: BeirEvalDataset,
    config: UMXConfig,
) -> Iterator[dict[str, Any]]:
    with TemporaryDirectory(prefix="gitmem-beir-eval-") as temp_dir:
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

            doc_id_by_fact_id: dict[str, str] = {}
            for document in dataset.corpus.values():
                fact = _fact_from_document(document, dataset_name=dataset.dataset_name)
                doc_id_by_fact_id[fact.fact_id] = document.doc_id
                add_fact(project_repo, fact, auto_commit=False)
            rebuild_index(project_repo, config=eval_config)
            yield {
                "repo_dir": project_repo,
                "config": eval_config,
                "doc_id_by_fact_id": doc_id_by_fact_id,
            }


def _fact_from_document(document: BeirDocument, *, dataset_name: str) -> Fact:
    digest = hashlib.sha1(f"{dataset_name}\0{document.doc_id}".encode("utf-8")).hexdigest()[:18].upper()
    topic = f"beir-{dataset_name}-{digest[:2].lower()}"
    return Fact(
        fact_id=f"01BEIR{digest}",
        text=_document_text(document),
        scope=Scope.PROJECT,
        topic=topic,
        encoding_strength=4,
        memory_type=MemoryType.EXPLICIT_SEMANTIC,
        verification=Verification.CORROBORATED,
        source_type=SourceType.EXTERNAL_DOC,
        source_tool="beir-eval",
        source_session=document.doc_id,
        consolidation_status=ConsolidationStatus.STABLE,
    )


def _document_text(document: BeirDocument) -> str:
    title = document.title.strip()
    text = document.text.strip()
    if title and text:
        return f"{title}: {text}"
    return title or text


def _run_query(
    query: BeirQuery,
    *,
    repo_dir: Path,
    config: UMXConfig,
    corpus: dict[str, BeirDocument],
    doc_id_by_fact_id: dict[str, str],
    top_k: int,
) -> dict[str, Any]:
    retrieval_limit = max(top_k, 10)
    query_terms = _search_terms(query.text)
    candidates: dict[str, tuple[Fact, int, int, int]] = {}
    for query_order, variant in enumerate(_candidate_queries(query.text, terms=query_terms)):
        for rank, fact in enumerate(query_index(repo_dir, variant, limit=retrieval_limit, config=config)):
            if fact.fact_id not in doc_id_by_fact_id:
                continue
            score = _fact_overlap_score(fact.text, query_terms)
            candidate = (fact, score, query_order, rank)
            existing = candidates.get(fact.fact_id)
            if existing is None or _candidate_sort_key(candidate) < _candidate_sort_key(existing):
                candidates[fact.fact_id] = candidate
    ordered = sorted(candidates.values(), key=_candidate_sort_key)
    retrieved_doc_ids = [doc_id_by_fact_id[fact.fact_id] for fact, _, _, _ in ordered[:retrieval_limit]]
    relevant_doc_ids = sorted(_positive_qrel_doc_ids(query.qrels))
    top_docs = []
    for rank, doc_id in enumerate(retrieved_doc_ids[:top_k], start=1):
        document = corpus.get(doc_id)
        relevance = float(query.qrels.get(doc_id, 0.0))
        top_docs.append(
            {
                "rank": rank,
                "doc_id": doc_id,
                "title": document.title if document is not None else "",
                "relevant": relevance > 0,
                "relevance": relevance,
            }
        )
    return {
        "query_id": query.query_id,
        "query": query.text,
        "relevant_doc_ids": relevant_doc_ids,
        "retrieved_doc_ids": retrieved_doc_ids[:top_k],
        "top_docs": top_docs,
        "ndcg_at_10": _ndcg_at_k(retrieved_doc_ids, qrels=query.qrels, k=10),
        "recall_at_10": _recall_at_k(retrieved_doc_ids, qrels=query.qrels, k=10),
    }


def _positive_qrel_doc_ids(qrels: dict[str, float]) -> set[str]:
    return {doc_id for doc_id, score in qrels.items() if score > 0}


def _ndcg_at_k(retrieved_doc_ids: list[str], *, qrels: dict[str, float], k: int) -> float:
    dcg = 0.0
    for rank, doc_id in enumerate(retrieved_doc_ids[:k], start=1):
        relevance = float(qrels.get(doc_id, 0.0))
        if relevance <= 0:
            continue
        dcg += (pow(2.0, relevance) - 1.0) / math.log2(rank + 1)
    ideal_rels = sorted((score for score in qrels.values() if score > 0), reverse=True)[:k]
    idcg = sum((pow(2.0, relevance) - 1.0) / math.log2(rank + 1) for rank, relevance in enumerate(ideal_rels, start=1))
    if idcg == 0:
        return 0.0
    return dcg / idcg


def _recall_at_k(retrieved_doc_ids: list[str], *, qrels: dict[str, float], k: int) -> float:
    relevant_doc_ids = _positive_qrel_doc_ids(qrels)
    if not relevant_doc_ids:
        return 0.0
    hits = sum(1 for doc_id in retrieved_doc_ids[:k] if doc_id in relevant_doc_ids)
    return hits / len(relevant_doc_ids)


def _search_terms(query: str) -> list[str]:
    terms: list[str] = []
    for match in TERM_RE.finditer(query):
        term = match.group(0).lower()
        if len(term) <= 2 or term in REFERENCE_STOPWORDS or term in QUESTION_STOPWORDS:
            continue
        if term not in terms:
            terms.append(term)
    return terms


def _candidate_queries(query: str, *, terms: list[str]) -> list[str]:
    variants = [query.strip()]
    if terms:
        strict = " ".join(terms)
        broad = " OR ".join(f'"{term.replace(chr(34), chr(34) * 2)}"' for term in terms)
        variants.extend([strict, broad])
    ordered: list[str] = []
    for variant in variants:
        if variant and variant not in ordered:
            ordered.append(variant)
    return ordered


def _fact_overlap_score(text: str, terms: list[str]) -> int:
    if not terms:
        return 0
    fact_terms = {match.group(0).lower() for match in TERM_RE.finditer(text)}
    return sum(1 for term in terms if term in fact_terms)


def _candidate_sort_key(candidate: tuple[Fact, int, int, int]) -> tuple[int, int, int, str]:
    fact, score, query_order, rank = candidate
    return (-score, query_order, rank, fact.fact_id)


def _required_string(value: Any, *, field: str, source: Path, line_no: int | None = None) -> str:
    if not isinstance(value, str) or not value.strip():
        location = f"{source}:{line_no}" if line_no is not None else str(source)
        raise RuntimeError(f"BEIR data is missing `{field}` in {location}")
    return value.strip()


def _string_or_empty(value: Any, *, field: str, source: Path, line_no: int) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise RuntimeError(f"BEIR data has invalid `{field}` in {source}:{line_no}")
    return value.strip()


def _optional_string(value: Any, *, field: str, source: Path) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"BEIR manifest has invalid `{field}` in {source}")
    return value.strip()


def _optional_string_list(
    value: Any,
    *,
    field: str,
    source: Path,
    context: str,
) -> list[str] | None:
    if value is None:
        return None
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
