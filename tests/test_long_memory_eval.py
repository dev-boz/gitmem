from __future__ import annotations

import json
from pathlib import Path

import pytest

from umx.config import default_config
from umx.long_memory_eval import load_long_memory_eval_cases, retrieve_long_memory_sessions, run_long_memory_eval


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

    assert payload["suite"] == "long-memory"
    assert payload["benchmark"]["name"] == "LongMemEval"
    assert Path(payload["cases_path"]).name == "cases.json"
    assert payload["case_filter"] is None
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
                    "question_type": "multi-session",
                    "question": "Which drinks does Sam prefer?",
                    "answer_session_ids": ["2026-01-11-tea-pref", "2026-01-12-coffee-pref"],
                    "haystack_session_ids": ["2026-01-11-tea-pref", "2026-01-12-coffee-pref"],
                    "haystack_dates": ["2026-01-11T09:00:00Z", "2026-01-12T09:00:00Z"],
                    "haystack_sessions": [
                        [
                            {"role": "user", "content": "Sam prefers tea in the morning."}
                        ],
                        [
                            {"role": "user", "content": "Sam prefers coffee in the afternoon."}
                        ],
                    ],
                },
            ]
        )
    )

    payload = run_long_memory_eval(cases_path, default_config(), min_pass_rate=0.75, search_limit=1)

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


def test_run_long_memory_eval_supports_longmemeval_manifest(tmp_path: Path) -> None:
    dataset_path = tmp_path / "longmemeval.json"
    dataset_path.write_text(
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
                    "question_id": "longmem-skip",
                    "question_type": "single-session-user",
                    "question": "Which city does Sam prefer?",
                    "answer_session_ids": ["2026-01-11-city-pref"],
                    "haystack_session_ids": ["2026-01-11-city-pref"],
                    "haystack_dates": ["2026-01-11T09:00:00Z"],
                    "haystack_sessions": [
                        [
                            {"role": "user", "content": "I prefer Lisbon."}
                        ]
                    ],
                },
            ]
        )
    )
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "format": "longmemeval-manifest",
                "dataset_path": "longmemeval.json",
                "question_ids": ["longmem-pass"],
            }
        )
    )

    payload = run_long_memory_eval(manifest_path, default_config(), min_pass_rate=1.0, search_limit=5)

    assert payload["status"] == "ok"
    assert payload["total"] == 1
    assert payload["results"][0]["case"] == "longmem-pass"
    assert payload["results"][0]["matched_session_ids"] == ["2026-01-10-tea-pref"]


def test_load_long_memory_eval_cases_rejects_duplicate_case_ids(tmp_path: Path) -> None:
    dataset_path = tmp_path / "longmemeval.json"
    dataset_path.write_text(
        json.dumps(
            [
                {
                    "question_id": "duplicate-case",
                    "question_type": "single-session-user",
                    "question": "Which tea does Sam prefer?",
                    "answer_session_ids": ["2026-01-10-tea-pref"],
                    "haystack_session_ids": ["2026-01-10-tea-pref"],
                    "haystack_dates": ["2026-01-10T09:00:00Z"],
                    "haystack_sessions": [
                        [{"role": "user", "content": "I prefer oolong tea in the morning."}]
                    ],
                },
                {
                    "question_id": "duplicate-case",
                    "question_type": "single-session-user",
                    "question": "Which city does Sam prefer?",
                    "answer_session_ids": ["2026-01-11-city-pref"],
                    "haystack_session_ids": ["2026-01-11-city-pref"],
                    "haystack_dates": ["2026-01-11T09:00:00Z"],
                    "haystack_sessions": [[{"role": "user", "content": "I prefer Lisbon."}]],
                },
            ]
        )
    )

    with pytest.raises(RuntimeError, match="duplicate question_id"):
        load_long_memory_eval_cases(dataset_path)


