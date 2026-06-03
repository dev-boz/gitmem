from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.secret_literals import GCP_API_KEY, OPENAI_KEY_SHORT, STRIPE_SECRET_KEY
from umx.config import default_config
from umx.redaction import RedactionError, redact_text
from umx.sessions import SessionQuarantineError, read_session, write_session
from umx.status import build_status_payload


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


def test_builtin_redaction_patterns_cover_gcp_and_stripe_keys() -> None:
    cfg = default_config()

    gcp = redact_text(GCP_API_KEY, cfg)
    stripe = redact_text(STRIPE_SECRET_KEY, cfg)

    assert gcp.text == "[REDACTED:gcp-api-key]"
    assert stripe.text == "[REDACTED:stripe-key]"


def test_write_session_tracks_high_entropy_review_in_status(project_dir: Path, project_repo: Path) -> None:
    session_id = "2026-04-11-review"
    path = write_session(
        project_repo,
        {"session_id": session_id, "tool": "codex"},
        [{"role": "user", "content": 'token = "uV7Q3pL9mN2xT8cJ5rK1wZ4bD6yH0sFq"'}],
        auto_commit=False,
    )

    payload = read_session(path)
    assert payload[0]["_meta"]["redaction_review"]["high_entropy_count"] == 1

    status = build_status_payload(project_dir)
    assert status["high_entropy_redaction_count"] == 1
    assert status["redaction_review"]["sessions"] == [session_id]
    assert any("high-entropy redaction" in flag for flag in status["flags"])


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


def test_write_session_quarantines_png_payload_before_sessions_write(project_repo: Path) -> None:
    cfg = default_config()
    session_id = "2026-04-11-png"
    png = b"\x89PNG\r\n\x1a\n" + (b"\x00" * 64)

    with pytest.raises(SessionQuarantineError, match="binary session payload intercepted: png payload"):
        write_session(
            project_repo,
            {"session_id": session_id, "tool": "codex"},
            [{"role": "tool_result", "content": png, "path": "screenshots/error.png"}],
            config=cfg,
            auto_commit=False,
        )

    session_path = project_repo / "sessions" / "2026" / "04" / f"{session_id}.jsonl"
    quarantine = project_repo / "local" / "quarantine" / f"{session_id}.jsonl"
    metadata = project_repo / "local" / "quarantine" / f"{session_id}.meta.json"
    assert not session_path.exists()
    assert quarantine.exists()
    assert metadata.exists()
    payload = quarantine.read_text()
    assert "__binary__" in payload
    assert "screenshots/error.png" in payload
    assert "png" in json.loads(metadata.read_text())["reason"]


def test_write_session_quarantines_oversized_opaque_binary_payload(project_repo: Path) -> None:
    cfg = default_config()
    cfg.sessions.binary_cap_kb = 1
    session_id = "2026-04-11-binary-cap"

    with pytest.raises(SessionQuarantineError, match="opaque session payload intercepted"):
        write_session(
            project_repo,
            {"session_id": session_id, "tool": "codex"},
            [{"role": "tool_result", "content": b"\x00" * 2048}],
            config=cfg,
            auto_commit=False,
        )

    session_path = project_repo / "sessions" / "2026" / "04" / f"{session_id}.jsonl"
    metadata = project_repo / "local" / "quarantine" / f"{session_id}.meta.json"
    assert not session_path.exists()
    assert json.loads(metadata.read_text())["reason"].startswith("opaque session payload intercepted")
