# umx — Universal Memory Exchange
### Specification v0.8 · April 2026

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
8. [Local Path Convention](#8-local-path-convention)
9. [Memory File Format](#9-memory-file-format)
10. [Read Strategy](#10-read-strategy)
11. [Dream Pipeline](#11-dream-pipeline)
12. [GitHub Dream Governance (gitmem)](#12-github-dream-governance-gitmem)
13. [Branch and Commit Conventions](#13-branch-and-commit-conventions)
14. [Memory Lifecycle](#14-memory-lifecycle)
15. [Promotion Protocol](#15-promotion-protocol)
16. [Injection Architecture](#16-injection-architecture)
17. [Context Budget](#17-context-budget)
18. [Git Strategy](#18-git-strategy)
19. [Session Logs](#19-session-logs)
20. [Search and Retrieval](#20-search-and-retrieval)
21. [Tombstones and Forgetting](#21-tombstones-and-forgetting)
22. [Failure Modes](#22-failure-modes)
23. [Conformance](#23-conformance)
24. [Comparison](#24-comparison)
25. [Python Package Structure](#25-python-package-structure)
26. [Viewer / Editor](#26-viewer--editor)
27. [Configuration Reference](#27-configuration-reference)
28. [Roadmap](#28-roadmap)
29. [Non-Goals](#29-non-goals)
30. [Relation to AIP](#30-relation-to-aip)
31. [References](#31-references)

---

## Notation

The key words "MUST", "MUST NOT", "SHOULD", "SHOULD NOT", and "MAY" in this document are to be interpreted as described in RFC 2119.

---

## 1  Problem Statement

Every AI coding CLI maintains its own isolated memory store. A fact learned by Claude Code about your project — *postgres runs on 5433, ignore CORS warnings in dev* — is invisible to Aider, Copilot, Gemini CLI, or any other tool you use on the same codebase. Switching tools means re-establishing context from scratch.

Beyond cross-tool isolation, existing memory systems share a deeper problem: **no auditability, no governance, no correction mechanism.** A cheap model extracts a fact incorrectly — that fact silently persists at full strength with no trace of where it came from or how to challenge it. Repeated summarisation degrades fidelity over time — the "JPEG compression" effect of LLM memory [2] — and there is no mechanism to detect or reverse the drift.

Existing solutions either require cloud infrastructure (Mem0, OneContext), are locked to a single tool (claude-mem, Copilot cross-agent memory), solve the chat-UI problem rather than the CLI-dev-workflow problem, or provide no audit trail.

**umx is a filesystem convention and injection protocol — not a service.** Any CLI that can read a file and execute a hook can participate. The gitmem backend adds GitHub as a durable, auditable source of truth with PR-based governance.

---

## 2  Design Principles

- **Git is the source of truth. Filesystem is the working copy.** Local directories are git clones of repos in the memory org. GitHub is canonical. If local and remote diverge, git merge resolves — local files never silently win.
- **Memory never lives in project repos.** All memory is stored in a separate GitHub organisation. Project repos stay clean — no session logs, no memory files, no dream agent noise polluting code history or leaking to collaborators.
- **Tool-agnostic by convention.** Tools adopt the spec; umx does not adopt tools.
- **Don't fight native memory systems.** Read what tools already write. Aggregate, don't replace.
- **Hierarchical scoping.** Memory is injected at the most specific relevant level, not dumped wholesale.
- **Encoding strength over flat confidence.** Facts carry a typed strength derived from how deliberately they were encoded, grounded in cognitive science taxonomy.
- **Facts are atomic.** The pipeline extracts, deduplicates, lints, and prunes — it never merges facts into narratives. Narrative synthesis is a viewer concern, not a storage concern.
- **Source origin is tracked.** Every fact carries a `source_type` that distinguishes ground-truth code from LLM inference, user assertions from tool output. Composite scoring weights these differently; hallucinated inferences cannot win conflict resolution against verified code reads.
- **Schema is explicit, not implicit.** `CONVENTIONS.md` is the human-authored project schema file that defines topic taxonomy, fact phrasing, and entity vocabulary. L2 dream review enforces it.
- **Raw sessions are immutable ground truth.** Session logs are never edited after the pre-commit redaction pass, never deleted. They are the audit baseline from which all derived memory can be verified or re-derived.
- **Tiered dream governance (in gitmem mode).** Cheap models propose. SotA models filter. Humans resolve ambiguity. No model auto-commits to main in `remote` or `hybrid` mode. In `local` mode, direct writes are permitted for solo/offline use.
- **Provenance on every fact.** Every fact MUST record minimal provenance inline in committed markdown: extraction model, approval model, and PR reference. Extended provenance (full session list, prompt hashes) lives in derived `.umx.json` (local-only cache). A fresh clone MUST be able to trace any fact to its extraction source and approval gate from committed files alone.
- **Storage and presentation are the same layer.** Markdown is the source of truth. JSON is a derived cache. SQLite is a derived search index. Both are local build artifacts, never committed to memory repos.
- **Redaction fails closed.** If any safety pass (secret scanning, redaction) fails for any reason, the affected content MUST be quarantined — never committed or pushed. Silent failure is a security breach.
- **Zero injection by default.** Nothing is added to context unless relevant to the current scope.
- **Separate org, not separate account.** The memory org is owned by your personal GitHub account. Your existing SSH keys and PATs work automatically — zero extra authentication.

---

## 3  Architecture Overview

### Separation model

Memory is **completely separate** from project repos. Project repos contain code. Memory repos contain cognition. They live in different GitHub organisations and never cross-pollinate.

Why:
- Project repos stay clean — no `.umx/` directories, no session logs, no dream commits in history
- Collaborators and public forks never see your memory or session data
- Dream agent activity (frequent commits, PRs, branch churn) doesn't swamp your contribution graph or project notifications
- Memory governance (branch protection, PR review rules) is independent of code governance

```
┌──────────────────────────────────────────────────────────────────┐
│  GitHub Memory Org (source of truth)                             │
│  Separate org, private repos, your personal account owns it      │
│                                                                  │
│  memory-org/user             ← user-global memory                │
│  memory-org/<project-slug>   ← per-project memory (one per)      │
│                                                                  │
│  Each repo contains:                                             │
│    CONVENTIONS.md  human-authored project schema (S:5)           │
│    sessions/    immutable raw logs (WAL, redacted)               │
│    episodic/    dream-extracted facts (proposed via PR)          │
│    facts/       consolidated stable facts (reviewed/merged)      │
│    principles/  cross-session patterns (SotA/human gate)         │
│    meta/        index, manifest, dream log, schema version       │
│    local/       gitignored — private/ and secret/ split          │
└──────────────────────┬───────────────────────────────────────────┘
                       │ git pull/push (async, via push queue)
┌──────────────────────▼───────────────────────────────────────────┐
│  Local Clones ($UMX_HOME, default ~/.umx/)                       │
│                                                                  │
│  user/                         ← clone of memory-org/user        │
│  projects/<slug>/              ← clone of memory-org/<slug>      │
│                                                                  │
│  Markdown files  (canonical format, human-editable)              │
│  SQLite index    (local-only, incrementally rebuilt on pull)      │
└──────────────────────┬───────────────────────────────────────────┘
                       │ reads/writes
┌──────────────────────▼───────────────────────────────────────────┐
│  Agent Session                                                   │
│  reads  → SQLite (fast) or raw sessions (brute force)            │
│  writes → local markdown → git commit → push queue               │
└──────────────────────────────────────────────────────────────────┘
```

### Dream mode permissions

| Capability | `local` mode | `remote` mode | `hybrid` mode |
|------------|:---:|:---:|:---:|
| Direct write to main (sessions) | ✓ | ✓ | ✓ |
| Direct write to main (facts) | ✓ | ✗ | ✗ |
| PR-based governance (facts) | ✗ | ✓ | ✓ |
| L1/L2/L3 tiers | ✗ | ✓ | ✓ |
| Offline-capable | ✓ | ✗ | Partial |
| Cross-project dream | ✗ | ✓ | ✓ |

`local` mode is designed for solo/offline use. It sacrifices governance for speed. `remote` mode provides full PR governance. `hybrid` mode pushes sessions directly to main (append-only, no governance needed) but routes **all fact changes** through PRs — it combines immediate session availability with full fact governance.

### GitHub org layout

```
memory-org/                        # private org, owned by your personal account
  user/                            # user-global memory
  boz/                             # project memory for "boz" project
  agent-interface-protocol/        # project memory for AIP
  umx/                             # project memory for umx itself
```

Project memory repos are named by slug matching the project they track. Default: derive from git remote URL. Override: `.umx-project` file or `config.yaml` `project.slug_format` setting.

### Auth model

The memory org is owned by your personal GitHub account. This means:

- **Locally:** Your existing SSH keys and `gh` auth work. Agents see local directories, not GitHub.
- **API access:** A single PAT with `repo` scope on the memory org. Stored in `$UMX_HOME/config.yaml` or `UMX_GITHUB_TOKEN` env var.
- **GitHub Actions:** Each memory repo gets a `GITHUB_TOKEN` automatically.
- **Agent tokens (gitmem mode):** Scoped per role — L1 gets PR-write only, L2 gets merge on allowed labels, indexer gets read-only.

Agents MUST NOT touch GitHub directly during a session. They read and write local files. The umx push queue owns the token and sync cadence.

---

## 4  Memory Model

umx grounds its memory taxonomy in established cognitive science rather than inventing new terms.

Endel Tulving's 1972 distinction between **episodic** and **semantic** memory [1], extended by Daniel Schacter's 1987 formalisation of **explicit** (declarative) vs **implicit** (non-declarative) memory [2], gives a well-validated framework for classifying how facts enter and persist in a memory system.

| Type | Cognitive science definition | umx equivalent |
|------|------------------------------|-----------------|
| **Explicit semantic** | Consciously encoded general facts | Facts deliberately saved by an LLM or human — the tool *meant* to remember this |
| **Explicit episodic** | Facts tied to a specific event, session, or time | Facts extracted from a known session — we know *when* it was learned |
| **Implicit** | Encoded without conscious intent | Facts inferred from patterns across logs — the tool never explicitly saved this |

**Origin is a proxy for reliability.** A fact an LLM deliberately wrote to its memory store is more likely to be correct than one a background extractor scraped from a transcript, which is more likely than a pattern inferred from log frequency.

This maps onto Anderson's ACT-R **activation strength** model [3]: each memory unit has a numeric activation level that decays with time and strengthens with each retrieval or corroboration.

Additional cognitive science models inform specific mechanisms:

- **Interference Theory** [8]: When a new fact contradicts an existing one, the old fact MUST be suppressed via a `conflicts_with` pointer — not silently outcompeted by activation alone. Retroactive interference (new overwrites old) is the primary failure mode in evolving codebases.
- **Consolidation Theory** [9]: Newly written facts are fragile until the dream pipeline has processed them. A `consolidation_status` field marks whether a fact has been stabilised.
- **Encoding Specificity** [10]: Recall is strongest when retrieval context matches encoding context. Facts MAY carry an `encoding_context` for context-aware retrieval scoring.
- **Ovsiankina Effect** [11]: Incomplete tasks maintain cognitive salience — the tendency to resume interrupted tasks. (Note: the related Zeigarnik claim of superior memory for unfinished tasks was refuted by a 2025 meta-analysis; only the resumption tendency is robustly supported.) Facts with `task_status: open` receive a retrieval bonus at session start.
- **Metacognition / Feeling of Knowing** [12]: The system SHOULD maintain a domain manifest (`meta/manifest.json`) distinguishing "true absence" (topic not covered) from "retrieval failure" (topic covered but fact not found).
- **Bartlett's Schema Theory** [13]: LLM-based extraction reconstructs rather than faithfully compresses. The dream pipeline prompt SHOULD flag project-specific conventions that deviate from common practice (`schema_conflict: true` tag).
- **Source Monitoring** [14]: Memory includes attribution of origin — distinguishing "I read this in code" from "I inferred this" from "the user told me." umx operationalises this via the `source_type` enum (Section 5), which feeds composite scoring so that ground-truth code extraction is weighted above LLM inference.

---

## 5  Encoding Strength

Every fact carries an `encoding_strength` from 1–5, a `memory_type`, and a `verification` status.

| Strength | Label | Memory type | Source | Git analogy |
|----------|-------|-------------|--------|-------------|
| **5** | Ground truth | Explicit semantic | Human manually confirmed | Signed tag / immutable release |
| **4** | Verified | Explicit semantic | Independently verified (SotA-approved OR corroborated ≥2 sources) | Protected branch, CODEOWNERS reviewed |
| **3** | Deliberate / Extracted | Explicit episodic/semantic | Tool native memory OR dream extraction from transcript | Merged to main |
| **2** | Inferred | Implicit | Repeated pattern across multiple logs | Unreviewed PR |
| **1** | Incidental | Implicit | Single transcript mention, unconfirmed | Uncommitted working tree |

### Verification field

The `verification` field disambiguates facts at the same encoding strength:

| Value | Meaning |
|-------|---------|
| `self-reported` | Tool's native memory — the LLM claimed it deliberately, but no independent check |
| `corroborated` | Same fact confirmed by ≥2 independent sources |
| `sota-reviewed` | SotA model approved during L2 dream review |
| `human-confirmed` | Human edited or approved directly |

`verification` is weighted in composite scoring. A `self-reported` S:3 fact scores lower than a `corroborated` S:3 fact, even at the same encoding strength.

### Strength mechanics

**Corroboration strengthens.** A fact at S:3 that also appears in an independent source gains +1. Corroboration requires **independent evidence**: two sources count as independent only if they have different `source_session` values AND different `source_tool` values, OR the same tool across sessions separated by ≥24 hours. Bridge-written facts (`CLAUDE.md` markers) MUST NOT count as independent corroboration sources.

**Manual edit always wins.** If a user manually confirms or edits a fact, the parser MUST create a **new** fact at S:5 with `supersedes` pointing to the original fact, and set `superseded_by` on the original. The old fact is preserved for audit. S:5 is exclusively reserved for human-authored or human-confirmed facts. Editing a fact's text MUST NOT reuse the old `fact_id` — identity is immutable.

**PR approval tier maps to strength.** L1 PRs arrive at S:2–3. L2 (SotA) approval promotes to S:4 with `verification: sota-reviewed`. L3 (human) confirmation elevates to S:5.

**Prune threshold.** Facts below a configurable threshold (default: **2**) are removed during Prune. Facts at S:1 that aren't corroborated within the decay window are pruned. S:1 exists as a capture mechanism — incidental facts must earn their place through corroboration.

### Atomic fact rule

Facts MUST remain atomic. The Dream pipeline MUST NOT merge multiple facts into a single narrative statement.

### Fact identity

Every fact MUST have a globally unique, immutable identifier.

- **`fact_id`** (immutable): ULID (Universally Unique Lexicographically Sortable Identifier). Generated on creation. Never changes. Used for merge, audit trail, PR references, tombstones.
- **Semantic dedup key** (derived): `SHA-256(lowercase(text + scope + topic))`, truncated to 16 hex chars. Used for deduplication detection. Two facts with different `fact_id` but same dedup key are candidates for merge/corroboration.

### Fact schema

```yaml
facts:
  - fact_id: 01JQXYZ1234567890ABCDEF    # ULID, immutable
    text: "postgres runs on port 5433 in dev"
    scope: project
    topic: devenv
    encoding_strength: 4
    memory_type: explicit_semantic
    verification: corroborated
    source_type: tool_output              # epistemic origin — see source_type enum
    confidence: 0.97
    tags: [database, environment]
    source_tool: claude-code
    source_session: "2026-04-03-01JQXYZ9876543210"
    corroborated_by_tools: [aider]        # tool names that independently confirmed
    corroborated_by_facts: []              # fact_ids of corroborating evidence
    conflicts_with: []                    # fact_ids of contradicting facts
    supersedes: null                      # fact_id this fact replaces
    superseded_by: null                   # fact_id that replaced this fact
    consolidation_status: stable          # fragile | stable
    task_status: null                     # null | open | blocked | resolved | abandoned
    last_retrieved: 2026-04-04T09:00Z
    created: 2026-04-03T20:11Z
    last_referenced: 2026-04-04T09:00Z
    expires_at: null                      # optional TTL — auto-prune when past
    applies_to: null                      # optional: environment qualifiers (see applies_to schema below)
    provenance:
      extracted_by: groq/llama-3.3-70b
      approved_by: claude-sonnet-4
      approval_tier: l2-auto
      pr: memory-org/boz#47
      sessions: ["2026-04-03-01JQXYZ9876543210"]
    encoding_context:                     # optional — for retrieval scoring
      task_type: debugging
      active_module: database
```

> `encoding_strength` and `confidence` are orthogonal. Strength is *how deliberately* a fact was encoded. Confidence is *how certain* the extractor was about the text. `verification` is *how independently the fact has been confirmed*. `source_type` is *what kind of evidence the fact came from*.

### Source type enum

Every fact MUST carry a `source_type` field identifying the epistemic origin of the evidence. This operationalises the Source Monitoring framework [14] and is weighted in composite scoring.

| Value | Meaning | Example | Default strength |
|-------|---------|---------|------------------|
| `ground_truth_code` | Read directly from a source file by an adapter | Function signature parsed from AST | S:4 |
| `user_prompt` | User explicitly stated it in conversation | "always use snake_case in this repo" | S:3 |
| `tool_output` | Tool native memory — LLM deliberately wrote it to its own store | Claude Code memory entry, Aider tags | S:3 (self-reported) |
| `llm_inference` | LLM inferred or reasoned during a session (no external grounding) | "this probably uses connection pooling" | S:1–2 |
| `dream_consolidation` | Derived by the Dream pipeline by combining multiple existing facts | Cross-session pattern extraction | Inherits from inputs |

**`dream_consolidation` boundary:** This source type is ONLY for facts derived by synthesising information from multiple existing facts or sessions (cross-session patterns, principles inferred from repeated behaviour). Single-session extraction is `llm_inference` regardless of whether the extraction runs inside the Dream pipeline. If L2 reviews a single-session extraction and approves it, the source type remains `llm_inference` — L2 review affects `verification` status, not `source_type`.
| `external_doc` | Extracted from documentation (README, API docs, wiki) | Port number from project README | S:3 |

`source_type` feeds the composite score (see Section 6). `llm_inference` facts require corroboration from a different `source_type` to reach S:3. `ground_truth_code` facts start at S:3–4 because they reflect actual code, not hearsay.

### Supersession chains

When Consolidate detects a contradiction and resolves it by composite score, it MUST set `superseded_by` on the losing fact and `supersedes` on the winning fact, creating an explicit replacement chain. Superseded facts remain in the repo for audit (`umx history --fact <id>` walks the chain) but are excluded from normal retrieval and injection. This preserves the temporal evolution of a fact (e.g., "postgres was on 5432 until 2026-03-14, now 5433") without requiring full bi-temporal validity fields.

### `applies_to` schema

The `applies_to` field uses a canonical structure to qualify facts by environment. Two facts with non-overlapping `applies_to` values are NOT contradictions — they are parallel truths for different contexts.

```yaml
applies_to:
  env: dev           # dev | staging | prod | * (wildcard)
  os: linux          # linux | macos | windows | *
  machine: desktop   # hostname or *
  branch: main       # branch name, glob pattern, or *
```

**Matching semantics:**
- `*` matches any value (wildcard)
- Exact match on all other values
- `null` / absent `applies_to` is equivalent to all-wildcards (applies everywhere)
- Two facts conflict only if their `applies_to` values **overlap** on all specified keys
- Facts with non-overlapping `applies_to` MUST NOT be flagged as contradictions by Consolidate or Lint

Implementations MAY extend the key set (e.g., `container`, `arch`, `workspace`) but MUST support the four canonical keys above.

### `ground_truth_code` anchoring

Facts with `source_type: ground_truth_code` MUST carry additional provenance fields to enable staleness detection:

```yaml
code_anchor:
  repo: github.com/user/boz          # source repository
  path: src/config/database.ts        # file path (repo-relative POSIX)
  git_sha: abc1234                    # optional: commit SHA at extraction time
  line_range: [42, 48]                # optional: line range
```

During Orient, the Dream pipeline SHOULD check whether anchored paths still exist in the project repo. If a path is deleted or the file has changed significantly since `git_sha`, the fact MUST be demoted to `consolidation_status: fragile` and flagged for re-verification in the next L2 review.

---

## 6  Composite Scoring

`encoding_strength` is the primary signal but not the only one. Three purpose-specific scores are derived for different uses.

### Trust score (conflict resolution)

Used when two facts contradict each other. Recency and task salience are deliberately excluded — truth should not depend on when you last looked at a fact or whether it's attached to an open task.

```
trust_score =
  (w_s  × encoding_strength)        # origin reliability
+ (w_c  × confidence)               # extraction certainty (informational until calibrated)
+ (w_k  × corroboration_count)      # independent agreement
+ (w_v  × verification_bonus)       # independent verification premium
+ (w_st × source_type_weight)       # epistemic origin — ground truth > inference
```

**Hard rule:** A fact with `source_type: llm_inference` MUST NOT win conflict resolution against a fact with `source_type: ground_truth_code` on the same subject, regardless of trust scores. If the `ground_truth_code` fact is suspected stale, it must be explicitly re-verified or tombstoned — never silently outranked by inference.

### Relevance score (injection prioritisation)

Used to decide which facts to inject into agent context. Task salience and recency are appropriate here — they affect what's useful right now, not what's true.

```
relevance_score =
  (p_s × scope_proximity)     # file > folder > project > user
+ (p_k × keyword_overlap)     # token/phrase match with prompt or file path
+ (p_r × recent_retrieval)    # fact used recently in this session
+ (p_e × encoding_strength)   # higher strength biases inclusion
+ (p_x × context_match)       # encoding context overlap (Encoding Specificity)
+ (p_t × task_salience)       # Ovsiankina bonus for open tasks
```

### Retention score (pruning decisions)

Used during Prune to decide which facts survive. Combines strength, recency, and usage patterns.

```
retention_score =
  (r_s  × encoding_strength)        # origin reliability
+ (r_r  × recency)                  # staleness penalty
+ (r_u  × usage_frequency)          # from usage.sqlite — frequently referenced facts survive
+ (r_v  × verification_bonus)       # verified facts survive longer
```

Weights are configurable per score. Defaults require empirical tuning. The formulae are fixed; the weights are not.

### Source type weight

| `source_type` | Weight |
|--------------|-------:|
| `ground_truth_code` | +1.5 |
| `tool_output` | +0.5 |
| `external_doc` | +0.5 |
| `user_prompt` | +0.3 |
| `dream_consolidation` | inherits from inputs (avg of source facts) |
| `llm_inference` | 0.0 |

A `ground_truth_code` fact at S:3 scores higher than a `llm_inference` fact at S:3. This prevents hallucinated inferences from winning conflict resolution against verified code reads.

### Verification bonus

| `verification` value | Bonus |
|---------------------|-------|
| `self-reported` | 0.0 |
| `corroborated` | +0.5 |
| `sota-reviewed` | +1.0 |
| `human-confirmed` | +1.5 |

### Task salience (Ovsiankina bonus)

Facts with `task_status: open` or `blocked` receive a constant additive bonus, bypassing time decay. When `task_status` changes to `resolved` or `abandoned`, normal decay resumes immediately. This ensures that incomplete debugging sessions, dangling TODOs, and open PRs surface reliably at session start.

**Auto-abandonment.** Tasks that remain `open` or `blocked` for longer than `prune.abandon_days` (default: 30) without any session referencing them are auto-transitioned to `abandoned` during the Prune phase. Abandoned tasks lose their Ovsiankina salience bonus but remain queryable via `umx resume --include-abandoned`.

**`umx resume`** lists all `open` and `blocked` tasks from recent sessions ordered by last activity, giving the next session an immediate "where was I?" view without grepping history.

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

### `last_referenced` — local-only telemetry

`last_referenced` is tracked in `meta/usage.sqlite` (local-only), NOT in committed markdown metadata. This prevents meaningless merge conflicts when two machines sync — every citation would otherwise create a diff on committed files.

`last_referenced` MUST only be updated in `usage.sqlite` on:
- Explicit user query (`umx view`, `umx search`)
- Agent explicitly cites the fact in output
- Session-end batch update for facts that were injected AND referenced in output

Silent injection (fact loaded into context but never referenced in output) MUST NOT update `last_referenced`. Retrieval telemetry (which facts were injected, which were actually used) is also tracked in `usage.sqlite`. This telemetry provides calibration data for relevance scoring weights.

### Calibration use

The Prune phase SHOULD read `usage.sqlite` to identify facts that were repeatedly injected but never referenced in output across multiple sessions. These facts are candidates for relevance-score down-weighting — their `keyword_overlap` or `scope_proximity` scoring may be miscalibrated, or the fact itself may be low-value. This is the primary calibration signal for tuning injection quality over time. Facts persistently injected-but-unused are also candidates for demotion from the hot tier (MEMORY.md) to the warm tier (on-demand retrieval).

---

## 7  Scope Hierarchy

Memory is scoped hierarchically. All memory lives in the memory org — **never inside project repos**. Resolution walks from most specific to most general.

| Scope | Stored in | Local path | Always loaded |
|-------|-----------|------------|---------------|
| **User** | `memory-org/user` | `$UMX_HOME/user/` | Yes |
| **Tool** | `memory-org/user` (subdirectory) | `$UMX_HOME/user/tools/<n>.md` | Yes |
| **Machine** | `memory-org/user` (subdirectory) | `$UMX_HOME/user/machines/<hostname>.md` | Yes |
| **Project** | `memory-org/<slug>` | `$UMX_HOME/projects/<slug>/` | Yes |
| **Project (private)** | `memory-org/<slug>` (gitignored) | `$UMX_HOME/projects/<slug>/local/private/` | Yes |
| **Project (secret)** | `memory-org/<slug>` (gitignored) | `$UMX_HOME/projects/<slug>/local/secret/` | **No** |
| **Folder** | `memory-org/<slug>` (subdirectory) | `$UMX_HOME/projects/<slug>/folders/<path>.md` | Lazy |
| **File** | `memory-org/<slug>` (subdirectory) | `$UMX_HOME/projects/<slug>/files/<file>.md` | Lazy |

### Private / secret split

The `local/` directory is gitignored. It contains two subdirectories with different injection behaviour:

- **`local/private/`** — private facts that ARE injected (personal preferences, machine-specific config, scratchpad). Always loaded: **Yes**.
- **`local/secret/`** — tokens, credentials, connection strings. Always loaded: **No**. Never injected into prompts. Accessed only by explicit CLI request (`umx secret get <key>`). Cross-machine syncing of secrets is a non-goal — use a dedicated secret manager (1Password CLI, Vault, etc.).

### Path encoding for folder/file scope

Paths are repo-relative POSIX, normalized (no `./`, no `..`, no trailing `/`).

- `/` encoded as `---` in filenames (chosen to avoid ambiguity with Python `__init__`, `__tests__`, etc.)
- Special characters percent-encoded
- Case-sensitive (preserve original case)
- Symlinks resolved to target before encoding

Examples:
- `src/api/auth/middleware.ts` → `files/src---api---auth---middleware.ts.md`
- `src/api/` → `folders/src---api.md`
- `src/__init__.py` → `files/src---__init__.py.md` (no ambiguity)

### Project discovery

When an agent starts in a project directory, umx resolves the project slug by:

1. Reading `.umx-project` file in project root (if present) — contains the slug
2. Deriving from the git remote URL (e.g., `github.com/user/boz` → `boz`)
3. Falling back to directory name

### Slug collision handling

On `umx init-project`, if a slug already exists in the memory org AND the git remote doesn't match the existing project's recorded remote, umx MUST warn and prompt for a custom slug. Alternative format: `<owner>-<repo>` (e.g., `alice-utils`), configurable via `config.yaml` `project.slug_format: name | owner-name`.

The `.umx-project` file is the **only** umx artifact that MAY optionally exist in a project repo. It contains a single line: the slug.

### Domain manifest (metacognition)

Each memory repo SHOULD maintain `meta/manifest.json` — a lightweight index of covered domains/topics with their memory counts:

```json
{
  "topics": {
    "devenv": {"fact_count": 12, "avg_strength": 3.8, "fragile_count": 1, "last_updated": "2026-04-08"},
    "auth":   {"fact_count": 5,  "avg_strength": 2.4, "fragile_count": 0, "last_updated": "2026-04-01"},
    "deploy": {"fact_count": 5,  "avg_strength": 2.1, "fragile_count": 4, "last_updated": "2026-04-07"}
  },
  "modules_seen": ["src/api/", "src/auth/", "tests/"],
  "uncertainty_hotspots": [
    {"topic": "deploy", "fragile_ratio": 0.8, "reason": "4 of 5 facts still fragile"}
  ],
  "knowledge_gaps": [
    {"topic": "monitoring", "gap_signals": 3, "fact_count": 0, "reason": "3 gap signals, no facts extracted yet"}
  ],
  "last_rebuilt": "2026-04-08T22:14:00Z"
}
```

The manifest enables proactive gap detection: before starting work on a module not in the manifest, the agent can flag "no prior knowledge of this module" rather than silently operating without context. It distinguishes true absence (topic not covered) from retrieval failure (topic covered but specific fact not found) — implementing Nelson & Narens' metacognitive monitoring [12].

`uncertainty_hotspots` surfaces topics where a high fraction of facts are still `fragile` — these are the most error-prone areas and should be prioritised for L2 review. `knowledge_gaps` surfaces topics where gap signals have fired but no facts have been extracted — candidates for focused extraction or user questioning. Both sections are regenerated during the Prune phase.

`umx meta --topic <name>` displays the manifest entry for a topic including its uncertainty and gap status — a metacognitive lookup distinct from content retrieval.

---

## 8  Local Path Convention

All memory is stored under `$UMX_HOME`. Default: `~/.umx/`. Overridable via the `UMX_HOME` environment variable (for containers, remote dev, CI, Windows, or enterprise setups).

```
$UMX_HOME/
├── config.yaml                     # global config
├── user/                           # clone of memory-org/user
│   ├── CONVENTIONS.md              # human-authored schema file (S:5)
│   ├── sessions/
│   ├── facts/
│   ├── principles/
│   ├── tools/
│   │   ├── claude-code.md
│   │   └── aider.md
│   ├── machines/
│   │   ├── desktop.md
│   │   └── chromebit.md
│   ├── meta/
│   │   ├── MEMORY.md
│   │   ├── manifest.json
│   │   ├── tombstones.jsonl
│   │   ├── gaps.jsonl
│   │   ├── schema_version
│   │   └── usage.sqlite         # local-only retrieval telemetry
│   └── local/
│       ├── private/
│       └── secret/
└── projects/
    ├── boz/                        # clone of memory-org/boz
    │   ├── CONVENTIONS.md          # project schema file (human-authored)
    │   ├── sessions/
    │   ├── episodic/
    │   ├── facts/
    │   ├── principles/
    │   ├── meta/
    │   └── local/
    │       ├── private/
    │       └── secret/
    └── agent-interface-protocol/
        └── ...
```

### CONVENTIONS.md — project schema file

Each memory repo SHOULD contain a `CONVENTIONS.md` file at its root. This is **human-authored** and treated as S:5 (ground truth). It defines the project's topic taxonomy, fact phrasing norms, entity vocabulary, and any project-specific conventions that L1/L2 dream agents MUST follow when extracting and reviewing facts.

`CONVENTIONS.md` is:
- Committed to main directly (not via PR) — it is a human-curated configuration file, not a dream artifact
- Read by the Orient phase of every dream cycle
- Included as context in the L2 reviewer prompt
- Always injected alongside `MEMORY.md` at session start (hot tier)
- Template generated by `umx init-project`; user edits to taste

Example:

```markdown
# Project Conventions

## Topic taxonomy
- devenv: local development environment setup
- auth: authentication and authorization
- deploy: deployment pipeline and infrastructure

## Fact phrasing
- Present tense for current state: "postgres runs on 5433"
- Past tense for historical: "previously used MySQL"
- Atomic — never combine two facts in one line
- ≤200 characters per fact

## Entity vocabulary
- "the API" = src/api/ Express server
- "the worker" = src/jobs/ background processor
- "staging" = GCP project boz-staging

## Project-specific conventions (deviations from common practice)
- We use port 5433 not 5432 for postgres (to avoid conflict with system postgres)
- Test files are co-located with source, not in a tests/ directory
- All timestamps in ISO 8601 UTC
```

The Dream pipeline's L2 reviewer checks proposed facts against `CONVENTIONS.md`. Facts that violate conventions (wrong tense, non-atomic, taxonomy mismatch) are rejected with a rationale pointing to the specific convention.

### Bootstrap

```bash
umx init --org my-memory-org
```

This:
1. Creates the GitHub org if it doesn't exist (free tier, private by default)
2. Creates the `user` repo in the org
3. Clones it to `$UMX_HOME/user/`
4. Initialises directory structure, `meta/schema_version`, and `meta/manifest.json`
5. Writes `$UMX_HOME/config.yaml` with org name and default settings

```bash
umx init-project --slug boz
```

This:
1. Checks if `memory-org/boz` already exists remotely
   - If yes AND the remote matches current project: clone it (machine migration case)
   - If yes AND the remote doesn't match: warn slug collision, prompt for custom slug
   - If no: create `memory-org/boz` repo (private)
2. Clones to `$UMX_HOME/projects/boz/`
3. Initialises directory structure
4. Optionally writes `.umx-project` to the project working directory

### Sync cadence

- **Pull:** On session start (automatic, via hook or wrapper)
- **Push:** On session end, post-dream, or via `umx sync`
- **Conflict on push:** Pull, re-run dream consolidation locally, re-push.

Agents work fully offline. Push failures are queued and retried.

---

## 9  Memory File Format

### Single source of truth

Markdown is the **canonical storage format**. JSON is a derived cache. SQLite is a derived search index. Both are local-only and rebuilt from markdown — MUST NOT be committed to memory repos.

Each topic has:
- `facts/topics/devenv.md` — source of truth, human-editable
- `facts/topics/devenv.umx.json` — derived index (local-only, fast machine access)

**The markdown file is always authoritative.** If JSON and markdown diverge, JSON is discarded and regenerated.

### Inline metadata in markdown

Facts in markdown carry inline metadata in HTML comments:

```markdown
## Facts
- [S:4|V:cor] postgres runs on port 5433 in this dev env <!-- umx:{"id":"01JQXYZ1234567890ABCDEF","conf":0.97,"cort":["aider"],"corf":[],"pr":"#47","src":"claude-code","xby":"gpt-4.1","aby":"claude-sonnet-4","ss":"2026-04-03-01JQXYZ9876543210","st":"tool_output","cr":"2026-04-03T20:11Z","v":"corroborated","cs":"stable","sup":"01JQXABCDEF0000000000"} -->
- [S:3|V:sr] CORS warnings on /api/auth can be ignored in dev <!-- umx:{"id":"01JQXYZ2345678901BCDEFG","conf":0.88,"cort":[],"corf":[],"src":"aider","xby":"gpt-4.1","aby":"claude-sonnet-4","ss":"2026-04-01-01JQXYZ1111111111","st":"tool_output","cr":"2026-04-01T14:33Z","v":"self-reported","cs":"fragile"} -->
```

### Inline metadata field specification

| Field | Key | Required | Source |
|-------|-----|----------|--------|
| Fact ID | `id` | MUST | Generated on creation (ULID) |
| Confidence | `conf` | MUST | Extractor output, bounded [0,1] — informational for v1 |
| Corroborated by tools | `cort` | MUST | Tool names array (e.g., `["aider","claude-code"]`) |
| Corroborated by facts | `corf` | MAY | Array of fact IDs providing independent evidence |
| PR reference | `pr` | MUST¹ | PR number that merged this fact (gitmem/hybrid modes) |
| Source tool | `src` | MUST | Tool that produced the session |
| Extracted by | `xby` | MUST | Model/tool that performed L1 extraction |
| Approved by | `aby` | MUST¹ | Model/tool that performed L2 review |
| Source session | `ss` | MUST | Session ID |
| Source type | `st` | MUST | `ground_truth_code` / `user_prompt` / `tool_output` / `llm_inference` / `dream_consolidation` / `external_doc` |
| Created timestamp | `cr` | MUST | ISO 8601 |
| Verification | `v` | MUST | self-reported / corroborated / sota-reviewed / human-confirmed |
| Consolidation status | `cs` | MUST | fragile / stable |
| Conflicts with | `cw` | MAY | Array of fact_ids |
| Supersedes | `sup` | MAY | fact_id this fact replaces |
| Superseded by | `sby` | MAY | fact_id that replaced this fact |
| Task status | `ts` | MAY | open / blocked / resolved / abandoned |
| Expires at | `ex` | MAY | ISO 8601 |
| Applies to | `at` | MAY | Environment qualifiers (see §5 applies_to schema) |
| Code anchor | `ca` | MUST² | `{repo, path, git_sha?, line_range?}` for ground_truth_code |

¹ MUST in gitmem/hybrid modes; omitted in local mode (no PR governance).
² MUST only when `st` is `ground_truth_code`.

`last_referenced` is deliberately excluded from inline metadata — it is tracked in `meta/usage.sqlite` (local-only) to prevent merge conflicts. See §6.

**Path-derived fields** (NOT stored in inline metadata — derived from file location):
- `scope`: from directory (`facts/` → project, `principles/` → project, `$UMX_HOME/user/` → user)
- `topic`: from filename (`facts/topics/devenv.md` → devenv)
- `memory_type`: from directory (`episodic/` → explicit_episodic, `facts/` → explicit_semantic, `principles/` → explicit_semantic)

**Inline provenance is the audit floor.** The inline metadata MUST carry enough provenance that a fresh clone — without any local JSON cache — can answer: "who extracted this fact, who approved it, and which PR merged it." The `.umx.json` cache MAY carry additional provenance (full session context, extraction parameters, review reasoning) but MUST NOT be the only place where core audit fields live.

If a user adds a line without metadata, the parser MUST assign S:5, `verification: human-confirmed`, `source_type: user_prompt`, and generate the metadata block. If a user edits the text of an existing line, the parser MUST create a NEW fact with the edited text, set `supersedes` pointing to the original fact ID, and set `superseded_by` on the original fact pointing to the new one. The original fact's `id` and text are preserved for audit. The new fact inherits S:5, `verification: human-confirmed`.

### MEMORY.md (index layer)

MEMORY.md is the **hot tier** — always injected into agent context at session start. It MUST be regenerated by the Prune phase on every dream cycle.

**Generation algorithm:**

1. Collect all non-superseded, non-tombstoned facts across all topic files
2. Score each fact by `relevance_score` using a synthetic "project overview" query
3. Sort by score descending
4. Take facts until the token budget is reached (default: 3000 tokens, configurable via `memory.hot_tier_max_tokens`)
5. Group selected facts by topic for readability
6. Regenerate the index table from the selected facts

If the hot tier exceeds the configured token cap, truncate by relevance score. `umx status` MUST warn when hot tier is at >90% capacity.

```markdown
# umx memory index
scope: project
schema_version: 2
last_dream: 2026-04-03T22:14:00Z
session_count: 47

## Index
| Topic       | File                      | Updated    | Avg strength | Facts |
|-------------|---------------------------|------------|--------------|-------|
| Database    | facts/topics/database.md  | 2026-04-03 | 4.2          | 8     |
| Auth system | facts/topics/auth.md      | 2026-04-01 | 3.1          | 5     |
| Dev env     | facts/topics/devenv.md    | 2026-04-03 | 3.8          | 12    |
```

**Size constraint:** The index section of `MEMORY.md` MUST stay under a configurable limit (default: 200 lines, configurable via `config.yaml` `memory.index_max_lines`). Enforced by Prune phase.

### Schema versioning

`meta/schema_version` contains an integer. Dream agents MUST check this value and run migrations before processing. Increment on any breaking change to fact schema or directory layout.

### Conflict file

```markdown
# Conflicts

## [OPEN] devenv · postgres port · 01JQX...DEF vs 01JQX...GHI
- Fact A [01JQX...DEF]: "postgres on 5433" — claude-code native (S:4, score:3.8, 2026-04-03)
- Fact B [01JQX...GHI]: "postgres on 5432" — aider transcript (S:2, score:1.4, 2026-03-28)
- Sessions: Fact A from session 2026-04-03-01JQX..., Fact B from session 2026-03-28-01JQX...
- Resolution: Fact A wins on score — pending user confirmation
```

---

## 10  Read Strategy

umx uses a **hybrid read** approach. Two tracks:

**Fast track** — SQLite FTS index for "what do I know about X" queries. Built from markdown, local-only, incrementally rebuilt on pull.

**Raw track** — direct agent access to `sessions/` JSONL for "what actually happened around X." Preserves full context, tone, reasoning, and back-and-forth that distillation loses.

### Source priority

```
Source                           → Encoding strength
────────────────────────────────────────────────────
Tool native memory               → 3 (explicit semantic, self-reported)
  ~/.claude/projects/*/
  .aider.tags.cache, logs
  ~/.config/copilot/*
  ~/.gemini/*

Session transcripts / logs       → 2–3 (explicit episodic)
  AIP workspace/events.jsonl     → structured events (preferred)
  sessions/*.jsonl               → raw session archive

Inferred patterns                → 1–2 (implicit)
  Repeated mentions across N sessions without explicit save
```

### .gitignore-driven extraction safety

During Gather, the Dream pipeline MUST parse the **project repo's** `.gitignore` and convert rules to path-matching patterns. Facts referencing gitignored paths (`.env`, `secrets.json`, etc.) are auto-routed to the memory repo's `local/private/`.

### Native memory adapters

| Tool | Native memory location | Capture status |
|------|----------------------|----------------|
| Claude Code | `~/.claude/projects/<path>/` | Needs reverse-engineering |
| Aider | `.aider.tags.cache`, `.aider.chat.history.md` | Adapter needed |
| Copilot | `~/.config/copilot/` | Format TBD |
| Gemini CLI | `~/.gemini/` | Format TBD |

Adapters normalise to the umx fact schema, assigning `encoding_strength: 3`, `memory_type: explicit_semantic`, `verification: self-reported`.

### Corroboration bonus

When the same fact appears in both native memory and transcript:
- `encoding_strength` promoted (+1, capped at 4)
- `verification` updated to `corroborated`
- `corroborated_by_tools` (`cort`) updated with tool name
- `corroborated_by_facts` (`corf`) updated with source fact ID if applicable
- `confidence` averaged across sources

---

## 11  Dream Pipeline

Runs after session end using free LLM API quota.

### Three-gate trigger + query-gap trigger

| Gate | Condition | Logic |
|------|-----------|-------|
| **Lock** | No concurrent dream (`meta/dream.lock`) | **Required** |
| **Time** | 24 hours since last dream | Either/or |
| **Sessions** | 5+ sessions since last dream | Either/or |

```
trigger = NOT locked AND (time_elapsed ≥ 24h OR session_count ≥ 5)
```

### Query-gap proposals

When an agent queries umx memory and finds the answer incomplete, it MAY emit a **gap signal** to `meta/gaps.jsonl`:

```jsonl
{"type":"gap","query":"fastboot timeout config","resolution_context":"agent read scripts/deploy.sh and found timeout=30 hardcoded","proposed_fact":"fastboot timeout is 30s by default on veyron","session":"2026-04-08-01JQX...","ts":"2026-04-08T14:30:00Z"}
```

The `resolution_context` field describes **how the gap was resolved** — which file was read, which tool output revealed the answer, or which user message supplied it. It is not general conversational context.

Gap signals SHOULD only be emitted when the agent **actually worked around the gap** during the session (found the answer elsewhere and used it) — not merely when a query returns empty. An empty query may mean the topic is irrelevant. The agent's subsequent workaround behaviour is what confirms the gap was real.

**Tool-driven emission triggers:** Since LLMs cannot reliably self-assess whether they "worked around" a gap, implementations SHOULD use observable tool signals:
1. Memory query returned empty or below-threshold results, AND
2. The agent subsequently read a project file, ran a command, or consulted an external source related to the same topic, AND
3. The session completed successfully (the agent wasn't simply stuck)

Implementations MUST NOT rely solely on LLM self-report for gap emission.

At session end, accumulated gaps are processed as part of the normal Dream pipeline:
- Proposed facts from gaps are treated as S:1 (incidental), `source_type: llm_inference` — lowest confidence, require corroboration to survive
- In gitmem mode, they are batched into a dedicated PR: `[dream/l1] Gap-fill proposals from session <id>`
- In local mode, they are written directly at S:1 and will be pruned unless corroborated

**Mid-session fast path (optional).** For tools with hook support, an agent MAY write a gap-proposed fact directly to `local/private/scratchpad.md` at S:1 for immediate availability in the current session. This fact is ephemeral — it is always injected within the current session but MUST survive the normal Dream pipeline (corroboration, consolidation) to be promoted to `facts/`. The scratchpad is gitignored; scratchpad facts never reach the remote repo unless promoted.

### Phases

The Dream pipeline runs four top-level phases. Phase 3 (Consolidate) contains an integrated Lint sub-phase (3b) that runs after the merge/resolve step (3a) and before Prune.

| # | Phase | Action | Output |
|---|-------|--------|--------|
| 1 | **Orient** | Read `MEMORY.md`, `CONVENTIONS.md` (if absent: skip convention checks, log notice, `umx status` SHOULD suggest creating one), and `meta/manifest.json`. Check `meta/schema_version`. Compare project file tree against existing `folders/` and `files/` entries (detect orphaned scoped memory from renames). Check tombstones. Check `ground_truth_code` anchors for staleness — demote to `fragile` if referenced files are deleted or significantly changed. | Current memory map + orphan candidates + convention context |
| 2 | **Gather** | Read tool native memory (`source_type: tool_output`, S:3). Parse project repo's `.gitignore` for exclusions. Read source files referenced in session (`source_type: ground_truth_code`, S:3–4). Extract from sessions (`source_type: llm_inference` for paraphrased, S:2–3). Infer patterns (S:1–2). Process `meta/gaps.jsonl`. Check tombstones — suppress matching facts. Apply `CONVENTIONS.md` phrasing and taxonomy rules. Flag project-specific conventions deviating from common practice (`schema_conflict: true`). | Candidate fact list with strength + `source_type` + provenance |
| 3a | **Consolidate** | Merge candidates against existing facts. Apply corroboration bonus. Detect contradictions and write `conflicts_with` pointers (Interference Theory). Resolve conflicts by composite score; set `supersedes`/`superseded_by` on winner/loser. Flag ties. Mark new facts as `consolidation_status: fragile`. Write atomic facts. **In local mode: direct write. In remote/hybrid mode: commit to branch, open PR.** | Updated topic files or PR |
| 3b | **Lint** | Integrity checks against the consolidated store: semantic contradiction scan (high similarity + opposing assertions not caught by `conflicts_with`), stale file references (`files/` / `folders/` scopes pointing to paths that no longer exist), orphan `fact_id` references (IDs in `corroborated_by_facts` / `conflicts_with` / `supersedes` / `provenance.sessions` that don't resolve), tag drift (same concept tagged inconsistently: `database` vs `db` vs `postgres`), convention violations (facts breaking `CONVENTIONS.md` phrasing or taxonomy). If `CONVENTIONS.md` is absent, Lint MUST skip convention checks and log a notice. | Lint findings report |
| 4 | **Prune** | Remove facts below threshold (default S:2) **AND** older than `prune.min_age_days` (default: 7 — protects incubating facts in new projects). Apply time decay (Ebbinghaus [5]). Check `expires_at` TTLs. Deduplicate by semantic dedup key. Read `meta/usage.sqlite` to identify injected-but-unused facts for relevance-weight calibration. Rebuild `MEMORY.md` index (see §9 generation algorithm). Rebuild `meta/manifest.json` (topics, uncertainty_hotspots, knowledge_gaps). Promote `consolidation_status: fragile` → `stable` for facts satisfying any stabilisation rule (Section 14). Auto-abandon open/blocked tasks older than `prune.abandon_days`. Enforce MEMORY.md size limit. Detect orphaned scope entries and propose rename/migration. | Pruned index |

### Lint sub-phase output

Lint findings are emitted as a structured report. In `local` mode, the report is written to `meta/lint-report.md` and obvious fixes (orphan cleanup, tag normalisation) are applied directly. In `remote`/`hybrid` mode, findings open a dedicated PR:

```
Title: [dream/lint] Weekly lint report — <date>
Labels: type: lint

## Findings
- **Contradiction (unflagged):** fact A (id...) and fact B (id...) are 0.91 similar and make opposing claims about deploy target
- **Stale reference:** files/src---legacy---api---auth.md → file deleted in project repo on 2026-03-22
- **Orphan ID:** fact X `corroborated_by_facts` references id 01JQXABC... which does not exist
- **Tag drift:** `database` (12 facts), `db` (3 facts), `postgres` (5 facts) — propose canonicalising to `database`
- **Convention violation:** fact Y in devenv uses past tense ("used port 5432") — CONVENTIONS.md requires present tense or explicit historical marker
```

L2 auto-merges obvious fixes (orphan cleanup, tag normalisation, stale reference tombstones). Contradictions and convention violations escalate to human review.

Lint runs on the slow Dream cycle (weekly by default, configurable via `dream.lint_interval`).

### Pipeline constraints

The Dream pipeline MAY only: extract facts, deduplicate facts, reweight facts, prune facts, detect contradictions, normalise formatting, flag lint findings.

It MUST NOT: rewrite facts semantically, merge facts into narratives, reinterpret meaning beyond extraction.

### Dream mode config

```yaml
dream:
  mode: local       # local | remote | hybrid
```

- `local`: dream writes facts directly to markdown, commits, pushes to main. No PR gates, no governance. For solo/offline use.
- `remote`: dream commits to a branch, opens a PR, never pushes facts to main. Full L1/L2/L3 governance. Sessions are still direct-pushed (append-only).
- `hybrid`: sessions push directly to main (append-only, no governance needed). ALL fact changes go through PRs — same L1/L2/L3 governance as remote mode. This gives immediate session availability with full fact governance.

### Provider independence

```
Default: free-tier rotation (Cerebras → Groq/Kimi K2 → GLM-4.5 → MiniMax → OpenRouter)
Local:   Ollama or any OpenAI-compatible local endpoint
Paid:    any provider with API key
```

Native memory reads (Gather, S:3) require no LLM calls. LLM is only used for transcript extraction.

> *Free compute depends on free-tier availability from third-party providers. Subject to change. Paid API keys recommended for production reliability.*

### Graceful degradation

| Stage | Condition | Behaviour |
|-------|-----------|-----------|
| **1** | Primary provider fails | Try next in rotation |
| **2** | All remote fail | Attempt local model if configured |
| **3** | No LLM | Native-only dream (S:3 only). Log skipped transcripts. |
| **4** | Native-only ran | Mark `partial` in `MEMORY.md`. Queue full dream next trigger. |

---

## 12  GitHub Dream Governance (gitmem)

Active only in `remote` and `hybrid` dream modes.

### The Refinery Pipeline

```
Raw Sessions (immutable)
    ↓
[L1 — Cheap Model]  runs constantly, high throughput
  → opens PRs: "extracted N facts from session <id>"
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

### PR format

L1 opens:
```
Title: [dream/l1] Extract facts from session 2026-04-08-01JQXYZ...

episodic/2026-04/session-01JQXYZ....md  (new)
facts/topics/devenv.md                  +3 lines

Source: session 2026-04-08-01JQXYZ...
Confidence: 0.7
Encoding strength: 2–3
Proposed provenance: extracted_by groq/llama-3.3-70b
```

### PR label system

```
type: principle          # promotes to principles/ — escalate always
type: consolidation      # merges episodic → facts
type: deletion           # removes existing fact — escalate if S:≥3
type: promotion          # project → user scope
type: hypothesis         # experimental branch, may be discarded
type: gap-fill           # from query-gap proposals — lowest priority
type: lint               # from Lint sub-phase (orphan cleanup, tag drift, convention)
type: supersession       # explicit replacement chain (supersedes/superseded_by)
confidence: high/medium/low
impact: local/global
```

L2 auto-merges: `confidence:high` + `impact:local` + non-destructive (including routine `type: lint` cleanup)
L2 escalates: `impact:global` + principle rewrites + deletions of S:≥3 facts + contradictions + `type: lint` convention violations

### L2 reviewer context

The L2 reviewer prompt MUST include, as context:
- The source session(s) the proposed facts were extracted from
- The current `CONVENTIONS.md` for the project (for phrasing, taxonomy, and entity vocabulary checks)
- The existing fact(s) any proposed change would supersede or contradict
- The `meta/manifest.json` for topic-level situational awareness

Facts proposed in a PR that violate `CONVENTIONS.md` MUST be rejected with a rationale citing the specific convention. This makes `CONVENTIONS.md` an enforcement contract, not just documentation.

### Git primitive mapping

| Memory need | Git feature |
|-------------|-------------|
| Fact versioning | Commit history |
| Conflict detection | Merge conflicts |
| Rejected memories | Closed PRs (audit trail preserved) |
| Memory corrections | Amended PRs |
| Full audit trail | PR comments + reviews |
| Rollback / forgetting | Revert commit |
| Human escalation | PR labels + assignees |
| Selective recall | Cherry-pick |
| Hypothetical reasoning | Experiment branch |
| Suppressed facts | Tombstones |

### Agent token model

```
L1 dream agent    → PR write only, cannot merge
L2 filter agent   → merge on allowed label sets only
Indexer/search    → read-only
Human (org owner) → admin
```

### Signed commits (optional)

Each agent identity MAY sign its commits (GPG or SSH key) for cryptographic provenance.

### GitHub Actions examples

L1 trigger (on session push or schedule):
```yaml
name: L1 Dream
on:
  push:
    paths: ['sessions/**']
  schedule:
    - cron: '0 2 * * *'
jobs:
  dream:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: pip install umx && umx dream --mode remote --tier l1
        env:
          UMX_PROVIDER: groq
          GROQ_API_KEY: ${{ secrets.GROQ_API_KEY }}
```

L2 trigger (on PR open):
```yaml
name: L2 Review
on:
  pull_request:
    types: [opened, synchronize]
    branches: [main]
jobs:
  review:
    runs-on: ubuntu-latest
    if: >-
      contains(github.event.pull_request.labels.*.name, 'type: consolidation') ||
      contains(github.event.pull_request.labels.*.name, 'type: gap-fill') ||
      contains(github.event.pull_request.labels.*.name, 'type: lint') ||
      contains(github.event.pull_request.labels.*.name, 'type: supersession')
    steps:
      - uses: actions/checkout@v4
      - run: pip install umx && umx dream --mode remote --tier l2 --pr ${{ github.event.pull_request.number }}
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
```

External agents like Google Jules can also be wired in as L1 workers.

### Pipeline health observability

Observable from GitHub without extra tooling:
- High L1 rejection rate → cheap model prompt needs tuning
- High escalation rate → domain genuinely ambiguous
- Old PRs sitting open → dream pipeline stalled
- PR volume per cycle → batching calibration

### Multi-project dream

L1 per-project dream consolidates project sessions. L2 cross-project dream (nightly, user repo) reads across `memory-org/*`, extracts cross-project patterns, proposes promotions to `memory-org/user`. Human gate required for cross-project promotions.

### Deep re-derivation

When a better model becomes available:
```bash
umx audit --rederive --all --model claude-opus-5
```
Re-extracts all facts from raw sessions using the new model, opens PRs proposing corrections. Always possible because raw sessions are immutable.

---

## 13  Branch and Commit Conventions

### Branch naming

```
main                              # stable memory — protected in remote/hybrid mode
session/<date>-<ulid>             # raw session dump
dream/l1/<date>-<description>     # L1 extraction proposals
dream/l2/<date>-<description>     # L2 cross-project clustering
proposal/<description>            # principle or promotion proposals
hypothesis/<description>          # experimental (may be discarded)
```

### Commit message convention

```
type(scope): summary

Context: <why>
Confidence: <high|medium|low>
Source: <session ID or dream cycle>
```

Types: `session`, `extract`, `consolidate`, `lint`, `prune`, `promote`, `correct`, `hypothesis`, `tombstone`, `gap-fill`, `supersede`

---

## 14  Memory Lifecycle

```
┌──────────┐     ┌───────────┐     ┌─────────────┐     ┌────────────┐
│  Raw      │────▶│ Candidate │────▶│ Stabilised  │────▶│ Deprecated │
│ (session) │     │ (fragile) │     │ (stable)    │     │ (pruned)   │
└──────────┘     └───────────┘     └─────────────┘     └────────────┘
                       │                  │
                       ▼                  ▼
                  [Rejected]         [Corrected]
                  (PR closed)        (amended PR)
```

| State | `consolidation_status` | Location | Gate to next |
|-------|----------------------|----------|--------------|
| **Raw** | N/A | `sessions/` | Automatic (session end) |
| **Candidate** | `fragile` | Open PR branch or local write | L2 approval or L3 review; survives one dream cycle |
| **Stabilised** | `stable` | `facts/` or `episodic/` on main | Prune threshold or manual deprecation |
| **Principled** | `stable` | `principles/` on main | L3 human review only |
| **Deprecated** | N/A | Removed (preserved in git history + tombstone) | N/A |

Facts at `consolidation_status: fragile` MUST NOT have their strength increased by subsequent access — the consolidation window must close first (Consolidation Theory [9]). Fragile facts are also weighted lower in composite scoring until stabilised, and when injected they SHOULD be marked `[fragile]` in the output so the agent knows to double-check rather than trust them blindly.

### Fragile → stable transition

A fact transitions from `fragile` to `stable` when ANY of the following conditions are met:

1. **Dream cycle survival (default)** — the fact survived one full Dream cycle (Consolidate → Lint → Prune) without rejection, tombstone, or contradiction.
2. **Independent corroboration** — a different tool (different `source_tool`) or the same tool in a session ≥24 hours later (different `source_session`) produces a matching fact. This applies the same independence rule used for corroboration bonuses (Section 5).
3. **Manual confirmation** — the user runs `umx confirm <fact_id>`. This promotes the fact to `stable` AND sets `encoding_strength: 5`, `verification: human-confirmed` (same semantics as a direct manual edit).

The Prune phase applies rules 1 and 2 automatically on every dream cycle. Rule 3 is user-initiated and bypasses the dream cycle entirely.

---

## 15  Promotion Protocol

### Project → User promotion

Eligible when:
1. Same fact appears independently in ≥3 project repos (detected by L2 cross-project dream)
2. Fact at S:≥3 for at least 7 days with no contradictions
3. **L3 human gate required** for all project → user promotions

### Folder → Project promotion

Same fact in ≥3 folder-level memories independently → auto-promote. Threshold configurable.

### Principle promotion

Eligible when:
1. Fact appears in ≥3 independent sessions
2. Stable at S:≥4 for at least 14 days
3. **L3 gate required** — always escalated to human

---

## 16  Injection Architecture

| Injection point | Trigger | Layers injected |
|-----------------|---------|-----------------|
| **Session start** | Tool launch | User-global, tool, machine, project, open tasks (Ovsiankina) |
| **Each prompt** | User message | Folder, keyword-matched |
| **Post-tool hook** | File/command touched | File layer, folder layer |
| **File read append** | File read intercept | File layer |
| **Pre-compact hook** | Context window compaction | Emergency sync: commit all uncommitted facts |
| **Wrapper shim** | Tool startup (no hooks) | Project + tool |

Facts ordered by `relevance_score` descending. Injection stops at budget. No partial facts. Injection MAY emit gap signals (see Section 11).

### Fragile fact marking

When a fact at `consolidation_status: fragile` is selected for injection, it SHOULD be prefixed with `[fragile]` in the injected output. Example:

```
MEMORY:
- postgres runs on 5433 in dev [S:4 verified]
- [fragile] worker pool size is 16 on staging [S:2 pending corroboration]
```

This signals to the agent that the fact is newly extracted and has not yet been stabilised — the agent should verify via a tool call or user check before relying on it, and treat it as a hypothesis rather than ground truth. This operationalises Consolidation Theory [9] at the point of use, not just in storage.

### Hot / warm / cold tier (informal)

umx does not formalise explicit memory tiers, but the read path exhibits three implicit levels which are worth naming:

- **Hot:** `MEMORY.md`, `CONVENTIONS.md`, `manifest.json`, `principles/` — always injected at session start. Subject to a configurable token cap (default: 3000 tokens, `memory.hot_tier_max_tokens`). If hot tier exceeds cap, truncate by relevance score. `umx status` MUST warn when hot tier is at >90% capacity. Maintained by Consolidate.
- **Warm:** `facts/topics/*.md`, scoped folder/file memory — retrieved on demand by `relevance_score`, roughly 4000 tokens per query. Indexed in SQLite FTS.
- **Cold:** `sessions/*.jsonl`, superseded facts, episodic facts beyond recent window — never auto-injected, accessed only via explicit CLI (`umx history`, `umx search --all`, `umx audit`).

Facts move between tiers implicitly via the existing mechanisms: a topic file frequently referenced accumulates hits in `usage.sqlite` and gets summarised into MEMORY.md by the next Consolidate; a hot-tier MEMORY.md entry not referenced for 14 days is a candidate for demotion during Prune. No separate promotion/demotion machinery is required — the existing Consolidate/Prune loops do the work.

### Tool coverage tiers

| Tier | Tools | Mechanism |
|------|-------|-----------|
| **1 — Native hooks** | Claude Code, Gemini CLI, Copilot, Cursor, Codex, Kiro | Hook API |
| **2 — Shim** | Aider, Amp, Vibe | Wrapper prepends memory at launch |
| **3 — MCP** | Any MCP-aware tool | `read_memory` / `write_memory` MCP tools |
| **4 — Manual** | Anything else | `aip hook emit` + wrapper |

### Legacy bridge (opt-in)

umx MAY optionally write condensed facts into legacy files (`CLAUDE.md`, `AGENTS.md`, `.cursorrules`) in the **project repo** within bounded markers:

```markdown
<!-- umx-start: do not edit manually -->
- postgres runs on 5433
- ignore CORS on /api/auth in dev
<!-- umx-end -->
```

**This is the only place umx writes to a project repo**, and it is opt-in. No session data, no provenance, no metadata — bare facts only for tool compatibility.

---

## 17  Context Budget

```bash
umx inject --cwd . --tool aider --max-tokens 4000
```

If `--max-tokens` is not specified, umx infers the budget from the tool adapter's known limit. Injection halts before exceeding the budget. No partial fact inclusion — a fact is either fully included or excluded.

> For v1, simple descending-sort-with-budget is used. The knapsack optimisation problem (a large high-relevance fact blocking many small useful facts) is acknowledged; future versions may implement greedy knapsack packing.

---

## 18  Git Strategy

- **One topic per file** — enforced by format
- **`local/` is gitignored** — private/secret facts never create merge conflicts
- **JSON, SQLite, `usage.sqlite` are local-only** — MUST NOT be committed
- **`sessions/` is append-only** — new files only, zero merge conflicts
- **Small files over large files** — git scales better with many small files

### Write path

In **local** dream mode:
```
agent writes fact
  → append to local markdown in $UMX_HOME/projects/<slug>/ (immediate)
  → update SQLite index (immediate)
  → git commit to memory repo locally (immediate, offline-safe)
  → push queue (async, retried on failure)
```

In **remote/hybrid** dream mode:
```
agent writes raw session
  → append JSONL to sessions/ (immediate)
  → git commit locally (immediate)
  → push sessions to main (direct, append-only)
  → dream pipeline commits facts to branch, opens PR (never pushes facts to main)
```

### Merge rule

```
- Identical fact_id           → merge metadata (take higher trust_score)
- Conflicting text same ID    → new conflict entry in conflicts.md
- Same semantic dedup key     → corroboration candidate
- Never silently overwrite a higher-strength fact
```

### Arbitrator agent

When a git merge conflict occurs on push, an arbitrator agent MAY:
1. Read the `<<<<<<< HEAD` markers
2. Evaluate both versions using composite score
3. Commit the resolution
4. If scores are tied, escalate to human via PR

Implementation is tool-specific; the spec defines the interface.

---

## 19  Session Logs

Raw session logs are stored in `sessions/` and are **immutable after the pre-commit redaction pass**. They are the ground truth from which all derived memory can be verified or re-derived.

### Format

Session files are pure JSONL. There is no YAML. The first line is a `_meta` record; all subsequent lines are event records.

Session ID format: `YYYY-MM-DD-<ulid>` (e.g., `2026-04-08-01JQXYZ1234567890`).

```
sessions/
  2026/
    04/
      2026-04-08-01JQXYZ1234567890.jsonl
      2026-04-08-01JQXYZ2345678901.jsonl
```

### `_meta` record (first line)

```jsonl
{"_meta":{"session_id":"2026-04-08-01JQXYZ1234567890","project":"boz","topics":["adb","fastboot"],"tool":"claude-code","machine":"desktop","started":"2026-04-08T10:23:01Z"}}
```

| Field | Required | Description |
|-------|----------|-------------|
| `session_id` | MUST | `YYYY-MM-DD-<ulid>` |
| `project` | MUST | Project slug |
| `tool` | MUST | Tool name |
| `machine` | SHOULD | Hostname |
| `started` | MUST | ISO 8601 |
| `topics` | MAY | Topic hints for clustering |

### Event records (subsequent lines)

```jsonl
{"ts":"2026-04-08T10:23:01Z","role":"user","content":"..."}
{"ts":"2026-04-08T10:23:04Z","role":"assistant","content":"..."}
{"ts":"2026-04-08T10:23:10Z","role":"tool_use","tool":"bash","input":"..."}
{"ts":"2026-04-08T10:23:11Z","role":"tool_result","content":"..."}
```

| Field | Required | Description |
|-------|----------|-------------|
| `ts` | MUST | ISO 8601 timestamp |
| `role` | MUST | `user` / `assistant` / `tool_use` / `tool_result` |
| `content` | MUST | Message content |
| `tool` | MAY | Tool name (for tool_use/tool_result) |
| `input` | MAY | Tool input |
| `tool_call_id` | MAY | For tool_result correlation |

### Pre-commit redaction

Before a session file is committed, a **synchronous local pattern scanner** MUST run to detect common secret formats:

- API keys (AWS, GCP, Anthropic, OpenAI, Stripe, etc.)
- Bearer tokens and JWTs
- Connection strings with credentials
- Private keys (PEM headers)
- User-defined patterns from `config.yaml` `sessions.redaction_patterns`

Matches are replaced with `[REDACTED:<type>]` placeholders. Sessions are immutable **after** redaction — the redaction pass is part of the write pipeline, not a post-hoc edit.

**Redaction fails closed.** If the redaction scanner crashes, times out, or produces an error for any reason (regex catastrophic backtracking, malformed JSONL, OOM), the session MUST be quarantined — written to `local/quarantine/` — and MUST NOT be committed or pushed. `umx status` MUST surface quarantined sessions. Manual resolution required: inspect, re-run redaction, or discard.

**Fact-level redaction.** The same redaction patterns used for sessions MUST also be applied to candidate facts BEFORE they enter `facts/`, `local/private/`, or any injection path. `.gitignore`-based routing to `local/private/` is a scope heuristic, not a security boundary. The redaction pass is the security boundary.

**Opt-in raw mode:** `sessions.redaction: none` for users who want unredacted sessions. These MUST NOT be pushed to GitHub — enforced via pre-push hook. Local-only raw sessions are permitted.

**Emergency purge:** `umx purge --session <id>` rewrites git history (BFG or `git filter-repo`) for secret removal. This breaks the immutability guarantee for that session and is logged in the audit trail. For jurisdictions requiring data deletion (GDPR), this is the escape hatch.

**Startup sweep.** On session start, before pull, the implementation MUST scan for uncommitted session files from prior runs (crashed or killed processes). Any found uncommitted sessions MUST be committed and pushed before proceeding. This prevents session data loss from non-graceful shutdowns.

### Session capture methods

| Tool | Capture method | Status |
|------|---------------|--------|
| Claude Code | `~/.claude/projects/` session data | Needs reverse-engineering |
| Aider | `.aider.chat.history.md` and session logs | Adapter needed |
| AIP-managed tools | `workspace/events.jsonl` (structured, preferred) | Available now |
| MCP-capable tools | `write_memory` MCP tool emits session events | Available now |
| Other | Manual `umx collect` or shim-based capture | Shim-dependent |

This is the biggest implementation risk. The spec honestly acknowledges which tools expose transcripts and which don't.

### Session log retention

Sessions are append-only and never deleted. Retention strategy:

- Active sessions (last 90 days): uncompressed
- Archive sessions (older): gzip per month, keep index
- Session index: `sessions/YYYY/MM/YYYY-MM-index.json` mapping session IDs to metadata

```
sessions/
  2026/
    04/
      2026-04-08-01JQXYZ....jsonl          # recent
    01/
      2026-01-archive.jsonl.gz             # compressed monthly
      2026-01-index.json                   # session ID → metadata
```

### Session log uses

1. **Audit baseline** — SotA model traces facts back to source sessions during PR review
2. **Re-derivation** — re-run extraction with a better model ("deep therapy")
3. **Brute-force retrieval** — agents grep/scan sessions directly
4. **Memory health audit** — periodic SotA pass: "are these facts accurate and complete?"

---

## 20  Search and Retrieval

### Two tracks

**Fast track (SQLite FTS):** "what do I know about X" queries. Built from markdown, local-only, incrementally rebuilt on pull.

**Raw track (direct session scan):** "what actually happened around X" queries. Full context.

### SQLite schema (canonical, defined once)

```sql
-- Local-only build artifact. MUST NOT be committed. Rebuild from markdown.
-- MUST be opened with PRAGMA journal_mode=WAL for concurrent reader/writer support.
CREATE TABLE _meta (
  key TEXT PRIMARY KEY,
  value TEXT
);
-- Stores: schema_version, last_indexed_sha

CREATE TABLE memories (
  id TEXT PRIMARY KEY,          -- ULID fact_id
  repo TEXT,                    -- which memory repo (for cross-project queries)
  scope TEXT,
  topic TEXT,
  content TEXT,
  tags TEXT,                    -- JSON array
  encoding_strength INTEGER,
  verification TEXT,
  source_type TEXT,             -- ground_truth_code | user_prompt | tool_output | llm_inference | dream_consolidation | external_doc
  consolidation_status TEXT,    -- fragile | stable
  task_status TEXT,             -- null | open | blocked | resolved | abandoned
  supersedes TEXT,              -- fact_id this replaces (NULL if none)
  superseded_by TEXT,           -- fact_id that replaced this (NULL if current)
  created_at TEXT,
  git_sha TEXT,
  pr TEXT
);

CREATE INDEX idx_memories_active ON memories(superseded_by) WHERE superseded_by IS NULL;
CREATE INDEX idx_memories_topic ON memories(repo, topic);
CREATE INDEX idx_memories_task ON memories(task_status) WHERE task_status IN ('open', 'blocked');

CREATE VIRTUAL TABLE memories_fts USING fts5(
  content, tags,
  tokenize='unicode61'
);
```

### Usage telemetry schema (usage.sqlite)

```sql
-- Local-only. MUST NOT be committed. Tracks retrieval metrics.
-- MUST be opened with PRAGMA journal_mode=WAL.
CREATE TABLE usage (
  fact_id TEXT NOT NULL,
  last_referenced TEXT,          -- ISO 8601 — moved here from inline metadata
  reference_count INTEGER DEFAULT 0,
  injected_count INTEGER DEFAULT 0,  -- times included in context
  cited_count INTEGER DEFAULT 0,     -- times referenced in output
  last_session TEXT,                  -- session ID of last reference
  PRIMARY KEY (fact_id)
);
```

This separation ensures `last_referenced` updates (which happen on every citation) never create merge conflicts in committed markdown files.

Default ranking function: `bm25()`.

### Incremental rebuild

On `git pull`, umx checks `git diff --name-only <last_indexed_sha> HEAD -- '*.md'` to identify changed files. Only those are re-indexed. Full rebuild occurs on: first clone, `schema_version` change, or `umx rebuild-index --force`.

The `last_indexed_sha` is stored in the SQLite `_meta` table.

---

## 21  Tombstones and Forgetting

### Problem

`umx forget` removes facts, but immutable sessions + re-derivation can resurrect them. Without a suppression mechanism, deleted facts reappear on the next dream cycle.

### Tombstone file

`meta/tombstones.jsonl` (append-only):

```jsonl
{"fact_id":"01JQXYZ...","match":"postgres.*5432","reason":"port changed to 5433","author":"human","created":"2026-04-08T14:30:00Z","suppress_from":["gather","rederive","audit"],"expires_at":null}
```

- Tombstones are checked during Gather, re-derivation, and audit. Matching facts are suppressed.
- Tombstones MAY have optional `expires_at` for temporary suppression.
- `umx forget --fact <id>` creates a tombstone + removes the fact from markdown.
- `umx forget --topic <topic>` creates tombstones for all facts in that topic.

### Supersession vs tombstone

umx distinguishes two forms of "no longer current":

- **Superseded** — the fact was *replaced* by a newer fact (e.g., port 5432 → 5433). Use the `supersedes`/`superseded_by` fields (Section 5). The old fact is retained for audit and is walkable via `umx history --fact <id>`.
- **Tombstoned** — the fact was *wrong* or no longer applies and should be actively suppressed from re-derivation (e.g., a hallucinated fact that keeps getting re-extracted from session transcripts). Use `meta/tombstones.jsonl`.

Supersession is the preferred mechanism for temporal evolution of facts; tombstones are the escape hatch for incorrect or unwanted facts.

### Inline deprecation (deprecated)

Earlier spec drafts used a `[DEPRECATED]` marker inline. This is superseded by the `supersedes`/`superseded_by` schema fields. Parsers SHOULD still accept the `[DEPRECATED]` marker for backwards compatibility but MUST NOT emit it for new facts.

---

## 22  Failure Modes

| Failure | Cause | Mitigation |
|---------|-------|------------|
| **Incorrect high-strength fact** | Tool-native memory error | Composite scoring dilutes; `verification: self-reported` scores lower; user override → S:5 |
| **Extraction hallucination** | LLM misinterpretation | Low initial strength; decay + pruning; raw sessions for audit; Bartlett schema-conflict flagging |
| **Summarisation drift** | Repeated LLM rewriting | Pipeline constraint: extract only, never rewrite. Raw sessions are immutable baseline. |
| **Premature promotion** | Fragile fact used before consolidation | `consolidation_status: fragile` prevents strength increase until dream cycle completes |
| **Interference (contradictions)** | Old fact competes with new | `conflicts_with` field + conflict detection in Consolidate; explicit `supersedes`/`superseded_by` chain preserves history |
| **Silent hallucination propagation** | LLM inference extracted as fact | `source_type: llm_inference` scored lowest; requires corroboration to reach S:3 |
| **Convention drift** | Facts violate project norms across sessions | `CONVENTIONS.md` enforced by L2 reviewer; Lint sub-phase flags violations |
| **Lint-discoverable defects** | Orphan IDs, tag drift, stale refs | Weekly Lint sub-phase in Consolidate; `[dream/lint]` PR |
| **Resurrected deleted facts** | Re-derivation from sessions | Tombstones in `meta/tombstones.jsonl` suppress matching facts |
| **Over-injection** | Weak relevance filtering | Relevance scoring; budget enforcement; `meta/usage.sqlite` calibration data |
| **Stale facts dominating** | High strength but outdated | Recency + time decay + `expires_at` TTL |
| **Secrets in sessions** | API keys in transcripts | Pre-commit redaction pass; pre-push hook blocks unredacted raw mode |
| **Secrets in prompts** | `local/secret/` injected | `local/secret/` is never injected; only `local/private/` is |
| **Metadata loss via manual edit** | User editing markdown | Parser regenerates on next pass; edited lines → S:5 |
| **Concurrent dream runs** | Multiple tools simultaneously | Lock file; one dream per 24h per project |
| **LLM providers unavailable** | All free tiers rate-limited | Graceful degradation; `NOTICE` at next session start |
| **PR volume spam** | L1 too aggressive | Batch: one PR per dream cycle per repo. Require N-session evidence for principles. |
| **Cognitive drift** | Unchecked L1 overwrites | L2 required for `facts/`; L3 required for `principles/` |
| **Hallucinated principles** | Cheap model promotes aggressively | ≥3 sessions + S:≥4 for 14 days + L3 gate |
| **Merge conflict on push** | Concurrent agents | Arbitrator agent; append-only sessions minimise this |
| **Repo bloat** | Session accumulation | Monthly gzip + index for archived sessions |
| **Schema migration** | Format change | `meta/schema_version`; dream agents check before processing |
| **Orphaned scoped memory** | Project file/folder renamed | Orient phase detects; proposes rename/migration PR. Manual: `umx migrate-scope` |
| **Slug collision** | Two repos with same name | Collision detection in `umx init-project`; override via `.umx-project` |
| **`last_referenced` inflation** | Every injection counts as retrieval | Only explicit use updates `last_referenced` in `usage.sqlite`; silent injection does not |
| **Redaction scanner failure** | Regex crash, malformed input | Session quarantined to `local/quarantine/`; MUST NOT be committed; `umx status` surfaces |
| **Uncommitted sessions from crash** | Process killed mid-write | Startup sweep detects and commits orphaned sessions before new session starts |
| **Context compaction data loss** | Tool compresses context mid-session | `pre_compact` hook triggers emergency sync of uncommitted facts |
| **Ground truth staleness** | Code changed since fact extraction | Orient phase checks anchored paths; demotes to `fragile` if source changed |
| **False contradiction (env mismatch)** | Facts true in different envs | `applies_to` schema prevents non-overlapping facts from being flagged as contradictions |

---

## 23  Conformance

### Conformance levels

Tools may adopt UMX incrementally. Three conformance levels are defined:

**UMX-Read** (minimal adoption): Tool can consume UMX memory but does not write.

| Requirement | Level |
|-------------|-------|
| Resolve project slug (via `.umx-project`, git remote, or directory name) | MUST |
| Read injected memory blocks from umx | MUST |
| Honour `[fragile]` markers in injected content (do not trust blindly) | SHOULD |
| Support at least one injection mechanism (hook, shim, MCP, or manual) | MUST |

**UMX-Write** (session producer): Tool can produce sessions for UMX consumption.

| Requirement | Level |
|-------------|-------|
| All UMX-Read requirements | MUST |
| Write session logs in the required JSONL schema (Section 19) | MUST |
| Support pre-commit redaction | MUST |
| Emit gap signals with `resolution_context` when queries return incomplete results and the agent works around the gap | MAY |

**UMX-Full** (complete participant): Tool can read, write, and produce governed facts.

| Requirement | Level |
|-------------|-------|
| All UMX-Write requirements | MUST |
| Parse and write markdown facts with inline metadata (including `source_type`, `verification`, `consolidation_status`) | MUST |
| Tag every produced fact with a valid `source_type` enum value | MUST |
| Respect `CONVENTIONS.md` when writing proposed facts | MUST |
| NOT commit derived artifacts (JSON, SQLite) to memory repos | MUST NOT |
| Track provenance fields (`xby`, `aby`, `pr`) | MUST |

A tool claiming "umx-compatible" without further qualification MUST satisfy at least UMX-Read. Tools SHOULD declare their conformance level explicitly.

---

## 24  Comparison

| Tool | Cross-tool | Hierarchical | Git-native | Auto-extract | Audit trail | Encoding strength | PR governance | Free compute* |
|------|-----------|-------------|-----------|-------------|-------------|-------------------|---------------|:---:|
| **umx + gitmem** | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| MemPalace | ✗ | ✓ | ✗ | ✓ | ✗ | ✗ | ✗ | ✓ |
| DiffMem | ✗ | ✗ | ✓ | ~ | ✗ | ✗ | ✗ | ~ |
| Mem0 | ~ | ✗ | ✗ | ✓ | ✗ | ~ | ✗ | ✗ |
| Karpathy LLM Wiki | ✗ | ✗ | ~ | ✓ | ✗ | ✗ | ✗ | ✗ |
| Copilot cross-agent | ✗ | ✗ | ✗ | ✓ | ✗ | ✗ | ✗ | ✗ |
| CLAUDE.md hierarchy | ✗ | ✓ | ✓ | ✗ | ✗ | ✗ | ✗ | ✓ |

`✓` fully supported · `~` partial · `✗` not supported

\* *Free compute depends on free-tier availability from third-party providers. Subject to change.*

**vs Karpathy's LLM Wiki pattern:** Karpathy's wiki shares the "LLM maintains markdown, raw sources are immutable" architecture. umx adds: git-native version history (wiki has no history), PR-based governance (wiki has no review mechanism), encoding strength and provenance (wiki has no quality differentiation), multi-agent support (wiki is single-user single-agent), and tombstone-based forgetting (wiki has no suppression mechanism). The wiki pattern is umx without governance, history, provenance, or multi-agent support.

---

## 25  Python Package Structure

```
umx/
├── __init__.py
├── cli.py                  # `umx` and `aip mem` subcommands
├── scope.py                # scope hierarchy + project discovery + slug collision
├── memory.py               # read/write MEMORY.md + topic files
├── strength.py             # encoding strength + composite scoring + verification
├── identity.py             # ULID generation + semantic dedup key
├── inject.py               # injection + relevance scoring + gap signal emission
├── budget.py               # context budget inference + enforcement
├── sessions.py             # session log write + JSONL + _meta schema
├── redaction.py            # pre-commit secret scanning + pattern matching
├── search.py               # SQLite FTS: build, incremental rebuild, query
├── manifest.py             # meta/manifest.json maintenance (topics, uncertainty_hotspots, knowledge_gaps)
├── tombstones.py           # tombstone CRUD + suppression checks
├── supersession.py         # supersedes/superseded_by chain walking (umx history)
├── conventions.py          # CONVENTIONS.md parse + enforcement hooks
├── tasks.py                # task_status lifecycle (open/blocked/resolved/abandoned, umx resume)
├── adapters/
│   ├── claude_code.py
│   ├── aider.py
│   ├── copilot.py
│   └── generic.py
├── dream/
│   ├── pipeline.py         # Orient → Gather → Consolidate → Lint → Prune
│   ├── gates.py            # three-gate trigger + gap trigger + lock file
│   ├── extract.py          # LLM extraction prompt + source_type tagging + schema_conflict flagging
│   ├── gitignore.py        # .gitignore parsing → exclusion rules
│   ├── conflict.py         # conflict detection + conflicts_with + supersession chains
│   ├── lint.py             # Lint sub-phase: contradictions, orphans, tag drift, convention checks
│   ├── arbitrator.py       # merge conflict resolution
│   ├── providers.py        # provider rotation + local fallback
│   ├── decay.py            # exponential recency decay
│   ├── consolidation.py    # fragile → stable lifecycle (3-rule transition)
│   └── notice.py           # NOTICE writer
├── gitmem/
│   ├── sync.py             # push queue, pull on session start
│   ├── pr.py               # open/review/merge PRs
│   ├── governance.py       # L1/L2/L3 tier logic
│   ├── audit.py            # session → fact audit + deep therapy
│   ├── org.py              # org layout, repo creation, bootstrap, slug collision
│   └── actions.py          # GitHub Actions workflow generation
├── hooks/
│   ├── session_start.py
│   ├── post_tool_use.py
│   ├── pre_compact.py          # emergency sync on context compaction
│   └── session_end.py
├── shim/
│   ├── aider.py
│   └── generic.py
├── bridge.py               # legacy file bridge (CLAUDE.md / AGENTS.md markers)
├── doctor.py               # umx doctor: auth, push queue, locks, schema, orphans, quarantine
├── viewer/
│   └── server.py
└── mcp_server.py           # read_memory / write_memory MCP tools
```

### CLI surface

```bash
umx init          --org <n>
umx init-project  --slug <n>
umx inject        --cwd . --tool aider [--max-tokens N]
umx collect       --cwd . --tool aider
umx dream         --cwd . [--force] [--mode local|remote|hybrid] [--tier l1|l2]
umx view          --cwd . [--scope project] [--min-strength N]
umx tui           --cwd .
umx status        --cwd .
umx conflicts     --cwd .
umx gaps          --cwd .                                    # list pending gap proposals
umx forget        --cwd . --fact <id> | --topic <topic>
umx promote       --cwd . --fact <id> --to user
umx confirm       --cwd . --fact <id>                        # manual stabilise: fragile → stable + S:5
umx history       --cwd . --fact <id>                        # walk supersedes/superseded_by chain
umx resume        --cwd . [--include-abandoned]              # list open/blocked tasks from recent sessions
umx meta          --cwd . --topic <name>                     # manifest entry for a topic
umx merge         --cwd .
umx audit         --cwd . [--session <id>] [--rederive] [--all] [--model <n>]
umx sync          [--all]
umx purge         --session <id>                             # emergency secret removal
umx rebuild-index [--force]
umx migrate-scope --from <old-path> --to <new-path>
umx doctor        [--fix]                                  # diagnostics: auth, push queue, locks, schema, orphans, quarantine
umx secret        get <key> | set <key> <value>              # local/secret/ access
umx import        --tool claude-code                         # bulk import existing memory
```

---

## 26  Viewer / Editor

Local web UI. `umx view`. No persistent process.

| Feature | Description |
|---------|-------------|
| **Memory tree** | Full scope hierarchy: user → machine → project → folder → file |
| **Fact view** | Source tool, session, strength, verification, source_type, confidence, provenance, PR link, consolidation_status, task_status, supersession chain |
| **Inline edit** | Edit any fact → promoted to S:5 on save |
| **Confirm action** | One-click `umx confirm` — promote fragile fact to stable + S:5 |
| **Promote / demote** | Move fact to higher or lower scope |
| **Conflict panel** | Flagged conflicts side-by-side with scores and `conflicts_with` links |
| **Supersession timeline** | Visualise a fact's evolution via `supersedes`/`superseded_by` chain |
| **Tombstone panel** | Active tombstones, expiry dates, suppression scope |
| **Task board** | Open / blocked / resolved / abandoned tasks — Ovsiankina resumption view |
| **Strength + source filter** | Filter by `encoding_strength`, `verification`, and `source_type` (e.g., "show only ground_truth_code facts") |
| **Lint report** | Latest Lint sub-phase findings: contradictions, orphan IDs, tag drift, convention violations |
| **Dream log** | Last dream: phases, facts added/removed/conflicted, provider, tokens |
| **Session browser** | Browse raw sessions by date/project/tool/machine |
| **Audit view** | Trace any fact back to source session and approval PR |
| **Gap proposals** | Pending gap-fill proposals from `meta/gaps.jsonl` with resolution_context |
| **Pipeline health** | L1 rejection rate, escalation rate, stale PR count |
| **Manifest coverage** | Topics, uncertainty_hotspots, knowledge_gaps from `meta/manifest.json` |
| **Conventions viewer** | Read-only display of `CONVENTIONS.md` with edit hand-off to `$EDITOR` |
| **Narrative view** | Optional synthesis of atomic facts into prose. Presentation only. |

---

## 27  Configuration Reference

Complete `$UMX_HOME/config.yaml` schema:

```yaml
# Organisation
org: my-memory-org                    # GitHub org name (required)
github_token: null                    # PAT. Override: UMX_GITHUB_TOKEN env var

# Project defaults
project:
  slug_format: name                   # name | owner-name

# Dream pipeline
dream:
  mode: local                         # local | remote | hybrid
  provider_rotation:                  # ordered list of providers
    - cerebras
    - groq
    - glm
    - minimax
    - openrouter
  local_model: null                   # e.g., ollama/llama3.1
  paid_provider: null                 # e.g., anthropic
  paid_api_key: null
  lint_interval: weekly               # weekly | daily | never — Lint sub-phase cadence

# Decay and scoring
decay:
  lambda: 0.023                       # default ~30 day half-life
  per_project:                        # optional per-project overrides
    boz: 0.046                        # fast-moving project

# Pruning
prune:
  threshold: 2                        # minimum encoding_strength to survive
  min_age_days: 7                     # never prune facts younger than N days (protects incubating projects)
  abandon_days: 30                    # open/blocked tasks auto-transition to abandoned after N days

# Memory
memory:
  index_max_lines: 200                # MEMORY.md index size limit
  hot_tier_max_tokens: 3000           # hot tier token budget (MEMORY.md + principles + CONVENTIONS.md)

# Sessions
sessions:
  redaction: default                  # default | none (none = local-only, no push)
  redaction_patterns: []              # additional regex patterns beyond built-in set
  retention:
    active_days: 90                   # days before archival
    compression: gzip                 # gzip | none

# Search
search:
  rebuild: incremental               # incremental | full
  backend: fts5                       # fts5 | hybrid (hybrid = fts5 + local embeddings, future)

# Bridge (legacy tool compatibility)
bridge:
  enabled: false
  targets: [CLAUDE.md, AGENTS.md]
  max_facts: 20

# Scoring weights (require empirical tuning — expose all as config)
# Three separate weight sets for trust, relevance, and retention scores (see §6)
weights:
  trust:                              # used for conflict resolution
    strength: 1.0
    confidence: 0.5                   # informational until calibrated
    corroboration: 0.4
    verification: 0.3
    source_type: 0.4
  relevance:                          # used for injection prioritisation
    scope_proximity: 1.0
    keyword_overlap: 0.8
    recent_retrieval: 0.3
    encoding_strength: 0.5
    context_match: 0.4
    task_salience: 0.5
  retention:                          # used for pruning decisions
    strength: 1.0
    recency: 0.3
    usage_frequency: 0.4
    verification: 0.3
```

---

## 28  Roadmap

| Phase | Milestone | Deliverables |
|-------|-----------|--------------|
| **0** | Foundation | Scope spec · file format · fact schema (ULID, verification, `source_type`, consolidation_status, conflicts_with, supersedes/superseded_by, `applies_to`, code anchors) · three-score model (trust/relevance/retention) · conflict format · private/secret split · schema_version · local path convention · `CONVENTIONS.md` template · extraction prompt skeleton (Appendix A) · L2 reviewer prompt skeleton (Appendix B) |
| **1** | Core library | `scope.py` + `memory.py` + `strength.py` + `identity.py` · trust/relevance/retention scoring · AIP hook integration · session log write + redaction (fail-closed) · project discovery + slug collision · quarantine system · `umx doctor` · **test harness: known sessions → expected facts at expected strengths** · **benchmark framework: extraction accuracy, recall quality** |
| **2** | Dream pipeline | Orient (reads CONVENTIONS.md — missing=skip+notice, checks `ground_truth_code` anchors) → Gather (source_type tagging, gap emission with tool-driven triggers) → Consolidate (supersession chains, `applies_to` conflict resolution) → Lint (weekly: orphans, tag drift, convention violations) → Prune (min_age_days, abandon_days, MEMORY.md generation algorithm, manifest rebuild) · 3-gate + gap trigger · provider rotation · `.gitignore` safety + fact-level redaction · tombstones · consolidation_status lifecycle · schema_conflict flagging · `pre_compact` hook |
| **3** | Read adapters | Claude Code · Aider · generic · hybrid gather · corroboration bonus (independence rules, split `cort`/`corf`) · SQLite FTS (WAL mode, incremental rebuild, source_type indexed) · usage.sqlite telemetry · **bulk import: `umx import --tool claude-code`** |
| **4** | Injection layer | Tier 1–4 hooks/shims/MCP · budget enforcement · hot-tier token cap · relevance scoring (encoding context) · task salience injection · `[fragile]` marker · gap signal emission · mid-session scratchpad fast path · legacy bridge · startup sweep for orphaned sessions |
| **5** | gitmem backend | `umx init` bootstrap · GitHub org layout · push queue · PR pipeline · L1/L2/L3 governance (reconciled label system) · CONVENTIONS.md in L2 reviewer context · audit trail · `umx sync` · Actions workflow templates · Lint PR automation · `meta/processing.jsonl` for distributed dream locking |
| **6** | Viewer / editor | Web viewer · strength/scope/verification/source_type filters · conflict UI · supersession timeline · task board · tombstone panel · lint report · session browser · audit view · gap proposals · pipeline health · manifest coverage · TUI |
| **7** | Hardening | `umx merge` · arbitrator agent · `umx confirm` / `umx history` / `umx resume` / `umx meta` CLI · schema migration tooling · session compression · time decay tuning · signed commits · hypothesis branches · orphaned scope detection · inline metadata conformance test corpus |
| **8** | Cross-project | Cross-project dream · promotion protocol · deep re-derivation (`umx audit --rederive`) · principle governance |
| **9** | Ecosystem | `aip mem` integration · published spec · legacy bridge · third-party adoption · optional `procedures/` directory (when/then rules, debugging playbooks) · optional local embeddings search backend (`search.backend: hybrid`) |

---

## 29  Non-Goals

- **No memory in project repos.** All memory lives in the memory org. Only optional: `.umx-project` (slug) and legacy bridge markers (opt-in).
- **No cloud-only sync.** GitHub is the remote; local is always functional. Offline-capable by default.
- **No auto-commit to main (in remote/hybrid mode).** All dream fact-writes go through PR review. `local` mode permits direct writes for solo/offline use.
- **No multi-user shared memory in v1.** umx is single-user. Team memory sharing is a future consideration.
- **No cross-machine secret syncing.** `local/secret/` is never pushed. Use a dedicated secret manager.
- **No vector search in v1.** SQLite FTS covers the common case. Embeddings are a future opt-in.
- **No pane-read mid-stream injection.** Too risky as default.
- **No auto-injection of sensitive data.** `.gitignore` exclusion enforced in Gather. `local/secret/` never injected.
- **No narrative merging in storage.** Facts are atomic. Narrative synthesis is viewer-only.
- **No opinions on which tool you use.** umx works identically with one CLI or ten.
- **No persistent daemons required.** Sync and indexing happen at session boundaries.
- **No multi-language normalisation in v1.** Facts are stored in whatever language they were expressed in.

---

## 30  Relation to AIP

umx is a natural extension of AIP. AIP provides the orchestration substrate (tmux + filesystem event bus + hook normalisation). umx adds the memory layer on top.

- AIP hook proxy normalises payloads from all Tier 1 CLIs. umx hook handlers consume those events.
- AIP shim watch provides lifecycle events for Tier 2 CLIs. umx shim handles collection.
- `workspace/events.jsonl` feeds the Gather phase as a structured session source (preferred over raw transcripts).
- umx ships as `aip mem` subcommands alongside its standalone CLI.
- gitmem's GitHub org is separate from the project org — memory governance is isolated from code governance.

**Boundary:** AIP owns orchestration and inter-agent communication. umx owns memory scoping, extraction, strength, injection, and governance.

---

## 31  References

[1] Tulving, E. (1972). *Episodic and semantic memory.* In E. Tulving & W. Donaldson (Eds.), Organisation of Memory. Academic Press.

[2] Schacter, D. L. (1987). *Implicit memory: History and current status.* Journal of Experimental Psychology: Learning, Memory, and Cognition, 13(3), 501–518.

[3] Anderson, J. R. (1983). *The Architecture of Cognition.* Harvard University Press. — ACT-R base-level learning.

[4] The New Stack (2026, January 16). *Memory for AI Agents: A New Paradigm of Context Engineering.*

[5] Ebbinghaus, H. (1885). *Über das Gedächtnis.* — Forgetting curve: basis for time decay.

[6] Observed patterns in production memory systems (2025–2026). Claude Code autoDream, Cursor memory layer, Windsurf context engine.

[7] Mnemoverse Documentation (2025). *Production Memory Systems: Implementation Analysis.*

[8] McGeoch, J. A. (1932). Forgetting and the law of disuse. *Psychological Review*, 39(4), 352–370. — Interference theory: retroactive and proactive interference between similar memories. umx uses `conflicts_with` pointers and contradiction detection to implement interference-based suppression.

[9] Dudai, Y. (2004). The neurobiology of consolidations, or, how stable is the engram? *Annual Review of Psychology*, 55, 51–86. — Consolidation theory: memories are fragile when first formed and stabilise over time, especially during offline processing. umx implements this via `consolidation_status: fragile | stable` and the dream pipeline's stabilisation pass.

[10] Tulving, E. & Thomson, D. M. (1973). Encoding specificity and retrieval processes in episodic memory. *Psychological Review*, 80(5), 352–373. — Recall is enhanced when retrieval context matches encoding context. umx's optional `encoding_context` field and context-match relevance scoring implement this.

[11] Ovsiankina, M. (1928). Die Wiederaufnahme unterbrochener Handlungen. *Psychologische Forschung*, 11, 302–379. — Tendency to resume incomplete tasks (more robustly supported than Zeigarnik's memory-advantage claim). umx implements this via `task_status` field and Ovsiankina salience bonus at session start.

[12] Nelson, T. O. & Narens, L. (1990). Metamemory: A theoretical framework and new findings. *The Psychology of Learning and Motivation*, 26, 125–173. — Metacognitive monitoring and control: distinguishing "I know that I don't know" from "I know but can't retrieve." umx implements monitoring via `meta/manifest.json` domain index.

[13] Bartlett, F. C. (1932). *Remembering: A Study in Experimental and Social Psychology.* Cambridge University Press. — Schema theory and reconstructive memory: remembering is reconstruction guided by existing knowledge structures, not faithful replay. umx addresses this by constraining the dream pipeline to extract-only (no rewriting) and flagging schema-conflicting conventions.

[14] Johnson, M. K., Hashtroudi, S., & Lindsay, D. S. (1993). Source monitoring. *Psychological Bulletin*, 114(1), 3–28. — Source monitoring framework: memory includes attribution of origin. umx's `provenance` field and `verification` status implement source tracking to prevent hallucination propagation.

---

## Open Questions

- **Trust/relevance/retention score weights** — require empirical tuning. All weights exposed as config initially. Three-score split (§6) is architecturally correct but weight values are estimates.
- **Time decay λ tuning** — default λ 0.023 is a starting point.
- **Copilot / Gemini native memory formats** — adapters blocked until formats documented.
- **L1 rate limiting** — PRs per dream cycle before batching kicks in TBD.
- **Cross-project dream cadence** — evidence threshold for project → user promotion TBD.
- **Session capture from closed tools** — biggest implementation risk (see Section 19).
- **Spaced repetition scheduler (Rehearse phase)** — the spacing effect [5] and testing effect (Roediger & Karpicke 2006) imply an active SRS in the dream pipeline that surfaces near-forgetting memories for reinforcement via synthetic retrieval queries. The v0.7 plan proposed **Rehearse** as a fifth dream phase between Consolidate/Lint and Prune. It is deferred to post-v1 because the infrastructure (SRS scheduler, decay-curve flattening for reinforced memories, bounded rehearsal budget per cycle) requires the core pipeline and telemetry (`usage.sqlite`) to be stable first. The architectural hook point is after Lint, before Prune.
- **Bi-temporal fact validity** — `valid_from` / `valid_to` fields with `umx query --as-of <date>` enables point-in-time queries. Deferred: most coding facts are point-in-time current state. `expires_at` covers the common TTL case. `supersedes`/`superseded_by` chains provide implicit temporal evolution. Revisit if time-sensitive facts (deploy targets, feature flags, API versions) prove problematic.
- **Encoding depth / processing depth** — Levels of Processing (Craik & Lockhart 1972) suggests facts formed through action-outcome cycles should carry higher initial strength. Optional `processing_depth` field deferred to post-v1. Partially subsumed by the `source_type` enum.
- **When to create project memory** — rule of thumb: if the project has architecture, it gets memory.
- **Inline metadata grammar formalisation** — §9 defines the field table. A formal EBNF grammar, escaping rules for `-->` inside JSON values, and a round-trip conformance test corpus (5-10 examples) are deferred to Phase 7 (Hardening). Canonical field ordering: `id`, `conf`, `cort`, `corf`, `pr`, `src`, `xby`, `aby`, `ss`, `st`, `cr`, `v`, `cs`, then optional fields alphabetically.
- **`procedures/` directory** — procedural memory (debugging playbooks, deployment checklists, when/then rules) is a distinct memory type from facts and principles. Schema stub deferred to Phase 9 (Ecosystem).
- **Distributed dream locking** — `meta/processing.jsonl` for multi-machine coordination is a Phase 5 deliverable. For now, GitHub Actions `concurrency` groups are sufficient.
- **`confidence` calibration** — bounded [0,1], informational-only for conflict resolution in v1. Excluded from trust_score until calibrated across models.

### Resolved from v0.7

The following v0.7 open questions have been resolved in v0.8:

- ~~Extraction prompt design~~ → Appendix A (extraction prompt skeleton)
- ~~Composite score weights~~ → Split into trust/relevance/retention scores with separate weight sets (§6)
- ~~Verification field weight calibration~~ → Folded into trust_score weights
- ~~Source type weight calibration~~ → Folded into trust_score weights, with hard rule: `llm_inference` never beats `ground_truth_code`
- ~~Formal hot/warm/cold tier promotion~~ → Hot-tier token cap and MEMORY.md generation algorithm defined (§9, §16)

---

## Appendix A: Extraction Prompt Skeleton (L1)

This is the normative baseline for L1 fact extraction. Implementations MUST include these instructions (or semantic equivalents) in the L1 extraction prompt. Weights, phrasing, and examples may be tuned — but the constraints are mandatory.

```
You are a memory extraction agent for the UMX system. Your job is to extract
atomic, factual statements from a coding session transcript.

## Rules

1. ATOMICITY: Each fact must be a single, self-contained assertion. No compound
   statements. "postgres runs on 5433 and redis on 6379" → two facts.

2. SOURCE TYPE: Tag every fact with exactly one source_type:
   - ground_truth_code: You read this directly from a source file (function
     signature, config value, import path). MUST include code_anchor.
   - user_prompt: The user explicitly stated this in conversation.
   - tool_output: A tool (bash, test runner, linter) produced this output.
   - llm_inference: You inferred or reasoned this — no external grounding.
     This is the default when uncertain.
   - external_doc: Extracted from documentation (README, API docs, wiki).

3. ENCODING STRENGTH: Assign based on HOW the fact was established:
   - S:1 — Incidental mention, pattern you noticed
   - S:2 — Discussed but not verified
   - S:3 — Tool-confirmed or user-stated
   - S:4 — Directly read from code or independently corroborated
   - S:5 — Reserved for human confirmation (never assign this)

4. CONFIDENCE: Your certainty about the extracted text, bounded [0.0, 1.0].
   0.5 = coin flip. 0.9+ = you would bet on it.

5. NO REWRITING: Extract what was said or shown. Do not paraphrase beyond
   normalisation. Do not merge facts. Do not add information not in the session.

6. CONVENTIONS: If CONVENTIONS.md is provided, follow its phrasing rules and
   taxonomy. Flag facts that conflict with established conventions as
   schema_conflict: true.

7. BARTLETT CHECK: If a fact contradicts your general knowledge or seems
   schema-inconsistent (e.g., unusual port numbers, non-standard patterns),
   set schema_conflict: true. Do not suppress — extract and flag.

8. DEDUPLICATION KEY: If a fact is semantically identical to one in the
   existing facts list, skip it. Use the semantic_dedup_key for comparison.

## Output format

For each fact, emit:
- text: the atomic fact
- source_type: one of the enum values above
- encoding_strength: 1-4
- confidence: 0.0-1.0
- schema_conflict: true/false
- code_anchor: {repo, path, git_sha, line_range} (only for ground_truth_code)
- topic: suggested topic slug
- scope: file | folder | project (based on specificity)

## Examples

Session excerpt: "User: what port is postgres on? Assistant: Let me check...
[reads docker-compose.yml] It's on 5433 in dev."

Extract:
- text: "postgres runs on port 5433 in dev"
  source_type: ground_truth_code
  encoding_strength: 4
  confidence: 0.95
  code_anchor: {path: "docker-compose.yml"}
  topic: devenv
  scope: project

Session excerpt: "I think we should probably use connection pooling for this"

Extract:
- text: "connection pooling is being considered for database access"
  source_type: llm_inference
  encoding_strength: 1
  confidence: 0.6
  topic: database
  scope: project
```

---

## Appendix B: L2 Reviewer Prompt Skeleton

This is the normative baseline for L2 review. The L2 reviewer evaluates L1-extracted facts for accuracy, convention compliance, and quality.

```
You are a memory quality reviewer for the UMX system. You review facts
extracted by the L1 agent before they are merged into the memory store.

## Your context

You are provided:
1. The proposed facts (from L1 extraction)
2. The source session transcript (ground truth)
3. CONVENTIONS.md (project conventions — if absent, skip convention checks)
4. Existing facts that would be affected (superseded, contradicted)
5. meta/manifest.json (topic overview)

## Review criteria

For each proposed fact, evaluate:

1. ACCURACY: Does the fact faithfully represent what happened in the session?
   - Check against the source transcript
   - Flag hallucinations (facts not grounded in session content)
   - Flag exaggerations (S:4 assigned to an S:2 observation)

2. ATOMICITY: Is the fact truly atomic? Split compound facts.

3. SOURCE TYPE: Is the source_type correct?
   - ground_truth_code requires actual code reading in the session
   - user_prompt requires explicit user statement
   - tool_output requires tool execution output
   - Don't let llm_inference masquerade as ground_truth_code

4. CONVENTIONS: Does the fact comply with CONVENTIONS.md?
   - Phrasing rules (tense, voice, terminology)
   - Taxonomy (canonical topic names, tag vocabulary)
   - Entity vocabulary (standardised names for services, tools)

5. CONTRADICTIONS: Does this fact contradict an existing fact?
   - If yes, is the contradiction real or due to applies_to differences?
   - Real contradictions need explicit supersession chains

## Decisions

For each fact, decide:
- APPROVE: Fact is accurate, well-formed, convention-compliant → auto-merge
- REJECT: Fact is inaccurate, hallucinated, or duplicate → close with reason
- ESCALATE: Fact is ambiguous, contradicts high-strength existing fact,
  or proposes a principle → label "human-review", explain why

## Output

Emit your decision for each fact with reasoning. For rejections, cite the
specific issue. For escalations, explain what the human should evaluate.
```

---

## Appendix C: Deferred Considerations

The following items were raised during v0.7 review and classified as noise for v1 or deferred for future versions. They are documented here for completeness.

1. **Knowledge graph / entity relationships** — Raised by 4/11 reviewers. The atomic fact model is a deliberate strength for CLI-dev-workflow memory. An optional `related_to` field is a reasonable future extension but is not a v1 gap. Revisit if users report frequent difficulty navigating fact relationships.

2. **Vector/semantic search** — Raised by 6/11 reviewers (highest count). FTS5 is adequate for CLI coding queries which are highly specific. The `search.backend: hybrid` config option is defined for future opt-in local embeddings (bge-small or all-MiniLM-L6-v2, ~100MB, no API needed). Deferred to Phase 9.

3. **Multimodal support** — Raised by 1/11. Text-based CLI memory tool. Images/audio/video are out of scope.

4. **EU AI Act compliance** — Raised by 1/11 (competitor comparison). Not relevant for an open-source CLI tool spec.

5. **Append-only event model / CRDT** — Raised by 1/11. Would require full rearchitecture. Markdown-as-truth is a core design decision enabling human readability and manual editing. The merge conflict concerns are addressed by moving `last_referenced` to local-only (§6) and fixing path encoding (§8).

6. **"Over-engineered" criticism** — Raised by 1/11. The governance model IS the differentiator. Local mode is the escape hatch. Progressive complexity through good defaults.

7. **Team/multi-user memory** — Raised by 3/11. Correctly and explicitly listed as non-goal (§29). Single-user first.

8. **Encryption at rest** — Raised by 2/11. GitHub private repos + OS-level disk encryption (FileVault, LUKS, BitLocker) cover this. Document: "Use OS-level disk encryption for `$UMX_HOME`."

9. **`.umx/` in project repo** — Raised by 1/11. Directly violates Design Principle #2. The legacy bridge covers the "facts traveling with code" case.

10. **VS Code extension / marketplace** — Raised by various. Feature envy from competitor comparisons. UMX is CLI-native; IDE extensions are ecosystem, not core spec.

---

*umx is part of the AIP ecosystem — [github.com/dev-boz/agent-interface-protocol](https://github.com/dev-boz/agent-interface-protocol)*
