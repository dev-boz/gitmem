# UMX Specification v0.9 — Thorough Review

**Reviewer:** Antigravity  
**Date:** 2026-04-10  
**Document:** `D:\umx-spec-v0_9.md` (2,302 lines, ~132 KB)

---

## Executive Summary

This is an impressively ambitious and well-thought-out specification. The cognitive science grounding is genuinely novel in this space, the git-native governance model is a genuine differentiator, and the separation of trust/relevance/retention scoring shows real architectural maturity. The spec reads as the product of someone who has deeply used AI coding tools and been frustrated by their memory limitations.

That said, a v0.9 spec at this length has accumulated some internal tensions, under-specified edges, and a few areas where the complexity may be working against the stated design principles. This review is organized from most actionable to most strategic.

---

## 1. Structural & Editorial Issues

### 1.1 Broken Section Numbering (§21)

**Line 1668–1698:** Section 21 ("Tombstones and Forgetting") is missing its header. The text jumps from the end of §20a's embedding storage discussion directly into "### Problem" with no `## 21` marker. The ToC references `#21-tombstones-and-forgetting` but the anchor doesn't exist in the document.

Compare line 1666–1668:
```
(blank line after §20a content)

### Problem
```

This should be:
```
## 21  Tombstones and Forgetting

### Problem
```

### 1.2 `source_type` Table Formatting (§5)

**Line 299:** The `external_doc` row of the source_type enum table is malformed — it appears _after_ the table's closing pipe row for `dream_consolidation`, making it look like a stray line rather than a table entry. It should be integrated into the table between `dream_consolidation` and the closing.

The current layout:
```
| `dream_consolidation` | Derived by the Dream pipeline... | ... | Inherits from inputs |
| `external_doc` | Extracted from documentation... | ... | S:3 |
```

The `external_doc` row appears to have been added after the fact and not properly inserted into the table structure.

### 1.3 Inline Metadata Example Inconsistency

