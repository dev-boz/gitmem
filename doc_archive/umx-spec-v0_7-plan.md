# umx Spec v0.7 — Revision Plan

## Arbitration Summary

Five reviewers (Gemini, Codex, Cursor, Amp, Trinity) independently reviewed v0.6. This plan synthesizes their feedback, adjudicates disagreements, and produces a prioritized fix list. Issues are grouped by severity and cross-referenced to reviewers who raised them.

---

## Tier 1 — Must Fix (Spec is internally contradictory or dangerously underspecified)

### 1.1 "No model auto-commits to main" vs local/hybrid mode direct writes
**Raised by:** Gemini, Codex, Cursor, Amp
**The problem:** Section 2 (Principle 9) and Section 26 (Non-Goals) state "No model auto-commits to main." Section 11 (dream mode) says local mode "writes facts directly to local markdown, commits, pushes to main." This is a flat contradiction present in four different places.
**Resolution:** This is the single most-raised issue across all reviews. The spec must reconcile it explicitly:
- Reword Principle 9 to: "No model auto-commits to main **in gitmem mode**."
- Reword Non-Goal to: "No auto-commit to main **when `dream.mode` is `remote`**."
- Add a clear table showing what each mode permits:
  - `local`: direct-to-main (no PR gates, no governance — solo/offline use)
  - `remote`: PR-only (full L1/L2/L3 governance)
  - `hybrid`: sessions direct-to-main, facts via PR
- Acknowledge the tradeoff: local mode sacrifices governance for speed/offline capability.

### 1.2 Session log security — secrets in raw transcripts
**Raised by:** Gemini, Codex, Cursor, Amp
**The problem:** Raw session logs capture full user/assistant/tool payloads and are pushed to GitHub. Secrets pasted into chat, API keys in stack traces, JWTs — all get committed as "immutable ground truth." `.gitignore`-driven extraction safety only applies to the Gather phase, not to raw session capture.
**Resolution:** All four reviewers agree this is critical. Add a new subsection to Section 19:
- **Pre-commit redaction pass** (MUST): Before a session file is committed, run a synchronous local pattern scanner for common secret formats (API keys, tokens, connection strings, AWS keys, JWTs). Replace matches with `[REDACTED:type]` placeholder.
- **Configurable patterns**: Ship default regex set, allow user extension via `config.yaml` `redaction.patterns`.
- **Opt-in raw mode**: `sessions.redaction: none` for users who want unredacted local-only sessions (these MUST NOT be pushed — enforce via pre-push hook).
- **Retroactive purge**: Add `umx purge --session <id>` that rewrites history (git filter-branch/BFG) for emergency secret removal. Acknowledge this breaks immutability guarantee for that session and note it in the audit log.
- **Amend the "immutable" language**: Sessions are immutable *after redaction*. The redaction pass is part of the write pipeline, not a post-hoc edit.

### 1.3 `local/` conflates "private" and "secret" — injection risk
**Raised by:** Codex, Cursor
**The problem:** `local/` is gitignored (good) and described as storing "private facts, local tokens, personal quirks, scratchpad." But Section 7 says Project (local) is "Always loaded: Yes." If local/ contains secrets AND is always injected, secrets leak into prompts.
**Resolution:** Split `local/` into two subdirectories:
- `local/private/` — private facts that ARE injected (personal preferences, machine-specific config). Always loaded: Yes.
- `local/secret/` — tokens, credentials, connection strings. Always loaded: **No**. Never injected. Accessed only by explicit tool/CLI request.
- Update Section 7 scope table, Section 8 directory layout, Section 16 injection architecture.

### 1.4 Fact identity — no stable ID scheme
**Raised by:** Codex, Cursor, Amp
**The problem:** Fact IDs are shown as `f_001` (local counter). In a multi-machine or multi-tool system, counters will collide. Deduplication, corroboration, and merge rules all depend on "same fact" determination, but there's no canonical identity scheme.
**Resolution:** Define a two-layer identity:
- **`fact_id`** (immutable): ULID (Universally Unique Lexicographically Sortable Identifier). Generated on creation. Never changes. Used for merge, audit trail, PR references.
- **Semantic dedup key** (derived): Normalized `lowercase(subject + predicate + scope + topic)` hash. Used for deduplication detection. Two facts with different `fact_id` but same dedup key are candidates for merge/corroboration.
- Add a new subsection to Section 9 defining ID generation rules.
- Update all examples from `f_001` to ULID format.

### 1.5 Markdown format underspecified — metadata fields gap
**Raised by:** Codex, Cursor
**The problem:** The YAML fact schema (Section 5) has ~15 fields. The inline markdown format (Section 9) carries only `id`, `conf`, `corroborated_by`, `pr`. Where do `scope`, `topic`, `memory_type`, `source_tool`, timestamps, `provenance` live? Either JSON is silently canonical for some fields, or those fields are lost.
**Resolution:**
- The markdown inline metadata carries the *minimum viable set* for display and quick lookup.
- The derived `.umx.json` file carries the full schema.
- BUT: since markdown is canonical, we need to specify how full-schema fields are reconstructed:
  - `scope` and `topic`: derived from file path (`facts/topics/devenv.md` → scope=project, topic=devenv)
  - `memory_type`: derived from directory (`episodic/` vs `facts/` vs `principles/`)
  - `source_tool`, `source_session`, `provenance`: stored in the inline metadata JSON (expand the required fields)
  - `created`, `last_referenced`: stored in inline metadata
- Add a normative table: "Inline metadata — required fields" vs "Inline metadata — optional fields" vs "Path-derived fields"
- Provide a formal JSON Schema for the `<!-- umx: {...} -->` block.

### 1.6 Session log format — JSONL vs "YAML frontmatter" contradiction
**Raised by:** Codex, Cursor, MiniMax
**The problem:** Section 19 says files are JSONL, then calls the metadata header "YAML frontmatter" while showing it as a JSON `_meta` object. These are different formats.
**Resolution:** It's clearly JSONL — the example shows JSON. Fix the text:
- Remove the phrase "YAML frontmatter" — replace with "metadata record."
- The `_meta` line is the first JSON object in the JSONL file, distinguished by the `_meta` key.
- Add: "Session files are pure JSONL. There is no YAML. The first line is a `_meta` record; all subsequent lines are event records."
- Define the `_meta` schema (required/optional keys).
- Define event record schema (required: `ts`, `role`, `content`; optional: `tool`, `input`, `tool_call_id`).
- Standardize session ID format: `YYYY-MM-DD-<ulid>` (not `abc123`).

