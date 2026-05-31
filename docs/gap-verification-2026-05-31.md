# Spec Gap Build-Out — Independent Verification (2026-05-31)

Verifies actual code state vs. the GPT-5.1-mini hand-off summary, by grepping `umx/`
for each GAP's concrete markers and tying claims to tests.

> ⚠️ **RISK FIRST: none of the build-out work is committed.** All ~4200 changed lines
> (57 modified files + new `push_queue.py`, `trust_tags.py`, `test_strength.py`) sit in
> the **working tree** of branch `spec-v0_10-unified-update`. A crash, errant
> `git checkout`/`git stash`, or branch switch loses everything Copilot did over both
> sessions. **Recommend committing now** before any further work. The 7 *commits*
> already on the branch are the earlier spec-writing task, not the gap build-out.

**Test suite: 1014 passed** (`python3 -m pytest -q`, ~109s, zero failures). Test deltas
corroborate the features (e.g. `test_git_ops +108`, `test_doctor +72`, `test_viewer +98`,
`test_backup +178`, `test_inject_search +233`).

**The mini summary undercounted badly — and was working from a truncated view.** It
reported 13 done / 2 in-progress / 3 pending out of "18 todos," and called GAP-29 merely
"in progress." The actual spec scope is **47 tasks (41 GAP + 6 HYG)**, of which **~29 are
done**. The mini was tracking a much smaller internal todo list than the real plan — so
its numbers aren't just imprecise, they describe a different (smaller) universe of work.

## ✅ DONE — verified in code

(Implementation confirmed by grep + the full suite is green. Where a GAP also has a
targeted assertion, it's noted; some entries are confirmed by implementation + green
suite rather than a dedicated assertion.)

| GAP | Evidence |
|---|---|
| 02 SOTA promotion | `dream/pipeline.py:841` assigns `Verification.SOTA_REVIEWED` |
| 03 L2 context (sessions+manifest) | `dream/l2_review.py` loads `source_sessions`, `missing_source_sessions`, manifest |
| 04 principle promotion bar | `fact_actions.py:125-130` gates ≥3 sessions + 14-day stability |
| 05 user-level dream lock | `dream/gates.py:133` `umx_home/state/dream.lock` |
| 07 retire prune.py | `dream/prune.py` is now a thin wrapper over `strength.should_prune` |
| 09 tool budget inference | `inject.py:1258` `inferred_max_tokens_for_tool` + `config.tool_max_tokens` |
| 10 retrieval_fidelity tags | `inject.py` emits exact/lexical/semantic/fallback; `search.py` persists |
| 12 binary pre-ingest interception | `sessions.py` magic-byte/size-cap → quarantine; test asserts PNG intercept |
| 13 tombstone suppress phases | `audit.py:59` `phase="rederive"`, `:185` `phase="audit"` |
| 14 same-fact_id merge/quarantine | `memory.py:524-651` `FactDataIntegrityError`, `_quarantine_same_fact_id_collision`, `_merge_same_fact_id` |
| 15 async push queue + retry | new `push_queue.py`; `git_push_with_retry`, `drain_push_queue`; non-ff retry test |
| 17 doctor (auth/push-queue/index) | `doctor.py:160-162` wires gh auth, `push_queue_summary`, `index_staleness` |
| 18 rejection/escalation rate | `governance_health.py:202-205` computes + surfaces both |
| 19 adapter memory_type | `adapters/generic.py:40-42` `EXPLICIT_SEMANTIC` + `SELF_REPORTED` + S:3 |
| 22 procedure-triggers lint | `dream/lint.py:150` "missing required ## Triggers" |
| 23 skill non-portability lint | `dream/lint.py:153-161` unsupported_directives/blocked paths |
| 24 injection-audit.jsonl | `search.py:25` `INJECTION_AUDIT_NAME` + `reason`/`relevance_score`/`dedup` |
| 25 min_facts warning | `budget.py:94-100` returns warning string |
| 26 sweep push | `hooks/session_start.py` drains queue + `git_push_with_retry` |
| 27 high-entropy in status | `status.py:87` `high_entropy_count` review flag |
| 28 GCP/Stripe redaction | `redaction.py:15,18` `gcp-api-key` (AIza…) + `stripe-key` |
| 29 hierarchical tree + machine | `viewer/server.py` `_tree_scope_section`, `Scope.MACHINE`; test at `test_viewer.py:479` |
| 30 native-only requeue | `pipeline.py` tracks `native_only`, excludes from gathered marking |
| 32 quarantine self-derived | `trust_tags.py` + `pipeline.py:152-160` |
| 33 untrusted/contamination | `trust_tags.py` + `extract.py:642` + L2 block |
| 35 corroboration on source_tool | `strength.py:173-187` keys on `source_tool` |
| 38 export --format memories | `backup.py` `export_memories`/`import_memories` round-trip |
| 39 ROUTING.md generator | `routing.py:170` `write_routing_index` |
| 40 --tool alias | `cli.py:2450` hidden alias of `--adapter` |

