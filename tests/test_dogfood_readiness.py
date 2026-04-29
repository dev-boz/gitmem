from __future__ import annotations

import json
from pathlib import Path
import subprocess
from unittest.mock import patch

from click.testing import CliRunner

from umx.cli import main
from umx.hooks.assistant_output import run as assistant_output_run
from umx.hooks.session_end import run as session_end_run
from umx.hooks.session_start import run as session_start_run
from umx.memory import add_fact
from umx.models import (
    ConsolidationStatus,
    Fact,
    MemoryType,
    Provenance,
    Scope,
    SourceType,
    Verification,
)
from umx.search import usage_snapshot
from umx.sessions import read_session, session_path


def _init_bare_remote(tmp_path: Path, name: str) -> Path:
    remote = tmp_path / f"{name}.git"
    subprocess.run(["git", "init", "--bare", str(remote)], capture_output=True, check=True)
    subprocess.run(
        ["git", "--git-dir", str(remote), "symbolic-ref", "HEAD", "refs/heads/main"],
        capture_output=True,
        check=True,
    )
    return remote


def _bootstrap_hybrid_home(
    runner: CliRunner,
    monkeypatch,
    home: Path,
    project_dir: Path,
    *,
    user_remote: Path,
    project_remote: Path,
) -> None:
    monkeypatch.setenv("UMX_HOME", str(home))
    with patch("umx.github_ops.gh_available", return_value=True), patch(
        "umx.github_ops.ensure_repo",
        return_value=str(user_remote),
    ), patch("umx.cli.assert_push_safe"), patch("umx.git_ops.assert_signed_commit_range"):
        init = runner.invoke(main, ["init", "--org", "memory-org", "--mode", "hybrid"])
    assert init.exit_code == 0, init.output

    with patch("umx.github_ops.gh_available", return_value=True), patch(
        "umx.github_ops.ensure_repo",
        return_value=str(project_remote),
    ), patch("umx.cli.assert_push_safe"), patch("umx.git_ops.assert_signed_commit_range"):
        init_project = runner.invoke(
            main,
            ["init-project", "--cwd", str(project_dir), "--slug", "dogfood-sync"],
        )
    assert init_project.exit_code == 0, init_project.output


def _codex_rollout_records(
    *,
    codex_session_id: str,
    user_text: str,
    assistant_text: str,
) -> list[dict[str, object]]:
    return [
        {
            "timestamp": "2026-04-11T13:22:22.564Z",
            "type": "session_meta",
            "payload": {
                "id": codex_session_id,
                "timestamp": "2026-04-11T13:22:22.564Z",
                "cwd": "/home/dinkum",
                "cli_version": "0.120.0",
            },
        },
        {
            "timestamp": "2026-04-11T13:25:11.599Z",
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": user_text}],
            },
        },
        {
            "timestamp": "2026-04-11T13:28:59.571Z",
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": assistant_text}],
            },
        },
    ]


def _write_codex_rollout(path: Path, records: list[dict[str, object]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(record, sort_keys=True) for record in records) + "\n")
    return path


def _make_fact(
    fact_id: str,
    text: str,
    *,
    topic: str,
    source_session: str,
) -> Fact:
    return Fact(
        fact_id=fact_id,
        text=text,
        scope=Scope.PROJECT,
        topic=topic,
        encoding_strength=4,
        memory_type=MemoryType.EXPLICIT_SEMANTIC,
        verification=Verification.CORROBORATED,
        source_type=SourceType.GROUND_TRUTH_CODE,
        source_tool="codex",
        source_session=source_session,
        consolidation_status=ConsolidationStatus.STABLE,
        provenance=Provenance(extracted_by="test", sessions=[source_session]),
    )


def test_local_dogfood_session_lifecycle_smoke(project_dir: Path, project_repo: Path) -> None:
    remembered = _make_fact(
        "01TESTFACT0000000000000901",
        "Deploys run through staging first",
        topic="release",
        source_session="sess-dogfood-seed",
    )
    add_fact(project_repo, remembered, auto_commit=False)

    session_id = "2026-04-11-dogfood001"
    start_block = session_start_run(cwd=project_dir, tool="codex", session_id=session_id)
    assert start_block is not None
    assert "UMX Memory" in start_block

    runner = CliRunner()
    inject = runner.invoke(
        main,
        [
            "inject",
            "--cwd",
            str(project_dir),
            "--session",
            session_id,
            "--prompt",
            "staging deploy checklist",
        ],
    )
    assert inject.exit_code == 0, inject.output
    assert "Deploys run through staging first" in inject.output

    snapshot = assistant_output_run(
        cwd=project_dir,
        session_id=session_id,
        tool="codex",
        event={
            "ts": "2026-04-11T00:00:01Z",
            "role": "assistant",
            "content": "Deploys run through staging first. The backup worker runs every hour.",
        },
    )
    assert snapshot is not None

    usage = usage_snapshot(project_repo)
    assert usage[remembered.fact_id]["cited_count"] >= 1

    end = session_end_run(
        cwd=project_dir,
        session_id=session_id,
        tool="codex",
        events=[
            {
                "ts": "2026-04-11T00:00:00Z",
                "role": "user",
                "content": "What should I remember about deploys and the backup worker?",
            },
            {
                "ts": "2026-04-11T00:00:01Z",
                "role": "assistant",
                "content": "Deploys run through staging first. The backup worker runs every hour.",
            },
        ],
    )
    assert end["session_written"] is True

    payload = read_session(session_path(project_repo, session_id))
    assert payload[0]["_meta"]["session_id"] == session_id
    assert payload[1]["role"] == "user"
    assert payload[2]["role"] == "assistant"

    dream = runner.invoke(main, ["dream", "--cwd", str(project_dir), "--force"])
    assert dream.exit_code == 0, dream.output
    dream_payload = json.loads(dream.output)
    assert dream_payload["status"] == "ok"

    raw = runner.invoke(main, ["search", "--cwd", str(project_dir), "--raw", "backup worker"])
    assert raw.exit_code == 0, raw.output
    assert session_id in raw.output

    indexed = runner.invoke(main, ["search", "--cwd", str(project_dir), "backup worker"])
    assert indexed.exit_code == 0, indexed.output
    assert "The backup worker runs every hour" in indexed.output

    listed = runner.invoke(main, ["view", "--cwd", str(project_dir), "--list"])
    assert listed.exit_code == 0, listed.output
    assert "The backup worker runs every hour" in listed.output


