# Gitmem Production Readiness Plan

**Target release:** v1.0.0 — GitHub-synced memory as the marquee feature.
**Baseline:** 0.9.1-alpha, 477 tests, local mode production-quality.
**Last updated:** 2026-04-20
**Plan owner:** `copilot-cli`

## North star

GitHub sync is the differentiator. Local mode is the fallback; remote/hybrid is **the product**. M3 is the headline milestone — M1 and M2 exist solely to make M3 production-safe, and M4–M7 polish around it. Keep that framing when sequencing day-to-day work.

## How to use this document

This is a living build plan. Agents and humans update it as work progresses.

### Editing rules for agents

- **Status markers**: `[ ]` not started · `[~]` in progress · `[x]` done · `[!]` blocked.
- **Claim a task**: set `Owner:` to your identifier and flip `[ ]` → `[~]` in the same commit.
- **Release a task you can't finish**: flip `[~]` → `[ ]`, clear `Owner:`, add a `Notes:` line explaining why.
- **Mark `[x]` only after**: acceptance criteria pass, tests green on CI, change merged.
- **Respect `Depends on:`** — do not start a task with unresolved deps.
- **Never rewrite the Progress Log** — only append.
- **One task per commit.** Commit message prefix: `[T<id>] <subject>`.
- **If scope grows beyond the task**: split into sub-tasks (T3.1a / T3.1b) rather than expanding silently.

### Editing the status dashboard

Update the `Progress` column when you flip a task status. Format: `done/total`.

### Appending to Progress Log

Format: `- YYYY-MM-DD [T<id>] <agent>: one-line summary`

Keep entries terse. Long rationale belongs in the task's `Notes:` block or a commit message.

---

## Status dashboard

| Milestone | Progress | Target | Owner |
|---|---|---|---|
| M1 Spec parity & correctness | 0/8 | 2026-05-08 | copilot-cli |
| M2 Security baseline for sync | 0/7 | 2026-05-22 | copilot-cli |
| M3 GitHub governance GA ★ | 0/11 | 2026-07-03 | — |
| M4 Scale & performance | 1/6 | 2026-07-17 | copilot-cli |
| M5 Ops & docs | 0/7 | 2026-07-31 | copilot-cli |
| M6 Private beta | 0/5 | 2026-08-28 | — |
| M7 GA 1.0.0 | 0/5 | 2026-09-04 | — |

**Overall: 1/49 tasks · 2% · ~20 weeks.**

---

## M1 — Spec parity & correctness

**Exit criteria:** v0.9.2 cut, all known spec gaps closed, coverage ≥80% on arbitration/supersession/tombstone paths, green CI on Python 3.11+3.12.

### T1.1 — Wire `dream.lint_interval` cadence

- Status: `[~]`
- Owner: copilot-cli
- Depends on: —
- Files: `umx/dream/pipeline.py`, `umx/config.py`, `umx/dream/lint.py` (new helper `should_run`)
- Outcome: Lint sub-phase runs only when the last lint is older than `dream.lint_interval` (`weekly`/`daily`/`never`). Timestamp persisted in `.umx.json`.
- Acceptance:
  - New test: lint skipped when `last_lint` <7d ago with `weekly`
  - New test: `never` skips unless forced via `--force-lint`
  - `umx dream` JSON output includes `lint: {ran: bool, reason: str}`
  - Existing dream tests still pass
- Out of scope: changing lint rules
- Notes:
  - Local implementation and targeted validation are complete; keep `[~]` until merge + CI per plan rules.

### T1.2 — Gather-time embedding generation

- Status: `[~]`
- Owner: copilot-cli
- Depends on: —
- Files: `umx/dream/pipeline.py` (Gather phase), `umx/search_semantic.py`
- Outcome: When `search.backend == hybrid`, Gather calls `ensure_embeddings` on newly written facts so inject never pays the cold-start cost.
- Acceptance:
  - New test: running `dream` with hybrid backend populates embeddings cache
  - Inject latency test: p95 inject time unchanged after switching from fts5 to hybrid (embeddings pre-warmed)
  - Fallback path tested: if `sentence-transformers` missing, Gather logs a warning and continues
- Notes:
  - Embedding prewarm now targets the final persisted active fact set so hybrid search warms the IDs that actually survive consolidation.

### T1.3 — Resolve `umx/gitmem/` subpackage layout

- Status: `[~]`
- Owner: copilot-cli
- Depends on: —
- Files: decide — either split `umx/governance.py`, `umx/github_ops.py`, `umx/cross_project.py`, `umx/audit.py`, `umx/actions.py` into `umx/gitmem/`, or update `gitmem-spec-v0_9.md` §25 to match flat layout.
- Outcome: Spec and code agree on module layout.
- Acceptance:
  - All imports updated if moved; no broken imports
  - Spec §25 reflects the chosen layout
  - Decision recorded in **Decisions** section below
- Notes: Pick whichever is cheaper to maintain long-term. Flat layout is simpler; subpackage matches spec and isolates gh-specific code for future vendor pluggability.
  - Decision taken: keep the current flat layout and update spec §25 accordingly.

### T1.4 — Interactive slug-collision prompt

- Status: `[~]`
- Owner: copilot-cli
- Depends on: —
- Files: `umx/scope.py` (`discover_project_slug`, `init_project_memory`)
- Outcome: On slug collision, `init-project` prompts the user for a disambiguated slug instead of silently appending.
- Acceptance:
  - New test using `click.testing` simulating collision + user input
  - `--slug <override>` flag bypasses prompt (for automation)
  - `--yes` flag auto-appends `-2`, `-3` for CI contexts
- Notes:
  - Unsafe slugs are now rejected before project init to prevent path traversal outside `$UMX_HOME/projects/`.

### T1.5 — CLI flags audit vs spec

- Status: `[~]`
- Owner: copilot-cli
- Depends on: —
- Files: `umx/cli.py`, `gitmem-spec-v0_9.md`
- Outcome: Every flag documented in the spec exists in code with the spec's semantics, and every flag in code is documented in the spec. Any intentional divergence recorded under **Decisions**.
- Acceptance:
  - Audit table committed under `docs/spec-parity.md` listing each flag, spec language, code location, status
  - Zero unexplained divergences
- Notes: Known suspects to verify — `audit --rederive --all --model`, `dream --tier`, `propose --cross-project`.
  - The parity pass aligns the spec to the shipped CLI where draft-only flags never landed in code.

### T1.6 — Property-based Fact schema tests

- Status: `[~]`
- Owner: copilot-cli
- Depends on: —
- Files: `tests/test_models_property.py` (new), `pyproject.toml` (add `hypothesis` dev dep)
- Outcome: Hypothesis-generated facts round-trip through serialize/deserialize; enum fields never accept invalid values.
- Acceptance:
  - `hypothesis` strategy for `Fact` covering all enums and optional fields
  - Round-trip tests: to_markdown → from_markdown preserves equality
  - Invalid scope/source_type raises typed error
- Notes:
  - Property tests use the current markdown serializer/parser surface (`format_fact_line` / `parse_fact_line`) and document its existing non-round-tripped fields.

### T1.7 — Corner-case coverage: arbitration, supersession chains, tombstone vs supersession

- Status: `[~]`
- Owner: copilot-cli
- Depends on: T1.6
- Files: `tests/test_merge_corner_cases.py` (new), `tests/test_tombstones.py` (extend)
- Outcome: Explicit tests for tied trust-scores, supersession chains ≥3 deep, tombstone vs supersession disambiguation, ground_truth_code hard-rule edge cases.
- Acceptance:
  - Coverage on `umx/merge.py` and `umx/fact_actions.py` ≥90%
  - Documented behavior for each edge case in test docstrings
