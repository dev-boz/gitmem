from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from umx.cli import main
from umx.config import default_config
from umx.retrieval_eval import load_retrieval_eval_cases, run_retrieval_eval


FIXTURES_ROOT = Path(__file__).parent / "eval" / "retrieval"


def test_retrieval_eval_cases_load_from_fixture_dir() -> None:
    cases = load_retrieval_eval_cases(FIXTURES_ROOT)

    assert len(cases) >= 3
    assert all(case.supporting_fact_ids for case in cases)


def test_run_retrieval_eval_passes_fixture_subset() -> None:
    payload = run_retrieval_eval(FIXTURES_ROOT, default_config(), min_pass_rate=1.0, top_k=5)

    assert payload["suite"] == "retrieval"
    assert payload["benchmark"]["name"] == "HotpotQA"
    assert Path(payload["cases_path"]).name == "cases.json"
    assert payload["case_filter"] is None
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
                    "question": "Where was Alfred Kempe born?",
                    "supporting_fact_ids": ["01TESTRETRIEVALPASS00001"],
                    "facts": [
                        {
                            "fact_id": "01TESTRETRIEVALPASS00001",
                            "text": "The mathematician Alfred Kempe was born in London.",
                            "topic": "biography",
                            "scope": "project",
                        },
                        {
                            "fact_id": "01TESTRETRIEVALPASS00002",
                            "text": "William Rowan Hamilton discovered quaternions.",
                            "topic": "math",
                            "scope": "project",
                        },
                    ],
                },
                {
                    "id": "retrieval-fail",
                    "question": "Which drinks does Sam prefer?",
                    "supporting_fact_ids": [
                        "01TESTRETRIEVALFAIL00001",
                        "01TESTRETRIEVALFAIL00002",
                    ],
                    "facts": [
                        {
                            "fact_id": "01TESTRETRIEVALFAIL00001",
                            "text": "Sam prefers tea in the morning.",
                            "topic": "preference",
                            "scope": "project",
                        },
                        {
                            "fact_id": "01TESTRETRIEVALFAIL00002",
                            "text": "Sam prefers coffee in the afternoon.",
                            "topic": "preference",
                            "scope": "project",
                        },
                        {
                            "fact_id": "01TESTRETRIEVALFAIL00003",
                            "text": "The deployment checklist includes smoke tests.",
                            "topic": "ops",
                            "scope": "project",
                        },
                    ],
                },
            ]
        )
    )

    payload = run_retrieval_eval(cases_path, default_config(), min_pass_rate=0.75, top_k=1)

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


def test_run_retrieval_eval_supports_hotpotqa_manifest(tmp_path: Path) -> None:
    dataset_path = tmp_path / "hotpotqa.json"
    dataset_path.write_text(
        json.dumps(
            [
                {
                    "_id": "hotpot-pass",
                    "question": "Where was Alfred Kempe, the mathematician who proved the four color theorem, born?",
                    "answer": "London",
                    "supporting_facts": [["Alfred Kempe", 0], ["Alfred Kempe", 1]],
                    "context": [
                        [
                            "Alfred Kempe",
                            [
                                "Alfred Kempe proved the four color theorem.",
                                "Alfred Kempe was born in London.",
                            ],
                        ],
                        ["William Rowan Hamilton", ["William Rowan Hamilton discovered quaternions."]],
                    ],
                }
            ]
        )
    )
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "format": "hotpotqa-manifest",
                "dataset_path": "hotpotqa.json",
                "question_ids": ["hotpot-pass"],
            }
        )
    )

    payload = run_retrieval_eval(manifest_path, default_config(), min_pass_rate=1.0, top_k=5)

    assert payload["status"] == "ok"
    assert payload["total"] == 1
    assert payload["average_recall"] == 1.0
    assert payload["average_answer_coverage"] == 1.0
    assert payload["results"][0]["case"] == "hotpot-pass"
    assert payload["results"][0]["expected_answer"] == "London"
    assert payload["results"][0]["answer_coverage"] == 1.0


