# Spec Review: gitmem / UMX v0.9.1

This is a thorough and ambitious specification. The core ideas—encoding strength grounded in source epistemology, PR-based fact governance, fragile→stable lifecycle, tombstone-based forgetting—are genuinely novel and well-motivated. The cognitive science framing, while occasionally decorative, provides a principled alternative to ad-hoc design.

Below I've organized my review into six categories: **naming/identity**, **consistency problems**, **missing or under-specified areas**, **practical concerns**, **specific technical issues**, and **structural/editorial items**.

---

## 1. Naming & Identity

**"gitmem" vs. "umx" is used interchangeably without a clear boundary.**

The title says "gitmem — Github Memory & Universal Memory Exchange (umx)." The body says "The gitmem backend adds GitHub as a durable source of truth" (§1), implying gitmem is a *mode* or *backend* of umx. But later sections use "gitmem" as if it's the whole system (§12 title: "GitHub Dream Governance (gitmem)"; §23a: "Agent Interaction Expectations…when consuming gitmem memory"). At §3, "umx is a filesystem convention and injection protocol — not a service" sits next to "The gitmem backend adds GitHub."

As a reader and potential implementer, I can't tell:
- Is **umx** the spec/format and **gitmem** the GitHub-sync implementation?
- Is **umx** the local-only system and **gitmem** the full system with GitHub?
- Are they the same thing with two names?

**Recommendation:** Define the relationship once, early, and stick to it. The cleanest split is:

> **UMX** = the specification: memory model, fact schema, file format, injection protocol, dream pipeline semantics. Any implementation can be "UMX-compatible."
>
> **gitmem** = the reference implementation's GitHub-backed sync/governance mode (what §12 describes). The `local`-mode-only subset is still UMX but not gitmem.

Then audit every use of both terms against this definition.

---

## 2. Internal Consistency Problems

### 2.1 Lint: every dream cycle, or weekly?

The Phase table (§11) lists Lint as phase **3b** that runs as part of *every* Dream cycle. But the "Lint sub-phase output" section says:

> Lint runs on the slow Dream cycle (weekly by default, configurable via `dream.lint_interval`).

These are contradictory. Does a lightweight lint run every cycle and a deep lint run weekly? The spec needs to distinguish them—or remove Lint from the per-cycle phase table and make it exclusively a periodic job.

### 2.2 Memory Model section mixes descriptive and normative

§4 is 1,500+ words of cognitive-science justification followed by the operational taxonomy. The cognitive science references are interesting but most have no testable normative requirements—they motivate design choices that are *already specified elsewhere* in operational terms. As a result, §4 is simultaneously the longest section and one of the least actionable.

**Recommendation:** Keep the cognitive-science grounding but move the extended literature review to an appendix. Leave §4 as a crisp operational taxonomy with one-sentence grounding citations, e.g.:

> **Fact** — Durable project knowledge expected to survive beyond one session. Grounded in semantic memory [1].

### 2.3 `confidence` is MUST but unused

§5: "confidence is stored for audit and future calibration but does not participate in conflict resolution." §6: confidence appears in neither `trust_score`, `relevance_score`, nor `retention_score`. Yet the fact schema marks it **MUST**, and §9's inline metadata field spec also marks it MUST.

If it's informational-only and excluded from all scoring, requiring every L1 extractor to emit it adds conformance cost without system benefit. It becomes a vanity metric.

**Recommendation:** Make `confidence` **SHOULD** or **MAY** in v1. Elevate to MUST only when it's actually wired into a scoring formula.

### 2.4 Corroboration independence rules have a gap

§5 states two independence rules:

1. Different `source_session` AND different `source_tool`, OR
2. Same tool across sessions separated by ≥24 hours

Then adds:

> Bridge-written facts MUST NOT count as independent corroboration…any fact whose provenance chain includes a bridge export/import round-trip is non-independent even if it later reappears via a different tool or session.

Consider: Fact A from Claude Code (session 1), gets bridge-written to CLAUDE.md, then re-read by Aider, producing Fact B (session 2, different tool, >24h apart). Under rules 1–2, Fact B qualifies as independent. Under the bridge rule, it doesn't. The bridge rule *narrows* the first rule—but the interaction isn't explicit. A precision-minded implementer won't know whether to check the bridge condition *before* or *after* applying the session/tool test.

