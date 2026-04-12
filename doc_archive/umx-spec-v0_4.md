# umx — Universal Memory Exchange
### Specification v0.4 · April 2026

> Tool-agnostic · Filesystem-native · Zero infrastructure  
> Hierarchical scoped memory for any CLI agent

---

## Table of Contents

1. [Problem Statement](#1-problem-statement)
2. [Design Principles](#2-design-principles)
3. [Memory Model](#3-memory-model)
4. [Encoding Strength](#4-encoding-strength)
5. [Composite Scoring](#5-composite-scoring)
6. [Scope Hierarchy](#6-scope-hierarchy)
7. [Memory File Format](#7-memory-file-format)
8. [Read Strategy](#8-read-strategy)
9. [Dream Pipeline](#9-dream-pipeline)
10. [Injection Architecture](#10-injection-architecture)
11. [Context Budget](#11-context-budget)
12. [Git Strategy](#12-git-strategy)
13. [Failure Modes](#13-failure-modes)
14. [Comparison](#14-comparison)
15. [Python Package Structure](#15-python-package-structure)
16. [Viewer / Editor](#16-viewer--editor)
17. [Roadmap](#17-roadmap)
18. [Non-Goals](#18-non-goals)
19. [Relation to AIP](#19-relation-to-aip)
20. [References](#20-references)

---

## 1  Problem Statement

Every AI coding CLI maintains its own isolated memory store. A fact learned by Claude Code about your project — *postgres runs on 5433, ignore CORS warnings in dev* — is invisible to Aider, Copilot, Gemini CLI, or any other tool you use on the same codebase. Switching tools means re-establishing context from scratch.

Existing solutions either require cloud infrastructure (Mem0, OneContext), are locked to a single tool (claude-mem, Copilot cross-agent memory), or solve the chat-UI problem rather than the CLI-dev-workflow problem.

**umx is a filesystem convention and injection protocol — not a service.** Any CLI that can read a file and execute a hook can participate.

---

## 2  Design Principles

- **Filesystem is the source of truth.** No database, no daemon, no cloud dependency.
- **Tool-agnostic by convention.** Tools adopt the spec; umx does not adopt tools.
- **Don't fight native memory systems.** Read what tools already write. Aggregate, don't replace.
- **Hierarchical scoping.** Memory is injected at the most specific relevant level, not dumped wholesale.
- **Encoding strength over flat confidence.** Facts carry a typed strength derived from how deliberately they were encoded, grounded in cognitive science taxonomy.
- **Facts are atomic.** The pipeline extracts, deduplicates, and prunes — it never merges facts into narratives. Narrative synthesis is a viewer concern, not a storage concern.
- **Storage and presentation are the same layer.** Markdown is the source of truth. JSON is a derived cache for fast machine access. One direction, no reconciliation.
- **Auto-extraction via free LLM tiers.** Session content is distilled automatically. Fine-tuning available via viewer.
- **Dream pipeline.** Based on converging patterns observed across multiple production memory systems (Claude Code, Cursor, Windsurf): Orient → Gather → Consolidate → Prune.
- **Zero injection by default.** Nothing is added to context unless relevant to the current scope.

---

## 3  Memory Model

umx grounds its memory taxonomy in established cognitive science rather than inventing new terms.

Endel Tulving's 1972 distinction between **episodic** and **semantic** memory [1], extended by Daniel Schacter's 1985 formalisation of **explicit** (declarative) vs **implicit** (non-declarative) memory [2], gives a well-validated framework for classifying how facts enter and persist in a memory system.

| Type | Cognitive science definition | umx equivalent |
|------|------------------------------|-----------------|
| **Explicit semantic** | Consciously encoded general facts, abstracted from the episode in which they were learned | Facts deliberately saved by an LLM or human — the tool *meant* to remember this |
| **Explicit episodic** | Consciously encoded facts tied to a specific event, session, or time | Facts extracted from a known session — we know *when* it was learned |
| **Implicit** | Encoded without conscious intent; influences behaviour through repeated exposure | Facts inferred from patterns across logs — the tool never explicitly saved this |

**Origin is a proxy for reliability.** A fact an LLM deliberately wrote to its memory store is more likely to be correct than one a background extractor scraped from a transcript, which is more likely than a pattern inferred from log frequency.

This maps onto Anderson's ACT-R **activation strength** model [3]: each memory unit has a numeric activation level that decays with time and strengthens with each retrieval or corroboration. umx uses this as its `encoding_strength` field.

> *"Human recall is recursive — by re-encoding memories each time we retrieve them, strengthening some, discarding others."* [4]

When the same fact appears in both a tool's native memory store and an independently extracted transcript, it is re-encoded at higher strength. Corroboration across multiple tools strengthens it further.

---

## 4  Encoding Strength

Every fact carries an `encoding_strength` from 1–5 and a `memory_type` drawn from the cognitive taxonomy.

| Strength | Label | Memory type | Source | Reliability |
|----------|-------|-------------|--------|-------------|
| **5** | Ground truth | Explicit semantic | User manually edited in viewer | Authoritative |
| **4** | Deliberate | Explicit semantic | Tool native memory (LLM intentionally wrote it) | High |
| **3** | Extracted | Explicit episodic | Dream pipeline from session transcript | Medium |
| **2** | Inferred | Implicit | Repeated pattern across multiple logs | Low-medium |
| **1** | Incidental | Implicit | Single transcript mention, unconfirmed | Low |

### Strength mechanics

**Corroboration strengthens.** A fact at strength 3 that also appears in a tool's native memory store is promoted to 4. The same fact independently extracted by two different tools gains +1. Mirrors ACT-R base-level learning [3].

**Manual edit always wins.** If a user manually confirms or edits a corroborated fact, it is promoted to strength 5 regardless of its current strength. Strength 5 is exclusively reserved for human-authored or human-confirmed facts — corroboration alone cannot reach it.

**Conflict resolution uses composite score** (see Section 5) — not strength alone.

**Injection priority uses composite score** — lowest score dropped first when context budget is exhausted.

**Prune threshold uses strength.** Facts below a configurable threshold (default: 1) are removed. Strength decays slowly with time if not corroborated — per Ebbinghaus's forgetting curve [5].

**Manual edits promote to strength 5.** If a user edits a fact directly in the markdown file, the parser detects changed text and promotes that fact to strength 5 (ground truth) on the next pass.

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

Rationale: atomic facts preserve traceability, enable independent decay and pruning, and keep storage deterministic. Narrative synthesis, if desired, happens at the viewer layer only.

### Fact schema

```yaml
facts:
  - id: f_001
    text: "postgres runs on port 5433 in dev"
    scope: project
    topic: devenv
    encoding_strength: 4          # deliberate — from claude-code native memory
    memory_type: explicit_semantic
    confidence: 0.97              # extractor certainty about the text
    tags: [database, environment]
    source_tool: claude-code
    source_session: 2026-04-03T20:11Z
    corroborated_by: [aider]
    last_retrieved: 2026-04-04T09:00Z
    created: 2026-04-03T20:11Z

  - id: f_002
    text: "CORS warnings on /api/auth ignored in dev"
    scope: project
    topic: devenv
    encoding_strength: 3          # extracted — from transcript
    memory_type: explicit_episodic
    confidence: 0.88
    tags: [api, environment]
    source_tool: aider
    source_session: 2026-04-01T14:33Z
    corroborated_by: []
    last_retrieved: null
    created: 2026-04-01T14:33Z
```

> `encoding_strength` and `confidence` are orthogonal. Strength is *how deliberately* a fact was encoded. Confidence is *how certain* the extractor was about the text. A fact can be high-strength but low-confidence, or low-strength but high-confidence.

---

## 5  Composite Scoring

`encoding_strength` is the primary signal but not the only one. A high-strength fact may be stale; a low-strength fact may be highly corroborated. A composite score is used internally for conflict resolution, injection prioritisation, and pruning decisions.

```
fact_score =
  (w_s × encoding_strength)    # origin reliability
+ (w_c × confidence)           # extraction certainty
+ (w_r × recency)              # staleness penalty
+ (w_k × corroboration_count)  # independent agreement
```

Weights are configurable. Defaults are intentionally not specified in the spec — they require empirical tuning against real session data before hardcoding. The formula is fixed; the weights are not.

### Recency calculation

Recency is a normalised 0–1 score using exponential decay:

```
recency = exp(-λ × age_days)
```

Where `age_days` is the number of days since the fact was last retrieved (or created, if never retrieved), and `λ` is the decay constant. Higher `λ` means faster decay.

| λ value | Half-life | Use case |
|---------|-----------|----------|
| **0.023** | ~30 days | Default — slow decay, suits most project work |
| **0.046** | ~15 days | Fast-moving projects with frequent context shifts |
| **0.010** | ~69 days | Long-lived reference projects with stable facts |

`λ` is configurable per scope. Project-level config in `.umx/config.yaml`:

```yaml
decay_lambda: 0.023   # default, ~30 day half-life
```

**Examples at default λ:**
| Age | Recency score |
|-----|---------------|
| 0 days (today) | 1.00 |
| 7 days | 0.85 |
| 30 days | 0.50 |
| 90 days | 0.13 |
| 180 days | 0.02 |

> Recency uses `last_retrieved` if available, falling back to `created`. This means actively used facts resist decay regardless of age — consistent with ACT-R's retrieval-strengthening model [3].

**Usage:**
- Conflicts resolve by highest `fact_score`, not strength alone
- Injection drops lowest `fact_score` first when constrained
- Prune phase considers both raw strength and score

### Relevance scoring for injection

A separate relevance score determines *which* facts are candidates for injection at a given point:

```
relevance_score =
  (p_s × scope_proximity)     # file > folder > project > user
+ (p_k × keyword_overlap)     # token/phrase match with prompt or file path
+ (p_r × recent_retrieval)    # fact used recently in this session
+ (p_e × encoding_strength)   # higher strength biases inclusion
```

Facts are included in descending `relevance_score` order. Injection stops when context budget is reached. Low-relevance facts are excluded entirely — not truncated mid-fact.

---

## 6  Scope Hierarchy

Memory is stored in `.umx/` directories at each level of the filesystem. Resolution mirrors `.gitignore` and `.editorconfig`: walk up from CWD, most specific scope first.

| Scope | Path | Injection trigger | Typical content | Always loaded |
|-------|------|-------------------|-----------------|---------------|
| **User** | `~/.umx/` | Every session start | Preferences, style, cross-project facts | Yes |
| **Tool** | `~/.umx/tools/<n>.md` | Tool identity at launch | Tool-specific flags, known bugs | Yes |
| **Project (local)** | `<root>/.umx/local/` | CWD inside project | Private facts, local secrets, personal workflow | Yes (gitignored) |
| **Project (team)** | `<root>/.umx/` | CWD inside project | Architecture, conventions, env facts, decisions | Yes (committed) |
| **Folder** | `<dir>/.umx/` | First file access in dir | Module context, API contracts | Lazy |
| **File** | `<dir>/.umx/files/<file>.md` | File read by agent | File-level annotations, gotchas | Lazy |

### Public / private split

The `local/` subdirectory is gitignored by default. It follows the same internal structure as the team directory but never leaves the machine.

```
/project/.umx/
├── MEMORY.md              # committed — team knowledge
├── config.yaml            # committed — project-level settings (decay λ, budget)
├── topics/
│   └── devenv.md          # committed — shared facts
├── files/
│   └── auth.py.md         # committed — file-level annotations
└── local/
    ├── MEMORY.md          # gitignored — private memory
    └── topics/
        └── secrets.md     # gitignored — local tokens, personal quirks
```

The standard `.gitignore` entry:
```
.umx/local/
.umx/dream.lock
.umx/dream.log
.umx/NOTICE
*.umx.json
```

### Resolution order (full)

```
~/.umx/                       # 1. user-global
~/.umx/tools/aider.md         # 2. tool-specific
/project/.umx/local/          # 3. project-local (private, higher priority)
/project/.umx/                # 4. project-team (committed)
/project/src/.umx/            # 5. folder (lazy)
/project/src/.umx/files/auth.py.md  # 6. file (lazy, inside folder .umx/)
```

> **Note:** File-level memory lives inside the nearest `.umx/files/` directory rather than as sidecars next to the source file. This keeps the working tree clean — no `.umx.md` files cluttering directory listings. Function-level memory is folded into the file layer. No separate function scope.

---

## 7  Memory File Format

### 7.1  Single source of truth

Markdown is the **canonical storage format**. JSON is derived, not authoritative.

Each topic has two files:
- `topics/devenv.md` — the source of truth, human-readable and directly editable
- `topics/devenv.umx.json` — derived index, regenerated from the markdown on every dream pass

**The markdown file is always authoritative.** If the JSON and markdown diverge, the JSON is discarded and regenerated. This eliminates an entire class of reconciliation bugs — there is no two-way sync, only one-way derivation.

The JSON exists solely to provide fast machine access without parsing inline HTML comments. It is a cache, not a store. Any tool that writes facts must write them to the markdown file; the pipeline regenerates the JSON.

If a user adds a line to the markdown without metadata, the parser assigns strength 5 (ground truth) and generates the inline metadata block. If a user edits the text of an existing line, the parser detects the change via the `id` in the inline metadata, promotes the fact to strength 5, and regenerates the JSON.

### 7.2  Inline metadata in markdown

Facts in the markdown file carry inline metadata in HTML comments. This survives manual edits and allows round-tripping without requiring users to touch JSON directly.

```markdown
## Facts
- [S:4] postgres runs on port 5433 in this dev env <!-- umx: {"id":"f_001","conf":0.97,"corroborated_by":["aider"]} -->
- [S:3] CORS warnings on /api/auth can be ignored in dev <!-- umx: {"id":"f_002","conf":0.88,"corroborated_by":[]} -->
- [S:2] `pytest -x` only; full suite takes 4 min <!-- umx: {"id":"f_003","conf":0.75,"corroborated_by":[]} -->
```

If a user adds a line without metadata, the parser assigns strength 5 and generates the block. If a user edits the text of an existing line, the parser detects the change, promotes to strength 5, and updates the JSON.

### 7.3  MEMORY.md  (index layer)

Always loaded when scope is active. Stores pointers, not data.

```markdown
# umx memory index
scope: project
last_dream: 2026-04-03T22:14:00Z
session_count: 47

## Index
| Topic       | File                | Updated    | Avg strength |
|-------------|---------------------|------------|--------------|
| Database    | topics/database.md  | 2026-04-03 | 4.2          |
| Auth system | topics/auth.md      | 2026-04-01 | 3.1          |
| Dev env     | topics/devenv.md    | 2026-04-03 | 3.8          |
```

**Size constraint:** `MEMORY.md` must stay under 200 lines / 25 KB. Enforced by Prune phase.

### 7.4  Conflict file

```markdown
# Conflicts

## [OPEN] devenv · postgres port · f_001 vs f_009
- Fact A [f_001]: "postgres on 5433" — claude-code native (S:4, score:3.8, 2026-04-03)
- Fact B [f_009]: "postgres on 5432" — aider transcript (S:2, score:1.4, 2026-03-28)
- Resolution: Fact A wins on score — pending user confirmation
- Override: edit viewer to confirm or swap
```

---

## 8  Read Strategy

umx uses a **hybrid read** approach. Native tool memory and session transcripts are complementary sources at different encoding strengths.

### Source priority

```
Source                           → Encoding strength
────────────────────────────────────────────────────
Tool native memory               → 4 (explicit semantic)
  ~/.claude/projects/*/          → claude-code auto-memory
  .aider.tags.cache, logs        → aider
  ~/.config/copilot/*            → copilot
  ~/.gemini/*                    → gemini

Session transcripts / logs       → 2–3 (explicit episodic)
  AIP workspace/events.jsonl     → structured events (preferred)
  AIP workspace/summaries/*.md   → agent summaries
  Raw session logs               → per-tool adapters

Inferred patterns                → 1–2 (implicit)
  Repeated mentions across N sessions without explicit save
```

### .gitignore-driven extraction safety

During the Gather phase, the Dream pipeline parses the project's `.gitignore` and converts rules to path-matching patterns. If a transcript or session log heavily references paths matching `.gitignore` patterns (`.env`, `secrets.json`, `node_modules/`), facts from those interactions are not extracted into team memory.

If such a fact must be saved (e.g. explicit native tool save), it is automatically routed to `.umx/local/` rather than `.umx/topics/`.

### Native memory adapters

| Tool | Native memory location | Notes |
|------|----------------------|-------|
| Claude Code | `~/.claude/projects/<path>/` | Walk on SessionEnd |
| Aider | `.aider.tags.cache`, session logs | Adapter needed |
| Copilot | `~/.config/copilot/` | Format TBD |
| Gemini CLI | `~/.gemini/` | Format TBD |

Adapters normalise to the umx fact schema, assigning `encoding_strength: 4` and `memory_type: explicit_semantic`.

### Corroboration bonus

When the same fact appears in both native memory and transcript:
- `encoding_strength` promoted (+1, capped at 4)
- `corroborated_by` field updated
- `confidence` averaged across sources

Per ACT-R base-level learning: each independent "retrieval" of a fact raises its activation [3].

---

## 9  Dream Pipeline

Runs after session end using free LLM API quota. Based on converging patterns observed across multiple production memory systems [6].

### 9.1  Three-gate trigger

The lock gate is mandatory. Either the time or session gate (or both) must also be satisfied.

| Gate | Condition | Logic |
|------|-----------|-------|
| **Lock** | No concurrent dream (`.umx/dream.lock`) | **Required** |
| **Time** | 24 hours since last dream | Either/or |
| **Sessions** | 5+ sessions since last dream | Either/or |

```
trigger = NOT locked AND (time_elapsed ≥ 24h OR session_count ≥ 5)
```

This ensures users with few long sessions (time gate) and users with many short sessions (session gate) both trigger dreams. The `--force` flag on `umx dream` bypasses both the time and session gates but still respects the lock.

### 9.2  Four phases

| # | Phase | Action | Output |
|---|-------|--------|--------|
| 1 | **Orient** | Read `MEMORY.md`, list `.umx/` contents, skim topic files. Establish baseline. | Current memory map |
| 2 | **Gather** | Read tool native memory (S:4, no LLM needed). Parse `.gitignore` for exclusion patterns. Extract from session transcripts/AIP event log (S:2–3). Infer patterns from repeated mentions (S:1–2). | Candidate fact list with strength assignments |
| 3 | **Consolidate** | Merge candidates against existing facts using composite score. Apply corroboration bonus. Resolve conflicts by score; flag ties. Convert relative timestamps to absolute. Write atomic facts to topic files. Route gitignored facts to `local/`. **Read-only bash. Writes only to `.umx/`.** | Updated topic files, conflicts.md |
| 4 | **Prune** | Remove facts below strength threshold. Apply time decay to uncorroborated low-strength facts (per Ebbinghaus [5]). Deduplicate. Rebuild `MEMORY.md` index. Enforce 200-line / 25 KB limit. | Pruned `MEMORY.md` |

### 9.3  Pipeline constraints

Many existing memory systems (Mem0, mem-mesh, mcp-memory-service) use LLMs during consolidation to rewrite, summarise, and merge facts into condensed narratives. This improves readability but introduces non-determinism, provenance loss, and semantic drift — a rewritten fact can no longer be traced to its original source or independently verified.

umx takes the opposite approach. The Dream pipeline may only:
- extract facts
- deduplicate facts
- reweight facts (composite score)
- prune facts
- normalise minor formatting (timestamps, whitespace)

It must **not**:
- rewrite facts semantically
- merge facts into narratives
- reinterpret meaning beyond extraction

The Consolidate phase sees a previous "Postgres on 5433" and a new "switched to MySQL" as two separate atomic facts — not a merge candidate. Decay handles the history naturally over subsequent dream cycles. Narrative synthesis, if desired, is a viewer-layer concern (see Section 16).

### 9.4  Provider independence and fallback

The pipeline is provider-agnostic. Modes:
- **Default:** free-tier rotation (Cerebras → Groq/Kimi K2 → GLM-4.5 → MiniMax → OpenRouter)
- **Local:** Ollama or any OpenAI-compatible local endpoint (configured in `~/.umx/config.yaml`)
- **Paid:** any provider with an API key set

No provider-specific assumptions in pipeline logic. One client, swap base URL.

> Native memory reads (Gather phase, strength 4 sources) require no LLM calls. LLM is only used for transcript extraction.

### 9.5  Graceful degradation

When all configured LLM providers are unavailable (rate-limited, down, or unreachable), the pipeline degrades in stages:

| Stage | Condition | Behaviour |
|-------|-----------|-----------|
| **1 — Provider rotation** | Primary provider fails | Try next provider in rotation |
| **2 — Local fallback** | All remote providers fail | Attempt local model if configured |
| **3 — Native-only dream** | No LLM available at all | Run Gather for native memory reads only (S:4). Skip transcript extraction. Consolidate and Prune on existing facts. Log skipped sources. |
| **4 — Deferred** | Native-only ran but transcripts are pending | Mark dream as `partial` in `MEMORY.md`. Queue full dream for next trigger. |

### 9.6  User notification

The pipeline writes status to `.umx/dream.log` after every run. When degradation occurs, it also writes a one-line notice to `.umx/NOTICE`:

```
[2026-04-04T10:30Z] Dream ran in native-only mode — all LLM providers unavailable. 3 session transcripts pending extraction. Run `umx dream --force` to retry.
```

Tools that support it surface this notice at session start. The notice file is cleared on the next successful full dream.

---

## 10  Injection Architecture

| Injection point | Trigger | Format | Layers injected |
|-----------------|---------|--------|-----------------|
| **Session start** | Tool launch | Compressed block | User-global, tool-specific, project |
| **Each prompt** | User message content | Matched snippets | Folder layers, keyword-matched |
| **Post-tool hook** | File/command touched | Targeted note | File layer, folder layer for touched path |
| **File read append** | File read intercept | Inline annotation | File layer appended to file content |
| **Wrapper shim** | Tool startup (no hooks) | Prepend to config | Project + tool layer written to tool's native config |

Facts are ordered by `relevance_score` descending within each injection point. When context budget is exhausted, lowest-relevance facts are excluded — no partial facts.

### Tool coverage tiers

| Tier | Tools | Mechanism |
|------|-------|-----------|
| **1 — Native hooks** | Claude Code, Gemini CLI, Copilot, Cursor, Codex, Kiro | Full injection via hook API. AIP hook proxy normalises payloads. |
| **2 — Shim** | Aider, Amp, Vibe | Wrapper prepends memory to tool's config at launch. |
| **3 — MCP** | Any MCP-aware tool | `read_memory` / `write_memory` MCP tools. |
| **4 — Manual** | Anything else | `aip hook emit` + wrapper. |

### Serverless injection

```bash
#!/bin/bash
# wrapper: umx-aider
umx inject --cwd . --tool aider   # writes .aider/memory-context.md
exec aider "$@"
# on exit:
umx collect --cwd . --tool aider  # reads native memory + session log, queues dream
```

### Legacy bridge (Trojan Horse strategy)

During the Prune phase, umx can optionally write a condensed summary into legacy files (`CLAUDE.md`, `AGENTS.md`, `.cursorrules`) within bounded markers:

```markdown
<!-- umx-start: do not edit manually -->
- postgres runs on 5433
- ignore CORS on /api/auth in dev
<!-- umx-end -->
```

This provides zero-friction compatibility with tools that already read those files, without requiring them to adopt `.umx/` natively. The markers prevent accidental overwrites on subsequent dream runs.

---

## 11  Context Budget

Different tools have vastly different context windows. umx must never crowd out codebase context with memory injection.

```bash
umx inject --cwd . --tool aider --max-tokens 4000
```

If `--max-tokens` is not specified, umx infers the budget from the tool adapter's known limit. Injection halts before exceeding the budget. No partial fact inclusion — a fact is either fully included or excluded.

---

## 12  Git Strategy

`.umx/` files will be updated frequently. To minimise merge conflicts:

- **One topic per file** — already enforced by the format. Never aggregate topics.
- **`local/` is gitignored** — personal facts never create team conflicts.
- **JSON is derived, not merged** — git merges happen on the markdown files. JSON is regenerated from markdown after merge. Structured inline metadata (HTML comments) keeps diffs clean.

### Merge rule

```
- Identical fact IDs → merge metadata (take higher fact_score)
- Conflicting text on same ID → new conflict entry in conflicts.md
- Never silently overwrite a higher-strength fact
```

Future tooling:
```bash
umx merge   # resolve conflicts using composite score, surfaces ties to viewer
```

---

## 13  Failure Modes

| Failure | Cause | Mitigation |
|---------|-------|------------|
| **Incorrect high-strength fact** | Tool-native memory error | Composite scoring (confidence + corroboration dilutes strength); user override → S:5 |
| **Extraction hallucination** | LLM misinterpretation of transcript | Low initial strength (1–3); confidence field; decay + pruning |
| **Over-injection** | Weak relevance filtering | Relevance scoring; strict budget enforcement; no partial facts |
| **Stale facts dominating** | High strength but outdated | Recency in composite score; time decay in Prune phase |
| **Metadata loss via manual edit** | User editing markdown directly | Parser regenerates metadata on next pass; edited lines promoted to S:5 |
| **Sensitive data in team memory** | Transcript references secrets | `.gitignore`-driven extraction exclusion; auto-route to `local/` |
| **Concurrent dream runs** | Multiple tools running simultaneously | Lock file (`.umx/dream.lock`); one dream per 24h per project |
| **LLM providers unavailable** | All free tiers rate-limited or down | Graceful degradation to native-only dream (Section 9.5); `.umx/NOTICE` surfaces status at next session start |

---

## 14  Comparison

| Tool | Cross-tool | Hierarchical | Serverless | Auto-extract | Hybrid read | Encoding strength | Open format | Free compute |
|------|-----------|-------------|-----------|-------------|-------------|-------------------|-------------|--------------|
| **umx** | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| claude-mem | ✗ | ✗ | ✗ | ✓ | ✗ | ✗ | ~ | ✗ |
| Mem0 | ~ | ✗ | ✗ | ✓ | ✗ | ~ | ✗ | ✗ |
| OneContext | ~ | ✗ | ✗ | ✓ | ✗ | ✗ | ✗ | ✗ |
| OpenContext | ~ | ✗ | ✓ | ✗ | ✗ | ✗ | ✓ | ✗ |
| mcp-memory-service | ~ | ✗ | ✗ | ~ | ✗ | ✗ | ✓ | ✗ |
| mem-mesh | ✓ | ✗ | ✗ | ✗ | ✗ | ✗ | ✓ | ✗ |
| OpenCxMS | ✓ | ~ | ✓ | ✗ | ✗ | ✗ | ✓ | ✗ |
| CLAUDE.md hierarchy | ✗ | ✓ | ✓ | ✗ | ✗ | ✗ | ✓ | ✗ |

`✓` fully supported · `~` partial / same-vendor only · `✗` not supported

**On encoding strength:** Mem0 gets `~` because it tracks implicit strengthening via access frequency [7], but does not model origin-based strength or cognitive science typing. No surveyed tool uses deliberate/incidental as a reliability dimension.

**OpenContext** added this revision — it is a knowledge base (user-authored documents), not a memory system (agent-observed facts). Complementary to umx rather than competitive. Its `contexts/` folder is analogous to umx's `~/.umx/` user scope, but manually curated rather than auto-extracted.

---

## 15  Python Package Structure

```
umx/
├── __init__.py
├── cli.py                  # `umx` and `aip mem` subcommands
├── scope.py                # scope hierarchy resolution + local/ split
├── memory.py               # read/write MEMORY.md + topic files
├── strength.py             # encoding strength + composite scoring + corroboration
├── inject.py               # injection point handlers + relevance scoring
├── budget.py               # context budget inference + enforcement
├── adapters/               # native memory read adapters
│   ├── claude_code.py
│   ├── aider.py
│   ├── copilot.py
│   └── generic.py
├── dream/
│   ├── pipeline.py         # Orient → Gather → Consolidate → Prune
│   ├── gates.py            # three-gate trigger (lock + time|sessions) + lock file
│   ├── extract.py          # LLM extraction prompt + fact schema
│   ├── gitignore.py        # .gitignore parsing → extraction exclusion rules
│   ├── conflict.py         # conflict detection + score-based resolution
│   ├── providers.py        # provider rotation + local fallback chain
│   ├── decay.py            # exponential recency decay (configurable λ)
│   └── notice.py           # .umx/NOTICE writer for degradation alerts
├── hooks/
│   ├── session_start.py
│   ├── post_tool_use.py
│   └── session_end.py
├── shim/
│   ├── aider.py
│   └── generic.py
├── bridge.py               # legacy file bridge (CLAUDE.md / AGENTS.md markers)
├── viewer/
│   └── server.py           # local web viewer, no daemon
└── mcp_server.py           # read_memory / write_memory MCP tools
```

### CLI surface

```bash
umx inject   --cwd . --tool aider [--max-tokens N]
umx collect  --cwd . --tool aider
umx dream    --cwd . [--force]
umx view     --cwd . [--scope project] [--min-strength N]
umx tui      --cwd .                            # Phase 5
umx status   --cwd .
umx conflicts --cwd .
umx forget   --cwd . --topic devenv
umx promote  --cwd . --fact f_001 --to project
umx merge    --cwd .                            # Phase 6
```

---

## 16  Viewer / Editor

Local web UI. `umx view`. No persistent process.

| Feature | Description |
|---------|-------------|
| **Memory tree** | Full scope hierarchy for CWD including `local/`. |
| **Fact view** | Source tool, session, `encoding_strength`, `memory_type`, `fact_score`, confidence, `corroborated_by`, timestamps. |
| **Inline edit** | Edit any fact. Promoted to S:5 on save. JSON updated automatically. |
| **Promote / demote** | Move fact to higher or lower scope. |
| **Conflict panel** | Flagged conflicts side-by-side with scores. Pick one, merge, or escalate. |
| **Strength filter** | Show only facts at or above a given strength. Hide noise. |
| **Scope filter** | Show only facts relevant to a specific folder or file. |
| **Dream log** | Last dream: phases, facts added/removed/conflicted, provider, tokens consumed. |
| **Narrative view** | Optional synthesis of related atomic facts into readable prose. Presentation only — does not affect storage. |

---

## 17  Roadmap

| Phase | Milestone | Deliverables |
|-------|-----------|--------------|
| **0** | Foundation | Scope spec · file format · fact schema · conflict format · `local/` split |
| **1** | Core library | `scope.py` + `memory.py` + `strength.py` · composite scoring · AIP hook integration |
| **2** | Dream pipeline | Orient → Gather → Consolidate → Prune · 3-gate trigger · provider rotation · `.gitignore` safety |
| **3** | Read adapters | Claude Code · Aider · generic · hybrid gather · corroboration bonus |
| **4** | Injection layer | Tier 1 hooks · Tier 2 shims · Tier 3 MCP · budget enforcement · relevance scoring · legacy bridge |
| **5** | Viewer / editor | Web viewer · strength/scope filters · conflict UI · narrative view · dream log · TUI |
| **6** | Hardening | File-read append injection · `umx merge` · worktree scope (optional) · time decay tuning |
| **7** | Ecosystem | `aip mem` integration · published spec · `AGENTS.md` / `CLAUDE.md` sync bridge · third-party adoption |

---

## 18  Non-Goals

- **No cloud sync.** Local-first. Multi-machine = git.
- **No vector search in v1.** Path-based scoping covers the common case. Embeddings are a future opt-in.
- **No pane-read mid-stream injection.** Too risky as a default. Future opt-in with strict safeguards: cooldown, user-authored patterns only, one injection per cycle, disabled by default.
- **No auto-injection of sensitive data.** `.gitignore` exclusion enforced in Gather phase.
- **No narrative merging in storage.** Facts are atomic. Narrative synthesis is viewer-only.
- **No opinions on which tool you use.** umx works identically with one CLI or ten.

---

## 19  Relation to AIP

umx is a natural extension of AIP. AIP provides the orchestration substrate (tmux + filesystem event bus + hook normalisation). umx adds the memory layer on top.

- AIP hook proxy normalises payloads from all Tier 1 CLIs. umx hook handlers consume those events.
- AIP shim watch provides lifecycle events for Tier 2 CLIs. umx shim handles collection.
- `workspace/events.jsonl` feeds the Gather phase as a structured session source (preferred over raw transcripts).
- umx ships as `aip mem` subcommands alongside its standalone CLI.

**Boundary:** AIP owns orchestration and inter-agent communication. umx owns memory scoping, extraction, strength, and injection. AIP emits events; umx consumes them and writes memory files that any tool can read.

---

## 20  References

[1] Tulving, E. (1972). *Episodic and semantic memory.* In E. Tulving & W. Donaldson (Eds.), Organisation of Memory. Academic Press. — Foundational distinction between episodic memory (tied to a specific event/time) and semantic memory (generalised facts abstracted from their learning context). umx uses this to distinguish `explicit_episodic` (strength 3, extracted from a known session) from `explicit_semantic` (strength 4–5, deliberate and generalised).

[2] Schacter, D. L. (1987). *Implicit memory: History and current status.* Journal of Experimental Psychology: Learning, Memory, and Cognition, 13(3), 501–518. — Formalises the explicit/implicit distinction. Explicit memory is conscious and intentional; implicit memory influences behaviour without awareness. umx uses this as the top-level split: strengths 4–5 are explicit, strengths 1–2 are implicit, strength 3 sits at the boundary (episodic-explicit, automated extraction).

[3] Anderson, J. R. (1983). *The Architecture of Cognition.* Harvard University Press. — Introduces ACT-R and the base-level learning equation: each retrieval of a memory raises its activation strength; unused memories decay. umx's corroboration mechanic (same fact from multiple sources → strength +1) and time decay during Prune directly implement this model computationally. The composite scoring formula is a simplified approximation of ACT-R activation.

[4] The New Stack (2026, January 16). *Memory for AI Agents: A New Paradigm of Context Engineering.* — "Human recall is recursive — by re-encoding memories each time we retrieve them, strengthening some, discarding others. AI systems can mimic this by summarising or rewriting old entries when new evidence appears. This prevents what researchers call context drift, where outdated facts persist."

[5] Ebbinghaus, H. (1885). *Über das Gedächtnis.* Duncker & Humblot. (Trans. 1913, *Memory: A Contribution to Experimental Psychology.*) — The forgetting curve: memory strength decays exponentially without reinforcement. umx uses this as the theoretical basis for time decay on uncorroborated low-strength facts in the Prune phase. Facts at strength 1–2 with no corroboration and no retrieval within a configurable window are candidates for pruning.

[6] Observed patterns in production memory systems (2025–2026). Multiple AI coding tools have converged on similar background memory consolidation architectures: phased pipelines (orient/gather/consolidate/prune), gated triggers (time-based, session-count, concurrency locks), read-only extraction constraints, and pointer-based memory indexes. Claude Code's autoDream pipeline (confirmed via public source analysis, March 2026), Cursor's memory layer, and Windsurf's context engine all exhibit variations of this pattern. umx adopts the phased pipeline architecture as a shared design, extending it with cross-tool scope hierarchy, encoding strength, and hybrid read strategy.

[7] Mnemoverse Documentation (2025). *Production Memory Systems: Implementation Analysis.* — Survey of existing AI memory platforms. Notes that most systems implement "implicit strengthening via access frequency" rather than explicit strength modelling, and that "few systems implement explicit decay." Confirms that origin-based encoding strength (deliberate vs incidental) is not implemented in any surveyed production system.

---

## Open questions

- **Composite score weights** — require empirical tuning against real session data before any defaults are set. Initial implementation should expose all weights as config.
- **Time decay λ tuning** — the default λ of 0.023 (~30 day half-life) is a reasonable starting point but needs validation against real project cadences. Projects with daily deploys may need faster decay; reference codebases may need slower.
- **Extraction prompt design** — the Gather phase LLM prompt for transcript extraction is the most implementation-critical piece not yet specified. Key decisions: input format normalisation across tools, confidence calibration, detecting when a fact is stated vs questioned.
- **Copilot / Gemini native memory formats** — adapters cannot be written until formats are documented or reverse-engineered.
- **Scope promotion heuristics** — if the same fact appears in 3+ folder-level memories independently, auto-promote to project? Threshold TBD.
- **Worktree scope** — parallel branches with divergent context. Structure `/project/.umx/worktrees/<name>/`. Deferred to Phase 6.

---

*umx is part of the AIP ecosystem — [github.com/dev-boz/agent-interface-protocol](https://github.com/dev-boz/agent-interface-protocol)*
