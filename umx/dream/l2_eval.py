from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from umx.config import UMXConfig
from umx.conventions import ConventionSet
from umx.dream.l2_review import (
    L2_REVIEW_PROMPT_ID,
    L2_REVIEW_PROMPT_VERSION,
    anthropic_l2_reviewer,
)
from umx.dream.pr_render import build_fact_delta_from_facts, render_governance_pr_body
from umx.governance import PRProposal
from umx.models import (
    ConsolidationStatus,
    Fact,
    MemoryType,
    Scope,
    SourceType,
    Verification,
)


REQUIRED_EVAL_BUCKETS = frozenset({
    "clean_extraction",
    "conflicting_fact",
    "overbroad_scope",
    "redundant_fact",
    "taxonomy_miss",
    "suspected_hallucination",
})


@dataclass(slots=True, frozen=True)
class L2EvalCase:
    case_id: str
    bucket: str
    description: str
    expected_action: str
    labels: tuple[str, ...]
    files_changed: tuple[str, ...]
    conventions: ConventionSet
    existing_facts: tuple[Fact, ...]
    new_facts: tuple[Fact, ...]

    def proposal(self) -> PRProposal:
        files_changed = list(self.files_changed) or [
            f"facts/topics/{fact.topic}.md" for fact in (self.new_facts or self.existing_facts)
        ]
        body = ""
        if self.new_facts:
            body = render_governance_pr_body(
                heading="L2 Eval Review Case",
                summary_lines=[
                    f"- Case: {self.case_id}",
                    f"- Bucket: {self.bucket}",
                    f"- Description: {self.description}",
                ],
                fact_delta=build_fact_delta_from_facts(list(self.new_facts), Path(".")),
            )
        return PRProposal(
            title=f"[eval/l2] {self.case_id}",
            body=body,
            branch=f"dream/l1/{self.case_id}",
            labels=list(self.labels),
            files_changed=files_changed,
        )


