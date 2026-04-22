from __future__ import annotations

import json
from pathlib import Path

import pytest

from umx.config import default_config
from umx.long_memory_eval import load_long_memory_eval_cases, run_long_memory_eval


FIXTURES_ROOT = Path(__file__).parent / "eval" / "long_memory"


def test_long_memory_eval_cases_load_from_fixture_dir() -> None:
    cases = load_long_memory_eval_cases(FIXTURES_ROOT)

    assert len(cases) >= 5
    assert {case.question_type for case in cases} >= {
        "single-session-user",
        "multi-session",
        "knowledge-update",
        "temporal-reasoning",
        "abstention",
    }


def test_run_long_memory_eval_passes_fixture_subset() -> None:
    payload = run_long_memory_eval(FIXTURES_ROOT, default_config(), min_pass_rate=1.0, search_limit=5)

    assert payload["status"] == "ok"
    assert payload["passed"] == payload["total"]
    assert payload["average_recall"] == 1.0


def test_run_long_memory_eval_computes_gate_failure(tmp_path: Path) -> None:
    cases_path = tmp_path / "cases.json"
    cases_path.write_text(
        json.dumps(
            [
                {
                    "question_id": "longmem-pass",
                    "question_type": "single-session-user",
                    "question": "Which tea does Sam prefer?",
                    "answer_session_ids": ["2026-01-10-tea-pref"],
                    "haystack_session_ids": ["2026-01-10-tea-pref"],
                    "haystack_dates": ["2026-01-10T09:00:00Z"],
                    "haystack_sessions": [
                        [
                            {"role": "user", "content": "I prefer oolong tea in the morning."}
                        ]
                    ],
                },
                {
                    "question_id": "longmem-fail",
                    "question_type": "single-session-user",
                    "question": "Which tea does Sam prefer?",
                    "answer_session_ids": ["missing-session"],
                    "haystack_session_ids": ["2026-01-10-tea-pref"],
                    "haystack_dates": ["2026-01-10T09:00:00Z"],
                    "haystack_sessions": [
                        [
                            {"role": "user", "content": "I prefer oolong tea in the morning."}
                        ]
                    ],
                },
            ]
        )
    )

    payload = run_long_memory_eval(cases_path, default_config(), min_pass_rate=0.75, search_limit=5)

    assert payload["status"] == "error"
    assert payload["total"] == 2
    assert payload["passed"] == 1
    assert payload["pass_rate"] == 0.5
    assert payload["failures"][0]["case"] == "longmem-fail"


def test_run_long_memory_eval_rejects_unknown_case(tmp_path: Path) -> None:
    cases_path = tmp_path / "cases.json"
    cases_path.write_text(
        json.dumps(
            [
                {
                    "question_id": "only-case",
                    "question_type": "single-session-user",
                    "question": "Which tea does Sam prefer?",
                    "answer_session_ids": ["2026-01-10-tea-pref"],
                    "haystack_session_ids": ["2026-01-10-tea-pref"],
                    "haystack_dates": ["2026-01-10T09:00:00Z"],
                    "haystack_sessions": [
                        [
                            {"role": "user", "content": "I prefer oolong tea in the morning."}
                        ]
                    ],
                }
            ]
        )
    )

    with pytest.raises(RuntimeError, match="no long-memory eval cases matched"):
        run_long_memory_eval(cases_path, default_config(), case_id="missing")
