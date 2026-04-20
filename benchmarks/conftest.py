from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from benchmarks._fixture_gen import PreparedRepo, RepoScale, build_prepared_repo, dream_scale, inject_scale
from umx.config import default_config, save_config
from umx.scope import config_path, init_local_umx


def pytest_configure(config: pytest.Config) -> None:
    config._umx_benchmark_records = []


@pytest.fixture(scope="session")
def benchmark_workspace(tmp_path_factory: pytest.TempPathFactory) -> Path:
    monkeypatch = pytest.MonkeyPatch()
    root = tmp_path_factory.mktemp("umx-benchmarks")
    umx_home = root / "umxhome"
    monkeypatch.setenv("UMX_HOME", str(umx_home))
    monkeypatch.setenv("GIT_AUTHOR_NAME", "UMX Bench")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "bench@example.com")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "UMX Bench")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "bench@example.com")
    init_local_umx()
    cfg = default_config()
    cfg.search.backend = "fts5"
    cfg.dream.mode = "local"
    cfg.dream.provider_rotation = []
    cfg.dream.paid_provider = None
    cfg.dream.local_model = None
    cfg.dream.lint_interval = "never"
    save_config(config_path(), cfg)
    try:
        yield root
    finally:
        monkeypatch.undo()


@pytest.fixture(scope="session")
def inject_base_repo(
    benchmark_workspace: Path,
    tmp_path_factory: pytest.TempPathFactory,
) -> PreparedRepo:
    root = tmp_path_factory.mktemp("inject-bench")
    return build_prepared_repo(root, slug="bench-inject-base", scale=inject_scale())


@pytest.fixture(scope="session")
def dream_base_repo(
    benchmark_workspace: Path,
    tmp_path_factory: pytest.TempPathFactory,
) -> PreparedRepo:
    root = tmp_path_factory.mktemp("dream-bench")
    return build_prepared_repo(root, slug="bench-dream-base", scale=dream_scale())


@pytest.fixture(scope="session")
def ingest_base_repo(
    benchmark_workspace: Path,
    tmp_path_factory: pytest.TempPathFactory,
) -> PreparedRepo:
    root = tmp_path_factory.mktemp("ingest-bench")
    return build_prepared_repo(root, slug="bench-ingest-base", scale=RepoScale(fact_count=0))


@pytest.fixture
def record_benchmark(request: pytest.FixtureRequest):
    records: list[dict[str, Any]] = request.config._umx_benchmark_records

    def _record(name: str, unit: str, summary: dict[str, Any], meta: dict[str, Any]) -> None:
        records.append(
            {
                "name": name,
                "unit": unit,
                "summary": summary,
                "meta": meta,
            }
        )

    return _record


def _meta_text(meta: dict[str, Any]) -> str:
    parts = [f"{key}={value}" for key, value in sorted(meta.items())]
    return " ".join(parts)


def pytest_terminal_summary(
    terminalreporter: pytest.TerminalReporter,
    exitstatus: int,
    config: pytest.Config,
) -> None:
    records: list[dict[str, Any]] = getattr(config, "_umx_benchmark_records", [])
    if not records:
        return
    terminalreporter.section("benchmarks")
    for record in records:
        name = str(record["name"])
        unit = str(record["unit"])
        summary = dict(record["summary"])
        meta = dict(record["meta"])
        if unit == "ms":
            terminalreporter.write_line(
                f"{name}: p50={summary['p50_ms']}ms p95={summary['p95_ms']}ms "
                f"samples={summary['samples']} {_meta_text(meta)}"
            )
        elif unit == "sessions/s":
            terminalreporter.write_line(
                f"{name}: p50={summary['p50_per_second']} sessions/s "
                f"p95={summary['p95_per_second']} sessions/s samples={summary['samples']} "
                f"{_meta_text(meta)}"
            )
        elif unit == "s":
            terminalreporter.write_line(
                f"{name}: median={summary['p50_ms']}ms max={summary['max_ms']}ms "
                f"samples={summary['samples']} {_meta_text(meta)}"
            )
        else:
            terminalreporter.write_line(f"{name}: {json.dumps(summary, sort_keys=True)} {_meta_text(meta)}")
    terminalreporter.write_line("benchmarks-json: " + json.dumps(records, sort_keys=True))
