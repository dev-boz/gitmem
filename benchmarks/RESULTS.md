# Benchmark Results

## Environment

- command: `pytest benchmarks -q`
- python: `3.11+`
- search backend: `fts5`
- dream mode: `local`
- lint interval: `never`
- provider rotation: disabled for deterministic native extraction

## Baseline

| benchmark | scale | samples | p50 | p95 | unit | notes |
| --- | --- | --- | --- | --- | --- | --- |
| ingest_throughput | 50 sessions/batch | 3 | 23.694 | 24.42 | sessions/s | `collect_session` |
| inject_latency | 10k facts | 9 | 105.286 | 142.366 | ms | `build_injection_block` |
| dream_wall_clock | 10k facts / 100k sessions | 1 | 75458.657 | 75458.657 | ms | `DreamPipeline.run(force=True)` |

Measured from a local `pytest benchmarks -q` run in this session.