- Notes:
  - Local corner-case coverage now exercises trust-score ties, GT edge handling, deep supersession chains, governed merge dry-run/apply behavior, and tombstone/supersession interactions; full local suite is green at 499 passed.

### T1.8 — Cut v0.9.2

- Status: `[~]`
- Owner: copilot-cli
- Depends on: T1.1, T1.2, T1.3, T1.4, T1.5, T1.6, T1.7
- Files: `pyproject.toml`, `CHANGELOG.md`, git tag
- Outcome: Tagged release v0.9.2 with changelog describing spec parity fixes.
- Acceptance:
  - `pip install git+...@v0.9.2` works against a fresh environment
  - Changelog entries match actual shipped work
  - Tag pushed, release notes published
- Notes:
  - Local release-prep slice is now in place: authoritative runtime/package versions moved to `0.9.2` in `pyproject.toml`, `umx/__init__.py`, and `umx/mcp_server.py`.
  - `CHANGELOG.md` remains under `## [Unreleased]` until a real tag exists, but now truthfully includes the 0.9.2 branch work across threat modeling, redaction hardening, adversarial fixtures, quarantine review, signed-history enforcement, scope isolation, and supply-chain CI.
  - `docs/threat-model.md` was refreshed during release prep so it no longer describes the completed M2 controls as still missing/planned; tag push, published release notes, and fresh-install/tag validation remain external.

---

## M2 — Security baseline for sync

**Exit criteria:** Session data can be safely pushed to GitHub. Threat model documented. Adversarial redaction fixtures passing. Signed-commits path enforceable. Supply-chain scanning in CI.

### T2.1 — Formal threat model

- Status: `[~]` locally in progress
- Owner: `copilot-cli`
- Depends on: —
- Files: `docs/threat-model.md` (new)
- Outcome: Documented threat model covering session capture, redaction, push safety, cross-project leaks, credential handling, insider agents.
- Acceptance:
  - STRIDE-style analysis for each subsystem
  - Mitigations mapped to existing/planned controls
  - Reviewed and signed off by plan owner
- Notes:
  - `docs/threat-model.md` now captures the repo-specific threat register and roadmap-aligned follow-ups; keep `[~]` until sign-off/merge per plan rules.

### T2.2 — User-defined redaction patterns

- Status: `[~]`
- Owner: copilot-cli
- Depends on: —
- Files: `umx/redaction.py`, `umx/config.py` (RedactionConfig), `umx/cli.py` (`config set redaction.patterns`)
- Outcome: Users can register additional regex redaction patterns via config with validation (regex compiles, has replacement token).
- Acceptance:
  - New test: user pattern triggers on match and masks correctly
  - Invalid pattern rejected with clear error
  - Config roundtrip preserves patterns
- Notes:
  - Local implementation keeps `sessions.redaction_patterns` as the canonical config shape and adds `config set redaction.patterns <value>` as the validated CLI convenience surface.
  - Values may be a single regex string or a JSON array of regex strings; invalid or empty patterns now fail closed with a clear `RedactionError`, and custom matches are masked as `[REDACTED:custom]`.
  - To avoid regex-DoS hangs in ingest/push-safety paths, custom patterns are restricted to simple token-shape regexes; quantified groups, backreferences, lookarounds, and wildcard repeaters are rejected.

### T2.3 — Adversarial redaction fixtures

- Status: `[~]`
- Owner: copilot-cli
- Depends on: T2.2
- Files: `tests/fixtures/secrets/`, `tests/test_redaction_adversarial.py` (new)
- Outcome: Fixture corpus with real-shape-but-synthetic secrets: AWS, GCP, Azure, OpenAI, Anthropic, JWT, SSH, GitHub PAT, Stripe, Slack, PII patterns.
- Acceptance:
  - Every fixture redacts on ingest
  - Entropy-only assignment hits are masked as `[REDACTED:high-entropy]`; quarantine remains reserved for scanner failures and other hard redaction errors
  - Nothing in the fixture corpus lands unredacted in a fact or session file
- Notes:
  - Synthetic shapes only — never commit real credentials.
  - Unblocked by aligning the plan to `gitmem-spec-v0_9.md` §19 and the shipped runtime: entropy-only detections surface as `[REDACTED:high-entropy]` values for review rather than quarantining the session outright.
  - Added a synthetic fixture corpus under `tests/fixtures/secrets/` plus `tests/test_redaction_adversarial.py`, covering shipped built-ins, safe custom-pattern shapes for unsupported token families, entropy-only assignment masking, and fail-closed quarantine metadata on scanner errors.
  - The adversarial suite now also checks the extractor path so raw fixture secrets do not survive into derived fact text even when sessions are successfully written.

### T2.4 — Quarantine review UI in viewer

- Status: `[~]`
- Owner: copilot-cli
- Depends on: T2.2
- Files: `umx/viewer/server.py`, `umx/viewer/templates/quarantine.html` (new)
- Outcome: Viewer surfaces a quarantine queue with redaction reason, snippet with the suspected secret masked, and release/discard actions.
- Acceptance:
  - Quarantined session visible in viewer within 1s of write
  - Release action requires explicit confirm
  - Discard action removes from quarantine and logs decision
  - Tests covering both actions
- Notes:
  - Added a quarantine queue to the viewer with a packaged partial template, masked previews, explicit-confirm release, discard handling, and HTTP-level tests for queue visibility, confirm gating, fail-closed release, discard logging, and push-safety report exclusion.
  - Quarantined sessions now persist a `local/quarantine/<session_id>.meta.json` sidecar with the original redaction failure reason so the viewer can explain why the session was quarantined; release re-runs redaction and never publishes raw content.
  - Decisions are logged locally in `local/quarantine-decisions.jsonl`, and `umx doctor` now excludes metadata sidecars from the quarantine count.

### T2.5 — `require_signed_commits` end-to-end enforcement

- Status: `[~]`
- Owner: copilot-cli
- Depends on: —
- Files: `umx/git_ops.py`, `umx/config.py`, `umx/dream/pipeline.py`
- Outcome: When `git.require_signed_commits=True`, every memory-repo commit (local, dream, governance) is signed or aborts with a clear error. Unsigned commits rejected on `sync` push.
- Acceptance:
  - New test: unsigned commit attempt fails with actionable message when enforced
  - CI path tested with GPG stub
  - `umx doctor` surfaces missing signing config
- Notes:
  - Local implementation now enforces signed history across sync, Dream push paths, cross-project proposal push, pre-compact sync, and first remote bootstrap pushes; the bootstrap path validates full local history when the remote base does not exist yet.
  - `git pull --rebase` now enables rebase signing when commit signing is enabled so `sync` does not strip signatures before checking the outbound range.
  - `umx doctor` now surfaces signer readiness plus missing `user.name`/`user.email` whenever signed commits are enabled, not just when they are strictly required.

### T2.6 — Scope-isolation guard tests

- Status: `[~]`
- Owner: copilot-cli
- Depends on: —
- Files: `tests/test_scope_isolation.py` (new)
- Outcome: Explicit tests asserting user-memory never syncs to a project org, and project-memory never syncs to another project's org.
- Acceptance:
  - Test simulates misconfigured remote and asserts push is refused
  - Cross-project promotion requires explicit `--proposal-key` (already true — test-locks the guarantee)
- Notes:
  - Added dedicated scope-isolation coverage plus a shared GitHub remote-identity guard so project memory only pushes to the expected project repo and user memory only pushes/open-PRs against `umx-user`.
  - The guard intentionally applies only to GitHub remotes; local/non-GitHub remotes remain allowed so local bare-repo workflows and tests keep their current behavior.

