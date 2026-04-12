from __future__ import annotations

import json
import os
from pathlib import Path

from click.testing import CliRunner

from umx.cli import main
from umx.copilot_capture import (
    latest_copilot_session_path,
    list_copilot_sessions,
    parse_copilot_session,
)
from umx.memory import load_all_facts
from umx.sessions import session_path


def _copilot_events(
    *,
    session_id: str,
    copilot_version: str = "1.0.24",
    cwd: str = "/home/user",
    model: str | None = None,
    user_text: str = "fix the auth bug",
    assistant_text: str = "I'll look at the auth module.",
) -> list[dict]:
    """Generate a minimal Copilot events.jsonl record set."""
    events: list[dict] = [
        {
            "type": "session.start",
            "data": {
                "sessionId": session_id,
                "copilotVersion": copilot_version,
                "startTime": "2026-04-12T04:02:46.320Z",
                "context": {"cwd": cwd},
            },
            "timestamp": "2026-04-12T04:02:46.471Z",
        }
    ]
    if model:
        events.append(
            {
                "type": "session.model_change",
                "data": {"newModel": model},
                "timestamp": "2026-04-12T04:03:07.985Z",
            }
        )
    events.extend(
        [
            {
                "type": "user.message",
                "data": {"content": user_text},
                "timestamp": "2026-04-12T04:05:00.000Z",
            },
            {
                "type": "assistant.message",
                "data": {"content": assistant_text},
                "timestamp": "2026-04-12T04:05:05.000Z",
            },
        ]
    )
    return events


def _write_copilot_events(
    copilot_root: Path,
    session_id: str,
    events: list[dict],
    *,
    trailing_fragment: str | None = None,
) -> Path:
    """Write Copilot events.jsonl to session-state/{session_id}/events.jsonl."""
    path = copilot_root / "session-state" / session_id / "events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(json.dumps(event, sort_keys=True) for event in events)
    if trailing_fragment is not None:
        body = f"{body}\n{trailing_fragment}"
    else:
        body = f"{body}\n"
    path.write_text(body)
    return path


