# Governance tutorial

This tutorial covers the **current shipped** remote/hybrid path. It is useful, but still experimental compared with local mode.

## 1. Prerequisites

- `gh` CLI installed and authenticated
- a GitHub org you control
- a project already initialized with git

## 2. Bootstrap user memory in governed mode

Choose `hybrid` if you want the lighter-weight governed path first:

```bash
gitmem init --org your-github-org --mode hybrid
```

Use `--mode remote` if you want the stricter remote path instead.

## 3. Initialize project memory

```bash
gitmem init-project --cwd /path/to/project
```

If the project already has local memory and you are attaching GitHub later:

```bash
gitmem setup-remote --cwd /path/to/project --mode hybrid
```

## 4. Run the normal memory loop

Capture or collect sessions:

```bash
gitmem capture claude-code --cwd /path/to/project
gitmem collect --cwd /path/to/project --tool cursor --file ./cursor-session.txt
```

Run Dream:

```bash
gitmem dream --cwd /path/to/project --force
```

In governed modes, Dream uses proposal branches / PR scaffolding for fact changes rather than treating them like ordinary local-only writes.

## 5. Sync sessions

```bash
gitmem sync --cwd /path/to/project
```

Use `gitmem sync`, not raw `git push`, for the normal governed session flow.

## 6. Check governance health

```bash
gitmem health --cwd /path/to/project --governance --format human
```

That is the fastest summary for branch-head governance state.

## 7. Review an L2 PR when needed

```bash
gitmem dream --cwd /path/to/project --mode remote --tier l2 --pr 42
```

If you need to pin the expected head commit:

```bash
gitmem dream --cwd /path/to/project --mode remote --tier l2 --pr 42 --head-sha <sha>
```

## 8. Use cross-project promotion deliberately

Inspect candidates:

```bash
gitmem audit --cwd /path/to/project --cross-project
```

Preview a specific proposal:

```bash
gitmem audit --cwd /path/to/project --cross-project --proposal-key "shared deploy checklist lives in docs/runbooks"
```

Materialize a local proposal branch:

```bash
gitmem propose --cwd /path/to/project --cross-project --proposal-key "shared deploy checklist lives in docs/runbooks"
```

Push it:

```bash
gitmem propose --cwd /path/to/project --cross-project --proposal-key "shared deploy checklist lives in docs/runbooks" --push
```

Open the PR:

```bash
gitmem propose --cwd /path/to/project --cross-project --proposal-key "shared deploy checklist lives in docs/runbooks" --open-pr
```

## Guardrails to keep

- Prefer `hybrid` first unless you specifically want full remote mode behavior.
- Use `gitmem` governance commands instead of raw git for sync and proposal flows.
- Treat the current path as review scaffolding, not as a complete containment boundary.
- Read the [threat model](threat-model.md) before rolling governed mode out to a team.
