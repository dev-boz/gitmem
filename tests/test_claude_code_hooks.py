from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from click.testing import CliRunner

from umx.cli import main
from umx.config import default_config, save_config
from umx.memory import add_fact
from umx.models import (
    ConsolidationStatus,
    Fact,
    MemoryType,
    Scope,
    SourceType,
    Verification,
)
from umx.scope import config_path
from umx.sessions import read_session, write_session


def _write_claude_session(path: Path, records: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    return path


def _minimal_claude_records(session_id: str, *, base_time: datetime | None = None) -> list[dict]:
    started = base_time or datetime(2026, 1, 15, 10, 0, 0, tzinfo=UTC)
    stamps = [
        (started + timedelta(seconds=offset)).isoformat().replace("+00:00", "Z")
        for offset in (0, 1, 5)
    ]
    return [
        {
            "type": "system",
            "subtype": "turn_duration",
            "sessionId": session_id,
            "cwd": "/tmp/project",
            "version": "2.1.0",
            "slug": "test-session",
            "timestamp": stamps[0],
        },
        {
            "type": "user",
            "isSidechain": False,
            "uuid": "u1",
            "parentUuid": None,
            "message": {"role": "user", "content": "How do deploys work?"},
            "timestamp": stamps[1],
        },
        {
            "type": "assistant",
            "isSidechain": False,
            "uuid": "a1",
            "parentUuid": "u1",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "Deploys require a smoke check."}],
            },
            "timestamp": stamps[2],
        },
    ]


def test_claude_code_hooks_install_writes_settings(project_dir: Path) -> None:
    runner = CliRunner()

    result = runner.invoke(
        main,
        [
            "hooks",
            "claude-code",
            "install",
            "--cwd",
            str(project_dir),
            "--scope",
            "local",
            "--command",
            "python -m umx.cli",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    settings_path = Path(payload["installed"])
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    assert "SessionStart" in settings["hooks"]
    assert "PreToolUse" in settings["hooks"]
    assert settings["hooks"]["SessionEnd"][0]["hooks"][0]["command"] == "python -m umx.cli hooks claude-code session-end"


def test_claude_code_session_start_hook_returns_additional_context(
    project_dir: Path,
) -> None:
    runner = CliRunner()
    transcript = _write_claude_session(
        project_dir / ".claude" / "projects" / "session.jsonl",
        _minimal_claude_records("abc12345-0000-0000-0000-000000000000"),
    )
    payload_path = project_dir / "session-start.json"
    payload_path.write_text(
        json.dumps(
            {
                "session_id": "abc12345-0000-0000-0000-000000000000",
                "transcript_path": str(transcript),
                "cwd": str(project_dir),
                "hook_event_name": "SessionStart",
                "source": "startup",
                "model": "claude-sonnet-4-6",
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        main,
        ["hooks", "claude-code", "session-start", "--payload-file", str(payload_path)],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert "UMX Memory" in payload["hookSpecificOutput"]["additionalContext"]


def test_claude_code_pre_tool_hook_returns_additional_context(
    project_dir: Path,
    project_repo: Path,
) -> None:
    add_fact(
        project_repo,
        Fact(
            fact_id="01TESTFACT0000000000000900",
            text="deploys require a smoke check",
            scope=Scope.PROJECT,
            topic="deploy",
            encoding_strength=4,
            memory_type=MemoryType.EXPLICIT_SEMANTIC,
            verification=Verification.CORROBORATED,
            source_type=SourceType.GROUND_TRUTH_CODE,
            source_tool="codex",
            source_session="sess-hook-001",
            consolidation_status=ConsolidationStatus.STABLE,
        ),
        auto_commit=False,
    )
    payload_path = project_dir / "pre-tool.json"
    payload_path.write_text(
        json.dumps(
            {
                "session_id": "abc12345-0000-0000-0000-000000000000",
                "cwd": str(project_dir),
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "tool_input": {
                    "command": "pytest -q",
                    "description": "Run test suite",
                },
            }
        ),
        encoding="utf-8",
    )

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["hooks", "claude-code", "pre-tool-use", "--payload-file", str(payload_path)],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    assert "UMX Memory" in payload["hookSpecificOutput"]["additionalContext"]


def test_claude_code_session_end_hook_imports_transcript(
    project_dir: Path,
    project_repo: Path,
) -> None:
    cfg = default_config()
    cfg.sessions.archive_interval = "daily"
    save_config(config_path(), cfg)
    write_session(
        project_repo,
        {
            "session_id": "2020-01-15-claude-archive",
            "started": "2020-01-15T00:00:00Z",
            "tool": "claude-code",
        },
        [{"ts": "2020-01-15T00:00:01Z", "role": "assistant", "content": "Legacy transcript content"}],
        auto_commit=False,
    )

    runner = CliRunner()
    now = datetime.now(tz=UTC)
    transcript = _write_claude_session(
        project_dir / ".claude" / "projects" / "def67890-0000-0000-0000-000000000000.jsonl",
        _minimal_claude_records("def67890-0000-0000-0000-000000000000", base_time=now),
    )
    payload_path = project_dir / "session-end.json"
    payload_path.write_text(
        json.dumps(
            {
                "session_id": "def67890-0000-0000-0000-000000000000",
                "transcript_path": str(transcript),
                "cwd": str(project_dir),
                "hook_event_name": "SessionEnd",
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        main,
        ["hooks", "claude-code", "session-end", "--payload-file", str(payload_path)],
    )

    assert result.exit_code == 0, result.output
    today = now.date().isoformat()
    year, month = today.split("-", 2)[:2]
    session_path = project_repo / "sessions" / year / month / f"{today}-claude-code-def67890.jsonl"
    assert session_path.exists()
    payload = read_session(session_path)
    assert payload[0]["_meta"]["tool"] == "claude-code"
    assert payload[1]["content"] == "How do deploys work?"
    assert (project_repo / "sessions" / "2020" / "01" / "2020-01-archive.jsonl.gz").exists()
