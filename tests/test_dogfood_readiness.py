from __future__ import annotations

import json
from pathlib import Path

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
