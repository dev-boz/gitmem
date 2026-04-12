from __future__ import annotations

from pathlib import Path

from umx.config import default_config
from umx.redaction import redact_text
from umx.sessions import read_session, write_session


def test_redaction_catches_named_secret_and_entropy_assignment() -> None:
    cfg = default_config()
    text = (
        'api_key = "sk-ABCDEFGHIJKLMNOPQRSTUV"\n'
        'token = "uV7Q3pL9mN2xT8cJ5rK1wZ4bD6yH0sFq"\n'
        "prose uV7Q3pL9mN2xT8cJ5rK1wZ4bD6yH0sFq should remain\n"
    )

    result = redact_text(text, cfg)

    assert "[REDACTED:openai-key]" in result.text
    assert "[REDACTED:high-entropy]" in result.text
    assert "prose uV7Q3pL9mN2xT8cJ5rK1wZ4bD6yH0sFq should remain" in result.text


def test_write_session_redacts_and_keeps_meta_first(project_repo: Path) -> None:
    path = write_session(
        project_repo,
        {
            "project": "project",
            "tool": "codex",
            "machine": "testbox",
            "started": "2026-04-11T00:00:00Z",
        },
        [
            {
                "ts": "2026-04-11T00:00:01Z",
                "role": "user",
                "content": 'set api_key = "sk-ABCDEFGHIJKLMNOPQRSTUV"',
            }
        ],
    )

    session = read_session(path)
    assert list(session[0]) == ["_meta"]
    assert "[REDACTED:openai-key]" in session[1]["content"]
