from __future__ import annotations

import os

from benchmarks._fixture_gen import clone_prepared_repo, ingest_batch_count
from benchmarks._timing import summarize_throughput
from umx.collect import collect_session


def test_ingest_throughput(ingest_base_repo, record_benchmark, tmp_path) -> None:
    batch_count = ingest_batch_count()
    sample_count = max(1, int(os.environ.get("UMX_BENCH_INGEST_SAMPLES", "3")))
    samples_ns: list[int] = []

    for sample_index in range(sample_count):
        sample = clone_prepared_repo(
            ingest_base_repo,
            tmp_path / f"ingest-sample-{sample_index}",
            slug=f"bench-ingest-{sample_index}",
        )
        from time import perf_counter_ns

        started = perf_counter_ns()
        for item_index in range(batch_count):
            session_id = f"2026-04-18-bench-ingest-{sample_index:02d}-{item_index:04d}"
            collect_session(
                sample.project_dir,
                (
                    "Deploys run through staging before production. "
                    f"Benchmark ingest sample {sample_index} event {item_index}."
                ),
                tool="benchmark",
                session_id=session_id,
            )
        samples_ns.append(perf_counter_ns() - started)

    summary = summarize_throughput(samples_ns, operations=batch_count)
    record_benchmark(
        "ingest_throughput",
        "sessions/s",
        summary,
        {
            "batch": batch_count,
            "facts": ingest_base_repo.scale.fact_count,
            "sessions": batch_count,
            "path": "collect_session",
        },
    )

    assert summary["p50_per_second"] > 0
    assert summary["p95_per_second"] >= summary["p50_per_second"]
