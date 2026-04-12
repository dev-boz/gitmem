# umx

Local-first implementation of the `gitmem` / `umx` v0.9 spec.

## Release Scope

This is being prepared as a local-first alpha release with experimental remote/hybrid support.

What the alpha is for:

- real-project local dogfooding
- local session capture, including `umx capture codex`
- local `inject -> dream -> search -> view`

What is experimental (new):

- `remote` mode — dream commits to a branch, pushes, and opens a PR on GitHub for governance review
- `hybrid` mode — sessions push directly to main, fact changes go through PRs
- `umx setup-remote` — connect an existing local project to a GitHub memory org

What is explicitly rough:

- live extraction on real Codex transcripts still keeps some low-value procedural and doc-derived facts
- document-derived facts are intentionally demoted to `external_doc`, `fragile`
- L2 review workflow runs in CI but does not yet auto-merge or auto-close PRs
- GitHub Actions workflows are deployed but not yet tested in CI

This repo currently focuses on the local-mode core:

- markdown fact storage with inline metadata
- separate `$UMX_HOME` memory repos
- session capture and redaction
- search/indexing and usage telemetry
- injection and budget packing
- a local dream pipeline with consolidation, lint, prune, manifest rebuild, tombstones, and supersession history

## Local Dogfood Quickstart

Install editable and initialize a local memory home:

```bash
pip install -e .
umx init --org memory-org
```

If you are actively developing from the checkout and do not want to rely on an installed binary, use the repo-local entry point:

```bash
PYTHONPATH=$PWD python3 -m umx.cli --help
```

A clean `pip install -e .` in a fresh virtualenv was also verified. Avoid `--system-site-packages` if you have an older global `umx` installed, because it can mask the checkout during verification.

Initialize one project:

```bash
umx init-project --cwd /path/to/project
umx status --cwd /path/to/project
```

Run the basic local loop:

```bash
umx inject --cwd /path/to/project --prompt "postgres deploy flow"
umx capture codex --cwd /path/to/project
umx search --cwd /path/to/project postgres
umx dream --cwd /path/to/project --force
umx view --cwd /path/to/project --list
```

If you are dogfooding from Codex, `umx capture codex` imports the latest rollout from `~/.codex/sessions` by default. You can also point it at a specific rollout file with `--file /path/to/rollout.jsonl`.

## Remote/Hybrid Quickstart

Requires `gh` CLI installed and authenticated (`gh auth login`).

Initialize with remote mode:

```bash
umx init --org your-github-org --mode remote
umx init-project --cwd /path/to/project
```

This creates private repos under your org (`your-github-org/umx-user`, `your-github-org/<project-slug>`) and sets up git remotes. In `remote` mode, GitHub Actions workflow templates (L1 dream, L2 review) are deployed.

Or connect an existing local project to a GitHub org:

```bash
umx setup-remote --cwd /path/to/project --mode hybrid
```

When you run `umx dream` in remote/hybrid mode, extracted facts are committed to a feature branch, pushed, and a PR is opened on GitHub:

```bash
umx dream --cwd /path/to/project --force
# Output includes PR number: "PR: [dream/l1] ... (#42)"
```

Sync sessions and facts with `umx sync`:

```bash
umx sync --cwd /path/to/project
```

### Mode differences

| | `local` | `remote` | `hybrid` |
|---|---|---|---|
| Facts | direct write | PR only | PR only |
| Sessions | local | local | push to main |
| Governance | none | full L1/L2 | full L1/L2 |
| Best for | solo/offline | team/audit | team/fast sessions |

## Dogfooding Checklist

Use this as the minimum manual readiness pass for a real repo:

1. `umx init` and `umx init-project` succeed with no patching.
2. `umx inject` returns a usable memory block for a real prompt.
3. A real session is written through hooks, MCP, or `umx capture codex` and shows up in `umx search --raw`.
4. `umx dream --force` extracts at least one expected fact.
5. `umx search` finds the new fact from the index.
6. `umx view --list` renders the retained fact set without starting the web UI.

## Useful Test Targets

For the current dogfooding slice, the highest-signal checks are:

```bash
pytest -q tests/test_codex_capture.py tests/test_golden_extraction.py tests/test_dogfood_readiness.py
pytest -q tests/test_github_ops.py tests/test_governance.py
pytest -q
```