### T2.7 — Supply-chain scanning in CI

- Status: `[~]` locally in progress
- Owner: `copilot-cli`
- Depends on: —
- Files: `.github/workflows/ci.yml`, `pyproject.toml`
- Outcome: CI runs `pip-audit` and generates SBOM on every PR. Failing audits block merge.
- Acceptance:
  - CI workflow green on current main
  - Known-vulnerable dep intentionally introduced in a test PR is caught
  - SBOM uploaded as workflow artifact
- Notes:
  - Local workflow update adds a PR-only `pip-audit` + CycloneDX SBOM job, runs with read-only `contents` permission, and passed a local workflow syntax sanity check; keep `[~]` until repository CI exercises it.

---

## M3 — GitHub governance GA ★

**Exit criteria:** Remote and hybrid modes marked production. Governance loop (L1 extract → L2 review → L3 approve) repeatable with minimal human touch on safe changes. Failure modes documented and recoverable.

### T3.1 — L2 reviewer: real Claude Opus integration

- Status: `[~]`
- Owner: copilot-cli
- Depends on: T1.8, T2.3
- Files: `umx/dream/l2_review.py` (new or extend existing), `umx/providers/anthropic.py` (if not present)
- Outcome: `umx dream --tier l2 --pr <n>` invokes Claude Opus with the fact-delta from the PR and produces a structured review (approve/escalate + per-fact notes).
- Acceptance:
  - Integration test against a recorded PR fixture
  - Reviewer output persisted as PR comment
  - Cost telemetry captured (tokens in/out per review)
  - Anthropic API key read from env; clear error if missing
- Notes:
  - Added `umx.dream.l2_review` plus a small `umx.providers.anthropic` client so L2 review can build a deterministic governance prompt, call Claude Opus 4.7 (`claude-opus-4-7` by default), parse structured JSON verdicts, and render a persisted PR review comment with per-fact notes.
  - `umx dream --tier l2 --pr <n>` now passes the validated governance PR body through to provider review, persists model-backed review comments, records token telemetry in the CLI payload and `meta/processing.jsonl`, and fails clearly when Anthropic is required but `ANTHROPIC_API_KEY` is missing.
  - The local slice is covered by recorded fixture tests, provider-plan tests, governance-path tests, and a fresh full-suite pass at 565 tests; live Anthropic invocation and real GitHub PR-comment persistence remain external acceptance gates.

### T3.2 — L2 reviewer eval harness

- Status: `[~]`
- Owner: copilot-cli
- Depends on: T3.1
- Files: `tests/eval/l2_reviewer/`, `tests/test_l2_eval.py`
- Outcome: Golden PR set with expected verdicts (approve / escalate / reject). Regression suite detects reviewer drift.
- Acceptance:
  - ≥20 golden PRs covering: clean extraction, conflicting fact, over-broad scope, redundant fact, taxonomy miss, suspected hallucination
  - Eval runs on demand (not every CI) with pass rate gate ≥85%
  - Prompt changes require eval re-run before merge
- Notes:
  - Added `umx eval l2-review` plus `umx.dream.l2_eval`, a 20-case golden corpus under `tests/eval/l2_reviewer/`, and harness tests that cover corpus shape, pass-rate scoring, and CLI gate behavior without forcing live Anthropic calls in normal CI.
  - The reviewer now emits prompt metadata (`prompt_id`, `prompt_version`) alongside model/usage telemetry, and the eval runner fails closed when prompt/model metadata is missing or inconsistent across cases instead of silently defaulting.
  - Local validation is complete: the eval-harness slice is green and the full local suite is green at 569 passing tests. Live-model calibration against the real Anthropic endpoint remains an external follow-up before treating the ≥85% gate as release-quality.

### T3.3 — Governance label lifecycle automation

- Status: `[~]`
- Owner: copilot-cli
- Depends on: T3.1
- Files: `umx/governance.py`, `umx/actions.py`, `umx/templates/l2-review.yml`
- Outcome: PRs auto-transition `extraction → reviewed` (after L2) → `approved` (after L3 human) → merged. Labels drive gating, not human memory.
- Acceptance:
  - Label changes happen via `gh` action, not manual
  - Misapplied labels reverted automatically
  - State machine tested
- Notes:
  - Added lifecycle labels (`state: extraction`, `state: reviewed`, `state: approved`), pure governance label reconciliation helpers, and GitHub transport helpers so L1/promotion PRs seed `state: extraction` and L2 review reconciles PRs into the next lifecycle state.
  - Hardened the local state machine after multiple review passes: promotion PRs retain `type: promotion`, governance-label reads fail closed on unknown governance-like labels, label mutation aborts if required governance labels cannot be ensured, reconciliation-failure payloads report actual current labels, approve reruns preserve `state: approved` while clearing stale `human-review`, and escalate reruns demote approved PRs back to `state: reviewed` plus `human-review`.
  - Local validation is complete: targeted governance/GitHub/cross-project slices are green and the full local suite is green at 588 passing tests. Live GitHub permission/event behavior and downstream merge enforcement remain external follow-ups for T3.5/T3.6.

### T3.4 — PR template enforcement with fact-delta block

- Status: `[~]`
- Owner: copilot-cli
- Depends on: —
- Files: `umx/templates/pr-body.md`, `umx/dream/pr_render.py` (if present), `umx/governance.py`
- Outcome: Every governance PR body contains a structured fact-delta block (added/modified/superseded/tombstoned). PRs missing the block are rejected by action.
- Acceptance:
  - Template renders deterministically from a DreamResult
  - Action rejects non-conformant PRs with actionable message
  - Backfill path for PRs opened before this feature
- Notes:
  - Added a deterministic governance PR template plus `umx.dream.pr_render` helpers for render/parse/validate, and wired them through Dream extraction, promotion previews, GitHub PR creation, and L2 review.
  - Enforcement now applies to governance branch families (`dream/*`, `proposal/*`) in addition to governance labels, so missing/mistyped labels do not silently skip fact-delta validation.
  - Legacy backfill is limited to recognized pre-T3.4 governance PR body shapes instead of trusting the legacy marker by itself, preventing current PRs from bypassing the fact-delta requirement.
  - Cross-project `--open-pr` now reuses the exact saved pushed proposal preview so the PR body cannot drift from the already-pushed branch contents.
  - Local validation is complete: targeted T3.4 slices are green and the full local suite is green at 560 passing tests; merge/CI completion remains external.

### T3.5 — Approval gating

- Status: `[~]`
- Owner: copilot-cli
- Depends on: T3.3
- Files: `umx/templates/*.yml`, branch protection docs
- Outcome: Merge blocked without `approved` label regardless of review state. Admins cannot force-merge without audit.
- Acceptance:
  - Branch protection config committed as reference
  - Test with a dummy PR without label fails to merge
  - Override path requires explicit `--force` on CLI and logs reason
- Notes:
  - Implemented the local approval-gating contract: L2 approve now blocks merge without `state: approved`; `--force` requires a nonblank `--force-reason`; override use emits the approval-override audit note and processing event; and admin merge is used only when the override path is actually consumed.
  - Added `approval-gate.yml` plus `governance-branch-protection.reference.json`, including governed-file detection via the GitHub PR files API, rename handling, and fail-closed malformed-payload behavior for the approval-gate workflow/reference lane.
  - Hardened the sequencing after repeated review: provenance push precedes label reconciliation/comment persistence, merge gating re-reads live labels before merge, demotion paths no longer reuse stale approved-label snapshots, and override audit metadata is only reported when the bypass actually succeeds.
  - Local validation is complete: targeted approval-gating coverage is green at 147 passing tests, the full local suite is green at 599 passing tests, and the final ad hoc review found no remaining blockers. Live repo ruleset/branch-protection application remains the external follow-up in T3.6.

