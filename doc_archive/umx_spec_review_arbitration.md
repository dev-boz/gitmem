# UMX Specification v0.9 Review Arbitration

**Role:** Lead architect and final arbitrator  
**Date:** 2026-04-10  
**Inputs:** `umx-spec-v0_9.md`, `umx_spec_review_A.md`, `umx_spec_review_B.md`, `umx_spec_review_C.md`, `umx_spec_review_D.md`

## Executive Decision

The four reviews are directionally consistent: the specification is architecturally strong, but it is not yet clean enough to treat as a frozen normative standard.

My ruling is:

- **Keep the core architecture.** The following design choices stand: markdown as canonical storage, inline provenance as the audit floor, separate trust/relevance/retention scores, local-only telemetry, separate memory org, and PR-gated governance in `remote`/`hybrid` mode.
- **Fix the consistency defects before freeze.** The spec has several real contradictions or broken normative edges that should be corrected before calling v0.9 "frozen".
- **Do not redesign around reviewer preference alone.** A few review suggestions would weaken the spec's core thesis. Those are rejected or deferred below.

## P0: Must Fix Before Freeze

| ID | Issue | Raised by | Arbitration | Decision |
|---|---|---|---|---|
| P0-1 | Missing `## 21  Tombstones and Forgetting` heading | A, C, D | Accept | Add the missing section header and restore the TOC anchor chain. |
| P0-2 | Broken `source_type` table rendering (`external_doc` row outside table) | A, C, D | Accept | Keep one contiguous table, then move the `dream_consolidation` boundary note below it. |
| P0-3 | `confidence` is both "informational-only" and weighted in `trust_score` | A, D | Accept | Pick one lane. For v1, `confidence` stays informational-only. Remove it from `trust_score` or set its trust weight to `0.0` everywhere normative. |
| P0-4 | Remote/hybrid Dream pipeline cannot read local `usage.sqlite` | B | Accept | `meta/usage.sqlite` remains local-only. Remote/hybrid pruning must explicitly run in degraded mode without usage telemetry until a future sync artifact is specified. |
| P0-5 | FTS5 schema is incomplete as written | C, D | Accept | Specify one canonical pattern. I recommend external-content FTS5 linked to `memories` with explicit sync triggers. |
| P0-6 | Bridge round-trips can launder corroboration | A, D | Accept | Independence is not just `(tool, session)` diversity. Any fact whose provenance chain includes a UMX bridge export is non-independent for corroboration. |
| P0-7 | Citation `[2]` is wrong in the problem statement | C, D | Accept | Replace the "JPEG compression" citation with an actually relevant source or recast it as an uncited observation. Keep Schacter `[2]` for the memory-taxonomy claim only. |
| P0-8 | `Conflicting text same ID -> conflict entry` treats corruption as a normal conflict | D | Accept | Same `fact_id` with different text is a data-integrity error, not a semantic contradiction. Route to hard error/quarantine, not `conflicts.md`. |
| P0-9 | `CONVENTIONS.md` is both fully injected and summary-injected | D | Accept | Separate the two uses. Orient/L2 read the full file. Runtime injection uses a bounded summary or excerpt under the hot-tier budget. |
| P0-10 | L2 workflow filter omits `type: deletion` and `type: principle` | D | Accept | Add both labels to the workflow logic so the automation matches the label system. |
| P0-11 | Inline metadata grammar defers `-->` escaping even though HTML comments break on it | D | Accept | Define the escaping rule now. Minimal rule: comment payloads must JSON-escape dangerous sequences before embedding, and parsers must reverse the escape. |
| P0-12 | `encoding_context` and `context_match` are scored but not specified | A, B | Accept | Defer scoring for v1. Keep the field reserved/optional, but set `context_match` weight to `0.0` until a minimal schema and matching algorithm are defined. |

## P1: Should Fix Next

| ID | Issue | Raised by | Arbitration | Decision |
|---|---|---|---|---|
| P1-1 | `applies_to` overlap semantics are ambiguous when facts specify different keys | A | Accept | Treat absent keys as `*`, evaluate overlap across the canonical key set, and define contradiction only when at least one shared world satisfies both qualifiers. |
| P1-2 | Orient demotion of `ground_truth_code` facts does not state whether `source_type` changes | A | Accept | Demotion changes `consolidation_status`, not `source_type`. Origin and current reliability are separate. |
| P1-3 | `expires_at` vs `prune.min_age_days` precedence is unspecified | A | Accept | Explicit TTL wins. `expires_at` is a hard expiry and bypasses the incubation floor. |
| P1-4 | Push concurrency is understated by "zero merge conflicts" | B | Accept | Reword: append-only sessions avoid content merges, but push races still require `fetch/rebase/retry` logic in the async push queue. |
| P1-5 | `task_status` has abandon logic but no clear resolution path | B | Accept | Add a minimal resolution rule: a task may become `resolved` via explicit user confirmation, explicit completion evidence in a later session, or stable superseding resolution facts. |
| P1-6 | Duplicate config path for semantic weighting | A | Accept | Keep a single canonical weight path. I recommend `weights.relevance.semantic_similarity`; remove the override in `search.embedding`. |
| P1-7 | `meta/usage.sqlite` naming is inconsistent | B, C | Accept | Standardise on `meta/usage.sqlite` in normative text. |
| P1-8 | Branch naming lists `session/` branches even though remote/hybrid sessions push to `main` | D | Accept | Clarify that `session/` branches are optional fallback/staging, not the normal remote/hybrid session path. |
| P1-9 | `dream.lock` is underspecified and only local | D, A | Accept | Document lock file format and staleness recovery, and explicitly warn that it only guards one clone. Multi-machine coordination remains a later feature. |
| P1-10 | Dedup key concatenation lacks delimiters | D | Accept | Add unambiguous field separators before hashing. Keep 16 hex chars for v1 unless collision evidence appears. |
| P1-11 | Folder -> project auto-promotion feels ungated | D | Accept with scope change | Do not add an L3 human gate. Instead clarify that the promotion still passes through normal Dream review in `remote`/`hybrid` mode. |
| P1-12 | Session `_meta` lacks `ended` or duration | D | Accept | Add `ended` or `duration_seconds`; this is cheap and operationally useful. |
| P1-13 | Compressed session archives need an access story | A | Accept | State that raw-track queries and audit re-derivation decompress on demand or use a dedicated session index. |
| P1-14 | Manual markdown edits need a final safety net | B | Accept | Add a defense-in-depth pre-push scan over tracked markdown artifacts, not just raw sessions. |
| P1-15 | `umx doctor` check set is only implied | A | Accept | Expand the diagnostic categories in the CLI reference and package notes. |
| P1-16 | `dream_consolidation` averaging is underspecified for integer strength | D | Accept | Round down the inherited strength by default to avoid overclaiming confidence. |
| P1-17 | The spec itself lacks a machine-readable version identifier | D | Accept | Add a formal spec version identifier for conformance claims. |

