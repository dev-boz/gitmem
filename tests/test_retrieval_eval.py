from __future__ import annotations

import json
from pathlib import Path

import pytest

from umx.config import default_config
from umx.retrieval_eval import load_retrieval_eval_cases, run_retrieval_eval


FIXTURES_ROOT = Path(__file__).parent / "eval" / "retrieval"


def test_retrieval_eval_cases_load_from_fixture_dir() -> None:
    cases = load_retrieval_eval_cases(FIXTURES_ROOT)

    assert len(cases) >= 3
    assert all(case.supporting_fact_ids for case in cases)


def test_run_retrieval_eval_passes_fixture_subset() -> None:
    payload = run_retrieval_eval(FIXTURES_ROOT, default_config(), min_pass_rate=1.0, top_k=5)

    assert payload["status"] == "ok"
    assert payload["passed"] == payload["total"]
    assert payload["average_recall"] == 1.0


def test_run_retrieval_eval_computes_gate_failure(tmp_path: Path) -> None:
    cases_path = tmp_path / "cases.json"
    cases_path.write_text(
        json.dumps(
            [
                {
                    "id": "retrieval-pass",
                    "question": "Where was Alfred Kempe, the mathematician who proved the four color theorem, born?",
                    "supporting_fact_ids": [
                        "01TESTRETRIEVALPASS00001",
                        "01TESTRETRIEVALPASS00002",
                    ],
                    "facts": [
                        {
                            "fact_id": "01TESTRETRIEVALPASS00001",
                            "text": "The mathematician Alfred Kempe proved the four color theorem.",
                            "topic": "math",
                            "scope": "project",
                        },
                        {
                            "fact_id": "01TESTRETRIEVALPASS00002",
                            "text": "The mathematician Alfred Kempe was born in London.",
                            "topic": "biography",
                            "scope": "project",
                        },
                    ],
                },
                {
                    "id": "retrieval-fail",
                    "question": "Where was Alfred Kempe, the mathematician who proved the four color theorem, born?",
                    "supporting_fact_ids": ["missing-fact"],
                    "facts": [
                        {
                            "fact_id": "01TESTRETRIEVALFAIL00001",
                            "text": "The mathematician Alfred Kempe proved the four color theorem.",
                            "topic": "math",
                            "scope": "project",
                        },
                        {
                            "fact_id": "01TESTRETRIEVALFAIL00002",
                            "text": "The mathematician Alfred Kempe was born in London.",
                            "topic": "biography",
                            "scope": "project",
                        },
                    ],
                },
            ]
        )
    )

    payload = run_retrieval_eval(cases_path, default_config(), min_pass_rate=0.75, top_k=5)

    assert payload["status"] == "error"
    assert payload["total"] == 2
    assert payload["passed"] == 1
    assert payload["pass_rate"] == 0.5
    assert payload["failures"][0]["case"] == "retrieval-fail"


def test_run_retrieval_eval_rejects_unknown_case(tmp_path: Path) -> None:
    cases_path = tmp_path / "cases.json"
    cases_path.write_text(
        json.dumps(
            [
                {
                    "id": "only-case",
                    "question": "Where was Alfred Kempe, the mathematician who proved the four color theorem, born?",
                    "supporting_fact_ids": [
                        "01TESTRETRIEVALONLY00001",
                        "01TESTRETRIEVALONLY00002",
                    ],
                    "facts": [
                        {
                            "fact_id": "01TESTRETRIEVALONLY00001",
                            "text": "The mathematician Alfred Kempe proved the four color theorem.",
                            "topic": "math",
                            "scope": "project",
                        },
                        {
                            "fact_id": "01TESTRETRIEVALONLY00002",
                            "text": "The mathematician Alfred Kempe was born in London.",
                            "topic": "biography",
                            "scope": "project",
                        },
                    ],
                }
            ]
        )
    )

    with pytest.raises(RuntimeError, match="no retrieval eval cases matched"):
        run_retrieval_eval(cases_path, default_config(), case_id="missing")


def test_load_retrieval_eval_cases_rejects_non_string_fact_fields(tmp_path: Path) -> None:
    cases_path = tmp_path / "cases.json"
    cases_path.write_text(
        json.dumps(
            [
                {
                    "id": "bad-fact",
                    "question": "Where was Alfred Kempe born?",
                    "supporting_fact_ids": ["fact-1"],
                    "facts": [
                        {
                            "fact_id": "fact-1",
                            "text": None,
                            "topic": "biography",
                            "scope": "project",
                        }
                    ],
                }
            ]
        )
    )

    with pytest.raises(RuntimeError, match="retrieval eval fact payload is missing `text`"):
        load_retrieval_eval_cases(cases_path)
