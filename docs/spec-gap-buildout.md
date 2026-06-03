# gitmem — Spec Gap Build-Out Plan

**Generated:** 2026-05-29 · **Branch:** `spec-v0_10-unified-update` (7 commits ahead of `main`, in sync with origin) · **Spec audited:** `gitmem-spec-v0_9.md` · **Tests:** 787 passing.

This document is a **hand-off task list** to bring the `umx/` implementation to full parity with the spec, plus the roadmap items worth doing now. Every task was derived by reading a spec section and verifying the corresponding code (file:line evidence inline). The narrow CLI-flag matrix in `docs/spec-parity.md` is complementary; this plan covers **behavior, components, and data-model fidelity** that the flag matrix explicitly does not.

## How to use this doc

- Tasks are `GAP-NN` (parity gaps) and `HYG-NN` (doc/spec hygiene). Reference them in commits/PRs/issues.
- Each task has **Sev / Effort / Bucket / Area**, the **spec ref**, the **current state** (with file:line), **what to build**, and **acceptance criteria**.
- **Bucket A** = code is missing a normative (MUST/SHOULD) behavior the spec describes as shipped → *build it*.
- **Bucket B** = the spec over-claims (describes something as shipped that is really roadmap/optional) → *build it, OR demote the spec text to roadmap*. The README is honest about alpha status; the spec is the optimistic document. Since the goal is to fully build out, the default action is **build**; "cut from spec" is noted where it's a legitimate option.
- **Effort:** `S` ≈ <½ day · `M` ≈ ½–2 days · `L` ≈ multi-day.
- **✅EW** marks an easy win (small + self-contained + clear value). All ✅EW items are collected in [Easy Wins](#easy-wins-do-these-first).

> Scope caveat: this is thorough across all 31 spec sections, but not a line-by-line audit of all ~120 modules. The six underlying area reports contain additional file:line detail if a task needs more.

---

## Status snapshot

| Item | State |
|---|---|
| On latest? | ✅ Yes — `spec-v0_10-unified-update`, ahead of `main`, synced with its remote. |
| Spec file vs branch | ⚠️ Spec still named `gitmem-spec-v0_9.md` while branch is "v0_10" (see HYG-04). |
| Untracked file | `README.local-before-github-merge.md` — decide commit/delete (HYG-05). |
| Core machinery | ✅ Faithful: scope/strength/scoring model, FTS5 + incremental rebuild, semantic re-rank, budget packing + L0/L1/L2 disclosure, session JSONL/`_meta`, tombstone format, backup export/import, signed commits, MCP tools, captures, 5-phase Dream + three-gate triggers. |
| Gaps | 41 build tasks + 6 hygiene tasks below. |

---

## Master index

| ID | Area | Title | Sev | Effort | Bucket | EW |
|---|---|---|---|---|---|:--:|
| GAP-01 | Blob | Content-addressed blob store (`blobs.py` + `umx blob` + doctor) | Critical | M | A/B | |
| GAP-02 | Governance | L2 approval promotes fact to S:4 / `sota-reviewed` | Critical | M | A | |
| GAP-03 | Governance | L2 reviewer context: include source sessions + `manifest.json` | Major | M | A | |
| GAP-04 | Governance | Enforce hallucinated-principle promotion bar | Major | M | A | |
| GAP-05 | Dream | User-level singleton lock `~/.gitmem/state/dream.lock` | Major | S–M | A | |
| GAP-06 | Dream | Route Consolidate dedup/corroboration through `semantic_dedup_key` | Major | M | A | |
| GAP-07 | Dream | Retire/fix divergent `dream/prune.py` | Major | S | A | ✅ |
| GAP-08 | Dream | Apply fragile→stable rule #2 in Prune | Major | S–M | A | |
| GAP-09 | Injection | Tool-adapter context-budget inference | Major | M | A | |
| GAP-10 | Injection | Emit retrieval-fidelity tags | Major | M | A/B | |
| GAP-11 | Injection | MEMORY CHRONICLES layered context blocks | Major | L | B | |
| GAP-12 | Sessions | Binary/media pre-ingest interception + quarantine | Major | M | A | |
| GAP-13 | Tombstones | Honor `suppress_from` `rederive`/`audit` phases | Major | S–M | A | |
| GAP-14 | Sessions | §18 same-`fact_id` merge/quarantine rule | Major | M | A | |
| GAP-15 | Sessions | Async push queue + non-ff retry/backoff | Major | M–L | A/B | |
| GAP-16 | Architecture | Reasoning artifacts (`memory/artifacts/`, `invalidates_when`) | Major | L | B | |
| GAP-17 | Diagnostics | `doctor`: add auth, push-queue, index-staleness checks | Major | M | A | |
| GAP-18 | Viewer | Pipeline-health: L1 rejection rate + escalation rate | Major | M | A | |
| GAP-19 | Adapters | Fix native-adapter `memory_type`/`source_type` (+ tests) | Major | S | A | |
| GAP-20 | Interop | Wire AIP `workspace/` ingest into Dream Orient | Major | M | A/B | |
| GAP-21 | Lint | Tag-drift detection | Minor | S–M | A | |
| GAP-22 | Lint | Error on procedure missing `## Triggers` | Minor | S | A | ✅ |
| GAP-23 | Lint | Skill non-portability + unsupported-directive checks | Minor | S | A | ✅ |
| GAP-24 | Injection | `injection-audit.jsonl` w/ `reason`/`relevance_score`/`dedup` | Minor | M | A/B | |
| GAP-25 | Injection | `min_facts` under-budget warning | Minor | S | A | ✅ |
| GAP-26 | Sessions | Startup safety-sweep should push (remote/hybrid) | Minor | S | A | ✅ |
| GAP-27 | Redaction | Surface high-entropy redactions in `umx status` | Minor | S | A | ✅ |
| GAP-28 | Redaction | Add GCP + Stripe builtin patterns | Minor | S | A | ✅ |
| GAP-29 | Viewer | Hierarchical memory tree + machine scope | Minor | M | A | |
| GAP-30 | Dream | Stage-5: requeue native-only sessions (don't mark gathered) | Minor | S | A | ✅ |
| GAP-31 | Dream | Dedicated gap-fill PR | Minor | S | A | ✅ |
| GAP-32 | Dream | Orient: quarantine self-derived `llm_inference` facts | Minor | M | A | |
| GAP-33 | Dream | `untrusted_source`/`contamination_risk` propagation + L2 block | Minor | M | A | |
| GAP-34 | Strength | `dream_consolidation` source weight = floor of avg | Minor | S | A | ✅ |
| GAP-35 | Strength | Corroboration independence keys on `source_tool` | Minor | S | A | ✅ |
| GAP-36 | Adapters | Native adapters read real stores (Claude JSONL / Copilot / Gemini) | Minor | M | A/B | |
| GAP-37 | Sessions | Diary + Session Handover (§8a) read/append/ingest | Major* | M–L | B | |
| GAP-38 | Interop | `umx export --format memories` (`/memories` projection) | Minor | M | B | ✅ |
| GAP-39 | Interop | `routing/ROUTING.md` index generator | Minor | S | B | ✅ |
| GAP-40 | Interop | `umx import --tool` alias for `--adapter` | Trivial | S | B | ✅ |
| GAP-41 | Governance | Surface schema-lock-in in L2 (also) | Note | S | A | ✅ |
| HYG-01 | Docs | Document `git.*`/`telemetry.*`/etc. in §27; soften `org` | — | S | B | ✅ |
| HYG-02 | Docs | Decide build-vs-cut for `blobs.py` in §25 | — | S | B | ✅ |
| HYG-03 | Docs | Reconcile §26 viewer feature table with shipped reality | — | S | B | ✅ |
| HYG-04 | Docs | Rename spec `v0_9`→`v0_10`; bump `spec_version` | — | S | B | ✅ |
| HYG-05 | Repo | Commit or delete `README.local-before-github-merge.md` | — | S | — | ✅ |
| HYG-06 | Docs | Reconcile §10 native paths + §28 `import --tool` example | — | S | B | ✅ |

\* GAP-37 is a whole spec feature (§8a) currently absent; severity depends on whether you treat diary/handover continuity as core or future.

---

## Suggested milestones

**M0 — Quick wins (≈1–2 days total):** GAP-07, 22, 23, 25, 26, 27, 28, 30, 31, 34, 35, 39, 40, 41 + HYG-01, 04, 05.

**M1 — Governance & the trust loop (the project thesis):** GAP-02, 03, 04, 05, 13, 19, 32, 33.

**M2 — Memory-model correctness:** GAP-06, 08, 21.

**M3 — Safety & robustness:** GAP-12, 14, 15, 17.

**M4 — Injection / retrieval depth:** GAP-09, 10, 24, 11, 16.

**M5 — Interop & UX:** GAP-20, 36, 38, 18, 29, 37, 01.

---

## Detailed tasks

### Blob store

#### GAP-01 — Content-addressed blob store
- **Sev:** Critical · **Effort:** M · **Bucket:** A/B · **Area:** Blob
- **Spec:** §25 ~L2780 lists `umx/blobs.py` ("content-addressed blob store: `umx blob store/get/list/purge`"); §25 ~L2782 lists a `doctor` "stale blobs" check; §19 ~L2203 routes small text-safe screenshots to `local/blobs/`.
- **Now:** Missing — `umx/blobs.py` does not exist; no `@main.group("blob")` in `cli.py` (only git-plumbing `git_blob_sha`); README makes no blob claim, so only the spec asserts this.
- **Build:** Create `umx/blobs.py` with `store(path|bytes)→sha`, `get(sha)`, `list()`, `purge(unreferenced)` against `local/blobs/<sha>`; add a `umx blob` Click group; add a "stale blobs" check to `doctor.run_doctor` (orphans not referenced by any fact/session). Coordinate with GAP-12 (binary interception writes here).
- **Done when:** `umx blob store/get/list/purge` round-trips a file by content hash; `doctor` reports unreferenced blobs; tests cover store/get/purge + doctor staleness.
- **Deps:** Pairs with GAP-12. Alternatively HYG-02 (cut from spec if you decide blobs are out of scope).

### Governance & the trust loop

#### GAP-02 — L2 approval promotes fact to S:4 / `sota-reviewed`
- **Sev:** Critical · **Effort:** M · **Bucket:** A · **Area:** Governance
- **Spec:** §5 ~L400 & §6 ~L586 — L2 (SotA) approval promotes to S:4 with `verification: sota-reviewed` (a +1.0 verification bonus); L3 confirmation elevates to S:5.
- **Now:** Missing — `Verification.SOTA_REVIEWED` is **only** referenced in `models.py:57` (enum), `strength.py:31` (weight), `memory.py:33` (short-code); it is never *assigned*. The production review path (`dream/l2_review.py:26` `run_l2_review_with_providers`) yields approve/reject/escalate, and the pipeline only mutates strength **downward** (`dream/pipeline.py:324`). The governance→strength feedback loop is open.
- **Build:** In the L2 review-apply step, on an `approve` verdict set `verification=SOTA_REVIEWED` and raise `encoding_strength` to ≥4; on L3 human confirmation, raise to S:5 (the `umx confirm` path already exists at `fact_actions.py:783` — verify it sets S:5).
- **Done when:** A fact carried through an `approve` L2 verdict ends with `encoding_strength>=4` and `verification=="sota-reviewed"`; a test asserts the promotion and the resulting verification bonus in scoring.
- **Deps:** GAP-03 (reviewer needs proper context to approve responsibly).

#### GAP-03 — L2 reviewer context: source sessions + `manifest.json`
- **Sev:** Major · **Effort:** M · **Bucket:** A · **Area:** Governance
- **Spec:** §12 ~L1610–1613 — the L2 prompt MUST include the source session(s), `CONVENTIONS.md`, the existing facts being superseded/contradicted, AND `meta/manifest.json`. §12 ~L1564: L2 "reviews diffs against source sessions."
- **Now:** Partial — `dream/l2_review.py:206` `build_l2_review_context` includes only conventions, `existing_facts`, `proposed_facts`. No source-session text, no manifest. L2 therefore cannot verify extraction fidelity against source.
- **Build:** Load the referenced raw sessions and `meta/manifest.json`; add both to the L2 context payload (bounded/truncated as needed).
- **Done when:** The L2 context dict contains source-session excerpts and manifest topics/hotspots; an eval case shows the reviewer rejecting a fact unsupported by its cited session.

#### GAP-04 — Enforce hallucinated-principle promotion bar
- **Sev:** Major · **Effort:** M · **Bucket:** A · **Area:** Governance
- **Spec:** §22 ~L2617 — principles require ≥3 sessions + S:≥4 sustained for 14 days + L3 gate.
- **Now:** Partial — the L3 escalation label fires for any PR touching `principles/` (`governance.py:771,794`), but no code checks the quantitative evidence bar before a fact lands in `principles/topics/`.
- **Build:** In the promotion path (`umx promote --to principle` and any Dream principle-promotion), gate on session-count ≥3, strength ≥4, and age ≥14 days in addition to the L3 label.
- **Done when:** A fact failing any threshold is rejected/escalated rather than promoted; tests cover each threshold boundary.

#### GAP-05 — User-level singleton Dream lock
- **Sev:** Major · **Effort:** S–M · **Bucket:** A · **Area:** Dream
- **Spec:** §11 ~L1453 — a Dream run MUST acquire a singleton lock at `~/.gitmem/state/dream.lock` before reading inputs (prevents two Dream processes racing across repos/shells on one machine).
- **Now:** Missing — `dream/gates.py:44` `DreamLock` manages only the repo-local `meta/dream.lock`; no `~/.gitmem/state` lock (grep: no matches). Repo-local lock gives per-clone protection only.
- **Build:** Add a user-level lock (acquire / heartbeat / stale-reclaim) at `~/.gitmem/state/dream.lock`, acquired in `run()` before orient/gather, released in `finally`.
- **Done when:** A second concurrent `umx dream` on a different repo of the same machine refuses to run while the first holds the lock; stale locks (dead PID/expired heartbeat) are reclaimed; test simulates contention.

#### GAP-41 — Surface schema-lock-in in L2 (also)
- **Sev:** Note · **Effort:** S · **Bucket:** A · **Area:** Governance · ✅EW
- **Spec:** §22 ~L2602 — L2 reviewer SHOULD flag cycles where >80% of new candidates fall into existing convention buckets.
- **Now:** Present in Lint (`dream/pipeline.py:275` `schema_lock_in_findings`, surfaced via lint PR), but not in the L2 reviewer context.
- **Build:** Pass the schema-lock-in ratio into the L2 context so the reviewer can also weigh it. (Optional; current Lint placement already reaches a human.)
- **Done when:** L2 context includes the lock-in ratio; or close as won't-do with a note that Lint covers the SHOULD.

### Dream pipeline & memory model

#### GAP-06 — Use `semantic_dedup_key` in Consolidate
- **Sev:** Major · **Effort:** M · **Bucket:** A · **Area:** Dream
- **Spec:** §5 ~L413 — dedup key = `SHA-256(lowercase(text + "\x00" + scope + "\x00" + topic))[:16]`; same-key facts are merge/corroboration candidates.
- **Now:** Stubbed — `identity.py:28` defines `semantic_dedup_key` with **zero call sites**; Consolidate matches on `existing.topic == candidate.topic and existing.text.lower() == candidate.text.lower()` (`dream/pipeline.py:243`), ignoring `scope`. Also the impl adds a `.strip()` not in the spec formula.
- **Build:** Route Consolidate merge/corroboration candidate detection through `semantic_dedup_key(text, scope, topic)`; remove the `.strip()` (or amend the spec); ensure corroboration/conflict paths consider scope.
- **Done when:** Two facts identical in text+scope+topic are detected as merge candidates while a same-text/different-scope pair is not; tests cover the key and the new candidate detection.

#### GAP-07 — Retire/fix divergent `dream/prune.py` ✅EW
- **Sev:** Major · **Effort:** S · **Bucket:** A · **Area:** Dream
- **Spec:** §5 ~L402 prune threshold default 2 on `retention_score`; §6 retention formula; §9 ~L978 MEMORY.md scored by `relevance_score`.
- **Now:** Divergent dead code — `dream/prune.py:20` `should_prune` ignores `retention_score`/recency/usage and hard-codes `encoding_strength<2`→prune, `==2 & llm_inference`→"archive" (a state `models.py` doesn't define); `dream/prune.py:95` sorts MEMORY.md by `(encoding_strength, created)`. The live pipeline correctly uses `strength.should_prune` (`pipeline.py:339`) and `memory.write_memory_md` (relevance-scored). `dream/prune.py` appears to be an unused parallel implementation contradicting the spec.
- **Build:** Delete `dream/prune.py`, or rewrite it to delegate to `strength.should_prune` / `memory.write_memory_md`. Confirm no imports rely on it first.
- **Done when:** No module contradicts the spec retention model; grep shows no live import of the divergent functions; tests green.

#### GAP-08 — Apply fragile→stable rule #2 in Prune
- **Sev:** Major · **Effort:** S–M · **Bucket:** A · **Area:** Dream
- **Spec:** §14 ~L1772–1778 — three fragile→stable rules; "Prune applies rules 1 and 2 automatically every cycle." Rule 2 = independent corroboration (different tool, or same tool ≥24h later).
- **Now:** Partial — Rule 1 (survive-one-cycle) via `stabilize_facts` (`dream/consolidation.py:8`, `pipeline.py:343`); Rule 3 (`umx confirm`) via `fact_actions.py:783`. Rule 2 only stabilizes when corroboration arrives in the same Consolidate pass (`pipeline.py:246`); an already-stored fragile fact independently corroborated in a prior cycle isn't re-checked distinctly.
- **Build:** In Prune, additionally stabilize fragile facts that already record independent corroboration (`corroborated_by_*`), independent of the survive-one-cycle rule.
- **Done when:** A fragile fact with recorded independent corroboration is promoted to stable in the next Prune even if it didn't gain new corroboration that cycle; test covers it.

#### GAP-21 — Tag-drift detection in Lint
- **Sev:** Minor · **Effort:** S–M · **Bucket:** A · **Area:** Lint
- **Spec:** §11 ~L1495 & §12 ~L1598 — Lint flags tag drift (`database`/`db`/`postgres`) and proposes canonicalisation (`type: lint`).
- **Now:** Missing — `dream/lint.py:108` `generate_lint_findings` covers orphans, stale refs, convention violations, contradictions, reverify, orphaned scope — but no tag-drift check. `Fact.tags` exists (`models.py:239`).
- **Build:** Add a tag-clustering/normalisation finding (e.g., near-duplicate tags by edit distance / known synonym map) to `generate_lint_findings`.
- **Done when:** A corpus with `db`/`database` produces a lint finding proposing one canonical tag; test covers it.

#### GAP-22 — Lint error on procedure missing `## Triggers` ✅EW
- **Sev:** Minor · **Effort:** S · **Bucket:** A · **Area:** Lint
- **Spec:** §9a ~L1101 — `## Triggers` is required; a procedure without triggers is a lint error.
- **Now:** Missing — `procedures.py:91` `read_procedure_file` parses triggers but never flags absence; no procedure check in `dream/lint.py`.
- **Build:** Emit a lint finding when a parsed procedure has zero triggers.
- **Done when:** A trigger-less procedure file yields a lint finding; test covers it.

#### GAP-23 — Lint skill non-portability + unsupported directives ✅EW
- **Sev:** Minor · **Effort:** S · **Bucket:** A · **Area:** Lint
- **Spec:** §9c ~L1184–1185, ~L1291 — `query:`/`link:` SHOULD be reported as unsupported; skills referencing paths outside skills-adjacent namespaces are flagged non-portable by `umx lint`.
- **Now:** Partial — `skills.py:367,371` `resolve_skill` collects `unsupported_directives`/`blocked_paths` at runtime, but `dream/lint.py` does no skill linting; the runtime collection isn't surfaced through lint.
- **Build:** Add skill checks to `generate_lint_findings` for unsupported directives and non-portable `load:` targets, reusing the `resolve_skill` collectors.
- **Done when:** A skill with a `query:` directive or out-of-namespace `load:` produces lint findings; test covers it.

#### GAP-30 — Stage-5: requeue native-only sessions ✅EW
- **Sev:** Minor · **Effort:** S · **Bucket:** A · **Area:** Dream
- **Spec:** §11 ~L1543 — graceful degradation Stage 5: native-only ran → mark `partial` in MEMORY.md AND queue a full dream next trigger.
- **Now:** Partial — `dream_status: partial` is written (`memory.py:674`), but `pipeline.py:1434` calls `mark_sessions_gathered` for ALL gathered sessions including native-only ones, and `extract.py:340` skips already-gathered next run (`skip_gathered=True`), so native-only sessions are never LLM-re-extracted.
- **Build:** Don't mark native-only sessions as gathered (or track them separately) so the next provider-available run re-extracts them.
- **Done when:** After a native-only run, the next full Dream re-processes those sessions; test covers the requeue.

#### GAP-31 — Dedicated gap-fill PR ✅EW
- **Sev:** Minor · **Effort:** S · **Bucket:** A · **Area:** Dream
- **Spec:** §11 ~L1446 — gap facts batched into a dedicated PR: `[dream/l1] Gap-fill proposals from session <id>`.
- **Now:** Divergent — gap facts are created correctly (S:1, `llm_inference`, fragile — `dream/extract.py:386`) with `type: gap-fill` label (`governance.py:319`) but merged into the single L1 snapshot PR.
- **Build:** Emit a separate gap-fill PR when gap candidates exist (or document the batching as an intentional simplification).
- **Done when:** A run with gap candidates opens a distinct gap-fill PR; test covers the title/labeling.

#### GAP-32 — Orient: quarantine self-derived `llm_inference` facts
- **Sev:** Minor · **Effort:** M · **Bucket:** A · **Area:** Dream
- **Spec:** §22 ~L2632 — Orient traces provenance chains; `llm_inference` facts with no session anchor that originate from a previous Dream PR are quarantined (anti self-modifying-memory loop).
- **Now:** Partial — `pipeline.py:141` `orient()` handles only code-anchor staleness; a per-hop confidence decay helper exists (`dream/decay.py:23`) but is a different mechanism; no quarantine of self-derived facts.
- **Build:** In Orient, detect `llm_inference` facts whose provenance traces to a prior Dream PR (not a raw session) and quarantine them.
- **Done when:** A fact whose only provenance is a Dream-authored commit is quarantined in Orient; test covers it.

#### GAP-33 — `untrusted_source`/`contamination_risk` propagation + L2 block
- **Sev:** Minor · **Effort:** M · **Bucket:** A · **Area:** Dream
- **Spec:** §22 ~L2631 — `untrusted_source` propagates through extraction; handoff content carries `contamination_risk`; L2 blocks high-strength promotion of untrusted-provenance facts.
- **Now:** Partial — the primary defense (`llm_inference` needs corroboration to reach S:3) exists, but `untrusted_source`/`contamination_risk` appear nowhere (grep: no matches).
- **Build:** Add the two tags on handoff/external-derived facts and have L2 block high-strength promotion when present.
- **Done when:** A fact tagged `untrusted_source` cannot be promoted past the configured strength ceiling via L2; test covers it.

### Injection, context budget & retrieval

#### GAP-09 — Tool-adapter context-budget inference
- **Sev:** Major · **Effort:** M · **Bucket:** A · **Area:** Injection
- **Spec:** §17 ~L1996 — if `--max-tokens` is unset, umx infers the budget from the tool adapter's known limit.
- **Now:** Missing — `cli.py:662` hardcodes `--max-tokens` default `4000` regardless of `--tool`; `InjectConfig` (`config.py:99–110`) has no token-limit field; adapters have no limit attribute. Tool name flows only into matching, never budget.
- **Build:** Add a per-tool context-limit map (or adapter attribute); have `inject_for_tool` fall back to it when `max_tokens` is unset, with a sane default.
- **Done when:** `umx inject --tool X` (no `--max-tokens`) uses X's limit; test asserts per-tool budgets.

#### GAP-10 — Emit retrieval-fidelity tags
- **Sev:** Major · **Effort:** M · **Bucket:** A/B · **Area:** Injection
- **Spec:** §20 ~L2282–2303 — each injected block SHOULD carry a `retrieval_fidelity` tag (`exact|lexical|semantic|fallback|expired`); IMX routing SHOULD gate on `exact`/`lexical`.
- **Now:** Missing — no matches anywhere; `_render_fact` (`inject.py:195–211`) emits only `[id S verification src]`; block headers carry no fidelity comment, though the pipeline knows which retrieval path produced each fact.
- **Build:** Emit `<!-- retrieval_fidelity: <level> source: <path> -->` on each injected block, deriving the level from FTS vs semantic re-rank vs recency fallback.
- **Done when:** Injected output carries fidelity tags reflecting the actual retrieval path; test asserts levels for exact/lexical/semantic/fallback cases.

#### GAP-11 — MEMORY CHRONICLES layered context blocks
- **Sev:** Major · **Effort:** L · **Bucket:** B · **Area:** Injection
- **Spec:** §16 ~L1899–1918 — Dream SHOULD emit `context/layers/{task_class}-{date}/{numeric,temporal,narrative,digest}.md`; the digest is always injected, others optional per budget; generated once per dream cycle.
- **Now:** Missing — no matches for `context/layers`, `numeric.md`, `narrative.md`, `digest.md`; no dream sub-phase produces layers; injection has no layer-selection logic.
- **Build:** Add a Dream layer-generation sub-phase writing the four files per task class; add a budget-aware layer selector in injection (digest default, others as budget allows).
- **Done when:** A dream cycle produces the four layer files; injection includes the digest and optionally upgrades to richer layers under budget; tests cover generation + selection.

#### GAP-24 — `injection-audit.jsonl` with `reason`/`relevance_score`/`dedup`
- **Sev:** Minor · **Effort:** M · **Bucket:** A/B · **Area:** Injection
- **Spec:** §16 ~L1919–1938 — append-only `local/injection-audit.jsonl` per-event with `reason` (e.g. `scope:…,keyword:…`), `relevance_score`, and `dedup: already_injected_this_session`.
- **Now:** Divergent — no `injection-audit` artifact; injection events go to SQLite `usage_events` in `meta/usage.sqlite` (`search.py:284–300`, `_record_injection_event` 894–965) capturing `injection_point`/`disclosure_level`/`token_count`/`used_in_output` but not `reason`/`relevance_score`; dedup is enforced via suppression, not a logged field.
- **Build:** Either emit the spec JSONL artifact, or add `reason`/`relevance_score` columns + a `dedup` marker to the SQLite events and document the substitution in §16.
- **Done when:** Per-injection `reason` + `relevance_score` are recorded and a dedup signal is logged; test asserts the fields.

#### GAP-25 — `min_facts` under-budget warning ✅EW
- **Sev:** Minor · **Effort:** S · **Bucket:** A · **Area:** Injection
- **Spec:** §17 ~L2023 — if packing yields fewer than `inject.min_facts` (default 3), SHOULD warn the budget may be too small.
- **Now:** Stubbed — `budget.py:93–95` checks `len(selected) < cfg.inject.min_facts` but both branches return the identical `BudgetDecision`; no warning channel.
- **Build:** Surface a warning (return flag / log) when selected count < `min_facts`.
- **Done when:** A tiny budget yields a visible "budget may be too small" warning; test covers it.

### Sessions, git strategy & safety

#### GAP-12 — Binary/media pre-ingest interception + quarantine
- **Sev:** Major · **Effort:** M · **Bucket:** A · **Area:** Sessions
- **Spec:** §19 ~L2203 — recognized binary types (`.png/.jpg/.mp4/.wav`, magic-byte detected) MUST be intercepted before reaching `sessions/`; large/opaque binaries MUST be quarantined; small text-safe screenshots MAY go to `local/blobs/` (default 100KB cap).
- **Now:** Missing — no magic-byte / extension / size-cap sniffing in `sessions.py`, `collect.py`, or `session_runtime.py`; `write_session` runs text redaction only; binary payloads pass straight through.
- **Build:** Add a magic-byte + size-cap pre-ingest check in the write/collect path that routes recognized binaries to `local/blobs/` (GAP-01) or `local/quarantine/`.
- **Done when:** A PNG/large binary in a session payload is intercepted (blob or quarantine), never written to `sessions/`; tests cover interception + size cap.
- **Deps:** GAP-01 (blob destination).

#### GAP-13 — Honor tombstone `suppress_from` `rederive`/`audit`
- **Sev:** Major · **Effort:** S–M · **Bucket:** A · **Area:** Tombstones
- **Spec:** §21 ~L2567 — tombstones checked during Gather, re-derivation, AND audit (`suppress_from:["gather","rederive","audit"]`).
- **Now:** Partial — `is_suppressed(...phase="gather")` only; all call sites pass/default `"gather"` (`inject.py:164,190`; `dream/pipeline.py:237,334`); `audit.py` never calls `is_suppressed`. A fact tombstoned only from `audit`/`rederive` is never suppressed.
- **Build:** Pass the active phase from the audit/re-derive paths to `is_suppressed`, or collapse `suppress_from` to a single flag and update §21.
- **Done when:** A fact tombstoned with `suppress_from:["audit"]` is suppressed during `umx audit`; test covers each phase.

#### GAP-14 — §18 same-`fact_id` merge/quarantine rule
- **Sev:** Major · **Effort:** M · **Bucket:** A · **Area:** Sessions
- **Spec:** §18 ~L2080–2085 — identical `fact_id` → merge metadata (take higher trust); conflicting text with same id → data-integrity error → quarantine; never silently overwrite a higher-strength fact.
- **Now:** Missing — `merge.py` only resolves semantic conflicts between *different* fact_ids via supersession; `collect.py` has no fact_id-collision logic; `fact_actions.py:976` "umx: merge conflicts" is the commit for the pair-resolver, not this rule.
- **Build:** Add an ingest/merge guard: same id + identical text → merge metadata (max trust); same id + divergent text → quarantine as data-integrity error; never overwrite higher strength.
- **Done when:** Two same-id facts with divergent text are quarantined and a metadata-merge occurs for identical text; tests cover both.

#### GAP-15 — Async push queue + non-ff retry/backoff
- **Sev:** Major · **Effort:** M–L · **Bucket:** A/B · **Area:** Sessions
- **Spec:** §18 ~L2066, L2074 — write path ends in a push queue (async, retried on failure); remote mode retries non-fast-forward via fetch/rebase/backoff.
- **Now:** Missing — `git_ops.py:550` `git_push` is a single synchronous push; `cli.py` sync does fetch→pull --rebase→push once then raises on failure. No queue/retry/backoff for git push (retry exists only for `gh` API in `github_ops.py`). Mitigated: local commit is durable, next sync re-pushes.
- **Build:** Wrap push in retry-with-backoff that re-runs fetch/rebase on non-ff, or add a persistent local push queue drained on next command.
- **Done when:** A simulated non-ff push retries (rebase + backoff) and succeeds without data loss; test covers the retry path.

#### GAP-26 — Startup safety-sweep should push (remote/hybrid) ✅EW
- **Sev:** Minor · **Effort:** S · **Bucket:** A · **Area:** Sessions
- **Spec:** §19 ~L2205 — uncommitted sessions from crashed runs MUST be committed AND pushed before proceeding.
- **Now:** Partial — `git_ops.py:834` `safety_sweep` only commits; `hooks/session_start.py:33` runs it before the pull and never pushes the swept commit.
- **Build:** After `safety_sweep` commits in remote/hybrid mode, push (or enqueue via GAP-15) the swept commits before continuing.
- **Done when:** A swept commit is pushed/enqueued before the session proceeds; test covers it.

#### GAP-37 — Diary + Session Handover (§8a)
- **Sev:** Major* · **Effort:** M–L · **Bucket:** B · **Area:** Sessions
- **Spec:** §8a ~L860–894 — `local/diary.md` (append-only observation log) and `local/handover.md` + `local/handovers/YYYY-MM-DD.md` (structured handover notes); Dream ingest MAY extract facts from handovers at S:3.
- **Now:** Missing — no `diary`/`handover` references anywhere; no read/write/ingest support. A whole spec feature is absent.
- **Build:** Implement diary/handover append+read helpers, CLI surface, and optional Gather ingestion of handovers at S:3; or formally mark §8a as future.
- **Done when:** `umx` can append/read diary + handover entries and (optionally) Dream ingests handovers at S:3; tests cover read/append/ingest.

### Redaction & privacy

#### GAP-27 — Surface high-entropy redactions in `umx status` ✅EW
- **Sev:** Minor · **Effort:** S · **Bucket:** A · **Area:** Redaction
- **Spec:** §19 ~L2185 — entropy-flagged strings tagged `[REDACTED:high-entropy]` are surfaced in `umx status` for human review without a pattern match.
- **Now:** Missing — `redaction.py:159` masks high-entropy hits in place; `status.py` surfaces quarantine counts only; `docs/threat-model.md:67` itself notes detections are masked in place, not routed to review.
- **Build:** Record high-entropy/redaction-hit counts at write time (session meta or a local index) and surface them in `umx status`.
- **Done when:** `umx status` reports a high-entropy-redaction review count; test covers it.

#### GAP-28 — Add GCP + Stripe builtin redaction patterns ✅EW
- **Sev:** Minor · **Effort:** S · **Bucket:** A · **Area:** Redaction
- **Spec:** §19 ~L2158 — API keys (AWS, GCP, Anthropic, OpenAI, Stripe, etc.).
- **Now:** Partial — `redaction.py:13–27` has AWS (`AKIA…`), OpenAI (`sk-…`), Anthropic (`sk-ant-…`), bearer, JWT, private-key, connection-string; no GCP (`AIza…`) or Stripe (`sk_live_…`).
- **Build:** Add `gcp-key` `AIza[0-9A-Za-z_-]{35}` and `stripe-key` `(sk|rk)_(live|test)_[0-9A-Za-z]{24,}` to `BUILTIN_PATTERNS`.
- **Done when:** Synthetic GCP/Stripe keys are redacted; fixtures added to the secret corpus.

### Diagnostics, viewer & architecture

#### GAP-17 — `doctor`: add auth, push-queue, index-staleness checks
- **Sev:** Major · **Effort:** M · **Bucket:** A · **Area:** Diagnostics
- **Spec:** §25 ~L2782 — `doctor` covers auth, push queue, locks, schema, orphans, quarantine, index staleness, hot-tier pressure, embedding availability, stale blobs.
- **Now:** Partial — `doctor.py:58` `run_doctor` covers schema, locks, orphans, quarantine, embeddings, hot-tier, conventions, git-signing. Missing: `auth` (`gh auth`), `push_queue`, `index staleness`. (Stale-blobs follows GAP-01.)
- **Build:** Add auth (gh/remote reachability), push-queue depth (GAP-15), and FTS index-staleness (compare `_meta.file_hashes` vs on-disk) checks.
- **Done when:** `umx doctor` reports all three; tests cover a stale index and a missing-auth case.

#### GAP-18 — Viewer pipeline-health: rejection + escalation rates
- **Sev:** Major · **Effort:** M · **Bucket:** A · **Area:** Viewer
- **Spec:** §26 ~L2865 — Pipeline health = L1 rejection rate, escalation rate, stale PR count.
- **Now:** Divergent — `governance_health.py:229` returns `reviewer_queue_depth`, `human_review_queue_depth`, `stale_branch_count`, `label_drift_count`; no rejection/escalation rate (only a TUI "not wired yet" string at `tui/app.py:358`).
- **Build:** Compute L1 rejection rate and escalation rate (from PR/label history) and surface them in the viewer + governance health; either add stale-PR count or relabel to stale-branch.
- **Done when:** Viewer shows rejection + escalation rates; tests cover the computations.
- **Deps:** Coordinate with HYG-03.

#### GAP-29 — Hierarchical memory tree + machine scope
- **Sev:** Minor · **Effort:** M · **Bucket:** A · **Area:** Viewer
- **Spec:** §26 ~L2850 — Memory tree shows the full hierarchy user → machine → project → folder → file.
- **Now:** Partial — `viewer/server.py` renders project+user repos as a flat "Fact Inventory" with a file-path column (`_display_path`, line 328); no tree widget, no machine scope.
- **Build:** Add a nested scope-tree view including the machine level.
- **Done when:** The viewer renders a collapsible user→machine→project→folder→file tree; covered by a viewer test.

#### GAP-16 — Reasoning artifacts (`memory/artifacts/`, `invalidates_when`)
- **Sev:** Major · **Effort:** L · **Bucket:** B · **Area:** Architecture
- **Spec:** §3b ~L298–322 — reasoning artifacts stored in `memory/artifacts/`, indexed in SQLite, injected on `conclusion`/`evidence` match, with `invalidates_when` conditions checked by Dream during Orient.
- **Now:** Missing — no matches for `invalidates_when`, `reasoning_artifact`, `memory/artifacts`; README dir list omits `artifacts/`; no viewer panel.
- **Build:** Implement artifact parse/index/inject + Orient invalidation, or mark §3b reasoning artifacts as roadmap.
- **Done when:** Artifacts are stored, indexed, injected on match, and invalidated by Dream when `invalidates_when` fires; tests cover the lifecycle. (Or §3b is demoted to roadmap with a note — see HYG.)

### Adapters & interop

#### GAP-19 — Fix native-adapter `memory_type`/`source_type`
- **Sev:** Major · **Effort:** S · **Bucket:** A · **Area:** Adapters
- **Spec:** §10 ~L1394 — adapters MUST normalise native memory to `memory_type: explicit_semantic`, `verification: self-reported`, `encoding_strength: 3`.
- **Now:** Divergent — `adapters/generic.py:41` sets `MemoryType.IMPLICIT`, `:43` `SourceType.TOOL_OUTPUT` (only strength=3 + self-reported match); the wrong values are locked in by `tests/test_adapters_gitignore.py:37,39,80`. (`bridge.py:148` already uses `EXPLICIT_SEMANTIC`, so the enum is used correctly elsewhere.)
- **Build:** Change adapter `_make_fact` to `memory_type=MemoryType.EXPLICIT_SEMANTIC`; update the test expectations; decide whether `source_type` stays `TOOL_OUTPUT` or aligns to spec.
- **Done when:** Adapter output is `explicit_semantic` + `self-reported` + S:3; updated tests pass.

#### GAP-20 — Wire AIP `workspace/` ingest into Dream Orient
- **Sev:** Major · **Effort:** M · **Bucket:** A/B · **Area:** Interop
- **Spec:** §30 ~L3071–3074 — Dream Orient reads `workspace/dream-candidates/`; `workspace/transcripts/{session_id}.jsonl` is a normalised extraction input; `workspace/tasks/{task_id}/audit.jsonl` and `workspace/status/HEARTBEAT-{agent}.md` are Orient ingest candidates.
- **Now:** Partial — only `workspace/events.jsonl` is consumed, and only as a manual `umx collect` stdin fallback (`cli.py:825`); no matches for `dream-candidates`, `workspace/transcripts`, `HEARTBEAT`, `workspace/tasks`.
- **Build:** Wire Dream Orient to read `workspace/dream-candidates/` and `workspace/transcripts/` as session sources (and optionally tasks/heartbeat), or demote these §30 lines to roadmap.
- **Done when:** A populated `workspace/dream-candidates/` is ingested by `umx dream`; test covers ingestion.

#### GAP-36 — Native adapters read real stores
- **Sev:** Minor · **Effort:** M · **Bucket:** A/B · **Area:** Adapters
- **Spec:** §10 ~L1364, L1389–1392 — Claude Code native memory at `~/.claude/projects/<path>/` (JSONL store); Copilot `~/.config/copilot/`; Gemini `~/.gemini/` (Format TBD).
- **Now:** Partial/divergent — `adapters/claude_code.py:24–40` globs only `CLAUDE.md` (not the JSONL transcripts; those are handled by `claude_code_capture.py`); `adapters/copilot.py:20` reads `.github/copilot-instructions.md` (capture targets `~/.copilot/…`); no `GeminiAdapter` in `adapters/__init__.py:8–12`.
- **Build:** Parse the actual native stores in the adapters (or document in §10 that transcript ingest is delegated to capture modules); add `GeminiAdapter` once the `~/.gemini/` format is known. Reconcile the `~/.config/copilot/` vs `~/.copilot/` path.
- **Done when:** Adapters cover the native stores §10 lists (or §10 is reconciled to the capture-module split); tests/fixtures cover any new parsing.
- **Deps:** Coordinate with HYG-06.

#### GAP-38 — `umx export --format memories` (`/memories` projection) ✅EW
- **Sev:** Minor · **Effort:** M · **Bucket:** B · **Area:** Interop
- **Spec:** §30b ~L3138–3144 — umx MAY generate `local/memories/` via `umx export --format memories --output local/memories/`; writes treated as S:5 ingest candidates.
- **Now:** Missing — `umx export` (`cli.py:2383`) only calls `export_full`; no `--format`/`--output`; `backup.py` has no `memories` format.
- **Build:** Add `--format memories --output <dir>` to `export` (project facts → `/memories`-style files) plus an optional write-back ingest path at S:5.
- **Done when:** `umx export --format memories` produces a `local/memories/` tree; round-trip ingest test passes. (MAY-level; optional but high interop value.)

#### GAP-39 — `routing/ROUTING.md` index generator ✅EW
- **Sev:** Minor · **Effort:** S · **Bucket:** B · **Area:** Interop
- **Spec:** §30a ~L3108 — `routing/` SHOULD contain a `routing/ROUTING.md` index analogous to `meta/MEMORY.md`.
- **Now:** Missing — route card read/write/promote/L2-validate is implemented (`routing.py`), but no `ROUTING.md` generator (no matches).
- **Build:** Mirror `meta/MEMORY.md` generation to produce `routing/ROUTING.md` listing active route cards by task class.
- **Done when:** A repo with route cards gets a generated `ROUTING.md`; test covers it.

#### GAP-40 — `umx import --tool` alias for `--adapter` ✅EW
- **Sev:** Trivial · **Effort:** S · **Bucket:** B · **Area:** Interop
- **Spec:** §28 Phase 3 ~L3037 — bulk import example `umx import --tool claude-code`.
- **Now:** Divergent (cosmetic) — `umx import` (`cli.py:2397`) exposes `--adapter`, not `--tool`.
- **Build:** Add `--tool` as a hidden alias of `--adapter` (or update the spec example — HYG-06).
- **Done when:** `umx import --tool claude-code` works identically to `--adapter`.

### Strength / scoring details

#### GAP-34 — `dream_consolidation` source weight = floor of avg ✅EW
- **Sev:** Minor · **Effort:** S · **Bucket:** A · **Area:** Strength
- **Spec:** §5 ~L466 & §6 ~L575 — `dream_consolidation` inherits "floor of avg source strength".
- **Now:** Divergent — `strength.py:68–71` `source_type_weight` computes a plain mean of `corroborating_source_weights` (no floor) and falls back to `0.0` with no inputs; no extraction site passes those weights, so consolidation facts get weight 0.0.
- **Build:** Apply a floor (e.g., `max(min_input, avg)`) and ensure consolidation extraction supplies the input weights (or amend the spec wording).
- **Done when:** A consolidation fact's source weight reflects the floor-of-avg of its inputs; test covers it.

#### GAP-35 — Corroboration independence keys on `source_tool` ✅EW
- **Sev:** Minor · **Effort:** S · **Bucket:** A · **Area:** Strength
- **Spec:** §5 ~L396 — independent iff different `source_session` AND different `source_tool` (or same tool ≥24h apart).
- **Now:** Divergent (low impact) — `strength.py:143–147` `independent_corroboration` early-outs on same `source_session` AND same `source_type` (wrong field; net behavior partly salvaged by a later different-session clause).
- **Build:** Compare `source_tool` (drop the `source_type` comparison) for spec fidelity.
- **Done when:** Independence is decided by session+tool per §5; test covers same-tool-≥24h and different-tool cases.

---

## Easy wins (do these first)

Small, self-contained, clear value — good warm-up PRs:

`GAP-07` retire dead `prune.py` · `GAP-22` procedure-triggers lint · `GAP-23` skill-portability lint · `GAP-25` min_facts warning · `GAP-26` sweep-push · `GAP-27` high-entropy in status · `GAP-28` GCP/Stripe patterns · `GAP-30` native-only requeue · `GAP-31` dedicated gap-fill PR · `GAP-34` source-weight floor · `GAP-35` corroboration source_tool · `GAP-39` ROUTING.md · `GAP-40` `--tool` alias · `GAP-41` schema-lock-in in L2 · plus all `HYG-*`.

**Roadmap easy-wins** (from §28/§30): `GAP-38` `/memories` projection · `GAP-39` ROUTING.md index · `GAP-40` `--tool` alias. (The rest of §28 phases 0–8 are already substantially shipped; phase 9's semantic re-rank is implemented.)

---

## Doc / spec hygiene

- **HYG-01 ✅** — §27 Config Reference: document the keys used in code but undocumented: `git.sign_commits`/`git.require_signed_commits` (`config.py:147`), the `telemetry:` section (`config.py:152`), `dream.l2_model` (`config.py:35`), `sessions.archive_interval` (`config.py:82`), and the `.cursorrules` bridge default (`config.py:139`). Soften `org` from "required" to "required for remote/hybrid" (`config.py:207` makes it optional).
- **HYG-02 ✅** — Decide build-vs-cut for `blobs.py` in §25 (ties to GAP-01). README doesn't claim it; if blobs are out of scope, remove the module + `umx blob` CLI + "stale blobs" doctor line from §25.
- **HYG-03 ✅** — Reconcile §26 viewer feature table with shipped reality (ties to GAP-18/29): either build the missing metrics/tree or update the table.
- **HYG-04 ✅** — Rename `gitmem-spec-v0_9.md` → `gitmem-spec-v0_10.md` to match the branch and bump the declared `spec_version`. Also note `type: extraction` is a real default label (`governance.py:29`) missing from the §12 label enum — add it.
- **HYG-05 ✅** — Commit or delete the untracked `README.local-before-github-merge.md`.
- **HYG-06 ✅** — Reconcile §10 native-memory paths with the capture-module split (ties to GAP-36) and the §28 `import --tool` example with the shipped `--adapter` (ties to GAP-40).

---

## Out of scope — do NOT build (spec-declared deferrals)

Per §28 roadmap / §29 non-goals / Appendix C — absent ≠ broken:

- **Non-goals:** memory in project repos; cloud-only sync; auto-commit to `main` in remote/hybrid; multi-user/team memory (v1); cross-machine secret syncing; vector search by default; pane-read mid-stream injection; narrative merging in storage; persistent daemons; multi-language normalisation (v1).
- **Deferred (Appendix C):** knowledge graph / `related_to`; CRDT / append-only event model; encryption at rest (delegated to GitHub private repos + disk encryption); `.umx/` inside project repos; VS Code extension/marketplace; multimodal (images/audio/video); EU AI Act compliance.
- **Experimental by design:** remote/hybrid PR governance, branch protection, approval gating (README already flags these), deeper `aip mem` runtime integration. The arbitrator agent for raw git-conflict markers (§18) is MAY/tool-specific.

---

## Verification notes

Headline claims were re-verified against source before writing (2026-05-29): `SOTA_REVIEWED` never assigned (only enum/weight/short-code); `semantic_dedup_key` zero call sites; `adapters/generic.py:41,43` `IMPLICIT`/`TOOL_OUTPUT`; zero matches for `retrieval_fidelity`, `invalidates_when`/`memory/artifacts`, `~/.gitmem/state` lock, MEMORY CHRONICLES layers, binary pre-ingest sniff, `injection-audit.jsonl`; `is_suppressed` only ever called with `phase="gather"`; redaction has AWS/OpenAI/Anthropic but no GCP/Stripe; `blobs.py` absent and no `umx blob` group.