### T3.6 — Branch protection auto-setup via `setup-remote`

- Status: `[~]`
- Owner: copilot-cli
- Depends on: T3.5
- Files: `umx/cli.py` (`setup-remote`), `umx/github_ops.py`
- Outcome: `umx setup-remote` applies branch protection (require PR, require label, require status checks) to the memory repo's main branch.
- Acceptance:
  - Idempotent (can run on already-configured repo)
  - Clear output of what was configured
  - Test against a throwaway repo in CI
- Notes:
  - Implemented the safe local scaffolding around the committed reference artifact: `umx/github_ops.py` now loads and validates `governance-branch-protection.reference.json`, builds a managed ruleset payload, paginates repository-ruleset discovery, and supports create/update/unchanged apply semantics without auto-enabling live enforcement.
  - Remote bootstrap/setup flows now surface governance-protection status explicitly after bootstrap. In the current codebase that status is intentionally `deferred`: remote sync, Dream maintenance, and other governed coordination flows still direct-push `main`, so enabling `require_pull_request` today would break remote mode or require an unsafe bypass.
  - Local validation is complete for the safe slice: targeted CLI/GitHub coverage is green at 91 passing tests, the full local suite is green at 632 passing tests, and ad hoc review found one paginated-ruleset discovery bug that was fixed before final validation. Remaining live enforcement stays blocked until governed maintenance/session sync no longer depends on direct pushes to `main`, or a dedicated non-human bypass identity exists.

### T3.7 — Concurrent PR conflict detection

- Status: `[~]`
- Owner: copilot-cli
- Depends on: T3.4
- Files: `umx/governance.py`, `umx/cross_project.py`
- Outcome: Opening a PR that touches a `fact_id` already in an open PR fails fast with a pointer to the other PR.
- Acceptance:
  - Detection works across local → remote sequence
  - Test with two concurrent PRs on same fact
  - Message includes PR URLs
- Notes:
  - Added deterministic overlap identity on top of T3.4’s fact-delta block: touched `fact_id`s are extracted from governance PR bodies, cross-project promotion previews now persist the materialized target `fact_id`, and overlap checks ignore evidence-only `source_fact_ids`.
  - Centralized overlap enforcement in `umx.github_ops.create_pr()` by listing open PRs, parsing their fact-delta payloads, and raising a structured conflict error when the new PR would touch a `fact_id` already claimed by an open governance PR.
  - Surfaced the same conflict through Dream and cross-project flows: Dream preflights overlap before push for GitHub remotes while still rechecking inside `create_pr()`, and cross-project `--open-pr` now returns the conflicting PR URL cleanly instead of failing generically.
  - Local validation is complete: targeted overlap/governance/cross-project slices are green at 181 passing tests, the full local suite is green at 621 passing tests, and ad hoc review found no remaining blockers. Remaining race-free server-side enforcement belongs to later GitHub-side follow-up work.

### T3.8 — Governed rollback / tombstone PR

- Status: `[~]`
- Owner: copilot-cli
- Depends on: T3.5
- Files: `umx/cli.py` (`forget --governed`), `umx/fact_actions.py`, `umx/governance.py`, `umx/dream/pr_render.py`
- Outcome: `gitmem forget <fact_id> --governed` opens a PR that tombstones the fact rather than mutating direct. Rollback equivalent opens a reverse PR.
- Acceptance:
  - Tombstone PR passes label gating
  - Direct write in governed mode still blocked (existing behavior)
  - Test: forget → PR → approve → merge removes fact from read paths
- Notes:
  - Added an explicit governed path for single-fact forgets: plain governed `forget` remains blocked, while `forget --fact <id> --governed` now creates a proposal branch from `main`, applies the existing tombstone mutation on that branch, and opens a deletion-labeled governance PR with a `tombstoned` fact-delta entry.
  - Kept the data-plane mutation model unchanged by reusing `umx.tombstones.forget_fact()`; after merge the fact disappears from active read paths and the tombstone persists in `meta/tombstones.jsonl`, so gather/rederive/audit suppression behavior stays aligned with existing local forget semantics.
  - Hardened the governed branch path after review: failed branch materialization now restores `main` cleanly instead of leaking staged tombstone edits back onto the default branch, and tombstone fact-delta entries now require `fact_id` so concurrent deletion PRs participate in overlap detection.
  - Local validation is complete for the shipped slice: targeted PR-render/governance/commands/viewer coverage is green at 117 passing tests, the full local suite is green at 637 passing tests, and ad hoc review found no remaining blockers after the cleanup/identity fixes. `--topic --governed` and an explicit reverse-PR rollback CLI remain follow-up work.

### T3.9 — `gh` CLI retry & rate-limit handling

- Status: `[~]`
- Owner: copilot-cli
- Depends on: —
- Files: `umx/github_ops.py` (`_run_gh`)
- Outcome: Transient failures retry with backoff; rate-limit responses pause and retry; permanent failures surface clearly.
- Acceptance:
  - Retry policy configurable
  - Test against a mocked gh that returns 429/5xx
  - User-facing error on final failure includes next steps
- Notes:
  - Added a conservative retry kernel in `umx.github_ops._run_gh` with env-tunable policy (`UMX_GH_*`), explicit rate-limit/transient/permanent failure classification, deterministic backoff, and retry coverage for safe idempotent `gh` operations (`auth status`, `repo view`, `pr view`, `label create --force`, and label-only `pr edit`).
  - Retry handling now recognizes common transport failures including `429`, `5xx`, connection resets, and `EOF`; retry-exhausted failures emit actionable next-step guidance instead of collapsing into silent `False` results on auth/repo probes.
  - Wrapped the higher-level propagation points so the new error detail reaches users: `gh_available()` only returns `False` for genuine auth/unavailable cases, `repo_exists()` only returns `False` for clear not-found responses, remote setup surfaces `GitHubError` as `ClickException`, cross-project `--open-pr` preserves retry guidance, and Dream PR opening stores `GitHubError` in `_push_block_reason` instead of leaking it.
  - Local validation is complete: direct `github_ops` retry coverage and affected CLI/cross-project/governance slices are green, and the full local suite is green at 616 passing tests. Non-idempotent write commands (`pr create/comment/merge/close`, `repo create`) remain single-attempt by design to avoid ambiguous duplicate side effects.

### T3.10 — `health --governance` surface

- Status: `[~]`
- Owner: copilot-cli
- Depends on: T3.3
- Files: `umx/governance_health.py`, `umx/git_ops.py`, `umx/cli.py`, `umx/viewer/server.py`
- Outcome: `gitmem health --governance` outputs open PRs, stale branches, reviewer queue depth, last L2 run, label drift.
- Acceptance:
  - JSON and human output
  - Viewer panel renders the same data
  - Tests covering empty/healthy/degraded states
- Notes:
  - Added a shared read-only governance health builder in `umx/governance_health.py` so the CLI and viewer consume the same payload for open governance PRs, queue depth, stale local `dream/` / `proposal/` branches, last L2 review completion, and invariant-based label drift.
  - Extended `gitmem health` with an explicit `--governance` path plus `--format json|human`, preserved the default health JSON surface, and rendered the same governance data in the viewer via a dedicated Governance Health panel.
  - Hardened the shared payload after ad hoc review: stale-branch checks now fail closed when PR inventory is unavailable, malformed governance PR bodies surface as warnings/errors instead of false healthy state, and drifted lifecycle labels no longer inflate queue depth.
  - Local validation is complete: affected governance-health/CLI/viewer/git slices are green at 63 passing tests, the full local suite is green at 648 passing tests, and no remaining local blockers were found in review.

