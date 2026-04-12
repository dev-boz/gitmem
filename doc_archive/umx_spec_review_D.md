# UMX Specification v0.9 — Technical Review

## Overall Assessment

This is an exceptionally well-thought-out specification. The cognitive science grounding is genuine and well-applied (not decorative), the governance model is the clearest differentiator in this space, and the markdown-as-truth decision is both pragmatic and principled. The spec reads like it was written by someone who has actually been bitten by the problems it solves.

**Verdict: Production-ready as a design document.** The open questions are honest and appropriately scoped. What follows are the issues I found worth flagging — most are tightenings, not redesigns.

---

## Strengths

- **Cognitive science grounding is load-bearing, not decorative.** Encoding strength (Tulving), consolidation status (Dudai), interference pointers (McGeoch), source monitoring (Johnson et al.) — each maps to a concrete mechanism. The Bartlett schema-conflict flag is particularly clever.
- **Three-score separation (trust/relevance/retention)** is architecturally correct. Mixing "what's true" with "what's useful right now" is the #1 design error in memory systems. This spec doesn't make it.
- **Hard rule: `llm_inference` never beats `ground_truth_code`** is exactly right. Soft scoring systems always eventually get gamed by accumulation; a hard override prevents the single worst failure mode.
- **Session immutability + tombstones** is the correct answer to the "resurrection" problem. Most systems either lose auditability (by editing history) or lose control (by allowing re-derivation to resurrect deleted facts).
- **`CONVENTIONS.md` as enforcement contract** — making L2 reject facts that violate conventions turns a style guide into a machine-enforced schema.
- **Greedy packing by `relevance/tokens`** is a nice improvement over naive sort-by-relevance.
- **Redaction fails closed** is the right security posture. The Shannon entropy + assignment-context approach for catching novel secret formats is well-considered.
- **`applies_to` schema** preventing false contradictions across environments is a subtle but important correctness guarantee.

---

## Issues Found

### 1. Structural/Formatting

| # | Location | Issue |
|---|----------|-------|
| 1.1 | §21 heading | **Missing section heading.** Lines 1667–1698 contain "Tombstones and Forgetting" content but the `## 21  Tombstones and Forgetting` heading is absent. The ToC references it at line 33, but the actual heading is missing from the body. The section starts mid-page without a proper `##` header after §20a's closing content. |
| 1.2 | §5, line 299 | **Orphaned table row.** The `external_doc` source type row (`\| \`external_doc\` \| ...`) appears *after* the table closing and the `dream_consolidation` boundary paragraph, making it look like a stray line rather than part of the source type enum table. Should be moved up into the table proper (before `dream_consolidation`). |
| 1.3 | ToC numbering | **`20a` breaks conventional numbering.** Consider `20.1` or `21` with a renumber. The `20a` label feels like a late insertion (which it likely is), but for a v1 spec it should have a clean structure. |

### 2. Semantic/Logical

| # | Location | Issue |
|---|----------|-------|
| 2.1 | §5, Corroboration rule | **Bridge exclusion is one-directional.** "Bridge-written facts (`CLAUDE.md` markers) MUST NOT count as independent corroboration sources" — but what about the reverse? If umx writes a bridge to `CLAUDE.md`, and Claude Code later reads that bridge and writes it back to its native memory, the adapter would see it as a "new" `tool_output` source. This is a corroboration laundering vector. The spec should explicitly state that facts whose provenance chain traces back through a bridge write are not independent. |
| 2.2 | §6, Trust score | **`confidence` is "informational until calibrated" but has a weight (`w_c`) in the trust formula.** If it's informational-only, it should either have `w_c = 0` by default (making the formula honest) or the spec should pick a lane: is it informational, or is it a composite input? The config reference (§27) sets `confidence: 0.5` which is non-zero. |
| 2.3 | §9, Parser behaviour | **User edit creates new fact with `supersedes` — but what scope/topic?** If a user adds a line to `facts/topics/devenv.md`, the parser assigns S:5 and generates metadata. But what if the user adds a line to the *wrong* file? Does the parser honour file location (deriving topic from filename) even if the fact text is clearly about a different topic? There's no mention of cross-topic validation at parse time. |
| 2.4 | §11, Gap signal | **Tool-driven emission triggers are sound but may be too strict.** Condition 3 ("session completed successfully") excludes the case where a gap was real but the agent failed for unrelated reasons. Consider "session continued past the workaround" instead of "completed successfully." |
| 2.5 | §14, Fragile → stable | **Rule 1 ("survived one dream cycle") has an ambiguity.** A fact created 5 minutes before a dream cycle runs would "survive" trivially — it was never at risk. Should the fact need to have been *processed* by the dream cycle (i.e., present during Gather/Consolidate), not merely created before one ran? |
| 2.6 | §15, Folder → Project | **"Same fact in ≥3 folder-level memories independently → auto-promote."** This has no human gate — unlike Project → User (L3 required) and Principle promotion (L3 required). Is this intentional? Auto-promoting folder → project without review seems inconsistent with the governance philosophy elsewhere. |
| 2.7 | §17, Always-inject | **`CONVENTIONS.md` summary is in the always-inject set, but §8 says the full `CONVENTIONS.md` is always injected.** Which is it — the full file or a summary? If the full file grows large (which it will for complex projects), it could consume a significant fraction of the 3000-token hot tier budget. |
| 2.8 | §19, Session JSONL | **No `ended` timestamp in `_meta`.** The session captures `started` but not `ended`. Duration is useful for dream pipeline heuristics (short sessions may produce lower-quality facts) and for the viewer. Adding `ended` or `duration_seconds` to the `_meta` record would be cheap and useful. |

