from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from tests.secret_literals import OPENAI_KEY_SHORT
from umx.bridge import END_MARKER, START_MARKER
from umx.cli import main
from umx.memory import add_fact, load_all_facts
from umx.models import (
    ConsolidationStatus,
    Fact,
    MemoryType,
    Scope,
    SourceType,
    Verification,
)
from umx.strength import independent_corroboration


def _make_fact(text: str, topic: str = "general", **overrides) -> Fact:
    values = {
        "fact_id": overrides.pop("fact_id", "01TESTFACT0000000000000700"),
        "text": text,
        "scope": Scope.PROJECT,
        "topic": topic,
        "encoding_strength": 4,
        "memory_type": MemoryType.EXPLICIT_SEMANTIC,
        "verification": Verification.CORROBORATED,
        "source_type": SourceType.GROUND_TRUTH_CODE,
        "source_tool": "codex",
        "source_session": "sess-bridge-001",
        "consolidation_status": ConsolidationStatus.STABLE,
    }
    values.update(overrides)
    return Fact(**values)


def test_bridge_sync_import_and_remove(project_dir: Path, project_repo: Path) -> None:
    base_fact = _make_fact(
        "postgres runs on 5433 in dev",
        topic="devenv",
        fact_id="01TESTFACT0000000000000701",
    )
    add_fact(project_repo, base_fact, auto_commit=False)

    runner = CliRunner()
    sync_result = runner.invoke(
        main,
        ["bridge", "sync", "--cwd", str(project_dir), "--target", "CLAUDE.md"],
    )
    assert sync_result.exit_code == 0, sync_result.output
    sync_payload = json.loads(sync_result.output)
    assert sync_payload == [str(project_dir / "CLAUDE.md")]

    bridge_text = (project_dir / "CLAUDE.md").read_text()
    assert START_MARKER in bridge_text
    assert END_MARKER in bridge_text
    assert "postgres runs on 5433 in dev" in bridge_text

    import_result = runner.invoke(
        main,
        ["bridge", "import", "--cwd", str(project_dir), "--target", "CLAUDE.md"],
    )
    assert import_result.exit_code == 0, import_result.output
    assert json.loads(import_result.output) == {"dry_run": False, "imported": 1}

    imported = [
        fact
        for fact in load_all_facts(project_repo, include_superseded=False)
        if fact.source_session.startswith("bridge:")
    ]
    assert len(imported) == 1
    bridge_fact = imported[0]
    assert bridge_fact.provenance.extracted_by == "legacy-bridge"
    assert not independent_corroboration(base_fact, bridge_fact)

    remove_result = runner.invoke(
        main,
        ["bridge", "remove", "--cwd", str(project_dir), "--target", "CLAUDE.md"],
    )
    assert remove_result.exit_code == 0, remove_result.output
    assert json.loads(remove_result.output) == [str(project_dir / "CLAUDE.md")]
    cleaned = (project_dir / "CLAUDE.md").read_text()
    assert START_MARKER not in cleaned
    assert END_MARKER not in cleaned


def test_bridge_import_redacts_secret_text(project_dir: Path, project_repo: Path) -> None:
    bridge_path = project_dir / "CLAUDE.md"
    bridge_path.write_text(
        START_MARKER
        + f"\n- API token {OPENAI_KEY_SHORT}\n"
        + END_MARKER
        + "\n"
    )

    result = CliRunner().invoke(
        main,
        ["bridge", "import", "--cwd", str(project_dir), "--target", "CLAUDE.md"],
    )

    assert result.exit_code == 0, result.output
    imported = [
        fact
        for fact in load_all_facts(project_repo, include_superseded=False)
        if fact.source_session.startswith("bridge:")
    ]
    assert len(imported) == 1
    assert "[REDACTED:openai-key]" in imported[0].text
