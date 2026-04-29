from __future__ import annotations

import json
from pathlib import Path

import pytest

from umx.config import default_config
from umx.convomem_eval import load_convomem_eval_cases, run_convomem_eval


def _raw_convomem_file(tmp_path: Path, *, evidence_type: str = "user_facts_evidence", answer: str = "3") -> Path:
    evidence_dir = tmp_path / evidence_type / "1_evidence"
    evidence_dir.mkdir(parents=True)
    path = evidence_dir / "sample_persona.json"
    path.write_text(
        json.dumps(
            {
                "evidence_items": [
                    {
                        "question": "How many children do I have?",
                        "answer": answer,
                        "message_evidences": [
                            {"speaker": "User", "text": "I have 3 kids named Emma, Josh, and Lily."}
                        ],
                        "conversations": [
                            {
                                "messages": [
                                    {"speaker": "Assistant", "text": "Good morning."},
                                    {"speaker": "User", "text": "I have 3 kids named Emma, Josh, and Lily."},
                                ]
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return path


def test_load_convomem_eval_cases_from_raw_file(tmp_path: Path) -> None:
    cases = load_convomem_eval_cases(_raw_convomem_file(tmp_path))

    assert len(cases) == 1
    assert cases[0].evidence_type == "user_facts"
    assert cases[0].answer_session_ids == ("C1",)


def test_run_convomem_eval_writes_artifacts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import umx.convomem_eval as convomem_eval
    from umx.long_memory_eval import LongMemorySession

    cases_path = _raw_convomem_file(tmp_path)
    out_dir = tmp_path / "artifacts"

    monkeypatch.setattr(
        convomem_eval,
        "retrieve_long_memory_sessions",
        lambda case, *, config, search_limit, rerank_mode="user-first": [  # noqa: ARG005
            LongMemorySession(
                session_id="C1",
                started=None,
                turns=tuple(case.haystack_sessions[0].turns),
            )
        ],
    )
    responses = iter(
        [
            convomem_eval.benchmark_llm.BenchmarkMessageResult(
                text="3",
                model="claude-opus-4-7",
                usage={"input_tokens": 10, "output_tokens": 4, "total_tokens": 14},
            ),
            convomem_eval.benchmark_llm.BenchmarkMessageResult(
                text="RIGHT",
                model="claude-opus-4-7",
                usage={"input_tokens": 5, "output_tokens": 1, "total_tokens": 6},
            ),
        ]
    )
    monkeypatch.setattr(
        convomem_eval.benchmark_llm,
        "send_benchmark_message_with_retry",
        lambda provider, *, config, model, system, prompt: next(responses),  # noqa: ARG005
    )

    payload = run_convomem_eval(
        out_dir,
        cases_path,
        default_config(),
        provider="claude-cli",
        judge_provider="claude-cli",
        min_pass_rate=0.0,
    )

    assert payload["status"] == "ok"
    assert payload["suite"] == "convomem"
    assert payload["pass_rate"] == 1.0
    assert payload["average_retrieval_recall"] == 1.0
    assert Path(payload["predictions_path"]).exists()
    assert Path(payload["judgments_path"]).exists()


def test_run_convomem_eval_handles_abstention(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import umx.convomem_eval as convomem_eval

    cases_path = _raw_convomem_file(
        tmp_path,
        evidence_type="abstention_evidence",
        answer="There is no information in prior conversations to answer this question",
    )
    out_dir = tmp_path / "artifacts"

    monkeypatch.setattr(convomem_eval, "retrieve_long_memory_sessions", lambda case, *, config, search_limit, rerank_mode="user-first": [])  # noqa: ARG005,E731
    responses = iter(
        [
            convomem_eval.benchmark_llm.BenchmarkMessageResult(text="I don't know.", model="claude-opus-4-7", usage={}),
            convomem_eval.benchmark_llm.BenchmarkMessageResult(text="RIGHT", model="claude-opus-4-7", usage={}),
        ]
    )
    monkeypatch.setattr(
        convomem_eval.benchmark_llm,
        "send_benchmark_message_with_retry",
        lambda provider, *, config, model, system, prompt: next(responses),  # noqa: ARG005
    )

    payload = run_convomem_eval(out_dir, cases_path, default_config(), provider="claude-cli", min_pass_rate=0.0)

    assert payload["status"] == "ok"
    assert payload["pass_rate"] == 1.0


def test_load_convomem_eval_cases_matches_evidence_by_speaker_and_text(tmp_path: Path) -> None:
    path = _raw_convomem_file(tmp_path)
    payload = json.loads(path.read_text())
    payload["evidence_items"][0]["conversations"].append(
        {
            "messages": [
                {"speaker": "Assistant", "text": "I have 3 kids named Emma, Josh, and Lily."},
            ]
        }
    )
    path.write_text(json.dumps(payload), encoding="utf-8")

    cases = load_convomem_eval_cases(path)

    assert cases[0].answer_session_ids == ("C1",)


def test_load_convomem_eval_cases_matches_evidence_substring(tmp_path: Path) -> None:
    path = _raw_convomem_file(tmp_path)
    payload = json.loads(path.read_text())
    payload["evidence_items"][0]["message_evidences"][0]["text"] = "I have 3 kids named Emma, Josh, and Lily."
    payload["evidence_items"][0]["conversations"][0]["messages"][1]["text"] = (
        "Actually, I have 3 kids named Emma, Josh, and Lily."
    )
    path.write_text(json.dumps(payload), encoding="utf-8")

    cases = load_convomem_eval_cases(path)

    assert cases[0].answer_session_ids == ("C1",)


def test_load_convomem_eval_cases_rejects_ambiguous_raw_file_path(tmp_path: Path) -> None:
    path = tmp_path / "sample.json"
    path.write_text(
        json.dumps(
            {
                "evidence_items": [
                    {
                        "question": "How many children do I have?",
                        "answer": "3",
                        "message_evidences": [{"speaker": "User", "text": "I have 3 kids."}],
                        "conversations": [{"messages": [{"speaker": "User", "text": "I have 3 kids."}]}],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="could not infer a supported ConvoMem evidence type"):
        load_convomem_eval_cases(path)