### T3.11 — Cross-org support + credential rotation path

- Status: `[ ]`
- Owner: —
- Depends on: T3.1, T3.6
- Files: `umx/config.py`, `umx/github_ops.py`, `docs/ops-runbook.md`
- Outcome: Memory repos can live in a different org from the reviewer's credentials; rotation procedure documented.
- Acceptance:
  - Multi-org config round-trips
  - Rotation doc walks through revoking a PAT and swapping without data loss
  - Test: swap remote mid-flight doesn't lose in-flight PRs
- Notes:

---

## M4 — Scale & performance

**Exit criteria:** Sub-second inject at 10k-fact stores; ingest handles bursty capture; benchmarks committed and tracked.

### T4.1 — Benchmark suite

- Status: `[~]`
- Owner: copilot-cli
- Depends on: —
- Files: `benchmarks/` (new)
- Outcome: Reproducible benchmarks: 10k facts × 100k sessions. Measures: ingest throughput, inject p50/p95 latency, dream cycle wall clock.
- Acceptance:
  - Fixture generator commits small seed data, expands to scale in-memory
  - Benchmarks run with `pytest benchmarks -q`
  - Baseline numbers recorded in `benchmarks/RESULTS.md`
- Notes:
  - Added a stdlib-only pytest benchmark harness under `benchmarks/` with deterministic seed data, runtime expansion to 10k facts / 100k sessions, terminal-summary reporting, and dedicated ingest / inject / dream measurement paths.
  - Benchmarks now run via `pytest benchmarks -q` without changing the repo’s default `pytest -q` surface; the suite uses isolated `UMX_HOME`, fixed git author/committer env, disabled dream provider rotation, and `dream.lint_interval=never` to keep runs reproducible.
  - `benchmarks/RESULTS.md` now tracks the current default-scale local baseline for this branch head; use it as the measurement source of truth for T4 follow-up tuning instead of duplicating raw numbers elsewhere in the plan.

### T4.2 — FTS5 tuning

- Status: `[x]`
- Owner: copilot-cli
- Depends on: T4.1
- Files: `umx/search.py`, `umx/cli.py`, `benchmarks/test_index.py`
- Outcome: Safe source-file keyed incremental index refresh with benchmarked rebuild speedups and measured sublinear index growth at benchmark scale.
- Acceptance:
  - Inject p95 at 10k facts <200ms
  - Index file size grows sublinearly with fact count
  - Rebuild-index 10x faster than pre-change
- Notes:
  - Landed the first safe local tuning slices without changing retrieval semantics: batched injection usage writes into a single transaction, memoized SQLite schema/bootstrap setup, added process-local parsed-fact caches, removed duplicate gather-time lexical scoring, deduped targeted scoped facts, and cached the gathered base inventory used by inject.
  - Follow-up tuning landed the deeper structural and persistence cuts: index-backed project candidate preselection with mandatory scoped/task/refresh/handoff unions, config parsing cache reuse, project-fingerprint reuse between gather and FTS readiness checks, anonymous injection telemetry collapsing to usage counters, and hot SQLite connection reuse for the inject path.
  - Closed the remaining CLI-safe refresh gap by storing indexed source paths, falling back to a full rebuild when schema/index metadata is stale or unreadable, and making plain `rebuild-index` default to the incremental refresh path while `--embeddings` still forces a full rebuild.
  - Revalidated correctness after each slice, including same-topic cross-scope refresh safety and legacy-index fallback coverage found during ad hoc review; the current local green baseline is `python3 -m pytest -q` → 667 passed and `python3 -m pytest benchmarks -q` → 5 passed.
  - Current benchmark state at default scale: ingest throughput measured 22.263 / 23.17 sessions/s, inject measured 114.019 / 122.264 ms, full rebuild measured 5698.476 / 5809.321 ms, incremental refresh measured 508.483 / 517.024 ms (11.207x faster), and dream wall clock measured 81302.942 ms.
  - Index growth is now proven sublinear at benchmark scale: `meta/index.sqlite` grew 9.181x across 1k→10k facts with a log-log slope of 0.963 and bytes/fact dropping from 655.36 to 601.702.

### T4.3 — Embedding backend abstraction

- Status: `[~]`
- Owner: copilot-cli
- Depends on: —
- Files: `umx/search_semantic.py`, `umx/providers/embeddings.py` (new)
- Outcome: Pluggable embedding backends — local `sentence-transformers`, OpenAI, Anthropic (if available), Voyage — selectable via config.
- Acceptance:
  - Each backend tested with a fixture
  - Switching backend triggers embedding rebuild with clear message
  - Model version recorded per fact so a future rebuild is lossless
- Notes:
  - Landed the thin first slice instead of the whole provider matrix at once: `search.embedding.provider` now resolves through a new `umx/providers/embeddings.py` seam, with the current production path preserved as `sentence-transformers` plus a deterministic `fixture` backend for regression coverage.
  - `.umx.json` now records both a repo-level `embedding_config` signature and per-fact `embedding_provider` / `embedding_model` / `embedding_model_version` metadata so provider/model/version drift is explicit and lossless.
  - Hybrid search and Dream prewarm no longer silently create mixed caches after a provider/model switch: when the stored signature differs from config, semantic reranking falls back to lexical-only behavior and `doctor` / `search` surface a clear `umx rebuild-index --embeddings` message.
  - The local slice is covered by new backend/switch tests, updated CLI/doctor expectations, the legacy-cache upgrade regression noted during ad hoc review, and a fresh full-suite pass at `python3 -m pytest -q` → 705 passed. Keep `[~]` until merge/CI per plan rules.

### T4.4 — Budget-aware inject retuned

- Status: `[~]`
- Owner: copilot-cli
- Depends on: T4.1, T4.2
- Files: `umx/inject.py`, `umx/config.py`, `docs/config.md`, `tests/test_inject_golden.py`, `tests/eval/inject/cases.json`
- Outcome: Inject defaults retuned against benchmark data; progressive disclosure thresholds backed by measurements.
- Acceptance:
  - New defaults documented in `docs/config.md`
  - Injection produces the same top-N under the new thresholds on the golden corpus
- Notes:
  - Landed the narrow retune slice instead of broad default unification: `inject.pre_tool_max_tokens` now defaults to `1400`, `inject.disclosure_slack_pct` now defaults to `0.20`, and `_disclosure_levels()` clamps and uses the configurable slack instead of a hardcoded reserve.
  - Added `docs/config.md` as the first focused config reference and documented the new inject/session cadence defaults there without changing manual `--max-tokens` override surfaces.
  - Added an injection golden corpus under `tests/eval/inject/cases.json` plus `tests/test_inject_golden.py`; the corpus now proves the new disclosure threshold preserves the same top-N fact ordering as the previous `0.30` slack on representative project and file-scoped cases, while `tests/test_inject_search.py` covers the intended near-threshold L1-vs-L0 disclosure change directly.
  - Local validation is complete at `python3 -m pytest -q` → 681 passed, and a final standalone `python3 -m pytest benchmarks/test_inject.py -q` sanity rerun measured `111.201 / 127.133 ms` p50/p95 at 10k facts; keep `[~]` until merge/CI per plan rules.

### T4.5 — Parallel capture ingestion