class TestParseCopilotSession:
    """Tests for parse_copilot_session function."""

    def test_extracts_session_metadata(self, tmp_path: Path) -> None:
        """Test that session metadata is extracted correctly."""
        copilot_root = tmp_path / ".copilot"
        events_path = _write_copilot_events(
            copilot_root,
            "abc-123",
            _copilot_events(
                session_id="abc-123",
                copilot_version="1.0.24",
                cwd="/home/user/projects/myapp",
                model="claude-opus-4.6",
            ),
        )

        transcript = parse_copilot_session(events_path)

        assert transcript.session_id == "abc-123"
        assert transcript.cwd == "/home/user/projects/myapp"
        assert transcript.copilot_version == "1.0.24"
        assert transcript.model == "claude-opus-4.6"
        assert transcript.started == "2026-04-12T04:02:46.320Z"
        assert transcript.source_path == events_path

    def test_extracts_user_and_assistant_messages(self, tmp_path: Path) -> None:
        """Test that user and assistant messages are extracted correctly."""
        copilot_root = tmp_path / ".copilot"
        events_path = _write_copilot_events(
            copilot_root,
            "session-1",
            _copilot_events(
                session_id="session-1",
                user_text="fix the auth bug",
                assistant_text="I'll look at the auth module.",
            ),
        )

        transcript = parse_copilot_session(events_path)

        assert len(transcript.events) == 2
        assert transcript.events[0]["role"] == "user"
        assert transcript.events[0]["content"] == "fix the auth bug"
        assert transcript.events[0]["ts"] == "2026-04-12T04:05:00.000Z"
        assert transcript.events[1]["role"] == "assistant"
        assert transcript.events[1]["content"] == "I'll look at the auth module."
        assert transcript.events[1]["ts"] == "2026-04-12T04:05:05.000Z"

    def test_skips_system_injected_environment_context_messages(
        self, tmp_path: Path
    ) -> None:
        """Test that system-injected <environment_context> messages are skipped."""
        copilot_root = tmp_path / ".copilot"
        events = _copilot_events(
            session_id="session-2",
            user_text="real user prompt",
            assistant_text="response",
        )
        # Insert a system-injected environment context message
        events.insert(
            2,
            {
                "type": "user.message",
                "data": {
                    "content": "<environment_context>\n  <cwd>/home/user</cwd>\n</environment_context>"
                },
                "timestamp": "2026-04-12T04:05:00.000Z",
            },
        )
        events_path = _write_copilot_events(copilot_root, "session-2", events)

        transcript = parse_copilot_session(events_path)

        # Should only have the real user message and assistant message, no environment context
        assert len(transcript.events) == 2
        assert transcript.events[0]["role"] == "user"
        assert transcript.events[0]["content"] == "real user prompt"

    def test_skips_system_injected_turn_aborted_messages(self, tmp_path: Path) -> None:
        """Test that <turn_aborted> system messages are skipped."""
        copilot_root = tmp_path / ".copilot"
        events = _copilot_events(
            session_id="session-3",
            user_text="real prompt",
            assistant_text="response",
        )
        events.insert(
            2,
            {
                "type": "user.message",
                "data": {"content": "<turn_aborted>User cancelled the turn</turn_aborted>"},
                "timestamp": "2026-04-12T04:04:50.000Z",
            },
        )
        events_path = _write_copilot_events(copilot_root, "session-3", events)

        transcript = parse_copilot_session(events_path)

        assert len(transcript.events) == 2
        assert all(evt["content"] != "<turn_aborted>" for evt in transcript.events)

    def test_skips_system_injected_current_datetime_messages(
        self, tmp_path: Path
    ) -> None:
        """Test that <current_datetime> system messages are skipped."""
        copilot_root = tmp_path / ".copilot"
        events = _copilot_events(
            session_id="session-4",
            user_text="what time is it",
            assistant_text="It's 2026-04-12",
        )
        events.insert(
            2,
            {
                "type": "user.message",
                "data": {"content": "<current_datetime>2026-04-12T04:05:00Z</current_datetime>"},
                "timestamp": "2026-04-12T04:05:00.000Z",
            },
        )
        events_path = _write_copilot_events(copilot_root, "session-4", events)

        transcript = parse_copilot_session(events_path)

        assert len(transcript.events) == 2
        assert all("<current_datetime>" not in evt["content"] for evt in transcript.events)

    def test_skips_reminder_system_messages(self, tmp_path: Path) -> None:
        """Test that <reminder> system messages are skipped."""
        copilot_root = tmp_path / ".copilot"
        events = _copilot_events(
            session_id="session-5",
            user_text="build the project",
            assistant_text="Running build",
        )
        events.insert(
            2,
            {
                "type": "user.message",
                "data": {"content": "<reminder>Remember to run tests</reminder>"},
                "timestamp": "2026-04-12T04:04:55.000Z",
            },
        )
        events_path = _write_copilot_events(copilot_root, "session-5", events)

        transcript = parse_copilot_session(events_path)

        assert len(transcript.events) == 2
        assert all("<reminder>" not in evt["content"] for evt in transcript.events)

    def test_skips_system_notification_messages(self, tmp_path: Path) -> None:
        """Test that <system_notification> system messages are skipped."""
        copilot_root = tmp_path / ".copilot"
        events = _copilot_events(
            session_id="session-6",
            user_text="continue coding",
            assistant_text="Here's the next part",
        )
        events.insert(
            2,
            {
                "type": "user.message",
                "data": {
                    "content": "<system_notification>Session limit approaching</system_notification>"
                },
                "timestamp": "2026-04-12T04:04:58.000Z",
            },
        )
        events_path = _write_copilot_events(copilot_root, "session-6", events)

        transcript = parse_copilot_session(events_path)

        assert len(transcript.events) == 2
        assert all("<system_notification>" not in evt["content"] for evt in transcript.events)

    def test_deduplicates_identical_user_messages(self, tmp_path: Path) -> None:
        """Test that identical user messages are deduplicated."""
        copilot_root = tmp_path / ".copilot"
        events = _copilot_events(
            session_id="session-7",
            user_text="duplicate message",
            assistant_text="first response",
        )
        # Add another identical user message
        events.append(
            {
                "type": "user.message",
                "data": {"content": "duplicate message"},
                "timestamp": "2026-04-12T04:06:00.000Z",
            }
        )
        events.append(
            {
                "type": "assistant.message",
                "data": {"content": "second response"},
                "timestamp": "2026-04-12T04:06:05.000Z",
            }
        )
        events_path = _write_copilot_events(copilot_root, "session-7", events)

        transcript = parse_copilot_session(events_path)

        # Should deduplicate the second identical user message
        user_messages = [evt for evt in transcript.events if evt["role"] == "user"]
        assert len(user_messages) == 1
        assert user_messages[0]["content"] == "duplicate message"

    def test_deduplicates_identical_assistant_messages(self, tmp_path: Path) -> None:
        """Test that identical assistant messages are deduplicated."""
        copilot_root = tmp_path / ".copilot"
        events = _copilot_events(
            session_id="session-8",
            user_text="ask something",
            assistant_text="identical response",
        )
        events.append(
            {
                "type": "user.message",
                "data": {"content": "ask again"},
                "timestamp": "2026-04-12T04:06:00.000Z",
            }
        )
        events.append(
            {
                "type": "assistant.message",
                "data": {"content": "identical response"},
                "timestamp": "2026-04-12T04:06:05.000Z",
            }
        )
        events_path = _write_copilot_events(copilot_root, "session-8", events)

        transcript = parse_copilot_session(events_path)

        # Should deduplicate the second identical assistant message
        assistant_messages = [evt for evt in transcript.events if evt["role"] == "assistant"]
        assert len(assistant_messages) == 1
        assert assistant_messages[0]["content"] == "identical response"

    def test_deduplicates_based_on_first_200_chars(self, tmp_path: Path) -> None:
        """Test that deduplication is based on first 200 chars of message."""
        copilot_root = tmp_path / ".copilot"
        long_message = "x" * 300
        events = _copilot_events(
            session_id="session-9",
            user_text=long_message,
            assistant_text="response",
        )
        # Add a second message that differs after 200 chars
        different_after_200 = "x" * 200 + "different suffix"
        events.append(
            {
                "type": "user.message",
                "data": {"content": different_after_200},
                "timestamp": "2026-04-12T04:06:00.000Z",
            }
        )
        events_path = _write_copilot_events(copilot_root, "session-9", events)

        transcript = parse_copilot_session(events_path)

        # Should deduplicate because first 200 chars are the same
        user_messages = [evt for evt in transcript.events if evt["role"] == "user"]
        assert len(user_messages) == 1

    def test_handles_missing_timestamp_gracefully(self, tmp_path: Path) -> None:
        """Test that events without timestamp field are handled gracefully."""
        copilot_root = tmp_path / ".copilot"
        events = _copilot_events(
            session_id="session-10",
            user_text="question",
            assistant_text="answer",
        )
        # Remove timestamp from the user message
        events[1]["timestamp"] = None
        events_path = _write_copilot_events(copilot_root, "session-10", events)

        transcript = parse_copilot_session(events_path)

        # Should still parse the message
        assert len(transcript.events) == 2
        assert transcript.events[0]["role"] == "user"
        assert "ts" not in transcript.events[0] or transcript.events[0].get("ts") is None

    def test_skips_empty_messages(self, tmp_path: Path) -> None:
        """Test that empty messages are skipped."""
        copilot_root = tmp_path / ".copilot"
        events = _copilot_events(
            session_id="session-11",
            user_text="real message",
            assistant_text="real response",
        )
        # Add an empty user message
        events.insert(
            2,
            {
                "type": "user.message",
                "data": {"content": ""},
                "timestamp": "2026-04-12T04:05:00.000Z",
            },
        )
        # Add an empty assistant message
        events.insert(
            4,
            {
                "type": "assistant.message",
                "data": {"content": "   "},
                "timestamp": "2026-04-12T04:05:01.000Z",
            },
        )
        events_path = _write_copilot_events(copilot_root, "session-11", events)

        transcript = parse_copilot_session(events_path)

        # Should skip empty/whitespace-only messages
        assert len(transcript.events) == 2
        assert all(evt["content"].strip() for evt in transcript.events)

    def test_handles_partial_json_lines(self, tmp_path: Path) -> None:
        """Test that partial JSON lines at end of file are skipped gracefully."""
        copilot_root = tmp_path / ".copilot"
        events = _copilot_events(
            session_id="session-12",
            user_text="complete message",
            assistant_text="complete response",
        )
        events_path = _write_copilot_events(
            copilot_root,
            "session-12",
            events,
            trailing_fragment='{"type":"user.message","data":{"content":"incomplete',
        )

        transcript = parse_copilot_session(events_path)

        # Should parse the complete events and skip the incomplete line
        assert len(transcript.events) == 2
        assert transcript.events[0]["content"] == "complete message"


