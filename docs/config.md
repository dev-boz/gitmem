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
    candidate_limit: 100
```

- `search.backend`: `fts5` for lexical-only search, `hybrid` to enable the optional embedding reranker.
- `search.embedding.provider`: embedding provider identifier. `sentence-transformers` is the supported production value today.
- `search.embedding.model`: provider-specific model name.
- `search.embedding.model_version`: cache signature version. Changing provider, model, or model version requires `gitmem rebuild-index --embeddings`.
- `search.embedding.candidate_limit`: maximum FTS candidate set passed to the semantic reranker in `hybrid` mode.
