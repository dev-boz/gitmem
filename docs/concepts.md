# Concepts

## Memory lives outside your code repo

gitmem keeps memory in separate git repos under `~/.umx/`:

- `~/.umx/user` for user-level memory
- `~/.umx/projects/<slug>` for project memory

Your code repo stays your code repo. gitmem links it with a `.umx-project` marker instead of writing a large `.umx/` tree into the project.

## Sessions are evidence; facts are durable memory

- **Sessions** are imported transcripts and collected logs.
- **Facts** are the distilled memory produced from those sessions.

The normal loop is:

1. capture or collect a session
2. run `gitmem dream`
3. search, inject, view, confirm, forget, or promote the resulting facts

## Dream is the consolidation pipeline

`gitmem dream` is the main maintenance step. In the current branch it runs:

- extract
- consolidate
- lint
- prune

In local mode it writes directly. In governed modes it scaffolds a review flow.

## Retrieval is scope-aware

gitmem injects the most relevant facts it can fit into budget. The shipped scope model is hierarchical, with more specific context winning over broader context.

You can also promote facts across longer-lived scopes:

- `--to user`
- `--to project`
- `--to principle`

## Modes

| Mode | Facts | Sessions | Best fit |
|---|---|---|---|
| `local` | direct local writes | local only | solo, offline, simplest setup |
| `remote` | PR-scaffolded governance flow | explicit sync/hooks to `main` | team review, audit trail |
| `hybrid` | PR-scaffolded governance flow | explicit sync/hooks to `main` | faster capture with governed fact changes |

`remote` and `hybrid` are shipped but still the roughest, most experimental part of the project.

## Governance today

In governed modes:

- session sync and fact review are separate flows
- fact changes are intended to move through proposal branches / PRs
- `gitmem` commands enforce more safety than raw `git push`

For the current security boundaries, read the [threat model](threat-model.md).

## Tool integration surfaces

- `capture` for first-class transcript adapters
- `collect` for manual or exported transcripts
- `hooks claude-code` for live Claude Code integration
- `shim` and `bridge` for compatibility surfaces
- `import --adapter ...` for native-memory import
- `mcp` for stdio MCP integration
