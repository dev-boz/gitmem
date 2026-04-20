from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from tests.secret_literals import materialize_placeholders
from umx.dream.extract import gap_records_to_facts, session_records_to_facts, source_files_to_facts
from umx.sessions import list_sessions, write_session

FIXTURES_ROOT = Path(__file__).parent / "fixtures" / "golden_extraction"


def _load_jsonl(path: Path) -> list[dict]:
    return [
        materialize_placeholders(json.loads(line))
        for line in path.read_text().splitlines()
        if line.strip()
    ]


def _normalize_fact(fact) -> dict:
    payload = {
        "text": fact.text,
        "topic": fact.topic,
        "scope": fact.scope.value,
        "encoding_strength": fact.encoding_strength,
        "memory_type": fact.memory_type.value,
        "verification": fact.verification.value,
        "source_type": fact.source_type.value,
        "source_tool": fact.source_tool,
        "source_session": fact.source_session,
        "consolidation_status": fact.consolidation_status.value,
        "confidence": fact.confidence,
        "provenance": {
            "extracted_by": fact.provenance.extracted_by,
            "sessions": sorted(fact.provenance.sessions),
        },
    }
    if fact.code_anchor is not None:
        payload["code_anchor"] = {"path": fact.code_anchor.path}
    return payload


def _sort_key(payload: dict) -> tuple:
    code_anchor = payload.get("code_anchor", {})
    provenance = payload.get("provenance", {})
    return (
        payload["text"],
        payload["topic"],
        payload["source_tool"],
        payload["source_session"],
        code_anchor.get("path", ""),
        tuple(provenance.get("sessions", [])),
    )


def _run_case(case_dir: Path, project_dir: Path, project_repo: Path) -> list[dict]:
    spec = materialize_placeholders(json.loads((case_dir / "expected.json").read_text()))

    project_fixture = case_dir / "project"
    if project_fixture.exists():
        shutil.copytree(project_fixture, project_dir, dirs_exist_ok=True)

    gaps_path = case_dir / "gaps.jsonl"
    if gaps_path.exists():
        target = project_repo / "meta" / "gaps.jsonl"
        target.write_text(gaps_path.read_text())

    session_path = case_dir / "session.jsonl"
    if session_path.exists():
        payload = _load_jsonl(session_path)
        meta = dict(payload[0]["_meta"])
        events = payload[1:]
        write_session(project_repo, meta=meta, events=events, auto_commit=False)

    runner = spec["runner"]
    if runner == "session_records_to_facts":
        facts = session_records_to_facts(project_repo)
    elif runner == "gap_records_to_facts":
        facts = gap_records_to_facts(project_repo)
    elif runner == "source_files_to_facts":
        facts = source_files_to_facts(project_repo, project_dir, list_sessions(project_repo))
    else:
        raise AssertionError(f"unknown golden runner: {runner}")

    actual = sorted((_normalize_fact(fact) for fact in facts), key=_sort_key)
    expected = sorted(spec["expected_facts"], key=_sort_key)
    assert actual == expected

    all_text = "\n".join(fact["text"] for fact in actual)
    for forbidden in spec.get("forbidden_substrings", []):
        assert forbidden not in all_text

    return actual


@pytest.mark.parametrize(
    "case_dir",
    sorted((path for path in FIXTURES_ROOT.iterdir() if path.is_dir()), key=lambda path: path.name),
    ids=lambda path: path.name,
)
def test_golden_extraction_cases(case_dir: Path, project_dir: Path, project_repo: Path) -> None:
    actual = _run_case(case_dir, project_dir, project_repo)
    assert actual
