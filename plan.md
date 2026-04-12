# gitmem Handoff
Updated: 2026-04-12

## Canon

- Spec of record: `gitmem-spec-v0_9.md`
- Addendum A1 is already integrated into the spec and should be treated as historical rationale only.
- Dogfooding audit trail: `dogfooding_tests_results_changes.md`

## Current State

The repo is now in a credible local dogfooding state.

The biggest change this session is that dogfooding stopped being hypothetical and started producing immediate corrective feedback:

- a deterministic golden extraction harness now exists
- a real local session-lifecycle smoke path now exists
- a real Codex rollout capture path now exists via `umx capture codex`
- the README now documents the smallest local-mode dogfood loop
- an audit-trail doc now exists for dogfood tests, observed behavior, and follow-on fixes
- at least one real dogfood cycle was run and used to tighten behavior

This is still a local-mode product first. Remote/hybrid governance is still not mature enough to be the main testing target.

## Release Direction Update

The near-term target has changed from “keep dogfooding until it feels polished” to “ship a local-first alpha quickly and explain the rough edges up front.”

For that alpha:

- local mode is the supported product
- `capture codex`, `dream`, `search`, and `view` are part of the real story
- remote/hybrid must be described as experimental or incomplete
- extraction quality should keep improving, but it is no longer a reason to block sharing the idea if the caveats are explicit

## What Was Completed This Session

### Closed work

- Added a real Codex rollout capture path:
  - `umx/codex_capture.py`
  - `umx.cli capture codex`
  - `tests/test_codex_capture.py`
- Fixed indexed search fallback for hyphenated FTS queries:
  - `umx/search.py`
  - `tests/test_search.py`
- Added a deterministic golden extraction harness:
  - `tests/test_golden_extraction.py`
  - `tests/fixtures/golden_extraction/...`
- Added a high-signal local dogfood smoke test:
  - `tests/test_dogfood_readiness.py`
  - exercises `session_start` → `inject` → `assistant_output` → `session_end` → `dream` → `search` → `view`
- Added a local dogfood guide to `README.md`
- Added `dogfooding_tests_results_changes.md` as a running audit trail
- Tightened transcript extraction:
  - sentence splitting now handles `?` boundaries better
  - topic extraction now skips placeholder/article tokens like `redacted`
- Fixed a real prune-path crash:
  - `DreamPipeline.prune()` now handles populated `usage_snapshot()` rows correctly
- Tightened source-file extraction after dogfooding feedback:
  - only `assistant` / `tool_result` file references count as evidence that a file was actually read
  - markdown/text files are now treated as `external_doc`, `S:2`, `fragile`
  - bare user references like `continue from plan.md` no longer promote that file into source extraction by themselves
- Added a `python3 -m umx.cli` execution path so the workspace code can be run directly during development

### What the dogfood pass revealed

The first session-summary dogfood run was a success specifically because it exposed a real issue immediately:

- mentioning `plan.md` caused the system to extract too many stable source-derived facts from a project document

That behavior was not catastrophic, but it was too eager. The follow-up change demoted document-derived facts and required stronger evidence that a file was actually read.

The first live Codex rollout import revealed the next real issue:

- the live path works, but Codex procedural commentary still promotes too many low-value session facts

## Verification At Handoff

Focused verification:

- `pytest -q tests/test_source_extraction.py tests/test_golden_extraction.py tests/test_dogfood_readiness.py`
- Result: `19 passed in 9.94s`

Focused verification for the live Codex path:

- `pytest -q tests/test_codex_capture.py tests/test_source_extraction.py tests/test_dogfood_readiness.py`
- Result: `17 passed in 17.27s`

CLI/command verification:

- `pytest -q tests/test_commands.py tests/test_cli_extras.py`
- Result: `13 passed in 11.45s`

Search/capture regression verification:

- `pytest -q tests/test_search.py tests/test_codex_capture.py`
- Result: `13 passed in 22.95s`

Latest extraction/source regression verification:

- `pytest -q tests/test_golden_extraction.py tests/test_dream.py tests/test_source_extraction.py tests/test_codex_capture.py tests/test_dogfood_readiness.py`
- Result: `27 passed in 16.94s`

Full verification:

- `pytest -q`
- Result: `191 passed in 138.64s`

Install/run verification:

- Confirmed repo-local execution with `PYTHONPATH=$PWD python3 -m umx.cli --help`
- Confirmed `pip install -e /home/dinkum/projects/gitmem` in a clean temp virtualenv imports `/home/dinkum/projects/gitmem/umx/cli.py` and exposes the current command set, including `capture`
- A temp virtualenv created with `--system-site-packages` can mask the checkout with a stale user-site `umx`; use a clean virtualenv for release verification

Manual local-mode verification was also run with a hermetic temp `UMX_HOME` using the workspace code path:

- initialized memory home
- initialized project memory for the repo
- imported a real Codex rollout with `capture codex`
- triggered Dream
- confirmed `search --raw` found the session
- confirmed indexed `search` found the learned fact
- confirmed hyphenated indexed queries no longer crash FTS parsing
- confirmed `inject` and `view --list` returned usable output
- confirmed assistant-read `plan.md` facts stayed `external_doc`, `fragile`

## Main Judgment

The repo is ready for continued local dogfooding and is close enough for a local-first alpha release.

Do not spend the next session broadening into remote governance unless explicitly asked.

Do not block alpha packaging on perfect extraction. Block only on honesty about scope, a verified install/run path, and one more credible real-world pass that does not explode into obviously bad memory.

The highest-value missing piece is no longer “do we have tests?” It is:

1. tightening extraction quality for the real live Codex path
2. continuing to calibrate what should become memory versus what should remain weak document evidence
3. recording those dogfood loops in the audit-trail doc

## What Is Still Missing Before Ongoing Real Use

### Must do next

1. Extraction calibration for the real Codex live path

- Current dogfooding proved the local-mode core and now includes a real live transcript import via `umx capture codex`.
- The next practical issue is quality, not capture:
  live Codex commentary still promotes too many procedural facts and weak topics.
- Start by reducing low-signal session facts such as:
  - procedural progress updates
  - commentary-only meta text
  - weak one-word topics like `i`, `cli`, `capture`

2. Continue extraction calibration from real dogfood evidence

- Document-derived memory is now safer, but still worth tuning.
- Re-run the same kind of dogfood capture and inspect:
  - how many facts came from the session itself
  - how many facts came from referenced documents
  - whether the document-derived facts are useful without dominating memory

3. Keep the audit trail current

- Every meaningful dogfood run should be logged in `dogfooding_tests_results_changes.md`
- Record:
  - scenario
  - observed results
  - judgment
  - code changes
  - verification

### Not required next

- Full remote/hybrid PR governance
- Cross-project dream
- Principle governance
- Published `aip mem` integration
- Full viewer editor/TUI feature set

## Important Limitation

Remote/hybrid `gitmem` is still not spec-complete enough for serious real-world testing.

Key issue:

- `remote` mode currently creates branch/PR proposals, but does not actually operate a full PR pipeline.
- `hybrid` still routes fact changes through the normal local prune/save path instead of enforcing true “all fact changes via PR” semantics.

Relevant files:

- `umx/dream/pipeline.py`
- `umx/governance.py`
- `umx/actions.py`

Conclusion:

- Keep real testing in `local` mode.
- Treat remote/hybrid as future work, not the present dogfood target.

## Recommended Next Session Goal

Title:

`Prepare a local-first alpha release`

Definition of done:

- The Codex live capture path still works end-to-end.
- One more real dogfood run is recorded in `dogfooding_tests_results_changes.md`.
- The README states the alpha scope and caveats plainly.
- The install/run story is verified with `pip install -e .` and/or `python3 -m umx.cli`.
- The repo remains green.

## Concrete Next Session Tasks

1. Dogfood the Codex live capture path again after extraction tightening

- Import a real rollout with `capture codex`
- Verify:
  - `search --raw` still reflects the imported session
  - Dream still runs cleanly
  - indexed search still surfaces at least one useful learned fact
  - low-signal procedural facts are reduced

2. Re-run the “referenced doc” scenario after the tightening

- Recreate a session that references `plan.md` or a similar doc
- Confirm:
  - user-only mentions do not trigger source extraction
  - actual assistant-read docs produce `external_doc`, fragile facts
  - real code/config files still produce `ground_truth_code`

3. Add another dogfood audit entry

- Append the exact test, result, and any follow-on change to:
  - `dogfooding_tests_results_changes.md`

4. Decide whether the next calibration pass should be retrieval or extraction

- If bad facts are still entering memory:
  focus on extraction/source attribution
- If good facts exist but are not surfaced well:
  focus on injection/retrieval precision

## Suggested Commands For The Next Session

Prefer the workspace code path during active development:

```bash
PYTHONPATH=/home/dinkum/projects/gitmem python3 -m umx.cli --help
```

If the installed `umx` binary is stale, use:

```bash
PYTHONPATH=/home/dinkum/projects/gitmem python3 -m umx.cli init --org memory-org
PYTHONPATH=/home/dinkum/projects/gitmem python3 -m umx.cli init-project --cwd /path/to/project
```

Useful checks:

```bash
pytest -q
pytest -q tests/test_codex_capture.py tests/test_source_extraction.py tests/test_dogfood_readiness.py
pytest -q tests/test_source_extraction.py tests/test_golden_extraction.py tests/test_dogfood_readiness.py
```

Likely dogfood commands:

```bash
PYTHONPATH=/home/dinkum/projects/gitmem python3 -m umx.cli status --cwd /path/to/project
PYTHONPATH=/home/dinkum/projects/gitmem python3 -m umx.cli capture codex --cwd /path/to/project
PYTHONPATH=/home/dinkum/projects/gitmem python3 -m umx.cli inject --cwd /path/to/project --prompt "postgres deploy flow"
PYTHONPATH=/home/dinkum/projects/gitmem python3 -m umx.cli search --cwd /path/to/project --raw postgres
PYTHONPATH=/home/dinkum/projects/gitmem python3 -m umx.cli search --cwd /path/to/project postgres
PYTHONPATH=/home/dinkum/projects/gitmem python3 -m umx.cli view --cwd /path/to/project --list
```

## Files Worth Opening First Next Session

- `plan.md`
- `dogfooding_tests_results_changes.md`
- `README.md`
- `umx/codex_capture.py`
- `umx/dream/extract.py`
- `umx/dream/pipeline.py`
- `umx/cli.py`
- `tests/test_golden_extraction.py`
- `tests/test_codex_capture.py`
- `tests/test_dogfood_readiness.py`
- `tests/test_source_extraction.py`

## Strategy Guidance For The Next Agent

- Do not broaden scope to full remote `gitmem` unless explicitly asked.
- Optimize for local use on a real project.
- Prefer real dogfood loops plus measurement over speculative feature additions.
- When dogfooding reveals a problem, preserve the evidence in `dogfooding_tests_results_changes.md` before or alongside the fix.
- If forced to choose between more viewer polish and extraction quality for live transcripts, choose extraction quality.
- Preserve the current green baseline.