def load_l2_eval_cases(cases_path: Path) -> list[L2EvalCase]:
    resolved = _resolve_cases_path(cases_path)
    try:
        payload = json.loads(resolved.read_text())
    except FileNotFoundError as exc:
        raise RuntimeError(f"L2 eval cases not found: {resolved}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"L2 eval cases are not valid JSON: {resolved}") from exc
    if not isinstance(payload, list):
        raise RuntimeError("L2 eval cases file must contain a JSON array")
    return [_case_from_payload(item, source=resolved) for item in payload]


def run_l2_review_eval(
    cases_path: Path,
    config: UMXConfig,
    *,
    case_id: str | None = None,
    min_pass_rate: float = 0.85,
    reviewer: Callable[[PRProposal, ConventionSet, list[Fact], list[Fact] | None, UMXConfig], dict[str, object]] = anthropic_l2_reviewer,
) -> dict[str, Any]:
    cases = load_l2_eval_cases(cases_path)
    if case_id is not None:
        cases = [case for case in cases if case.case_id == case_id]
    if not cases:
        raise RuntimeError("no L2 eval cases matched the requested selection")

    results: list[dict[str, Any]] = []
    bucket_totals: dict[str, dict[str, int]] = {}
    usage_totals = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    models: set[str] = set()
    prompt_ids: set[str] = set()
    prompt_versions: set[str] = set()
    missing_model_cases: list[str] = []
    missing_prompt_id_cases: list[str] = []
    missing_prompt_version_cases: list[str] = []
    passed = 0

    for case in cases:
        bucket_totals.setdefault(case.bucket, {"total": 0, "passed": 0})
        bucket_totals[case.bucket]["total"] += 1
        try:
            review = reviewer(
                case.proposal(),
                case.conventions,
                list(case.existing_facts),
                list(case.new_facts) or None,
                config,
            )
            actual_action = str(review["action"])
            ok = actual_action == case.expected_action
            if ok:
                passed += 1
                bucket_totals[case.bucket]["passed"] += 1
            usage = review.get("usage")
            if isinstance(usage, dict):
                for key in usage_totals:
                    value = usage.get(key)
                    if value is not None:
                        usage_totals[key] += int(value)
            model = review.get("model")
            if isinstance(model, str) and model.strip():
                models.add(model)
            else:
                missing_model_cases.append(case.case_id)
            prompt_id_value = review.get("prompt_id")
            if isinstance(prompt_id_value, str) and prompt_id_value.strip():
                prompt_ids.add(prompt_id_value)
            else:
                missing_prompt_id_cases.append(case.case_id)
            prompt_version_value = review.get("prompt_version")
            if isinstance(prompt_version_value, str) and prompt_version_value.strip():
                prompt_versions.add(prompt_version_value)
            else:
                missing_prompt_version_cases.append(case.case_id)
            results.append(
                {
                    "case": case.case_id,
                    "bucket": case.bucket,
                    "expected_action": case.expected_action,
                    "actual_action": actual_action,
                    "passed": ok,
                    "reason": str(review.get("reason", "")),
                }
            )
        except Exception as exc:
            results.append(
                {
                    "case": case.case_id,
                    "bucket": case.bucket,
                    "expected_action": case.expected_action,
                    "actual_action": None,
                    "passed": False,
                    "error": str(exc),
                }
            )

    total = len(cases)
    pass_rate = passed / total if total else 0.0
    missing_buckets = sorted(REQUIRED_EVAL_BUCKETS - set(bucket_totals)) if case_id is None else []
    meets_case_count = total >= 20 if case_id is None else True
    failures = [result for result in results if not result["passed"]]
    metadata_errors = _metadata_errors(
        models=models,
        prompt_ids=prompt_ids,
        prompt_versions=prompt_versions,
        missing_model_cases=missing_model_cases,
        missing_prompt_id_cases=missing_prompt_id_cases,
        missing_prompt_version_cases=missing_prompt_version_cases,
    )
    status = (
        "ok"
        if pass_rate >= min_pass_rate and not missing_buckets and meets_case_count and not metadata_errors
        else "error"
    )

    return {
        "status": status,
        "prompt_id": _single_or_none(prompt_ids),
        "prompt_version": _single_or_none(prompt_versions),
        "model": _single_or_none(models),
        "total": total,
        "passed": passed,
        "pass_rate": pass_rate,
        "min_pass_rate": min_pass_rate,
        "meets_case_count": meets_case_count,
        "required_case_count": 20,
        "missing_buckets": missing_buckets,
        "metadata_errors": metadata_errors,
        "bucket_summary": bucket_totals,
        "usage_totals": usage_totals,
        "failures": failures,
        "results": results,
    }


def _resolve_cases_path(cases_path: Path) -> Path:
    return cases_path / "cases.json" if cases_path.is_dir() else cases_path


def _case_from_payload(payload: Any, *, source: Path) -> L2EvalCase:
    if not isinstance(payload, dict):
        raise RuntimeError(f"invalid L2 eval case entry in {source}")
    case_id = _required_string(payload.get("id"), field="id", source=source)
    bucket = _required_string(payload.get("bucket"), field="bucket", source=source)
    description = _required_string(payload.get("description"), field="description", source=source)
    expected_action = _required_string(payload.get("expected_action"), field="expected_action", source=source)
    labels_payload = payload.get("labels", [])
    if not isinstance(labels_payload, list) or not all(isinstance(label, str) for label in labels_payload):
        raise RuntimeError(f"L2 eval case `{case_id}` has invalid labels in {source}")
    files_payload = payload.get("files_changed", [])
    if not isinstance(files_payload, list) or not all(isinstance(path, str) for path in files_payload):
        raise RuntimeError(f"L2 eval case `{case_id}` has invalid files_changed in {source}")
    conventions_payload = payload.get("conventions", {})
    if conventions_payload is None:
        conventions_payload = {}
    if not isinstance(conventions_payload, dict):
        raise RuntimeError(f"L2 eval case `{case_id}` has invalid conventions in {source}")
    return L2EvalCase(
        case_id=case_id,
        bucket=bucket,
        description=description,
        expected_action=expected_action,
        labels=tuple(labels_payload),
        files_changed=tuple(files_payload),
        conventions=_conventions_from_payload(conventions_payload),
        existing_facts=tuple(_fact_from_eval_payload(item) for item in payload.get("existing_facts", [])),
        new_facts=tuple(_fact_from_eval_payload(item) for item in payload.get("new_facts", [])),
    )


def _required_string(value: Any, *, field: str, source: Path) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"L2 eval case is missing `{field}` in {source}")
    return value.strip()


