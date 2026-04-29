from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from umx.cli import main
from umx.config import default_config
from umx.release_gate_eval import run_release_gate_eval


def _write_json(path: Path, payload: dict[str, object]) -> Path:
    path.write_text(json.dumps(payload))
    return path


def _long_memory_case_file(tmp_path: Path) -> Path:
    path = tmp_path / "long-memory.json"
    path.write_text(
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
    return path


def _retrieval_case_file(tmp_path: Path) -> Path:
    path = tmp_path / "retrieval.json"
    path.write_text(
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
    return path


def _failing_long_memory_case_file(tmp_path: Path) -> Path:
    path = tmp_path / "long-memory-fail.json"
    path.write_text(
        json.dumps(
            [
                {
                    "question_id": "longmem-capture-only",
                    "question_type": "single-session-user",
                    "question": "Which tea does Sam prefer?",
                    "answer_session_ids": ["2026-01-10-answer"],
                    "haystack_session_ids": [
                        "2026-01-10-distractor-1",
                        "2026-01-10-distractor-2",
                        "2026-01-10-distractor-3",
                        "2026-01-10-distractor-4",
                        "2026-01-10-distractor-5",
                        "2026-01-10-distractor-6",
                        "2026-01-10-answer",
                    ],
                    "haystack_dates": [
                        "2026-01-10T09:00:00Z",
                        "2026-01-11T09:00:00Z",
                        "2026-01-12T09:00:00Z",
                        "2026-01-13T09:00:00Z",
                        "2026-01-14T09:00:00Z",
                        "2026-01-15T09:00:00Z",
                        "2026-01-16T09:00:00Z",
                    ],
                    "haystack_sessions": [
                        [{"role": "user", "content": "Sam prefers coffee after tea break one."}],
                        [{"role": "user", "content": "Sam prefers coffee after tea break two."}],
                        [{"role": "user", "content": "Sam prefers coffee after tea break three."}],
                        [{"role": "user", "content": "Sam prefers coffee after tea break four."}],
                        [{"role": "user", "content": "Sam prefers coffee after tea break five."}],
                        [{"role": "user", "content": "Sam prefers coffee after tea break six."}],
                        [{"role": "user", "content": "I start mornings with oolong."}],
                    ],
                }
            ]
        )
    )
    return path


def _failing_retrieval_case_file(tmp_path: Path) -> Path:
    path = tmp_path / "retrieval-fail.json"
    path.write_text(
        json.dumps(
            [
                {
                    "id": "retrieval-capture-only",
                    "question": "Where was Alfred Kempe born?",
                    "supporting_fact_ids": ["01TESTRETRIEVALFAIL00007"],
                    "facts": [
                        {
                            "fact_id": "01TESTRETRIEVALFAIL00001",
                            "text": "Alfred Kempe was born near the river district.",
                            "topic": "math",
                            "scope": "project",
                        },
                        {
                            "fact_id": "01TESTRETRIEVALFAIL00002",
                            "text": "Alfred Kempe was born near the observatory.",
                            "topic": "biography",
                            "scope": "project",
                        },
                        {
                            "fact_id": "01TESTRETRIEVALFAIL00003",
                            "text": "Alfred Kempe was born during a cold winter.",
                            "topic": "biography",
                            "scope": "project",
                        },
                        {
                            "fact_id": "01TESTRETRIEVALFAIL00004",
                            "text": "Alfred Kempe was born before his family moved south.",
                            "topic": "biography",
                            "scope": "project",
                        },
                        {
                            "fact_id": "01TESTRETRIEVALFAIL00005",
                            "text": "Alfred Kempe was born in a bustling port district.",
                            "topic": "biography",
                            "scope": "project",
                        },
                        {
                            "fact_id": "01TESTRETRIEVALFAIL00006",
                            "text": "Alfred Kempe was born to a family of merchants.",
                            "topic": "biography",
                            "scope": "project",
                        },
                        {
                            "fact_id": "01TESTRETRIEVALFAIL00007",
                            "text": "The birthplace was London.",
                            "topic": "biography",
                            "scope": "project",
                        },
                    ],
                }
            ]
        )
    )
    return path


def test_run_release_gate_eval_writes_bundle(tmp_path: Path) -> None:
    out_dir = tmp_path / "artifacts"
    summary = run_release_gate_eval(
        out_dir,
        default_config(),
        long_memory_release_cases=_long_memory_case_file(tmp_path),
        retrieval_release_cases=_retrieval_case_file(tmp_path),
    )

    assert summary["status"] == "ok"
    assert (out_dir / "summary.json").exists()
    assert (out_dir / "local" / "inject.smoke.json").exists()
    assert (out_dir / "local" / "long-memory.smoke.json").exists()
    assert (out_dir / "local" / "retrieval.smoke.json").exists()
    assert (out_dir / "release" / "longmemeval.release.json").exists()
    assert (out_dir / "release" / "hotpotqa.release.json").exists()
    assert summary["release"]["long-memory"]["status"] == "ok"
    assert summary["release"]["retrieval"]["status"] == "ok"


def test_cli_eval_release_gate_writes_compare_artifacts(tmp_path: Path) -> None:
    long_memory_cases = _long_memory_case_file(tmp_path)
    retrieval_cases = _retrieval_case_file(tmp_path)
    long_memory_baseline = _write_json(
        tmp_path / "longmem-baseline.json",
        {
            "suite": "long-memory",
            "status": "ok",
            "pass_rate": 1.0,
            "average_recall": 1.0,
            "type_summary": {"single-session-user": {"average_recall": 1.0}},
        },
    )
    retrieval_baseline = _write_json(
        tmp_path / "retrieval-baseline.json",
        {
            "suite": "retrieval",
            "status": "ok",
            "pass_rate": 1.0,
            "average_recall": 1.0,
            "average_answer_coverage": 1.0,
        },
    )

    out_dir = tmp_path / "bundle"
    result = CliRunner().invoke(
        main,
        [
            "eval",
            "release-gate",
            "--out-dir",
            str(out_dir),
            "--long-memory-release-cases",
            str(long_memory_cases),
            "--retrieval-release-cases",
            str(retrieval_cases),
            "--long-memory-baseline",
            str(long_memory_baseline),
            "--retrieval-baseline",
            str(retrieval_baseline),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "ok"
    assert (out_dir / "release" / "longmemeval.compare.json").exists()
    assert (out_dir / "release" / "hotpotqa.compare.json").exists()
    assert payload["compare"]["long-memory"]["status"] == "ok"
    assert payload["compare"]["retrieval"]["status"] == "ok"


def test_cli_eval_release_gate_supports_capture_only_release_mode(tmp_path: Path) -> None:
    out_dir = tmp_path / "capture-only"
    result = CliRunner().invoke(
        main,
        [
            "eval",
            "release-gate",
            "--out-dir",
            str(out_dir),
            "--long-memory-release-cases",
            str(_failing_long_memory_case_file(tmp_path)),
            "--retrieval-release-cases",
            str(_failing_retrieval_case_file(tmp_path)),
            "--long-memory-release-min-pass-rate",
            "0",
            "--retrieval-release-min-pass-rate",
            "0",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "ok"
    assert payload["release_capture_only"] is True
    assert payload["release"]["long-memory"]["status"] == "captured"
    assert payload["release"]["long-memory"]["eval_status"] == "ok"
    assert payload["release"]["long-memory"]["capture_only"] is True
    assert payload["release"]["long-memory"]["gate_passed"] is False
    assert payload["release"]["retrieval"]["status"] == "captured"
    assert payload["release"]["retrieval"]["eval_status"] == "ok"
    assert payload["release"]["retrieval"]["capture_only"] is True
    assert payload["release"]["retrieval"]["gate_passed"] is False
