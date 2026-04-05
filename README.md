# umx — Universal Memory Exchange

A filesystem-native, tool-agnostic memory system for CLI coding agents.

## Overview

umx provides a shared memory layer that persists across sessions and tools. Facts are stored as markdown with inline metadata, scored using composite signals (strength, confidence, recency, corroboration), and managed through a dream pipeline that extracts, deduplicates, and prunes memory.

## Installation

```bash
pip install -e .
```

## Quick Start

```bash
# Initialise memory in a project
umx init

# Add a fact
umx add "The API uses JWT auth with RS256" --topic auth --strength 4

# View memory
umx view

# Inject memory into a tool session
umx inject --tool claude-code

# Run dream pipeline (consolidation)
umx dream --force
```

## Scope Hierarchy

| Scope | Path | Description |
|-------|------|-------------|
| User | `~/.umx/` | Cross-project preferences |
| Tool | `~/.umx/tools/<tool>/` | Per-tool settings |
| Project (team) | `<root>/.umx/` | Shared project memory |
| Project (local) | `<root>/.umx/local/` | Private local memory |
| Folder | `<dir>/.umx/` | Directory-scoped memory |
| File | `<dir>/.umx/files/<file>.md` | File-specific memory |

## Encoding Strength (1–5)

| Level | Meaning | Source |
|-------|---------|--------|
| 5 | Ground truth | Human-authored or edited |
| 4 | Deliberate | Tool native memory |
| 3 | Stated | Explicit in-session statements |
| 2 | Inferred | Extracted patterns |
| 1 | Ambient | Weak signals |

## Commands

- `umx init` — Initialise `.umx/` in the current project
- `umx inject` — Generate injection content for a tool
- `umx collect` — Collect tool memory after session end
- `umx dream` — Run the dream pipeline
- `umx view` — View current memory state
- `umx status` — Show project memory status
- `umx conflicts` — Show detected fact conflicts
- `umx forget` — Remove a fact by ID
- `umx promote` — Move a fact between scopes
- `umx add` — Add a new fact manually

## Specification

See [umemx-spec-v0_4.md](umemx-spec-v0_4.md) for the full specification.

## License

MIT