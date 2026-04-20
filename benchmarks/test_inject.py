from __future__ import annotations

import os
from itertools import cycle

from benchmarks._timing import run_samples, summarize_duration_ns
from umx.inject import build_injection_block

_PROMPTS = (
    "deploy staging smoke test rollback postgres",
    "feature flags rollout notes and incident handling",
    "search index maintenance for benchmark memory topics",
)


def test_inject_latency(inject_base_repo, record_benchmark) -> None:
    prompts = cycle(_PROMPTS)
    sample_count = max(5, int(os.environ.get("UMX_BENCH_INJECT_SAMPLES", "9")))

    def _run_once() -> str:
        return build_injection_block(
            inject_base_repo.project_dir,
            tool="copilot",
            prompt=next(prompts),
            max_tokens=1400,
        )

    samples_ns, outputs = run_samples(_run_once, warmups=2, samples=sample_count)
    summary = summarize_duration_ns(samples_ns)
    record_benchmark(
        "inject_latency",
        "ms",
        summary,
        {
            "facts": inject_base_repo.scale.fact_count,
            "sessions": inject_base_repo.scale.total_sessions,
            "path": "build_injection_block",
        },
    )

    assert all(block.strip() for block in outputs)
    assert summary["p50_ms"] > 0
    assert summary["p95_ms"] >= summary["p50_ms"]
