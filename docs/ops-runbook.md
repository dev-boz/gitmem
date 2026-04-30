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

## Release gate workflow

1. Run the normal offline validation first (`pytest -q`, then `mkdocs build --strict`).
2. Run the checked-in subset evals as a local smoke gate.
3. Run the release-grade public benchmark slices for LongMemEval and HotpotQA.
4. Compare the new benchmark artifacts against the last accepted baseline.
5. Record personal dogfood evidence for one local repo and one GitHub-backed repo.
6. Copy the artifact locations and bug status into the [launch checklist](launch-checklist.md).

### Artifact conventions

- Keep generated outputs under `artifacts/release-gates/<stamp>/`; that path is gitignored.
- Keep maintainer-prepared public benchmark slices under `benchmarks/release-data/<suite>/cases.json`; that path is also gitignored because the repo does not vendor upstream benchmark data.
- Recommended layout:

  ```text
  artifacts/release-gates/<stamp>/
    local/
      inject.smoke.json
      long-memory.smoke.json
      retrieval.smoke.json
    release/
      longmemeval.release.json
      hotpotqa.release.json
      longmemeval.compare.json
      hotpotqa.compare.json
    dogfood/
      local-<repo>.json
      hybrid-<repo>.json
  ```

### Local subset smoke runs

Use the checked-in offline subsets after ranking, retrieval, review, or dogfood-facing changes:

```bash
stamp=2026-04-28-rc1
mkdir -p artifacts/release-gates/$stamp/local

gitmem eval inject > artifacts/release-gates/$stamp/local/inject.smoke.json
gitmem eval long-memory > artifacts/release-gates/$stamp/local/long-memory.smoke.json
gitmem eval retrieval > artifacts/release-gates/$stamp/local/retrieval.smoke.json
```

- `inject` is a native gitmem golden eval.
- `long-memory` is the offline LongMemEval-style subset.
- `retrieval` is the offline HotpotQA-style subset.
- Every command keeps the stable-JSON + nonzero-exit contract, so the same commands work in local smoke checks, CI, and release checklists.

### Release-grade benchmark runs

For release candidates, point the same commands at fixed public slices that you prepared locally from the canonical upstream sources:

```bash
mkdir -p artifacts/release-gates/$stamp/release

gitmem eval long-memory \
  --cases benchmarks/release-data/longmemeval/cases.json \
  > artifacts/release-gates/$stamp/release/longmemeval.release.json

gitmem eval retrieval \
  --cases benchmarks/release-data/hotpotqa/cases.json \
  > artifacts/release-gates/$stamp/release/hotpotqa.release.json
```

- **LongMemEval** is the required public long-memory benchmark for 1.0.
- **HotpotQA** is the required public multi-hop retrieval benchmark for 1.0.
- `benchmarks/release-data/hotpotqa/cases.json` may be either a filtered raw HotpotQA JSON slice or a `hotpotqa-manifest` file referencing a larger upstream dataset. No separate gitmem-native conversion step is required.
- Keep the slice fixed across reruns. Record the upstream repo/data revision, split, and slice recipe next to the release artifacts or in the launch checklist.
- Do not vendor the public benchmark data into the repo unless the license and size make that explicitly acceptable.

If you need the upstream-style LongMemEval QA benchmark instead of the retrieval-only pilot, run:

```bash
gitmem eval longmemeval \
  --cases benchmarks/release-data/longmemeval/cases.json \
  --out-dir artifacts/release-gates/$stamp/release/longmemeval-qa \
  --min-pass-rate 0
```

That command writes `hypotheses.jsonl`, `judgments.jsonl`, and `summary.json` under the chosen output directory. By default it uses the local Claude Code CLI OAuth session, but `--provider` can also target `codex-cli`, `gemini-cli`, or `opencode-cli`. Keep `--min-pass-rate 0` only for capture-only benchmark runs; raise it when you want the command itself to act as a gate.

Optional exploratory memory benchmarks can be run the same way:

```bash
gitmem eval beir \
  --cases benchmarks/release-data/beir/scifact/manifest.json \
  --min-ndcg-at-10 0 \
  > artifacts/release-gates/$stamp/release/beir-scifact.release.json

gitmem eval locomo \
  --cases benchmarks/release-data/locomo/cases.json \
  --out-dir artifacts/release-gates/$stamp/release/locomo \
  --min-average-f1 0

gitmem eval convomem \
  --cases benchmarks/release-data/convomem/cases.json \
  --out-dir artifacts/release-gates/$stamp/release/convomem \
  --min-pass-rate 0

gitmem eval longbench-v2 \
  --cases benchmarks/release-data/longbench_v2/cases.json \
  --out-dir artifacts/release-gates/$stamp/release/longbench-v2 \
  --provider codex-cli \
  --min-accuracy 0

gitmem eval ruler \
  --cases benchmarks/release-data/ruler/extractive/manifest.json \
  --out-dir artifacts/release-gates/$stamp/release/ruler \
  --provider gemini-cli \
  --min-average-score 0
```

The BEIR path stays fully local: point `--cases` either at a raw dataset directory (`corpus.jsonl`, `queries.jsonl`, `qrels/test.tsv`) or at a `beir-manifest` that pins a query subset while still referencing those raw files. SciFact is the recommended first BEIR dataset because it is small, public, and retrieval-focused. The RULER path expects either a normalized slice or a local `ruler-manifest` that references upstream-style generated JSONL task files; keep those generated files under the ignored `benchmarks/release-data/ruler/` tree. These BEIR, LoCoMo, ConvoMem, LongBench v2, and RULER adapters are still exploratory and should be treated as **benchmark signals**, not as full Dream-cycle benchmark coverage.

### Comparing against the last baseline

Save the last accepted release artifacts, then compare them before sign-off:

```bash
gitmem eval compare \
  artifacts/release-gates/2026-04-14-rc0/release/longmemeval.release.json \
  artifacts/release-gates/$stamp/release/longmemeval.release.json \
  > artifacts/release-gates/$stamp/release/longmemeval.compare.json

gitmem eval compare \
  artifacts/release-gates/2026-04-14-rc0/release/hotpotqa.release.json \
  artifacts/release-gates/$stamp/release/hotpotqa.release.json \
  > artifacts/release-gates/$stamp/release/hotpotqa.compare.json
```

- `gitmem eval compare` exits nonzero when the candidate artifact itself is not `ok` or when the candidate regresses below the baseline on the selected metrics.
- With no explicit `--metric`, the command uses suite defaults:
  - `beir`: `ndcg_at_10` and `recall_at_10`
  - `long-memory`: `pass_rate`, `average_recall`, and each `type_summary.<question_type>.average_recall` present in the artifacts
  - `retrieval`: `pass_rate`, `average_recall`, and `average_answer_coverage` when that metric exists in the artifacts
  - `ruler`: `average_score` and `pass_rate`
  - `inject`: `pass_rate`
- Use `--metric` to compare a custom numeric field and `--tolerance` to allow a small absolute drop when you consciously redefine the gate.

If you want one command to write the bundle, use the helper instead of stitching the steps together manually:

```bash
gitmem eval release-gate \
  --out-dir artifacts/release-gates/$stamp \
  --long-memory-release-cases benchmarks/release-data/longmemeval/cases.json \
  --retrieval-release-cases benchmarks/release-data/hotpotqa/cases.json \
  --long-memory-baseline artifacts/release-gates/baselines/longmemeval-last.json \
  --retrieval-baseline artifacts/release-gates/baselines/hotpotqa-last.json
```

That command always writes the local smoke artifacts under `local/`, writes release-grade benchmark artifacts under `release/` when the release case paths are supplied, and writes compare JSONs under `release/` when the baseline paths are supplied.

For the first capture before you have an accepted baseline, drop the release-case pass-rate gates to zero so the command records the benchmark payloads without claiming they are green:

