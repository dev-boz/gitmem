from __future__ import annotations

import json
from pathlib import Path

import pytest

from umx.config import default_config
from umx.longmemeval_eval import normalize_longmemeval_provider, run_longmemeval_eval


def _case_file(tmp_path: Path, *, question_type: str = "single-session-user", answer: object = "Business Administration") -> Path:
    path = tmp_path / "cases.json"
    path.write_text(
        json.dumps(
            [
                {
                    "question_id": "longmemeval-case-001" if question_type != "abstention" else "longmemeval-case-001_abs",
                    "question_type": question_type,
                    "question": "What degree did I graduate with?" if question_type != "abstention" else "What is my passport number?",
                    "question_date": "2023/05/30 (Tue) 23:40",
                    "answer": answer,
                    "answer_session_ids": [] if question_type == "abstention" else ["answer_280352e9"],
                    "haystack_session_ids": ["answer_280352e9"],
                    "haystack_dates": ["2023/05/30 (Tue) 21:40"],
                    "haystack_sessions": [
                        [
                            {
                                "role": "user",
                                "content": "I graduated with a degree in Business Administration."
                                if question_type != "abstention"
                                else "I need to renew my gym membership.",
                                "has_answer": question_type != "abstention",
                            }
                        ]
                    ],
                }
            ]
        ),
        encoding="utf-8",
    )
    return path


def test_normalize_longmemeval_provider_accepts_known_aliases() -> None:
    assert normalize_longmemeval_provider(None) == "claude-cli"
    assert normalize_longmemeval_provider("claude-code") == "claude-cli"
    with pytest.raises(RuntimeError, match="claude-cli"):
        normalize_longmemeval_provider("anthropic-api")


