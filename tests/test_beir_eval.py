from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from umx.beir_eval import load_beir_eval_dataset, run_beir_eval
from umx.cli import main
from umx.config import default_config


FIXTURES_ROOT = Path(__file__).parent / "eval" / "beir" / "scifact-mini"


def test_load_beir_eval_dataset_from_fixture_dir() -> None:
    dataset = load_beir_eval_dataset(FIXTURES_ROOT)

    assert dataset.dataset_name == "scifact-mini"
    assert dataset.split == "test"
    assert len(dataset.corpus) == 4
    assert [query.query_id for query in dataset.queries] == ["q1", "q2"]


def test_run_beir_eval_passes_fixture_subset() -> None:
    payload = run_beir_eval(FIXTURES_ROOT, default_config(), min_ndcg_at_10=0.75, top_k=2)

    assert payload["suite"] == "beir"
    assert payload["benchmark"]["name"] == "BEIR"
    assert payload["dataset_name"] == "scifact-mini"
    assert payload["status"] == "ok"
    assert payload["gate_passed"] is True
    assert payload["total_queries"] == 2
    assert payload["completed_queries"] == 2
    assert payload["failed_queries"] == 0
    assert payload["ndcg_at_10"] == 1.0
    assert payload["recall_at_10"] == 1.0
    assert len(payload["results"][0]["top_docs"]) == 2


def test_run_beir_eval_supports_manifest_subset(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "format": "beir-manifest",
                "dataset": "scifact-mini",
                "split": "test",
                "dataset_dir": str(FIXTURES_ROOT),
                "query_ids": ["q2"],
            }
        )
    )

    payload = run_beir_eval(manifest_path, default_config(), min_ndcg_at_10=1.0)

    assert payload["status"] == "ok"
    assert payload["total_queries"] == 1
    assert payload["query_filter"] is None
    assert payload["results"][0]["query_id"] == "q2"


def test_load_beir_eval_dataset_rejects_duplicate_manifest_query_ids(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "format": "beir-manifest",
                "dataset_dir": str(FIXTURES_ROOT),
                "query_ids": ["q1", "q1"],
            }
        )
    )

    with pytest.raises(RuntimeError, match="duplicate query_ids"):
        load_beir_eval_dataset(manifest_path)


def test_run_beir_eval_rejects_unknown_query_id(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "format": "beir-manifest",
                "dataset_dir": str(FIXTURES_ROOT),
                "query_ids": ["missing"],
            }
        )
    )

    with pytest.raises(RuntimeError, match="unknown query_id `missing`"):
        load_beir_eval_dataset(manifest_path)


def test_run_beir_eval_surfaces_query_runtime_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import umx.beir_eval as beir_eval

    def _explode(*args: object, **kwargs: object) -> dict[str, object]:
        raise ValueError("boom")

    monkeypatch.setattr(beir_eval, "_run_query", _explode)

    payload = beir_eval.run_beir_eval(FIXTURES_ROOT, default_config(), min_ndcg_at_10=0.0)

    assert payload["status"] == "error"
    assert payload["failed_queries"] == payload["total_queries"]
    assert payload["failures"][0]["error"] == "boom"


def test_cli_beir_eval_supports_query_filter() -> None:
    result = CliRunner().invoke(
        main,
        [
            "eval",
            "beir",
            "--cases",
            str(FIXTURES_ROOT),
            "--query-id",
            "q1",
            "--min-ndcg-at-10",
            "0.5",
            "--top-k",
            "2",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["suite"] == "beir"
    assert payload["query_filter"] == "q1"
    assert payload["total_queries"] == 1
