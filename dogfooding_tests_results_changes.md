# Dogfooding Tests Results Changes

Updated: 2026-04-12

## Purpose

This document is the running audit trail for gitmem dogfooding.

For each dogfood cycle, record:

- what was tested
- what the system learned or failed to learn
- why the behavior mattered
- what was changed in response
- how the change was verified

This is intentionally operational, not aspirational. It should capture real tests on real flows, including awkward or imperfect results.

## Entry Format

For each entry, capture:

- Date
- Test name
- Environment
- Input / scenario
- Observed result
- Judgment
- Change made
- Verification
- Follow-up

## Entries

### 2026-04-11 — Local Dogfooding Readiness Baseline

- Test name:
  `local-mode dogfooding readiness build-out`
- Environment:
  workspace code in `/home/dinkum/projects/gitmem`
- Input / scenario:
  Implement the minimum local-mode dogfooding slice from [plan.md](/home/dinkum/projects/gitmem/plan.md):
  golden extraction harness, one high-signal lifecycle smoke, and a short dogfood guide.
- Observed result:
  The repo was already close to viable local testing. The main missing pieces were deterministic extraction evaluation and a real operational smoke path.
- Judgment:
  Not blocked on remote governance. The right near-term product is local-mode `umx`.
- Change made:
  Added [tests/test_golden_extraction.py](/home/dinkum/projects/gitmem/tests/test_golden_extraction.py), [tests/test_dogfood_readiness.py](/home/dinkum/projects/gitmem/tests/test_dogfood_readiness.py), fixture corpus under [tests/fixtures/golden_extraction](/home/dinkum/projects/gitmem/tests/fixtures/golden_extraction), extractor tightening in [umx/dream/extract.py](/home/dinkum/projects/gitmem/umx/dream/extract.py), prune fix in [umx/dream/pipeline.py](/home/dinkum/projects/gitmem/umx/dream/pipeline.py), and local-use guidance in [README.md](/home/dinkum/projects/gitmem/README.md).
- Verification:
  `pytest -q` passed with `183 passed in 142.25s`.
- Follow-up:
  Start recording real dogfood runs and use them to calibrate extraction and retrieval behavior.

### 2026-04-11 — Session Summary Capture Exposed Over-Eager Document Extraction

- Test name:
  `manual summary capture of this session`
- Environment:
  hermetic temp home at `/tmp/gitmem-session-dogfood-xZtiP0`, workspace code via `PYTHONPATH=/home/dinkum/projects/gitmem python3 -m umx.cli`, target repo `/home/dinkum/projects/gitmem`
- Input / scenario:
  Wrote a concise summary session with session id `2026-04-11-codex-dogfood-001`, then let `session_end` trigger Dream and inspected the resulting facts.
- Observed result:
  The system correctly extracted session-summary facts such as:
  `Gitmem has a deterministic golden extraction harness for session, redaction, source-file, and gap cases`
  and
  `Gitmem has a local session lifecycle smoke test that covers session_start, inject, assistant_output, session_end, dream, search, and view`.

  It also extracted a large set of stable `source-extract` facts from `plan.md` because the session context referenced that file.
- Judgment:
  This was a successful dogfood run because it exposed a real behavior immediately worth fixing. The `plan.md` read was not catastrophic, but document-derived facts were too easy to promote and too strong once promoted.
- Change made:
  Tightened [umx/dream/extract.py](/home/dinkum/projects/gitmem/umx/dream/extract.py) so:
  - only `assistant` / `tool_result` file references count as evidence that a file was actually read
  - markdown/text files are emitted as `external_doc`, `S:2`, `fragile` instead of `ground_truth_code`, `S:3`, `stable`

  Added regression coverage in [tests/test_source_extraction.py](/home/dinkum/projects/gitmem/tests/test_source_extraction.py) for both behaviors.
- Verification:
  Targeted verification:
  `pytest -q tests/test_source_extraction.py tests/test_golden_extraction.py tests/test_dogfood_readiness.py`
  passed with `19 passed in 9.94s`.

  Full verification:
  `pytest -q`
  passed with `185 passed in 144.57s`.
