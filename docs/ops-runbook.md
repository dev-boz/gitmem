# Ops runbook

This is the short day-2 checklist for a shipped gitmem install.

## Routine checks

```bash
gitmem status --cwd /path/to/project
gitmem health --cwd /path/to/project
gitmem doctor --cwd /path/to/project
```

Use `health --governance --format human` when operating a governed repo.

## If search or injection looks stale

1. Re-run Dream:

   ```bash
   gitmem dream --cwd /path/to/project --force
   ```

2. Rebuild the lexical index:

   ```bash
   gitmem rebuild-index --cwd /path/to/project
   ```

3. If you intentionally use the `hybrid` search backend, rebuild embeddings too:

   ```bash
   gitmem rebuild-index --cwd /path/to/project --embeddings
   ```

## If sessions are piling up

```bash
gitmem archive-sessions --cwd /path/to/project
```

Review the archive cadence in [config.md](config.md).

## If you need to remove bad data

- Dry-run a session purge first:

  ```bash
  gitmem purge --cwd /path/to/project --session SESSION_ID --dry-run
  ```

- Then apply it:

  ```bash
  gitmem purge --cwd /path/to/project --session SESSION_ID
  ```

- Tombstone a bad fact or topic:

  ```bash
  gitmem forget --cwd /path/to/project --fact FACT_ID
  gitmem forget --cwd /path/to/project --topic deploy-flow
  ```

## Governed repos

- Use `gitmem sync --cwd ...` instead of raw `git push` for session sync.
- Run governance checks with:

  ```bash
  gitmem health --cwd /path/to/project --governance --format human
  ```

- Review the current trust boundaries before wider rollout: [threat-model.md](threat-model.md)

## Backups and recovery

Before migrations or risky repairs:

```bash
gitmem export --cwd /path/to/project --out /path/to/backup-dir
```

For restore and upgrade details, use the [upgrade guide](upgrade-0.9-to-1.0.md).

## Security notes

- gitmem redacts before session persistence, but operators should still treat captured transcripts as sensitive.
- `gitmem` safety checks cover `sync` and other managed flows; raw git commands can bypass them.
- In current branch-head behavior, `doctor` is the safer first stop for repo issues than ad hoc file edits.
