from __future__ import annotations

import json
from pathlib import Path
from urllib.error import URLError

from click.testing import CliRunner

from umx.cli import main
from umx.config import default_config, save_config
from umx.scope import config_path
from umx.telemetry import telemetry_queue_path, telemetry_state_path


class _FakeResponse:
    def __init__(self, body: str = "", *, headers: dict[str, str] | None = None) -> None:
        self._body = body.encode("utf-8")
        self.headers = headers or {}

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


def test_status_makes_no_network_calls_when_telemetry_disabled(
    project_dir: Path,
    project_repo: Path,
    monkeypatch,
) -> None:
    called = {"count": 0}

    def _unexpected(*args, **kwargs):
        called["count"] += 1
        raise AssertionError("network should not be used when telemetry is disabled")

    monkeypatch.setattr("umx.telemetry.urlopen", _unexpected)

    runner = CliRunner()
    result = runner.invoke(main, ["status", "--cwd", str(project_dir)])

    assert result.exit_code == 0, result.output
    assert called["count"] == 0
    assert not telemetry_queue_path().exists()


def test_status_uploads_anonymous_telemetry_when_enabled(
    project_dir: Path,
    project_repo: Path,
    monkeypatch,
) -> None:
    cfg = default_config()
    cfg.telemetry.enabled = True
    save_config(config_path(), cfg)

    captured: dict[str, object] = {}

    def _fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return _FakeResponse('{"accepted": true}')

    monkeypatch.setattr("umx.telemetry.urlopen", _fake_urlopen)

    runner = CliRunner()
    result = runner.invoke(main, ["status", "--cwd", str(project_dir)])

    assert result.exit_code == 0, result.output
    payload = captured["body"]
    assert isinstance(payload, dict)
    assert payload["schema_version"] == 1
    assert payload["client"]["name"] == "gitmem"
    events = payload["events"]
    assert isinstance(events, list) and len(events) == 1
    event = events[0]
    assert event["command"] == "status"
    assert event["success"] is True
    assert "cwd" not in event
    assert "repo" in event
    repo = event["repo"]
    assert repo["present"] is True
    assert "fact_count_bucket" in repo
    assert "pending_session_count_bucket" in repo
    assert project_repo.name not in json.dumps(payload)
    assert str(project_dir) not in json.dumps(payload)


def test_telemetry_kill_switch_disables_future_uploads(
    project_dir: Path,
    project_repo: Path,
    monkeypatch,
) -> None:
    cfg = default_config()
    cfg.telemetry.enabled = True
    save_config(config_path(), cfg)

    called = {"count": 0}

    def _kill_switch(request, timeout):
        called["count"] += 1
        return _FakeResponse(headers={"X-UMX-Telemetry-Disabled": "true", "X-UMX-Telemetry-Reason": "maintenance"})

    monkeypatch.setattr("umx.telemetry.urlopen", _kill_switch)

    runner = CliRunner()
    first = runner.invoke(main, ["status", "--cwd", str(project_dir)])
    assert first.exit_code == 0, first.output
    assert called["count"] == 1

    state = json.loads(telemetry_state_path().read_text())
    assert state["disabled_by_server"] is True
    assert state["disabled_reason"] == "maintenance"
    assert not telemetry_queue_path().exists()

    def _unexpected(*args, **kwargs):
        raise AssertionError("kill-switched telemetry should not upload again")

    monkeypatch.setattr("umx.telemetry.urlopen", _unexpected)
    second = runner.invoke(main, ["status", "--cwd", str(project_dir)])
    assert second.exit_code == 0, second.output


def test_telemetry_transport_failure_does_not_break_command(
    project_dir: Path,
    project_repo: Path,
    monkeypatch,
) -> None:
    cfg = default_config()
    cfg.telemetry.enabled = True
    save_config(config_path(), cfg)

    monkeypatch.setattr("umx.telemetry.urlopen", lambda *args, **kwargs: (_ for _ in ()).throw(URLError("offline")))

    runner = CliRunner()
    result = runner.invoke(main, ["status", "--cwd", str(project_dir)])

    assert result.exit_code == 0, result.output
    state = json.loads(telemetry_state_path().read_text())
    assert state["last_error"] == "offline"
    queued = telemetry_queue_path().read_text().splitlines()
    assert len(queued) == 1


def test_telemetry_disable_env_blocks_upload_even_when_enabled(
    project_dir: Path,
    project_repo: Path,
    monkeypatch,
) -> None:
    cfg = default_config()
    cfg.telemetry.enabled = True
    save_config(config_path(), cfg)

    called = {"count": 0}

    def _unexpected(*args, **kwargs):
        called["count"] += 1
        raise AssertionError("UMX_TELEMETRY_DISABLE should block uploads")

    monkeypatch.setenv("UMX_TELEMETRY_DISABLE", "1")
    monkeypatch.setattr("umx.telemetry.urlopen", _unexpected)

    runner = CliRunner()
    result = runner.invoke(main, ["status", "--cwd", str(project_dir)])

    assert result.exit_code == 0, result.output
    assert called["count"] == 0
    assert not telemetry_queue_path().exists()


def test_telemetry_local_write_failure_does_not_break_command(
    project_dir: Path,
    project_repo: Path,
    monkeypatch,
) -> None:
    cfg = default_config()
    cfg.telemetry.enabled = True
    save_config(config_path(), cfg)

    monkeypatch.setattr("umx.telemetry._save_queue", lambda events: (_ for _ in ()).throw(OSError("disk full")))

    runner = CliRunner()
    result = runner.invoke(main, ["status", "--cwd", str(project_dir)])

    assert result.exit_code == 0, result.output


def test_invalid_telemetry_config_values_fail_open(
    project_dir: Path,
    project_repo: Path,
) -> None:
    cfg = default_config()
    cfg.telemetry.enabled = True
    cfg.telemetry.timeout_seconds = "oops"  # type: ignore[assignment]
    cfg.telemetry.batch_size = "nope"  # type: ignore[assignment]
    save_config(config_path(), cfg)

    runner = CliRunner()
    result = runner.invoke(main, ["status", "--cwd", str(project_dir)])

    assert result.exit_code == 0, result.output
