# gitmem

**Git-native shared memory for AI CLI agents.**

A shared memory layer that runs on your filesystem and syncs through GitHub.
Any tool that can read a file gets the same context. Facts are governed through pull requests so memory is auditable, correctable, and versioned like code.

Your AI tools share a brain, and you can see exactly what's in it.


`umx` (Universal Memory Exchange) gives AI coding agents — Claude Code, Codex, Copilot, Cursor, Aider — persistent, structured memory that survives across sessions. Memory is stored as git repos with markdown fact files, scored by encoding strength, and managed through a dream pipeline that extracts, consolidates, and prunes knowledge automatically.

> **Alpha release** — the local-mode core is solid and dogfood-tested across Claude Code, Copilot CLI, Gemini CLI, and OpenCode. Remote/hybrid GitHub integration is experimental.

## Features

- **Dream pipeline** — extract facts from sessions → consolidate → lint → prune → save
- **Session capture** — `umx capture codex` / `umx capture copilot`, hooks, or MCP server
- **Budget-aware injection** — pack the most relevant memory into a token budget
- **FTS5 search** — full-text indexed fact search
- **Git-native storage** — every fact is a markdown file with inline metadata in a git repo
- **Scope hierarchy** — user → tool → project → folder → file
- **Encoding strength 1–5** — ground truth to ambient signals
- **GitHub integration** (experimental) — remote/hybrid modes with PR-based governance

## Status

| Feature | Status |
|---|---|
| Local mode (init, dream, inject, search, view) | ✅ Solid |
| Codex capture | ✅ Working |
| Copilot CLI capture | ✅ Working |
| Claude Code capture | 🔜 Next |
| Session hooks & MCP server | ✅ Working |
| Remote mode (PR governance) | 🧪 Experimental |
| Hybrid mode (sessions push, facts via PR) | 🧪 Experimental |
| Extraction quality on real transcripts | ⚠️ Rough edges |

## Install

```bash
pip install git+https://github.com/dev-boz/gitmem.git
```

Or for development:

```bash
git clone https://github.com/dev-boz/gitmem.git
cd gitmem
pip install -e ".[dev]"
```

## Quick Start

```bash
# Initialize memory home
umx init

# Initialize a project
umx init-project --cwd /path/to/project

# Capture a Codex session
umx capture codex --cwd /path/to/project

# Capture a Copilot CLI session
umx capture copilot --cwd /path/to/project

# Run the dream pipeline
umx dream --cwd /path/to/project --force

# Search memory
umx search --cwd /path/to/project postgres

# Inject memory into a prompt
umx inject --cwd /path/to/project --prompt "postgres deploy flow"

# View facts
umx view --cwd /path/to/project --list
```

## Remote/Hybrid Mode (Experimental)

Requires `gh` CLI installed and authenticated (`gh auth login`).

Initialize with remote mode:

```bash
umx init --org your-github-org --mode remote
umx init-project --cwd /path/to/project
```

This creates private repos under your org (`your-github-org/umx-user`, `your-github-org/<project-slug>`) and sets up git remotes. In `remote` mode, GitHub Actions workflow templates (L1 dream, L2 review) are deployed.

Or connect an existing local project to a GitHub org:

```bash
umx setup-remote --cwd /path/to/project --mode hybrid
```

When you run `umx dream` in remote/hybrid mode, extracted facts are committed to a feature branch, pushed, and a PR is opened on GitHub:

```bash
umx dream --cwd /path/to/project --force
# Output includes PR number: "PR: [dream/l1] ... (#42)"
```

Sync sessions and facts with `umx sync`:

```bash
umx sync --cwd /path/to/project
```

### Mode differences

| | `local` | `remote` | `hybrid` |
|---|---|---|---|
| Facts | direct write | PR only | PR only |
| Sessions | local | local | push to main |
| Governance | none | full L1/L2 | full L1/L2 |
| Best for | solo/offline | team/audit | team/fast sessions |

## Tested With

The full capture → dream → search → inject loop has been dogfood-tested by:

- **Copilot CLI** (Claude Opus 4.6) — captured 84 events, extracted 127 facts
- **Claude Code** — captured 153 events, extracted 73 facts, 203 retained
- **Gemini CLI** — captured 153 events, 207 facts retained
- **OpenCode** — captured 153 events, 205 facts retained

All four agents ran the complete pipeline without crashes and converged on the same quality feedback (extraction noise, topic assignment, injection relevance).

## Development

```bash
# Run all tests
pytest -q

# Focused test suites
pytest -q tests/test_codex_capture.py tests/test_copilot_capture.py tests/test_golden_extraction.py
pytest -q tests/test_github_ops.py tests/test_governance.py
```

## Spec

See [gitmem-spec-v0_9.md](gitmem-spec-v0_9.md) for the full specification.

## License

MIT — see [LICENSE](LICENSE).
