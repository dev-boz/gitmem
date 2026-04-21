"""Tests for Claude Code session capture."""
from __future__ import annotations

import json
import threading
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from umx.cli import main
from umx.git_ops import GitCommitResult
from umx.claude_code_capture import (
    ClaudeCodeTranscript,
    _extract_text,
    _is_tool_result_only,
    _project_hash,
    capture_claude_code_session,
    latest_claude_code_session_path,
    list_claude_code_sessions,
    parse_claude_code_session,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_session(path: Path, records: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record) + "\n")
    return path


def _minimal_session(session_id: str = "abc12345-0000-0000-0000-000000000000") -> list[dict]:
    return [
        {
            "type": "system",
            "subtype": "turn_duration",
            "sessionId": session_id,
            "cwd": "/home/test/project",
            "version": "2.1.0",
            "slug": "test-slug",
            "timestamp": "2026-01-15T10:00:00.000Z",
        },
        {
            "type": "user",
            "isSidechain": False,
            "uuid": "u1",
            "parentUuid": None,
            "message": {
                "role": "user",
                "content": "How does this work?",
            },
            "timestamp": "2026-01-15T10:00:01.000Z",
        },
        {
            "type": "assistant",
            "isSidechain": False,
            "uuid": "a1",
            "parentUuid": "u1",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "Let me think..."},
                    {"type": "text", "text": "It works by doing X."},
                ],
            },
            "timestamp": "2026-01-15T10:00:05.000Z",
        },
    ]


# ---------------------------------------------------------------------------
# Unit tests for helper functions
# ---------------------------------------------------------------------------

class TestProjectHash:
    def test_root_path(self):
        assert _project_hash(Path("/")) == "-"

    def test_home_path(self):
        assert _project_hash(Path("/home/user")) == "-home-user"

    def test_nested_path(self):
        assert _project_hash(Path("/home/user/project")) == "-home-user-project"


class TestExtractText:
    def test_plain_string(self):
        assert _extract_text("Hello world") == "Hello world"

    def test_list_with_text_item(self):
        content = [{"type": "text", "text": "Some text"}]
        assert _extract_text(content) == "Some text"

    def test_list_skips_thinking(self):
        content = [
            {"type": "thinking", "thinking": "private thoughts"},
            {"type": "text", "text": "Public response"},
        ]
        assert _extract_text(content) == "Public response"

    def test_list_skips_tool_use(self):
        content = [
            {"type": "tool_use", "id": "tool_1", "name": "Bash"},
            {"type": "text", "text": "Done."},
        ]
        assert _extract_text(content) == "Done."

    def test_multiple_text_items_joined(self):
        content = [
            {"type": "text", "text": "First part."},
            {"type": "text", "text": "Second part."},
        ]
        assert _extract_text(content) == "First part.\n\nSecond part."

    def test_empty_list(self):
        assert _extract_text([]) == ""

    def test_only_thinking(self):
        content = [{"type": "thinking", "thinking": "..."}]
        assert _extract_text(content) == ""

    def test_non_list_non_string(self):
        assert _extract_text(None) == ""
        assert _extract_text(42) == ""


class TestIsToolResultOnly:
    def test_all_tool_results(self):
        content = [
            {"type": "tool_result", "tool_use_id": "t1", "content": "ok"},
            {"type": "tool_result", "tool_use_id": "t2", "content": "ok"},
        ]
        assert _is_tool_result_only(content) is True

    def test_mixed_content(self):
        content = [
            {"type": "text", "text": "User said something"},
            {"type": "tool_result", "tool_use_id": "t1", "content": "ok"},
        ]
        assert _is_tool_result_only(content) is False

    def test_plain_string(self):
        assert _is_tool_result_only("plain text") is False

    def test_empty_list(self):
        assert _is_tool_result_only([]) is True  # vacuously true, but no text content


# ---------------------------------------------------------------------------
# Parse tests
# ---------------------------------------------------------------------------

