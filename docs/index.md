# gitmem docs

gitmem is a **CLI-first, git-native shared memory layer for AI coding agents**. It stores sessions and derived facts in separate memory repos on your filesystem, with optional GitHub sync and PR-based governance.

Examples in these docs use `gitmem`. The legacy `umx` alias still works.

## Start here

- [Quickstart](quickstart.md) — install, initialize, capture, dream, search, inject
- [Concepts](concepts.md) — sessions, facts, scopes, modes, governance
- [CLI reference](cli.md) — shipped commands and the most useful flags
- [Ops runbook](ops-runbook.md) — day-2 checks, recovery, and maintenance
- [Governance tutorial](governance-tutorial.md) — remote/hybrid setup and review flow
- [Threat model](threat-model.md) — current trust boundaries and security posture
- [FAQ](faq.md) — short answers to common operator questions
- [API reference](api/index.md) — curated generated Python reference for the public module surface
- [Privacy and telemetry](privacy.md) — exact opt-in telemetry boundaries and kill-switch behavior

## What ships today

- Local-mode memory repos and the native Dream pipeline
- Transcript capture for Codex, Copilot CLI, Claude Code, Gemini CLI, OpenCode, and Amp
- Manual session ingest with `gitmem collect`
- Search, inject, view, status, health, doctor, promote, and audit flows
- Claude Code hook install helpers
- MCP server over stdio
- Remote/hybrid bootstrap, session sync, and PR-scaffolded governance flows (**experimental**)

## Reference pages

- [Configuration reference](config.md)
- [CLI spec parity audit](spec-parity.md)
- [Threat model](threat-model.md)
- [0.9.x -> 1.0 upgrade guide](upgrade-0.9-to-1.0.md)
