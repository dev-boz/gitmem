from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from click.testing import CliRunner

from umx.artifacts import parse_reasoning_artifact
from umx.cli import main
from umx.continuity import read_diary, read_handover
from umx.dream.extract import handover_records_to_facts
from umx.dream.pipeline import DreamPipeline
from umx.inject import build_injection_block
from umx.models import ConsolidationStatus, Fact, MemoryType, Scope, SourceType, Verification
from umx.search import query_reasoning_artifacts, rebuild_index


def _fact(fact_id: str, text: str, *, topic: str = "general") -> Fact:
    return Fact(
        fact_id=fact_id,
        text=text,
        scope=Scope.PROJECT,
        topic=topic,
        encoding_strength=3,
        memory_type=MemoryType.EXPLICIT_SEMANTIC,
        verification=Verification.CORROBORATED,
        source_type=SourceType.TOOL_OUTPUT,
        source_tool="test",
        source_session="test-session",
        consolidation_status=ConsolidationStatus.STABLE,
    )


def test_diary_and_handover_cli_write_local_continuity(project_dir: Path, project_repo: Path) -> None:
    runner = CliRunner()

    diary_result = runner.invoke(
        main,
        [
            "diary",
            "append",
            "--cwd",
            str(project_dir),
            "Token endpoint requires X-Request-ID header.",
        ],
    )
    handover_result = runner.invoke(
        main,
        [
            "handover",
            "write",
            "--cwd",
            str(project_dir),
            "Token endpoint requires X-Request-ID header.",
        ],
    )

    assert diary_result.exit_code == 0, diary_result.output
    assert handover_result.exit_code == 0, handover_result.output
    payload = json.loads(handover_result.output)
    assert payload["latest"].endswith("local/handover.md")
    assert "Token endpoint requires X-Request-ID header" in read_diary(project_repo)
    assert "Token endpoint requires X-Request-ID header" in read_handover(project_repo)


def test_handover_ingest_produces_tool_output_facts(project_dir: Path, project_repo: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "handover",
            "write",
            "--cwd",
            str(project_dir),
            "Refresh tokens expire after 30 days.",
        ],
    )

    assert result.exit_code == 0, result.output
    facts = handover_records_to_facts(project_repo)

    assert any(
        fact.source_tool == "handover"
        and fact.source_type == SourceType.TOOL_OUTPUT
        and fact.encoding_strength == 3
        and "Refresh tokens expire after 30 days" in fact.text
        for fact in facts
    )


def test_reasoning_artifact_index_injection_and_orient_invalidation(
    project_dir: Path,
    project_repo: Path,
) -> None:
    artifact_path = project_repo / "memory" / "artifacts" / "pgbouncer.md"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(
        """---
artifact_id: ART_PGBOUNCER
kind: reasoning_artifact
conclusion: Use PgBouncer for connection pooling
evidence:
  - Load tests showed lower latency under 50 concurrent requests
confidence: 0.92
invalidates_when:
  - auth.py changes the connection model
created_at: 2026-04-08T12:00:00Z
---

## Reasoning
PgBouncer keeps database connection counts bounded.
""",
        encoding="utf-8",
    )

    rebuild_index(project_repo)
    rows = query_reasoning_artifacts(project_repo, "PgBouncer connection pooling")
    block = build_injection_block(
        project_dir,
        prompt="Should we use PgBouncer connection pooling?",
        max_tokens=1200,
    )

    assert rows and rows[0]["id"] == "ART_PGBOUNCER"
    assert "## Reasoning Artifacts" in block
    assert "Use PgBouncer for connection pooling" in block

    (project_dir / "auth.py").write_text("CONNECTION_MODEL = 'async'\n", encoding="utf-8")
    oriented = DreamPipeline(project_dir).orient()
    artifact = parse_reasoning_artifact(artifact_path)

    assert oriented == []
    assert artifact is not None
    assert artifact.status == "invalidated"
    assert artifact.invalidation_reason == "auth.py changes the connection model"


def test_dream_prune_generates_context_layers_and_injection_uses_digest(
    project_dir: Path,
    project_repo: Path,
) -> None:
    now = datetime(2026, 5, 21, 12, 0, tzinfo=UTC)
    fact = _fact(
        "FACT_CHRONICLE_1",
        "The staging API uses port 8443 after the May rollout.",
        topic="staging",
    )

    final_facts, pruned = DreamPipeline(project_dir).prune([fact], now)
    block = build_injection_block(
        project_dir,
        prompt="Debug the staging API port after the rollout",
        max_tokens=1600,
    )

    layer_dir = project_repo / "context" / "layers" / "general-2026-05-21"
    assert final_facts
    assert pruned == 0
    assert (layer_dir / "digest.md").exists()
    assert (layer_dir / "numeric.md").exists()
    assert "The staging API uses port 8443" in (layer_dir / "digest.md").read_text()
    assert "## Context Layers" in block
    assert "context_layer: digest" in block


def test_dream_gather_includes_handover_facts(project_dir: Path, project_repo: Path) -> None:
    from umx.continuity import write_handover

    write_handover(project_repo, "OAuth refresh requires clock skew handling.")

    candidates = DreamPipeline(project_dir).gather()

    assert any(
        fact.source_tool == "handover"
        and fact.source_type == SourceType.TOOL_OUTPUT
        and "OAuth refresh requires clock skew handling" in fact.text
        for fact in candidates
    )