**Recommendation:** Restate corroboration independence as a single algorithm with explicit check order, e.g.:

```
is_independent(A, B):
  if bridge_provenance(A) or bridge_provenance(B): return false
  if A.source_session == B.source_session: return false
  if A.source_tool != B.source_tool: return true
  if age(|A.source_session - B.source_session|) ≥ 24h: return true
  return false
```

### 2.5 Always-inject floor lacks a secondary tiebreaker

§17: "If always-inject content alone exceeds the budget, injection logs a warning and truncates from the lowest-strength always-inject facts first." The always-inject tier includes "User-global facts (S:≥4)." If you have 20 user-global facts all at S:4, which ones get dropped? The spec doesn't say.

**Recommendation:** Add a secondary tiebreaker for always-inject truncation—e.g., `relevance_score` or recency.

### 2.6 Duplicated defaults risk drift

| Default | Stated in | Also stated in |
|---------|-----------|---------------|
| `hot_tier_max_tokens: 3000` | §9 (MEMORY.md) | §16 (Hot/warm/cold tier) |
| >90% capacity warning | §9 | §16 |
| `prune.min_age_days: 7` | §11 (Prune phase) | §27 (config reference) |
| `prune.abandon_days: 30` | §6 (Task salience) | §27 |

If one is updated without the other, the spec contradicts itself.

**Recommendation:** State each default exactly once (in §27), and reference §27 from all other locations.

### 2.7 `MEMORY.md` generation uses an undefined "synthetic project overview query"

§9: "Score each fact by `relevance_score` using a synthetic 'project overview' query." But `relevance_score` (§6) has terms like `keyword_overlap`, `recent_retrieval`, `scope_proximity`, and `semantic_similarity`—all of which need a *query* to compute against. What is a "synthetic project overview query"? Is it the project name? A set of topic keywords? The content of `CONVENTIONS.md`?

**Recommendation:** Define the synthetic query explicitly, e.g.:

> The synthetic query is the concatenation of project name, all topic slugs from `manifest.json`, and the `CONVENTIONS.md` entity vocabulary.

### 2.8 Pre-compact hook is an injection point but isn't an injection

§16's injection table lists "Pre-compact hook → Emergency sync: commit all uncommitted facts." This is a *write* action, not a *retrieval/injection* action. It doesn't inject facts into context; it durably commits facts before they're lost to compaction. It's in the wrong table.

**Recommendation:** Move the pre-compact hook to a separate "Write Triggers" table or to §18 (Git Strategy).

---

## 3. Missing or Under-Specified Areas

### 3.1 Multi-repo projects

Many real projects span multiple git repositories (microservices, platform + SDKs, monorepo with submodules). The current model is one project slug = one memory repo. What happens when a developer works across repos that share architectural facts? The `user` scope partially addresses this, but user-global facts are different from project-specific facts shared across repos.

**Recommendation:** At minimum, add a non-goal statement clarifying this. Ideally, define a convention for multi-repo projects (e.g., a shared memory repo referenced by multiple slugs).

### 3.2 MCP server security model

The package structure (§25) includes `mcp_server.py` exposing `read_memory` / `write_memory`. No section addresses:

- Can any MCP client read `local/private/`?
- Can any MCP client write facts? At what encoding strength?
- What prevents a malicious MCP tool from polluting memory with S:5 facts?
- Is there a permission model scoped to the calling tool?

**Recommendation:** Add a security model section for MCP access, even if it's "all access by default in local mode, read-only for untrusted callers in remote mode."

### 3.3 `local/secret/` format is undefined

§7 says secrets are "Accessed only by explicit CLI request (`umx secret get <key>`)." But what's the storage format? A JSON file per project? Key-value pairs in a single file? How are they written — `umx secret set <key> <value>`? The CLI surface (§25) lists this command but no section defines the behavior.

**Recommendation:** Add a brief "Secret Storage" subsection defining the format. Even something as simple as "YAML key-value file at `local/secret/secrets.yaml`, gitignored, never committed" would suffice.

### 3.4 Ground_truth_code vs ground_truth_code conflict resolution

§6's hard rule prevents `llm_inference` from beating `ground_truth_code`. But what if two `ground_truth_code` facts contradict? (E.g., docker-compose.yml says port 5433, .env says port 5432.) Both have `source_type_weight: +1.5`. The trust_score formula would need to resolve this via verification, corroboration count, or encoding_strength—but the spec doesn't explicitly discuss this case.

