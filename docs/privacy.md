# Privacy and telemetry

gitmem telemetry is **off by default**.

You only opt in when `telemetry.enabled: true` is set in `~/.umx/config.yaml` or via:

```bash
gitmem config set telemetry.enabled true
```

## What telemetry sends

Telemetry is limited to anonymous product signals:

- command usage (`status`, `dream`, `capture/claude-code`, and similar command paths)
- success or failure
- coarse error kind (`click_error`, `usage_error`, `abort`, `exit`, `other`)
- command latency
- coarse repo-size buckets when a project repo is in play:
  - fact count bucket
  - pending-session count bucket
- current mode/search-backend flags (`local`/`remote`/`hybrid`, `fts5`/`hybrid`)
- coarse runtime metadata (Python major/minor, platform)

## What telemetry does **not** send

Telemetry does **not** send:

- prompts
- session transcripts
- fact text
- repo paths
- project slugs
- remote URLs
- PR bodies
- secrets or raw config values
- source code or diffs

The client uses a random local installation ID stored under `~/.umx/telemetry/`. It is not derived from your username, repo path, or GitHub org.

## Local queue and kill switch

- Upload failures do not block normal commands.
- Events queue locally under `~/.umx/telemetry/` and retry later.
- `config set telemetry.enabled true|false` does not upload the consent-toggle command itself.
- `UMX_TELEMETRY_DISABLE=1` disables uploads immediately.
- The server can also disable future uploads with a kill switch response; the local client will stop sending after that until the local telemetry state is reset.
