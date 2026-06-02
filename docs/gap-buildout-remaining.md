# gitmem spec gap build-out — remaining work (handoff)

**As of:** 2026-06-01 · **Branch:** `spec-v0_10-unified-update`
**Source plan:** `docs/spec-gap-buildout.md` · **Verification:** `docs/gap-verification-2026-05-31.md`

## State of play

- The original handoff slice plus the follow-up completion work for GAP-11, GAP-16, GAP-37,
  and the remaining GAP-34 floor behavior are implemented in the current working tree.
- **Independent audit (2026-06-02):** all 9 of the latest-batch gaps (06, 08, 11, 16, 20,
  21, 34, 36, 37) were re-audited against their spec "Done when" criteria. Implementations
  satisfy acceptance. Three gaps were missing the *dedicated test assertion* the acceptance
  text requires; those were added this session:
  - **GAP-06:** `tests/test_dream.py::test_consolidate_merges_same_text_scope_topic`
    (positive merge path) + made `semantic_dedup_key` spec-faithful (dropped the `.strip()`,
    lowercase the full payload per §5 ~L413).
  - **GAP-08:** `tests/test_dream_prune.py::test_stabilize_facts_rule_two_isolated_from_survive_one_cycle`
    (rule-2 promotion isolated from rule-1).
  - **GAP-35:** `tests/test_strength.py::test_independent_corroboration_for_different_tool_ignores_time_gap`.
- **Full-suite baseline:** **1044 passing** via `.venv/bin/python -m pytest -q` (textual 0.89.1
  is installed in `.venv`, so `tests/test_tui.py` and `tests/test_viewer.py` now run — no ignore needed).
- ⚠️ **The entire latest gap batch (~1357 insertions across 23 modified files + 5 untracked
  modules: `umx/{artifacts,chronicles,continuity}.py`, `umx/adapters/gemini.py`, and
  `tests/test_continuity_artifacts_chronicles.py`) is still UNCOMMITTED** on top of
  `5a6cacd`. Commit it before any branch switch to avoid losing the work.

### How to run things
- Tests: `.venv/bin/python -m pytest -q --ignore=tests/test_tui.py` (system `python` is absent;
  use `.venv/bin/python` or `python3`).
- The repo's spec lives at `gitmem-spec-v0_9.md` (repo root). Gap line refs below point there.

## Remaining tasks

As of the current working tree, all gaps listed in this handoff have implementation and focused
regression coverage. The file is retained as the historical handoff source.

### ✅ COMPLETED — larger Bucket-B features

| GAP | Completion |
|---|---|
| **11** MEMORY CHRONICLES layers | Dream writes `context/layers/{task_class}-{date}/{numeric,temporal,narrative,digest}.md`; injection includes digest by default and optional richer layers as budget permits. |
| **16** Reasoning artifacts | `memory/artifacts/*.md` parsing, SQLite indexing, injection on conclusion/evidence keyword match, and Orient invalidation via `invalidates_when` are implemented. |
| **20** workspace/ ingest into Orient | Dream Gather reads `workspace/transcripts/*.jsonl` and `workspace/dream-candidates/`; malformed `.json`/`.jsonl` candidates are skipped without aborting ingest. |
| **37** Diary + Session Handover (§8a) | `local/diary.md`, `local/handover.md`, dated handover archives, CLI read/write surfaces, and S:3 handover Dream ingest are implemented. |

### ✅ COMPLETED — correctness fixes

| GAP | Completion |
|---|---|
| **06** Wire `semantic_dedup_key` | Consolidate dedup/corroboration now uses the scope-aware semantic key so same text in different scopes remains distinct. |
| **08** fragile→stable Rule #2 in Prune | Prune-time stabilization now promotes fragile facts that already have independent corroboration recorded from a prior cycle. |
| **21** Tag-drift lint | Lint reports canonical tag drift clusters such as `db`/`database`/`postgres`. |
| **34** `dream_consolidation` weight floor | Consolidation facts carry corroborating source weights and use a positive source-weight floor when averaging inputs. |
| **36** Native adapters read real stores | Claude, Copilot, and Gemini adapters read their native real session stores while retaining markdown import paths. |

## Suggested next verification

1. Run the focused suite used for this closure.
2. Run `.venv/bin/python -m pytest -q --ignore=tests/test_tui.py`.
3. If `textual` is installed in the active venv, run the full suite without the `test_tui.py` ignore.

## Working agreement / lessons from last session

- **Run the relevant tests and confirm GREEN before committing.** Last session a GAP-01
  commit (`936f44b`) shipped broken because a `cli.py` edit silently failed to apply and I
  committed without re-running tests; fixed forward in `1bf2d9d`. Don't repeat that.
- **`git grep`/secret-scanning:** GitHub push protection blocks synthetic secrets in tests.
  Use the split-string constants in `tests/secret_literals.py` (e.g. `GCP_API_KEY`,
  `STRIPE_SECRET_KEY`) — never inline a full `sk_live_…`/`AIza…` literal.
- **Commit hygiene:** end commit messages with the `Co-Authored-By: Claude Opus 4.8` trailer.
- Branch tracks `origin/spec-v0_10-unified-update`; pushes are normal fast-forwards.