- Follow-up:
  Re-run the same session-summary capture after more live dogfood sessions and compare:
  - number of session-derived facts
  - number of document-derived facts
  - whether useful docs still contribute without dominating memory

### 2026-04-11 — Installed CLI Drift Found During Manual Dogfood Pass

- Test name:
  `real CLI execution on temp project`
- Environment:
  temp project plus hermetic `UMX_HOME`; compared global `umx` binary versus workspace code path
- Input / scenario:
  Attempted to run local dogfood commands with the globally installed `umx`.
- Observed result:
  The global binary was stale and did not expose current commands such as `init --org` and `init-project`.
- Judgment:
  This is a real dogfooding paper-cut. It is not a core memory-system bug, but it will confuse local testing if the user assumes the installed binary matches the checkout.
- Change made:
  Added a module entry point guard to [umx/cli.py](/home/dinkum/projects/gitmem/umx/cli.py) so the workspace code can be run directly with `python3 -m umx.cli`.
- Verification:
  Confirmed `PYTHONPATH=/home/dinkum/projects/gitmem python3 -m umx.cli --help` exposes the current command set, and used that path for the hermetic manual dogfood pass.
- Follow-up:
  Keep recommending `pip install -e .` or `python3 -m umx.cli` during active development to avoid stale-install confusion.

### 2026-04-11 — Real Codex Rollout Capture Replaced Manual Summary Capture

- Test name:
  `codex rollout import dogfood`
- Environment:
  hermetic temp home at `/tmp/gitmem-codex-dogfood-ogAV9F`, workspace code via `PYTHONPATH=/home/dinkum/projects/gitmem python3 -m umx.cli`, target repo `/home/dinkum/projects/gitmem`, source rollout `/home/dinkum/.codex/sessions/2026/04/11/rollout-2026-04-11T23-22-22-019d7cb5-1fb1-7af3-a70c-78c403aeaa70.jsonl`
- Input / scenario:
  Imported the live Codex rollout with `capture codex`, verified the raw session was searchable, ran `dream --force`, and inspected both indexed facts and retained source-extracted facts.
- Observed result:
  `capture codex` imported `16` real transcript events into a normal `umx` session file. `search --raw "first-class for Codex"` immediately found the imported session. `dream --force` completed successfully and retained `69` facts. Referenced `plan.md` content stayed demoted as `external_doc`, `S:2`, `fragile`, so the earlier source-attribution tightening still held on the live path.

  The first indexed lookup with a hyphenated query triggered an SQLite FTS parser error (`no such column: class`). That turned out to be a query-escaping issue in indexed search, not a capture-path failure.

  The quality issue exposed by this run is now different from the previous manual-summary issue: live Codex commentary still promotes too many low-value operational facts such as `The capture code is in`, plus many weak topics like `i`, `cli`, and `capture`.
- Judgment:
  The live capture path now works end-to-end for the real tool in use. The next calibration pass should target extraction quality, not retrieval precision.
- Change made:
  Added [umx/codex_capture.py](/home/dinkum/projects/gitmem/umx/codex_capture.py), wired [capture codex](/home/dinkum/projects/gitmem/umx/cli.py) into the CLI, documented the path in [README.md](/home/dinkum/projects/gitmem/README.md), added [tests/test_codex_capture.py](/home/dinkum/projects/gitmem/tests/test_codex_capture.py), and tightened [umx/search.py](/home/dinkum/projects/gitmem/umx/search.py) so hyphenated indexed queries fall back to a safely quoted FTS query instead of crashing.
- Verification:
  Targeted verification:
  `pytest -q tests/test_codex_capture.py tests/test_source_extraction.py tests/test_dogfood_readiness.py`
  passed with `17 passed in 17.27s`.

  CLI/command verification:
  `pytest -q tests/test_commands.py tests/test_cli_extras.py`
  passed with `13 passed in 11.45s`.

  Search/capture regression verification:
  `pytest -q tests/test_search.py tests/test_codex_capture.py`
  passed with `13 passed in 22.95s`.

  Full verification:
  `pytest -q`
  passed with `189 passed in 147.89s`.
