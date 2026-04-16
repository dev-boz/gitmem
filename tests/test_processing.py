from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from umx.dream.processing import (
    active_processing_runs,
    append_processing_event,
    complete_processing_run,
    read_processing_log,
    start_processing_run,
    summarize_processing_log,
)


def test_processing_log_tracks_run_lifecycle(project_repo: Path) -> None:
    run_id = start_processing_run(project_repo, mode="remote", force=True, branch="main")
    complete_processing_run(
        project_repo,
        run_id,
        mode="remote",
        branch="main",
        added=2,
        pruned=1,
        message="2 facts retained",
        pr_branch="dream/l1/test-run",
        pr_number=7,
    )

    rows = read_processing_log(project_repo)

    assert [row["event"] for row in rows] == ["started", "completed"]
    summary = summarize_processing_log(project_repo)
    assert summary["active_runs"] == 0
    assert summary["last_completed"]["run_id"] == run_id
    assert summary["last_completed"]["pr_number"] == 7


def test_processing_log_active_detection_ignores_stale_runs(project_repo: Path) -> None:
    stale_ts = (datetime.now(tz=UTC) - timedelta(minutes=31)).isoformat().replace("+00:00", "Z")
    active_ts = datetime.now(tz=UTC).isoformat().replace("+00:00", "Z")
    append_processing_event(
        project_repo,
        {
            "run_id": "dream-stale",
            "event": "started",
            "status": "running",
            "mode": "remote",
            "branch": "main",
            "ts": stale_ts,
        },
    )
    append_processing_event(
        project_repo,
        {
            "run_id": "dream-active",
            "event": "started",
            "status": "running",
            "mode": "remote",
            "branch": "main",
            "ts": active_ts,
        },
    )

    active = active_processing_runs(project_repo)

    assert [row["run_id"] for row in active] == ["dream-active"]