### 1.7 Prune threshold default is a no-op
**Raised by:** Cursor · **Escalated to Tier 1 by:** Spec author
**The problem:** Section 5 says "Facts below a configurable threshold (default: 1) are removed." But the strength scale starts at 1 (Incidental). Nothing exists below 1. The Prune phase literally never removes anything at the default setting. This is a functional bug, not polish.
**Resolution:** Change default prune threshold to **2**. Facts at S:1 (incidental, single-mention, unconfirmed) are pruned if not corroborated within the decay window. This makes the Prune phase actually functional out of the box. Update Section 5 (Encoding Strength) and Section 11 (Dream Pipeline, Prune phase).

---

## Tier 2 — Should Fix (Significant design gaps that will cause implementation problems)

### 2.1 Tombstone / negative memory mechanism
**Raised by:** Codex, Gemini, Cursor, Amp
**The problem:** `umx forget` removes facts, but immutable sessions + re-derivation can resurrect them. No suppression mechanism survives future extraction passes.
**Resolution:** Add `meta/tombstones.jsonl`:
```jsonl
{"fact_id":"01J...","match":"postgres.*5432","reason":"port changed to 5433","author":"human","created":"2026-04-08T...","suppress_from":["gather","rederive","audit"]}
```
- Tombstones are checked during Gather and re-derivation. Matching facts are suppressed.
- Tombstones can have optional `expires_at` for temporary suppression.
- `umx forget --fact <id>` creates a tombstone + removes the fact.
- `umx forget --topic <topic>` creates tombstones for all facts in that topic.
- Add a `DEPRECATED` marker for inline use: `- [DEPRECATED] ...` with metadata including `replaces` field.

### 2.2 Encoding strength 4 is overloaded
**Raised by:** Amp, Gemini, Cursor
**The problem:** S:4 means both "tool native memory (LLM wrote it)" and "SotA-approved PR." LLM native memory is self-reported and can be wrong. SotA approval is independent verification. Same strength, very different reliability.
**Resolution:** Revise the strength table:
- S:3 — Tool native memory (LLM deliberately wrote it, but self-reported — no independent verification)
- S:3 — Extracted from session transcript (Dream pipeline)
- S:4 — Independently verified (SotA-approved PR, OR corroborated across ≥2 independent sources)
- Corroboration bonus: native memory (S:3) + transcript extraction (S:3) → S:4
- This means native memory starts lower but can be promoted via the normal pipeline.
- **Alternatively** (simpler): Keep S:4 for native memory but add a `verification` field (`self-reported` | `corroborated` | `sota-reviewed` | `human-confirmed`). Use this in composite scoring. This avoids changing the strength scale.
- **Decision:** Go with the `verification` field approach. It's less disruptive and more informative. Add `verification` to the fact schema and weight it in composite scoring.

### 2.3 `last_referenced` feedback loop
**Raised by:** Codex, Cursor, Amp
**The problem:** Updating `last_referenced` on every retrieval turns reads into writes. If retrieval includes injection (every prompt), facts stay artificially fresh forever. Also creates git churn.
**Resolution:**
- `last_referenced` updates ONLY on: (a) explicit user query (`umx view`, `umx search`), (b) agent explicitly cites the fact in output, (c) session-end batch update for facts that were injected AND used.
- Silent injection (fact loaded into context but never referenced in output) does NOT update `last_referenced`.
- Move retrieval telemetry to `meta/usage.sqlite` (local-only). Batch `last_referenced` updates to fact files on session end, not per-retrieval.
- **Calibration signal:** `meta/usage.sqlite` SHOULD track which facts were injected but never referenced in agent output. Over time, a high injected-but-unused ratio for a fact is a signal that it has low real-world relevance — the relevance scoring weights need adjustment. This is free calibration data that the Dream pipeline can use during Prune to down-weight facts that are consistently injected but ignored.
- Clarify this in Sections 6 and 10.

### 2.4 SQLite rebuild performance
**Raised by:** Gemini, Cursor, Trinity
**The problem:** "Rebuilt on pull" could mean full rebuild. For large repos (hundreds of files, months of use), this adds startup latency.
**Resolution:**
- Specify **incremental rebuild**: On pull, `git diff --name-only HEAD@{1} HEAD -- '*.md'` identifies changed files. Only those are re-indexed.
- Full rebuild only on: first clone, `schema_version` change, or `umx rebuild-index --force`.
- Store a `last_indexed_sha` in the SQLite database. On startup, diff from that SHA to HEAD.
- Update Sections 10 and 20.