**Line 674:** The inline metadata example shows `"sup":"01JQXABCDEF0000000000"` (supersedes) on the first fact, but the fact schema field table in §9 (line 696) uses the key `sup` for "Supersedes" and `sby` for "Superseded by." The example is internally consistent, but the _second_ example fact (line 675) omits `sup` — which is correct (it's MAY), but worth noting that the first example shows a `sup` value without showing the corresponding `sby` on whatever fact it supersedes. A reader following the audit trail would hit a dead end.

> [!TIP]
> Consider adding a third example line showing the superseded fact with `"sby":"01JQXYZ1234567890ABCDEF"` to demonstrate the full chain.

### 1.4 Duplicate `semantic_weight` Configuration

**Line 2012:** In the config reference, `weights.relevance.semantic_similarity` is described as "overridden by `search.embedding.semantic_weight` if set." This creates two config paths for the same value (`p_v`). The spec should pick one canonical location and alias the other, or remove the duplicate. Having two config keys that interact non-obviously is a usability footgun.

### 1.5 Minor Typos & Style

- **Line 56:** "cross-tool isolation" and "deeper problem" — the `**` bold around the second problem statement is effective, but the paragraph is quite dense. Consider splitting.
- **Line 192:** Parenthetical about Ovsiankina vs Zeigarnik is fascinating but adds ~40 words of nuance that most spec readers won't need. Consider moving to a footnote or the references section.
- **Line 869:** The Gather phase description is a 7-line single-cell table entry. Extremely hard to parse. Consider bullet-pointing or splitting into sub-rows.

---

## 2. Internal Consistency Problems

### 2.1 `confidence` — Informational or Scored?

The spec is ambivalent about `confidence`:

- **Line 284:** "Strength is *how deliberately*... Confidence is *how certain* the extractor was..."
- **Line 355:** `confidence` appears in the `trust_score` formula with weight `w_c`.
- **Line 2001:** Default weight for `confidence` in trust scoring is `0.5` — a _significant_ weight.
- **Line 2117:** Open questions says: "bounded [0,1], **informational-only** for conflict resolution in v1."
- **Line 683:** Inline metadata spec says confidence is "informational for v1."

**The contradiction:** If `confidence` is "informational-only" in v1, it should NOT appear in the `trust_score` formula with a non-zero weight. Either:
1. Remove it from the trust_score formula and set its default weight to 0.0, or
2. Commit to using it in v1 and drop the "informational-only" qualifier.

Currently, an implementer reading §6 would include it; an implementer reading §9 or the open questions would exclude it.

> [!IMPORTANT]
> This is the most significant internal consistency issue in the spec. It directly affects conflict resolution behaviour.

### 2.2 `dream_consolidation` Source Type — Boundary Ambiguity with Orient Phase

**Line 298:** The spec carefully distinguishes `dream_consolidation` (multi-session synthesis) from `llm_inference` (single-session extraction). But the **Orient phase** (line 868) performs staleness checks on `ground_truth_code` anchors, which may result in _demoting_ a fact's `consolidation_status` to `fragile`. This demotion is a Dream pipeline action on a single fact — is it a `dream_consolidation` action? The spec doesn't say.

More subtly: if Orient demotes a `ground_truth_code` fact to `fragile`, does its `source_type` change? The spec says `source_type` reflects epistemic origin, not current status, so it should remain `ground_truth_code`. But a `ground_truth_code` fact at `consolidation_status: fragile` is semantically odd — it was ground truth _at extraction time_ but the ground may have shifted.

> [!NOTE]
> Recommend adding a clarifying note: "Orient-phase demotion to `fragile` does NOT change `source_type`. The fact retains its original epistemic origin; `consolidation_status` reflects current reliability, not origin."

### 2.3 Corroboration Independence Rule — Bridge Facts

**Line 226:** "Bridge-written facts (`CLAUDE.md` markers) MUST NOT count as independent corroboration sources." This is correct and important. But the bridge _reads from_ umx facts (line 1227–1236). If a bridge writes a fact to `CLAUDE.md`, and Claude Code later reads it back as "tool native memory" (adapter, line 807), the adapter would assign `source_type: tool_output` and `verification: self-reported`. The anti-circular-corroboration rule catches the tool _name_ case, but does it catch the case where the same fact round-trips through a bridge and comes back with a different `source_session`?

The independence rule requires "different `source_session` values AND different `source_tool` values" — a bridge round-trip would have a different session but the same tool (Claude Code). So it would be correctly excluded only if the tool name matches. But what if the user uses Cursor (which also reads `CLAUDE.md`)? Then a umx → bridge → Cursor round-trip would have both different tool AND different session, passing the independence test despite being circular.

> [!WARNING]
> The corroboration independence rule has a bridge round-trip loophole: `umx → CLAUDE.md bridge → Cursor reads CLAUDE.md → adapter imports as "new" fact → corroboration detected`. Consider adding: "Facts whose text matches a bridge-written marker MUST NOT be treated as independent corroboration regardless of tool or session."

### 2.4 `applies_to` Overlap Semantics — Wildcard Edge Cases

**Line 319–323:** Matching semantics say `null`/absent `applies_to` is equivalent to all-wildcards. Two facts conflict only if their `applies_to` values overlap on all specified keys. But consider:

- Fact A: `applies_to: {env: dev}` (no `os`, `machine`, `branch` keys)
- Fact B: `applies_to: {os: linux}` (no `env`, `machine`, `branch` keys)

Do these conflict? Fact A has no `os` key (wildcard). Fact B has no `env` key (wildcard). So Fact A applies to `env:dev, os:*` and Fact B applies to `env:*, os:linux`. They overlap on `env:dev, os:linux`. By the "overlap on all specified keys" rule, they conflict — but this seems wrong. They're making claims about different dimensions.

The semantics need a third option: keys present in only one fact are treated as "not comparable" rather than "wildcard overlap." Or the spec should require all four canonical keys to be present (defaulting to `*` explicitly).

### 2.5 Prune Phase — `min_age_days` vs `expires_at`

**Line 872:** Prune removes facts below threshold AND older than `prune.min_age_days`. But `expires_at` is also checked. What happens if a fact has `encoding_strength: 1` (below threshold), age 3 days (below `min_age_days: 7`), but `expires_at: 2026-04-08` (already expired)?

Does `min_age_days` protect an expired fact? The spec doesn't specify. Recommendation: `expires_at` should override `min_age_days` — an explicitly expired fact should be pruned regardless of age.

---

## 3. Technical Design Concerns

### 3.1 SHA-256 Dedup Key Truncation to 16 Hex Chars

**Line 243:** The semantic dedup key is `SHA-256(lowercase(text + scope + topic))` truncated to 16 hex chars = 64 bits. For a typical umx deployment (hundreds to low thousands of facts), collision probability is negligible. But the spec is designed for long-lived memory stores that accumulate over years.

At 10,000 facts, birthday-paradox collision probability is ~1 in 3.4 billion — fine. At 1 million facts (a very large, long-lived user store): ~1 in 34,000 — starting to be concerning. Since dedup collisions would cause fact loss (one fact silently suppresses another), this is a data integrity risk.

> [!TIP]
> Consider truncating to 32 hex chars (128 bits) instead of 16. The storage cost is 16 extra bytes per fact in inline metadata — negligible. Collision probability at 1M facts drops to ~1 in 10^23.

### 3.2 Greedy Packing vs Always-Inject Floor Interaction

**Lines 1264–1271:** Always-inject facts are committed first, then the packing algorithm fills the remaining budget. But the always-inject tier includes "User-global facts (S:≥4), `CONVENTIONS.md` summary, active open tasks." For a user with many projects and many conventions, the always-inject floor could consume the entire budget, leaving zero room for project-specific facts.

The spec says "truncate from the lowest-strength always-inject facts first" if always-inject exceeds budget — but CONVENTIONS.md is S:5 ground truth. If CONVENTIONS.md alone exceeds the budget, the system breaks. The spec should specify a maximum size for CONVENTIONS.md or a budget partition (e.g., "always-inject MAY NOT consume more than 60% of the total budget").

### 3.3 SQLite WAL Mode — Concurrent Writers

**Line 1481:** The spec mandates `PRAGMA journal_mode=WAL` for concurrent reader/writer support. This is correct for the read path, but SQLite WAL still serialises _writers_. If multiple agents are running simultaneously (which the spec supports — different tools on the same project), they will contend on the SQLite write lock. For local-only index updates this is fine (fast writes). For `usage.sqlite` updates at session end, it could block if two sessions end simultaneously.

This is a minor issue — the probability is low and the lock duration is short. But worth a note: "SQLite WAL supports concurrent reads but serialises writes. Short write contention is expected and acceptable at typical umx scale."

### 3.4 Session JSONL Append — No Fsync Guarantee

**Line 1428:** The startup sweep catches uncommitted sessions from crashes. But the _session file itself_ might be partially written (truncated JSONL line) if the process was killed mid-write. The spec should specify how to handle a partially-written final line in a session file: "Parsers MUST tolerate and skip a truncated final line in session JSONL files."

### 3.5 Token Estimation: `len(text) // 4`

**Line 1262:** The `len(text) // 4` approximation is reasonable for English prose but systematically underestimates for code (identifiers, operators, and special characters tokenise less efficiently). For facts like `"src/api/auth/middleware.ts:42 — exports verifyJWT(token: string): Promise<UserClaims>"` the real token count might be 2x the estimate. This could cause injection to exceed its budget by up to 50%.

Consider `len(text) // 3` as a safer approximation for code-heavy fact stores, or expose the divisor as a config parameter.

### 3.6 Embedding in `.umx.json` — Scaling Concern

**Line 1593–1605:** Embeddings are stored per-fact in `.umx.json`. A `all-MiniLM-L6-v2` embedding is 384 floats = ~3 KB in JSON. At 1,000 facts, that's ~3 MB — fine. At 10,000 facts, it's ~30 MB. Since `.umx.json` is read on every search query, large files will add latency.

Consider moving embeddings to the SQLite index instead of JSON, or sharding `.umx.json` per-topic to match the markdown sharding.

---

## 4. Security & Safety Gaps

### 4.1 Entropy Threshold — Base64-Encoded Short Strings

**Line 1414:** The entropy threshold of 4.5 bits/char is well-calibrated for the general case. But consider: a 16-character API key like `AaBbCcDdEeFfGgHh` has Shannon entropy of ~4.0 bits/char (each character appears once → perfect uniformity at that length). It would pass the default threshold. At minimum length 16 and threshold 4.5, there's a window where short, structured API keys evade detection.

The assignment-context requirement ("KEY = ...") mitigates this significantly. But if a session transcript contains `export ANTHROPIC_API_KEY=sk-ant-api03-...` the key portion is often 40+ characters and Base64-ish, so it should score ~5.5+. The concern is mainly for short custom tokens.

> [!NOTE]
> This isn't a gap in practice because the regex pass catches all standard provider key formats. The entropy check is a _second line of defence_ for novel formats. The combined system is sound.

### 4.2 Quarantine Directory — Not Gitignored by Default

**Line 1420:** Sessions that fail redaction are quarantined to `local/quarantine/`. The `local/` directory is gitignored (line 474), so quarantined sessions won't be committed. Good. But if a user manually runs `git add .` in the memory repo, the gitignore will protect them. This is fine — just confirming the quarantine path is correctly within the gitignored tree.

### 4.3 `umx purge` — BFG/filter-repo Availability

**Line 1426:** `umx purge` relies on BFG Cleaner or `git filter-repo`. Neither is a standard git installation component. The spec should note that `git filter-repo` is a Python package (pip-installable, fits the Python ecosystem) and SHOULD be declared as a dependency or checked at runtime with a clear error message.

### 4.4 Fact-Level Redaction — Timing

**Line 1422:** "The same redaction patterns used for sessions MUST also be applied to candidate facts BEFORE they enter `facts/`." This is correct. But when does this happen in the Dream pipeline? The Gather phase extracts facts from sessions (which are already redacted). So a fact containing a secret would mean the session redaction _missed_ it, and now the fact-level redaction is the fallback.

The concern: if session redaction missed a secret, it's already committed to git history in the session file. The fact-level redaction prevents it from propagating further, but the damage (secret in session git history) is already done. The spec should acknowledge this: "Fact-level redaction is a defence-in-depth measure. If a secret passes session redaction, it is already in git history and requires `umx purge` to remove."

---

## 5. Under-Specified Areas

### 5.1 CONVENTIONS.md Structure — No Schema

**Lines 580–618:** CONVENTIONS.md is described by example but has no formal structure. The Dream pipeline's L2 reviewer (line 1000) "checks proposed facts against CONVENTIONS.md" — but how? If CONVENTIONS.md is free-form markdown, the L2 reviewer is doing free-text reasoning against free-text rules, which is exactly the kind of unreliable LLM behaviour the spec is designed to guard against.

Consider defining a minimal structured format for at least the topic taxonomy and entity vocabulary sections (e.g., YAML frontmatter, or a specific heading-level convention the parser can extract deterministically).

### 5.2 `umx doctor` — Diagnostic Scope

**Line 1854:** `doctor.py` is listed in the package structure, and the CLI shows `umx doctor [--fix]`. But the spec doesn't define what `doctor` checks beyond the brief mention "auth, push queue, locks, schema, orphans, quarantine." This is a v1-critical tool — it's the user's primary way to diagnose issues.

Worth at least enumerating the check categories:
1. GitHub auth (PAT validity, org access)
2. Push queue (pending pushes, failed retries)
3. Lock files (stale `dream.lock`)
4. Schema version (migration needed?)
5. Orphaned scope entries (files/folders that no longer exist in project)
6. Quarantined sessions (unresolved redaction failures)
7. Index staleness (`last_indexed_sha` vs HEAD)
8. Hot tier capacity (>90% token budget)
9. Embedding model availability (if `backend: hybrid`)

### 5.3 Multi-Machine Same-User — Merge Strategy

The spec assumes a single user but doesn't explicitly address the multi-machine scenario beyond "git merge resolves" (line 67). Two machines running simultaneous sessions on the same project will both write to `sessions/` (append-only, no conflicts) but may both trigger Dream pipelines. The lock file (`meta/dream.lock`) is local-only — it doesn't prevent two machines from running Dream simultaneously.

In `remote`/`hybrid` mode, both machines would open PRs — the GitHub PR system naturally handles this. In `local` mode, both machines would commit to main and push, potentially creating merge conflicts on `facts/` files.

> [!IMPORTANT]
> The `dream.lock` file only prevents concurrent _local_ dreams. Multi-machine `local` mode users need either a coordination mechanism or a warning: "In `local` mode across multiple machines, run `umx sync` before `umx dream` to minimise merge conflicts."

### 5.4 Session Archival — Compressed File Access

**Line 1456:** Archived sessions are gzip-compressed monthly. But `umx audit --rederive` and `umx search --all` need to read these. The spec doesn't describe how compressed sessions are handled — are they decompressed on-demand, or does the search index cover them? If the search index is rebuilt from markdown (fact files), compressed sessions are only needed for raw-track queries and re-derivation, which is infrequent.

Add a note: "Compressed session archives are decompressed on-demand for `umx audit` and raw-track queries. The SQLite FTS index does NOT cover session content — sessions are searched via brute-force scan or dedicated session-level index."

### 5.5 `encoding_context` — No Schema

**Lines 279–281:** The `encoding_context` field is defined with a brief example (`task_type: debugging`, `active_module: database`) and referenced in the relevance score (`p_x × context_match`). But there's no schema, no enumeration of valid `task_type` values, and no definition of how `context_match` is computed. This makes the feature unimplementable as specified.

Either define a minimal schema and matching algorithm, or explicitly defer: "encoding_context schema and context_match scoring are deferred to post-v1. The field is reserved but not scored."

---

## 6. Cognitive Science Grounding — Critique

### 6.1 Strengths

The cognitive science grounding is the spec's most distinctive feature and is generally well-applied:
- **Tulving's episodic/semantic distinction** maps cleanly to the source hierarchy.
- **ACT-R activation** is a natural model for the composite scoring system.
- **Interference Theory** motivating `conflicts_with` is exactly right.
- **Consolidation Theory** (`fragile` → `stable`) is a clever application.
- **Source Monitoring** driving `source_type` is the most impactful design decision — it directly prevents hallucination propagation.

### 6.2 Overstretched Applications

- **Ovsiankina Effect (§6, line 192, 420):** The spec correctly notes that the Zeigarnik _memory advantage_ claim was refuted, keeping only the _resumption tendency_. But the implementation (a salience bonus for `task_status: open`) is closer to a simple "prioritise unfinished work" heuristic than anything requiring a cognitive science citation. The Ovsiankina reference adds legitimacy but the mechanism would be identical without it. This is fine — it's harmless — but the spec shouldn't oversell the cognitive science link.

- **Bartlett's Schema Theory (§4, line 194):** Used to motivate `schema_conflict` flagging. The connection is real but indirect — Bartlett's point was about _human_ reconstructive memory, not LLM summarisation. The analogy holds loosely (LLMs also reconstruct rather than faithfully compress) but it's worth noting the spec is applying a human memory model to LLM behaviour, which may not transfer cleanly.

### 6.3 Missing: Spacing Effect

**Line 2110:** The Open Questions section mentions a deferred "Rehearse phase" based on the spacing effect. This is actually the most directly applicable cognitive science finding for a _memory system_ (Ebbinghaus' forgetting curve is the decay side; spacing is the retention side). Deferring it is reasonable for v1, but it's worth noting that the current system has decay without reinforcement — facts only avoid decay by being referenced, not by active rehearsal. This means rarely-referenced but correct facts (e.g., deployment procedures used quarterly) will decay and be pruned even though they're still valid.

The `expires_at` field partially addresses this for facts with known TTLs. But "never expires, used infrequently" is a gap. Consider adding: "Facts with `encoding_strength: 5` (human-confirmed) are exempt from time decay" as a simple mitigation.

---

## 7. Strategic & Ecosystem Observations

### 7.1 The Biggest Risk: Session Capture

The spec honestly acknowledges this (line 1440): "This is the biggest implementation risk." The adapter table (lines 800–805) shows all major tools as "Needs reverse-engineering" or "Format TBD." If Claude Code and Cursor don't expose session transcripts, umx's Dream pipeline has nothing to extract from.

**Mitigation paths (in order of likelihood):**
1. **AIP events.jsonl** — If the user is already running AIP, sessions are captured by the orchestration layer. This is the golden path.
2. **MCP `write_memory`** — Tools with MCP support can push structured events. Growing adoption.
3. **Terminal recording** — Screen/tmux recording as a fallback. Low fidelity.
4. **Tool vendors open up** — Claude Code's `~/.claude/projects/` is the most accessible target. Reverse-engineering is fragile.

The spec should consider whether umx is viable _without_ session capture — i.e., as a pure fact governance layer on top of manually-entered or MCP-pushed facts. If the answer is yes, that should be explicitly stated as a minimal viable deployment.

### 7.2 Complexity Budget

The spec is at ~132 KB. A v1 implementation touching all described features would be a substantial engineering effort:
- Dream pipeline with 4 phases + lint sub-phase
- 6 source types × 4 verification levels × 5 encoding strengths
- 3 dream modes × 3 composite scores
- PR governance with L1/L2/L3 tiers
- Tombstones, supersession chains, tombstone-vs-supersession distinction
- Greedy packing with always-inject floor
- Optional semantic re-ranking
- Legacy bridge, MCP server, adapters for 4+ tools
- Session redaction with regex + entropy
- Manifest maintenance with uncertainty hotspots and knowledge gaps
- Usage telemetry and calibration

The roadmap (§28) has 10 phases. Realistically, reaching Phase 5 (gitmem backend) is probably 6–12 months of focused work.

**Recommendation:** Consider explicitly defining a "umx-lite" conformance profile that implements Phases 0–2 only: local fact storage + basic Dream (extract + prune) + SQLite search. No governance, no PR pipeline, no semantic re-ranking. This gives early adopters something useful while the full system matures.

### 7.3 Comparison Table — Fairness

**Lines 1782–1791:** The comparison table is inherently biased (umx has ✓ everywhere). This is expected in a spec document, but some entries are debatable:
- **Mem0 "~" for cross-tool:** Mem0's API is tool-agnostic by design. It may deserve a ✓.
- **DiffMem "~" for auto-extract:** DiffMem does extract from diffs. The "~" may be generous to umx's differentiators.
- **CLAUDE.md "✓" for Git-native:** CLAUDE.md is a file in git — it _is_ git-native. Arguably more so than umx, which uses a separate org.

The table is marketing, not spec. Consider either making it more balanced or removing it from the normative spec and putting it in a separate "positioning" document.

### 7.4 `CONVENTIONS.md` — Self-Referential Power

The `CONVENTIONS.md` pattern is one of the spec's best ideas. It's a human-authored configuration file that shapes LLM behaviour without requiring code changes. The L2 reviewer enforcing conventions via prompt creates a feedback loop: the human defines norms, the LLM enforces them, deviations surface for human correction.

This is potentially more impactful than any single feature in the spec. Consider promoting it from "SHOULD contain" to "MUST contain for UMX-Full conformance" — even if it's just a stub generated by `umx init-project`.

---

## 8. Summary of Recommendations (Priority Order)

### Must Fix (Internal Consistency / Correctness)

| # | Issue | Section |
|---|-------|---------|
| 1 | **Fix §21 missing header** — broken ToC anchor | §21 (line 1668) |
| 2 | **Resolve `confidence` ambiguity** — informational or scored? Pick one | §5, §6, §9, §27 |
| 3 | **Fix `external_doc` table formatting** — row is outside the table | §5 (line 299) |
| 4 | **Bridge round-trip corroboration loophole** — add anti-circular rule | §5 (line 226) |
| 5 | **Clarify `applies_to` overlap** when facts have _different_ keys specified | §5 (line 319) |
| 6 | **`expires_at` vs `min_age_days` precedence** | §11 (line 872) |

### Should Fix (Robustness / Implementability)

| # | Issue | Section |
|---|-------|---------|
| 7 | Increase dedup key truncation from 16 to 32 hex chars | §5 (line 243) |
| 8 | Add CONVENTIONS.md max size / budget partition rule | §17 (line 1264) |
| 9 | Define encoding_context schema or explicitly defer | §5 (line 279) |
| 10 | Add truncated-JSONL tolerance for session files | §19 (line 1428) |
| 11 | Add multi-machine `local` mode warning | §18 |
| 12 | Resolve duplicate `semantic_weight` config paths | §6, §27 |
| 13 | Specify `umx doctor` check categories | §25 |
| 14 | Note `git filter-repo` dependency for `umx purge` | §19 |

### Consider (Design Improvements)

| # | Issue | Section |
|---|-------|---------|
| 15 | Exempt S:5 facts from time decay | §6 |
| 16 | Move embeddings from `.umx.json` to SQLite | §20a |
| 17 | Use `len(text) // 3` for token estimation (code-heavy) | §17 |
| 18 | Define "umx-lite" conformance profile (Phases 0–2 only) | §23, §28 |
| 19 | Promote CONVENTIONS.md from SHOULD to MUST for UMX-Full | §8, §23 |
| 20 | Clarify Orient-phase demotion doesn't change `source_type` | §5 |
| 21 | Acknowledge fact-level redaction as defence-in-depth (not primary) | §19 |

---

## Overall Assessment

**Grade: A-**

This is one of the most thoroughly designed agent memory specifications I've seen. The cognitive science grounding is genuine (not hand-wavy), the git-native governance model is novel and practical, and the spec is honest about its biggest risks (session capture, weight tuning). The three-score separation (trust/relevance/retention) is architecturally correct and shows real insight into why "one confidence number" fails.

The main weaknesses are:
1. **Internal consistency erosion** from iterative drafting (especially the `confidence` ambiguity)
2. **Complexity accumulation** — the spec tries to be both a normative standard and an implementation guide, which creates tension at this length
3. **Under-specification of LLM-dependent behaviour** — CONVENTIONS.md enforcement, encoding_context matching, and gap signal emission all depend on LLM reasoning quality that can't be spec'd deterministically

The spec would benefit from a clear split into "normative" (MUST/SHOULD requirements) and "informative" (implementation guidance, cognitive science context, examples) sections, following RFC style. This would reduce the reading burden for implementers while preserving the excellent context.