```bash
gitmem eval release-gate \
  --out-dir artifacts/release-gates/$stamp \
  --long-memory-release-cases benchmarks/release-data/longmemeval/cases.json \
  --retrieval-release-cases benchmarks/release-data/hotpotqa/cases.json \
  --long-memory-release-min-pass-rate 0 \
  --retrieval-release-min-pass-rate 0
```

In `summary.json`, those release entries are marked as `captured` and `release_capture_only` is set to `true`; treat that bundle as artifact capture, not benchmark sign-off.

### Recording personal dogfood evidence

Before 1.0, keep at least two dogfood records:

- one pure-local repo
- one GitHub-backed repo (`remote` or `hybrid`)

Record each run as a small JSON note under `artifacts/release-gates/<stamp>/dogfood/`. A minimal shape is:

```json
{
  "repo": "gitmem",
  "mode": "hybrid",
  "date_window": "2026-04-21..2026-04-28",
  "commands_exercised": ["init", "capture", "dream", "search", "inject", "health", "sync"],
  "clean_room_umx_home": true,
  "reattach_sync": true,
  "open_p0_p1_bugs": [],
  "notes": "Fresh UMX_HOME attach and sync stayed clean."
}
```

Use the launch checklist to roll those records up into the final **works personally** sign-off.

### L2 reviewer provider note

`gitmem eval l2-review` defaults to the Anthropic API and requires `ANTHROPIC_API_KEY`. To run the same eval against the operator's existing Claude Code OAuth session instead of an API key, install the Claude Code CLI, sign in once, and pass `--provider claude-cli`:

```bash
gitmem eval l2-review --provider claude-cli > artifacts/release-gates/$stamp/local/l2-review.smoke.json
```

The `claude-cli` path shells out to `claude --print --output-format json` per case, so wall-clock time and per-call cost mirror Claude Code itself; reserve it for release-gate runs rather than tight inner loops.

The same provider switch now works on live PR review runs:

```bash
gitmem dream --cwd /path/to/project --mode remote --tier l2 --pr 42 --provider claude-cli
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
- `gitmem sync` runs `umx-user` first and the project repo second; if the second half fails, treat the result as a partial handoff rather than assuming neither repo moved.
- The automated coverage now exercises sequential two-home project handoff in `local`, `hybrid`, and `remote` modes, plus a local-mode user-memory promotion handoff where one machine promotes a fact to user scope and the second machine picks it up through `gitmem sync`.
- Governed fact changes still belong in PR branches; this guidance is for shared session history, user-memory propagation, and coordination state on `main`.

If you expect overlap or divergence:

- If `gitmem sync` says `rebase already in progress` or `merge already in progress`, finish or abort that git operation before rerunning the command.
- Avoid parallel `dream` runs against the same project memory repo until the broader multi-machine matrix lands; keep one machine responsible for governed PR creation at a time.
- If one machine already opened a governed PR, let the others sync `main` and review that PR instead of trying to produce competing direct-main changes.
- If `gitmem sync` reports `pull --rebase failed with conflicts in ...`, stop and resolve the overlapping files in the memory repo (or `git rebase --abort` to back out), then rerun `gitmem sync`.
- For local-mode fact-file overlap, resolve the raw git conflict first and then use `gitmem conflicts` / `gitmem merge --dry-run` if the surviving file still leaves contradictory facts to arbitrate.

## Backups and recovery

Before migrations or risky repairs:

```bash
gitmem export --cwd /path/to/project --out /path/to/backup-dir
```

For restore and upgrade details, use the [upgrade guide](upgrade-0.9-to-1.0.md).

## Security notes

- gitmem redacts before session persistence, but operators should still treat captured transcripts as sensitive.
- `gitmem` safety checks cover `sync` and other managed flows; raw git commands can bypass them.
- On GitHub Free org-owned private repos, `main-guard.yml` is the current remote/hybrid fallback for missing rulesets. It preserves auditability by reverting unauthorized governed pushes with a bot commit and appending a `governance_auto_revert` record to `meta/processing.jsonl`, but it is still post-push enforcement rather than hard branch protection.
- In current branch-head behavior, `doctor` is the safer first stop for repo issues than ad hoc file edits.