Plus easy-wins **GAP-41** (schema-lock-in surfaced) and **HYG-01..06** (docs reconciled:
`cli.md`, `config.md`, `spec-parity.md` all modified).

## ⚠️ PARTIAL — works, but not fully to spec (low risk)

| GAP | State |
|---|---|
| 08 fragile→stable rule #2 in Prune | `stabilize_facts` (`pipeline.py:398`) only stabilises facts *not* touched this cycle that survived; Rule-2 corroboration is applied inline in Consolidate (`pipeline.py:319-322`) only when corroboration arrives the same pass. No separate Prune pass re-stabilises a fragile fact whose independent corroboration landed in a *prior* cycle. Matches the plan's original finding. |

## ❌ NOT DONE — confirmed absent

| GAP | Evidence |
|---|---|
| 01 blob store (**Critical**) | `umx/blobs.py` **missing**; no `umx blob` CLI; GAP-12 quarantines rather than routing small media to `local/blobs/` |
| 06 semantic_dedup_key | `identity.py:28` still has **zero call sites**; `consolidate()` (`pipeline.py:318`) matches on `topic + text.lower()`, ignoring `scope`. Unchanged from "stubbed". |
| 11 MEMORY CHRONICLES layers | no `context/layers` / `numeric|temporal|narrative|digest.md` generation in code (spec commit only) |
| 16 reasoning artifacts | no `invalidates_when` / `memory/artifacts` / `reasoning_artifact` anywhere |
| 20 workspace/ ingest into Orient | only telemetry `*_to_dream_candidates` helpers exist; real `workspace/dream-candidates/` + `workspace/transcripts/` dirs are **not** read by Dream Orient |
| 21 tag-drift lint | no tag-drift/canonicalisation finding in `dream/lint.py` |
| 34 source weight = floor of avg | `strength.py:97` returns plain mean `sum/len` (no floor) AND no site populates `corroborating_source_weights` (zero callers) → the `DREAM_CONSOLIDATION` branch is inert dead code |
| 36 native adapters read real stores | `adapters/claude_code.py:32,37` globs `CLAUDE.md` only (not JSONL); no `GeminiAdapter` |
| 37 diary + session handover (§8a) | no `diary`/`handover` code anywhere (spec text only); whole §8a feature absent |

## Bottom line

Most of M0–M4 (easy wins, trust loop, memory-model, safety, injection depth) plus much
of M5 is **done and tested (1014 passing)**. Remaining real work:

- **Critical:** GAP-01 blob store.
- **Larger Bucket-B features:** GAP-11 (chronicles), GAP-16 (reasoning artifacts),
  GAP-20 (workspace ingest), GAP-37 (diary/handover).
- **Correctness fixes (small):** GAP-06 (wire `semantic_dedup_key`, honour scope),
  GAP-34 (weight floor + populate inputs), GAP-21 (tag-drift lint), GAP-36 (native stores
  / `GeminiAdapter`).
- **Optional polish:** GAP-08 separate Prune-time re-stabilisation (low risk).

Then: review the ~4200-line working tree and commit it (it is currently all uncommitted).
