from __future__ import annotations

import json
from pathlib import Path

import pytest

from umx.config import default_config
from umx.inject_eval import load_inject_eval_cases, run_inject_eval


FIXTURES_ROOT = Path(__file__).parent / "eval" / "inject"


def test_inject_eval_cases_load_from_fixture_dir() -> None:
    cases = load_inject_eval_cases(FIXTURES_ROOT)

    assert len(cases) >= 3
    assert cases[0].expected_top_ids


def test_run_inject_eval_computes_pass_rate(tmp_path: Path) -> None:
    cases_path = tmp_path / "cases.json"
    cases_path.write_text(
        json.dumps(
            [
                {
                    "id": "postgres-ranking",
                    "prompt": "postgres dev",
                    "max_tokens": 1400,
                    "expected_top_ids": [
                        "01TESTINJECTEVAL0000001",
                        "01TESTINJECTEVAL0000002",
                    ],
                    "facts": [
                        {
                            "fact_id": "01TESTINJECTEVAL0000001",
                            "text": "postgres runs on 5433 in dev",
                            "topic": "devenv",
                            "scope": "project",
                        },
                        {
                            "fact_id": "01TESTINJECTEVAL0000002",
                            "text": "redis queue workers batch notifications",
                            "topic": "queues",
                            "scope": "project",
                        },
                    ],
                },
                {
                    "id": "postgres-mismatch",
                    "prompt": "postgres dev",
                    "max_tokens": 1400,
                    "expected_top_ids": ["01TESTINJECTEVAL9999999"],
                    "facts": [
                        {
                            "fact_id": "01TESTINJECTEVAL0000003",
                            "text": "postgres runs on 5433 in dev",
                            "topic": "devenv",
                            "scope": "project",
                        },
                        {
                            "fact_id": "01TESTINJECTEVAL0000004",
                            "text": "redis queue workers batch notifications",
                            "topic": "queues",
                            "scope": "project",
                        },
                    ],
                },
            ]
        )
    )

    payload = run_inject_eval(cases_path, default_config(), min_pass_rate=0.75)

    assert payload["status"] == "error"
    assert payload["total"] == 2
    assert payload["passed"] == 1
    assert payload["pass_rate"] == 0.5
    assert payload["failures"][0]["case"] == "postgres-mismatch"


def test_run_inject_eval_rejects_unknown_case(tmp_path: Path) -> None:
    cases_path = tmp_path / "cases.json"
    cases_path.write_text(
        json.dumps(
            [
                {
                    "id": "only-case",
                    "prompt": "postgres dev",
                    "max_tokens": 1400,
                    "expected_top_ids": ["01TESTINJECTEVAL0000010"],
                    "facts": [
                        {
                            "fact_id": "01TESTINJECTEVAL0000010",
                            "text": "postgres runs on 5433 in dev",
                            "topic": "devenv",
                            "scope": "project",
                        }
                    ],
                }
            ]
        )
    )

    with pytest.raises(RuntimeError, match="no inject eval cases matched"):
        run_inject_eval(cases_path, default_config(), case_id="missing")
