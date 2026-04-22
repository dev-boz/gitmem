"""Tests for OpenCode session capture."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from umx.opencode_capture import (
    OpenCodeSession,
    capture_opencode_session,
    latest_opencode_session,
    list_opencode_sessions,
)


def _create_opencode_db(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE session (
                id TEXT PRIMARY KEY,
                project_id TEXT,
                slug TEXT,
                directory TEXT,
                title TEXT,
                version TEXT,
                time_created INTEGER,
                time_updated INTEGER
            );

            CREATE TABLE message (
                id TEXT PRIMARY KEY,
                session_id TEXT,
                time_created INTEGER,
                data TEXT
            );

            CREATE TABLE part (
                id TEXT PRIMARY KEY,
                message_id TEXT,
                session_id TEXT,
                time_created INTEGER,
                data TEXT
            );
            """
        )
        conn.commit()
    finally:
        conn.close()
    return path


def _insert_session(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    directory: str | None,
    slug: str = "sample",
    title: str = "Sample Session",
    version: str = "1.2.27",
    time_created: int = 1000,
    time_updated: int = 1000,
) -> None:
    conn.execute(
        """
        INSERT INTO session (id, project_id, slug, directory, title, version, time_created, time_updated)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            session_id,
            "global",
            slug,
            directory,
            title,
            version,
            time_created,
            time_updated,
        ),
    )


def _insert_message(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    message_id: str,
    role: str,
    time_created: int,
    parts: list[dict],
) -> None:
    conn.execute(
        "INSERT INTO message (id, session_id, time_created, data) VALUES (?, ?, ?, ?)",
        (message_id, session_id, time_created, json.dumps({"role": role})),
    )
    for index, part in enumerate(parts, start=1):
        conn.execute(
            "INSERT INTO part (id, message_id, session_id, time_created, data) VALUES (?, ?, ?, ?, ?)",
            (
                f"{message_id}-p{index}",
                message_id,
                session_id,
                time_created + index,
                json.dumps(part),
            ),
        )


class TestListOpenCodeSessions:
    def test_list_and_filter_sessions(self, tmp_path: Path) -> None:
        db_path = _create_opencode_db(tmp_path / "opencode.db")
        conn = sqlite3.connect(db_path)
        try:
            _insert_session(
                conn,
                session_id="ses_exact",
                directory="/home/dinkum/project",
                time_created=1,
            )
            _insert_session(
                conn,
                session_id="ses_child",
                directory="/home/dinkum/project/sub",
                time_created=2,
            )
            _insert_session(
                conn,
                session_id="ses_other",
                directory="/home/dinkum/other",
                time_created=3,
            )
            conn.commit()
        finally:
            conn.close()

        all_sessions = list_opencode_sessions(source_root=db_path)
        assert [session.session_id for session in all_sessions] == [
            "ses_exact",
            "ses_child",
            "ses_other",
        ]

        filtered = list_opencode_sessions(
            project_root=Path("/home/dinkum/project"), source_root=db_path
        )
        # Exact matches win over prefix matches.
        assert [session.session_id for session in filtered] == ["ses_exact"]

    def test_prefix_fallback_when_no_exact_match(self, tmp_path: Path) -> None:
        db_path = _create_opencode_db(tmp_path / "opencode.db")
        conn = sqlite3.connect(db_path)
        try:
            _insert_session(
                conn,
                session_id="ses_one",
                directory="/home/dinkum/project/sub",
                time_created=1,
            )
            _insert_session(
                conn,
                session_id="ses_two",
                directory="/home/dinkum/project/tools",
                time_created=2,
            )
            _insert_session(
                conn,
                session_id="ses_three",
                directory="/home/dinkum/other",
                time_created=3,
            )
            conn.commit()
        finally:
            conn.close()

        filtered = list_opencode_sessions(
            project_root=Path("/home/dinkum/project"), source_root=db_path
        )
        assert [session.session_id for session in filtered] == ["ses_one", "ses_two"]

    def test_empty_or_missing_db_returns_empty(self, tmp_path: Path) -> None:
        db_path = _create_opencode_db(tmp_path / "opencode.db")
        assert list_opencode_sessions(source_root=db_path) == []
        assert list_opencode_sessions(source_root=tmp_path / "missing.db") == []


class TestLatestOpenCodeSession:
    def test_latest_uses_newest_time_created(self, tmp_path: Path) -> None:
        db_path = _create_opencode_db(tmp_path / "opencode.db")
        conn = sqlite3.connect(db_path)
        try:
            _insert_session(
                conn, session_id="ses_old", directory="/repo", time_created=1000
            )
            _insert_session(
                conn, session_id="ses_new", directory="/repo", time_created=2000
            )
            conn.commit()
        finally:
            conn.close()

        latest = latest_opencode_session(
            project_root=Path("/repo"), source_root=db_path
        )
        assert latest is not None
        assert latest.session_id == "ses_new"


class TestMessageExtraction:
    def test_only_text_parts_are_joined_and_empty_messages_skipped(
        self, tmp_path: Path
    ) -> None:
        db_path = _create_opencode_db(tmp_path / "opencode.db")
        conn = sqlite3.connect(db_path)
        try:
            _insert_session(
                conn,
                session_id="ses_extract",
                directory="/repo",
                time_created=1712664000000,
            )
            _insert_message(
                conn,
                session_id="ses_extract",
                message_id="m_user",
                role="user",
                time_created=1712664001000,
                parts=[
                    {"type": "tool", "text": "skip"},
                    {"type": "text", "text": "First text"},
                    {"type": "text", "text": "Second text"},
                    {"type": "text", "text": "   "},
                ],
            )
            _insert_message(
                conn,
                session_id="ses_extract",
                message_id="m_tool_only",
                role="assistant",
                time_created=1712664002000,
                parts=[
                    {"type": "reasoning", "text": "skip"},
                    {"type": "tool", "text": "skip"},
                ],
            )
            _insert_message(
                conn,
                session_id="ses_extract",
                message_id="m_assistant",
                role="assistant",
                time_created=1712664003000,
                parts=[
                    {"type": "text", "text": "Final answer"},
                ],
            )
            conn.commit()
        finally:
            conn.close()

        sessions = list_opencode_sessions(source_root=db_path)
        assert len(sessions) == 1
        transcript = sessions[0]
        assert [event["role"] for event in transcript.events] == ["user", "assistant"]
        assert transcript.events[0]["content"] == "First text\n\nSecond text"
        assert transcript.events[1]["content"] == "Final answer"
        assert all("ts" in event for event in transcript.events)


class TestCapture:
    def test_capture_writes_session(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from umx.config import default_config, save_config
        from umx.scope import config_path
        from umx.scope import init_local_umx, init_project_memory
        from umx.scope import project_memory_dir
        from umx.sessions import read_session, session_path

        home = tmp_path / "umxhome"
        monkeypatch.setenv("UMX_HOME", str(home))
        init_local_umx()
        save_config(config_path(), default_config())

        project = tmp_path / "project"
        project.mkdir()
        (project / ".git").mkdir()
        init_project_memory(project)

        session = OpenCodeSession(
            session_id="ses_28fb6a341ffeaupTJ2rub5rLaB",
            slug="happy-planet",
            directory=str(project),
            title="Add opencode capture",
            version="1.2.27",
            time_created=1712664000000,
            events=[
                {
                    "role": "user",
                    "content": "Please wire this in.",
                    "ts": "2026-04-09T00:00:01Z",
                },
                {"role": "assistant", "content": "Done.", "ts": "2026-04-09T00:00:03Z"},
            ],
        )

        result = capture_opencode_session(project, session)
        assert result["tool"] == "opencode"
        assert result["events_imported"] == 2

        repo = project_memory_dir(project)
        saved = session_path(repo, result["umx_session_id"])
        assert saved.exists()

        payload = read_session(saved)
        meta = payload[0]["_meta"]
        assert meta["tool"] == "opencode"
        assert meta["opencode_session_id"] == session.session_id
        assert meta["opencode_slug"] == "happy-planet"

    def test_capture_uses_collision_resistant_umx_session_ids(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from umx.config import default_config, save_config
        from umx.scope import config_path
        from umx.scope import init_local_umx, init_project_memory
        from umx.scope import project_memory_dir
        from umx.sessions import session_path

        home = tmp_path / "umxhome"
        monkeypatch.setenv("UMX_HOME", str(home))
        init_local_umx()
        save_config(config_path(), default_config())

        project = tmp_path / "project"
        project.mkdir()
        (project / ".git").mkdir()
        init_project_memory(project)

        first = OpenCodeSession(
            session_id="ses_28fb6a341ffeaupTJ2rub5rLaB",
            slug="happy-planet",
            directory=str(project),
            title="Session A",
            version="1.2.27",
            time_created=1712664000000,
            events=[{"role": "user", "content": "First"}],
        )
        second = OpenCodeSession(
            session_id="ses_28fb6a341ffeZZZZZZZZZZZZZZ",
            slug="happy-planet",
            directory=str(project),
            title="Session B",
            version="1.2.27",
            time_created=1712664000000,
            events=[{"role": "user", "content": "Second"}],
        )

        first_result = capture_opencode_session(project, first)
        second_result = capture_opencode_session(project, second)

        assert first_result["umx_session_id"] != second_result["umx_session_id"]
        repo = project_memory_dir(project)
        assert session_path(repo, first_result["umx_session_id"]).exists()
        assert session_path(repo, second_result["umx_session_id"]).exists()
