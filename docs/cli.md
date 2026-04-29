# CLI reference

`gitmem` is the primary command. `umx` remains a compatibility alias.

This page is a concise operator reference for the shipped CLI. For a command-by-command parity audit, see [spec-parity.md](spec-parity.md).

## Setup

| Command | Purpose | Common flags |
|---|---|---|
| `gitmem init` | Initialize `~/.umx/` | `--owner` (`--org` compatibility alias), `--mode local\|remote\|hybrid` |
| `gitmem init-project` | Create a project memory repo | `--cwd`, `--slug`, `--yes` |
| `gitmem setup-remote` | Attach an existing project repo to GitHub-backed memory | `--cwd`, `--mode remote\|hybrid` |
| `gitmem config set` | Set supported config keys | `redaction.patterns <value>`, `telemetry.enabled <true\|false>` |
| `gitmem secret get/set` | Read or write user secret files | `<key> [value]` |

## Capture and import

| Command | Purpose | Common flags |
|---|---|---|
| `gitmem capture codex` | Import Codex rollout JSONL | `--cwd`, `--file`, `--source-root`, `--dry-run` |
| `gitmem capture copilot` | Import Copilot `events.jsonl` | `--cwd`, `--file`, `--source-root`, `--dry-run` |
| `gitmem capture claude-code` | Import Claude Code session JSONL | `--cwd`, `--file`, `--source-root`, `--all`, `--dry-run` |
| `gitmem capture gemini` | Import Gemini CLI session JSON | `--cwd`, `--file`, `--source-root`, `--all`, `--dry-run` |
| `gitmem capture opencode` | Import OpenCode sessions from SQLite | `--cwd`, `--db`, `--session-id`, `--all`, `--dry-run` |
| `gitmem capture amp` | Import Amp thread JSON | `--cwd`, `--file`, `--source-root`, `--thread-id`, `--all`, `--dry-run` |
| `gitmem collect` | Store manual or wrapper-exported sessions | `--cwd`, `--tool`, `--file`, `--format`, `--role`, `--session-id`, `--meta`, `--dry-run` |
| `gitmem import --adapter ...` | Import existing native-memory files | `--cwd`, `--adapter`, `--dry-run` |
| `gitmem import --full ...` | Restore a full project backup | `--cwd`, `--full`, `--force`, `--dry-run` |

## Retrieval and review

| Command | Purpose | Common flags |
|---|---|---|
| `gitmem dream` | Run Dream or review a governed PR | `--cwd`, `--force`, `--force-lint`, `--mode`, `--tier l2`, `--pr`, `--head-sha`, `--provider` |
| `gitmem search` | Search facts, or raw sessions with `--raw` | `--cwd`, `--raw` |
| `gitmem inject` | Build prompt-ready memory context | `--cwd`, `--tool`, `--prompt`, `--command`, `--session`, `--file`, `--max-tokens` |
| `gitmem view` | List facts, inspect one fact, or launch viewer | `--cwd`, `--list`, `--fact`, `--min-strength` |
| `gitmem tui` | Launch the local viewer | `--cwd` |
| `gitmem status` | Repo status summary | `--cwd` |
| `gitmem health` | Memory health or governance health | `--cwd`, `--governance`; `--format json\|human` only with `--governance` |
| `gitmem conflicts` | Show conflicting facts | `--cwd` |
| `gitmem gaps` | Print gap report or emit a worked-around gap signal | `--cwd`, `--query`, `--resolution-context`, `--proposed-fact`, `--session` |
| `gitmem history` | Show a fact’s supersession chain | `--cwd`, `--fact` |
| `gitmem resume` | List open tasks from memory | `--cwd`, `--include-abandoned` |
| `gitmem meta` | Show topic metadata | `--cwd`, `--topic` |

## Fact actions

