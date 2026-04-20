from __future__ import annotations

from benchmarks._fixture_gen import clone_prepared_repo
from benchmarks._timing import summarize_duration_ns
from umx.dream.pipeline import DreamPipeline, DreamResult


def test_dream_wall_clock(dream_base_repo, record_benchmark, tmp_path) -> None:
    sample_count = 1
    samples_ns: list[int] = []
    outputs: list[DreamResult] = []

    for sample_index in range(sample_count):
        sample = clone_prepared_repo(
            dream_base_repo,
            tmp_path / f"dream-sample-{sample_index}",
            slug=f"bench-dream-{sample_index}",
        )
        from time import perf_counter_ns

        started = perf_counter_ns()
        result = DreamPipeline(sample.project_dir).run(force=True)
        samples_ns.append(perf_counter_ns() - started)
        outputs.append(result)

    summary = summarize_duration_ns(samples_ns)
    record_benchmark(
        "dream_wall_clock",
        "ms",
        summary,
        {
            "facts": dream_base_repo.scale.fact_count,
            "sessions": dream_base_repo.scale.total_sessions,
            "path": "DreamPipeline.run",
        },
    )

    assert all(result.status == "ok" for result in outputs)
    assert summary["p50_ms"] > 0
