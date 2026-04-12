# umx — Universal Memory Exchange
### Specification v0.5 · April 2026

> Tool-agnostic · Git-native · Zero infrastructure  
> Hierarchical scoped memory for any CLI agent  
> GitHub as source of truth · gitmem backend

---

## Table of Contents

1. [Problem Statement](#1-problem-statement)
2. [Design Principles](#2-design-principles)
3. [Architecture Overview](#3-architecture-overview)
4. [Memory Model](#4-memory-model)
5. [Encoding Strength](#5-encoding-strength)
6. [Composite Scoring](#6-composite-scoring)
7. [Scope Hierarchy](#7-scope-hierarchy)
8. [Memory File Format](#8-memory-file-format)
9. [Read Strategy](#9-read-strategy)
10. [Dream Pipeline](#10-dream-pipeline)
11. [GitHub Dream Governance (gitmem)](#11-github-dream-governance-gitmem)
12. [Injection Architecture](#12-injection-architecture)
13. [Context Budget](#13-context-budget)
14. [Git Strategy](#14-git-strategy)
15. [Session Logs](#15-session-logs)
16. [Search and Retrieval](#16-search-and-retrieval)
17. [Failure Modes](#17-failure-modes)
18. [Comparison](#18-comparison)
19. [Python Package Structure](#19-python-package-structure)
20. [Viewer / Editor](#20-viewer--editor)
21. [Roadmap](#21-roadmap)
22. [Non-Goals](#22-non-goals)
23. [Relation to AIP](#23-relation-to-aip)
24. [References](#24-references)

---

## 1  Problem Statement

Every AI coding CLI maintains its own isolated memory store. A fact learned by Claude Code about your project — *postgres runs on 5433, ignore CORS warnings in dev* — is invisible to Aider, Copilot, Gemini CLI, or any other tool you use on the same codebase. Switching tools means re-establishing context from scratch.

Beyond cross-tool isolation, existing memory systems share a deeper problem: **no auditability, no governance, no correction mechanism.** A cheap model extracts a fact incorrectly — that fact silently persists at full strength with no trace of where it came from or how to challenge it.

Existing solutions either require cloud infrastructure (Mem0, OneContext), are locked to a single tool (claude-mem, Copilot cross-agent memory), solve the chat-UI problem rather than the CLI-dev-workflow problem, or provide no audit trail.

**umx is a filesystem convention and injection protocol — not a service.** Any CLI that can read a file and execute a hook can participate. The gitmem backend adds GitHub as a durable, auditable source of truth with PR-based governance.

---

## 2  Design Principles

- **Git is the source of truth. Filesystem is the working copy.** Local `.umx/` directories are git worktrees. GitHub is canonical. If local and remote diverge, git merge resolves — local files never silently win.
- **Tool-agnostic by convention.** Tools adopt the spec; umx does not adopt tools.
- **Don't fight native memory systems.** Read what tools already write. Aggregate, don't replace.
- **Hierarchical scoping.** Memory is injected at the most specific relevant level, not dumped wholesale.
- **Encoding strength over flat confidence.** Facts carry a typed strength derived from how deliberately they were encoded, grounded in cognitive science taxonomy.
- **Facts are atomic.** The pipeline extracts, deduplicates, and prunes — it never merges facts into narratives. Narrative synthesis is a viewer concern, not a storage concern.
- **Raw sessions are immutable ground truth.** Session logs are never edited, never deleted. They are the audit baseline from which all derived memory can be verified or re-derived.
- **Tiered dream governance.** Cheap models propose. SotA models filter. Humans resolve ambiguity. No model auto-commits to main.
- **Provenance on every fact.** Every fact records the full chain: session → extraction model → approval model → PR.
- **Storage and presentation are the same layer.** Markdown is the source of truth. JSON is a derived cache. SQLite is a derived search index. Both are gitignored build artifacts, never committed.
- **Zero injection by default.** Nothing is added to context unless relevant to the current scope.

---

## 3  Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│  GitHub Memory Org (source of truth)                        │
│                                                             │
│  memory-org/user          ← ~/.umx/  (cloned)              │
│  memory-org/<project>     ← project/.umx/  (worktree)      │
│                                                             │
│  Each repo:                                                 │
│    sessions/   immutable raw logs (WAL)                     │
│    episodic/   dream-extracted facts (proposed via PR)      │
│    facts/      consolidated stable facts (reviewed/merged)  │
│    principles/ cross-session patterns (SotA/human gate)     │
│    meta/       index, dream log, schema version             │
└────────────────────┬────────────────────────────────────────┘
                     │ git pull/push
┌────────────────────▼────────────────────────────────────────┐
│  Local Worktree (~/.umx/ or project/.umx/)                  │
│                                                             │
│  Markdown files  (canonical format, human-editable)         │
│  SQLite index    (gitignored, rebuilt on pull)              │
└────────────────────┬────────────────────────────────────────┘
                     │ reads/writes
┌────────────────────▼────────────────────────────────────────┐
│  Agent Session                                              │
│  reads  → SQLite (fast) or raw sessions (brute force)       │
│  writes → local files → git commit → async push queue      │
└─────────────────────────────────────────────────────────────┘
```

### GitHub Org Layout

```
memory-org/
  user/                    ← user-global memory (~/.umx/)
  <project-slug>/          ← per-project memory (project/.umx/)
  <project-slug>/          ← one repo per project in main org
```

Project memory syncs automatically via `git push` on the project repo (`.umx/` is committed). User memory (`~/.umx/`) syncs via a post-dream push hook — same cadence, different repo.

---

## 4  Memory Model

umx grounds its memory taxonomy in established cognitive science rather than inventing new terms.

Endel Tulving's 1972 distinction between **episodic** and **semantic** memory [1], extended by Daniel Schacter's 1985 formalisation of **explicit** (declarative) vs **implicit** (non-declarative) memory [2], gives a well-validated framework for classifying how facts enter and persist in a memory system.

| Type | Cognitive science definition | umx equivalent |
|------|------------------------------|-----------------|
| **Explicit semantic** | Consciously encoded general facts, abstracted from the episode in which they were learned | Facts deliberately saved by an LLM or human — the tool *meant* to remember this |
| **Explicit episodic** | Consciously encoded facts tied to a specific event, session, or time | Facts extracted from a known session — we know *when* it was learned |
| **Implicit** | Encoded without conscious intent; influences behaviour through repeated exposure | Facts inferred from patterns across logs — the tool never explicitly saved this |

**Origin is a proxy for reliability.** A fact an LLM deliberately wrote to its memory store is more likely to be correct than one a background extractor scraped from a transcript, which is more likely than a pattern inferred from log frequency.

This maps onto Anderson's ACT-R **activation strength** model [3]: each memory unit has a numeric activation level that decays with time and strengthens with each retrieval or corroboration.

> *"Human recall is recursive — by re-encoding memories each time we retrieve them, strengthening some, discarding others."* [4]

When the same fact appears in both a tool's native memory store and an independently extracted transcript, it is re-encoded at higher strength. Corroboration across multiple tools strengthens it further.

---

## 5  Encoding Strength

Every fact carries an `encoding_strength` from 1–5 and a `memory_type` drawn from the cognitive taxonomy.

| Strength | Label | Memory type | Source | Git analogy |
|----------|-------|-------------|--------|-------------|
| **5** | Ground truth | Explicit semantic | Human manually confirmed | Signed tag / immutable release |
| **4** | Deliberate | Explicit semantic | Tool native memory (LLM intentionally wrote it) | Protected branch, CODEOWNERS reviewed |
| **3** | Extracted | Explicit episodic | Dream pipeline from session transcript | Merged to main |
| **2** | Inferred | Implicit | Repeated pattern across multiple logs | Unreviewed PR |
| **1** | Incidental | Implicit | Single transcript mention, unconfirmed | Uncommitted working tree |

### Strength mechanics

**Corroboration strengthens.** A fact at strength 3 that also appears in a tool's native memory store is promoted to 4. The same fact independently extracted by two different tools gains +1. Mirrors ACT-R base-level learning [3].

**Manual edit always wins.** If a user manually confirms or edits a corroborated fact, it is promoted to strength 5 regardless of current strength. Strength 5 is exclusively reserved for human-authored or human-confirmed facts — corroboration alone cannot reach it.

**PR approval tier maps to strength.** L1 (cheap model) PRs arrive at S:2–3. SotA approval promotes to S:4. Human confirmation elevates to S:5.

**Conflict resolution uses composite score** (see Section 6) — not strength alone.

**Prune threshold uses strength.** Facts below a configurable threshold (default: 1) are removed. Strength decays slowly with time if not corroborated — per Ebbinghaus's forgetting curve [5].

### Atomic fact rule

Facts must remain atomic. The Dream pipeline must not merge multiple facts into a single narrative statement.

**Disallowed:**
```
"We use MySQL (recently migrated from Postgres)"
```

**Allowed:**
```
- uses MySQL
- previously used Postgres
```

### Fact schema

```yaml
facts:
  - id: f_001
    text: "postgres runs on port 5433 in dev"
    scope: project
    topic: devenv
    encoding_strength: 4
    memory_type: explicit_semantic
    confidence: 0.97
    tags: [database, environment]
    source_tool: claude-code
    source_session: 2026-04-03T20:11Z
    corroborated_by: [aider]
    last_retrieved: 2026-04-04T09:00Z
    created: 2026-04-03T20:11Z
    last_referenced: 2026-04-04T09:00Z    # for TTL/decay
    provenance:
      extracted_by: groq/llama-3.3-70b
      approved_by: claude-sonnet-4
      approval_tier: l2-auto              # l1-proposed | l2-auto | l3-human
      pr: memory-org/myproject-memory#47
      sessions: [2026-04-03T20:11Z]       # source session(s)
```

> `encoding_strength` and `confidence` are orthogonal. Strength is *how deliberately* a fact was encoded. Confidence is *how certain* the extractor was about the text.

---

## 6  Composite Scoring

`encoding_strength` is the primary signal but not the only one. A composite score is used internally for conflict resolution, injection prioritisation, and pruning decisions.

```
fact_score =
  (w_s × encoding_strength)    # origin reliability
+ (w_c × confidence)           # extraction certainty
+ (w_r × recency)              # staleness penalty
+ (w_k × corroboration_count)  # independent agreement
```

Weights are configurable. Defaults require empirical tuning. The formula is fixed; the weights are not.

### Recency calculation

```
recency = exp(-λ × age_days)
```

Where `age_days` is days since `last_referenced` (falling back to `created`), and `λ` is the decay constant.

| λ value | Half-life | Use case |
|---------|-----------|----------|
| **0.023** | ~30 days | Default |
| **0.046** | ~15 days | Fast-moving projects |
| **0.010** | ~69 days | Long-lived reference projects |

**TTL metadata.** The `last_referenced` timestamp is updated on every retrieval. Facts at high encoding_strength resist decay; low-strength uncorroborated facts decay to pruning threshold within the configurable window.

### Relevance scoring for injection

```
relevance_score =
  (p_s × scope_proximity)     # file > folder > project > user
+ (p_k × keyword_overlap)     # token/phrase match with prompt or file path
+ (p_r × recent_retrieval)    # fact used recently in this session
+ (p_e × encoding_strength)   # higher strength biases inclusion
```

---

## 7  Scope Hierarchy

Memory is stored in `.umx/` directories at each level of the filesystem. Resolution mirrors `.gitignore`: walk up from CWD, most specific scope first.

| Scope | Path | Sync target | Always loaded |
|-------|------|-------------|---------------|
| **User** | `~/.umx/` | `memory-org/user` (own repo) | Yes |
| **Tool** | `~/.umx/tools/<n>.md` | Part of user repo | Yes |
| **Project (local)** | `<root>/.umx/local/` | Gitignored, never synced | Yes |
| **Project (team)** | `<root>/.umx/` | `memory-org/<project>` | Yes |
| **Folder** | `<dir>/.umx/` | Part of project repo | Lazy |
| **File** | `<dir>/.umx/files/<file>.md` | Part of project repo | Lazy |

### Directory layout (per repo)

```
.umx/
├── sessions/               # raw session logs — immutable, append-only
│   └── 2026/04/
│       └── 2026-04-08-abc123.jsonl
├── episodic/               # dream-extracted from sessions (proposed via PR)
│   └── 2026-04/
│       └── session-abc123.md
├── facts/                  # consolidated stable facts (reviewed/merged)
│   └── topics/
│       ├── devenv.md
│       └── architecture.md
├── principles/             # cross-session patterns (SotA/human gate required)
├── meta/
│   ├── MEMORY.md           # index — always loaded, max 200 lines / 25 KB
│   ├── conflicts.md        # open conflicts awaiting resolution
│   ├── dream.log           # last dream run stats
│   ├── dream.lock          # concurrency lock
│   ├── schema_version: 1   # format version for migration
│   └── NOTICE              # degradation alerts
└── local/                  # gitignored — private facts, local secrets
    ├── MEMORY.md
    └── topics/
        └── secrets.md
```

Standard `.gitignore` entry:
```
.umx/local/
.umx/dream.lock
.umx/dream.log
.umx/meta/NOTICE
*.umx.json
*.umx.sqlite
```

---

## 8  Memory File Format

### Single source of truth

Markdown is the **canonical storage format**. JSON is a derived cache. SQLite is a derived search index. Both are gitignored and rebuilt from markdown — never committed.

Each topic has:
- `facts/topics/devenv.md` — source of truth, human-editable
- `facts/topics/devenv.umx.json` — derived index (gitignored, fast machine access)

**The markdown file is always authoritative.** If JSON and markdown diverge, JSON is discarded and regenerated. One-way derivation, no reconciliation.

### Inline metadata in markdown

```markdown
## Facts
- [S:4] postgres runs on port 5433 in this dev env <!-- umx: {"id":"f_001","conf":0.97,"corroborated_by":["aider"],"pr":"#47"} -->
- [S:3] CORS warnings on /api/auth can be ignored in dev <!-- umx: {"id":"f_002","conf":0.88,"corroborated_by":[]} -->
- [S:2] `pytest -x` only; full suite takes 4 min <!-- umx: {"id":"f_003","conf":0.75,"corroborated_by":[]} -->
```

If a user adds a line without metadata, the parser assigns strength 5 and generates the block. If a user edits an existing line, the parser detects the change via the `id`, promotes to strength 5, and updates JSON.

### MEMORY.md (index layer)

```markdown
# umx memory index
scope: project
schema_version: 1
last_dream: 2026-04-03T22:14:00Z
session_count: 47

## Index
| Topic       | File                      | Updated    | Avg strength |
|-------------|---------------------------|------------|--------------|
| Database    | facts/topics/database.md  | 2026-04-03 | 4.2          |
| Auth system | facts/topics/auth.md      | 2026-04-01 | 3.1          |
| Dev env     | facts/topics/devenv.md    | 2026-04-03 | 3.8          |
```

**Size constraint:** `MEMORY.md` must stay under 200 lines / 25 KB. Enforced by Prune phase.

### Schema versioning

`schema_version` in `meta/` allows dream agents to detect old formats and migrate before processing. Increment on any breaking change to the fact schema or directory layout.

### Conflict file

```markdown
# Conflicts

## [OPEN] devenv · postgres port · f_001 vs f_009
- Fact A [f_001]: "postgres on 5433" — claude-code native (S:4, score:3.8, 2026-04-03)
- Fact B [f_009]: "postgres on 5432" — aider transcript (S:2, score:1.4, 2026-03-28)
- Sessions: f_001 from session abc123, f_009 from session def456
- Resolution: Fact A wins on score — pending user confirmation
```

---

## 9  Read Strategy

umx uses a **hybrid read** approach. Two tracks:

**Fast track** — SQLite FTS index for "what do I know about X" style lookups. Built from markdown files, gitignored, rebuilt on pull.

**Raw track** — direct agent access to `sessions/` JSONL for "what actually happened around X." Preserves full context, tone, reasoning, and back-and-forth that distillation loses.

```sql
-- SQLite schema (gitignored, rebuild artifact)
CREATE TABLE memories (
  id TEXT PRIMARY KEY,
  repo TEXT,
  scope TEXT,
  content TEXT,
  tags TEXT,              -- JSON array
  encoding_strength INTEGER,
  created_at TEXT,
  git_sha TEXT,           -- commit that introduced this fact
  pr TEXT                 -- PR that approved this fact
);

CREATE VIRTUAL TABLE memories_fts USING fts5(content, tags);
```

Agents query SQLite for fast retrieval. Raw sessions are always available for audit or brute-force context recovery.

### Source priority

```
Source                           → Encoding strength
────────────────────────────────────────────────────
Tool native memory               → 4 (explicit semantic)
  ~/.claude/projects/*/
  .aider.tags.cache, logs
  ~/.config/copilot/*
  ~/.gemini/*

Session transcripts / logs       → 2–3 (explicit episodic)
  AIP workspace/events.jsonl     → structured events (preferred)
  .umx/sessions/*.jsonl          → raw session archive

Inferred patterns                → 1–2 (implicit)
  Repeated mentions across N sessions without explicit save
```

### .gitignore-driven extraction safety

During Gather, the Dream pipeline parses `.gitignore` and converts rules to path-matching patterns. Facts referencing gitignored paths (`.env`, `secrets.json`, etc.) are auto-routed to `.umx/local/` rather than team memory.

---

## 10  Dream Pipeline

Runs after session end using free LLM API quota.

### Three-gate trigger

| Gate | Condition | Logic |
|------|-----------|-------|
| **Lock** | No concurrent dream (`.umx/dream.lock`) | **Required** |
| **Time** | 24 hours since last dream | Either/or |
| **Sessions** | 5+ sessions since last dream | Either/or |

```
trigger = NOT locked AND (time_elapsed ≥ 24h OR session_count ≥ 5)
```

### Four phases

| # | Phase | Action | Output |
|---|-------|--------|--------|
| 1 | **Orient** | Read `MEMORY.md`, list `.umx/` contents, check `schema_version`, skim topic files. | Current memory map |
| 2 | **Gather** | Read tool native memory (S:4). Parse `.gitignore` exclusions. Extract from sessions (S:2–3). Infer patterns (S:1–2). | Candidate fact list with strength + provenance |
| 3 | **Consolidate** | Merge candidates against existing facts. Apply corroboration bonus. Resolve conflicts by composite score; flag ties. Write atomic facts. **In local mode: direct write. In gitmem mode: commit to branch, open PR.** | Updated topic files or PR |
| 4 | **Prune** | Remove facts below threshold. Apply time decay (Ebbinghaus [5]). Deduplicate. Rebuild `MEMORY.md`. Enforce 200-line limit. | Pruned index |

### Pipeline constraints

The Dream pipeline may only:
- extract facts
- deduplicate facts
- reweight facts (composite score)
- prune facts
- normalise minor formatting (timestamps, whitespace)

It must **not**:
- rewrite facts semantically
- merge facts into narratives
- reinterpret meaning beyond extraction

### Dream mode config

```yaml
# ~/.umx/config.yaml
dream:
  mode: local       # local | remote | hybrid
  # local   = direct write to .umx/ files (default, offline-capable)
  # remote  = GitHub Actions, commits via PR only
  # hybrid  = local dream for immediate consolidation
  #           remote dream for cross-project clustering and audit
```

### Provider independence

```
Default: free-tier rotation (Cerebras → Groq/Kimi K2 → GLM-4.5 → MiniMax → OpenRouter)
Local:   Ollama or any OpenAI-compatible local endpoint
Paid:    any provider with API key
```

Native memory reads (Gather, S:4) require no LLM calls.

### Graceful degradation

| Stage | Condition | Behaviour |
|-------|-----------|-----------|
| **1** | Primary provider fails | Try next in rotation |
| **2** | All remote fail | Attempt local model if configured |
| **3** | No LLM | Native-only dream (S:4 only). Log skipped transcripts. |
| **4** | Native-only ran | Mark `partial` in `MEMORY.md`. Queue full dream next trigger. |

---

## 11  GitHub Dream Governance (gitmem)

The gitmem backend adds a tiered PR-based review pipeline on top of the Dream pipeline. It uses the GitHub org as a governance layer, mapping git primitives directly to memory quality control.

### The Refinery Pipeline

```
Raw Sessions (immutable)
    ↓
[L1 — Cheap Model]  runs constantly, high throughput
  → opens PRs: "extracted N facts from session abc123"
  → never merges its own PRs
  → one PR per dream cycle per repo (batched, not per-fact)
    ↓
[L2 — SotA Model]  runs nightly or on PR accumulation
  → reviews diffs against source sessions
  → approve  → auto-merge
  → reject   → close with reason (audit trail preserved)
  → escalate → label "human-review", leave comment
    ↓
[L3 — Human]  async, low friction
  → sees only escalated PRs via GitHub UI
  → no tooling required beyond normal GitHub review
```

### PR Format

L1 opens:
```
Title: [dream/l1] Extract facts from session 2026-04-08-abc123

episodic/2026-04/session-abc123.md  (new)
facts/topics/devenv.md              +3 lines

Source: session abc123
Confidence: 0.7
Encoding strength: 2–3
Proposed provenance: extracted_by groq/llama-3.3-70b
```

L2 reviews:
- reads the diff
- reads the source session (linked in PR body)
- checks for contradictions with existing `facts/`
- comments reasoning before approving/rejecting/escalating

### PR Label System

```
type: principle          # promotes to principles/ — escalate always
type: consolidation      # merges episodic → facts
type: deletion           # removes existing fact — escalate if S:≥3
type: promotion          # project → user scope
confidence: high/medium/low
impact: local/global
```

L2 auto-merges: `confidence:high` + `impact:local` + non-destructive  
L2 escalates: `impact:global` + principle rewrites + deletions of S:≥3 facts + contradictions

### Git Primitive Mapping

| Memory need | Git feature |
|-------------|-------------|
| Fact versioning | Commit history |
| Conflict detection | Merge conflicts |
| Rejected memories | Closed PRs (audit trail preserved) |
| Memory corrections | Amended PRs |
| Full audit trail | PR comments + reviews |
| Rollback | Revert commit |
| Human escalation | PR labels + assignees |
| Encoding strength 5 | Protected branch + CODEOWNERS |
| Encoding strength 4 | Merged to main, reviewed |
| Encoding strength 2 | Open PR, not yet reviewed |

### Agent Token Model

```
L1 dream agent    → PR write only, cannot merge
L2 filter agent   → merge on allowed label sets only
Indexer/search    → read-only
Human (org owner) → admin
```

Single PAT stored in `~/.umx/config` or env var. Agents never touch GitHub directly — they communicate via the local umx daemon which owns the token and push cadence.

### Pipeline Health Observability

The pipeline is observable from GitHub without extra tooling:
- High L1 rejection rate → cheap model prompt needs tuning
- High escalation rate → SotA model needs better context or the domain is genuinely ambiguous
- Old PRs sitting open → dream pipeline stalled
- PR volume per cycle → batching calibration

### Multi-Project Dream

```
L1 per-project dream
  → consolidates project sessions
  → extracts architecture decisions per repo

L2 cross-project dream (nightly, user repo)
  → reads across memory-org/*
  → extracts cross-project patterns
  → proposes promotions to memory-org/user
  → human gate required for cross-project promotions
```

---

## 12  Injection Architecture

| Injection point | Trigger | Format | Layers injected |
|-----------------|---------|--------|-----------------|
| **Session start** | Tool launch | Compressed block | User-global, tool-specific, project |
| **Each prompt** | User message content | Matched snippets | Folder layers, keyword-matched |
| **Post-tool hook** | File/command touched | Targeted note | File layer, folder layer |
| **File read append** | File read intercept | Inline annotation | File layer appended to file content |
| **Wrapper shim** | Tool startup (no hooks) | Prepend to config | Project + tool layer |

Facts ordered by `relevance_score` descending. Injection stops at budget. No partial facts.

### Tool coverage tiers

| Tier | Tools | Mechanism |
|------|-------|-----------|
| **1 — Native hooks** | Claude Code, Gemini CLI, Copilot, Cursor, Codex, Kiro | Full injection via hook API |
| **2 — Shim** | Aider, Amp, Vibe | Wrapper prepends memory to tool config at launch |
| **3 — MCP** | Any MCP-aware tool | `read_memory` / `write_memory` MCP tools |
| **4 — Manual** | Anything else | `aip hook emit` + wrapper |

### Legacy bridge

During Prune, umx optionally writes condensed facts into legacy files within bounded markers:

```markdown
<!-- umx-start: do not edit manually -->
- postgres runs on 5433
- ignore CORS on /api/auth in dev
<!-- umx-end -->
```

---

## 13  Context Budget

```bash
umx inject --cwd . --tool aider --max-tokens 4000
```

If `--max-tokens` is not specified, umx infers the budget from the tool adapter's known limit. Injection halts before exceeding the budget. No partial fact inclusion.

---

## 14  Git Strategy

`.umx/` files are updated frequently. To minimise merge conflicts:

- **One topic per file** — enforced by format. Never aggregate topics.
- **`local/` is gitignored** — personal facts never create team conflicts.
- **JSON and SQLite are derived, not merged** — git merges happen on markdown. JSON and SQLite are regenerated post-merge.
- **sessions/ is append-only** — new files only, no edits. Zero merge conflicts.

### Write path

```
agent writes fact
  → append to local markdown (immediate)
  → update SQLite (immediate)
  → git commit locally (immediate, offline-safe)
  → push queue (async, retried on failure)
```

Conflicts on push → daemon pulls, reruns dream consolidation locally, re-pushes.

### Merge rule

```
- Identical fact IDs        → merge metadata (take higher fact_score)
- Conflicting text same ID  → new conflict entry in conflicts.md
- Never silently overwrite a higher-strength fact
```

---

## 15  Session Logs

Raw session logs are stored in `sessions/` and are **immutable**. They are the ground truth from which all derived memory can be verified or re-derived.

```
sessions/
  2026/
    04/
      2026-04-08-abc123.jsonl
      2026-04-08-def456.jsonl
```

Each file is a JSONL stream — one JSON object per line:

```jsonl
{"ts":"2026-04-08T10:23:01Z","role":"user","content":"..."}
{"ts":"2026-04-08T10:23:04Z","role":"assistant","content":"..."}
{"ts":"2026-04-08T10:23:10Z","role":"tool_use","tool":"bash","input":"..."}
{"ts":"2026-04-08T10:23:11Z","role":"tool_result","content":"..."}
```

### Session log uses

1. **Audit baseline** — SotA model traces any extracted fact back to the source session during PR review
2. **Re-derivation** — if a dream model performed poorly over a period, re-run extraction against raw logs with a better model
3. **Brute-force retrieval** — agents can grep/scan sessions directly for queries requiring full context rather than distilled facts
4. **Memory health audit** — periodic SotA pass asking "given raw sessions, are these facts accurate and complete?"

### Audit pipeline

```
Raw sessions (never deleted)
    ↓  L1 cheap model, runs often
Episodic memories (proposed via PR)
    ↓  L2 SotA model, runs nightly
Consolidated facts (reviewed/merged)
    ↓  L3 SotA or human, runs occasionally
Audited/corrected facts (with provenance)
```

Each layer is checkable against the layer below. The SotA audit asks: *"given the raw sessions, are these facts accurate, complete, missing anything important?"*

---

## 16  Search and Retrieval

### Two tracks

**Fast track (SQLite FTS):** For "what do I know about X" queries. Built from markdown, gitignored, rebuilt on `git pull`.

**Raw track (direct session scan):** For "what actually happened around X" queries. Agents point directly at `sessions/*.jsonl` using grep or an LLM. Preserves full reasoning, tone, and back-and-forth that distillation loses.

### SQLite schema

```sql
CREATE TABLE memories (
  id TEXT PRIMARY KEY,
  scope TEXT,
  content TEXT,
  tags TEXT,
  encoding_strength INTEGER,
  created_at TEXT,
  last_referenced TEXT,
  git_sha TEXT,
  pr TEXT
);

CREATE VIRTUAL TABLE memories_fts USING fts5(content, tags);
```

SQLite is a **derived build artifact** — gitignored, always rebuildable from markdown. Treat it as cache, not store.

### Rebuild trigger

On `git pull`, if any `.umx/` markdown files changed (detected via `git diff`), rebuild SQLite index. Otherwise skip.

---

## 17  Failure Modes

| Failure | Cause | Mitigation |
|---------|-------|------------|
| **Incorrect high-strength fact** | Tool-native memory error | Composite scoring dilutes; user override → S:5 |
| **Extraction hallucination** | LLM misinterpretation | Low initial strength (1–3); decay + pruning; raw session always available for audit |
| **Over-injection** | Weak relevance filtering | Relevance scoring; strict budget enforcement |
| **Stale facts dominating** | High strength but outdated | Recency in composite score; time decay; TTL via `last_referenced` |
| **Metadata loss via manual edit** | User editing markdown directly | Parser regenerates on next pass; edited lines → S:5 |
| **Sensitive data in team memory** | Transcript references secrets | `.gitignore`-driven extraction exclusion; auto-route to `local/` |
| **Concurrent dream runs** | Multiple tools simultaneously | Lock file; one dream per 24h per project |
| **LLM providers unavailable** | All free tiers rate-limited | Graceful degradation to native-only dream; `NOTICE` surfaces at next session start |
| **PR volume spam** | L1 agents too aggressive | Batch: one PR per dream cycle per repo. Rate-limit L1. Require N-session evidence before principle proposals. |
| **Cognitive drift** | Unchecked L1 overwrites | L2 required before merge to `facts/`; L3 required for `principles/`; raw sessions always available to re-derive |
| **Binary diff problem** | SQLite committed to git | SQLite is gitignored. Rebuild from markdown. |
| **Schema migration** | Format change across hundreds of files | `schema_version` in `meta/`; dream agents check version before processing; migration scripts versioned in repo |

---

## 18  Comparison

| Tool | Cross-tool | Hierarchical | Git-native | Auto-extract | Audit trail | Encoding strength | PR governance | Free compute |
|------|-----------|-------------|-----------|-------------|-------------|-------------------|---------------|--------------|
| **umx + gitmem** | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| MemPalace | ✗ | ✓ | ✗ | ✓ | ✗ | ✗ | ✗ | ✓ |
| DiffMem | ✗ | ✗ | ✓ | ~ | ✗ | ✗ | ✗ | ~ |
| Mem0 | ~ | ✗ | ✗ | ✓ | ✗ | ~ | ✗ | ✗ |
| Copilot cross-agent | ✗ | ✗ | ✗ | ✓ | ✗ | ✗ | ✗ | ✗ |
| claude-mem | ✗ | ✗ | ✗ | ✓ | ✗ | ✗ | ✗ | ✗ |
| CLAUDE.md hierarchy | ✗ | ✓ | ✓ | ✗ | ✗ | ✗ | ✗ | ✓ |

`✓` fully supported · `~` partial · `✗` not supported

**Unique to umx + gitmem:** The combination of cross-tool scope hierarchy, encoding-strength provenance, raw session WAL, and PR-based tiered governance does not exist in any surveyed system. MemPalace solves retrieval well but has no write governance, no audit trail, and no correction mechanism. DiffMem uses git but has no dream pipeline or review model.

---

## 19  Python Package Structure

```
umx/
├── __init__.py
├── cli.py                  # `umx` and `aip mem` subcommands
├── scope.py                # scope hierarchy + local/ split
├── memory.py               # read/write MEMORY.md + topic files
├── strength.py             # encoding strength + composite scoring
├── inject.py               # injection point handlers + relevance scoring
├── budget.py               # context budget inference + enforcement
├── sessions.py             # session log write + JSONL format
├── search.py               # SQLite FTS index build + query
├── adapters/               # native memory read adapters
│   ├── claude_code.py
│   ├── aider.py
│   ├── copilot.py
│   └── generic.py
├── dream/
│   ├── pipeline.py         # Orient → Gather → Consolidate → Prune
│   ├── gates.py            # three-gate trigger + lock file
│   ├── extract.py          # LLM extraction prompt + fact schema
│   ├── gitignore.py        # .gitignore parsing → exclusion rules
│   ├── conflict.py         # conflict detection + score-based resolution
│   ├── providers.py        # provider rotation + local fallback chain
│   ├── decay.py            # exponential recency decay
│   └── notice.py           # .umx/NOTICE writer for degradation alerts
├── gitmem/                 # GitHub backend
│   ├── sync.py             # push queue, pull on session start
│   ├── pr.py               # open/review/merge PRs via GitHub API
│   ├── governance.py       # L1/L2/L3 tier logic
│   ├── audit.py            # session → fact audit pipeline
│   └── org.py              # org layout, repo naming, token management
├── hooks/
│   ├── session_start.py
│   ├── post_tool_use.py
│   └── session_end.py
├── shim/
│   ├── aider.py
│   └── generic.py
├── bridge.py               # legacy file bridge (CLAUDE.md / AGENTS.md markers)
├── viewer/
│   └── server.py           # local web viewer
└── mcp_server.py           # read_memory / write_memory MCP tools
```

### CLI surface

```bash
umx inject    --cwd . --tool aider [--max-tokens N]
umx collect   --cwd . --tool aider
umx dream     --cwd . [--force] [--mode local|remote|hybrid]
umx view      --cwd . [--scope project] [--min-strength N]
umx tui       --cwd .
umx status    --cwd .
umx conflicts --cwd .
umx forget    --cwd . --topic devenv
umx promote   --cwd . --fact f_001 --to project
umx merge     --cwd .
umx audit     --cwd . --session 2026-04-08-abc123  # re-derive from raw session
umx sync      # push/pull memory repos
```

---

## 20  Viewer / Editor

Local web UI. `umx view`. No persistent process.

| Feature | Description |
|---------|-------------|
| **Memory tree** | Full scope hierarchy for CWD |
| **Fact view** | Source tool, session, `encoding_strength`, `memory_type`, `fact_score`, `provenance`, PR link |
| **Inline edit** | Edit any fact → promoted to S:5 on save |
| **Promote / demote** | Move fact to higher or lower scope |
| **Conflict panel** | Flagged conflicts side-by-side with scores |
| **Strength filter** | Show only facts at or above a given strength |
| **Dream log** | Last dream: phases, facts added/removed/conflicted, provider, tokens consumed |
| **Session browser** | Browse raw session logs by date/project |
| **Audit view** | Trace any fact back to its source session and approval PR |
| **Narrative view** | Optional synthesis of related atomic facts into readable prose. Presentation only. |

---

## 21  Roadmap

| Phase | Milestone | Deliverables |
|-------|-----------|--------------|
| **0** | Foundation | Scope spec · file format · fact schema · conflict format · `local/` split · `schema_version` |
| **1** | Core library | `scope.py` + `memory.py` + `strength.py` · composite scoring · AIP hook integration · session log write |
| **2** | Dream pipeline | Orient → Gather → Consolidate → Prune · 3-gate trigger · provider rotation · `.gitignore` safety · TTL metadata |
| **3** | Read adapters | Claude Code · Aider · generic · hybrid gather · corroboration bonus · SQLite FTS index |
| **4** | Injection layer | Tier 1 hooks · Tier 2 shims · Tier 3 MCP · budget enforcement · relevance scoring · legacy bridge |
| **5** | gitmem backend | GitHub org layout · push queue · PR pipeline · L1/L2/L3 governance · audit trail · `umx sync` |
| **6** | Viewer / editor | Web viewer · strength/scope filters · conflict UI · session browser · audit view · dream log · TUI |
| **7** | Hardening | `umx merge` · schema migration tooling · worktree scope · time decay tuning · cross-project dream |
| **8** | Ecosystem | `aip mem` integration · published spec · `AGENTS.md` / `CLAUDE.md` sync bridge · third-party adoption |

---

## 22  Non-Goals

- **No cloud-only sync.** GitHub is the remote; local is always a functional working copy. Offline-capable by default.
- **No vector search in v1.** SQLite FTS covers the common case. Embeddings are a future opt-in.
- **No pane-read mid-stream injection.** Too risky as default. Future opt-in.
- **No auto-injection of sensitive data.** `.gitignore` exclusion enforced in Gather.
- **No narrative merging in storage.** Facts are atomic. Narrative synthesis is viewer-only.
- **No opinions on which tool you use.** umx works identically with one CLI or ten.
- **No auto-commit to main.** In gitmem mode, all dream writes go through PR review — no model writes directly to `main`.

---

## 23  Relation to AIP

umx is a natural extension of AIP. AIP provides the orchestration substrate (tmux + filesystem event bus + hook normalisation). umx adds the memory layer on top.

- AIP hook proxy normalises payloads from all Tier 1 CLIs. umx hook handlers consume those events.
- AIP shim watch provides lifecycle events for Tier 2 CLIs. umx shim handles collection.
- `workspace/events.jsonl` feeds the Gather phase as a structured session source (preferred over raw transcripts).
- umx ships as `aip mem` subcommands alongside its standalone CLI.
- gitmem's GitHub org is separate from the project org — memory governance is isolated from code governance.

**Boundary:** AIP owns orchestration and inter-agent communication. umx owns memory scoping, extraction, strength, injection, and governance. AIP emits events; umx consumes them, writes memory files, and manages their lifecycle through to GitHub.

---

## 24  References

[1] Tulving, E. (1972). *Episodic and semantic memory.* In E. Tulving & W. Donaldson (Eds.), Organisation of Memory. Academic Press.

[2] Schacter, D. L. (1987). *Implicit memory: History and current status.* Journal of Experimental Psychology: Learning, Memory, and Cognition, 13(3), 501–518.

[3] Anderson, J. R. (1983). *The Architecture of Cognition.* Harvard University Press. — ACT-R base-level learning: retrieval raises activation; unused memories decay.

[4] The New Stack (2026, January 16). *Memory for AI Agents: A New Paradigm of Context Engineering.*

[5] Ebbinghaus, H. (1885). *Über das Gedächtnis.* — Forgetting curve: basis for time decay on uncorroborated low-strength facts in Prune phase.

[6] Observed patterns in production memory systems (2025–2026). Claude Code autoDream, Cursor memory layer, Windsurf context engine. umx adopts the phased pipeline architecture, extending with cross-tool scope hierarchy, encoding strength, and hybrid read strategy.

[7] Mnemoverse Documentation (2025). *Production Memory Systems: Implementation Analysis.* — Notes that origin-based encoding strength (deliberate vs incidental) is not implemented in any surveyed production system.

---

## Open Questions

- **Composite score weights** — require empirical tuning against real session data. All weights exposed as config initially.
- **Time decay λ tuning** — default λ 0.023 (~30 day half-life) is a starting point. Projects with daily deploys may need faster decay.
- **Extraction prompt design** — the Gather phase LLM prompt is the most implementation-critical piece not yet specified. Key decisions: input format normalisation, confidence calibration, detecting stated vs questioned facts.
- **Copilot / Gemini native memory formats** — adapters cannot be written until formats are documented or reverse-engineered.
- **Scope promotion heuristics** — if the same fact appears in 3+ folder-level memories independently, auto-promote to project? Threshold TBD.
- **L1 rate limiting** — how many PRs per dream cycle before batching kicks in? How to prevent PR spam without losing extraction fidelity?
- **Cross-project dream cadence** — nightly cross-project L2 scan across all repos in memory org. What triggers promotion from project → user scope? Evidence threshold TBD.
- **Session log retention** — sessions/ is append-only and never deleted. Long-running systems will accumulate large archives. Compression strategy for old sessions TBD (gzip monthly, keep index).

---

*umx is part of the AIP ecosystem — [github.com/dev-boz/agent-interface-protocol](https://github.com/dev-boz/agent-interface-protocol)*
