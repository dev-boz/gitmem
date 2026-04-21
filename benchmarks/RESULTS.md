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
| ingest_throughput | 50 sessions/batch | 3 | 22.263 | 23.17 | sessions/s | `collect_session` |
| inject_latency | 10k facts | 9 | 114.019 | 122.264 | ms | `build_injection_block` |
| rebuild_full_latency | 10k facts / 1-file edit | 3 | 5698.476 | 5809.321 | ms | `rebuild_index` baseline |
| refresh_index_latency | 10k facts / 1-file edit | 3 | 508.483 | 517.024 | ms | `refresh_index`; 11.207x faster than full rebuild |
| dream_wall_clock | 10k facts / 100k sessions | 1 | 81302.942 | 81302.942 | ms | `DreamPipeline.run(force=True)` |

Measured from a local `pytest benchmarks -q` run in this session.

## T4.2 follow-up

- `index_size_growth` (`meta/index.sqlite`, scales `1000,2500,5000,10000`): 10x fact growth produced 9.181x index growth with a log-log slope of `0.963`; bytes per fact fell from `655.36` at 1k facts to `601.702` at 10k facts.
- `refresh_index_latency` closed the rebuild-speed acceptance item against the previous full rebuild path: p50 speedup was `11.207x` on a 10k-fact repo after a single-file edit.

## T4.4 follow-up

- Narrow inject retune slice: `inject.pre_tool_max_tokens` now defaults to `1400` and `inject.disclosure_slack_pct` to `0.20`.
- Injection golden-corpus coverage now proves the retuned disclosure threshold preserves the same top-N fact ordering as the previous `0.30` slack on representative cases.
- Final standalone sanity rerun of `python3 -m pytest benchmarks/test_inject.py -q`: `111.201 / 127.133 ms` p50/p95 at 10k facts.