## Accepted, But Not as Reviewers Proposed

### Markdown metadata footprint

Reviewer B's core complaint is valid in spirit: inline metadata is visually heavy. The proposed remedy is not.

**Ruling:** Reject moving core provenance out of markdown.

Reason:

- The spec explicitly chooses markdown as canonical storage.
- The audit floor must survive a fresh clone without derived caches.
- Moving core provenance to a shadow store would weaken one of the document's best properties: inspectability without tooling.

Follow-up:

- Keep core provenance inline.
- Keep richer provenance in `.umx.json`.
- Improve ergonomics through viewer/editor presentation and, if needed later, a compact metadata grammar. Do not move the audit floor out of the canonical file.

### Superseded fact persistence

Reviewer B is right that the parser behaviour needs clarification. The proposed `history.md` split is not necessary.

**Ruling:** Keep a single canonical fact store.

Decision:

- Supersession remains expressed by `supersedes` / `superseded_by`.
- The spec should clarify whether superseded lines remain inline in-topic, move to a bounded "History" subsection in the same file, or are hidden by the viewer.
- Do not create a second normative archive file just to avoid UI clutter.

### Folder -> project promotion governance

Reviewer D is right to notice the asymmetry. The answer is not "add a new mandatory human gate".

**Ruling:** Keep folder -> project cheaper than project -> user.

Reason:

- It is intra-project scope widening, not cross-project or user-global promotion.
- The normal Dream governance path already provides automated review in governed modes.

## Rejected

| Proposal | Source | Arbitration | Reason |
|---|---|---|---|
| Make `CONVENTIONS.md` mandatory for UMX-Full | A | Reject | Graceful adoption matters. Tools must respect it when present, but the repo itself should still bootstrap without it. |
| Exempt all S:5 facts from time decay | A | Reject | Strength is not the same as relevance. Human-confirmed facts can still become stale or cold. |
| Replace inline provenance with a shadow provenance store | B | Reject | Conflicts with canonical markdown and fresh-clone auditability. |
| Treat the conformance rule "derived artifacts MUST NOT be committed" as invalid because it is operational | B | Reject | This is a legitimate normative requirement. Conformance rules often constrain filesystem behaviour. |
| Add a human gate to every folder -> project promotion | D | Reject | Too heavy for same-project scope promotion; normal Dream review is enough. |

## Deferred

| Proposal | Source | Arbitration | Reason |
|---|---|---|---|
| Define a separate `umx-lite` profile | A | Defer | Good packaging/roadmap idea, but not required to resolve the current spec defects. |
| Revisit `20a` numbering as `20.1` or full renumber | D | Defer | Editorial cleanup, not a correctness blocker once `## 21` exists again. |
| Move embeddings from `.umx.json` into SQLite | A | Defer | Worth benchmarking later, but not a spec defect. |
| Rework repo archival strategy for very large long-lived stores | D | Defer | Operational tuning should follow real usage data. |
| Central provider-list update mechanism | D | Defer | The current warning plus graceful degradation is enough for v0.9. |
| Move comparison table to a separate positioning document | A | Defer | Reasonable, but non-normative and not blocking implementation. |

## Architect Guidance for the Next Revision

The next edit pass should not be a broad rewrite. It should be a tight normalization pass:

1. Fix the broken normative edges first.
2. Remove contradictions before adding new concepts.
3. Keep the design principles intact.
4. Push optional sophistication out of the critical path when the spec does not yet define deterministic behavior.

In concrete terms, the most important architectural call is this:

- **UMX v1 should privilege typed provenance and governance over speculative scoring sophistication.**

That means:

- `confidence` stays informational until calibrated.
- `encoding_context` stays reserved until specified.
- bridge-derived evidence never counts as independent corroboration.
- local telemetry stays local unless and until a deliberate sync artifact is designed.

## Final Verdict

**Status:** Architecturally approved, not yet freeze-ready.

If the P0 items are applied, the document is strong enough to serve as the implementation baseline. If they are not applied, implementers will make divergent choices in exactly the areas where the spec most needs determinism: trust scoring, retrieval calibration, corroboration, indexing, and governance.
