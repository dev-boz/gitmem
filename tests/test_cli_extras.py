from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from umx.aip import main as aip_main, mem_main
from umx.cli import main
from umx.memory import add_fact
from umx.models import (
    ConsolidationStatus,
    Fact,
    MemoryType,
    Scope,
    SourceType,
    Verification,
)
from umx.sessions import session_path, write_session


def _make_fact(text: str, topic: str = "general", **overrides) -> Fact:
    values = {
        "fact_id": overrides.pop("fact_id", "01TESTFACT0000000000000800"),
        "text": text,
        "scope": Scope.PROJECT,
        "topic": topic,
        "encoding_strength": 3,
        "memory_type": MemoryType.EXPLICIT_SEMANTIC,
        "verification": Verification.SELF_REPORTED,
        "source_type": SourceType.TOOL_OUTPUT,
        "source_tool": "codex",
        "source_session": "sess-cli-001",
        "consolidation_status": ConsolidationStatus.FRAGILE,
    }
    values.update(overrides)
    return Fact(**values)


def test_cli_init_actions_writes_templates(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["init-actions", "--dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert len(payload) == 2
    assert (tmp_path / ".github" / "workflows" / "l1-dream.yml").exists()
    assert (tmp_path / ".github" / "workflows" / "l2-review.yml").exists()


def test_cli_archive_sessions_moves_old_sessions(project_dir: Path, project_repo: Path) -> None:
    write_session(
        project_repo,
        {
            "session_id": "2020-01-15-archivecli",
            "started": "2020-01-15T00:00:00Z",
            "tool": "codex",
        },
        [{"ts": "2020-01-15T00:00:01Z", "role": "assistant", "content": "postgres runs on 5433 in dev"}],
        auto_commit=False,
    )

    runner = CliRunner()
    result = runner.invoke(main, ["archive-sessions", "--cwd", str(project_dir)])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["archived_sessions"] == 1
    assert not session_path(project_repo, "2020-01-15-archivecli").exists()
    assert (project_repo / "sessions" / "2020" / "01" / "2020-01-archive.jsonl.gz").exists()


def test_cli_rebuild_index_with_embeddings_writes_cache(
    monkeypatch, project_dir: Path, project_repo: Path
) -> None:
    fact = _make_fact(
        "deploy to staging via make ship",
        topic="deploy",
        fact_id="01TESTFACT0000000000000801",
    )
    add_fact(project_repo, fact, auto_commit=False)

    monkeypatch.setattr("umx.search_semantic.embed_fact", lambda fact, config=None: [1.0, 0.0, 0.0])

    runner = CliRunner()
    result = runner.invoke(main, ["rebuild-index", "--cwd", str(project_dir), "--embeddings"])
    assert result.exit_code == 0, result.output

    cache_path = project_repo / ".umx.json"
    payload = json.loads(cache_path.read_text())
    assert fact.fact_id in payload["facts"]
    assert payload["facts"][fact.fact_id]["embedding"] == [1.0, 0.0, 0.0]


def test_aip_mem_namespace_forwards_status(project_dir: Path, project_repo: Path) -> None:
    runner = CliRunner()

    result = runner.invoke(aip_main, ["mem", "status", "--cwd", str(project_dir)])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert "facts" in payload
    assert "session_count" in payload


def test_aip_mem_entrypoint_forwards_view(project_dir: Path, project_repo: Path) -> None:
    fact = _make_fact(
        "document staging rollout steps",
        topic="deploy",
        fact_id="01TESTFACT0000000000000802",
    )
    add_fact(project_repo, fact, auto_commit=False)

    runner = CliRunner()
    result = runner.invoke(mem_main, ["view", "--cwd", str(project_dir), "--fact", fact.fact_id])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["fact_id"] == fact.fact_id
