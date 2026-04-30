from __future__ import annotations

import json
import stat
from pathlib import Path

from umx.providers import opencode_cli as opencode_cli_provider


def _write_fake_opencode(tmp_path: Path, *, stdout_lines: list[dict[str, object]]) -> Path:
    script_path = tmp_path / "fake-opencode"
    sidecar = tmp_path / "fake-opencode-args.json"
    serialized = "\n".join(json.dumps(line) for line in stdout_lines)
    script = (
        "#!/usr/bin/env python3\n"
        "import json, sys, pathlib\n"
        f"sidecar = pathlib.Path({str(sidecar)!r})\n"
        "stdin_data = sys.stdin.read()\n"
        "sidecar.write_text(json.dumps({'argv': sys.argv[1:], 'stdin': stdin_data}))\n"
        f"sys.stdout.write({serialized!r})\n"
        "sys.exit(0)\n"
    )
    script_path.write_text(script, encoding="utf-8")
    script_path.chmod(script_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return script_path


def test_send_opencode_cli_message_uses_stdin_and_joins_text(tmp_path: Path, monkeypatch) -> None:
    fake = _write_fake_opencode(
        tmp_path,
        stdout_lines=[
            {"type": "step_start", "part": {"type": "step-start"}},
            {"type": "text", "part": {"type": "text", "text": "Hello"}},
            {"type": "text", "part": {"type": "text", "text": " world"}},
            {
                "type": "step_finish",
                "part": {
                    "type": "step-finish",
                    "tokens": {"total": 20, "input": 6, "output": 10, "reasoning": 2, "cache": {"read": 4}},
                },
            },
        ],
    )
    monkeypatch.setenv(opencode_cli_provider.OPENCODE_CLI_BINARY_ENV, str(fake))

    response = opencode_cli_provider.send_opencode_cli_message(
        model="opencode/big-pickle",
        system="be brief",
        prompt="hello",
    )

    assert response.text == "Hello world"
    assert response.usage == {
        "input_tokens": 8,
        "output_tokens": 12,
        "total_tokens": 20,
        "reasoning_output_tokens": 2,
        "cached_input_tokens": 4,
    }

    sidecar = json.loads((tmp_path / "fake-opencode-args.json").read_text(encoding="utf-8"))
    argv = sidecar["argv"]
    assert argv[:1] == ["run"]
    assert "--dangerously-skip-permissions" not in argv
    assert "--model" in argv and argv[argv.index("--model") + 1] == "opencode/big-pickle"
    assert sidecar["stdin"].startswith("System instructions:\nbe brief")
