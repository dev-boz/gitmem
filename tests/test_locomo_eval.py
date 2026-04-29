from __future__ import annotations

import json
from pathlib import Path

import pytest

from umx.config import default_config
from umx.locomo_eval import _answer_user_prompt, _multi_answer_f1, load_locomo_eval_cases, run_locomo_eval


def _raw_locomo_file(tmp_path: Path, *, category: int = 2, answer: str = "7 May 2023") -> Path:
    path = tmp_path / "locomo10.json"
    path.write_text(
        json.dumps(
            [
                {
                    "sample_id": "locomo-sample-001",
                    "conversation": {
                        "speaker_a": "Caroline",
                        "speaker_b": "Max",
                        "session_1_date_time": "7 May 2023",
                        "session_1": [
                            {"speaker": "Caroline", "dia_id": "D1:1", "text": "I went to the support group on 7 May 2023."},
                            {"speaker": "Max", "dia_id": "D1:2", "text": "That sounds helpful."},
                        ],
                    },
                    "qa": [
                        {
                            "question": "When did Caroline go to the support group?",
                            "answer": answer,
                            "evidence": ["D1:1"],
                            "category": category,
                        }
                    ],
                }
            ]
        ),
        encoding="utf-8",
    )
    return path


def test_load_locomo_eval_cases_expands_raw_dataset(tmp_path: Path) -> None:
    cases = load_locomo_eval_cases(_raw_locomo_file(tmp_path))

    assert len(cases) == 1
    assert cases[0].question_id == "locomo-sample-001::0000"
    assert cases[0].answer_session_ids == ("S1",)
    assert cases[0].question_type == "temporal"


def test_run_locomo_eval_writes_artifacts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import umx.locomo_eval as locomo_eval
    from umx.long_memory_eval import LongMemorySession

    cases_path = _raw_locomo_file(tmp_path)
    out_dir = tmp_path / "artifacts"

    monkeypatch.setattr(
        locomo_eval,
        "retrieve_long_memory_sessions",
        lambda case, *, config, search_limit, rerank_mode="user-first": [  # noqa: ARG005
            LongMemorySession(
                session_id="S1",
                started="7 May 2023",
                turns=tuple(case.haystack_sessions[0].turns),
            )
        ],
    )
    monkeypatch.setattr(
        locomo_eval.benchmark_llm,
        "send_benchmark_message_with_retry",
        lambda provider, *, config, model, system, prompt: locomo_eval.benchmark_llm.BenchmarkMessageResult(  # noqa: ARG005
            text="7 May 2023",
            model="claude-opus-4-7",
            usage={"input_tokens": 5, "output_tokens": 4, "total_tokens": 9},
        ),
    )

    payload = run_locomo_eval(
        out_dir,
        cases_path,
        default_config(),
        provider="claude-cli",
        min_average_f1=0.0,
    )

    assert payload["status"] == "ok"
    assert payload["suite"] == "locomo"
    assert payload["average_f1"] == 1.0
    assert payload["average_evidence_recall"] == 1.0
    assert Path(payload["predictions_path"]).exists()
    assert (out_dir / "summary.json").exists()


def test_run_locomo_eval_abstention_scoring(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import umx.locomo_eval as locomo_eval

    cases_path = _raw_locomo_file(tmp_path, category=5, answer="No information available")
    out_dir = tmp_path / "artifacts"

    monkeypatch.setattr(locomo_eval, "retrieve_long_memory_sessions", lambda case, *, config, search_limit, rerank_mode="user-first": [])  # noqa: ARG005,E731
    monkeypatch.setattr(
        locomo_eval.benchmark_llm,
        "send_benchmark_message_with_retry",
        lambda provider, *, config, model, system, prompt: locomo_eval.benchmark_llm.BenchmarkMessageResult(  # noqa: ARG005
            text="No information available",
            model="claude-opus-4-7",
            usage={},
        ),
    )

    payload = run_locomo_eval(out_dir, cases_path, default_config(), provider="claude-cli", min_average_f1=0.0)

    assert payload["status"] == "ok"
    assert payload["average_f1"] == 1.0


def test_load_locomo_eval_cases_handles_null_abstention_answers(tmp_path: Path) -> None:
    path = tmp_path / "locomo10.json"
    path.write_text(
        json.dumps(
            [
                {
                    "sample_id": "locomo-sample-002",
                    "conversation": {
                        "session_1_date_time": "7 May 2023",
                        "session_1": [
                            {"speaker": "Caroline", "dia_id": "D1:1", "text": "I went to the support group."},
                        ],
                    },
                    "qa": [
                        {
                            "question": "What did Melanie do at the support group?",
                            "answer": None,
                            "adversarial_answer": "Melanie went too",
                            "evidence": ["D1:1"],
                            "category": 5,
                        }
                    ],
                }
            ]
        ),
        encoding="utf-8",
    )

    cases = load_locomo_eval_cases(path)

    assert cases[0].answer == "No information available"


def test_locomo_multi_answer_f1_penalizes_extra_predictions() -> None:
    assert _multi_answer_f1("Sweden, counseling certification, Melbourne", "Sweden, counseling certification") < 1.0


def test_locomo_answer_prompt_includes_question_date() -> None:
    from umx.long_memory_eval import LongMemorySession

    prompt = _answer_user_prompt(
        [
            LongMemorySession(
                session_id="S1",
                started="7 May 2023",
                turns=({"role": "Caroline", "content": "I went to the group."},),
            )
        ],
        question="When did Caroline go to the group?",
        category=2,
        question_date="10 May 2023",
        history_format="nl",
    )

    assert "Current date: 10 May 2023" in prompt
