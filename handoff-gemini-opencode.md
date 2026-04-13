# Handoff: Add Gemini CLI and OpenCode session capture to gitmem

## What this project is

**gitmem** (`/home/dinkum/projects/gitmem`) is a git-native shared memory system for AI coding agents. It ingests conversation transcripts from tools like Codex, Copilot, and Claude Code, extracts facts from them via a "dream" consolidation pipeline, and makes those facts available to all tools via an MCP server and injected context blocks.

Your job is to add two new transcript capture commands:
- `gitmem capture gemini` — reads Gemini CLI session JSON files
- `gitmem capture opencode` — reads OpenCode's SQLite database

Both should follow the exact same pattern as the existing `capture claude-code` command.

---

## How to orient yourself

Read these files in order:

1. **`umx/claude_code_capture.py`** — the reference implementation (258 lines). This is the cleanest, most recent capture module. Model your work on this exactly.
2. **`umx/codex_capture.py`** — an older reference for contrast.
3. **`umx/cli.py`** lines 825–888 — the `capture claude-code` CLI command. You need to add analogous `capture gemini` and `capture opencode` commands in the same `capture_group`.
4. **`tests/test_claude_code_capture.py`** — the test suite to model yours on.

Run the tests first to confirm all 273 pass before you touch anything:
```bash
cd /home/dinkum/projects/gitmem
python3 -m pytest tests/ -q
```

---

## The pattern every capture module must follow

Every capture module:
1. Has a `parse_<tool>_session(path) -> <Tool>Transcript` (or `parse_<tool>_sessions(db_path) -> list[<Tool>Transcript]` for DB-backed tools)
2. Has a `capture_<tool>_session(cwd, source, config=None) -> dict` that calls `write_session(repo_dir, meta, events, config, auto_commit=False)` and returns a result dict
3. Has discovery helpers: `list_<tool>_sessions(project_root, source_root) -> list[Path]` and `latest_<tool>_session_path(...) -> Path | None`
4. The `meta` dict passed to `write_session` must include: `session_id` (the UMX session ID), `tool` (the tool name string), and `source` (a short string describing how it was captured)
5. `events` is `list[dict]` where each dict has `role` (`"user"` or `"assistant"`), `content` (string), and optionally `ts` (ISO timestamp string)
6. The `umx_session_id` property on the transcript should be formatted as `"YYYY-MM-DD-<toolname>-<short-id>"`

The `write_session` function is at `umx/sessions.py`. Import it as:
```python
from umx.sessions import write_session
```

---

## Task 1: Gemini CLI capture

### Where Gemini stores sessions

- **Session files**: `~/.gemini/tmp/<project-slug>/chats/session-<date>-<id>.json`
- **Project slug → directory mapping**: `~/.gemini/projects.json`
  ```json
  {"projects": {"/home/dinkum": "dinkum", "/home/dinkum/projects/foo": "foo"}}
  ```
- **Project history root**: `~/.gemini/history/<project-slug>/` (contains a `.project_root` file with the absolute path)

To find sessions for a given `cwd`, look up the project slug from `~/.gemini/projects.json` by matching the cwd to a project path (longest-prefix match), then read `~/.gemini/tmp/<slug>/chats/*.json`.

### Session file format

Each file is a single JSON object:
```json
{
  "sessionId": "423243ca-034d-4136-88b2-43f95bf4522c",
  "projectHash": "2f46b97...",
  "startTime": "2026-04-09T03:21:21.301Z",
  "lastUpdated": "2026-04-09T03:23:02.550Z",
  "kind": "main",
  "messages": [...]
}
```

Messages have a `type` field:
- `"info"` — system notices (e.g. update notifications). **Skip.**
- `"user"` — user turns. `content` is a **list** of `{"text": "..."}` objects. Extract all `.text` values and join.
- `"gemini"` — assistant turns. `content` is a **string** (the final response text). Many intermediate turns will have `content: ""` (they were tool-use turns); **skip those**.