def test_load_retrieval_eval_cases_rejects_duplicate_case_ids(tmp_path: Path) -> None:
    dataset_path = tmp_path / "hotpotqa.json"
    dataset_path.write_text(
        json.dumps(
            [
                {
                    "_id": "duplicate-case",
                    "question": "Where was Alfred Kempe born?",
                    "answer": "London",
                    "supporting_facts": [["Alfred Kempe", 0]],
                    "context": [["Alfred Kempe", ["Alfred Kempe was born in London."]]],
                },
                {
                    "_id": "duplicate-case",
                    "question": "Who discovered quaternions?",
                    "answer": "William Rowan Hamilton",
                    "supporting_facts": [["William Rowan Hamilton", 0]],
                    "context": [
                        ["William Rowan Hamilton", ["William Rowan Hamilton discovered quaternions."]]
                    ],
                },
            ]
        )
    )

    with pytest.raises(RuntimeError, match="duplicate question_id"):
        load_retrieval_eval_cases(dataset_path)


def test_load_retrieval_eval_cases_rejects_duplicate_manifest_question_ids(tmp_path: Path) -> None:
    dataset_path = tmp_path / "hotpotqa.json"
    dataset_path.write_text(
        json.dumps(
            [
                {
                    "_id": "hotpot-pass",
                    "question": "Where was Alfred Kempe born?",
                    "answer": "London",
                    "supporting_facts": [["Alfred Kempe", 0]],
                    "context": [["Alfred Kempe", ["Alfred Kempe was born in London."]]],
                }
            ]
        )
    )
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "format": "hotpotqa-manifest",
                "dataset_path": "hotpotqa.json",
                "question_ids": ["hotpot-pass", "hotpot-pass"],
            }
        )
    )

    with pytest.raises(RuntimeError, match="duplicate question_ids"):
        load_retrieval_eval_cases(manifest_path)


def test_run_retrieval_eval_supports_raw_hotpotqa_json(tmp_path: Path) -> None:
    dataset_path = tmp_path / "hotpotqa.json"
    dataset_path.write_text(
        json.dumps(
            [
                {
                    "_id": "hotpot-pass",
                    "question": "Where was Alfred Kempe, the mathematician who proved the four color theorem, born?",
                    "answer": "London",
                    "supporting_facts": [["Alfred Kempe", 0], ["Alfred Kempe", 1]],
                    "context": [
                        [
                            "Alfred Kempe",
                            [
                                "Alfred Kempe proved the four color theorem.",
                                "Alfred Kempe was born in London.",
                            ],
                        ],
                        ["William Rowan Hamilton", ["William Rowan Hamilton discovered quaternions."]],
                    ],
                },
                {
                    "_id": "hotpot-skip",
                    "question": "Who discovered quaternions?",
                    "answer": "William Rowan Hamilton",
                    "supporting_facts": [["William Rowan Hamilton", 0]],
                    "context": [
                        ["William Rowan Hamilton", ["William Rowan Hamilton discovered quaternions."]],
                    ],
                },
            ]
        )
    )

    payload = run_retrieval_eval(
        dataset_path,
        default_config(),
        case_id="hotpot-pass",
        min_pass_rate=1.0,
        top_k=5,
    )

    assert payload["status"] == "ok"
    assert payload["total"] == 1
    assert Path(payload["cases_path"]) == dataset_path
    assert payload["case_filter"] == "hotpot-pass"
    assert payload["average_recall"] == 1.0
    assert payload["average_answer_coverage"] == 1.0
    assert payload["results"][0]["case"] == "hotpot-pass"
    assert payload["results"][0]["expected_answer"] == "London"