| Command | Purpose | Common flags |
|---|---|---|
| `gitmem confirm` | Confirm a fact | `--cwd`, `--fact` |
| `gitmem forget` | Tombstone a fact or topic | `--cwd`, `--fact` or `--topic`, `--governed` |
| `gitmem rollback` | Open a governed reverse PR for a prior tombstone PR | `--cwd`, `--pr` |
| `gitmem promote` | Promote a fact across scopes | `--cwd`, `--fact`, `--to user\|project\|principle` |
| `gitmem merge` | Merge conflict candidates | `--cwd`, `--dry-run` |
| `gitmem purge` | Remove a session and derived facts | `--cwd`, `--session`, `--dry-run` |

## Governance, sync, and cross-project work

| Command | Purpose | Common flags |
|---|---|---|
| `gitmem sync` | Sync project and user memory repos to their configured remotes | `--cwd` |
| `gitmem audit` | Audit a repo or inspect cross-project promotion candidates | `--cwd`, `--rederive`, `--session`, `--cross-project`, `--proposal-key` |
| `gitmem propose` | Materialize, push, or open cross-project promotion PRs | `--cwd`, `--cross-project`, `--proposal-key`, `--push`, `--open-pr` |
| `gitmem init-actions` | Write workflow templates into a target directory | `--dir` |
| `gitmem eval l2-review` | Run the L2 review eval harness | `--cases`, `--case`, `--min-pass-rate`, `--provider` |
| `gitmem eval inject` | Run the inject/retrieval golden eval harness | `--cases`, `--case`, `--min-pass-rate`, `--disclosure-slack-pct` |
| `gitmem eval long-memory` | Run the LongMemEval-style evidence-retrieval pilot | `--cases`, `--case`, `--min-pass-rate`, `--search-limit` |
| `gitmem eval longmemeval` | Run the upstream-style LongMemEval QA benchmark through the local Claude Code CLI session | `--cases`, `--out-dir`, `--case`, `--min-pass-rate`, `--search-limit`, `--provider`, `--judge-provider`, `--model`, `--judge-model`, `--history-format` |
| `gitmem eval retrieval` | Run the HotpotQA-style supporting-fact retrieval pilot | `--cases`, `--case`, `--min-pass-rate`, `--top-k` |
| `gitmem eval compare` | Compare two saved eval JSON artifacts and fail on regressions | `--metric`, `--tolerance` |
| `gitmem eval release-gate` | Write a release-gate artifact bundle | `--out-dir`, `--long-memory-release-cases`, `--retrieval-release-cases`, `--long-memory-release-min-pass-rate`, `--retrieval-release-min-pass-rate`, `--long-memory-baseline`, `--retrieval-baseline` |

In local mode, `gitmem sync` remains a no-op until at least one memory repo has a configured remote. When the paired user repo also has a remote, the same command syncs both repos in one handoff, but it does so sequentially (`umx-user` first, then the project repo) and reports partial success if the later repo fails.

When concurrent edits touch the same memory file, `gitmem sync` fails closed and surfaces the conflicting paths from the failed rebase so you can resolve or abort the rebase before retrying. If a previous run already left a rebase or merge in progress, the command now fails fast with explicit continue/abort guidance instead of stumbling into a generic git error.

`gitmem eval l2-review` and `gitmem eval inject` are native gitmem evals over checked-in corpora. `gitmem eval long-memory` and `gitmem eval retrieval` are benchmark-shaped adapters that stay offline by running against checked-in subsets and temporary repos, or against a maintainer-provided public benchmark slice via `--cases`. `gitmem eval longmemeval` is the provider-backed QA path: it reuses gitmem retrieval to select memory sessions, generates answer text, and then scores those answers with the LongMemEval yes/no rubric through the operator's Claude Code CLI session.

All `gitmem eval ...` commands emit stable JSON to stdout and exit nonzero when the requested pass-rate gate fails, so they can be used directly in CI or saved as release artifacts. `gitmem eval compare` reads two saved artifacts, compares default suite metrics for `inject`, `long-memory`, and `retrieval`, and exits nonzero when the candidate regresses past the chosen tolerance.

`gitmem eval longmemeval` also writes benchmark artifacts under `--out-dir`:

