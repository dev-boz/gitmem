# gitmem 0.9.x -> 1.0 upgrade guide

This guide documents the branch-head upgrade path from the current `0.9.x` line to the planned `1.0` release.

The repo still reports `0.9.2` in `pyproject.toml` today, so treat this as the runbook to use when the `1.0.0` package tag is cut.

## What changes in practice

| Area | What to do |
| --- | --- |
| Package install | Upgrade the `umx` / `gitmem` package to the `1.0.0` release you intend to run. |
| Repo-level schema | Run `umx doctor --cwd ...` first; use `umx doctor --cwd ... --fix` only for repo-level schema repair. |
| Fact-file schema | If `doctor` reports `fact_file_schema` drift, run `umx migrate --cwd ...`. |
| Backups | Use `umx export --cwd ... --out <dir>` before the upgrade. The backup is self-contained: `backup-manifest.json` plus `snapshot/`. |
| Config | No required key rename is planned in the current `0.9.x -> 1.0` path. New optional knobs are available; see below. |
| Legacy CLI usage | Existing `umx import --adapter ...` flows still work. New backup/restore flags are additive. |

## 1. Back up before touching the install

Do this for every project memory repo you care about.

```bash
gitmem export --cwd /path/to/project --out /tmp/gitmem-backup-my-project
```

The resulting directory is the unit you keep:

```text
/tmp/gitmem-backup-my-project/
├── backup-manifest.json
└── snapshot/
```

That bundle is raw-copy and includes the project memory repo contents except `.git`, including:

- facts and topic caches
- sessions plus archived session bundles/indexes
- `meta/` state, including SQLite index/usage DBs and WAL sidecars
- `.umx.json`
- local/private, local/secret, and quarantine content

Also copy the user-level repo and config separately if you use them:

```bash
cp -a ~/.umx/user /tmp/gitmem-backup-user
cp -a ~/.umx/config.yaml /tmp/gitmem-config.yaml
```

`umx export` / `umx import --full` currently operate on the project memory repo selected by `--cwd`; they do not replace a manual backup of `~/.umx/user` yet.

## 2. Install the 1.0 package

Upgrade using the channel you normally use. For a Git install, that will look like:

```bash
pip install -U git+https://github.com/dev-boz/gitmem.git@v1.0.0
```

Or, if you install from a wheel or internal package mirror, upgrade to that `1.0.0` build instead.

If you want to confirm the installed package version:

```bash
python -c "import importlib.metadata as md; print(md.version('umx'))"
```

## 3. Run doctor, then migrate only if needed

Start with a read-only check:

```bash
gitmem doctor --cwd /path/to/project
```

Two schema surfaces matter now:

1. **Repo-level schema** (`meta/schema_version`, generated index scaffolding, and related layout checks)
2. **Fact-file schema** (topic file header `schema_version: 1`)

If `doctor` says the **repo-level** schema is fixable, you can apply the built-in repair:

```bash
gitmem doctor --cwd /path/to/project --fix
```

If `doctor` reports **fact-file** schema drift, run the explicit migration:

```bash
gitmem migrate --cwd /path/to/project
```

### Governed mode caveat

`umx migrate` is a direct-write command. It will refuse in governed `remote` / `hybrid` modes.

For upgrades in governed environments, use one of these approaches:

1. Temporarily switch `dream.mode` to `local`, run the migration, commit/push the result, then switch back.
2. Run the migration in a local maintenance clone, review the resulting commit, and re-enable governed flows after the upgraded state is published.

Do **not** expect `umx doctor --fix` to apply fact-file migrations for you; it intentionally stops at repo-level repair.

## 4. Review optional config additions

No mandatory config key rename is currently required for the `0.9.x -> 1.0` path. Existing configs should continue to load.

The main new optional knobs you may want to add or review are:

```yaml
sessions:
  archive_interval: daily

inject:
  pre_tool_max_tokens: 1400
  disclosure_slack_pct: 0.20
```

- `sessions.archive_interval` controls scheduled archive compaction cadence.
- `inject.pre_tool_max_tokens` is the current default pre-tool injection budget.
- `inject.disclosure_slack_pct` controls the reserve before stable `L1` facts are downgraded to `L0`.

See [`docs/config.md`](./config.md) for the focused config reference.

## 5. Validate the upgraded repo

Run a small post-upgrade sanity pass:

```bash
gitmem status --cwd /path/to/project
gitmem doctor --cwd /path/to/project
gitmem search --cwd /path/to/project postgres
gitmem inject --cwd /path/to/project --prompt "current deploy flow"
```

If you upgraded a repo that needed fact-file migration, confirm `doctor` no longer reports `fact_file_schema` drift.

If you use capture-heavy workflows, it is also worth importing one fresh session after the upgrade:

```bash
gitmem capture claude-code --cwd /path/to/project
```

Adjust the capture command to match the backend you actually use.

## 6. Roll back if the upgrade goes sideways

If the upgrade produces a bad repo state or a workflow you cannot accept:

1. Reinstall the previous `0.9.x` package you were using.
2. Restore the project backup:

```bash
gitmem import --cwd /path/to/project --full /tmp/gitmem-backup-my-project --force
```

3. Restore the user repo/config if you backed them up separately:

```bash
rm -rf ~/.umx/user
cp -a /tmp/gitmem-backup-user ~/.umx/user
cp -a /tmp/gitmem-config.yaml ~/.umx/config.yaml
```

If you had temporarily disabled governed mode for migration, switch the config back only after the restored state is the one you want to keep.

## 7. Things that are *not* breaking in the current plan

As of the current branch head:

- `umx import --adapter ...` remains valid.
- The new backup/restore path is additive (`umx export`, `umx import --full`, `--force`).
- Config loading remains backward-compatible for existing `0.9.x` files.
- Repo-level repair and fact-file migration are intentionally separate commands.

If the actual `1.0.0` release introduces extra breaking changes beyond the current branch-head plan, update this guide alongside the release notes before shipping.