- Status: `[~]`
- Owner: copilot-cli
- Depends on: T4.1
- Files: `umx/cli.py`, `umx/claude_code_capture.py`, `umx/gemini_capture.py`, `umx/amp_capture.py`
- Outcome: Importing a batch of transcripts parallelizes redaction and write.
- Acceptance:
  - 10x transcript batch imports in <3x the time of one
  - No data races; redaction order-independent
  - Test with deliberately interleaved writes
- Notes:
  - Landed the first safe slice instead of naive concurrent writes: `capture claude-code --all`, `capture gemini --all`, and `capture amp --all` now parallelize preparation only via a bounded thread pool, while persistence stays serial and still ends with one final batch commit.
  - Each included backend now exposes a `prepare_*` helper so parse/metadata assembly can run concurrently without duplicating logic; `_persist_prepared_capture_batch()` keeps `write_session(..., auto_commit=False)` on the main thread, so this slice does not widen the current write-session race surface.
  - `capture opencode --all` is intentionally deferred in this first landing because its list path already materializes DB-backed session payloads up front, so the same prep-only pattern would add complexity for less benefit.
  - Added new CLI `--all` tests for Claude Code, Gemini, and Amp that use a barrier to prove prep actually runs concurrently, preserve output order, and assert only one final commit for the batch; local validation is complete at `python3 -m pytest -q` → 701 passed. Keep `[~]` until merge/CI per plan rules.

### T4.6 — Archive compaction schedule

- Status: `[~]`
- Owner: copilot-cli
- Depends on: —
- Files: `umx/config.py`, `umx/sessions.py`, `umx/hooks/session_end.py`, `umx/dream/pipeline.py`
- Outcome: Session archive compaction runs on a config-driven cadence, persists its last-run timestamp in `.umx.json`, and keeps archived sessions searchable through the existing archive index/decompression path.
- Acceptance:
  - Configurable cadence (`daily`/`weekly`/`monthly`/`never`)
  - Hook into session-end and Dream completion paths without breaking manual `umx archive-sessions`
  - Test verifies archived sessions still searchable
- Notes:
  - Added `sessions.archive_interval` with cadence state stored in local-only `.umx.json` under `sessions.last_archive_compaction`, so scheduled compaction stays repo-local and survives process restarts.
  - `scheduled_archive_sessions()` now drives the schedule, `session_end` runs it even when no new events were written, and Dream local/hybrid completions call the same helper after marking the cycle complete.
  - Archived sessions remain searchable because session search still reads archived payloads via the monthly index plus on-demand decompression; the explicit `umx archive-sessions` CLI remains the manual override.
  - Local implementation and validation are complete at `python3 -m pytest -q` → 675 passed; keep `[~]` until merge/CI per plan rules.

---

## M5 — Ops & docs

**Exit criteria:** A user can install, operate, upgrade, and recover gitmem without direct support.

### T5.1 — Migration framework

- Status: `[~]`
- Owner: copilot-cli
- Depends on: —
- Files: `umx/migrations/` (new), `umx/memory.py`, `umx/cli.py` (`migrate`), `umx/doctor.py`, `umx/status.py`, `umx/metrics.py`
- Outcome: Every fact file carries a file-header `schema_version`. `umx migrate` applies ordered migrations; `umx doctor` warns on stale versions.
- Acceptance:
  - Seed migration `0001_initial.py` bumps all existing facts to current version
  - Test: migrate from synthetic v0 store to current
  - Idempotent rerun is a no-op
- Notes:
  - Fact topic files now use an explicit header shape (`# <topic>`, `schema_version: 1`, blank line, `## Facts`), and append/write paths upgrade missing headers in place so new writes converge on the current file schema.
  - Added a dedicated fact-file migration runner under `umx/migrations/` with seed migration `0001_initial`, rollback on partial failure, idempotent reruns, and clear refusal when a repo contains future file-schema versions.
  - `umx migrate` is governed by the same direct-write guard as other mutating local commands, while `umx doctor` now reports `fact_file_schema` issues but keeps `doctor --fix` scoped to the existing repo-level schema repair path.
  - `umx status` and metrics now load facts with `normalize=False` so read-only surfaces expose migration debt instead of silently rewriting legacy files; local validation is complete at `python3 -m pytest -q` → 688 passed, and ad hoc review found no significant issues. Keep `[~]` until merge/CI per plan rules.

### T5.2 — Export / import full

- Status: `[~]`
- Owner: copilot-cli
- Depends on: T5.1
- Files: `umx/cli.py` (`export`, `import --full`), `umx/backup.py` (new)
- Outcome: `umx export --out <dir>` produces a portable backup (facts + sessions + index). `umx import --full <dir>` restores.
- Acceptance:
  - Round-trip preserves all fact IDs, timestamps, supersession chains
  - Test verifies bit-equivalence of round-tripped fact files
  - Safe refusal if target already has data (requires `--force`)
- Notes:
  - Added `umx/backup.py` with a self-contained backup bundle format: `umx export --out <dir>` now writes a root `backup-manifest.json` plus a `snapshot/` subtree containing the raw memory-repo bytes, so restore never has to parse/reserialize fact files or sessions.
  - The bundle is intentionally full-repo scope except `.git`: it preserves fact/topic files, sessions and session archives, `meta/` state (including SQLite index/usage DBs and WAL sidecars), `.umx.json`, local/private/secret/quarantine content, and awkward edge cases like a real repo file named `backup-manifest.json`.
  - `umx import --full <dir>` keeps the legacy adapter import path intact, adds `--dry-run`/`--force` handling for full restores, and now rejects invalid manifest paths, truncated bundles, symlinked snapshot roots or ancestors, and source/target overlap before any forced clear can touch the target repo.
  - Added `tests/test_backup.py` covering raw byte round-trips, CLI export/import JSON, refusal without `--force`, truncated-bundle preflight, manifest path escape, symlinked snapshot parent/root rejection, and overlap-source protection; local validation is complete at `python3 -m pytest -q` → 698 passed. Keep `[~]` until merge/CI per plan rules.

### T5.3 — Multi-machine sync test matrix

- Status: `[ ]`
- Owner: —
- Depends on: T3.11
- Files: `tests/test_multi_machine.py` (new)
- Outcome: Matrix: 2 machines × {local, hybrid, remote} × {user, project} scopes — verify consistent state after sync.
- Acceptance:
  - Matrix runs in CI against a test org
  - Conflict scenarios documented (branch divergence, parallel dreams)
  - Resolution steps in runbook
- Notes:

### T5.4 — Opt-in telemetry

- Status: `[ ]`
- Owner: —
- Depends on: —
- Files: `umx/telemetry.py` (new), `umx/config.py`
- Outcome: Anonymous opt-in metrics (errors, latency, fact counts, feature usage). Off by default; enabled with `telemetry.enabled=true`.
- Acceptance:
  - Zero network calls when disabled
  - Privacy doc enumerates what is and isn't sent
  - Kill switch honored
- Notes: Consider whether this is worth building pre-1.0. Skip if the beta cohort is small enough for direct feedback.

### T5.5 — Docs site

- Status: `[ ]`
- Owner: —
- Depends on: T1.5
- Files: `docs/`, `mkdocs.yml` (new)
- Outcome: mkdocs-material site with: quickstart, concepts, CLI ref, ops runbook, threat model, FAQ, governance tutorial.
- Acceptance:
  - Site builds in CI
  - Published on gh-pages
  - Quickstart runnable end-to-end against a fresh environment
- Notes:

### T5.6 — Generated API reference

