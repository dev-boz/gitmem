# CLI spec parity audit

This matrix records the v0.9.2 parity pass between `gitmem-spec-v0_9.md` §25 and `umx/cli.py`. Status legend:

- **Match** — spec and code agree for the shipped surface.
- **Documented** — auxiliary shipped surface is referenced in the spec and enumerated here.

There are no unresolved CLI divergences in this snapshot; draft-only flags were removed from the canonical spec surface rather than left unexplained.

## Core commands

| Command | Shipped flags / surface | Code location | Status | Notes |
|---|---|---|---|---|
| `init` | `--owner` (`--org` alias), `--mode local\|remote\|hybrid` | `umx/cli.py:136-173` | Match | Spec updated to include owner-first wording while keeping the legacy alias. |
| `init-project` | `--cwd`, `--slug`, `--yes` | `umx/cli.py:176-230` | Match | `--slug` bypasses prompting; `--yes` auto-suffixes collisions. |
| `inject` | `--cwd`, `--tool`, `--prompt`, `--command`, `--session`, `--context-window`, `--expand-fact ...`, `--file ...`, `--max-tokens` | `umx/cli.py:233-289` | Match | Spec now mirrors the actual injection surface. |
| `collect` | `--cwd`, `--tool`, `--file`, `--format`, `--role`, `--session-id`, `--meta ...`, `--dry-run` | `umx/cli.py:292-376` | Match | Spec updated from the earlier shorthand. |
| `dream` | `--cwd`, `--force`, `--force-lint`, `--mode`, `--tier`, `--pr`, `--head-sha` | `umx/cli.py:379-429` | Match | `--force-lint` added in T1.1; L2 review flags documented explicitly. |
| `view` | `--cwd`, `--fact`, `--list`, `--min-strength` | `umx/cli.py:432-465` | Match | Replaced stale draft `--scope` surface. |
| `tui` | `--cwd` | `umx/cli.py:468-478` | Match | No flag divergence. |
| `status` | `--cwd` | `umx/cli.py:481-484` | Match | No flag divergence. |
| `health` | `--cwd` | `umx/cli.py:487-506` | Match | Added to spec to reflect shipped diagnostics surface. |
| `conflicts` | `--cwd` | `umx/cli.py:509-515` | Match | No flag divergence. |
| `gaps` | `--cwd` | `umx/cli.py:518-520` | Match | No flag divergence. |
| `forget` | `--cwd`, `--fact`, `--topic`, `--governed` | `umx/cli.py:802-840` | Match | Governing a forget now routes fact or topic tombstones through proposal PR branches instead of direct writes. |
| `rollback` | `--cwd`, `--pr` | `umx/cli.py:843-852` | Documented | Governed reverse-PR maintenance surface for restoring facts removed by a prior tombstone PR. |
| `promote` | `--cwd`, `--fact`, `--to user\|project\|principle` | `umx/cli.py:539-555` | Match | Spec updated from `--to user` only. |
| `confirm` | `--cwd`, `--fact` | `umx/cli.py:557-566` | Match | No flag divergence. |
| `history` | `--cwd`, `--fact` | `umx/cli.py:569-575` | Match | No flag divergence. |
| `resume` | `--cwd`, `--include-abandoned` | `umx/cli.py:578-587` | Match | No flag divergence. |
| `meta` | `--cwd`, `--topic` | `umx/cli.py:590-596` | Match | No flag divergence. |
| `merge` | `--cwd`, `--dry-run` | `umx/cli.py:599-608` | Match | Spec updated to document the dry-run switch. |
| `audit` | `--cwd`, `--rederive`, `--cross-project`, `--proposal-key`, `--session ...` | `umx/cli.py:611-663` | Match | Replaced stale draft `--all` / `--model` with the shipped cross-project surface. |
| `propose` | `--cwd`, `--cross-project`, `--proposal-key`, `--push`, `--open-pr` | `umx/cli.py:665-717` | Match | Canonical promotion proposal surface. |
| `sync` | `--cwd` | `umx/cli.py:728-802` | Match | Replaced stale draft `--all`; sync is project-scoped via `--cwd`. |
| `setup-remote` | `--cwd`, `--mode remote\|hybrid` | `umx/cli.py:805-844` | Match | Added to spec to reflect the shipped bootstrap workflow. |
| `purge` | `--cwd`, `--session`, `--dry-run` | `umx/cli.py:847-875` | Match | Spec updated to include `--cwd` and dry-run mode. |
| `rebuild-index` | `--cwd`, `--embeddings` | `umx/cli.py:878-884` | Match | Replaced stale draft `--force`. |
| `archive-sessions` | `--cwd` | `umx/cli.py:887-896` | Match | Added to spec to reflect the shipped archive operation. |
| `init-actions` | `--dir` | `umx/cli.py:899-905` | Match | Added to spec to reflect workflow-template scaffolding. |
| `migrate-scope` | `--cwd`, `--from`, `--to` | `umx/cli.py:908-919` | Match | No flag divergence. |
| `doctor` | `--cwd`, `--fix` | `umx/cli.py:915-919` | Match | Spec updated to include `--cwd`. |
| `config` | `set redaction.patterns <value>` | `umx/cli.py:951-966` | Match | Validates a regex string or JSON array of regex strings and stores them under `sessions.redaction_patterns`. |
| `secret` | `get <key>`, `set <key> <value>` | `umx/cli.py:1138-1165` | Match | No flag divergence. |
| `import` | `--cwd`, `--adapter`, `--dry-run` | `umx/cli.py:1168-1195` | Match | Spec updated from `--tool` to the shipped adapter terminology. |
| `mcp` | no flags | `umx/cli.py:1198-1203` | Match | Added to spec to reflect the shipped MCP server entrypoint. |