def test_hybrid_dogfood_readiness_covers_fresh_home_reattach_and_sync(
    tmp_path: Path,
    monkeypatch,
) -> None:
    user_remote = _init_bare_remote(tmp_path, "dogfood-user")
    project_remote = _init_bare_remote(tmp_path, "dogfood-project")
    project_a = tmp_path / "project-a"
    project_b = tmp_path / "project-b"
    for project_dir in (project_a, project_b):
        project_dir.mkdir()
        (project_dir / ".git").mkdir()

    home_a = tmp_path / "home-a"
    home_b = tmp_path / "home-b"
    runner = CliRunner()

    _bootstrap_hybrid_home(
        runner,
        monkeypatch,
        home_a,
        project_a,
        user_remote=user_remote,
        project_remote=project_remote,
    )

    rollout_a = _write_codex_rollout(
        tmp_path / "rollouts" / "machine-a-first.jsonl",
        _codex_rollout_records(
            codex_session_id="019d7cb5-machine-a-first",
            user_text="Where should release artifacts live?",
            assistant_text="Release artifacts live under artifacts/release-gates.",
        ),
    )
    capture_a = runner.invoke(
        main,
        ["capture", "codex", "--cwd", str(project_a), "--file", str(rollout_a)],
    )
    assert capture_a.exit_code == 0, capture_a.output
    sync_a = runner.invoke(main, ["sync", "--cwd", str(project_a)])
    assert sync_a.exit_code == 0, sync_a.output

    _bootstrap_hybrid_home(
        runner,
        monkeypatch,
        home_b,
        project_b,
        user_remote=user_remote,
        project_remote=project_remote,
    )

    raw_reattach = runner.invoke(
        main,
        ["search", "--cwd", str(project_b), "--raw", "artifacts/release-gates"],
    )
    assert raw_reattach.exit_code == 0, raw_reattach.output
    assert "release artifacts live under artifacts/release-gates" in raw_reattach.output.lower()

    status = runner.invoke(main, ["status", "--cwd", str(project_b)])
    assert status.exit_code == 0, status.output
    status_payload = json.loads(status.output)
    assert status_payload["session_count"] >= 1

    monkeypatch.setenv("UMX_HOME", str(home_a))
    rollout_a_again = _write_codex_rollout(
        tmp_path / "rollouts" / "machine-a-second.jsonl",
        _codex_rollout_records(
            codex_session_id="019d7cb5-machine-a-second",
            user_text="Did the fresh UMX_HOME reattach work?",
            assistant_text="Fresh UMX_HOME reattach succeeded before the rc sync.",
        ),
    )
    capture_a_again = runner.invoke(
        main,
        ["capture", "codex", "--cwd", str(project_a), "--file", str(rollout_a_again)],
    )
    assert capture_a_again.exit_code == 0, capture_a_again.output
    sync_a_again = runner.invoke(main, ["sync", "--cwd", str(project_a)])
    assert sync_a_again.exit_code == 0, sync_a_again.output

    monkeypatch.setenv("UMX_HOME", str(home_b))
    raw_before_sync = runner.invoke(
        main,
        ["search", "--cwd", str(project_b), "--raw", "reattach succeeded before the rc sync"],
    )
    assert raw_before_sync.exit_code == 0, raw_before_sync.output
    assert "fresh umx_home reattach succeeded before the rc sync" not in raw_before_sync.output.lower()

    sync_b = runner.invoke(main, ["sync", "--cwd", str(project_b)])
    assert sync_b.exit_code == 0, sync_b.output

    raw_after_sync = runner.invoke(
        main,
        ["search", "--cwd", str(project_b), "--raw", "reattach succeeded before the rc sync"],
    )
    assert raw_after_sync.exit_code == 0, raw_after_sync.output
    assert "fresh umx_home reattach succeeded before the rc sync" in raw_after_sync.output.lower()

    health = runner.invoke(main, ["health", "--cwd", str(project_b)])
    assert health.exit_code == 0, health.output
