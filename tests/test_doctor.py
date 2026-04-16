from __future__ import annotations

import json
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
    assert payload["embeddings"]["enabled"] is False
    assert isinstance(payload["embeddings"]["available"], bool)
    assert "health" in payload
    assert "hot_tier_pct" in payload["health"]
    assert any(item["metric"] == "hot_tier_utilisation" for item in payload["health"]["guidance"])
    assert payload["advice"] == payload["health"]["guidance"]
