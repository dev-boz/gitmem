# gitmem configuration reference

This is a focused reference for the config knobs that most directly affect capture, injection, and scheduled maintenance. All values live in `~/.umx/config.yaml`.

## Injection

```yaml
inject:
  pre_tool_max_tokens: 1400
  disclosure_slack_pct: 0.20
  subagent_max_tokens: 2000
  subagent_hot_tokens: 1500
  turn_token_estimate: 250
```

- `inject.pre_tool_max_tokens`: default token budget for pre-tool hook injection.
- `inject.disclosure_slack_pct`: fraction of the fact budget reserved before stable `L1` facts are downgraded to `L0`.
- `inject.subagent_max_tokens`: default relay budget for subagent handoff memory.
- `inject.subagent_hot_tokens`: maximum token budget reserved for hot-summary excerpts in subagent handoffs.
- `inject.turn_token_estimate`: fallback turn-size estimate when a tool does not report native token usage.

Manual surfaces such as `umx inject --max-tokens ...` and shim commands still honor their explicit `--max-tokens` flags.

## Sessions

```yaml
sessions:
  archive_interval: daily
  retention:
    active_days: 90
```

- `sessions.archive_interval`: cadence for automatic archive compaction (`daily`, `weekly`, `monthly`, `never`).
- `sessions.retention.active_days`: how old a live session must be before archive compaction can move it into the monthly gzip archive.

The archive scheduler persists its last run in local-only `.umx.json` under `sessions.last_archive_compaction`.

## Dream

```yaml
dream:
  lint_interval: weekly
```

- `dream.lint_interval`: cadence for the Dream lint sub-phase (`daily`, `weekly`, `never`).

## Search

```yaml
search:
  backend: fts5
  embedding:
    provider: sentence-transformers
    model: all-MiniLM-L6-v2
    model_version: v1.0
    api_base: null
    candidate_limit: 100
```

- `search.backend`: `fts5` for lexical-only search, `hybrid` to enable the optional embedding reranker.
- `search.embedding.provider`: embedding provider identifier. Supported values today are `sentence-transformers` (default local backend), `openai`, `voyage`, and `fixture` (test-only).
- `search.embedding.model`: provider-specific model name.
- `search.embedding.model_version`: cache signature version. Changing provider, model, or model version requires `gitmem rebuild-index --embeddings`.
- `search.embedding.api_base`: optional endpoint override for OpenAI-compatible or Voyage-compatible embedding APIs.
- `search.embedding.candidate_limit`: maximum FTS candidate set passed to the semantic reranker in `hybrid` mode.

Remote embedding providers read credentials from the environment, not `config.yaml`:

- `openai`: `UMX_OPENAI_API_KEY` first, then `OPENAI_API_KEY`
- `voyage`: `UMX_VOYAGE_API_KEY` first, then `VOYAGE_API_KEY`

Anthropic does not currently expose a native embeddings endpoint, so there is no `anthropic` embedding provider yet.

## Telemetry

```yaml
telemetry:
  enabled: false
  endpoint: https://telemetry.gitmem.dev/v1/events
  timeout_seconds: 2
  batch_size: 20
```

- `telemetry.enabled`: opt in to anonymous product telemetry. Default is `false`.
- `telemetry.endpoint`: POST endpoint for batched telemetry events. `UMX_TELEMETRY_ENDPOINT` can override it for testing or self-hosted collection.
- `telemetry.timeout_seconds`: per-upload timeout. Failures are queued locally and retried later.
- `telemetry.batch_size`: maximum events sent per upload attempt.

Set `UMX_TELEMETRY_DISABLE=1` to honor the local kill switch immediately, even when telemetry is enabled in config.

See [privacy.md](privacy.md) for the exact payload boundaries.
