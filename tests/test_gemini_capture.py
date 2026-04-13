"""Tests for Gemini CLI session capture."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from umx.gemini_capture import (
    GeminiTranscript,
    _project_slug_for_cwd,
    _gemini_chats_dir,
    capture_gemini_session,
    latest_gemini_session_path,
    list_gemini_sessions,
    parse_gemini_session,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_gemini_session(path: Path, data: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _minimal_gemini_session(session_id: str = "abc12345-0000-0000-0000-000000000000") -> dict:
    return {
        "sessionId": session_id,
        "projectHash": "hash123",
        "startTime": "2026-04-10T10:00:00.000Z",
        "lastUpdated": "2026-04-10T10:05:00.000Z",
        "messages": [
            {
                "type": "user",
                "timestamp": "2026-04-10T10:00:01.000Z",
                "content": [{"text": "Hello Gemini"}]
            },
            {
                "type": "gemini",
                "timestamp": "2026-04-10T10:00:05.000Z",
                "content": "Hello User"
            }
        ]
    }


# ---------------------------------------------------------------------------
# Unit tests for helper functions
# ---------------------------------------------------------------------------

class TestProjectSlug:
    def test_exact_match(self, tmp_path: Path):
        projects_root = tmp_path / "gemini"
        projects_root.mkdir()
        cwd = tmp_path / "myproject"
        cwd.mkdir()
        projects_json = projects_root / "projects.json"
        projects_json.write_text(json.dumps({
            "projects": {
                str(cwd.resolve()): "my-slug"
            }
        }))
        assert _project_slug_for_cwd(cwd, source_root=projects_root) == "my-slug"

    def test_longest_prefix_match(self, tmp_path: Path):
        projects_root = tmp_path / "gemini"
        projects_root.mkdir()
        parent = tmp_path / "parent"
        child = parent / "child"
        child.mkdir(parents=True)
        projects_json = projects_root / "projects.json"
        projects_json.write_text(json.dumps({
            "projects": {
                str(parent.resolve()): "parent-slug",
                str(child.resolve()): "child-slug"
            }
        }))
        assert _project_slug_for_cwd(child, source_root=projects_root) == "child-slug"
        assert _project_slug_for_cwd(parent, source_root=projects_root) == "parent-slug"

    def test_no_match(self, tmp_path: Path):
        projects_root = tmp_path / "gemini"
        projects_root.mkdir()
        cwd = tmp_path / "other"
        cwd.mkdir()
        projects_json = projects_root / "projects.json"
        projects_json.write_text(json.dumps({
            "projects": {
                "/some/other/path": "other-slug"
            }
        }))
        assert _project_slug_for_cwd(cwd, source_root=projects_root) is None


# ---------------------------------------------------------------------------
# Parse tests
# ---------------------------------------------------------------------------

class TestParseGeminiSession:
    def test_basic_parse(self, tmp_path: Path):
        session_id = "abc12345-dead-beef-cafe-000000000000"
        path = tmp_path / "tmp" / "myslug" / "chats" / f"session-{session_id}.json"
        _write_gemini_session(path, _minimal_gemini_session(session_id))
        
        transcript = parse_gemini_session(path)
        assert transcript.session_id == session_id
        assert transcript.project_slug == "myslug"
        assert transcript.start_time == "2026-04-10T10:00:00.000Z"
        assert len(transcript.events) == 2
        assert transcript.events[0]["role"] == "user"
        assert transcript.events[0]["content"] == "Hello Gemini"
        assert transcript.events[1]["role"] == "assistant"
        assert transcript.events[1]["content"] == "Hello User"

    def test_skips_info_messages(self, tmp_path: Path):
        session_id = "info-test"
        path = tmp_path / "tmp" / "slug" / "chats" / "session-info.json"
        data = _minimal_gemini_session(session_id)
        data["messages"].insert(0, {"type": "info", "content": "Update available"})
        _write_gemini_session(path, data)
        
        transcript = parse_gemini_session(path)
        assert len(transcript.events) == 2
        assert "Update available" not in [e["content"] for e in transcript.events]

    def test_skips_empty_gemini_messages(self, tmp_path: Path):
        session_id = "empty-test"
        path = tmp_path / "tmp" / "slug" / "chats" / "session-empty.json"
        data = _minimal_gemini_session(session_id)
        data["messages"].append({"type": "gemini", "content": "", "timestamp": "..."})
        _write_gemini_session(path, data)
        
        transcript = parse_gemini_session(path)
        assert len(transcript.events) == 2

    def test_user_content_list_joined(self, tmp_path: Path):
        session_id = "join-test"
        path = tmp_path / "tmp" / "slug" / "chats" / "session-join.json"
        data = {
            "sessionId": session_id,
            "messages": [
                {
                    "type": "user",
                    "content": [{"text": "Part 1"}, {"text": " Part 2"}]
                }
            ]
        }
        _write_gemini_session(path, data)
        
        transcript = parse_gemini_session(path)
        assert len(transcript.events) == 1
        assert transcript.events[0]["content"] == "Part 1 Part 2"

    def test_deduplicates_identical_messages(self, tmp_path: Path):
        session_id = "dup-test"
        path = tmp_path / "tmp" / "slug" / "chats" / "session-dup.json"
        data = _minimal_gemini_session(session_id)
        data["messages"].append(data["messages"][0])
        _write_gemini_session(path, data)
        
        transcript = parse_gemini_session(path)
        assert len(transcript.events) == 2

    def test_umx_session_id_format(self, tmp_path: Path):
        session_id = "fff12345-0000-0000-0000-000000000000"
        path = tmp_path / "tmp" / "slug" / "chats" / "session-id.json"
        _write_gemini_session(path, _minimal_gemini_session(session_id))
        transcript = parse_gemini_session(path)
        assert transcript.umx_session_id == "2026-04-10-gemini-fff12345"

    def test_malformed_json_skipped(self, tmp_path: Path):
        path = tmp_path / "malformed.json"
        path.write_text("{not valid}")
        transcript = parse_gemini_session(path)
        assert transcript.events == []

    def test_empty_file(self, tmp_path: Path):
        path = tmp_path / "empty.json"
        path.write_text("")
        transcript = parse_gemini_session(path)
        assert transcript.events == []


# ---------------------------------------------------------------------------
# List / discovery tests
# ---------------------------------------------------------------------------

class TestListGeminiSessions:
    def test_finds_sessions_for_project(self, tmp_path: Path):
        gemini_root = tmp_path / "gemini"
        project_root = tmp_path / "myproject"
        project_root.mkdir()
        
        projects_json = gemini_root / "projects.json"
        projects_json.parent.mkdir(parents=True)
        projects_json.write_text(json.dumps({
            "projects": {str(project_root.resolve()): "my-slug"}
        }))
        
        chats_dir = gemini_root / "tmp" / "my-slug" / "chats"
        chats_dir.mkdir(parents=True)
        session_file = chats_dir / "session-1.json"
        session_file.write_text("{}")
        
        found = list_gemini_sessions(project_root=project_root, source_root=gemini_root)
        assert session_file in found

    def test_latest_session(self, tmp_path: Path):
        gemini_root = tmp_path / "gemini"
        project_root = tmp_path / "myproject"
        project_root.mkdir()
        
        projects_json = gemini_root / "projects.json"
        projects_json.parent.mkdir(parents=True)
        projects_json.write_text(json.dumps({
            "projects": {str(project_root.resolve()): "my-slug"}
        }))
        
        chats_dir = gemini_root / "tmp" / "my-slug" / "chats"
        chats_dir.mkdir(parents=True)
        
        old = chats_dir / "session-old.json"
        old.write_text("{}")
        import time; time.sleep(0.01)
        new = chats_dir / "session-new.json"
        new.write_text("{}")
        
        latest = latest_gemini_session_path(project_root=project_root, source_root=gemini_root)
        assert latest == new


# ---------------------------------------------------------------------------
# Integration: capture writes into repo
# ---------------------------------------------------------------------------

class TestCapture:
    def test_capture_writes_session(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        from umx.scope import init_local_umx, init_project_memory
        from umx.sessions import read_session, session_path
        from umx.scope import project_memory_dir
        from umx.config import default_config, save_config
        from umx.scope import config_path

        home = tmp_path / "umxhome"
        monkeypatch.setenv("UMX_HOME", str(home))
        init_local_umx()
        save_config(config_path(), default_config())

        project = tmp_path / "project"
        project.mkdir()
        (project / ".git").mkdir()
        init_project_memory(project)

        session_id = "aaa11111-0000-0000-0000-000000000000"
        # Setup gemini path structure for parse_gemini_session to find the slug
        session_path_in_gemini = tmp_path / "gemini" / "tmp" / "slug" / "chats" / f"session-{session_id}.json"
        _write_gemini_session(session_path_in_gemini, _minimal_gemini_session(session_id))

        result = capture_gemini_session(project, session_path_in_gemini)

        assert result["tool"] == "gemini"
        assert result["events_imported"] == 2
        assert "umx_session_id" in result

        repo = project_memory_dir(project)
        path = session_path(repo, result["umx_session_id"])
        assert path.exists()
        session_data = read_session(path)
        meta = session_data[0]["_meta"]
        assert meta["tool"] == "gemini"
        assert meta["gemini_session_id"] == session_id
        assert meta["gemini_project_slug"] == "slug"
