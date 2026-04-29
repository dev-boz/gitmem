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
from umx.inject import build_injection_block
from umx.memory import add_fact
from umx.models import (
    ConsolidationStatus,
    Fact,
    MemoryType,
    Scope,
    SourceType,
    Verification,
)
from umx.scope import (
    config_path,
    encode_scope_path,
    init_local_umx,
    init_project_memory,
    project_memory_dir,
)
from umx.search import session_replay


@dataclass(slots=True, frozen=True)
class InjectEvalCase:
    case_id: str
    prompt: str
    max_tokens: int
    expected_top_ids: tuple[str, ...]
    facts: tuple[Fact, ...]
    file_paths: tuple[str, ...] = ()


def load_inject_eval_cases(cases_path: Path) -> list[InjectEvalCase]:
    resolved = _resolve_cases_path(cases_path)
    try:
        payload = json.loads(resolved.read_text())
    except FileNotFoundError as exc:
        raise RuntimeError(f"inject eval cases not found: {resolved}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"inject eval cases are not valid JSON: {resolved}") from exc
    if not isinstance(payload, list):
        raise RuntimeError("inject eval cases file must contain a JSON array")
    return [_case_from_payload(item, source=resolved) for item in payload]


def run_inject_eval(
    cases_path: Path,
    config: UMXConfig,
    *,
    case_id: str | None = None,
    min_pass_rate: float = 1.0,
    disclosure_slack_pct: float | None = None,
) -> dict[str, Any]:
    resolved_cases_path = _resolve_cases_path(cases_path)
    cases = load_inject_eval_cases(resolved_cases_path)
    if case_id is not None:
        cases = [case for case in cases if case.case_id == case_id]
    if not cases:
        raise RuntimeError("no inject eval cases matched the requested selection")
    if not 0 <= min_pass_rate <= 1:
        raise RuntimeError("inject eval min_pass_rate must be between 0 and 1")

    effective_slack = (
        float(disclosure_slack_pct)
        if disclosure_slack_pct is not None
        else float(config.inject.disclosure_slack_pct)
    )
    if not 0 <= effective_slack <= 1:
        raise RuntimeError("inject eval disclosure_slack_pct must be between 0 and 1")

    results: list[dict[str, Any]] = []
    passed = 0
    for case in cases:
        try:
            actual_top_ids = _run_case(case, config=config, disclosure_slack_pct=effective_slack)
            ok = actual_top_ids == list(case.expected_top_ids)
            if ok:
                passed += 1
            results.append(
                {
                    "case": case.case_id,
                    "expected_top_ids": list(case.expected_top_ids),
                    "actual_top_ids": actual_top_ids,
                    "passed": ok,
                }
            )
        except Exception as exc:
            results.append(
                {
                    "case": case.case_id,
                    "expected_top_ids": list(case.expected_top_ids),
                    "actual_top_ids": None,
                    "passed": False,
                    "error": str(exc),
                }
            )

    total = len(cases)
    pass_rate = passed / total if total else 0.0
    failures = [result for result in results if not result["passed"]]
    status = "ok" if pass_rate >= min_pass_rate else "error"
    return {
        "suite": "inject",
        "cases_path": str(resolved_cases_path),
        "case_filter": case_id,
        "status": status,
        "total": total,
        "passed": passed,
        "pass_rate": pass_rate,
        "min_pass_rate": min_pass_rate,
        "disclosure_slack_pct": effective_slack,
        "failures": failures,
        "results": results,
    }


def _resolve_cases_path(cases_path: Path) -> Path:
    return cases_path / "cases.json" if cases_path.is_dir() else cases_path


def _case_from_payload(payload: Any, *, source: Path) -> InjectEvalCase:
    if not isinstance(payload, dict):
        raise RuntimeError(f"invalid inject eval case entry in {source}")
    case_id = _required_string(payload.get("id"), field="id", source=source)
    prompt = _required_string(payload.get("prompt"), field="prompt", source=source)
    max_tokens = payload.get("max_tokens")
    if not isinstance(max_tokens, int) or max_tokens <= 0:
        raise RuntimeError(f"inject eval case `{case_id}` has invalid max_tokens in {source}")
    expected_top_ids = payload.get("expected_top_ids", [])
    if not isinstance(expected_top_ids, list) or not all(
        isinstance(fact_id, str) and fact_id.strip() for fact_id in expected_top_ids
    ):
        raise RuntimeError(f"inject eval case `{case_id}` has invalid expected_top_ids in {source}")
    file_paths = payload.get("file_paths", [])
    if not isinstance(file_paths, list) or not all(
        isinstance(path, str) and path.strip() for path in file_paths
    ):
        raise RuntimeError(f"inject eval case `{case_id}` has invalid file_paths in {source}")
    facts_payload = payload.get("facts", [])
    if not isinstance(facts_payload, list) or not facts_payload:
        raise RuntimeError(f"inject eval case `{case_id}` has invalid facts in {source}")
    return InjectEvalCase(
        case_id=case_id,
        prompt=prompt,
        max_tokens=max_tokens,
        expected_top_ids=tuple(expected_top_ids),
        facts=tuple(_fact_from_eval_payload(item) for item in facts_payload),
        file_paths=tuple(file_paths),
    )


def _required_string(value: Any, *, field: str, source: Path) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"inject eval case is missing `{field}` in {source}")
    return value.strip()


