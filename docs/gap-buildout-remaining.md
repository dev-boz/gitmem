# gitmem spec gap build-out — remaining work (handoff)

**As of:** 2026-06-01 · **Branch:** `spec-v0_10-unified-update` (local == remote == `1bf2d9d`)
**Source plan:** `docs/spec-gap-buildout.md` · **Verification:** `docs/gap-verification-2026-05-31.md`

## State of play

- The full gap build-out (≈29 of 47 tasks) plus **GAP-01 (blob store, the only Critical)**
  is **committed and pushed**. Nothing is left uncommitted.
- **Test suite: 1017 passing** via `.venv/bin/python -m pytest -q --ignore=tests/test_tui.py`.
- **Known environment issue (not a regression):** `tests/test_tui.py` fails at *collection*
  because the `textual` package isn't installed in `.venv`. It fails identically on older
  commits — it is purely a missing optional dep. Either `pip install 'textual>=0.63,<1'`
  into `.venv`, or keep using `--ignore=tests/test_tui.py`. Resolve this first so the next
  person gets a clean full-suite run.

### How to run things
- Tests: `.venv/bin/python -m pytest -q --ignore=tests/test_tui.py` (system `python` is absent;
  use `.venv/bin/python` or `python3`).
- The repo's spec lives at `gitmem-spec-v0_9.md` (repo root). Gap line refs below point there.

## Remaining tasks

### ❌ NOT DONE — larger Bucket-B features

| GAP | Effort | What's needed | Spec ref |
|---|---|---|---|
| **11** MEMORY CHRONICLES layers | L | Dream sub-phase writes `context/layers/{task_class}-{date}/{numeric,temporal,narrative,digest}.md`; injection selects digest by default, richer layers as budget allows. None of this exists in code (only in the spec commit). | §16 ~L1899 |
| **16** Reasoning artifacts | L | `memory/artifacts/` store, SQLite index, injection on conclusion/evidence match, `invalidates_when` checked by Dream Orient. Zero code today. *Option: demote §3b to roadmap instead of building.* | §3b ~L298 |
| **20** workspace/ ingest into Orient | M | Dream Orient should read `workspace/dream-candidates/` and `workspace/transcripts/{session_id}.jsonl` as session sources (optionally `workspace/tasks/*/audit.jsonl`, `workspace/status/HEARTBEAT-*.md`). Today only telemetry `*_to_dream_candidates` helpers exist; the real dirs are never read. *Option: demote these §30 lines to roadmap.* | §30 ~L3071 |
| **37** Diary + Session Handover (§8a) | M–L | `local/diary.md` (append-only log) + `local/handover.md` / `local/handovers/YYYY-MM-DD.md`; read/append helpers, CLI surface, optional Dream ingest of handovers at S:3. Whole feature absent. *Option: formally mark §8a as future.* | §8a ~L860 |

### ❌ NOT DONE — small correctness fixes (good warm-ups)

| GAP | Effort | What's needed | Spec ref |
|---|---|---|---|
| **06** Wire `semantic_dedup_key` | M | `identity.py:28` `semantic_dedup_key(text, scope, topic)` has **zero call sites**. `consolidate()` (`umx/dream/pipeline.py:318`) still matches on `topic + text.lower()`, ignoring `scope`. Route merge/corroboration detection through the key so same-text/different-scope pairs are NOT merged. Add a test. | §5 ~L413 |
| **21** Tag-drift lint | S–M | Add a tag-clustering/canonicalisation finding to `umx/dream/lint.py` `generate_lint_findings` (e.g. `db`/`database`/`postgres` → propose one canonical tag, `type: lint`). Add a test. | §11 ~L1495 |
| **34** `dream_consolidation` weight floor | S | `umx/strength.py:97` returns a plain mean `sum/len` (no floor) AND nothing populates `corroborating_source_weights` (zero callers) — so the whole `DREAM_CONSOLIDATION` branch is inert. Apply floor-of-avg AND make consolidation extraction supply the input weights. Add a test. | §5 ~L466, §6 ~L575 |
| **36** Native adapters read real stores | M | `umx/adapters/claude_code.py:32,37` globs `CLAUDE.md` only (not the JSONL transcripts); Copilot path is `.github/copilot-instructions.md` vs `~/.copilot/`; no `GeminiAdapter`. Parse the real native stores OR reconcile §10 to document the capture-module split (HYG-06 already partly did docs). | §10 ~L1364 |

### ⚠️ PARTIAL — optional polish (low risk, matches plan's original finding)

| GAP | Effort | What's needed |
|---|---|---|
| **08** fragile→stable Rule #2 in Prune | S–M | `stabilize_facts` (`pipeline.py:398`) only stabilises facts not touched this cycle; Rule-2 corroboration is applied inline in Consolidate (`pipeline.py:319-322`) only when corroboration arrives the same pass. Add a distinct Prune-time pass that re-stabilises a fragile fact whose independent corroboration (`corroborated_by_*`) landed in a *prior* cycle. |

## Suggested order for a fresh session

1. **Fix the venv:** install `textual` so the full suite (incl. `test_tui.py`) runs clean.
2. **Warm-ups (small, self-contained):** GAP-34 → GAP-06 → GAP-21. Each is a focused change + one test.
3. **GAP-36** (or just reconcile §10 docs if not parsing real stores).
4. **GAP-08** polish if desired.
5. **Decide build-vs-demote** for the big four (GAP-11, 16, 20, 37). 16/20/37 each have a
   legitimate "mark as roadmap" option in the plan — confirm with the user before building,
   since they're multi-day each.

## Working agreement / lessons from last session

- **Run the relevant tests and confirm GREEN before committing.** Last session a GAP-01
  commit (`936f44b`) shipped broken because a `cli.py` edit silently failed to apply and I
  committed without re-running tests; fixed forward in `1bf2d9d`. Don't repeat that.
- **`git grep`/secret-scanning:** GitHub push protection blocks synthetic secrets in tests.
  Use the split-string constants in `tests/secret_literals.py` (e.g. `GCP_API_KEY`,
  `STRIPE_SECRET_KEY`) — never inline a full `sk_live_…`/`AIza…` literal.
- **Commit hygiene:** end commit messages with the `Co-Authored-By: Claude Opus 4.8` trailer.
- Branch tracks `origin/spec-v0_10-unified-update`; pushes are normal fast-forwards.
