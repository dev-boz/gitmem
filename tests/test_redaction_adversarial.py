from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.secret_literals import OPENAI_KEY_LONG, materialize_placeholders
from umx.config import default_config
from umx.dream.extract import session_records_to_facts
from umx.redaction import RedactionError, redact_candidate_fact_text
from umx.sessions import (
    quarantine_metadata_path,
    quarantine_path,
    read_session,
    write_session,
)

FIXTURES_ROOT = Path(__file__).parent / "fixtures" / "secrets"
CASES = materialize_placeholders(json.loads((FIXTURES_ROOT / "cases.json").read_text()))


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_adversarial_secret_fixtures_redact_candidate_fact_text(case: dict[str, object]) -> None:
    cfg = default_config()
    cfg.sessions.redaction_patterns = list(case.get("custom_patterns", []))

    redacted = redact_candidate_fact_text(str(case["text"]), cfg)

    assert str(case["expected_token"]) in redacted
    for forbidden in case["forbidden_substrings"]:
        assert str(forbidden) not in redacted


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_adversarial_secret_fixtures_redact_on_session_write(
    case: dict[str, object],
    project_repo: Path,
) -> None:
    cfg = default_config()
    cfg.sessions.redaction_patterns = list(case.get("custom_patterns", []))
    session_id = f"2026-04-11-{case['id']}"

    path = write_session(
        project_repo,
        {"session_id": session_id, "tool": "codex"},
        [{"role": "user", "content": str(case["text"])}],
        config=cfg,
        auto_commit=False,
    )

    payload = read_session(path)
    session_text = json.dumps(payload, sort_keys=True)
    facts = session_records_to_facts(
        project_repo,
        config=cfg,
        include_archived=False,
        session_ids={session_id},
        skip_gathered=False,
    )
    fact_text = "\n".join(fact.text for fact in facts)
    assert str(case["expected_token"]) in session_text
    for forbidden in case["forbidden_substrings"]:
        assert str(forbidden) not in session_text
        assert str(forbidden) not in fact_text
    assert not quarantine_path(project_repo, session_id).exists()


def test_adversarial_redaction_quarantines_on_scanner_error(
    project_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id = "2026-04-11-redaction-scanner-error"

    def fail_closed(*_args, **_kwargs):
        raise RedactionError("scanner crashed")

    monkeypatch.setattr("umx.sessions.redact_jsonl_lines", fail_closed)

    with pytest.raises(RedactionError, match="scanner crashed"):
        write_session(
            project_repo,
            {"session_id": session_id, "tool": "codex"},
            [{"role": "user", "content": f'api_key = "{OPENAI_KEY_LONG}"'}],
            config=default_config(),
            auto_commit=False,
        )

    assert quarantine_path(project_repo, session_id).exists()
    metadata = json.loads(quarantine_metadata_path(project_repo, session_id).read_text())
    assert metadata["reason"] == "scanner crashed"
