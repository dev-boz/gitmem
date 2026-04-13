# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Cross-scope promotion to `user`, `project`, and `principle` memory via `umx promote --to ...`
- Claude Code live-hook workflow helpers under `umx hooks claude-code` for install, export, session-start injection, pre-tool procedures, pre-compact sync, and session-end capture
- Richer viewer read surfaces: task board, tombstones, audit view, session browser, and conventions display
- Safe `aip-mem` compatibility entrypoint via `umx.aip`
- Experimental L2 PR review wiring for `umx dream --tier l2 --pr <number>`

### Changed
- README now documents live Claude hooks, promote destinations, richer viewer surfaces, `aip-mem`, and the current experimental status of remote/hybrid governance
- Remote/hybrid `pre_compact` now syncs session files without pushing fact-file changes straight to `main`
- L2 workflow templates and governance labels now align on `type: extraction`

### Fixed
- `umx promote` now rejects invalid destinations before mutating state and safely handles same-repo promotions
- Full test suite coverage now stands at 310 passing tests

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
