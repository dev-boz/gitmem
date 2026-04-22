# Quickstart

## Prerequisites

- Python 3.11+
- `gh` CLI only if you plan to use `remote` or `hybrid` mode

## Install

```bash
pip install git+https://github.com/dev-boz/gitmem.git
```

## 1. Initialize gitmem

```bash
gitmem init
```

That creates your memory home under `~/.umx/`.

## 2. Initialize a project

From the project you want to remember:

```bash
gitmem init-project --cwd /path/to/project
```

gitmem keeps memory in a separate repo. Your project repo only gets a `.umx-project` marker.

## 3. Capture or collect a session

Use the adapter that matches the tool you already ran:

```bash
gitmem capture codex --cwd /path/to/project
gitmem capture copilot --cwd /path/to/project
gitmem capture claude-code --cwd /path/to/project
gitmem capture gemini --cwd /path/to/project
gitmem capture opencode --cwd /path/to/project
gitmem capture amp --cwd /path/to/project
```

For tools without a native capture adapter yet, use `collect`:

```bash
gitmem collect --cwd /path/to/project --tool aider --file ./aider-session.txt
cat ./cursor-session.txt | gitmem collect --cwd /path/to/project --tool cursor
```

For a minimal reproducible local smoke loop, you can collect one line directly:

```bash
printf 'postgres runs on port 5433 in dev.\n' | gitmem collect --cwd /path/to/project --tool aider
gitmem dream --cwd /path/to/project --force
gitmem search --cwd /path/to/project postgres
gitmem inject --cwd /path/to/project --prompt "postgres"
gitmem status --cwd /path/to/project
```

This is the branch-head quickstart path kept runnable in CI.

## 4. Run Dream

```bash
gitmem dream --cwd /path/to/project --force
```

This runs the shipped pipeline: extract, consolidate, lint, and prune.

## 5. Retrieve memory

```bash
gitmem search --cwd /path/to/project postgres
gitmem inject --cwd /path/to/project --prompt "postgres deploy flow"
gitmem view --cwd /path/to/project --list
gitmem status --cwd /path/to/project
gitmem health --cwd /path/to/project
gitmem doctor --cwd /path/to/project
```

## 6. Optional: install Claude Code hooks

```bash
gitmem hooks claude-code install --cwd /path/to/project
```

To inspect the generated config without writing it:

```bash
gitmem hooks claude-code print
```

## Next

- [Concepts](concepts.md)
- [CLI reference](cli.md)
- [Governance tutorial](governance-tutorial.md) for `remote` / `hybrid`
- [API reference](api/index.md)
- [Configuration reference](config.md)