- Follow-up:
  Tighten live transcript extraction so Codex procedural commentary does not dominate memory promotion, while preserving the now-working real transcript import path.

### 2026-04-12 — Real Codex Rollout No Longer Explodes Source Extraction

- Test name:
  `codex rollout extraction-noise reduction for alpha release`
- Environment:
  hermetic temp home at `/tmp/gitmem-release-pass-kgLgpt`, workspace code via `PYTHONPATH=/home/dinkum/projects/gitmem python3 -m umx.cli`, target repo `/home/dinkum/projects/gitmem`, source rollout `/home/dinkum/.codex/sessions/2026/04/11/rollout-2026-04-11T23-22-22-019d7cb5-1fb1-7af3-a70c-78c403aeaa70.jsonl`
- Input / scenario:
  Re-imported the same real Codex rollout after tightening live session extraction and assistant file-reference evidence, then ran `dream --force` and inspected `view --list`.
- Observed result:
  `capture codex` imported `34` transcript events from the real rollout. The important behavioral change was source attribution: assistant summary mentions and changelog-style file references no longer caused the repo to explode into source-derived facts. For this rollout, assistant file-reference extraction collapsed to a single real read-evidence reference: `plan.md`.

  In the hermetic pass, `dream --force` retained `66` facts. Before the assistant file-evidence tightening, the same rollout had ballooned to `308` retained facts because summary links in assistant output were treated as proof that many files had been read. After the change, assistant-read `plan.md` material still came through as `external_doc`, `fragile`, which is the intended alpha behavior.

  Session extraction is still not perfect. Some roadmap/status facts from the live transcript are still being retained, but the result is materially less explosive and much easier to explain as a rough local-first alpha.
- Judgment:
  This is good enough to support a local-first alpha release, provided the README and release notes are explicit about scope and rough edges. The remaining work should bias toward packaging, install/run clarity, and honest positioning rather than blocking on perfect extraction.
- Change made:
  Tightened [umx/dream/extract.py](/home/dinkum/projects/gitmem/umx/dream/extract.py) so:
  - session extraction rejects more progress-update, changelog, and release-status phrasing
  - assistant file references only count when the sentence actually looks like a read/inspect/check claim, or when the file path itself is used as the factual subject
  - summary-style assistant file mentions no longer count as read evidence

  Added deterministic regression coverage in [tests/fixtures/golden_extraction/codex_procedural_noise](/home/dinkum/projects/gitmem/tests/fixtures/golden_extraction/codex_procedural_noise) and direct source-evidence coverage in [tests/test_source_extraction.py](/home/dinkum/projects/gitmem/tests/test_source_extraction.py).
- Verification:
  Focused verification:
  `pytest -q tests/test_golden_extraction.py tests/test_dream.py tests/test_source_extraction.py tests/test_codex_capture.py tests/test_dogfood_readiness.py`
  passed with `27 passed in 16.94s`.

  Full verification:
  `pytest -q`
  passed with `191 passed in 138.64s`.

  Manual hermetic verification:
  `capture codex` imported `34` events from the real rollout and `dream --force` retained `66` facts in the temp `UMX_HOME`, with assistant summary links no longer pulling large parts of the repo into source extraction.

  Install/run verification:
  a clean temp virtualenv successfully ran `pip install -e /home/dinkum/projects/gitmem` and `python -m umx.cli --help`, importing `/home/dinkum/projects/gitmem/umx/cli.py` and exposing the current CLI including `capture`.
- Follow-up:
  For alpha release prep, verify the install/run story end-to-end, keep the scope statement blunt about local-only support, and only then decide whether the next calibration pass should be more extraction work or retrieval precision.

## Current Themes

- Local mode is the correct dogfooding target.
- Deterministic extraction tests are paying for themselves immediately.
- Source provenance is the main lever for reducing bad memory promotion.
- Dogfooding should prefer “useful but imperfect” over blocking on full governance completeness.
