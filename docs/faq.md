# FAQ

## Is this `gitmem` or `umx`?

Both. The repo and primary docs use **gitmem**. The Python package metadata and compatibility CLI alias still use **umx**.

## Does gitmem store memory in my project repo?

No. Memory lives in separate repos under `~/.umx/`. Your project repo gets a `.umx-project` marker so gitmem can map code to its memory repo.

## Do I need GitHub?

No. `local` mode works entirely on your filesystem. GitHub is only needed for `remote` or `hybrid` mode.

## Do I need model API keys?

Not for the local alpha loop. Provider-backed review is still experimental.

## Is there telemetry?

Only if you opt in. `telemetry.enabled` is off by default, and the payload is limited to anonymous command usage, latency, coarse repo-size buckets, and coarse error kinds. See the [privacy page](privacy.md).

## Which tools have first-class transcript capture today?

Codex, Copilot CLI, Claude Code, Gemini CLI, OpenCode, and Amp.

## What if my tool has no native capture adapter yet?

Use `gitmem collect` for exported transcripts, or a compatibility surface such as `shim` or `import --adapter ...` where it fits.

## Does gitmem intercept network traffic or wrap my CLI?

No. The shipped capture path reads files and hook outputs you point it at.

## What is the difference between `local`, `remote`, and `hybrid`?

- `local`: everything stays local
- `remote`: fact changes follow a PR-scaffolded governance path
- `hybrid`: same governed fact path, but session history syncs faster

`remote` and `hybrid` are currently experimental compared with the local loop.

## Are secrets injected back into prompts?

Project-secret facts are excluded from normal injection, and session text is redacted before persistence. See the [threat model](threat-model.md) for the current limits.

## Is the docs site the source of truth for exact flags?

Use [cli.md](cli.md) for the short reference and [spec-parity.md](spec-parity.md) for the exact branch-head parity audit.