class TestParseClaudeCodeSession:
    def test_basic_parse(self, tmp_path: Path):
        session_id = "abc12345-dead-beef-cafe-000000000000"
        path = _write_session(
            tmp_path / f"{session_id}.jsonl",
            _minimal_session(session_id),
        )
        transcript = parse_claude_code_session(path)
        assert transcript.session_id == session_id
        assert transcript.cwd == "/home/test/project"
        assert transcript.version == "2.1.0"
        assert transcript.slug == "test-slug"
        assert transcript.started == "2026-01-15T10:00:00.000Z"
        assert len(transcript.events) == 2

    def test_events_content(self, tmp_path: Path):
        session_id = "aaa00000-0000-0000-0000-000000000000"
        path = _write_session(tmp_path / f"{session_id}.jsonl", _minimal_session(session_id))
        transcript = parse_claude_code_session(path)
        assert transcript.events[0]["role"] == "user"
        assert transcript.events[0]["content"] == "How does this work?"
        assert transcript.events[1]["role"] == "assistant"
        assert transcript.events[1]["content"] == "It works by doing X."

    def test_skips_sidechain_messages(self, tmp_path: Path):
        session_id = "bbb00000-0000-0000-0000-000000000000"
        records = _minimal_session(session_id) + [
            {
                "type": "user",
                "isSidechain": True,
                "uuid": "side1",
                "parentUuid": "u1",
                "message": {"role": "user", "content": "Sidechain message"},
                "timestamp": "2026-01-15T10:00:06.000Z",
            }
        ]
        path = _write_session(tmp_path / f"{session_id}.jsonl", records)
        transcript = parse_claude_code_session(path)
        texts = [e["content"] for e in transcript.events]
        assert "Sidechain message" not in texts

    def test_skips_tool_result_only_user_messages(self, tmp_path: Path):
        session_id = "ccc00000-0000-0000-0000-000000000000"
        records = _minimal_session(session_id) + [
            {
                "type": "user",
                "isSidechain": False,
                "uuid": "u2",
                "parentUuid": "a1",
                "message": {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "t1", "content": "output"},
                    ],
                },
                "timestamp": "2026-01-15T10:00:07.000Z",
            }
        ]
        path = _write_session(tmp_path / f"{session_id}.jsonl", records)
        transcript = parse_claude_code_session(path)
        # Only the original 2 messages, not the tool result
        assert len(transcript.events) == 2

    def test_skips_file_history_and_progress(self, tmp_path: Path):
        session_id = "ddd00000-0000-0000-0000-000000000000"
        records = [
            {"type": "file-history-snapshot", "messageId": "x", "snapshot": {}},
            {"type": "progress", "data": "..."},
        ] + _minimal_session(session_id)
        path = _write_session(tmp_path / f"{session_id}.jsonl", records)
        transcript = parse_claude_code_session(path)
        assert len(transcript.events) == 2

    def test_deduplicates_identical_messages(self, tmp_path: Path):
        session_id = "eee00000-0000-0000-0000-000000000000"
        duplicate = {
            "type": "user",
            "isSidechain": False,
            "uuid": "u_dup",
            "parentUuid": None,
            "message": {"role": "user", "content": "How does this work?"},
            "timestamp": "2026-01-15T10:00:10.000Z",
        }
        records = _minimal_session(session_id) + [duplicate]
        path = _write_session(tmp_path / f"{session_id}.jsonl", records)
        transcript = parse_claude_code_session(path)
        user_events = [e for e in transcript.events if e["role"] == "user"]
        assert len(user_events) == 1

    def test_umx_session_id_format(self, tmp_path: Path):
        session_id = "fff12345-0000-0000-0000-000000000000"
        path = _write_session(tmp_path / f"{session_id}.jsonl", _minimal_session(session_id))
        transcript = parse_claude_code_session(path)
        # Format: YYYY-MM-DD-claude-code-<first8chars>
        assert transcript.umx_session_id == "2026-01-15-claude-code-fff12345"

    def test_malformed_lines_skipped(self, tmp_path: Path):
        session_id = "ggg00000-0000-0000-0000-000000000000"
        path = tmp_path / f"{session_id}.jsonl"
        path.write_text('{"type": "user", "isSidechain": false, "uuid": "u1", "parentUuid": null, "message": {"role": "user", "content": "Valid"}, "timestamp": "2026-01-01T00:00:00.000Z"}\n{not valid json\n')
        transcript = parse_claude_code_session(path)
        assert len(transcript.events) == 1
        assert transcript.events[0]["content"] == "Valid"

    def test_empty_file(self, tmp_path: Path):
        path = tmp_path / "empty.jsonl"
        path.write_text("")
        transcript = parse_claude_code_session(path)
        assert transcript.events == []
        assert transcript.session_id == "empty"

    def test_timestamps_in_events(self, tmp_path: Path):
        session_id = "hhh00000-0000-0000-0000-000000000000"
        path = _write_session(tmp_path / f"{session_id}.jsonl", _minimal_session(session_id))
        transcript = parse_claude_code_session(path)
        assert all("ts" in e for e in transcript.events)


# ---------------------------------------------------------------------------
# List / discovery tests
# ---------------------------------------------------------------------------