class TestUmxSessionIdProperty:
    """Tests for the umx_session_id property of CopilotTranscript."""

    def test_umx_session_id_format(self, tmp_path: Path) -> None:
        """Test that umx_session_id has the correct format: {date}-copilot-{first12chars}."""
        copilot_root = tmp_path / ".copilot"
        events_path = _write_copilot_events(
            copilot_root,
            "abc-def-123-xyz-456",
            _copilot_events(session_id="abc-def-123-xyz-456"),
        )

        transcript = parse_copilot_session(events_path)

        # Format should be: {date}-copilot-{first12chars}
        assert transcript.umx_session_id == "2026-04-12-copilot-abc-def-123-"

    def test_umx_session_id_extracts_date_from_started_timestamp(
        self, tmp_path: Path
    ) -> None:
        """Test that date is extracted from the started timestamp."""
        copilot_root = tmp_path / ".copilot"
        events_path = _write_copilot_events(
            copilot_root,
            "session-xyz",
            _copilot_events(session_id="session-xyz"),
        )

        transcript = parse_copilot_session(events_path)

        # Should extract date from startTime
        assert transcript.umx_session_id.startswith("2026-04-12-copilot-")

    def test_umx_session_id_uses_default_date_if_no_started(self, tmp_path: Path) -> None:
        """Test that default date is used if started timestamp is not available."""
        copilot_root = tmp_path / ".copilot"
        events = [
            {
                "type": "session.start",
                "data": {
                    "sessionId": "no-timestamp-session",
                    "context": {"cwd": "/home/user"},
                },
            }
        ]
        events_path = _write_copilot_events(copilot_root, "no-timestamp-session", events)

        transcript = parse_copilot_session(events_path)

        # Should use default date 1970-01-01
        assert transcript.umx_session_id.startswith("1970-01-01-copilot-")


