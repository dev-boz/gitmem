# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Cross-scope promotion to `user`, `project`, and `principle` memory via `umx promote --to ...`
- Cross-project governance flow: `umx audit --cross-project`, proposal previews, local proposal-branch materialization, guarded `--push`, and explicit `--open-pr`
- Manual session capture via `umx collect` and first-class Amp transcript capture via `umx capture amp`
- Claude Code live-hook workflow helpers under `umx hooks claude-code` for install, export, session-start injection, pre-tool procedures, pre-compact sync, and session-end capture
- Richer viewer read surfaces: task board, tombstones, audit view, session browser, and conventions display
- Safe `aip-mem` compatibility entrypoint via `umx.aip`
- Experimental L2 PR review wiring for `umx dream --tier l2 --pr <number>`
- Shared status/health/doctor surfaces, calibration guidance, schema repair, and push-safety guardrails for governed flows

### Changed
- README now documents live Claude hooks, manual collect, Amp/Gemini/OpenCode capture, cross-project governance commands, signed commits, richer viewer surfaces, `aip-mem`, and the current experimental status of remote/hybrid governance
- Remote/hybrid `pre_compact` now syncs session files without pushing fact-file changes straight to `main`
- L2 workflow templates and governance labels now align on `type: extraction`

### Fixed
- `umx promote` now rejects invalid destinations before mutating state and safely handles same-repo promotions
- Lint and `umx doctor` now flag orphaned `files/` and `folders/` scoped memory when project paths disappear
- `umx doctor --fix` now repairs missing/stale schema markers, and Dream now refuses to process missing or unsupported repo schemas silently
- Cross-project proposal publication now prevents local-main leakage, redacts credentialed remotes, avoids leaking local absolute paths, and supports retryable PR-open for already-pushed proposal branches
- L2 review now evaluates fact-level deltas safely: resolved conflicts no longer falsely escalate, weak in-place supersessions stay non-destructive, and strong same-ID rewrites escalate for human review
- Full test suite coverage now stands at 477 passing tests

## [0.9.1-alpha] - 2026-04-12

### Added
- Core dream pipeline: extract → consolidate → lint → prune → save
- Codex capture: import Codex CLI rollout transcripts as sessions
- FTS5-indexed search with budget-aware injection
- Session management with hooks (session_start, session_end, pre_compact, etc.)
- Identity system for agent fingerprinting
- Convention detection and enforcement
- MCP server for tool integration
- Bridge sync between memory stores
- Viewer web UI for browsing memory
- GitHub integration (experimental): remote and hybrid modes via `gh` CLI
  - `init --mode remote|hybrid` creates repos on GitHub org
  - `dream` pushes branches and opens PRs in remote mode
  - L1/L2 governance with workflow templates
- 239 tests (unit, integration, golden extraction)
- Spec document: gitmem-spec-v0_9.md

### Changed
- Remote/hybrid bootstrap now pushes the initial `main` branch for memory repos
- Dream PR branches now exclude session-history diffs and carry the memory snapshot instead
- Mutating CLI commands now commit their repository changes by default

### Architecture
- Three modes: local (direct write), remote (all via PR), hybrid (mixed)
- Scope hierarchy: user → tool → project → folder → file
- Encoding strength 1-5 with composite scoring
- Git-native storage with markdown fact files