def test_run_longmemeval_eval_writes_artifacts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import umx.longmemeval_eval as longmemeval_eval
    from umx.long_memory_eval import LongMemorySession

    cases_path = _case_file(tmp_path)
    out_dir = tmp_path / "artifacts"

    monkeypatch.setattr(
        longmemeval_eval,
        "retrieve_long_memory_sessions",
        lambda case, *, config, search_limit, rerank_mode="user-first": [  # noqa: ARG005
            LongMemorySession(
                session_id="answer_280352e9",
                started="2023/05/30 (Tue) 21:40",
                turns=tuple(case.haystack_sessions[0].turns),
            )
        ],
    )

    responses = iter(
        [
            longmemeval_eval.LongMemEvalMessageResult(
                text="Business Administration",
                model="claude-opus-4-7",
                usage={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
            ),
            longmemeval_eval.LongMemEvalMessageResult(
                text="yes",
                model="claude-opus-4-7",
                usage={"input_tokens": 4, "output_tokens": 1, "total_tokens": 5},
            ),
        ]
    )
    monkeypatch.setattr(
        longmemeval_eval,
        "_send_message_with_provider",
        lambda provider, *, config, model, system, prompt: next(responses),  # noqa: ARG005
    )

    payload = run_longmemeval_eval(
        out_dir,
        cases_path,
        default_config(),
        provider="claude-cli",
        judge_provider="claude-cli",
    )

    assert payload["status"] == "ok"
    assert payload["suite"] == "longmemeval"
    assert payload["pass_rate"] == 1.0
    assert Path(payload["hypotheses_path"]).exists()
    assert Path(payload["judgments_path"]).exists()
    assert (out_dir / "summary.json").exists()
    assert payload["results"][0]["actual_answer"] == "Business Administration"
    assert payload["results"][0]["retrieval_recall"] == 1.0
    assert payload["generation_usage_totals"]["total_tokens"] == 15
    assert payload["judge_usage_totals"]["total_tokens"] == 5


def test_run_longmemeval_eval_handles_abstention(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import umx.longmemeval_eval as longmemeval_eval

    cases_path = _case_file(tmp_path, question_type="abstention", answer="No passport number is mentioned.")
    out_dir = tmp_path / "artifacts"

    monkeypatch.setattr(
        longmemeval_eval,
        "retrieve_long_memory_sessions",
        lambda case, *, config, search_limit, rerank_mode="user-first": [],  # noqa: ARG005
    )
    responses = iter(
        [
            longmemeval_eval.LongMemEvalMessageResult(
                text="The provided history does not mention a passport number.",
                model="claude-opus-4-7",
                usage={},
            ),
            longmemeval_eval.LongMemEvalMessageResult(
                text="yes",
                model="claude-opus-4-7",
                usage={},
            ),
        ]
    )
    monkeypatch.setattr(
        longmemeval_eval,
        "_send_message_with_provider",
        lambda provider, *, config, model, system, prompt: next(responses),  # noqa: ARG005
    )

    payload = run_longmemeval_eval(
        out_dir,
        cases_path,
        default_config(),
        provider="claude-cli",
    )

    assert payload["status"] == "ok"
    assert payload["abstention_accuracy"] == 1.0
    assert payload["results"][0]["passed"] is True


def test_run_longmemeval_eval_marks_case_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import umx.longmemeval_eval as longmemeval_eval
    from umx.long_memory_eval import LongMemorySession

    cases_path = _case_file(tmp_path)
    out_dir = tmp_path / "artifacts"

    monkeypatch.setattr(
        longmemeval_eval,
        "retrieve_long_memory_sessions",
        lambda case, *, config, search_limit, rerank_mode="user-first": [  # noqa: ARG005
            LongMemorySession(
                session_id="answer_280352e9",
                started="2023/05/30 (Tue) 21:40",
                turns=tuple(case.haystack_sessions[0].turns),
            )
        ],
    )
    responses = iter(
        [
            longmemeval_eval.LongMemEvalMessageResult(
                text="Business Administration",
                model="claude-opus-4-7",
                usage={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
            )
        ]
    )

    def _send(provider, *, config, model, system, prompt):  # noqa: ARG001
        try:
            return next(responses)
        except StopIteration as exc:
            raise RuntimeError("boom") from exc

    monkeypatch.setattr(longmemeval_eval, "_send_message_with_provider", _send)

    payload = run_longmemeval_eval(
        out_dir,
        cases_path,
        default_config(),
        provider="claude-cli",
        min_pass_rate=0.0,
    )

    assert payload["status"] == "error"
    assert payload["results"][0]["error"] == "boom"
    assert payload["completed"] == 0
    assert payload["results"][0]["actual_answer"] == "Business Administration"
    assert len((out_dir / "hypotheses.jsonl").read_text().strip().splitlines()) == 1
    assert len((out_dir / "judgments.jsonl").read_text().strip().splitlines()) == 1


def test_run_longmemeval_eval_retries_transient_claude_cli_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import umx.longmemeval_eval as longmemeval_eval
    from umx.long_memory_eval import LongMemorySession

    cases_path = _case_file(tmp_path)
    out_dir = tmp_path / "artifacts"

    monkeypatch.setattr(
        longmemeval_eval,
        "retrieve_long_memory_sessions",
        lambda case, *, config, search_limit, rerank_mode="user-first": [  # noqa: ARG005
            LongMemorySession(
                session_id="answer_280352e9",
                started="2023/05/30 (Tue) 21:40",
                turns=tuple(case.haystack_sessions[0].turns),
            )
        ],
    )
    monkeypatch.setattr(longmemeval_eval.time, "sleep", lambda *_args: None)

    responses = iter(
        [
            RuntimeError("Claude CLI failed: exit code 1"),
            longmemeval_eval.LongMemEvalMessageResult(
                text="Business Administration",
                model="claude-opus-4-7",
                usage={},
            ),
            longmemeval_eval.LongMemEvalMessageResult(
                text="yes",
                model="claude-opus-4-7",
                usage={},
            ),
        ]
    )

    def _send(provider, *, config, model, system, prompt):  # noqa: ARG001
        value = next(responses)
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(longmemeval_eval, "_send_message_with_provider", _send)

    payload = run_longmemeval_eval(
        out_dir,
        cases_path,
        default_config(),
        provider="claude-cli",
    )

    assert payload["status"] == "ok"
    assert payload["pass_rate"] == 1.0