class TestListCopilotSessions:
    """Tests for list_copilot_sessions function."""

    def test_finds_events_jsonl_files_in_session_state_dirs(self, tmp_path: Path) -> None:
        """Test that list_copilot_sessions finds events.jsonl files in session-state dirs."""
        copilot_root = tmp_path / ".copilot"
        session1 = _write_copilot_events(
            copilot_root,
            "session-1",
            _copilot_events(session_id="session-1"),
        )
        session2 = _write_copilot_events(
            copilot_root,
            "session-2",
            _copilot_events(session_id="session-2"),
        )

        session_state_dir = copilot_root / "session-state"
        sessions = list_copilot_sessions(session_state_dir)

        assert len(sessions) == 2
        assert session1 in sessions
        assert session2 in sessions

    def test_returns_empty_list_if_root_does_not_exist(self, tmp_path: Path) -> None:
        """Test that empty list is returned if session-state root does not exist."""
        nonexistent = tmp_path / ".copilot" / "session-state"
        sessions = list_copilot_sessions(nonexistent)
        assert sessions == []

    def test_returns_sessions_sorted_by_modification_time(self, tmp_path: Path) -> None:
        """Test that sessions are sorted by modification time (oldest first)."""
        copilot_root = tmp_path / ".copilot"
        older = _write_copilot_events(
            copilot_root,
            "older-session",
            _copilot_events(session_id="older-session"),
        )
        newer = _write_copilot_events(
            copilot_root,
            "newer-session",
            _copilot_events(session_id="newer-session"),
        )
        # Set modification times
        os.utime(older, (1, 1))
        os.utime(newer, (2, 2))

        session_state_dir = copilot_root / "session-state"
        sessions = list_copilot_sessions(session_state_dir)

        # Should be sorted by modification time
        assert sessions[0] == older
        assert sessions[1] == newer


class TestLatestCopilotSessionPath:
    """Tests for latest_copilot_session_path function."""

    def test_returns_most_recently_modified_session(self, tmp_path: Path) -> None:
        """Test that latest_copilot_session_path returns the most recently modified session."""
        copilot_root = tmp_path / ".copilot"
        older = _write_copilot_events(
            copilot_root,
            "older",
            _copilot_events(session_id="older"),
        )
        newer = _write_copilot_events(
            copilot_root,
            "newer",
            _copilot_events(session_id="newer"),
        )
        os.utime(older, (1, 1))
        os.utime(newer, (2, 2))

        session_state_dir = copilot_root / "session-state"
        latest = latest_copilot_session_path(session_state_dir)

        assert latest == newer

    def test_returns_none_if_no_sessions_exist(self, tmp_path: Path) -> None:
        """Test that latest_copilot_session_path returns None if no sessions exist."""
        nonexistent = tmp_path / ".copilot" / "session-state"
        latest = latest_copilot_session_path(nonexistent)
        assert latest is None