def test_cli_retrieval_eval_supports_raw_hotpotqa_json(tmp_path: Path) -> None:
    dataset_path = tmp_path / "hotpotqa.json"
    dataset_path.write_text(
        json.dumps(
            [
                {
                    "_id": "hotpot-pass",
                    "question": "Where was Alfred Kempe, the mathematician who proved the four color theorem, born?",
                    "answer": "London",
                    "supporting_facts": [["Alfred Kempe", 0], ["Alfred Kempe", 1]],
                    "context": [
                        [
                            "Alfred Kempe",
                            [
                                "Alfred Kempe proved the four color theorem.",
                                "Alfred Kempe was born in London.",
                            ],
                        ],
                        ["William Rowan Hamilton", ["William Rowan Hamilton discovered quaternions."]],
                    ],
                }
            ]
        )
    )

    result = CliRunner().invoke(
        main,
        [
            "eval",
            "retrieval",
            "--cases",
            str(dataset_path),
            "--case",
            "hotpot-pass",
            "--min-pass-rate",
            "1.0",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "ok"
    assert payload["suite"] == "retrieval"
    assert payload["case_filter"] == "hotpot-pass"
    assert payload["results"][0]["case"] == "hotpot-pass"


def test_load_retrieval_eval_cases_rejects_hotpotqa_manifest_with_missing_question(tmp_path: Path) -> None:
    dataset_path = tmp_path / "hotpotqa.json"
    dataset_path.write_text(
        json.dumps(
            [
                {
                    "_id": "hotpot-only",
                    "question": "Where was Alfred Kempe born?",
                    "answer": "London",
                    "supporting_facts": [["Alfred Kempe", 0]],
                    "context": [["Alfred Kempe", ["Alfred Kempe was born in London."]]],
                }
            ]
        )
    )
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "format": "hotpotqa-manifest",
                "dataset_path": "hotpotqa.json",
                "question_ids": ["missing"],
            }
        )
    )

    with pytest.raises(RuntimeError, match="unknown question_id `missing`"):
        load_retrieval_eval_cases(manifest_path)


def test_load_retrieval_eval_cases_rejects_missing_supporting_fact_id(tmp_path: Path) -> None:
    cases_path = tmp_path / "cases.json"
    cases_path.write_text(
        json.dumps(
            [
                {
                    "id": "bad-case",
                    "question": "Where was Alfred Kempe born?",
                    "supporting_fact_ids": ["missing-fact"],
                    "facts": [
                        {
                            "fact_id": "fact-1",
                            "text": "Alfred Kempe was born in London.",
                            "topic": "biography",
                            "scope": "project",
                        }
                    ],
                }
            ]
        )
    )

    with pytest.raises(RuntimeError, match="unknown supporting_fact_ids"):
        load_retrieval_eval_cases(cases_path)


def test_load_retrieval_eval_cases_rejects_invalid_raw_hotpotqa_context(tmp_path: Path) -> None:
    dataset_path = tmp_path / "hotpotqa.json"
    dataset_path.write_text(
        json.dumps(
            [
                {
                    "_id": "hotpot-bad",
                    "question": "Where was Alfred Kempe born?",
                    "answer": "London",
                    "supporting_facts": [["Alfred Kempe", 0]],
                    "context": [["Alfred Kempe", [None]]],
                }
            ]
        )
    )

    with pytest.raises(RuntimeError, match="invalid context entry"):
        load_retrieval_eval_cases(dataset_path)


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


def test_run_retrieval_eval_surfaces_case_runtime_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import umx.retrieval_eval as retrieval_eval

    cases_path = tmp_path / "cases.json"
    cases_path.write_text(
        json.dumps(
            [
                {
                    "id": "retrieval-pass",
                    "question": "Where was Alfred Kempe born?",
                    "supporting_fact_ids": ["fact-1"],
                    "facts": [
                        {
                            "fact_id": "fact-1",
                            "text": "Alfred Kempe was born in London.",
                            "topic": "biography",
                            "scope": "project",
                        }
                    ],
                }
            ]
        )
    )

    def _explode(*args: object, **kwargs: object) -> list[str]:
        raise ValueError("boom")

    monkeypatch.setattr(retrieval_eval, "_run_case", _explode)

    with pytest.raises(RuntimeError, match="retrieval eval case `retrieval-pass` failed: boom"):
        retrieval_eval.run_retrieval_eval(cases_path, default_config())
