# CLI spec parity audit

This matrix records the v0.9.2 CLI-surface parity pass between `gitmem-spec-v0_9.md` §25 and `umx/cli.py`. Status legend:

- **Match** — spec and code agree for the shipped surface.
- **Documented** — auxiliary shipped surface is referenced in the spec and enumerated here.

Within the audited CLI surface below, there are no unresolved command/flag divergences in this snapshot. This page does **not** claim full runtime/spec parity for broader Dream-governance behavior; those larger gaps still live in the main spec and implementation docs.

## Core commands

| Command | Shipped flags / surface | Code location | Status | Notes |
|---|---|---|---|---|
| `init` | `--owner` (`--org` alias), `--mode local\|remote\|hybrid` | `umx/cli.py:136-173` | Match | Spec updated to include owner-first wording while keeping the legacy alias. |
| `init-project` | `--cwd`, `--slug`, `--yes` | `umx/cli.py:176-230` | Match | `--slug` bypasses prompting; `--yes` auto-suffixes collisions. |
| `inject` | `--cwd`, `--tool`, `--prompt`, `--command`, `--session`, `--context-window`, `--expand-fact ...`, `--file ...`, `--max-tokens` | `umx/cli.py:233-289` | Match | Spec now mirrors the actual injection surface. |
| `collect` | `--cwd`, `--tool`, `--file`, `--format`, `--role`, `--session-id`, `--meta ...`, `--dry-run` | `umx/cli.py:292-376` | Match | Spec updated from the earlier shorthand. |
| `dream` | `--cwd`, `--force`, `--force-reason`, `--force-lint`, `--mode`, `--tier`, `--pr`, `--head-sha`, `--provider` | `umx/cli.py:779-845` | Match | `--force-lint` and `--force-reason` document the shipped governance override surface; `--provider` documents L2 reviewer selection. |
| `view` | `--cwd`, `--fact`, `--list`, `--min-strength` | `umx/cli.py:432-465` | Match | Replaced stale draft `--scope` surface. |
| `tui` | `--cwd` | `umx/cli.py:468-478` | Match | No flag divergence. |
| `status` | `--cwd` | `umx/cli.py:916-919` | Match | No flag divergence. |
| `health` | `--cwd`, `--governance`, `--format json\|human` | `umx/cli.py:923-949` | Match | Governance health adds a richer repo-policy audit surface; `--format human` is only valid with `--governance`. |
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
| `doctor` | `--cwd`, `--fix` | `umx/cli.py:1416-1420` | Match | Spec updated to include `--cwd`. |
| `migrate` | `--cwd` | `umx/cli.py:1423-1434` | Match | Added to spec to reflect the shipped fact-file migration command. |
| `export` | `--cwd`, `--out` | `umx/cli.py:2238-2249` | Match | Added to spec to reflect the shipped full-backup export command. |
| `config` | `set redaction.patterns <value>` | `umx/cli.py:951-966` | Match | Validates a regex string or JSON array of regex strings and stores them under `sessions.redaction_patterns`. |
| `secret` | `get <key>`, `set <key> <value>` | `umx/cli.py:1138-1165` | Match | No flag divergence. |
| `import` | `--cwd`, `--adapter`, `--dry-run` | `umx/cli.py:1168-1195` | Match | Spec updated from `--tool` to the shipped adapter terminology. |
| `mcp` | no flags | `umx/cli.py:1198-1203` | Match | Added to spec to reflect the shipped MCP server entrypoint. |
| `search` | `--cwd`, `--raw` (`--all` alias) | `umx/cli.py:2852-2875` | Match | Spec updated so the cold-tier search example can use the shipped raw-session alias without diverging from the canonical `--raw` wording. |

## Auxiliary command groups

