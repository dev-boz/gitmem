# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
- 211 tests (unit, integration, golden extraction)
- Spec document: gitmem-spec-v0_9.md

### Architecture
- Three modes: local (direct write), remote (all via PR), hybrid (mixed)
- Scope hierarchy: user → tool → project → folder → file
- Encoding strength 1-5 with composite scoring
- Git-native storage with markdown fact files
