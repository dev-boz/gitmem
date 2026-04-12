# Contributing

Thanks for contributing to `gitmem`.

This project is still alpha. Small, focused fixes are preferred over broad refactors.

## Before you start

- Open an issue or discussion first if you want to change architecture, memory model semantics, or governance flow.
- Keep pull requests narrow in scope. One behavior change per PR is the right default.
- If a feature is experimental in the README, keep the docs honest. Do not present draft or partial work as production-ready.

## Development setup

Requires Python 3.11+.

```bash
git clone https://github.com/dev-boz/gitmem.git
cd gitmem
pip install -e ".[dev]"
```

## Running tests

Run the full suite before opening a PR:

```bash
pytest -q
```

Useful focused suites:

```bash
pytest -q tests/test_codex_capture.py tests/test_copilot_capture.py tests/test_golden_extraction.py
pytest -q tests/test_mcp_server.py tests/test_security.py tests/test_governance.py
```

## What to include in a change

- Add or update tests for behavior changes.
- Update `README.md` if the user-facing workflow, install story, or support matrix changes.
- Update `gitmem-spec-v0_9.md` if you change the memory model, strength taxonomy, governance semantics, or retrieval behavior.
- Keep claims conservative. If something is partial or experimental, say so explicitly.

## Pull request checklist

- The branch is focused and easy to review.
- Tests pass locally.
- New behavior is covered by tests.
- Docs/spec are updated where needed.
- No unrelated cleanup is bundled into the PR.

## Reporting issues

When filing a bug, include:

- what you ran
- what you expected
- what happened instead
- your OS, Python version, and tool involved (`codex`, `copilot`, `claude-code`, etc.)
- a minimal repro if possible

## License

By contributing, you agree that your contributions will be licensed under the project's MIT license.