def test_load_long_memory_eval_cases_rejects_duplicate_manifest_question_ids(tmp_path: Path) -> None:
    dataset_path = tmp_path / "longmemeval.json"
    dataset_path.write_text(
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
                        [{"role": "user", "content": "I prefer oolong tea in the morning."}]
                    ],
                }
            ]
        )
    )
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "format": "longmemeval-manifest",
                "dataset_path": "longmemeval.json",
                "question_ids": ["longmem-pass", "longmem-pass"],
            }
        )
    )

    with pytest.raises(RuntimeError, match="duplicate question_ids"):
        load_long_memory_eval_cases(manifest_path)


def test_run_long_memory_eval_supports_non_date_session_ids(tmp_path: Path) -> None:
    cases_path = tmp_path / "cases.json"
    cases_path.write_text(
        json.dumps(
            [
                {
                    "question_id": "longmem-upstream-shape",
                    "question_type": "single-session-user",
                    "question": "What degree did I graduate with?",
                    "answer_session_ids": ["answer_280352e9"],
                    "haystack_session_ids": ["sharegpt_yywfIrx_0", "answer_280352e9"],
                    "haystack_dates": [
                        "2023/05/20 (Sat) 02:21",
                        "2023/05/30 (Tue) 21:40",
                    ],
                    "haystack_sessions": [
                        [
                            {
                                "role": "user",
                                "content": "Can you help me solve a river crossing puzzle?",
                            }
                        ],
                        [
                            {
                                "role": "user",
                                "content": "I graduated with a degree in Business Administration.",
                            }
                        ],
                    ],
                }
            ]
        )
    )

    payload = run_long_memory_eval(cases_path, default_config(), min_pass_rate=0.0, search_limit=5)

    assert payload["status"] == "ok"
    assert payload["total"] == 1
    assert payload["results"][0]["case"] == "longmem-upstream-shape"
    assert payload["results"][0]["actual_session_ids"] is not None
    assert "answer_280352e9" in payload["results"][0]["actual_session_ids"]
    assert payload["results"][0]["matched_session_ids"] == ["answer_280352e9"]


def test_run_long_memory_eval_dedupes_upstream_session_ids_after_remap(tmp_path: Path) -> None:
    cases_path = tmp_path / "cases.json"
    cases_path.write_text(
        json.dumps(
            [
                {
                    "question_id": "longmem-duplicate-upstream-id",
                    "question_type": "single-session-user",
                    "question": "coffee tea",
                    "answer_session_ids": ["answer_unique"],
                    "haystack_session_ids": ["dup_id", "dup_id", "answer_unique"],
                    "haystack_dates": [
                        "2023/05/20 (Sat) 02:21",
                        "2023/05/21 (Sun) 02:21",
                        "2023/05/22 (Mon) 02:21",
                    ],
                    "haystack_sessions": [
                        [{"role": "user", "content": "coffee"}],
                        [{"role": "user", "content": "coffee"}],
                        [{"role": "user", "content": "tea"}],
                    ],
                }
            ]
        )
    )

    payload = run_long_memory_eval(cases_path, default_config(), min_pass_rate=0.0, search_limit=2)

    assert payload["status"] == "ok"
    assert payload["results"][0]["actual_session_ids"] == ["dup_id", "answer_unique"]
    assert payload["results"][0]["matched_session_ids"] == ["answer_unique"]