def _conventions_from_payload(payload: dict[str, Any]) -> ConventionSet:
    topics = payload.get("topics", [])
    phrasing_rules = payload.get("phrasing_rules", [])
    project_conventions = payload.get("project_conventions", [])
    topic_descriptions = payload.get("topic_descriptions", {})
    entity_vocabulary = payload.get("entity_vocabulary", {})
    return ConventionSet(
        topics={str(topic) for topic in topics},
        topic_descriptions={str(key): str(value) for key, value in dict(topic_descriptions).items()},
        phrasing_rules=[str(rule) for rule in phrasing_rules],
        entity_vocabulary={str(key): str(value) for key, value in dict(entity_vocabulary).items()},
        project_conventions=[str(rule) for rule in project_conventions],
    )


def _fact_from_eval_payload(payload: Any) -> Fact:
    if not isinstance(payload, dict):
        raise RuntimeError("L2 eval fact payload must be an object")
    return Fact(
        fact_id=str(payload["fact_id"]),
        text=str(payload["text"]),
        scope=Scope(str(payload.get("scope", Scope.PROJECT.value))),
        topic=str(payload["topic"]),
        encoding_strength=int(payload.get("encoding_strength", 3)),
        memory_type=MemoryType(str(payload.get("memory_type", MemoryType.EXPLICIT_SEMANTIC.value))),
        verification=Verification(str(payload.get("verification", Verification.SELF_REPORTED.value))),
        source_type=SourceType(str(payload.get("source_type", SourceType.LLM_INFERENCE.value))),
        confidence=float(payload.get("confidence", 0.9)),
        source_tool=str(payload.get("source_tool", "l2-eval")),
        source_session=str(payload.get("source_session", "l2-eval")),
        conflicts_with=[str(item) for item in payload.get("conflicts_with", [])],
        supersedes=str(payload["supersedes"]) if payload.get("supersedes") is not None else None,
        superseded_by=str(payload["superseded_by"]) if payload.get("superseded_by") is not None else None,
        consolidation_status=ConsolidationStatus(
            str(payload.get("consolidation_status", ConsolidationStatus.STABLE.value))
        ),
    )


def _single_or_none(values: set[str]) -> str | None:
    return next(iter(values)) if len(values) == 1 else None


def _metadata_errors(
    *,
    models: set[str],
    prompt_ids: set[str],
    prompt_versions: set[str],
    missing_model_cases: list[str],
    missing_prompt_id_cases: list[str],
    missing_prompt_version_cases: list[str],
) -> list[str]:
    errors: list[str] = []
    if missing_model_cases:
        errors.append(f"cases missing model metadata: {', '.join(sorted(missing_model_cases))}")
    if missing_prompt_id_cases:
        errors.append(f"cases missing prompt_id metadata: {', '.join(sorted(missing_prompt_id_cases))}")
    if missing_prompt_version_cases:
        errors.append(
            f"cases missing prompt_version metadata: {', '.join(sorted(missing_prompt_version_cases))}"
        )
    if len(models) > 1:
        errors.append(f"cases returned multiple models: {', '.join(sorted(models))}")
    if len(prompt_ids) > 1:
        errors.append(f"cases returned multiple prompt ids: {', '.join(sorted(prompt_ids))}")
    if len(prompt_versions) > 1:
        errors.append(f"cases returned multiple prompt versions: {', '.join(sorted(prompt_versions))}")
    return errors
