from __future__ import annotations

import json
import stat
from pathlib import Path

from umx.providers import gemini_cli as gemini_cli_provider


def _write_fake_gemini(tmp_path: Path, *, stdout_payload: dict[str, object]) -> Path:
    script_path = tmp_path / "fake-gemini"
    sidecar = tmp_path / "fake-gemini-args.json"
    script = (
        "#!/usr/bin/env python3\n"
        "import json, sys, pathlib\n"
        f"sidecar = pathlib.Path({str(sidecar)!r})\n"
        "stdin_data = sys.stdin.read()\n"
        "sidecar.write_text(json.dumps({'argv': sys.argv[1:], 'stdin': stdin_data}))\n"
        f"sys.stdout.write({json.dumps(stdout_payload)!r})\n"
        "sys.exit(0)\n"
    )
    script_path.write_text(script, encoding="utf-8")
    script_path.chmod(script_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return script_path


def test_send_gemini_cli_message_uses_stdin(tmp_path: Path, monkeypatch) -> None:
    fake = _write_fake_gemini(
        tmp_path,
        stdout_payload={
            "response": "PONG",
            "stats": {
                "models": {
                    "gemini-2.5-flash": {
                        "tokens": {"input": 10, "total": 14, "thoughts": 1, "cached": 2}
                    }
                }
            },
        },
    )
    monkeypatch.setenv(gemini_cli_provider.GEMINI_CLI_BINARY_ENV, str(fake))

    response = gemini_cli_provider.send_gemini_cli_message(
        model="gemini-2.5-flash",
        system="be brief",
        prompt="hello",
    )

    assert response.text == "PONG"
    assert response.usage == {
        "input_tokens": 10,
        "output_tokens": 4,
        "total_tokens": 14,
        "cached_input_tokens": 2,
        "reasoning_output_tokens": 1,
    }

    sidecar = json.loads((tmp_path / "fake-gemini-args.json").read_text(encoding="utf-8"))
    argv = sidecar["argv"]
    assert "--prompt" not in argv
    assert "--approval-mode" in argv and argv[argv.index("--approval-mode") + 1] == "plan"
    assert "--model" in argv and argv[argv.index("--model") + 1] == "gemini-2.5-flash"
    assert sidecar["stdin"].startswith("System instructions:\nbe brief")
