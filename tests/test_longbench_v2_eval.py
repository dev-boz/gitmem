from __future__ import annotations

import json
from pathlib import Path

import pytest

from umx.config import default_config
from umx.longbench_v2_eval import load_longbench_v2_eval_cases, run_longbench_v2_eval


def _case_file(tmp_path: Path) -> Path:
    path = tmp_path / "cases.json"
    path.write_text(
        json.dumps(
            [
                {
                    "_id": "lbv2-001",
                    "domain": "Single-Document QA",
                    "sub_domain": "Synthetic",
                    "difficulty": "easy",
                    "length": "short",
                    "question": "Which option is correct?",
                    "choice_A": "Alpha",
                    "choice_B": "Bravo",
                    "choice_C": "Charlie",
                    "choice_D": "Delta",
                    "answer": "B",
                    "context": "The correct option is Bravo because it matches the cited evidence.",
                }
            ]
        ),
        encoding="utf-8",
    )
    return path


def test_load_longbench_v2_eval_cases_reads_official_shape(tmp_path: Path) -> None:
    cases = load_longbench_v2_eval_cases(_case_file(tmp_path))

    assert len(cases) == 1
    assert cases[0].question_id == "lbv2-001"
    assert cases[0].answer == "B"


def test_run_longbench_v2_eval_writes_artifacts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import umx.longbench_v2_eval as longbench_v2_eval

    cases_path = _case_file(tmp_path)
    out_dir = tmp_path / "artifacts"

    monkeypatch.setattr(
        longbench_v2_eval.benchmark_llm,
        "send_benchmark_message_with_retry",
        lambda provider, *, config, model, system, prompt: longbench_v2_eval.benchmark_llm.BenchmarkMessageResult(  # noqa: ARG005
            text="The correct answer is (B)",
            model="gpt-5.2",
            usage={"input_tokens": 12, "output_tokens": 3, "total_tokens": 15},
        ),
    )

    payload = run_longbench_v2_eval(
        out_dir,
        cases_path,
        default_config(),
        provider="codex-cli",
        min_accuracy=0.0,
    )

    assert payload["status"] == "ok"
    assert payload["suite"] == "longbench-v2"
    assert payload["accuracy"] == 1.0
    assert Path(payload["predictions_path"]).exists()
    assert (out_dir / "summary.json").exists()


def test_run_longbench_v2_eval_marks_case_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import umx.longbench_v2_eval as longbench_v2_eval

    cases_path = _case_file(tmp_path)
    out_dir = tmp_path / "artifacts"

    monkeypatch.setattr(
        longbench_v2_eval.benchmark_llm,
        "send_benchmark_message_with_retry",
        lambda provider, *, config, model, system, prompt: (_ for _ in ()).throw(RuntimeError("boom")),  # noqa: ARG005,E731
    )

    payload = run_longbench_v2_eval(
        out_dir,
        cases_path,
        default_config(),
        provider="codex-cli",
        min_accuracy=0.0,
    )

    assert payload["status"] == "error"
    assert payload["gate_passed"] is False
    assert payload["results"][0]["error"] == "boom"