### 2.5 Duplicate SQLite schema definitions
**Raised by:** Cursor, MiniMax
**The problem:** Schema appears in both Section 10 and Section 20 with slight differences (Section 10 has `repo` column, Section 20 doesn't).
**Resolution:** Define the schema ONCE in Section 20 (Search and Retrieval). Section 10 references it. Add:
- `repo TEXT` column (present in both — it's needed for cross-project queries)
- FTS table specification: `fts5(content, tags, tokenize='unicode61')`
- Migration strategy: `schema_version` in a `_meta` table within SQLite
- Ranking function: `bm25()`

### 2.6 `~/.umx/` hardcoded path — no override
**Raised by:** Codex, Gemini, Cursor
**The problem:** "Non-negotiable" path doesn't work for containers, remote dev, CI, Windows, enterprise setups, or XDG-compliant systems.
**Resolution:**
- Add `UMX_HOME` environment variable override.
- Resolution order: `$UMX_HOME` → `~/.umx/` (default)
- Section 8: Change "non-negotiable" to "default, overridable via `UMX_HOME`."
- Note: XDG compliance is a non-goal for v1 but `UMX_HOME` covers the practical cases.

### 2.7 Scope path encoding — no canonical mapping
**Raised by:** Cursor, Gemini
**The problem:** `folders/<path>.md` and `files/<file>.md` have no specified encoding for path separators, case sensitivity, unicode, symlinks, or collision handling.
**Resolution:** Add a "Path Encoding" subsection to Section 7:
- Paths are repo-relative POSIX, normalized (no `./`, no `..`, no trailing `/`)
- `/` encoded as `__` in filenames
- Special characters URL-encoded (percent encoding)
- Case-sensitive (preserve original case)
- Symlinks resolved to their target before encoding
- Example: `src/api/auth/middleware.ts` → `files/src__api__auth__middleware.ts.md`
- Example: `src/api/` → `folders/src__api.md`

### 2.8 Query-triggered write-back (Karpathy pattern)
**Raised by:** Author (inspired by Karpathy's wiki-memory pattern)
**The problem:** Currently, the Dream pipeline is triggered only at session end or on a schedule (the three-gate trigger in Section 11). If an agent queries memory mid-session and finds the answer incomplete or missing, there's no mechanism to capture that gap. The agent discovers the gap, works around it, and the insight that memory was incomplete is lost until the next dream cycle processes the full session transcript — by which point the gap signal is buried in noise.
**The insight:** Karpathy's pattern has a "query" operation where the LLM reads the wiki to answer a question and *also updates the wiki if the query reveals a gap*. That's a read-triggered write-back loop. umx already has all the infrastructure for this (L1 dream PRs, low-confidence fact proposals, the full governance pipeline) — but the trigger is missing.
**Resolution:** Add a new trigger type to the Dream pipeline: **query-gap proposal**.
- A gap signal is emitted only when an agent queries memory, finds the answer incomplete, **and then resolves the gap through other means during the session** (e.g., reads a config file, asks the user, discovers via tool output). An empty query result alone is NOT sufficient — the agent's subsequent workaround behavior is what confirms the gap was real, not that the topic was simply irrelevant.
- The gap signal format: `{"type": "gap", "query": "...", "resolution_context": "...", "proposed_fact": "..."}` where `resolution_context` describes how the agent found the answer (file read, user response, tool output, etc.).
- Gap signals are written to `meta/gaps.jsonl` (append-only, local).
- The gap signal MUST include a `proposed_fact` — the answer the agent found, formatted as an atomic fact.
- At session end, accumulated gaps are processed as part of the normal Dream pipeline:
  - Proposed facts from gaps are treated as S:1 (incidental) candidates — lowest confidence, require corroboration to survive.
  - In gitmem mode, they are batched into a dedicated PR: `[dream/l1] Gap-fill proposals from session <id>`.
  - In local mode, they are written directly but at S:1, meaning they will be pruned quickly unless corroborated.
- **Mid-session fast path (optional, opt-in):** For tools with hook support, an agent MAY write a gap-proposed fact directly to `local/private/scratchpad.md` for immediate availability in the current session. This fact is ephemeral and must survive the normal Dream pipeline to be promoted to `facts/`.
- **Why S:1 and not higher:** A single agent noticing a gap and proposing a fact is the weakest form of evidence — literally "one mention, unconfirmed." The value is in *capturing the signal*, not in immediately trusting it. The existing promotion pipeline (corroboration, L2 review, decay) handles the rest.
- Add to Section 11 (Dream Pipeline) as a new trigger type alongside the three-gate trigger.
- Add to Section 16 (Injection Architecture) a note that injection can emit gap signals.
- Add `umx gaps` CLI command to list pending gap proposals.

### 2.9 Project slug collision
**Raised by:** Amp, Gemini
**The problem:** Two repos with the same name on different remotes (e.g., `alice/utils` and `bob/utils`) both slug to `utils`.
**Resolution:**
- Default slug: `<repo-name>` (simple case, works for most users)
- Collision detection: On `umx init-project`, if slug already exists AND the git remote doesn't match the existing project's remote, warn and prompt for a custom slug.
- Override: `.umx-project` file always takes precedence.
- Alternative format available: `<owner>-<repo>` (e.g., `alice-utils`), configurable in `config.yaml` as `project.slug_format: name | owner-name`.

### 2.10 `umx init-project` shadows existing remote memory
**Raised by:** Spec author (missed by all reviewers)
**The problem:** If the user runs `umx init-project --slug boz` and `memory-org/boz` already exists in the GitHub org (e.g., the user reinstalled their OS, moved to a new machine, or deleted `~/.umx/` locally), the current spec implies creating a new empty repo — which shadows the real memory. All existing facts, sessions, and history are silently replaced by an empty repo.
**Resolution:**
- `umx init-project` MUST check whether the remote repo already exists before creating.
- If remote exists and local clone does not: **clone the existing remote repo**. Done. No data loss.
- If remote exists and local clone exists but points to a different remote: warn and abort. User must resolve manually.
- If remote does not exist: create new repo (current behavior).
- Same logic applies to `umx init` for the `user` repo.
- Add to Section 8 (Bootstrap) with explicit flow:
  ```
  umx init-project --slug boz
    → Does memory-org/boz exist on GitHub?
      → Yes: clone to ~/.umx/projects/boz/ (preserve existing memory)
      → No:  create repo, clone, initialize directory structure
  ```

### 2.11 Supersession and interference handling
**Source:** Interference theory (proactive/retroactive interference, McGeoch 1932; Anderson 1983 ACT-R) + Zep/Graphiti's temporal knowledge graph model (bi-temporal edges with `valid_from`/`invalid_at`).
**The problem:** umx tombstones (2.1) handle *deletion* but not *supersession*. When a fact is replaced by a newer contradictory fact (port 5432 → 5433), the old fact is either deleted (losing history) or kept (creating retroactive interference — the old fact competes with the new one during retrieval). Cognitive science calls this "retroactive interference"; production systems solve it with explicit supersession edges.
**Resolution:** Add two fields to the fact schema:
- `supersedes: [fact_id, ...]` — facts this one replaces
- `superseded_by: fact_id` — set when a newer fact obsoletes this one (mutually exclusive with active retrieval)
- Superseded facts are NOT deleted. They remain queryable via `umx history` but are excluded from normal retrieval and injection.
- The Dream pipeline's Consolidate phase detects contradictions (same canonical_key, different values) and proposes a supersession PR rather than a simple overwrite.
- This preserves audit trail ("postgres was on 5432 until 2026-03-14, now 5433") which matters for debugging time-sensitive issues.
- Update Section 4 (Memory Model), Section 10 (Conflict Resolution), Section 11 (Dream — Consolidate phase).

### 2.12 Consolidation fragility window
**Source:** Memory consolidation theory (Müller & Pilzecker 1900; modern reconsolidation literature, Nader et al. 2000). Newly encoded memories are labile for a period before becoming stable.
**The problem:** umx treats a newly-extracted fact as immediately equivalent to a mature fact at the same strength. But freshly-extracted facts are the riskiest — extraction errors, misread context, hallucinated specifics. They need a fragility window where they're easier to correct or reject before hardening into the store.
**Resolution:** Add a `consolidation_state` field to the fact schema with values `fragile` | `stable`:
- New facts enter in `fragile` state.
- Transition to `stable` after: (a) N days elapsed (default 7), OR (b) corroboration from an independent source, OR (c) explicit user confirmation via `umx confirm <fact_id>`.
- Fragile facts are:
  - Injected with a `[fragile]` marker so the agent knows to double-check
  - Easier to delete (no tombstone required; simple removal)
  - Prioritized for review in the L2 queue
  - Weighted lower in composite scoring until stable
- This operationalizes the "new memories are labile" insight into concrete behavior.
- Update Section 5 (Encoding Strength), Section 11 (Dream Pipeline), Section 16 (Injection).

### 2.13 Rehearse phase — active reinforcement scheduler
**Source:** Spacing effect (Ebbinghaus 1885; Cepeda et al. 2006 meta-analysis) + testing effect (Roediger & Karpicke 2006). Spaced retrieval practice is the single most robust memory intervention in cognitive science.
**The problem:** umx has Orient → Gather → Consolidate → Prune. All four are *passive* on facts themselves — they extract, merge, and delete. Nothing actively *strengthens* facts via retrieval. The result: a fact that is real and important but rarely queried will decay at the same rate as noise, because the only strength signal is passive retrieval telemetry.
**Resolution:** Add a fifth Dream phase: **Rehearse**.
- Rehearse runs after Consolidate, before Prune.
- It selects a small set (default: 5) of `stable` facts that are approaching the decay threshold but have high composite scores (high `verification`, high `corroborated_by` count).
- For each selected fact, it generates a synthetic retrieval query and checks whether the fact would be correctly surfaced. If yes, `last_referenced` is updated (this is the only case where a non-user-triggered update is permitted — Rehearse is the exception to rule 2.3).
- If the fact fails its own retrieval test, it's flagged for re-indexing or a tagging review.
- Rehearse is bounded and deterministic: ≤5 facts per dream cycle, ≤1 rehearsal per fact per week.
- This is the direct analog of "testing effect" — retrieval practice that strengthens the memory trace.
- Update Section 11 (Dream Pipeline) to add Rehearse as the 5th phase.

### 2.14 Task resumption field (Ovsiankina, not Zeigarnik)
**Source:** Ovsiankina effect (Ovsiankina 1928) — the tendency to resume interrupted tasks. Note: the related Zeigarnik effect (better memory for unfinished tasks) was contested by a 2025 meta-analysis that found no reliable memory advantage. The *resumption tendency* (Ovsiankina) is robustly supported; the *memory advantage* (Zeigarnik) is not. This spec cites Ovsiankina.
**The problem:** When a session ends with work in-progress (a bug half-diagnosed, a refactor partially complete, a question the agent was about to investigate), there is no structured way to mark that state. The next session starts cold. Agents routinely re-discover the same "where was I?" context by reading recent git log or scrolling session history.
**Resolution:** Add an optional `task_status` field to episodic facts:
- Values: `open` | `blocked` | `resolved` | `abandoned`
- New CLI: `umx resume` lists all `open` and `blocked` episodic facts from the last N sessions (default 3), ordered by recency. This gives the next session an immediate "you were working on..." list.
- Status transitions: facts start `open`, move to `resolved` when the agent explicitly marks completion, move to `blocked` when the agent records a blocker, move to `abandoned` after M days of inactivity (auto, with tombstone).
- This is a concrete memory-shaped substitute for the "TODO list across sessions" pattern and leverages the Ovsiankina resumption tendency rather than the weaker Zeigarnik claim.
- Update Section 4 (Memory Model, episodic subsection), add `umx resume` to Section 12 (CLI).

### 2.15 Metacognition manifest (meta-level index)
**Source:** Nelson & Narens (1990) metamemory framework — the meta-level monitors and controls the object-level. A memory system without a meta-level has no way to reason about what it knows, what it's uncertain about, or what's missing.
**The problem:** umx's SQLite index is a search index, not a metamemory. Nothing answers "what do I know about X topic?" at a summary level, "what are my most uncertain facts?", or "what topics have I touched but never consolidated?" This forces every query to scan facts directly.
**Resolution:** Add `meta/manifest.yaml` — a lightweight meta-level index distinct from the memory store:
```yaml
topics:
  devenv: { fact_count: 23, avg_confidence: 0.87, last_updated: "2026-04-08", fragile: 2 }
  build-system: { fact_count: 8, avg_confidence: 0.62, last_updated: "2026-03-21", fragile: 5 }
uncertainty_hotspots:  # topics where fragile/stable ratio is high
  - build-system
knowledge_gaps:  # topics with many gap signals but few facts
  - deployment
```
- Regenerated during the Consolidate phase (cheap — aggregates over existing SQLite data).
- Injected with MEMORY.md so the agent has a "what do I know about myself" view before diving into specifics.
- Enables meta-queries: `umx meta --topic devenv` returns confidence stats, not facts.
- This is distinct from MEMORY.md (which is the content index) — manifest.yaml is the *epistemic* index.
- Update Section 8 (Directory Layout), Section 16 (Injection Architecture).

### 2.16 Dream pipeline linting pass
**Source:** Karpathy's April 3 2026 post on LLM Knowledge Bases (gist.github.com/karpathy/442a6bf) — describes a periodic "linter" pass over a wiki-style knowledge base that scans for contradictions, stale claims, orphaned entries, and broken cross-references. Presented as essential maintenance, not optional polish.
**The problem:** umx has Consolidate (merge facts) and Prune (delete weak facts) but no dedicated pass that hunts for *quality problems* across the corpus: two facts that contradict each other without supersession, facts referencing files that no longer exist, cross-references to fact IDs that have been deleted, tag typos that split related facts across misspelled tags.
**Resolution:** Add a **Lint** sub-phase within Consolidate (not a new top-level phase — it's cheap enough to piggyback).
- Lint scans:
  - **Contradictions:** pairs of facts with the same canonical_key and incompatible values that lack a supersession link → propose supersession PR
  - **Stale references:** facts that reference a `files/<path>.md` scope where the underlying file no longer exists (see 3.10) → propose migration or tombstone
  - **Orphan fact IDs:** `corroborated_by` or `supersedes` pointing to non-existent fact IDs → flag for L2 review
  - **Tag drift:** tag clusters with small edit distance (`devenv` / `dev-env` / `dev_env`) → propose merge
- Lint findings are emitted as a single `[dream/lint] Weekly lint report` PR with each finding as a separate fix proposal.
- Runs on the slow Dream cycle (not every session) — weekly by default.
- Update Section 11 (Dream Pipeline).

### 2.17 CONVENTIONS.md schema file
**Source:** Karpathy's April 2026 wiki pattern — three-layer Raw / Wiki / Schema architecture. The Schema layer defines *how* the wiki is structured so both humans and future agents can contribute consistently.
**The problem:** umx has implicit conventions: topic naming, fact phrasing ("atomic facts, ≤200 chars"), the distinction between episodic/semantic/procedural, when to use a principle vs a fact. New agents joining the project (or the same agent in a fresh session) must re-derive these conventions from examples. Drift is inevitable.
**Resolution:** Add `CONVENTIONS.md` at the project memory root — a short, human-authored file that codifies:
- Topic taxonomy for this project (`devenv`, `architecture`, `ops`, ...)
- Fact phrasing rules (atomic, present tense, ≤200 chars, use canonical entity names)
- When to file as episodic vs semantic vs procedural
- When to file as fact vs principle
- Project-specific entity vocabulary (what "the API" refers to, what "main" means)
- CONVENTIONS.md is **always injected** alongside MEMORY.md with high priority.
- Template provided by `umx init-project`; user edits to taste.
- The Dream pipeline's L2 reviewer reads CONVENTIONS.md as part of every review — it becomes the "style guide" enforced on PRs.
- Update Section 8 (Directory Layout), Section 16 (Injection Architecture), Section 11 (Dream Pipeline L2 prompt).

### 2.18 Bi-temporal fact validity
**Source:** Zep/Graphiti temporal knowledge graphs — every edge carries both a *transaction time* (when the system learned the fact) and a *valid time* (the real-world time range the fact was true). This enables "what did we believe about X on date Y?" queries.
**The problem:** umx has `created` and `last_referenced` (transaction time) but no way to express *when a fact was true in the world*. "Postgres runs on port 5432" — when? Since when? Until when? The `supersedes`/`superseded_by` mechanism (2.11) captures *that* a fact was replaced but not *when it was valid*.
**Resolution:** Add two optional fields to the fact schema:
- `valid_from: <ISO8601 | null>` — when the fact became true in the world (null = "always")
- `valid_to: <ISO8601 | null>` — when the fact stopped being true (null = "still true")
- Distinct from `created` (when the fact entered the memory system) and `superseded_by` (the link to the replacement fact).
- Retrieval defaults to `valid_to IS NULL OR valid_to > now()`.
- `umx query --as-of 2026-01-15` enables point-in-time queries — "what did we know about the infra on January 15th?"
- Most facts will leave these fields null; they only matter for time-sensitive facts (port numbers, deploy targets, team membership, deprecation dates).
- Update Section 4 (Memory Model) and Section 20 (Search and Retrieval).

### 2.19 Hot / warm / cold memory tiers
**Source:** Letta (MemGPT) core/recall/archival tiers + MemoryOS STM/MTM/LTM heat-scored promotion. Both systems outperform flat-store baselines on LOCOMO by making the tier distinction first-class in the architecture.
**The problem:** umx has an implicit tier structure (MEMORY.md is "always loaded", `facts/topics/*.md` is "on demand", session logs are "rarely touched") but doesn't name it or expose it as a concept. This makes it hard to reason about what's always injected vs retrieved vs archived, and makes the injection budget hard to tune.
**Resolution:** Formally name the tiers in Section 16:
- **Hot:** MEMORY.md, CONVENTIONS.md, manifest.yaml, principles/. Always injected. Budget: ~2000 tokens. Edited by the Consolidate phase.
- **Warm:** `facts/topics/*.md`, scoped memory. Retrieved on demand by relevance scoring. Budget: ~4000 tokens per query. Indexed in SQLite FTS.
- **Cold:** session logs, superseded facts, episodic facts beyond recent window. Never auto-injected. Accessed only via explicit CLI (`umx history`, `umx search --all`).
- Promotion rules: a warm fact that is referenced in ≥3 consecutive sessions is a candidate for hot-tier inclusion (via a summary line in MEMORY.md, not the full fact).
- Demotion rules: a hot-tier entry not referenced in 14 days demotes to warm.
- This makes the existing structure legible and tunable, without changing the underlying storage.
- Update Section 16 (Injection Architecture).

### 2.20 Source type enum (source monitoring)
**Source:** Source monitoring framework (Johnson, Hashtroudi, Lindsay 1993) — the cognitive ability to distinguish *where* a memory came from (self-generated vs perceived, told vs inferred) is a distinct skill from recalling the content. Source confusion is a major failure mode in human memory and in RAG systems.
**The problem:** umx's `source_tool` field tells you which CLI tool captured a fact but not the *epistemic type* of the source. A fact extracted from a code file is ground truth; a fact extracted from a user assertion is a claim; a fact extracted from an LLM's own reasoning is a hypothesis. The Dream pipeline and composite scoring should treat these very differently, but the current schema collapses them.
**Resolution:** Add a `source_type` enum field to the fact schema:
- `GROUND_TRUTH_CODE` — extracted from an actual file in the repo (highest trust)
- `USER_PROMPT` — asserted by the user in a session (high trust, but user can be wrong)
- `TOOL_OUTPUT` — observed from a tool invocation (e.g., `psql \l` output, `curl` response) (high trust, captures reality)
- `LLM_INFERENCE` — synthesized by the agent from context (lower trust — may be hallucinated)
- `DREAM_CONSOLIDATION` — derived by the Dream pipeline from other facts (trust inherits from inputs)
- `EXTERNAL_DOC` — extracted from documentation, README, etc. (medium trust — docs go stale)
- Composite scoring weights these differently. `LLM_INFERENCE` facts require corroboration to reach S:3. `GROUND_TRUTH_CODE` starts at S:3 by default.
- This is the single biggest quality-of-retrieval improvement available from the cognitive science literature — source monitoring distinguishes reliable from unreliable memories.
- Update Section 5 (Encoding Strength), Section 9 (Fact Schema), Section 10 (Composite Scoring).

---

## Tier 3 — Nice to Have (Improvements that strengthen the spec but aren't blocking)

### 3.1 Normative language pass (MUST/SHOULD/MAY)
**Raised by:** Cursor, Codex
**Action:** Do a full pass converting descriptive language to RFC 2119 keywords for all normative requirements. This is a v0.7 polish task, not a structural change. Mark sections as "Normative" vs "Informative."

### 3.2 Schacter date inconsistency
**Raised by:** Cursor, Trinity
**Action:** Section 4 says "Schacter's 1985 formalisation" but Reference [2] says 1987. The paper is from 1987. Fix the body text to say 1987.

### 3.3 ~~Prune threshold default~~ — Promoted to 1.7

(Moved to Tier 1. See 1.7.)

### 3.4 200-line MEMORY.md limit
**Raised by:** Amp
**The problem:** Mature projects with 50+ topics will exceed 200 lines in the index table alone.
**Resolution:** Clarify that the 200-line limit applies to the *index summary* section only, not to the entire file. Or: increase the limit to 500 lines with a note that the Prune phase should prioritize keeping the index concise. Better: make it configurable with 200 as default.

### 3.5 Context budget knapsack problem
**Raised by:** Gemini
**The problem:** Simple descending-sort truncation can waste budget — a huge high-relevance fact blocks many small useful facts.
**Resolution:** Acknowledge the knapsack aspect. For v1, simple descending-sort-with-budget is acceptable. Add a note that future versions may implement greedy knapsack packing. The current "no partial facts" rule is correct — never truncate a fact mid-text.

### 3.6 Corroboration independence rules
**Raised by:** Codex
**The problem:** Bridge-generated content or copied summaries shouldn't count as independent corroboration.
**Resolution:** Add to Section 10: "Corroboration requires independent evidence. Two sources count as independent only if they have different `source_session` values AND different `source_tool` values, OR the same tool across sessions separated by ≥24 hours. Bridge-written facts (CLAUDE.md markers) are never counted as independent corroboration sources."

### 3.7 Session capture — how it actually works
**Raised by:** Amp
**The problem:** The spec never explains how raw session transcripts are captured from each tool. This is a hard engineering problem.
**Resolution:** Add a new subsection "Session Capture" to Section 19:

| Tool | Capture method | Status |
|------|---------------|--------|
| Claude Code | `~/.claude/projects/` contains session data; also JSONL export via `claude-code --export` (if available) | Needs reverse-engineering |
| Aider | `.aider.chat.history.md` and session logs | Adapter needed |
| AIP-managed tools | `workspace/events.jsonl` (structured, preferred) | Available now |
| MCP-capable tools | `write_memory` MCP tool can emit session events | Available now |
| Other | Manual `umx collect` or shim-based capture | Shim-dependent |

Honestly acknowledge which tools expose transcripts and which don't. This is the biggest implementation risk.

### 3.8 "Free compute" claim
**Raised by:** Amp
**Resolution:** Soften the comparison table. Change "Free compute ✓" to "Free compute ✓*" with footnote: "*Depends on free-tier availability from third-party providers. Subject to change. Paid API keys recommended for production reliability."

### 3.9 `config.yaml` full schema
**Raised by:** Amp, MiniMax
**Resolution:** Add a "Configuration Reference" appendix with the complete `config.yaml` schema, all keys, types, defaults, and descriptions.

### 3.10 Orphaned scoped memory (file rename problem)
**Raised by:** Gemini
**Resolution:** Add to Section 21 (Failure Modes): "If project files/folders are renamed, associated scoped memory becomes orphaned. Detection: the Dream pipeline's Orient phase can compare the project repo's file tree against existing `folders/` and `files/` entries. When orphans are detected, the pipeline proposes a rename/migration PR. This is best-effort — manual `umx migrate-scope` is the reliable fallback."

### 3.11 Fact TTL / expiry
**Raised by:** Amp
**Resolution:** Add optional `expires_at` field to fact schema. Facts past their expiry are auto-pruned regardless of strength. Useful for ephemeral facts ("deploy is broken", "feature flag X is on"). Not required — most facts use time decay instead.

### 3.12 Environment/variant qualifiers
**Raised by:** Cursor
**Resolution:** Add optional `applies_to` metadata field: `{"os": "linux", "env": "dev", "branch": "main"}`. Injection filters facts by matching qualifiers against the current environment. Not required for v1 but the schema should reserve the field.

### 3.13 Spec compliance section
**Raised by:** Cursor
**Resolution:** Add a "Conformance" section defining minimal requirements for a tool to claim "umx-compatible":
- MUST resolve project slug
- MUST be able to read injected memory blocks
- MUST write session logs in required JSONL schema
- MUST parse/write markdown facts with inline metadata
- MUST NOT commit derived artifacts (JSON/SQLite)
- SHOULD support at least one injection mechanism (hook, shim, MCP, or manual)

---

## Tier 4 — Deferred / Out of Scope for v0.7

### 4.1 Multi-user / team story
**Raised by:** Amp
**Decision:** Explicitly state this is a non-goal for v1. Add to Non-Goals: "No multi-user shared memory in v1. umx is single-user. Team memory sharing is a future consideration."

### 4.2 Cross-machine secret syncing
**Raised by:** Gemini
**Decision:** Explicitly state in Section 7: "Cross-machine syncing of `local/secret/` is a non-goal. Use a dedicated secret manager (1Password CLI, Vault, etc.) for cross-machine secrets."

### 4.3 Vector search
**Already deferred in spec.** No change needed.

### 4.4 GDPR / data deletion obligations
**Raised by:** Amp, Cursor
**Decision:** Add a brief note in Section 19: "For jurisdictions requiring data deletion, `umx purge --session <id>` with git history rewrite is the escape hatch. This breaks immutability for the affected session. Consider encrypted session storage for sensitive environments."

### 4.5 Import/bootstrap from existing memory
**Raised by:** Amp
**Decision:** Good idea but implementation-phase concern. Add to Roadmap Phase 3: "Bulk import: `umx import --tool claude-code` scans existing native memory stores and bootstraps initial facts."

### 4.6 Testing / validation strategy
**Raised by:** Amp, Trinity
**Decision:** Add to Roadmap Phase 1: "Test harness: regression tests that verify known sessions produce expected facts at expected strengths." Not a spec concern but worth noting in the roadmap.

### 4.7 Multi-language support
**Raised by:** MiniMax
**Decision:** Non-goal for v1. Facts are stored in whatever language they were expressed in. No translation or normalization.

---

## Rejected / Disagreed

### R1 "Zero Infrastructure" tagline
**Raised by:** Gemini
**Decision:** Disagree. The tagline is accurate — umx requires no infrastructure *you have to provision or pay for*. Git + GitHub free tier + free LLM APIs = zero infrastructure from the user's perspective. SQLite is a library, not infrastructure. The distinction from "serverless" is intentional.

### R2 Concurrency and divergence mid-session
**Raised by:** Gemini
**Decision:** Partially addressed by sync cadence (pull at session start, push at session end). Mid-session staleness is acceptable — memory is advisory, not transactional. The worst case is a stale fact being injected, which is already handled by the conflict/scoring system. No additional mechanism needed for v0.7.

### R3 Arbitrator agent full specification
**Raised by:** MiniMax
**Decision:** The arbitrator is described at the right level for a spec. Implementation details (prompt, model, trigger) belong in the implementation, not the spec. Add a one-line note: "Arbitrator implementation is tool-specific; the spec defines the interface (read conflict markers, evaluate scores, commit resolution)."

### R4 Mermaid diagrams
**Raised by:** Trinity
**Decision:** Nice to have but not a spec fix. Can be added in a polish pass.

---

## Tier 5 — Future Options / Parking Lot

Items explored during research but deliberately set aside for v0.7. Recorded here so we don't rediscover them in v0.8, and so the trade-offs behind rejection are legible.

### F1 Actor-aware memories (multi-agent)
**Source:** Letta's actor-tagged memories; MCP multi-client scenarios.
**Idea:** Tag every fact with the agent/tool identity that wrote it (not just `source_tool` but a proper actor ID). Enables multi-agent scenarios where Agent A shouldn't trust Agent B's assertions without corroboration, or where agents have role-scoped write permissions.
**Why deferred:** v1 is single-user, single-agent-per-session. `source_tool` is enough for now. Revisit when the multi-user story (4.1) is on the table.

### F2 MemCube / standardized memory object abstraction
**Source:** MemOS MemCube — memories as first-class objects with a uniform read/write/subscribe interface.
**Idea:** Wrap facts in a MemCube-style interface so tools can subscribe to changes ("notify me when any fact in topic X is updated") rather than polling.
**Why deferred:** The reactive model is powerful but adds a runtime daemon. umx's CLI-driven batch model is simpler and works without a long-running process. Reconsider if a persistent umx daemon emerges for other reasons.

### F3 Procedural memory (how-to scripts)
**Source:** LangMem's procedural memory type — storing reusable action sequences, not just facts.
**Idea:** A new memory type beyond episodic/semantic: procedural. Stores "how to deploy this app" as an executable-ish sequence with preconditions. Differs from a fact in that it's meant to be *replayed*, not just read.
**Why deferred:** There's significant overlap with existing shell aliases, Makefiles, and `.cursorrules`-style files. The clearest use case is scripts that are tool-agnostic and cross-session, which is a small niche. Revisit if users ask for it.

### F4 Embedding-based canonical_key for semantic dedup
**Source:** Mem0's hybrid vector+graph+KV storage.
**Idea:** The current canonical_key is a lowercased text hash — "postgres runs on 5432" and "Postgres is on port 5432" hash differently and don't dedup. An embedding-based similarity threshold would catch paraphrased duplicates.
**Why deferred:** Vector search is already deferred (4.3). If/when vectors come in, this is a natural extension. Until then, the L1 Consolidate pass can catch paraphrased dupes as part of its LLM-based review.

### F5 LOCOMO / LOCOMO-Plus benchmark harness
**Source:** LOCOMO benchmark (Mem0 used it to claim 67% F1) and LOCOMO-Plus (Feb 2026 cognitive memory benchmark).
**Idea:** Build an eval harness that runs umx against LOCOMO so we can cite a concrete number and track regressions.
**Why deferred:** Not a spec concern — it's a test/tooling concern. Belongs in the implementation roadmap, not v0.7. Noted here so we don't forget the benchmark exists.

### F6 Cognitive core (Karpathy)
**Source:** Karpathy's "cognitive core" concept — small model with massive context beats large model with tiny context for knowledge-base tasks.
**Idea:** umx's L1 dream model could explicitly be a small, fast model (Haiku-tier) with all of MEMORY.md + relevant session slices loaded, rather than a medium model with aggressive retrieval. The spec currently says "L1 cheap model" — this would make the "cheap" part load-bearing on context size, not parameter count.
**Why deferred:** Implementation choice, not spec. But worth noting because it reframes what "cheap" means in L1/L2/L3.

### F7 Schema/reconstructive framing (Bartlett)
**Source:** Bartlett 1932 — human memory is reconstructive, not replaying a stored trace. Every retrieval is a new synthesis shaped by current context.
**Idea:** Add a section to the spec acknowledging that injected memory is *reconstructive context*, not ground truth. Facts are prompts to think, not authoritative claims. This reframes failure modes: "the agent believed a stale fact" becomes "the agent reconstructed based on stale context" which is a different problem with a different fix (refresh retrieval rather than edit the store).
**Why deferred:** This is philosophical framing, not a concrete change. Consider for the Section 4 introduction in a future pass.

### F8 Levels of processing (extraction quality tiers)
**Source:** Craik & Lockhart 1972 — deeper semantic processing at encoding produces better retention than shallow lexical processing.
**Idea:** The Gather phase could produce facts at different processing depths: shallow (verbatim extraction) vs deep (paraphrased, linked to existing concepts, tagged with implications). Deep-processed facts would start at higher strength.
**Why deferred:** Good intuition but hard to operationalize cheaply. The `verification` field (2.2) and `source_type` enum (2.20) cover most of the practical value. Revisit if extraction quality becomes a measured bottleneck.

### F9 Encoding specificity (retrieval-cue matching)
**Source:** Tulving 1973 — retrieval is better when cues at retrieval match the encoding context.
**Idea:** Store the *retrieval context* along with the fact — what the user was doing, what files were open, what error prompted the capture. Injection would boost facts whose encoding context resembles the current context.
**Why deferred:** Adds a lot of metadata for a speculative win. Current relevance scoring (tags + topic + recency) is a rough proxy. Reconsider if relevance scoring proves insufficient.

### F10 Formal Ebbinghaus decay parameters
**Source:** Ebbinghaus forgetting curve — exponential decay with a characteristic time constant.
**Idea:** Replace the informal "decay window" with a formal exponential curve: `strength(t) = S_0 * exp(-t/τ)` where τ depends on `memory_type` (procedural long, episodic short). Rehearse phase (2.13) resets t.
**Why deferred:** The informal time-window approach is easier to reason about and tune. Formal curves are premature without data. Kept as an option for v0.8 once we have usage telemetry to fit τ.

### F11 Compilation / precomputed summaries
**Source:** Karpathy "compilation over retrieval" — precompile hot queries into ready-to-inject answers.
**Idea:** For queries that recur often (observed via `meta/usage.sqlite`), precompile the answer during Dream and cache it in MEMORY.md as a ready-to-inject summary. Trades storage for retrieval cost.
**Why deferred:** MEMORY.md already serves this role for the most-referenced topics. Formalizing the hot-query → cached-summary loop is an optimization, not a core feature. Reconsider if retrieval latency becomes a bottleneck.

### F12 Semantic diff for fact updates
**Idea:** When a fact is updated, compute whether the change is syntactic (wording) or semantic (meaning). Syntactic edits don't require supersession; semantic edits do.
**Why deferred:** Requires an LLM call per edit. The Consolidate phase already handles this implicitly. Formalizing it is nice-to-have.

### F13 Confidence calibration self-check
**Source:** Nelson & Narens metamemory — tracking calibration of confidence judgments against outcomes.
**Idea:** Periodic pass that tests stored confidence against observed retrieval-use outcomes. If high-confidence facts are frequently corrected or ignored, down-weight the confidence scorer itself.
**Why deferred:** Needs labeled outcome data (did the agent use this fact? was it right?). The calibration signal in 2.3 (injected-but-unused tracking) is a weaker proxy that covers the 80% case.

### F14 Prompt injection defense for memory content
**Idea:** Every injected fact is untrusted input. Facts that contain "ignore previous instructions" or similar should be flagged at ingest. Consider a "toxicity" lint pass at Gather.
**Why deferred:** Real concern but not v0.7 scope. Adding now would be speculative — there are no known attacks against umx-shaped stores yet. Add a note to the threat model section and revisit.

### F15 MCP-native memory server
**Idea:** Expose umx as an MCP server so any MCP-capable tool gets memory for free, without needing a tool-specific adapter.
**Why deferred:** Strong candidate for v0.8. Depends on MCP's `resources` and `tools` primitives stabilizing. Architecturally, umx CLI + MCP wrapper is the right split — the MCP wrapper is thin.

### F16 Memory checkpoints (LangGraph-style)
**Idea:** Full state snapshots at session boundaries — not just the session log, but a complete "what did the agent know at this moment" snapshot. Useful for replay/debugging.
**Why deferred:** Storage cost is high and the value is narrow (debugging bad consolidations). Session logs + git history already cover most of this.

### F17 Federation / cross-project fact sharing
**Idea:** An org-wide facts repo that individual projects can import from (e.g., "we use ISO 8601 everywhere" is true across every project). Facts would carry a `scope: global` marker.
**Why deferred:** Governance gets complex fast — who approves changes to the global repo? Single-user constraint (4.1) makes this moot for v1. Natural v2 feature once multi-user is on the table.

### F18 Granular privacy levels
**Idea:** Per-fact visibility: `public`, `team`, `personal`, `secret`. More granular than the current `local/private/` vs `local/secret/` split.
**Why deferred:** The binary split from 1.3 handles the sharp injection-risk boundary. More levels are premature without a team story.

### F19 Attention-weighted relevance
**Idea:** Use transformer attention weights (from the agent model) as a relevance signal — which injected facts did the agent actually attend to when generating its response?
**Why deferred:** Requires model-internal access that most tool APIs don't expose. Heuristic-based relevance scoring (tags + recency + composite score) is the practical choice.

### F20 Spec-level extraction quality metrics
**Idea:** Define quality metrics for extracted facts (atomicity, specificity, ground-truth alignment) and require the L1 extractor to self-report scores. Facts below threshold are rejected before entering the store.
**Why deferred:** Good idea, but "what counts as a quality fact" is better learned from usage than specified top-down. Revisit with telemetry from Tier 2's calibration signal.

---

## Execution Order

1. **Fix contradictions** (1.1, 1.6, 1.7, 3.2) — factual errors and no-ops
2. **Security model** (1.2, 1.3) — most critical design gap
3. **Identity & format** (1.4, 1.5, 2.5, 2.7) — needed for implementability
4. **Schema additions** (2.11, 2.12, 2.14, 2.18, 2.20) — new fact fields; do these before behavioral fixes so downstream logic can assume they exist
5. **Behavioral fixes** (2.1, 2.2, 2.3, 2.4, 2.6, 2.8, 2.9, 2.10) — design improvements
6. **Pipeline additions** (2.13 Rehearse, 2.16 Lint) — new Dream phases/subphases
7. **Architecture additions** (2.15 manifest, 2.17 CONVENTIONS.md, 2.19 hot/warm/cold) — cross-cutting structure
8. **New sections** (3.7, 3.9, 3.13) — filling gaps
9. **Polish** (3.1, 3.4, 3.5, 3.6, 3.8, 3.10, 3.11, 3.12) — strengthening
10. **Scope notes** (4.1–4.7) — explicitly deferring things