**Recommendation:** Add a paragraph or example covering same-source-type conflicts.

### 3.5 Tombstone garbage collection

Tombstones are append-only in `meta/tombstones.jsonl`. The `expires_at` field supports temporary suppression, but there's no mention of:
- Cleaning up expired tombstones
- Compacting the file
- Post-prune tombstone removal (when the sessions that would re-extract the fact have been archived and are no longer processed)

**Recommendation:** Add a tombstone compaction rule to the Prune phase, e.g.: "Tombstones with `expires_at` in the past, or targeting facts from sessions older than the archive threshold, MAY be removed during Prune."

### 3.6 `fragile` + high strength interaction

Can a fact be `consolidation_status: fragile` AND `encoding_strength: 4`? This happens if a fact is corroborated (S:3→S:4) but hasn't survived a dream cycle yet. §16 says fragile facts SHOULD be injected with the `[fragile]` marker. But a corroborated S:4 fact seems reliable—marking it `[fragile]` may cause agents to unnecessarily double-check.

**Recommendation:** Clarify the interaction. One option: `[fragile]` marking only applies to facts below a strength threshold (e.g., S:≤3 fragile gets the marker, S:≥4 fragile gets injected normally with a subtle provenance note).

### 3.7 Thread safety and concurrent access

Multiple tools might invoke `umx inject` or `umx dream` simultaneously. The spec mentions `meta/dream.lock` for dream coordination, but doesn't address concurrent reads/writes to:
- Markdown files (no locking mentioned for fact writes)
- SQLite (WAL mode helps readers, but concurrent writers need coordination)
- The push queue

**Recommendation:** Add a brief concurrency section or expand the existing lock mechanism to cover fact writes, not just dream cycles.

### 3.8 Performance expectations

No section specifies latency targets. How fast must `umx inject` return? Is 100ms acceptable? 1 second? This matters because injection happens at session start, at each prompt, and at pre-tool hooks—latency there directly impacts user experience.

**Recommendation:** Add a non-normative performance note, e.g., "Implementations SHOULD complete `umx inject` in under 200ms for a typical fact set (<500 facts in scope)."

---

## 4. Practical Concerns

### 4.1 GitHub org creation is non-trivial

