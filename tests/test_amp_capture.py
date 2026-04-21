"""Tests for Amp CLI thread capture."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from umx.amp_capture import (
    capture_amp_thread,
    latest_amp_thread_path,
    list_amp_threads,
    parse_amp_thread,
)
from umx.cli import main
from umx.git_ops import GitCommitResult


def _write_amp_thread(path: Path, data: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _minimal_amp_thread(
    thread_id: str = "T-019d18bf-7b9d-7082-b525-1467aa335ca8",
    project_uri: str = "file:///tmp/project",
) -> dict:
    return {
        "v": 7,
        "id": thread_id,
        "created": 1774236695457,
        "agentMode": "smart",
        "messages": [
            {
                "role": "user",
                "messageId": 0,
                "content": [{"type": "text", "text": "How do we run tests?"}],
                "meta": {"sentAt": 1774236695489},
            },
            {
                "role": "assistant",
                "messageId": 1,
                "content": [{"type": "text", "text": "Run pytest -q from the repo root."}],
                "meta": {"sentAt": 1774236696489},
            },
        ],
        "env": {
            "initial": {
                "trees": [{"displayName": "project", "uri": project_uri}],
                "platform": {
                    "client": "VS Code CLI Execute Mode",
                    "clientVersion": "0.0.1774224286-g947cba",
                },
            }
        },
    }


class TestAmpThreadDiscovery:
    def test_list_threads_for_project(self, tmp_path: Path) -> None:
        amp_root = tmp_path / "amp"
        project = tmp_path / "project"
        project.mkdir()

        matching = _write_amp_thread(
            amp_root / "threads" / "T-match.json",
            _minimal_amp_thread("T-match", project.resolve().as_uri()),
        )
        _write_amp_thread(
            amp_root / "threads" / "T-other.json",
            _minimal_amp_thread("T-other", (tmp_path / "other").resolve().as_uri()),
        )

        found = list_amp_threads(project_root=project, source_root=amp_root)
        assert found == [matching]

    def test_latest_thread_path(self, tmp_path: Path) -> None:
        amp_root = tmp_path / "amp"
        project = tmp_path / "project"
        project.mkdir()

        old_data = _minimal_amp_thread("T-old", project.resolve().as_uri())
        new_data = _minimal_amp_thread("T-new", project.resolve().as_uri())
        new_data["created"] = old_data["created"] + 1000

        old = _write_amp_thread(
            amp_root / "threads" / "T-old.json",
            old_data,
        )
        new = _write_amp_thread(
            amp_root / "threads" / "T-new.json",
            new_data,
        )
        old.touch()
        new.touch()

        latest = latest_amp_thread_path(project_root=project, source_root=amp_root)
        assert latest == new


class TestParseAmpThread:
    def test_parse_thread_extracts_messages(self, tmp_path: Path) -> None:
        thread_path = _write_amp_thread(
            tmp_path / "threads" / "T-parse.json",
            _minimal_amp_thread(project_uri="file:///tmp/project"),
        )

        transcript = parse_amp_thread(thread_path)
        assert transcript.thread_id == "T-019d18bf-7b9d-7082-b525-1467aa335ca8"
        assert transcript.started == "2026-03-23T03:31:35.457000Z"
        assert transcript.agent_mode == "smart"
        assert transcript.client == "VS Code CLI Execute Mode"
        assert transcript.client_version == "0.0.1774224286-g947cba"
        assert transcript.project_roots == ["/tmp/project"]
        assert [event["role"] for event in transcript.events] == ["user", "assistant"]
        assert transcript.events[1]["content"] == "Run pytest -q from the repo root."
        assert transcript.umx_session_id == "2026-03-23-amp-T-019d18"

    def test_parse_thread_skips_non_text_and_duplicates(self, tmp_path: Path) -> None:
        data = _minimal_amp_thread(project_uri="file:///tmp/project")
        data["messages"].append(data["messages"][1])
        data["messages"].append(
            {
                "role": "assistant",
                "messageId": 2,
                "content": [{"type": "tool", "text": "skip"}],
                "meta": {"sentAt": 1774236697000},
            }
        )
        thread_path = _write_amp_thread(tmp_path / "threads" / "T-dup.json", data)

        transcript = parse_amp_thread(thread_path)
        assert len(transcript.events) == 2


class TestCaptureAmpThread:
    def test_capture_writes_session(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from umx.config import default_config, save_config
        from umx.scope import config_path, init_local_umx, init_project_memory, project_memory_dir
        from umx.sessions import read_session, session_path

        home = tmp_path / "umxhome"
        monkeypatch.setenv("UMX_HOME", str(home))
        init_local_umx()
        save_config(config_path(), default_config())

        project = tmp_path / "project"
        project.mkdir()
        (project / ".git").mkdir()
        init_project_memory(project)

        thread_path = _write_amp_thread(
            tmp_path / "amp" / "threads" / "T-capture.json",
            _minimal_amp_thread(project_uri=project.resolve().as_uri()),
        )

        result = capture_amp_thread(project, thread_path)
        repo = project_memory_dir(project)
        session_file = session_path(repo, result["umx_session_id"])

        assert session_file.exists()
        payload = read_session(session_file)
        assert payload[0]["_meta"]["tool"] == "amp"
        assert payload[0]["_meta"]["source"] == "amp-thread"
        assert payload[0]["_meta"]["amp_thread_id"] == "T-019d18bf-7b9d-7082-b525-1467aa335ca8"
        assert payload[1]["role"] == "user"
        assert payload[2]["role"] == "assistant"

    def test_cli_capture_amp_dry_run(self, project_dir: Path) -> None:
        thread_path = _write_amp_thread(
            project_dir.parent / "amp" / "threads" / "T-cli.json",
            _minimal_amp_thread(project_uri=project_dir.resolve().as_uri()),
        )

        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "capture",
                "amp",
                "--cwd",
                str(project_dir),
                "--file",
                str(thread_path),
                "--dry-run",
            ],
        )

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["tool"] == "amp"
        assert payload["events_imported"] == 2

    def test_cli_capture_amp_all_preserves_order_and_commits_once(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
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

        source_root = tmp_path / "amp"
        first = _write_amp_thread(
            source_root / "threads" / "T-001.json",
            _minimal_amp_thread(project_uri=project.resolve().as_uri(), thread_id="T-001"),
        )
        second = _write_amp_thread(
            source_root / "threads" / "T-002.json",
            _minimal_amp_thread(project_uri=project.resolve().as_uri(), thread_id="T-002"),
        )

        barrier = threading.Barrier(2, timeout=2)
        original = parse_amp_thread

        def parse_with_barrier(path: Path):
            transcript = original(path)
            barrier.wait()
            return transcript

        runner = CliRunner()
        with (
            patch("umx.amp_capture.parse_amp_thread", side_effect=parse_with_barrier),
            patch(
                "umx.git_ops.git_add_and_commit",
                return_value=GitCommitResult.committed_result(),
            ) as mock_commit,
        ):
            result = runner.invoke(
                main,
                [
                    "capture",
                    "amp",
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
