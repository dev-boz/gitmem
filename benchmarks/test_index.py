from __future__ import annotations

import math
import os
from time import perf_counter_ns

from benchmarks._fixture_gen import RepoScale, build_prepared_repo, clone_prepared_repo
from benchmarks._timing import summarize_duration_ns
from umx.memory import iter_fact_files
from umx.search import index_path, rebuild_index, refresh_index

_INDEX_GROWTH_SCALES = (1_000, 2_500, 5_000, 10_000)


def _mutate_fact_file(repo_dir) -> str:
    path = iter_fact_files(repo_dir)[0]
    original = path.read_text(encoding="utf-8")
    mutated = original.replace("Benchmark fact ", "Benchmark fact refreshed ", 1)
    if mutated == original:
        raise AssertionError("benchmark fact marker not found")
    path.write_text(mutated, encoding="utf-8")
    return str(path.relative_to(repo_dir))


def test_rebuild_refresh_latency(inject_base_repo, record_benchmark, tmp_path) -> None:
    sample_count = max(1, int(os.environ.get("UMX_BENCH_REBUILD_SAMPLES", "3")))
    baseline_samples_ns: list[int] = []
    refresh_samples_ns: list[int] = []
    changed_counts: list[int] = []
    changed_paths: list[str] = []

    for sample_index in range(sample_count):
        baseline = clone_prepared_repo(
            inject_base_repo,
            tmp_path / f"rebuild-full-{sample_index}",
            slug=f"bench-rebuild-full-{sample_index}",
        )
        refresh = clone_prepared_repo(
            inject_base_repo,
            tmp_path / f"rebuild-refresh-{sample_index}",
            slug=f"bench-rebuild-refresh-{sample_index}",
        )
        changed_paths.append(_mutate_fact_file(baseline.project_repo))
        _mutate_fact_file(refresh.project_repo)

        started = perf_counter_ns()
        rebuild_index(baseline.project_repo)
        baseline_samples_ns.append(perf_counter_ns() - started)

        started = perf_counter_ns()
        changed_counts.append(refresh_index(refresh.project_repo))
        refresh_samples_ns.append(perf_counter_ns() - started)

    baseline_summary = summarize_duration_ns(baseline_samples_ns)
    refresh_summary = summarize_duration_ns(refresh_samples_ns)
    speedup = round(
        float(baseline_summary["p50_ms"]) / max(float(refresh_summary["p50_ms"]), 0.001),
        3,
    )

    record_benchmark(
        "rebuild_full_latency",
        "ms",
        baseline_summary,
        {
            "facts": inject_base_repo.scale.fact_count,
            "delta": "1-file-edit",
            "path": "rebuild_index",
        },
    )
    record_benchmark(
        "refresh_index_latency",
        "ms",
        refresh_summary,
        {
            "facts": inject_base_repo.scale.fact_count,
            "delta": "1-file-edit",
            "path": "refresh_index",
            "speedup_x": speedup,
        },
    )

    assert all(count == 1 for count in changed_counts)
    assert speedup >= 10


def test_index_size_growth(benchmark_workspace, record_benchmark, tmp_path) -> None:
    points: list[tuple[int, int]] = []
    for fact_count in _INDEX_GROWTH_SCALES:
        prepared = build_prepared_repo(
            tmp_path / f"index-size-{fact_count}",
            slug=f"bench-index-size-{fact_count}",
            scale=RepoScale(fact_count=fact_count),
        )
        points.append((fact_count, index_path(prepared.project_repo).stat().st_size))

    first_facts, first_size = points[0]
    last_facts, last_size = points[-1]
    slope = round(
        (math.log(last_size) - math.log(first_size)) / (math.log(last_facts) - math.log(first_facts)),
        3,
    )
    bytes_per_fact = {
        str(fact_count): round(size_bytes / fact_count, 3)
        for fact_count, size_bytes in points
    }
    summary = {
        "samples": len(points),
        "min_bytes": min(size_bytes for _, size_bytes in points),
        "max_bytes": max(size_bytes for _, size_bytes in points),
        "slope": slope,
        "size_ratio": round(last_size / first_size, 3),
        "facts_ratio": round(last_facts / first_facts, 3),
        "bytes_per_fact": bytes_per_fact,
    }

    record_benchmark(
        "index_size_growth",
        "bytes",
        summary,
        {
            "path": "meta/index.sqlite",
            "scales": ",".join(str(scale) for scale in _INDEX_GROWTH_SCALES),
        },
    )

    assert slope < 1.0
    assert (last_size / first_size) < (last_facts / first_facts)
    assert bytes_per_fact[str(last_facts)] < bytes_per_fact[str(first_facts)]
