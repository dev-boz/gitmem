from __future__ import annotations

import json
from pathlib import Path

import pytest

from umx.benchmark_llm import BenchmarkMessageResult
from umx.config import default_config
from umx.ruler_eval import load_ruler_eval_cases, run_ruler_eval


def test_load_ruler_eval_cases_reads_normalized_fixture() -> None:
    cases = load_ruler_eval_cases(Path("tests") / "eval" / "ruler")

    assert [case.question_id for case in cases] == ["ruler-niah-001", "ruler-vt-001"]
    assert cases[0].base_task == "niah"
    assert cases[1].scorer == "string_match_all"


def test_load_ruler_eval_cases_reads_manifest_jsonl(tmp_path: Path) -> None:
    dataset_dir = tmp_path / "generated" / "qa_1"
    dataset_dir.mkdir(parents=True)
    (dataset_dir / "validation.jsonl").write_text(
        json.dumps(
            {
                "input": "Context: Paris is the capital.\nQuestion: What city is the capital?",
                "outputs": ["Paris", "The answer is Paris"],
                "length": 8192,
                "answer_prefix": "Answer:",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    manifest_path = tmp_path / "generated" / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "format": "ruler-manifest",
                "tasks": [
                    {
                        "task": "qa_1",
                        "base_task": "qa",
                        "category": "synthetic-qa",
                        "context_length": 8192,
                        "path": "qa_1/validation.jsonl",
                        "scorer": "string_match_part",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    cases = load_ruler_eval_cases(manifest_path)

    assert len(cases) == 1
    assert cases[0].question_id == "qa_1-0"
    assert cases[0].prompt.startswith("Context: Paris")
    assert cases[0].scorer == "string_match_part"
    assert cases[0].answer_prefix == "Answer:"


def test_load_ruler_eval_cases_rejects_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "cases.json"
    path.write_text("{not valid}", encoding="utf-8")

    with pytest.raises(RuntimeError, match="not valid JSON"):
        load_ruler_eval_cases(path)


def test_load_ruler_eval_cases_rejects_manifest_path_escape(tmp_path: Path) -> None:
    outside = tmp_path / "outside.jsonl"
    outside.write_text('{"input":"hi","outputs":["A"],"length":1}\n', encoding="utf-8")
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "format": "ruler-manifest",
                "dataset_dir": ".",
                "tasks": [
                    {
                        "task": "qa_1",
                        "base_task": "qa",
                        "category": "synthetic-qa",
                        "context_length": 8192,
                        "path": "../outside.jsonl",
                        "scorer": "string_match_part",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="must stay within"):
        load_ruler_eval_cases(manifest_path)


def test_run_ruler_eval_writes_predictions_and_summary(monkeypatch, tmp_path: Path) -> None:
    cases_path = Path("tests") / "eval" / "ruler"
    out_dir = tmp_path / "artifacts"

    def _send(provider, *, config, model, system, prompt):
        assert provider == "gemini-cli"
        assert model == "gemini-2.5-flash"
        assert "synthetic long-context benchmark" in system
        if "green-apple" in prompt:
            return BenchmarkMessageResult(
                text="1234567",
                model="gemini-2.5-flash",
                usage={"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
            )
        return BenchmarkMessageResult(
            text="alpha",
            model="gemini-2.5-flash",
            usage={"input_tokens": 7, "output_tokens": 3, "total_tokens": 10},
        )

    monkeypatch.setattr("umx.benchmark_llm.send_benchmark_message_with_retry", _send)

    summary = run_ruler_eval(
        cases_path=cases_path,
        out_dir=out_dir,
        config=default_config(),
        provider="gemini-cli",
        model="gemini-2.5-flash",
        min_average_score=0.7,
    )

    assert summary["status"] == "ok"
    assert summary["average_score"] == pytest.approx(0.75)
    assert summary["pass_rate"] == pytest.approx(0.5)
    assert summary["usage"] == {"input_tokens": 17, "output_tokens": 5, "total_tokens": 22}
    assert summary["generation_prompt_id"] == "gemini-cli.ruler-qa-v1"
    assert summary["task_summary"]["niah_single_1"]["average_score"] == pytest.approx(1.0)
    assert summary["task_summary"]["vt"]["average_score"] == pytest.approx(0.5)

    rows = [
        json.loads(line)
        for line in (out_dir / "predictions.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(rows) == 2
    assert rows[0]["prediction"] == "1234567"
    assert rows[1]["score"] == pytest.approx(0.5)

    written_summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
    assert written_summary["average_score"] == pytest.approx(0.75)


def test_run_ruler_eval_counts_failures_in_denominator(monkeypatch, tmp_path: Path) -> None:
    cases_path = Path("tests") / "eval" / "ruler"
    out_dir = tmp_path / "artifacts"

    def _send(provider, *, config, model, system, prompt):
        if "green-apple" in prompt:
            return BenchmarkMessageResult(
                text="1234567",
                model="gemini-2.5-flash",
                usage={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            )
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr("umx.benchmark_llm.send_benchmark_message_with_retry", _send)

    summary = run_ruler_eval(
        cases_path=cases_path,
        out_dir=out_dir,
        config=default_config(),
        provider="gemini-cli",
        model="gemini-2.5-flash",
        min_average_score=0.4,
    )

    assert summary["status"] == "error"
    assert summary["completed"] == 1
    assert summary["failed"] == 1
    assert summary["average_score"] == pytest.approx(0.5)
    assert summary["task_summary"]["vt"]["cases"] == 1
    assert summary["task_summary"]["vt"]["average_score"] == 0.0


def test_run_ruler_eval_capture_only_does_not_mark_gate_passed(monkeypatch, tmp_path: Path) -> None:
    cases_path = Path("tests") / "eval" / "ruler"
    out_dir = tmp_path / "artifacts"

    monkeypatch.setattr(
        "umx.benchmark_llm.send_benchmark_message_with_retry",
        lambda provider, *, config, model, system, prompt: BenchmarkMessageResult(  # noqa: ARG005
            text="1234567" if "green-apple" in prompt else "alpha bravo",
            model="gemini-2.5-flash",
            usage={},
        ),
    )

    summary = run_ruler_eval(
        cases_path=cases_path,
        out_dir=out_dir,
        config=default_config(),
        provider="gemini-cli",
        model="gemini-2.5-flash",
        min_average_score=0.0,
    )

    assert summary["status"] == "ok"
    assert summary["capture_only"] is True
    assert summary["gate_passed"] is False


def test_run_ruler_eval_rejects_invalid_threshold(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="between 0 and 1"):
        run_ruler_eval(
            cases_path=Path("tests") / "eval" / "ruler",
            out_dir=tmp_path / "artifacts",
            config=default_config(),
            provider="gemini-cli",
            model="gemini-2.5-flash",
            min_average_score=1.5,
        )