`umx init --org my-memory-org` says it "Creates the GitHub org if it doesn't exist." GitHub org creation requires:
- Choosing a billing plan
- Setting up a payment method (for private repos, depending on GitHub's current pricing)
- Two-factor authentication on the creating account

These are UX blockers that can't be automated away. The spec treats org creation as a simple API call.

**Recommendation:** Change to: "Validates the org exists; if not, provides instructions for manual creation, then initialises repos." Or add a `--create-org` flag that acknowledges the manual steps.

### 4.2 Session capture is Phase 3 but is the critical path

The spec acknowledges "Session capture from closed tools" as the "biggest implementation risk" and places adapters in Phase 3. But without session capture, the dream pipeline has no input and the system is limited to native memory reads (which themselves need adapters). The core value proposition—extraction and governance—requires session capture.

**Recommendation:** Move at least one working adapter (Claude Code or Aider) to Phase 1. A system that can capture even one tool's sessions end-to-end is more valuable than a complete scoring/injection system with no sessions to process.

### 4.3 `sentence-transformers` is not lightweight

§20a recommends `sentence-transformers` as a `pip-installable` optional extra and claims "zero-infrastructure compliance for the default install path." But `sentence-transformers` depends on PyTorch (~2GB installed) or at minimum `onnxruntime`. This is the opposite of zero-infrastructure.

**Recommendation:** Either:
- Recommend `fastembed` (ONNX-based, ~100MB, no PyTorch) instead
- Or explicitly acknowledge the heavy dependency and warn users about install size

### 4.4 Semantic dedup key: 16 hex chars = 64 bits

The birthday problem gives ~3% collision probability at 100K facts (plausible for a long-lived multi-project user). A collision would cause two semantically different facts to be treated as duplicates.

**Recommendation:** Increase to 32 hex chars (128 bits), or add a collision-handling mechanism: "If two facts have the same dedup key but different text, treat as a collision and escalate to L2 review rather than auto-deduplicating."

### 4.5 L2 model can also fail

The spec assumes L2 (SotA model) review is reliable. But L2 is still an LLM—it can approve convention-violating facts, miss hallucinations, or introduce its own errors. The spec doesn't address:
- What happens when L2 approves a fact that violates CONVENTIONS.md
- How to detect L2 failures
- Whether L2 decisions should be spot-checked by L3

**Recommendation:** Add to §12 or §22: "L2 approval is a quality filter, not a guarantee. Implementations SHOULD periodically sample L2-approved facts for L3 review as a calibration mechanism."

### 4.6 Dream pipeline complexity vs. value timeline

The dream pipeline has 4 phases, 3 gates, gap signals, provider rotation, graceful degradation, Lint sub-phases, and a weekly cycle. This is a lot of machinery. A user who starts `umx init` today won't see governed facts until they've:
1. Completed a session (captured somehow)
2. Waited for a dream trigger (24h or 5 sessions)
3. Had L1 extract facts
4. Had L2 review and approve
5. Had facts merged

In `local` mode this is faster but still requires the full pipeline.

**Recommendation:** Consider a "fast path" for the first dream cycle on a new project—run immediately on first session, skip gates, write directly in local mode. The three-gate trigger is designed for steady-state, not onboarding.

---

## 5. Specific Technical Issues

### 5.1 SQLite schema is incomplete

The `memories` table (§20) doesn't include columns for:
- `confidence` (in the fact schema but not queryable)
- `expires_at` (needed for efficient TTL-based pruning)
- `applies_to` (needed for environment-aware conflict detection)
- `code_anchor` (needed for staleness detection during Orient)
- `corroborated_by_tools` / `corroborated_by_facts`
- `encoding_context`

The most impactful omission is `expires_at`—without it, the Prune phase must scan all markdown files to find expired facts instead of running `WHERE expires_at < datetime('now')`.

**Recommendation:** At minimum, add `expires_at TEXT`, `applies_to TEXT` (JSON), and `confidence REAL` to the schema.

### 5.2 FTS5 content table rebuild not mentioned

The SQLite setup uses `content='memories'` and `content_rowid='rowid'`, which is the FTS5 "content table" pattern. After an incremental rebuild that modifies the `memories` table directly, the FTS index can become stale and requires `INSERT INTO memories_fts(memories_fts) VALUES('rebuild')`. The spec's incremental rebuild section (§20) doesn't mention this.

**Recommendation:** Add an explicit rebuild step to the incremental rebuild procedure, or switch to the simpler FTS5 pattern without content tables (with triggers as already specified).

### 5.3 Commit type → PR label mapping is implicit

§13 defines commit types (`session`, `extract`, `consolidate`, `lint`, `prune`, `promote`, `correct`, `hypothesis`, `tombstone`, `gap-fill`, `supersede`). §12 defines PR labels (`type: principle`, `type: consolidation`, `type: deletion`, etc.). There's overlap (`consolidate` ↔ `type: consolidation`, `lint` ↔ `type: lint`, `gap-fill` ↔ `type: gap-fill`) but the mapping isn't 1:1 (no PR label for `session`, no commit type for `type: promotion`).

**Recommendation:** Add a mapping table, either here or in §12–13.

### 5.4 Fact YAML schema vs. inline metadata naming

The YAML fact schema uses `corroborated_by_tools` and `corroborated_by_facts`. The inline metadata uses `cort` and `corf`. Having two naming conventions for the same data is error-prone for implementers. The mapping IS documented, but it's an unnecessary cognitive load.

**Recommendation:** Pick one naming convention. If space is the concern in inline metadata, use the short names everywhere and define them as the canonical keys.

### 5.5 Provider rotation list inconsistency

§11 narrative: "Cerebras → Groq/Kimi K2 → GLM-4.5 → MiniMax → OpenRouter"
§27 config: `cerebras`, `groq`, `glm`, `minimax`, `openrouter`

The narrative includes specific model names (Kimi K2, GLM-4.5) that the config omits. This will confuse users who try to match the narrative to their config file.

**Recommendation:** The narrative should use provider names only: "Cerebras → Groq → GLM → MiniMax → OpenRouter." Model selection happens within each provider's adapter.

---

## 6. Structural & Editorial Issues

### 6.1 Typo: §9a "Proceedure" → "Procedure"

### 6.2 Section numbering is ad hoc

3a, 9a, 20a, 23a, 26a are interspersed as if they're top-level sections but are logically subsections. This makes the ToC look like 31 top-level sections when there are really ~20 with sub-sections. It also makes referencing sections error-prone.

**Recommendation:** Use proper hierarchical numbering: §3.1, §9.1, §20.1, etc.

### 6.3 Normative vs. explanatory content interleaved

The spec mixes MUST/SHOULD requirements with design rationale in the same paragraphs. For implementers, it's hard to extract "what must I build?" from "why was it designed this way?"

**Recommendation:** Adopt a visual convention (e.g., normative requirements in bold or in requirement blocks, rationale indented or in asides). Even a simple pattern like:

> **Requirement:** The Prune phase MUST remove facts below threshold.
> *Rationale: S:1 facts that aren't corroborated within the decay window represent noise, not signal.*

### 6.4 Appendix C reads like an ADR, not a spec

The "Deferred Considerations" section documents reviewer feedback and decisions from v0.7. This is valuable project history but isn't normative spec content.

**Recommendation:** Move to a companion document (ADR or changelog). Keep the spec forward-looking.

### 6.5 Comparison table (§24) references unfamiliar tools

"MemPalace" and "DiffMem" aren't widely known. Brief descriptions or links would help readers evaluate the comparison.

**Recommendation:** Add a one-line description and link for each tool, or drop obscure ones and focus on well-known comparators.

### 6.6 `consolidation_status` after corroboration

How does a corroborated fragile fact eventually become stable? §14 says the fragile→stable transition happens via dream cycle survival, independent corroboration, or manual confirmation. If a fragile fact gets corroborated during Consolidate, does it immediately transition to stable (rule 2), or does it need to survive the *next* Prune phase (rule 1)?

The current wording of rule 2 ("a different tool…produces a matching fact") sounds like it takes effect immediately, but the "Prune phase applies rules 1 and 2 automatically on every dream cycle" implies it happens at Prune time.

**Recommendation:** Clarify whether corroboration-based stabilisation is immediate or deferred to the next Prune phase.

### 6.7 The `Rehearse` phase is mentioned in open questions but its absence leaves a gap

The spacing effect and testing effect suggest that facts not recently used should be actively reinforced. Without Rehearse, the only mechanism preventing fact loss is the retention score during Prune. If a high-value fact stops being referenced (e.g., a non-obvious architectural constraint that doesn't come up in daily work), it will decay and be pruned. Tombstones prevent re-derivation resurrection. The fact is simply gone.

I'm not saying implement Rehearse now—but the open question should mention this failure mode: **valuable but infrequently-referenced facts will be pruned without a reinforcement mechanism.**

**Recommendation:** Add to §22 (Failure Modes): "Inactive but valuable fact loss — facts that are correct and important but rarely referenced in daily work will decay and be pruned. Mitigation: increase retention score weight for high-strength facts; future Rehearse phase."

---

## Summary Assessment

**What's genuinely excellent:**

- The encoding strength + source_type model is the best epistemic-grounding system I've seen for AI agent memory. It directly addresses the "hallucinated inference beats code truth" failure mode.
- PR-based governance with tiered review is the right primitive for auditable memory.
- The fragile→stable lifecycle, tombstones, and supersession chains form a coherent lifecycle model.
- Redaction fails closed. The Shannon entropy scan is a thoughtful addition.
- The separation of trust/relevance/retention into three scores with different term memberships is architecturally sound.
- The `applies_to` schema preventing false contradictions is a clean solution.

**What needs the most work before v1.0:**

1. **Resolve the gitmem/umx naming ambiguity** — this will confuse every new reader
2. **Fix the Lint timing contradiction** (every cycle vs. weekly)
3. **Move session capture forward in the roadmap** — without it, nothing else works
4. **Address the `sentence-transformers` dependency weight** honestly
5. **Consolidate duplicated defaults** into §27 only
6. **Add the missing SQLite columns** (especially `expires_at`)
7. **Tighten the line between normative requirements and design rationale**

The spec is 80% of the way to something implementable. The architectural bones are strong. The remaining 20% is consistency, scoping (what's truly v1 vs. future), and making it possible for a reader to extract "what must I build?" in under an hour.