- `hypotheses.jsonl` with `question_id` / `hypothesis`
- `judgments.jsonl` with per-case answer labels
- `summary.json` with aggregate metrics and usage totals

`gitmem eval release-gate` is the thin bundling helper for repeated RC runs: it always writes the local smoke artifacts (`inject`, `long-memory`, `retrieval`) under one `--out-dir`, can optionally add release-grade LongMemEval / HotpotQA artifacts from `--*-release-cases`, and can optionally write compare artifacts when baseline JSON paths are supplied. If you need a first benchmark capture before an accepted baseline exists, lower the release-case gates with `--long-memory-release-min-pass-rate 0` and `--retrieval-release-min-pass-rate 0`.

For `gitmem eval long-memory` and `gitmem eval retrieval`, `--cases` can point at:

- raw LongMemEval JSON
- raw HotpotQA JSON
- a `hotpotqa-manifest` file that selects pinned HotpotQA `_id` values from a larger dataset file
- the checked-in offline subsets under `tests/eval/`

That means a release-grade HotpotQA rerun can point straight at a filtered raw benchmark slice without pre-converting it into gitmem’s native eval schema. `gitmem eval longmemeval` is stricter: it expects upstream-style LongMemEval JSON with `answer`, `question_date`, and haystack session fields, and writes answer/judgment artifacts under `--out-dir`.

For 1.0, release stays blocked until both claims are signed off: **it works personally** and **it works on benchmarks**. Use the [operations runbook](ops-runbook.md) for the step-by-step workflow and the [launch checklist](launch-checklist.md) for the final ship/no-ship record.

### L2 reviewer providers

`gitmem eval l2-review` and `gitmem dream --tier l2` choose a reviewer via `--provider`:

- `--provider anthropic` (default) — direct Anthropic API. Requires `ANTHROPIC_API_KEY` in the environment.
- `--provider claude-cli` — shells out to the locally installed Claude Code CLI in headless `-p` mode (`claude --print --output-format json`). Uses the operator's existing Claude Code OAuth session, so no API key is required. The binary path can be overridden with `UMX_CLAUDE_CLI_BIN`, and the per-call timeout with `UMX_CLAUDE_CLI_TIMEOUT` (seconds, default 180).

Both providers emit the same JSON payload shape and the same `prompt_version`; only the `prompt_id` differs (`anthropic-l2-review` vs `claude-cli-l2-review`) so historical runs stay comparable within a provider.

## Maintenance and recovery

| Command | Purpose | Common flags |
|---|---|---|
| `gitmem doctor` | Validate repo state and optionally repair repo-level issues | `--cwd`, `--fix` |
| `gitmem migrate` | Run fact-file migrations | `--cwd` |
| `gitmem migrate-scope` | Move a scope path inside the memory repo | `--cwd`, `--from`, `--to` |
| `gitmem rebuild-index` | Rebuild search indexes | `--cwd`, `--embeddings` |
| `gitmem archive-sessions` | Compact older sessions into archives | `--cwd` |
| `gitmem export` | Create a full project backup bundle | `--cwd`, `--out` |

## Integration surfaces

| Command | Purpose | Common flags |
|---|---|---|
| `gitmem hooks claude-code print` | Print the Claude Code hook config | `--command` |
| `gitmem hooks claude-code install` | Install Claude Code hooks | `--cwd`, `--scope`, `--command` |
| `gitmem hooks claude-code session-start` | Hook dispatch helper | `--payload-file` |
| `gitmem hooks claude-code pre-tool-use` | Hook dispatch helper | `--payload-file` |
| `gitmem hooks claude-code pre-compact` | Hook dispatch helper | `--payload-file` |
| `gitmem hooks claude-code session-end` | Hook dispatch helper | `--payload-file` |
| `gitmem shim aider\|generic\|amp\|cursor\|jules\|qodo` | Emit wrapper-compatible context | tool-specific |
| `gitmem bridge sync\|remove\|import` | Manage legacy bridge files | tool-specific |
| `gitmem mcp` | Start the stdio MCP server | none |
