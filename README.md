# gitmem

**Git-native shared memory for AI coding agents.**

A shared memory layer that runs on your filesystem and syncs through GitHub.
Any tool that can read a file gets the same context. Facts are governed through pull requests so memory is auditable, correctable, and versioned like code.

---

Your AI tools don't talk to each other. A fact learned by Claude Code — *postgres runs on 5433, ignore CORS warnings in dev* — is invisible to Codex, Copilot, Gemini CLI, or any other tool you use on the same codebase. Switch tools and you start from scratch.

Worse: when tools *do* remember things, there's no audit trail. A cheap model extracts a fact wrong and it persists silently — no provenance, no correction mechanism, no way to see what your agent "knows" or challenge it.

gitmem fixes both problems. Memory is stored as markdown files in git repos, synced through GitHub, and governed through pull requests. Any tool that can read a file gets the same context. Facts are versioned, auditable, and correctable — like code.

**Your AI tools share a brain, and you can see exactly what's in it.**

> **Alpha release** — the local-mode core is solid and dogfood-tested across Claude Code, Codex, Copilot CLI, Gemini CLI, and OpenCode. GitHub PR governance is experimental. Releasing now to get the idea out; expect rough edges.

## The idea

Andrej Karpathy [described a pattern](https://x.com/karpathy/status/1913013338341937621) where an LLM maintains a wiki of its own knowledge — raw session transcripts are immutable source material, and the model distills them into structured, maintained pages.

gitmem takes that idea and builds it for the multi-tool coding workflow:

- **Git-native history** where the wiki has none — every fact change is a commit, every correction is traceable
- **PR-based governance** where the wiki has no review mechanism — cheap models propose facts, SotA models filter, humans resolve ambiguity
- **Encoding strength and provenance** where the wiki treats all knowledge equally — a fact read from source code outranks an LLM's guess, always
- **Multi-agent support** where the wiki is single-user — Claude Code, Codex, Copilot, Aider, Gemini CLI all read and write the same memory
- **Cognitive science taxonomy** where the wiki has flat pages — episodic vs semantic memory, interference-based conflict resolution, cue-dependent retrieval

The wiki pattern is the right intuition. gitmem adds the engineering: governance, history, provenance, and multi-agent coordination.

## What sets it apart

**GitHub as the source of truth.** Memory repos live in a private GitHub org you own. Facts arrive via PR. You review what your agents "learn" the same way you review code. Branch protection, audit logs, and Actions workflows come free.

**Cognitive science, not vibes.** The memory model is grounded in established research — Tulving's episodic/semantic distinction, Anderson's activation strength, interference theory for contradiction handling, cue-dependent retrieval for injection. This isn't arbitrary; it's why the system handles conflict resolution, fact decay, and context-aware recall the way it does.

**Encoding strength, not flat confidence.** Every fact carries a strength from 1-5 based on *how* it was learned, not just a model's self-assessed confidence score. A function signature parsed from an AST (S:4) outranks a pattern inferred from logs (S:2), which outranks a single unconfirmed mention (S:1). Ground-truth code *cannot* be overruled by LLM inference — it's a hard rule, not a scoring tiebreak.

**Tool-agnostic by design.** gitmem is a filesystem convention and injection protocol, not a service. It doesn't wrap or replace your tools. Any CLI that can read a file and execute a hook can participate. The `umx` CLI handles the pipeline; your agents just read and write.

**Zero infrastructure.** No cloud services, no API keys (beyond GitHub). Memory is markdown files in git repos. SQLite indexes are local build artifacts. Everything works offline in local mode.

**Dream pipeline.** After sessions end, a background pipeline extracts facts from transcripts, consolidates them against existing knowledge, detects contradictions, resolves conflicts by composite score, lints for drift, and prunes stale facts. In remote/hybrid mode, all of this goes through PRs — cheap models propose, SotA models review, nothing auto-commits to main.

## How it works

```
                    You
                     |
        +-----------+-----------+
        |           |           |
   Claude Code    Codex     Gemini CLI    ...any CLI agent
        |           |           |
        +-----------+-----------+
                    |
              umx (capture)
                    |
        +-----------+-----------+
        |                       |
   sessions/                 dream pipeline
   (immutable logs)     extract -> consolidate -> lint -> prune
        |                       |
        +-----------+-----------+
                    |
              memory repo
         (markdown + git)
                    |
              GitHub sync
         (PRs, governance)
```

Memory is completely separate from your project repos. Project repos contain code. Memory repos contain cognition. They live in different GitHub orgs and only touch the project repo through a single `.umx-project` marker — no `.umx/` directories cluttering your code history.

gitmem is the reference implementation of the **UMX specification**.
The public project is `gitmem`; the Python package and CLI remain `umx`.

## Install

The package/command name is currently `umx`:

```bash
pip install git+https://github.com/dev-boz/gitmem.git
```

Or for development:

```bash
git clone https://github.com/dev-boz/gitmem.git
cd gitmem
pip install -e ".[dev]"
```

## Quick start

```bash
# Initialize memory home
umx init

# Initialize a project
umx init-project --cwd /path/to/project

# Capture a session
umx capture codex --cwd /path/to/project
umx capture copilot --cwd /path/to/project

# Run the dream pipeline (extract, consolidate, lint, prune)
umx dream --cwd /path/to/project --force

# Search memory
umx search --cwd /path/to/project postgres

# Inject memory into a prompt
umx inject --cwd /path/to/project --prompt "postgres deploy flow"

# View facts
umx view --cwd /path/to/project --list
```

## Remote mode (experimental)

Requires `gh` CLI installed and authenticated.

```bash
# Bootstrap with GitHub org
umx init --org your-github-org --mode remote
umx init-project --cwd /path/to/project

# Dream pipeline opens PRs instead of direct-writing
umx dream --cwd /path/to/project --force
# → PR: [dream/l1] ... (#42)

# Sync sessions and facts
umx sync --cwd /path/to/project
```

### Mode comparison

| | `local` | `remote` | `hybrid` |
|---|---|---|---|
| Facts | direct write | PR only | PR only |
| Sessions | local | local | push to main |
| Governance | none | full (L1/L2/L3) | full (L1/L2/L3) |
| Offline | yes | no | partial |
| Best for | solo / offline | team / audit | team / fast capture |

## Features

- **Dream pipeline** — Orient, Gather, Consolidate, Lint, Prune — runs on free LLM API quota
- **Session capture** — `umx capture codex` / `umx capture copilot`, hooks, or MCP server
- **Budget-aware injection** — greedy-packs the most relevant facts into a token budget
- **Scope hierarchy** — user > tool > project > folder > file — facts injected at the most specific relevant level
- **Encoding strength 1-5** — ground truth code (S:5) to incidental mention (S:1), with composite scoring for trust, relevance, and retention
- **Provenance tracking** — every fact records extraction model, approval model, PR reference, and source sessions
- **Conflict resolution** — contradiction detection with `conflicts_with` pointers and supersession chains
- **FTS5 search** — full-text indexed fact search with optional semantic re-ranking
- **Attention refresh** — re-injects facts that have drifted too far from the active cursor in long sessions
- **Tombstones** — explicit forgetting mechanism that suppresses facts across future dream cycles
- **Procedures** — reusable playbooks and action rules, matched and injected at pre-tool time

## Tested with

The full capture, dream, search, inject loop has been dogfood-tested with:

- **Codex**
- **Claude Code**
- **Copilot CLI**
- **Gemini CLI**
- **OpenCode**

The local-mode loop is in daily use; remote and hybrid mode are included in alpha and now cover bootstrap, PR generation, and session sync.

## Roadmap

gitmem is releasing as alpha to get the core idea — governed, cross-tool, git-native AI memory — into the world. Here's where it's headed:

### Now (alpha)
- Local-mode dream pipeline (extract, consolidate, lint, prune)
- Session capture for Codex and Copilot CLI
- FTS5 search and budget-aware injection
- Encoding strength and composite scoring

### Next
- **Claude Code capture** — session hooks and MCP server integration
- **Read adapters** — generic CLI, hybrid gather across tools
- **Injection layer** — full hook/shim/MCP integration, pre-tool guidance, attention refresh, procedure matching, subagent relay
- **Extraction quality** — better prompts, golden-test harness, benchmark framework

### Then: GitHub governance (the big differentiator)
- **gitmem backend** — GitHub org bootstrap, push queue, PR pipeline
- **L1/L2/L3 governance** — cheap models propose (L1), SotA models review (L2), humans confirm (L3)
- **CONVENTIONS.md enforcement** — human-authored project schema drives extraction taxonomy
- **Audit trail** — session-to-fact traceability, deep therapy re-derivation
- **GitHub Actions** — workflow templates for automated dream cycles and lint PRs

### Later
- Web viewer with strength/scope/conflict filters, supersession timelines, session browser
- Cross-project dream and principle promotion
- Semantic re-ranking (optional embeddings, hybrid search)
- Schema migration tooling, signed commits, hypothesis branches
- `aip mem` integration and published spec for third-party adoption

## Spec

The full specification — memory model, encoding strength taxonomy, dream pipeline, governance tiers, injection architecture, and 19 cognitive science references — is in [gitmem-spec-v0_9.md](gitmem-spec-v0_9.md).

## Development

```bash
pytest -q

# Focused test suites
pytest -q tests/test_codex_capture.py tests/test_copilot_capture.py tests/test_golden_extraction.py
pytest -q tests/test_github_ops.py tests/test_governance.py
```

## License

MIT — see [LICENSE](LICENSE).