class TestCaptureCopilotSession:
    """Tests for capture_copilot_session function and CLI integration."""

    def test_capture_copilot_session_writes_umx_session_file(
        self,
        tmp_path: Path,
        project_dir: Path,
        project_repo: Path,
    ) -> None:
        """Test that capture_copilot_session writes a session file to the umx repository."""
        copilot_root = tmp_path / ".copilot"
        events_path = _write_copilot_events(
            copilot_root,
            "capture-test-session",
            _copilot_events(
                session_id="capture-test-session",
                model="claude-opus-4.6",
                user_text="build the project",
                assistant_text="I'll help you build it",
            ),
        )

        runner = CliRunner()
        capture = runner.invoke(
            main,
            [
                "capture",
                "copilot",
                "--cwd",
                str(project_dir),
                "--file",
                str(events_path),
            ],
        )
        assert capture.exit_code == 0, capture.output
        payload = json.loads(capture.output)

        umx_session_id = payload["umx_session_id"]
        assert payload["tool"] == "copilot"
        assert payload["events_imported"] == 2
        assert session_path(project_repo, umx_session_id).exists()

    def test_capture_preserves_copilot_metadata(
        self,
        tmp_path: Path,
        project_dir: Path,
        project_repo: Path,
    ) -> None:
        """Test that capture_copilot_session preserves Copilot-specific metadata."""
        copilot_root = tmp_path / ".copilot"
        events_path = _write_copilot_events(
            copilot_root,
            "metadata-test",
            _copilot_events(
                session_id="metadata-test",
                copilot_version="1.0.25",
                cwd="/home/user/gitmem",
                model="gpt-5.4",
                user_text="optimize performance",
                assistant_text="I suggest caching",
            ),
        )

        runner = CliRunner()
        capture = runner.invoke(
            main,
            [
                "capture",
                "copilot",
                "--cwd",
                str(project_dir),
                "--file",
                str(events_path),
            ],
        )
        assert capture.exit_code == 0, capture.output
        payload = json.loads(capture.output)

        # Read the session file to verify metadata
        umx_session_id = payload["umx_session_id"]
        session_file = session_path(project_repo, umx_session_id)
        # Session files are JSONL format; first line is _meta
        lines = session_file.read_text().splitlines()
        first_line = json.loads(lines[0])
        meta = first_line.get("_meta", {})

        assert meta["tool"] == "copilot"
        assert meta["copilot_version"] == "1.0.25"
        assert meta["copilot_cwd"] == "/home/user/gitmem"
        assert meta["copilot_model"] == "gpt-5.4"
        assert meta["source"] == "copilot-cli"

    def test_capture_with_system_injected_messages_filters_correctly(
        self,
        tmp_path: Path,
        project_dir: Path,
        project_repo: Path,
    ) -> None:
        """Test that system-injected messages are filtered during capture."""
        copilot_root = tmp_path / ".copilot"
        events = _copilot_events(
            session_id="filter-test",
            user_text="real user question",
            assistant_text="real response",
        )
        # Insert system-injected messages
        events.insert(
            2,
            {
                "type": "user.message",
                "data": {"content": "<environment_context>cwd: /home/user</environment_context>"},
                "timestamp": "2026-04-12T04:04:50.000Z",
            },
        )
        events.insert(
            3,
            {
                "type": "user.message",
                "data": {"content": "<current_datetime>2026-04-12T04:05:00Z</current_datetime>"},
                "timestamp": "2026-04-12T04:04:55.000Z",
            },
        )
        events_path = _write_copilot_events(copilot_root, "filter-test", events)

        runner = CliRunner()
        capture = runner.invoke(
            main,
            [
                "capture",
                "copilot",
                "--cwd",
                str(project_dir),
                "--file",
                str(events_path),
            ],
        )
        assert capture.exit_code == 0, capture.output
        payload = json.loads(capture.output)

        # Should only import the real user and assistant messages
        assert payload["events_imported"] == 2

    def test_capture_deduplicates_messages_on_import(
        self,
        tmp_path: Path,
        project_dir: Path,
        project_repo: Path,
    ) -> None:
        """Test that duplicate messages are deduplicated during capture."""
        copilot_root = tmp_path / ".copilot"
        events = _copilot_events(
            session_id="dedup-test",
            user_text="repeated question",
            assistant_text="response",
        )
        # Add duplicate user message
        events.append(
            {
                "type": "user.message",
                "data": {"content": "repeated question"},
                "timestamp": "2026-04-12T04:06:00.000Z",
            }
        )
        events.append(
            {
                "type": "assistant.message",
                "data": {"content": "another response"},
                "timestamp": "2026-04-12T04:06:05.000Z",
            }
        )
        events_path = _write_copilot_events(copilot_root, "dedup-test", events)

        runner = CliRunner()
        capture = runner.invoke(
            main,
            [
                "capture",
                "copilot",
                "--cwd",
                str(project_dir),
                "--file",
                str(events_path),
            ],
        )
        assert capture.exit_code == 0, capture.output
        payload = json.loads(capture.output)

        # Should deduplicate: 1 user + 2 assistant (not duplicate user counted)
        assert payload["events_imported"] == 3
