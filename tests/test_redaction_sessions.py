from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.secret_literals import OPENAI_KEY_SHORT
from umx.config import default_config
from umx.redaction import RedactionError, redact_text
from umx.sessions import read_session, write_session


def test_redaction_catches_named_secret_and_entropy_assignment() -> None:
    cfg = default_config()
    text = (
        f'api_key = "{OPENAI_KEY_SHORT}"\n'
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
                "content": f'set api_key = "{OPENAI_KEY_SHORT}"',
            }
        ],
    )

    session = read_session(path)
    assert list(session[0]) == ["_meta"]
    assert "[REDACTED:openai-key]" in session[1]["content"]


def test_redaction_rejects_invalid_custom_pattern_with_clear_error() -> None:
    cfg = default_config()
    cfg.sessions.redaction_patterns = [""]

    with pytest.raises(RedactionError, match=r"non-empty regex strings"):
        redact_text("customer-1234", cfg)

    cfg.sessions.redaction_patterns = ["["]

    with pytest.raises(RedactionError, match=r"invalid redaction pattern"):
        redact_text("customer-1234", cfg)

    cfg.sessions.redaction_patterns = [r"(a+)+$"]

    with pytest.raises(RedactionError, match=r"unsafe regex constructs"):
        redact_text("a" * 64, cfg)


def test_custom_redaction_patterns_mask_with_standard_token(project_repo: Path) -> None:
    cfg = default_config()
    cfg.sessions.redaction_patterns = [r"customer-\d+"]

    result = redact_text("customer-1234", cfg)

    assert result.text == "[REDACTED:custom]"

    path = write_session(
        project_repo,
        {"session_id": "2026-04-11-custom", "tool": "codex"},
        [{"role": "user", "content": "customer-1234"}],
        config=cfg,
        auto_commit=False,
    )

    session = read_session(path)
    assert session[1]["content"] == "[REDACTED:custom]"


def test_write_session_quarantines_payload_on_redaction_failure(project_repo: Path) -> None:
    cfg = default_config()
    cfg.sessions.redaction_patterns = ["["]
    session_id = "2026-04-11-quarantine"

    with pytest.raises(RedactionError, match=r"invalid redaction pattern"):
        write_session(
            project_repo,
            {"session_id": session_id, "tool": "codex"},
            [{"role": "user", "content": "customer-1234"}],
            config=cfg,
            auto_commit=False,
        )

    assert not (project_repo / "sessions" / "2026" / "04" / f"{session_id}.jsonl").exists()
    quarantine = project_repo / "local" / "quarantine" / f"{session_id}.jsonl"
    metadata = project_repo / "local" / "quarantine" / f"{session_id}.meta.json"
    assert quarantine.exists()
    assert metadata.exists()
    assert "customer-1234" in quarantine.read_text()
    assert json.loads(metadata.read_text())["reason"].startswith("invalid redaction pattern")
