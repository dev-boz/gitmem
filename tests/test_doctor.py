from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

from umx.config import default_config, save_config
from umx.doctor import run_doctor
from umx.scope import config_path


def test_doctor_reports_and_clears_stale_dream_lock(project_dir: Path, project_repo: Path) -> None:
    lock_path = project_repo / "meta" / "dream.lock"
    lock_path.write_text(
        json.dumps(
            {
                "pid": 1234,
                "hostname": "test-host",
                "started": "2026-04-16T00:00:00Z",
                "heartbeat": (datetime.now(tz=UTC) - timedelta(minutes=31)).isoformat().replace("+00:00", "Z"),
            },
            sort_keys=True,
        )
        + "\n"
    )

    payload = run_doctor(project_dir)
    assert payload["dream_lock"]["present"] is True
    assert payload["dream_lock"]["stale"] is True

    fixed = run_doctor(project_dir, fix=True)
    assert fixed["dream_lock"]["present"] is False
    assert fixed["dream_lock"]["stale"] is False
    assert "cleared stale dream lock" in fixed["fixes_applied"]
    assert not lock_path.exists()


def test_doctor_reports_and_clears_corrupt_dream_lock(project_dir: Path, project_repo: Path) -> None:
    lock_path = project_repo / "meta" / "dream.lock"
    lock_path.write_text("{not json}\n")

    payload = run_doctor(project_dir)
    assert payload["dream_lock"]["present"] is True
    assert payload["dream_lock"]["stale"] is True

    fixed = run_doctor(project_dir, fix=True)
    assert fixed["dream_lock"]["present"] is False
    assert fixed["dream_lock"]["stale"] is False
    assert "cleared corrupt dream lock" in fixed["fixes_applied"]
    assert not lock_path.exists()


def test_doctor_surfaces_processing_quarantine_health_and_embeddings(
    project_dir: Path,
    project_repo: Path,
) -> None:
    cfg = default_config()
    cfg.memory.hot_tier_max_tokens = 1
    save_config(config_path(), cfg)
    (project_repo / "local" / "quarantine").mkdir(parents=True, exist_ok=True)
    (project_repo / "local" / "quarantine" / "sess-1.jsonl").write_text('{"_meta":{"session_id":"sess-1"}}\n')
    (project_repo / "MEMORY.md").write_text("# Memory\n\n" + ("hot token\n" * 40))
    (project_repo / "meta" / "processing.jsonl").write_text(
        json.dumps(
            {
                "run_id": "dream-run-1",
                "event": "started",
                "status": "running",
                "mode": "local",
                "branch": "main",
                "ts": datetime.now(tz=UTC).isoformat().replace("+00:00", "Z"),
            },
            sort_keys=True,
        )
        + "\n"
    )

    payload = run_doctor(project_dir)

    assert payload["processing"]["active_runs"] == 1
    assert payload["quarantine"] == {
        "count": 1,
        "files": ["local/quarantine/sess-1.jsonl"],
    }
    assert payload["embeddings"]["backend"] == "fts5"
    assert payload["embeddings"]["provider"] == "sentence-transformers"
    assert payload["embeddings"]["enabled"] is False
    assert isinstance(payload["embeddings"]["available"], bool)
    assert payload["embeddings"]["state"] == "empty"
    assert payload["embeddings"]["message"] is None
    assert "health" in payload
    assert "hot_tier_pct" in payload["health"]
    assert any(item["metric"] == "hot_tier_utilisation" for item in payload["health"]["guidance"])
    assert payload["advice"] == payload["health"]["guidance"]


def test_doctor_surfaces_git_signing_readiness_issues(
    project_dir: Path,
    project_repo: Path,
    tmp_path: Path,
    monkeypatch,
) -> None:
    empty_home = tmp_path / "empty-home"
    empty_home.mkdir()
    monkeypatch.setenv("HOME", str(empty_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(empty_home / ".config"))

    cfg = default_config()
    cfg.git.require_signed_commits = True
    save_config(config_path(), cfg)

    subprocess.run(
        ["git", "-C", str(project_repo), "config", "gpg.format", "ssh"],
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(project_repo), "config", "gpg.ssh.program", "missing-ssh-signer"],
        capture_output=True,
        check=True,
    )

    payload = run_doctor(project_dir)

    assert payload["git_signing"]["require_signed_commits"] is True
    readiness = payload["git_signing_readiness"]
    assert readiness["format"] == "ssh"
    assert readiness["ready"] is False
    assert any("user.signingkey" in issue for issue in readiness["issues"])
    assert any("missing-ssh-signer" in issue for issue in readiness["issues"])


def test_doctor_surfaces_identity_issues_when_signing_is_enabled(
    project_dir: Path,
    project_repo: Path,
    tmp_path: Path,
    monkeypatch,
) -> None:
    empty_home = tmp_path / "empty-home-sign"
    empty_home.mkdir()
    monkeypatch.setenv("HOME", str(empty_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(empty_home / ".config"))

    cfg = default_config()
    cfg.git.sign_commits = True
    save_config(config_path(), cfg)

    payload = run_doctor(project_dir)

    readiness = payload["git_signing_readiness"]
    assert readiness["ready"] is False
    assert any("user.name" in issue for issue in readiness["issues"])
    assert any("user.email" in issue for issue in readiness["issues"])