- Status: `[ ]`
- Owner: —
- Depends on: T5.5
- Files: `docs/api/`, `mkdocs.yml`
- Outcome: API reference generated from docstrings (mkdocstrings or similar). Covers public modules.
- Acceptance:
  - Internal `_private` names excluded
  - Build fails on missing docstring for public API
- Notes:

### T5.7 — 0.9 → 1.0 upgrade guide

- Status: `[~]`
- Owner: copilot-cli
- Depends on: T5.1, T5.2
- Files: `docs/upgrade-0.9-to-1.0.md`
- Outcome: Step-by-step upgrade covering config format changes, schema bumps, breaking CLI flag changes (if any).
- Acceptance:
  - Dogfood: upgrade plan owner's personal gitmem and document friction
  - Rollback path included
- Notes:
  - Added `docs/upgrade-0.9-to-1.0.md` as a branch-head upgrade runbook covering pre-upgrade backups, installation, repo-level `doctor --fix` versus fact-file `umx migrate`, optional config additions, post-upgrade validation, and rollback.
  - The guide is explicit about the current surfaces rather than hand-wavy future intent: `umx export` / `umx import --full` are project-scoped today, user-scope backup still needs a manual copy of `~/.umx/user`, and `umx migrate` remains a local direct-write command that refuses governed `remote` / `hybrid` modes.
  - Current branch-head guidance says there is no mandatory config key rename in the `0.9.x -> 1.0` path and no breaking replacement for `umx import --adapter ...`; the new backup/restore flags are additive.
  - The guide was validated against the shipped CLI/config surfaces after the T4.5 branch-head revalidation at `python3 -m pytest -q` → 701 passed. Keep `[~]` until merge/CI per plan rules.

---

## M6 — Private beta

**Exit criteria:** 5–10 real teams running gitmem for ≥2 weeks each. All P0/P1 bugs closed. Launch checklist signed off.

### T6.1 — Recruit beta cohort

- Status: `[ ]`
- Owner: —
- Depends on: T5.5, T5.7
- Outcome: 5–10 teams committed. Mix of solo devs and small teams. At least 2 teams using remote mode seriously.
- Acceptance:
  - Signed-off participation from each team
  - Onboarding call completed
  - Beta license/terms accepted
- Notes:

### T6.2 — Feedback intake

- Status: `[ ]`
- Owner: —
- Depends on: T6.1
- Files: External — issues repo or Linear project
- Outcome: Weekly office hours; intake channel; triage cadence; public roadmap reflects beta input.
- Acceptance:
  - Every issue triaged within 3 business days
  - P0 response within 24h
- Notes:

### T6.3 — Telemetry review

- Status: `[ ]`
- Owner: —
- Depends on: T5.4, T6.1
- Outcome: Weekly review of telemetry data. Issues surfaced from telemetry tracked and resolved.
- Acceptance:
  - Review cadence documented
  - Minimum 3 insights fed back into bug list or roadmap
- Notes: Skip entire task if T5.4 was deferred.

### T6.4 — Bug bash

- Status: `[ ]`
- Owner: —
- Depends on: T6.1
- Outcome: Scheduled bug bash sessions (≥2) with beta cohort. Focus areas documented.
- Acceptance:
  - Each bash produces a tracked bug list
  - P0/P1 fixed before next milestone
- Notes:

### T6.5 — Launch checklist sign-off

- Status: `[ ]`
- Owner: —
- Depends on: T6.1, T6.2, T6.3, T6.4
- Files: `docs/launch-checklist.md`
- Outcome: All P0/P1 bugs closed; docs reviewed; license finalized; pricing/positioning decided (if applicable); announcement drafted.
- Acceptance:
  - Each item explicitly signed off by plan owner
  - No open P0/P1 bugs
- Notes:

---

## M7 — GA 1.0.0

**Exit criteria:** Gitmem 1.0.0 published with GitHub sync as the headline feature.

### T7.1 — Version bump 1.0.0

- Status: `[ ]`
- Owner: —
- Depends on: T6.5
- Files: `pyproject.toml`, `umx/__init__.py` (if version exposed)
- Notes:

### T7.2 — CHANGELOG finalization

- Status: `[ ]`
- Owner: —
- Depends on: T7.1
- Files: `CHANGELOG.md`
- Outcome: Consolidated 0.9.x→1.0.0 changelog with the governance story prominent.
- Notes:

### T7.3 — Announcement

- Status: `[ ]`
- Owner: —
- Depends on: T7.1
- Files: External — blog, social
- Outcome: Blog post framed around "memory that reviews itself" — GitHub sync + L2 review as the story.
- Notes:

### T7.4 — Docs site publication

- Status: `[ ]`
- Owner: —
- Depends on: T5.5, T7.1
- Outcome: Docs site live at final URL; quickstart proven against 1.0.0 install.
- Notes:

### T7.5 — Community channels

- Status: `[ ]`
- Owner: —
- Depends on: T7.1
- Outcome: GitHub Discussions opened; issue templates in place; CoC published.
- Notes:

---

## Decisions

Record architectural and scope choices made during the build. Agents: add a new entry whenever a task forces a choice that future agents should not relitigate.

Format:

```
### D<n> — <title> (YYYY-MM-DD, <agent>)
- Context: what triggered the decision
- Options considered: A, B, C
- Choice: B
- Rationale: why
- Reversibility: easy / costly / one-way
```

### D1 — Keep GitHub-oriented modules flat (2026-04-17, copilot-cli)
- Context: T1.3 exposed a mismatch between spec §25 and the current code layout for governance/GitHub modules.
- Options considered: move `governance.py`, `github_ops.py`, `cross_project.py`, `audit.py`, and `actions.py` into `umx/gitmem/`; keep the current flat `umx.*` layout and update the spec.
- Choice: keep the current flat `umx.*` layout.
- Rationale: the codebase and tests already import the flat modules directly, so moving them would create broad churn for little M1 value. Future vendor-pluggability can be introduced behind explicit interfaces without renaming the package now.
- Reversibility: easy

### D2 — Align the canonical CLI surface to the shipped implementation (2026-04-17, copilot-cli)
- Context: T1.5 found several draft-only spec flags that never landed (`audit --all --model`, `sync --all`, `rebuild-index --force`, `view --scope`, `import --tool`) alongside shipped code-only surfaces (`health`, `setup-remote`, `archive-sessions`, `init-actions`, `mcp`, `capture`, `hooks`, `bridge`, `shim`).
- Options considered: add placeholder code for the draft flags; leave the divergence undocumented; update the spec and parity matrix to the shipped CLI.
- Choice: update the spec and parity matrix to the shipped CLI.
- Rationale: this is the lowest-risk M1 path and documents the interface users can rely on today without inventing untested flag stubs.
- Reversibility: easy

---

## Risks & open questions

Track here. Agents: move items into tasks when they become actionable; delete when resolved.

- **L2 reviewer cost unknown** — token spend per PR is not yet measured. Gate on T3.1 telemetry.
- **Beta cohort recruitment** — how are we sourcing users? Need plan by end of M5.
- **Pricing / commercial model** — undecided. Affects M7 announcement framing.
- **LTS policy** — 1.0 support window? Affects upgrade guide scope.
- **Competing standards** — is there an MCP-native memory standard emerging that we should track?

---

## Progress Log

Append-only. Most recent at top. Historical entries keep the validation counts that were true when each slice landed; the latest branch-head baseline is the most recent entry above.