class TestListSessions:
    def test_finds_sessions_for_project(self, tmp_path: Path):
        project_root = tmp_path / "myproject"
        project_root.mkdir()
        projects_root = tmp_path / "claude_projects"
        hash_dir = projects_root / "-myproject" / "subdir"
        # Incorrect: project hash for /tmp/.../myproject isn't "-myproject"
        # Use the correct hash for project_root
        from umx.claude_code_capture import _project_hash
        hash_name = _project_hash(project_root)
        hash_dir = projects_root / hash_name
        hash_dir.mkdir(parents=True)
        session_file = hash_dir / "session1.jsonl"
        session_file.write_text("")
        found = list_claude_code_sessions(project_root=project_root, source_root=projects_root)
        assert session_file in found

    def test_no_sessions_returns_empty(self, tmp_path: Path):
        project_root = tmp_path / "empty_project"
        project_root.mkdir()
        projects_root = tmp_path / "claude_projects"
        projects_root.mkdir()
        result = list_claude_code_sessions(project_root=project_root, source_root=projects_root)
        assert result == []

    def test_latest_session(self, tmp_path: Path):
        project_root = tmp_path / "myproject"
        project_root.mkdir()
        projects_root = tmp_path / "claude_projects"
        hash_name = _project_hash(project_root)
        hash_dir = projects_root / hash_name
        hash_dir.mkdir(parents=True)

        old = hash_dir / "old.jsonl"
        old.write_text("")
        import time; time.sleep(0.01)
        new = hash_dir / "new.jsonl"
        new.write_text("")

        latest = latest_claude_code_session_path(project_root=project_root, source_root=projects_root)
        assert latest == new


# ---------------------------------------------------------------------------
# Integration: capture writes into repo
# ---------------------------------------------------------------------------

class TestCapture:
    def test_capture_writes_session(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        from umx.scope import ensure_repo_structure, init_local_umx, init_project_memory
        from umx.sessions import read_session, session_path
        from umx.scope import project_memory_dir

        home = tmp_path / "umxhome"
        monkeypatch.setenv("UMX_HOME", str(home))
        init_local_umx()
        from umx.config import default_config, save_config
        from umx.scope import config_path
        save_config(config_path(), default_config())

        project = tmp_path / "project"
        project.mkdir()
        (project / ".git").mkdir()
        init_project_memory(project)

        session_id = "aaa11111-0000-0000-0000-000000000000"
        session_file = tmp_path / f"{session_id}.jsonl"
        _write_session(session_file, _minimal_session(session_id))

        result = capture_claude_code_session(project, session_file)

        assert result["tool"] == "claude-code"
        assert result["events_imported"] == 2
        assert "umx_session_id" in result

        repo = project_memory_dir(project)
        path = session_path(repo, result["umx_session_id"])
        assert path.exists()
        session_data = read_session(path)
        meta = session_data[0]["_meta"]
        assert meta["tool"] == "claude-code"
        assert meta["claude_code_session_id"] == session_id

    def test_cli_capture_all_preserves_order_and_commits_once(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from umx.config import default_config, save_config
        from umx.scope import config_path, init_local_umx, init_project_memory, project_memory_dir
        from umx.sessions import session_path

        home = tmp_path / "umxhome"
        monkeypatch.setenv("UMX_HOME", str(home))
        init_local_umx()
        save_config(config_path(), default_config())

        project = tmp_path / "project"
        project.mkdir()
        (project / ".git").mkdir()
        init_project_memory(project)

        source_root = tmp_path / "claude-projects"
        sessions_dir = source_root / _project_hash(project)
        first = sessions_dir / "aaa11111-0000-0000-0000-000000000000.jsonl"
        second = sessions_dir / "bbb22222-0000-0000-0000-000000000000.jsonl"
        _write_session(first, _minimal_session("aaa11111-0000-0000-0000-000000000000"))
        _write_session(second, _minimal_session("bbb22222-0000-0000-0000-000000000000"))

        barrier = threading.Barrier(2, timeout=2)
        original = parse_claude_code_session

        def parse_with_barrier(path: Path):
            transcript = original(path)
            barrier.wait()
            return transcript

        runner = CliRunner()
        with (
            patch(
                "umx.claude_code_capture.parse_claude_code_session",
                side_effect=parse_with_barrier,
            ),
            patch(
                "umx.git_ops.git_add_and_commit",
                return_value=GitCommitResult.committed_result(),
            ) as mock_commit,
        ):
            result = runner.invoke(
                main,
                [
                    "capture",
                    "claude-code",
                    "--cwd",
                    str(project),
                    "--source-root",
                    str(source_root),
                    "--all",
                ],
            )

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert [item["source_file"] for item in payload] == [str(first), str(second)]
        repo = project_memory_dir(project)
        assert all(session_path(repo, item["umx_session_id"]).exists() for item in payload)
        mock_commit.assert_called_once()
