from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from umx.cli import main
from umx.config import default_config
from umx.dream.l2_eval import REQUIRED_EVAL_BUCKETS, load_l2_eval_cases, run_l2_review_eval


FIXTURES_ROOT = Path(__file__).parent / "eval" / "l2_reviewer"


def test_l2_eval_cases_cover_required_buckets_and_count() -> None:
    cases = load_l2_eval_cases(FIXTURES_ROOT)

    assert len(cases) >= 20
    assert {case.bucket for case in cases} >= REQUIRED_EVAL_BUCKETS


def test_run_l2_review_eval_computes_pass_rate(tmp_path: Path) -> None:
    cases_path = tmp_path / "cases.json"
    cases_path.write_text(
        json.dumps(
            [
                {
                    "id": "approve-case",
                    "bucket": "clean_extraction",
                    "description": "approve case",
                    "expected_action": "approve",
                    "labels": ["confidence:high", "impact:local", "type: extraction"],
                    "files_changed": ["facts/topics/general.md"],
                    "conventions": {"topics": ["general"]},
                    "new_facts": [{"fact_id": "01TESTEVAL0001", "text": "service docs live in docs/service", "topic": "general"}],
                },
                {
                    "id": "reject-case",
                    "bucket": "taxonomy_miss",
                    "description": "reject case",
                    "expected_action": "reject",
                    "labels": ["confidence:low", "impact:local", "type: extraction"],
                    "files_changed": ["facts/topics/unknown.md"],
                    "conventions": {"topics": ["general"]},
                    "new_facts": [{"fact_id": "01TESTEVAL0002", "text": "billing retries after ten minutes", "topic": "billing"}],
                },
            ]
        )
    )

    def reviewer(pr, *_args, **_kwargs):
        if pr.title.endswith("approve-case"):
            return {
                "action": "approve",
                "reason": "ok",
                "model": "claude-opus-4-7",
                "prompt_id": "prompt",
                "prompt_version": "v1",
            }
        return {
            "action": "escalate",
            "reason": "wrong",
            "model": "claude-opus-4-7",
            "prompt_id": "prompt",
            "prompt_version": "v1",
        }

    payload = run_l2_review_eval(
        cases_path,
        default_config(),
        min_pass_rate=0.75,
        reviewer=reviewer,
    )

    assert payload["status"] == "error"
    assert payload["total"] == 2
    assert payload["passed"] == 1
    assert payload["pass_rate"] == 0.5
    assert payload["failures"][0]["case"] == "reject-case"


def test_run_l2_review_eval_fails_on_mixed_prompt_metadata(tmp_path: Path) -> None:
    cases_path = tmp_path / "cases.json"
    cases_path.write_text(
        json.dumps(
            [
                {
                    "id": "case-one",
                    "bucket": "clean_extraction",
                    "description": "first case",
                    "expected_action": "approve",
                    "labels": ["confidence:high", "impact:local", "type: extraction"],
                    "files_changed": ["facts/topics/general.md"],
                    "conventions": {"topics": ["general"]},
                    "new_facts": [{"fact_id": "01TESTEVAL1001", "text": "service docs live in docs/service", "topic": "general"}],
                },
                {
                    "id": "case-two",
                    "bucket": "conflicting_fact",
                    "description": "second case",
                    "expected_action": "approve",
                    "labels": ["confidence:high", "impact:local", "type: extraction"],
                    "files_changed": ["facts/topics/general.md"],
                    "conventions": {"topics": ["general"]},
                    "new_facts": [{"fact_id": "01TESTEVAL1002", "text": "service docs live in docs/service", "topic": "general"}],
                },
            ]
        )
    )

    def reviewer(pr, *_args, **_kwargs):
        if pr.title.endswith("case-one"):
            return {
                "action": "approve",
                "reason": "ok",
                "model": "claude-opus-4-7",
                "prompt_id": "prompt-a",
                "prompt_version": "v1",
            }
        return {
            "action": "approve",
            "reason": "ok",
            "model": "claude-opus-4-7",
            "prompt_id": "prompt-b",
            "prompt_version": "v1",
        }

    payload = run_l2_review_eval(
        cases_path,
        default_config(),
        min_pass_rate=0.5,
        reviewer=reviewer,
    )

    assert payload["status"] == "error"
    assert payload["prompt_id"] is None
    assert "multiple prompt ids" in payload["metadata_errors"][0]


def test_cli_eval_l2_review_exits_nonzero_on_gate_failure(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "umx.dream.l2_eval.run_l2_review_eval",
        lambda *args, **kwargs: {
            "status": "error",
            "prompt_id": "anthropic-l2-review",
            "prompt_version": "v1",
            "model": "claude-opus-4-7",
            "total": 20,
            "passed": 10,
            "pass_rate": 0.5,
            "min_pass_rate": 0.85,
            "meets_case_count": True,
            "required_case_count": 20,
            "missing_buckets": [],
            "bucket_summary": {},
            "usage_totals": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
            "failures": [{"case": "broken-case"}],
            "results": [],
        },
    )

    result = CliRunner().invoke(main, ["eval", "l2-review", "--cases", str(tmp_path)])

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["status"] == "error"