def test_run_long_memory_eval_rerank_respects_storage_level_candidates_with_duplicate_ids(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import umx.long_memory_eval as long_memory_eval

    cases_path = tmp_path / "cases.json"
    cases_path.write_text(
        json.dumps(
            [
                {
                    "question_id": "longmem-duplicate-candidates",
                    "question_type": "single-session-user",
                    "question": "tea",
                    "answer_session_ids": ["answer_unique"],
                    "haystack_session_ids": ["dup_id", "dup_id", "answer_unique"],
                    "haystack_dates": [
                        "2023-05-20T02:21:00Z",
                        "2023-05-21T02:21:00Z",
                        "2023-05-22T02:21:00Z",
                    ],
                    "haystack_sessions": [
                        [{"role": "user", "content": "coffee"}],
                        [{"role": "user", "content": "tea"}],
                        [{"role": "user", "content": "tea"}],
                    ],
                }
            ]
        )
    )

    dup_candidate_storage_id = long_memory_eval._storage_session_id(  # noqa: SLF001
        "dup_id",
        started="2023-05-20T02:21:00Z",
        index=0,
    )
    answer_storage_id = long_memory_eval._storage_session_id(  # noqa: SLF001
        "answer_unique",
        started="2023-05-22T02:21:00Z",
        index=2,
    )

    monkeypatch.setattr(
        long_memory_eval,
        "search_sessions",
        lambda repo_dir, query, limit=20: [  # noqa: ARG005
            {"session_id": dup_candidate_storage_id, "score": 1.0},
            {"session_id": answer_storage_id, "score": 1.0},
        ],
    )

    payload = long_memory_eval.run_long_memory_eval(
        cases_path,
        default_config(),
        min_pass_rate=1.0,
        search_limit=1,
    )

    assert payload["status"] == "ok"
    assert payload["results"][0]["actual_session_ids"] == ["answer_unique"]
    assert payload["results"][0]["matched_session_ids"] == ["answer_unique"]


def test_run_long_memory_eval_reranks_sessions_with_clean_question_terms(tmp_path: Path) -> None:
    cases_path = tmp_path / "cases.json"
    cases_path.write_text(
        json.dumps(
            [
                {
                    "question_id": "longmem-rerank",
                    "question_type": "single-session-user",
                    "question": "What degree did I graduate with?",
                    "answer_session_ids": ["answer-session"],
                    "haystack_session_ids": [
                        "distractor-1",
                        "distractor-2",
                        "distractor-3",
                        "answer-session",
                    ],
                    "haystack_dates": [
                        "2026-01-10T09:00:00Z",
                        "2026-01-11T09:00:00Z",
                        "2026-01-12T09:00:00Z",
                        "2026-01-13T09:00:00Z",
                    ],
                    "haystack_sessions": [
                        [{"role": "user", "content": "What should I do with my old planner?"}],
                        [{"role": "user", "content": "What did you do with the spare keys?"}],
                        [{"role": "user", "content": "What do I do with my receipts?"}],
                        [
                            {
                                "role": "user",
                                "content": "I graduated with a degree in Business Administration.",
                            }
                        ],
                    ],
                }
            ]
        )
    )

    payload = run_long_memory_eval(cases_path, default_config(), min_pass_rate=1.0, search_limit=1)

    assert payload["status"] == "ok"
    assert payload["results"][0]["actual_session_ids"] == ["answer-session"]
    assert payload["results"][0]["matched_session_ids"] == ["answer-session"]


def test_retrieve_long_memory_sessions_supports_full_first_rerank_for_assistant_answers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import umx.long_memory_eval as long_memory_eval

    cases_path = tmp_path / "cases.json"
    cases_path.write_text(
        json.dumps(
            [
                {
                    "question_id": "longmem-assistant-answer",
                    "question_type": "single-session-assistant",
                    "question": "Who recommended Trello?",
                    "answer_session_ids": ["assistant-answer"],
                    "haystack_session_ids": ["user-distractor", "assistant-answer"],
                    "haystack_dates": ["2026-01-10T09:00:00Z", "2026-01-11T09:00:00Z"],
                    "haystack_sessions": [
                        [{"role": "user", "content": "I use Trello for tracking chores."}],
                        [{"role": "assistant", "content": "I recommended Trello for task management."}],
                    ],
                }
            ]
        )
    )
    case = load_long_memory_eval_cases(cases_path)[0]
    distractor_storage_id = long_memory_eval._storage_session_id(  # noqa: SLF001
        "user-distractor",
        started="2026-01-10T09:00:00Z",
        index=0,
    )
    answer_storage_id = long_memory_eval._storage_session_id(  # noqa: SLF001
        "assistant-answer",
        started="2026-01-11T09:00:00Z",
        index=1,
    )
    monkeypatch.setattr(
        long_memory_eval,
        "search_sessions",
        lambda repo_dir, query, limit=20: [  # noqa: ARG005
            {"session_id": distractor_storage_id, "score": 1.0},
            {"session_id": answer_storage_id, "score": 1.0},
        ],
    )

    sessions = retrieve_long_memory_sessions(
        case,
        config=default_config(),
        search_limit=1,
        rerank_mode="full-first",
    )

    assert [session.session_id for session in sessions] == ["assistant-answer"]


def test_run_long_memory_eval_does_not_recover_sessions_outside_search_candidates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import umx.long_memory_eval as long_memory_eval

    cases_path = tmp_path / "cases.json"
    cases_path.write_text(
        json.dumps(
            [
                {
                    "question_id": "longmem-search-boundary",
                    "question_type": "single-session-user",
                    "question": "What degree did I graduate with?",
                    "answer_session_ids": ["answer-session"],
                    "haystack_session_ids": ["distractor-session", "answer-session"],
                    "haystack_dates": ["2026-01-10T09:00:00Z", "2026-01-11T09:00:00Z"],
                    "haystack_sessions": [
                        [{"role": "user", "content": "What should I do with my old planner?"}],
                        [
                            {
                                "role": "user",
                                "content": "I graduated with a degree in Business Administration.",
                            }
                        ],
                    ],
                }
            ]
        )
    )

    distractor_storage_id = long_memory_eval._storage_session_id(  # noqa: SLF001
        "distractor-session",
        started="2026-01-10T09:00:00Z",
        index=0,
    )

    monkeypatch.setattr(
        long_memory_eval,
        "search_sessions",
        lambda repo_dir, query, limit=20: [  # noqa: ARG005
            {"session_id": distractor_storage_id, "score": 1.0}
        ],
    )

    payload = long_memory_eval.run_long_memory_eval(
        cases_path,
        default_config(),
        min_pass_rate=1.0,
        search_limit=1,
    )

    assert payload["status"] == "error"
    assert payload["results"][0]["actual_session_ids"] == ["distractor-session"]
    assert payload["results"][0]["matched_session_ids"] == []


def test_load_long_memory_eval_cases_rejects_manifest_with_missing_question(tmp_path: Path) -> None:
    dataset_path = tmp_path / "longmemeval.json"
    dataset_path.write_text(
        json.dumps(
            [
                {
                    "question_id": "longmem-only",
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
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "format": "longmemeval-manifest",
                "dataset_path": "longmemeval.json",
                "question_ids": ["missing"],
            }
        )
    )

    with pytest.raises(RuntimeError, match="unknown question_id `missing`"):
        load_long_memory_eval_cases(manifest_path)


def test_load_long_memory_eval_cases_rejects_missing_answer_session_id(tmp_path: Path) -> None:
    cases_path = tmp_path / "cases.json"
    cases_path.write_text(
        json.dumps(
            [
                {
                    "question_id": "bad-case",
                    "question_type": "single-session-user",
                    "question": "Which tea does Sam prefer?",
                    "answer_session_ids": ["missing-session"],
                    "haystack_session_ids": ["2026-01-10-tea-pref"],
                    "haystack_dates": ["2026-01-10T09:00:00Z"],
                    "haystack_sessions": [
                        [{"role": "user", "content": "I prefer oolong tea in the morning."}]
                    ],
                }
            ]
        )
    )

    with pytest.raises(RuntimeError, match="unknown answer_session_ids"):
        load_long_memory_eval_cases(cases_path)


def test_run_long_memory_eval_surfaces_case_runtime_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import umx.long_memory_eval as long_memory_eval

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
                        [{"role": "user", "content": "I prefer oolong tea in the morning."}]
                    ],
                }
            ]
        )
    )

    def _explode(*args: object, **kwargs: object) -> list[str]:
        raise ValueError("boom")

    monkeypatch.setattr(long_memory_eval, "_run_case", _explode)

    with pytest.raises(RuntimeError, match="long-memory eval case `longmem-pass` failed: boom"):
        long_memory_eval.run_long_memory_eval(cases_path, default_config())
