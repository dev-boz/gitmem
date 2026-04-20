from __future__ import annotations

import math
from statistics import mean
from time import perf_counter_ns
from typing import Callable, TypeVar

T = TypeVar("T")


def run_samples(
    fn: Callable[[], T],
    *,
    warmups: int = 0,
    samples: int = 1,
) -> tuple[list[int], list[T]]:
    for _ in range(max(0, warmups)):
        fn()
    durations: list[int] = []
    outputs: list[T] = []
    for _ in range(max(1, samples)):
        started = perf_counter_ns()
        outputs.append(fn())
        durations.append(perf_counter_ns() - started)
    return durations, outputs


def percentile_ms(samples_ns: list[int], percentile: float) -> float:
    if not samples_ns:
        return 0.0
    ordered = sorted(samples_ns)
    rank = max(0, min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1))
    return ordered[rank] / 1_000_000


def summarize_duration_ns(samples_ns: list[int]) -> dict[str, float | int]:
    if not samples_ns:
        return {
            "samples": 0,
            "min_ms": 0.0,
            "p50_ms": 0.0,
            "p95_ms": 0.0,
            "max_ms": 0.0,
            "mean_ms": 0.0,
        }
    ordered = sorted(samples_ns)
    values_ms = [value / 1_000_000 for value in ordered]
    return {
        "samples": len(ordered),
        "min_ms": round(values_ms[0], 3),
        "p50_ms": round(percentile_ms(ordered, 0.50), 3),
        "p95_ms": round(percentile_ms(ordered, 0.95), 3),
        "max_ms": round(values_ms[-1], 3),
        "mean_ms": round(mean(values_ms), 3),
    }


def summarize_throughput(samples_ns: list[int], *, operations: int) -> dict[str, float | int]:
    if not samples_ns:
        return {
            "samples": 0,
            "min_per_second": 0.0,
            "p50_per_second": 0.0,
            "p95_per_second": 0.0,
            "max_per_second": 0.0,
            "mean_per_second": 0.0,
        }
    rates = sorted((operations / (value / 1_000_000_000)) for value in samples_ns if value > 0)
    return {
        "samples": len(rates),
        "min_per_second": round(rates[0], 3),
        "p50_per_second": round(rates[max(0, min(len(rates) - 1, math.ceil(0.50 * len(rates)) - 1))], 3),
        "p95_per_second": round(rates[max(0, min(len(rates) - 1, math.ceil(0.95 * len(rates)) - 1))], 3),
        "max_per_second": round(rates[-1], 3),
        "mean_per_second": round(mean(rates), 3),
    }