| Family | Shipped subcommands / flags | Code location | Status | Notes |
|---|---|---|---|---|
| `capture` | `codex(--cwd,--file,--source-root,--dry-run)`, `copilot(--cwd,--file,--source-root,--dry-run)`, `claude-code(--cwd,--file,--source-root,--all,--dry-run)`, `gemini(--cwd,--file,--source-root,--all,--dry-run)`, `opencode(--cwd,--db,--session-id,--all,--dry-run)`, `amp(--cwd,--file,--source-root,--thread-id,--all,--dry-run)` | `umx/cli.py:1206-1635` | Documented | Auxiliary transcript-import surfaces referenced in the spec and enumerated here. |
| `eval` | `l2-review(--cases,--case,--min-pass-rate,--provider)`, `inject(--cases,--case,--min-pass-rate,--disclosure-slack-pct)`, `long-memory(--cases,--case,--min-pass-rate,--search-limit)`, `longmemeval(--cases,--out-dir,--provider,--model,...)`, `locomo(--cases,--out-dir,--provider,--model,...)`, `convomem(--cases,--out-dir,--provider,--model,...)`, `longbench-v2(--cases,--out-dir,--provider,--model,...)`, `ruler(--cases,--out-dir,--provider,--model,...)`, `beir(--cases,--min-ndcg-at-10,--min-recall-at-10)`, `retrieval(--cases,--case,--min-pass-rate,--top-k)`, `compare(<baseline> <candidate>)`, `release-gate(...)` | `umx/cli.py:1489-2017` | Documented | On-demand eval harnesses now cover governance review, native inject drift, long-memory retrieval, benchmark-backed provider runs, offline retrieval comparison, and bundled release-gate capture flows. `l2-review --provider` selects between Anthropic API, NVIDIA API, and Claude Code CLI (OAuth) reviewers. |
| `hooks claude-code` | `print(--command)`, `install(--cwd,--scope,--command)`, `session-start(--payload-file)`, `pre-tool-use(--payload-file)`, `pre-compact(--payload-file)`, `session-end(--payload-file)` | `umx/cli.py:937-1022` | Documented | Hook-install and hook-dispatch helpers for Claude Code. |
| `bridge` | `sync(--cwd,--target ...)`, `remove(--cwd,--target ...)`, `import(--cwd,--target ...,--topic,--dry-run)` | `umx/cli.py:1025-1078` | Documented | Legacy compatibility surface for project-repo bridge files. |
| `shim` | `aider(--cwd,--output,--max-tokens)`, `generic(--cwd,--tool,--output,--max-tokens)`, `amp(--cwd,--output,--max-tokens)`, `cursor(--cwd,--output,--max-tokens)`, `jules(--cwd,--output,--max-tokens)`, `qodo(--cwd,--output,--max-tokens)` | `umx/cli.py:1081-1135` | Documented | Wrapper/shim helpers for tool integration. |

## Resolved draft mismatches

The parity pass intentionally aligned the spec to the shipped CLI instead of adding placeholder implementations for draft-only flags:

- `audit --all --model` → removed from the canonical CLI surface; the shipped audit path is `--rederive`, `--session`, and `--cross-project` / `--proposal-key`.
- `sync --all` → removed; sync is project-scoped and selected via `--cwd`.
- `rebuild-index --force` → removed; the shipped explicit flag is `--embeddings`.
- `view --scope` → removed; the shipped surface is `--fact` / `--list`.
- `import --tool` → renamed in the spec to the shipped `--adapter`.
- `health`, `setup-remote`, `archive-sessions`, `init-actions`, `mcp`, `search`, `capture`, `hooks`, `bridge`, `shim`, `migrate`, and `export` were added to the documented surface because they already ship in code.

## Known broader spec gaps

These are outside the narrow CLI flag audit above:

- Remote/hybrid Dream now opens dedicated lint PRs for `meta/lint-report.md` while syncing `meta/lint-state.json` to `main` as operational cadence state, so lint PRs stay non-blocking without reopening on every run.
- `audit --rederive` now opens governed correction proposal branches/PRs when it finds drift, but the re-extraction itself is still the shipped native session-derive path rather than a model-selectable deep re-derivation workflow.
- Branch protection, approval gating, and other GitHub governance hardening remain partial/experimental outside the shipped local-first core.