Also available on `gemini` messages (optional, don't rely on these): `thoughts` (list), `toolCalls` (list), `tokens` (dict), `model` (string).

### What to implement

**`umx/gemini_capture.py`** — mirror of `claude_code_capture.py`:

```
GeminiTranscript(session_id, project_slug, start_time, last_updated, source_path, events)
  .umx_session_id  →  "YYYY-MM-DD-gemini-<first8charsOfSessionId>"

_gemini_projects_root(source_root=None) → ~/.gemini
_project_slug_for_cwd(cwd, source_root=None) → str | None
    reads ~/.gemini/projects.json, finds longest matching path prefix
_gemini_chats_dir(project_slug, source_root=None) → Path
    → ~/.gemini/tmp/<slug>/chats/

list_gemini_sessions(project_root=None, source_root=None) → list[Path]
latest_gemini_session_path(project_root=None, source_root=None) → Path | None
parse_gemini_session(path) → GeminiTranscript
capture_gemini_session(cwd, session_path, config=None) → dict
```

**`tests/test_gemini_capture.py`** — model on `test_claude_code_capture.py`. Cover:
- `_project_slug_for_cwd` with exact match, longest-prefix match, no match
- `parse_gemini_session`: info messages skipped, empty-content gemini turns skipped, user content list joined, timestamps in events, deduplication, malformed JSON lines skipped, empty file
- `list_gemini_sessions` and `latest_gemini_session_path`
- `capture_gemini_session` end-to-end (writes session to repo)

**CLI command in `umx/cli.py`** — add to `capture_group`, same shape as `capture claude-code`:
```
gitmem capture gemini [--cwd PATH] [--file PATH] [--source-root PATH] [--all] [--dry-run]
```

---

## Task 2: OpenCode capture

### Where OpenCode stores sessions

OpenCode uses **SQLite** at `~/.local/share/opencode/opencode.db`.

**Schema (relevant tables):**

`session`:
```
id TEXT          -- e.g. "ses_28fb6a341ffeaupTJ2rub5rLaB"
project_id TEXT  -- usually "global"
slug TEXT        -- human readable, e.g. "happy-planet"
directory TEXT   -- absolute path of the working directory
title TEXT       -- inferred title
version TEXT     -- opencode version, e.g. "1.2.27"
time_created INTEGER  -- epoch milliseconds
time_updated INTEGER
```

`message`:
```
id TEXT
session_id TEXT
time_created INTEGER  -- epoch milliseconds (use for ordering)
data TEXT   -- JSON: {"role": "user"|"assistant", "time": {...}, ...}
```

`part`:
```
id TEXT
message_id TEXT
session_id TEXT
time_created INTEGER
data TEXT   -- JSON: {"type": "text"|"tool"|"step-start"|"step-finish"|"reasoning", "text": "..."}
```

### How to extract conversation turns

For a session:
1. `SELECT * FROM session WHERE id = ?` — get metadata
2. `SELECT id, time_created, data FROM message WHERE session_id = ? ORDER BY time_created` — get messages
3. For each message, `SELECT data FROM part WHERE message_id = ? ORDER BY time_created` — get parts
4. Extract `role` from `json_extract(message.data, '$.role')` → `"user"` or `"assistant"`
5. From parts, collect all with `type = "text"` and non-empty `text`. Join them with `"\n\n"` as the event content.
6. Skip messages where no text parts have non-empty content (those are pure tool-use turns).

**Do not use `json_extract` in SQLite** if you can avoid it — parse `data` in Python instead, it's safer and simpler.

Use `time_created / 1000` to get a Unix timestamp, then `datetime.utcfromtimestamp(...).isoformat() + "Z"` for the `ts` field.

### Finding sessions for a project

Filter `session.directory` to match the project root. Use an exact match first; fall back to prefix match. If `directory IS NULL` or doesn't match, include anyway in `--all` mode.

The default `source_root` is `~/.local/share/opencode/opencode.db` (pass the file directly, not a directory).

### What to implement

**`umx/opencode_capture.py`**:

```
OpenCodeSession(session_id, slug, directory, title, version, time_created, events)
  .umx_session_id  →  "YYYY-MM-DD-opencode-<first8charsOfSessionId>"
  .started         →  ISO timestamp derived from time_created

_opencode_db_path(source_root=None) → ~/.local/share/opencode/opencode.db

list_opencode_sessions(project_root=None, source_root=None) → list[OpenCodeSession]
    Opens the SQLite DB, returns sessions (filtered by directory if project_root given).
    Returns [] if db doesn't exist.

latest_opencode_session(project_root=None, source_root=None) → OpenCodeSession | None

capture_opencode_session(cwd, session, config=None) -> dict
    session is an OpenCodeSession (already parsed, not a path)
```

Note: because OpenCode uses SQLite (not per-session files), `list_opencode_sessions` returns parsed `OpenCodeSession` objects rather than `Path` objects. The CLI `--file` flag doesn't apply; instead use `--session-id` to target a specific session by ID.

**`tests/test_opencode_capture.py`** — use a temporary SQLite DB in `tmp_path`. Cover:
- `list_opencode_sessions`: all sessions, filtered by directory, empty DB
- `latest_opencode_session`: picks most recent by `time_created`
- Text extraction: only `type=text` parts, non-empty, joined correctly
- Messages with no text parts are skipped
- `capture_opencode_session` end-to-end

**CLI command in `umx/cli.py`**:
```
gitmem capture opencode [--cwd PATH] [--db PATH] [--session-id ID] [--all] [--dry-run]
```

---

## Common implementation notes

### `write_session` call shape

```python
from umx.sessions import write_session

session_file = write_session(
    repo_dir,                        # from project_memory_dir(project_root)
    meta={
        "session_id": transcript.umx_session_id,
        "tool": "gemini",            # or "opencode"
        "source": "gemini-chat",     # or "opencode-db"
        "gemini_session_id": ...,    # original tool session ID
        "gemini_project_slug": ...,  # or opencode equivalents
        # ... any other useful metadata fields
    },
    events=transcript.events,        # list of {role, content, ts?}
    config=cfg,
    auto_commit=False,
)
```

After capturing (in the CLI command), commit once:
```python
from umx.git_ops import git_add_and_commit
git_add_and_commit(repo_dir, message="umx: capture gemini sessions")
```

### CLI command skeleton (copy from claude-code, adapt)

The `capture claude-code` command is at `umx/cli.py` around line 826. Copy its structure. For OpenCode, replace `--file PATH` with `--session-id TEXT` (optional specific session ID to import).

### Test fixture pattern

Every integration test uses this fixture pattern (copy from `test_claude_code_capture.py::TestCapture`):

```python
def test_something(self, tmp_path, monkeypatch):
    from umx.scope import init_local_umx, init_project_memory
    from umx.config import default_config, save_config
    from umx.scope import config_path

    home = tmp_path / "umxhome"
    monkeypatch.setenv("UMX_HOME", str(home))
    init_local_umx()
    save_config(config_path(), default_config())

    project = tmp_path / "project"
    project.mkdir()
    (project / ".git").mkdir()
    init_project_memory(project)

    # ... your capture call, then assertions on the session file
```

### Running the tests

```bash
# Run just your new tests
python3 -m pytest tests/test_gemini_capture.py tests/test_opencode_capture.py -v

# Run the full suite to confirm nothing broke
python3 -m pytest tests/ -q
```

---

## File checklist

When you are done, these files should exist or be modified:

- [ ] `umx/gemini_capture.py` (new)
- [ ] `umx/opencode_capture.py` (new)
- [ ] `tests/test_gemini_capture.py` (new)
- [ ] `tests/test_opencode_capture.py` (new)
- [ ] `umx/cli.py` (modified — add `capture gemini` and `capture opencode` commands to `capture_group`)
- [ ] `README.md` (modified — add both tools to the capture section, same as how claude-code was added)

Do **not** modify `umx/mcp_server.py`, `umx/dream/`, `umx/memory.py`, `umx/models.py`, or any existing tests. Those are stable.

---

## Quick sanity check once done

```bash
# Gemini (should find sessions in ~/.gemini/tmp/dinkum/chats/)
gitmem capture gemini --cwd /home/dinkum --dry-run

# OpenCode (should find sessions for /home/dinkum in ~/.local/share/opencode/opencode.db)
gitmem capture opencode --cwd /home/dinkum --dry-run

# Full test suite
python3 -m pytest tests/ -q
```

If either dry-run finds 0 events and the local data clearly has sessions, the discovery logic for that tool's project-to-slug/directory mapping needs debugging — check the mapping files first.