- 2026-04-20 [T4.3] copilot-cli: added a thin embedding-provider seam with provider-aware cache metadata, blocked mixed-cache lazy refresh on provider/model/version drift, surfaced `umx rebuild-index --embeddings` guidance through search/doctor, and revalidated the branch at 705 passing tests
- 2026-04-20 [T5.7] copilot-cli: added a concrete `docs/upgrade-0.9-to-1.0.md` runbook covering backup, doctor/migrate sequencing, optional config additions, validation, and rollback, aligned to the current branch-head CLI/config surfaces after the 701-test baseline
- 2026-04-20 [T4.5] copilot-cli: added bounded parallel prep for `capture --all` on Claude Code, Gemini, and Amp while keeping `write_session` serial and the final batch commit singular, then revalidated the branch at 701 passing tests
- 2026-04-20 [T5.2] copilot-cli: added a self-contained raw-copy backup bundle (`backup-manifest.json` + `snapshot/`) plus `umx export` / `umx import --full`, preserved the legacy adapter import path, hardened restore preflight against truncation/symlink/overlap hazards, and revalidated the branch at 698 passing tests
- 2026-04-20 [T5.1] copilot-cli: added fact-file schema headers plus governed `umx migrate` and doctor reporting, kept repo-level schema repair separate, stopped read-only status/metrics paths from silently normalizing legacy files, and revalidated the branch at 688 passing tests
- 2026-04-20 [T4.4] copilot-cli: retuned inject with a 1400-token pre-tool default and configurable disclosure slack, added `docs/config.md` plus an injection golden corpus, and revalidated the branch at 681 passing tests with a standalone inject benchmark sanity rerun at 111.201 / 127.133 ms
- 2026-04-20 [T4.6] copilot-cli: added config-driven archive cadence state in `.umx.json`, ran scheduled compaction from session-end and local/hybrid Dream completion, kept archived sessions searchable, and revalidated the branch at 675 passing tests
- 2026-04-20 [T4.2] copilot-cli: hardened incremental rebuild with source-path tracking and stale-index fallbacks, made plain `rebuild-index` use the safe refresh path, added benchmark coverage for rebuild speedup and index-size growth, and closed the remaining local T4.2 acceptance items at 667 passing tests / 5 benchmark tests
- 2026-04-18 [T4.2] copilot-cli: cleared the inject-latency target at default benchmark scale by driving `build_injection_block` down to 105.286 / 142.366 ms p50/p95 through bounded project candidate selection and hot-path SQLite telemetry reductions, while leaving the rebuild-speed and index-growth acceptance work explicitly open
- 2026-04-18 [T4.2] copilot-cli: reduced default-scale inject latency from 6315.741 / 6578.679 ms to 251.498 / 328.48 ms via batched injection telemetry and hot-path fact caching, revalidated the full suite at 655 passing tests, and kept the benchmark suite green while leaving the final `<200 ms` candidate-selection redesign for follow-up
- 2026-04-18 [T4.1] copilot-cli: added a reproducible pytest benchmark suite with deterministic seed expansion, recorded first local ingest/inject/dream baselines in `benchmarks/RESULTS.md`, and validated the benchmark command at default scale
- 2026-04-18 [T3.10] copilot-cli: added a shared governance-health payload for CLI/viewer, surfaced `health --governance` JSON and human output with fail-closed PR inventory/body handling, and revalidated the full suite at 648 passing tests
- 2026-04-18 [T3.8] copilot-cli: added explicit single-fact `forget --governed` tombstone PRs with deletion labels, clean branch restoration on failure, and tombstone fact-id enforcement for overlap detection, and revalidated the full suite at 637 passing tests
- 2026-04-18 [T3.6] copilot-cli: added deferred remote-mode governance-protection planning/apply plumbing, paginated ruleset discovery, and truthful post-bootstrap status output, and revalidated the full suite at 632 passing tests; live PR-required enforcement remains blocked on current direct-main sync/maintenance flows
- 2026-04-18 [T3.7] copilot-cli: added fact-id overlap detection for open governance PRs, propagated materialized promotion fact IDs into PR bodies, surfaced conflicting PR URLs through Dream and cross-project flows, and revalidated the full suite at 621 passing tests
- 2026-04-18 [T3.9] copilot-cli: added env-tunable gh retry/backoff with 429/5xx/EOF handling, preserved next-step error surfacing through remote setup and cross-project PR flows, and revalidated the full suite at 616 passing tests
- 2026-04-18 [T3.5] copilot-cli: implemented local approval gating with audited force overrides, committed approval-gate/ruleset reference artifacts, closed stale-label race handling, and revalidated the full suite at 599 passing tests
- 2026-04-18 [T3.3] copilot-cli: implemented governance label lifecycle automation with fail-closed label reads, promotion-label preservation, monotonic rerun transitions, and revalidated the full suite at 588 passing tests
- 2026-04-18 [T3.2] copilot-cli: added the on-demand L2 eval harness with a 20-case golden corpus, prompt/version tracking, fail-closed metadata checks, and revalidated the full suite at 569 passing tests
- 2026-04-18 [T3.1] copilot-cli: added Anthropic-backed L2 review with structured verdict parsing, persisted review comments, token telemetry, clear required-key failures, and revalidated the full suite at 565 passing tests
- 2026-04-18 [T3.4] copilot-cli: enforced deterministic fact-delta governance PR bodies across creation/review paths, tightened legacy backfill, anchored cross-project PR opening to saved pushed previews, and revalidated the full suite at 560 passing tests
- 2026-04-17 [T1.8] copilot-cli: prepared the local 0.9.2 release slice with bumped runtime versions plus truthful changelog/threat-model updates; tag publication remains external
- 2026-04-17 [T2.3] copilot-cli: aligned entropy-only redaction semantics to the spec and added an adversarial synthetic secret corpus with extractor/session fail-closed coverage
- 2026-04-17 [T2.4] copilot-cli: added viewer quarantine queue, metadata sidecars, confirmed release/discard actions, and local quarantine decision logging
- 2026-04-17 [T2.5] copilot-cli: closed signed-history enforcement across sync/bootstrap/governance push paths and revalidated the full suite at 521 passing tests
- 2026-04-17 [T2.6] copilot-cli: added GitHub remote-identity guards plus dedicated scope-isolation coverage for sync and cross-project proposal push paths
- 2026-04-17 [T2.2] copilot-cli: added validated `config set redaction.patterns` wiring plus fail-closed custom-pattern coverage across CLI/runtime/session writes
- 2026-04-17 [T2.7] copilot-cli: added PR-only pip-audit and CycloneDX SBOM CI job with artifact upload
- 2026-04-17 [T1.7] copilot-cli: landed merge/tombstone corner-case coverage and revalidated the full suite at 499 passing tests
- 2026-04-17 [T2.1] copilot-cli: drafted repo-specific STRIDE threat model for capture, redaction, sync, governance, scope isolation, and agent misuse surfaces
- 2026-04-17 [T2.7] copilot-cli: started CI supply-chain update; wiring PR-only pip-audit plus CycloneDX SBOM artifact
- 2026-04-17 [T1.5] copilot-cli: added CLI parity matrix and aligned spec surface to shipped commands/flags
- 2026-04-17 [T1.3] copilot-cli: kept governance/GitHub modules flat and updated spec §25
- 2026-04-17 [T1.6] copilot-cli: added Hypothesis property coverage for Fact dict/markdown round-trips
- 2026-04-17 [T1.4] copilot-cli: landed slug-collision prompting, auto-suffix mode, and slug validation after delegated implementation + review
- 2026-04-17 [T1.2] copilot-cli: prewarmed hybrid embeddings against final persisted fact IDs and added fallback warning coverage
- 2026-04-17 [T1.1] copilot-cli: wired lint cadence, force-lint, and .umx.json timestamp tracking
- 2026-04-17 [plan] claude-opus-4-7: initial plan document created.