### 3. Specification Gaps

| # | Location | Issue |
|---|----------|-------|
| 3.1 | §9, Inline metadata | **No escaping rules for `-->` inside JSON values.** If a fact's text or any metadata value contains the literal string `-->`, the HTML comment parser would terminate early. The spec should define an escaping convention (e.g., `--\>` or `\u002D\u002D\u003E` in the JSON). This is acknowledged as deferred in Open Questions but is a correctness issue, not a nicety. |
| 3.2 | §12, L2 Actions workflow | **L2 workflow doesn't filter session PRs.** The `if` condition checks for `type: consolidation`, `type: gap-fill`, `type: lint`, `type: supersession` labels — but not `type: deletion` or `type: principle`. Type `deletion` at S:≥3 should trigger L2 per the label system rules. Type `principle` should always escalate. Missing from the workflow filter. |
| 3.3 | §13, Branch naming | **`session/` branches aren't used in hybrid mode.** In hybrid mode, sessions push directly to main (line 909). But the branch convention table still lists `session/<date>-<ulid>`. Clarify: is the session branch convention only for `remote` mode? |
| 3.4 | §18, Merge rule | **"Conflicting text same ID → new conflict entry"** contradicts §5's rule that `fact_id` is immutable and identity is immutable. If two sources produce the same `fact_id` with different text, that's a data integrity error, not a normal conflict. The merge rule should treat this as an error condition, not route it to `conflicts.md`. |
| 3.5 | §20, SQLite schema | **`memories_fts` is not linked to `memories` table.** The FTS5 virtual table has `content` and `tags` columns but no `content_rowid` linking back to the `memories` table. Without this, FTS results require a separate lookup. Standard pattern is `USING fts5(content, tags, content=memories, content_rowid=id)` or a similar join. |
| 3.6 | General | **No versioning on the spec itself.** The spec references its own version informally ("v0.9") but there's no machine-readable version field. If tools are declaring UMX conformance levels, they need to say *which version* of the spec they conform to. Consider a `spec_version` field in the schema or a formal version identifier. |
| 3.7 | §5, `dream_consolidation` | **"Inherits from inputs (avg of source facts)" for both `source_type_weight` and `encoding_strength`.** Averaging could produce non-integer strengths (e.g., avg of S:3 and S:4 = 3.5). The spec doesn't address rounding/flooring. |

### 4. Potential Operational Concerns