## Auxiliary command groups

| Family | Shipped subcommands / flags | Code location | Status | Notes |
|---|---|---|---|---|
| `capture` | `codex(--cwd,--file,--source-root,--dry-run)`, `copilot(--cwd,--file,--source-root,--dry-run)`, `claude-code(--cwd,--file,--source-root,--all,--dry-run)`, `gemini(--cwd,--file,--source-root,--all,--dry-run)`, `opencode(--cwd,--db,--session-id,--all,--dry-run)`, `amp(--cwd,--file,--source-root,--thread-id,--all,--dry-run)` | `umx/cli.py:1206-1635` | Documented | Auxiliary transcript-import surfaces referenced in the spec and enumerated here. |
| `eval` | `l2-review(--cases,--case,--min-pass-rate,--provider)`, `inject(--cases,--case,--min-pass-rate,--disclosure-slack-pct)`, `long-memory(--cases,--case,--min-pass-rate,--search-limit)`, `retrieval(--cases,--case,--min-pass-rate,--top-k)` | `umx/cli.py:1273-1400` | Documented | On-demand eval harnesses for governance review, native inject drift, and offline benchmark-shaped adapters for long-memory and multi-hop supporting-fact retrieval. `l2-review --provider` selects between the Anthropic API and the Claude Code CLI (OAuth) reviewer. |
| `hooks claude-code` | `print(--command)`, `install(--cwd,--scope,--command)`, `session-start(--payload-file)`, `pre-tool-use(--payload-file)`, `pre-compact(--payload-file)`, `session-end(--payload-file)` | `umx/cli.py:937-1022` | Documented | Hook-install and hook-dispatch helpers for Claude Code. |
| `bridge` | `sync(--cwd,--target ...)`, `remove(--cwd,--target ...)`, `import(--cwd,--target ...,--topic,--dry-run)` | `umx/cli.py:1025-1078` | Documented | Legacy compatibility surface for project-repo bridge files. |
| `shim` | `aider(--cwd,--output,--max-tokens)`, `generic(--cwd,--tool,--output,--max-tokens)`, `amp(--cwd,--output,--max-tokens)`, `cursor(--cwd,--output,--max-tokens)`, `jules(--cwd,--output,--max-tokens)`, `qodo(--cwd,--output,--max-tokens)` | `umx/cli.py:1081-1135` | Documented | Wrapper/shim helpers for tool integration. |

## Resolved draft mismatches

The parity pass intentionally aligned the spec to the shipped CLI instead of adding placeholder implementations for draft-only flags:

- `audit --all --model` → removed from the canonical spec surface; the shipped audit path is `--rederive`, `--session`, and `--cross-project` / `--proposal-key`.
- `sync --all` → removed; sync is project-scoped and selected via `--cwd`.
- `rebuild-index --force` → removed; the shipped explicit flag is `--embeddings`.
- `view --scope` → removed; the shipped surface is `--fact` / `--list`.
- `import --tool` → renamed in the spec to the shipped `--adapter`.
- `health`, `setup-remote`, `archive-sessions`, `init-actions`, `mcp`, `capture`, `hooks`, `bridge`, and `shim` were added to the documented surface because they already ship in code.
