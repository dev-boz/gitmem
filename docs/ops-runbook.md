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

## Eval gates before release

Run the eval suite after meaningful ranking, retrieval, review, or dogfood-facing changes:

```bash
gitmem eval l2-review > artifacts/l2-review.json
gitmem eval inject > artifacts/inject.json
gitmem eval long-memory > artifacts/long-memory.json
gitmem eval retrieval > artifacts/retrieval.json
```

- `l2-review` and `inject` are native gitmem evals over checked-in local corpora.
- `long-memory` and `retrieval` are offline benchmark adapters built from LongMemEval-style and HotpotQA-style subsets.
- Each command exits nonzero when its pass-rate gate fails, so the same commands work in local smoke checks, CI, and release checklists.

`gitmem eval l2-review` defaults to the Anthropic API and requires `ANTHROPIC_API_KEY`. To run the same eval against the operator's existing Claude Code OAuth session instead of an API key, install the Claude Code CLI, sign in once, and pass `--provider claude-cli`:

```bash
gitmem eval l2-review --provider claude-cli > artifacts/l2-review.json
```

The `claude-cli` path shells out to `claude --print --output-format json` per case, so wall-clock time and per-call cost mirror Claude Code itself; reserve it for release-gate runs rather than tight inner loops.

If you want a minimal CI/pytest-style gate, run the normal test suite first and then the eval commands as plain subprocess steps:

```bash
pytest -q
gitmem eval inject > artifacts/inject.json
gitmem eval long-memory > artifacts/long-memory.json
gitmem eval retrieval > artifacts/retrieval.json
```

Keep external frameworks optional. If you want DeepEval or another reporting layer, ingest the emitted JSON artifacts there rather than adding framework-specific runtime dependencies to the core gitmem path.

## If you need a fresh machine or isolated test owner

Use a dedicated `UMX_HOME` when you want to attach the same project to a separate machine, sandbox, or GitHub owner without touching your primary local state:

```bash
export UMX_HOME=/path/to/isolated-umx-home
gitmem init --owner <owner> --mode hybrid
gitmem init-project --cwd /path/to/project --yes
```

- A fresh home now reuses existing remote `umx-user` and project memory repos instead of failing on a non-fast-forward bootstrap push.
- Keep the alternate `UMX_HOME` isolated when dogfooding a different owner or credential set.
- Use `gitmem sync --cwd /path/to/project` after attachment to confirm the remote is reachable from the new machine.

## If you need to rotate GitHub credentials

1. Confirm the current account still sees the expected owner:

   ```bash
   gh auth status
   gitmem health --cwd /path/to/project --governance --format human
   ```

2. Re-authenticate `gh` with the replacement credential that still has access to the same memory-repo owner.

3. Re-run a managed sync:

   ```bash
   gitmem sync --cwd /path/to/project
   ```

4. If you need to test or stage the rotation first, use an isolated `UMX_HOME` and reattach with `gitmem init --owner ... --mode hybrid` plus `gitmem init-project --cwd ... --yes`.

The memory state stays in the remote `umx-user` and project repos, so a fresh `UMX_HOME` can reattach after the credential swap as long as the new credential still has access to the target owner.

## If you work from multiple machines

For the currently supported low-friction flow, keep each machine on the same project slug and remote memory repo, then use managed sync when handing work off:

```bash
gitmem sync --cwd /path/to/project
```

- In local mode, `gitmem sync` stays a no-op until at least one memory repo (`projects/<slug>` or `umx-user`) has a configured git remote. Once a remote exists, the same command will fetch/rebase/push local memory-repo commits instead of requiring raw git commands.
- Sync on machine A before switching away, especially after new session capture or other operational state changes.
- Sync on machine B before starting new work so `main` fast-forwards to the latest shared memory state.
- When the user memory repo also has a configured remote, `gitmem sync` carries both the project repo and `umx-user`, so cross-project promotions made in local mode can follow the same handoff path.
- The automated coverage now exercises sequential two-home project handoff in `local`, `hybrid`, and `remote` modes, plus a local-mode user-memory promotion handoff where one machine promotes a fact to user scope and the second machine picks it up through `gitmem sync`.
- Governed fact changes still belong in PR branches; this guidance is for shared session history, user-memory propagation, and coordination state on `main`.

If you expect overlap or divergence:

- Avoid parallel `dream` runs against the same project memory repo until the broader multi-machine matrix lands; keep one machine responsible for governed PR creation at a time.
- If one machine already opened a governed PR, let the others sync `main` and review that PR instead of trying to produce competing direct-main changes.
- Treat `pull --rebase failed` during `gitmem sync` as a handoff problem, not something to bulldoze through with raw pushes.

## Backups and recovery

Before migrations or risky repairs:

```bash
gitmem export --cwd /path/to/project --out /path/to/backup-dir
```

For restore and upgrade details, use the [upgrade guide](upgrade-0.9-to-1.0.md).

## Security notes

- gitmem redacts before session persistence, but operators should still treat captured transcripts as sensitive.
- `gitmem` safety checks cover `sync` and other managed flows; raw git commands can bypass them.
- On GitHub Free org-owned private repos, `main-guard.yml` is the current fallback for missing rulesets. It preserves auditability by reverting unauthorized governed pushes with a bot commit, but it is still post-push enforcement rather than hard branch protection.
- In current branch-head behavior, `doctor` is the safer first stop for repo issues than ad hoc file edits.