| # | Topic | Issue |
|---|-------|-------|
| 4.1 | Git repo bloat | Monthly gzip helps, but long-lived projects with verbose tools (Claude Code sessions can be 100KB+) will accumulate GB-scale repos. The spec mentions `git filter-repo` for emergency purge only. Consider documenting a recommended `git gc` or shallow clone strategy for large repos, or a "cold archive" mechanism that moves old sessions to a separate archive repo. |
| 4.2 | PR volume in team-like setups | Even for a solo user with 3-4 projects, active dream pipelines could produce 5-10 PRs/day across repos. GitHub notification fatigue is real. The spec acknowledges batching ("one PR per dream cycle per repo") but doesn't discuss notification management (e.g., a notification filter rule, a separate GitHub notification routing for the memory org). |
| 4.3 | Free-tier provider rotation | The default rotation (Cerebras → Groq → GLM → MiniMax → OpenRouter) assumes these providers will maintain free tiers. Two of these (GLM, MiniMax) have historically been unstable with free-tier availability. The spec's graceful degradation handles this, but a config update mechanism (recommended provider list fetched from a central source) might reduce user friction when providers change. |
| 4.4 | Startup sweep timing | "On session start, before pull, scan for uncommitted sessions from prior runs" — this adds latency to every session start. For the common case (no orphans), a fast check (e.g., `git status --porcelain sessions/`) should be specified to keep the happy path fast. |
| 4.5 | Multi-machine `usage.sqlite` | `usage.sqlite` is local-only, which is correct for avoiding merge conflicts. But a user working on two machines will have divergent usage telemetry, leading to different relevance scores and different injection behaviour for the same project. This isn't a bug per se, but worth documenting as a known limitation. |

### 5. Nitpicks & Polish

| # | Location | Issue |
|---|----------|-------|
| 5.1 | §4, Reference [2] | Schacter [2] is cited for the "JPEG compression" effect in §1, but that's a modern metaphor, not from Schacter 1987. The citation is slightly misleading — Schacter's paper is about implicit memory, not compression artifacts. |
| 5.2 | §5, dedup key | `SHA-256(lowercase(text + scope + topic))` — no delimiter between fields. `text="foo", scope="bardev", topic="env"` has the same hash as `text="foobar", scope="dev", topic="env"`. Insert a delimiter: `SHA-256(lowercase(text + "\x00" + scope + "\x00" + topic))`. |
| 5.3 | §11, Dream lock | `meta/dream.lock` is mentioned but never specified. What format? PID? Timestamp? Staleness timeout for crash recovery? A stuck lock file is a common operational issue. |
| 5.4 | §19, Redaction | The built-in pattern list (AWS, GCP, Anthropic, OpenAI, Stripe, etc.) is mentioned but not enumerated. A normative list of minimum required patterns would make conformance testing possible. |
| 5.5 | §25, CLI | `umx doctor` is in the package structure but not listed in the CLI surface (line 1885 has `--fix` but no dedicated entry). Actually — looking again, it is there at line 1885. Ignore this one. |
| 5.6 | Appendix A | The extraction prompt says "S:5 — Reserved for human confirmation (never assign this)" but doesn't mention this constraint for S:4 with `ground_truth_code`. An L1 agent could assign S:4 to a `ground_truth_code` fact — is that correct, or should L1 be capped at S:3 with S:4 reserved for L2/corroboration? The current rules allow L1 to assign S:4 which feels high for uncorroborated extraction. |

---

## Summary of Recommendations

> [!IMPORTANT]
> **Fix before v1.0:**
> 1. Add missing §21 heading
> 2. Fix `external_doc` table row placement (§5)
> 3. Define `-->` escaping in inline metadata JSON
> 4. Add null-byte delimiters to semantic dedup key hash
> 5. Resolve the `confidence` weight contradiction (informational vs. weighted)
> 6. L2 workflow filter: add `type: deletion` and `type: principle`
> 7. Clarify: full `CONVENTIONS.md` vs summary in always-inject tier
> 8. Link FTS5 virtual table to `memories` base table

> [!NOTE]
> **Consider for v1.0 (quality improvements):**
> - Folder → Project auto-promotion: add a lightweight gate?
> - Add `ended` timestamp to session `_meta`
> - Document `dream.lock` format and staleness recovery
> - Clarify `session/` branch usage per dream mode
> - Address bridge-mediated corroboration laundering
> - Document multi-machine `usage.sqlite` divergence as known limitation
> - Specify rounding for averaged `encoding_strength` in `dream_consolidation`

> [!TIP]
> **Quality signals:**
> - The deferred items (Appendix C) are well-triaged
> - The failure modes table (§22) is unusually comprehensive for a v0.9 spec
> - The Appendix A/B prompt skeletons are a smart inclusion — they make the spec testable
> - The three conformance levels (Read/Write/Full) is the right adoption gradient