def _fact_from_eval_payload(payload: Any) -> Fact:
    if not isinstance(payload, dict):
        raise RuntimeError("inject eval fact payload must be an object")
    scope = Scope(str(payload.get("scope", Scope.PROJECT.value)))
    topic = str(payload.get("topic", "general"))
    if scope == Scope.FILE:
        topic = encode_scope_path(topic)
    return Fact(
        fact_id=str(payload["fact_id"]),
        text=str(payload["text"]),
        scope=scope,
        topic=topic,
        encoding_strength=int(payload.get("encoding_strength", 4)),
        memory_type=MemoryType(str(payload.get("memory_type", MemoryType.EXPLICIT_SEMANTIC.value))),
        verification=Verification(str(payload.get("verification", Verification.CORROBORATED.value))),
        source_type=SourceType(str(payload.get("source_type", SourceType.GROUND_TRUTH_CODE.value))),
        source_tool=str(payload.get("source_tool", "inject-eval")),
        source_session=str(payload.get("source_session", "inject-eval")),
        consolidation_status=ConsolidationStatus(
            str(payload.get("consolidation_status", ConsolidationStatus.STABLE.value))
        ),
    )


def _run_case(case: InjectEvalCase, *, config: UMXConfig, disclosure_slack_pct: float) -> list[str]:
    with TemporaryDirectory(prefix="gitmem-inject-eval-") as temp_dir:
        temp_path = Path(temp_dir)
        with _temporary_umx_home(temp_path / "umxhome"):
            init_local_umx()
            eval_config = copy.deepcopy(config)
            eval_config.inject.disclosure_slack_pct = disclosure_slack_pct
            save_config(config_path(), eval_config)

            project_dir = temp_path / "project"
            project_dir.mkdir()
            (project_dir / ".git").mkdir()
            init_project_memory(project_dir)
            project_repo = project_memory_dir(project_dir)

            for fact in case.facts:
                add_fact(project_repo, fact, auto_commit=False)

            session_id = f"inject-eval-{case.case_id}"
            build_injection_block(
                project_dir,
                prompt=case.prompt,
                file_paths=list(case.file_paths),
                max_tokens=case.max_tokens,
                session_id=session_id,
            )
            return [
                str(row["fact_id"])
                for row in session_replay(project_repo, session_id)
                if row["event_kind"] == "inject" and row.get("item_kind") == "fact"
            ]


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
