from __future__ import annotations

import json
import os
from pathlib import Path

from click.testing import CliRunner

from umx.cli import main
from umx.codex_capture import latest_codex_rollout_path, parse_codex_rollout
from umx.memory import load_all_facts
from umx.models import ConsolidationStatus, SourceType
from umx.sessions import session_path


def _rollout_records(
    *,
    codex_session_id: str,
    user_text: str,
    assistant_text: str,
) -> list[dict]:
    return [
        {
            "timestamp": "2026-04-11T13:22:22.564Z",
            "type": "session_meta",
            "payload": {
                "id": codex_session_id,
                "timestamp": "2026-04-11T13:22:22.564Z",
                "cwd": "/home/dinkum",
                "cli_version": "0.120.0",
            },
        },
        {
            "timestamp": "2026-04-11T13:25:11.577Z",
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": "<environment_context>\n  <cwd>/home/dinkum</cwd>\n</environment_context>",
                    }
                ],
            },
        },
        {
            "timestamp": "2026-04-11T13:25:11.599Z",
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": user_text}],
            },
        },
        {
            "timestamp": "2026-04-11T13:25:11.599Z",
            "type": "event_msg",
            "payload": {
                "type": "user_message",
                "message": user_text,
            },
        },
        {
            "timestamp": "2026-04-11T13:28:59.571Z",
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": assistant_text}],
            },
        },
    ]


def _write_codex_rollout(
    codex_root: Path,
    filename: str,
    records: list[dict],
    *,
    trailing_fragment: str | None = None,
) -> Path:
    path = codex_root / "sessions" / "2026" / "04" / "11" / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(json.dumps(record, sort_keys=True) for record in records)
    if trailing_fragment is not None:
        body = f"{body}\n{trailing_fragment}"
    else:
        body = f"{body}\n"
    path.write_text(body)
    return path


def test_parse_codex_rollout_skips_framework_messages_and_partial_lines(tmp_path: Path) -> None:
    codex_root = tmp_path / ".codex"
    rollout = _write_codex_rollout(
        codex_root,
        "rollout-2026-04-11T23-22-22-019d7cb5-1fb1-7af3-a70c-78c403aeaa70.jsonl",
        _rollout_records(
            codex_session_id="019d7cb5-1fb1-7af3-a70c-78c403aeaa70",
            user_text="Continue building gitmem",
            assistant_text="The backup worker runs every hour.",
        ),
        trailing_fragment='{"timestamp":"2026-04-11T13:30:00.000Z"',
    )

    transcript = parse_codex_rollout(rollout)

    assert transcript.codex_session_id == "019d7cb5-1fb1-7af3-a70c-78c403aeaa70"
    assert transcript.umx_session_id == "2026-04-11-codex-019d7cb5-1fb1-7af3-a70c-78c403aeaa70"
    assert [event["role"] for event in transcript.events] == ["user", "assistant"]
    assert transcript.events[0]["content"] == "Continue building gitmem"
    assert transcript.events[1]["content"] == "The backup worker runs every hour."


def test_latest_codex_rollout_path_uses_newest_file(tmp_path: Path) -> None:
    codex_root = tmp_path / ".codex"
    older = _write_codex_rollout(
        codex_root,
        "rollout-2026-04-11T20-00-00-019d7cb5-older.jsonl",
        _rollout_records(
            codex_session_id="019d7cb5-older",
            user_text="older",
            assistant_text="older",
        ),
    )
    newer = _write_codex_rollout(
        codex_root,
        "rollout-2026-04-11T21-00-00-019d7cb5-newer.jsonl",
        _rollout_records(
            codex_session_id="019d7cb5-newer",
            user_text="newer",
            assistant_text="newer",
        ),
    )
    os.utime(older, (1, 1))
    os.utime(newer, (2, 2))

    assert latest_codex_rollout_path(codex_root) == newer


def test_capture_codex_cli_imports_transcript_and_preserves_external_doc_behavior(
    tmp_path: Path,
    project_dir: Path,
    project_repo: Path,
) -> None:
    docs_dir = project_dir / "docs"
    docs_dir.mkdir()
    (docs_dir / "plan.md").write_text(
        "# Plan\n\n"
        "- The backup worker runs every hour\n"
        "- Deploys run through staging first\n"
    )

    codex_root = tmp_path / ".codex"
    rollout = _write_codex_rollout(
        codex_root,
        "rollout-2026-04-11T23-22-22-019d7cb5-1fb1-7af3-a70c-78c403aeaa70.jsonl",
        _rollout_records(
            codex_session_id="019d7cb5-1fb1-7af3-a70c-78c403aeaa70",
            user_text="Continue from docs/plan.md",
            assistant_text="I read docs/plan.md. The backup worker runs every hour.",
        ),
    )

    runner = CliRunner()
    capture = runner.invoke(
        main,
        ["capture", "codex", "--cwd", str(project_dir), "--file", str(rollout)],
    )
    assert capture.exit_code == 0, capture.output
    payload = json.loads(capture.output)

    umx_session_id = payload["umx_session_id"]
    assert payload["events_imported"] == 2
    assert session_path(project_repo, umx_session_id).exists()

    raw = runner.invoke(main, ["search", "--cwd", str(project_dir), "--raw", "backup worker"])
    assert raw.exit_code == 0, raw.output
    assert umx_session_id in raw.output

    dream = runner.invoke(main, ["dream", "--cwd", str(project_dir), "--force"])
    assert dream.exit_code == 0, dream.output
    dream_payload = json.loads(dream.output)
    assert dream_payload["status"] == "ok"

    indexed = runner.invoke(main, ["search", "--cwd", str(project_dir), "backup worker"])
    assert indexed.exit_code == 0, indexed.output
    assert "The backup worker runs every hour" in indexed.output

    facts = load_all_facts(project_repo, include_superseded=False)
    doc_facts = [fact for fact in facts if fact.code_anchor and fact.code_anchor.path == "docs/plan.md"]
    assert len(doc_facts) >= 1
    assert all(fact.source_type == SourceType.EXTERNAL_DOC for fact in doc_facts)
    assert all(fact.consolidation_status == ConsolidationStatus.FRAGILE for fact in doc_facts)
