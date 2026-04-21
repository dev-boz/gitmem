from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from umx.cli import main
from umx.config import default_config, save_config
from umx.doctor import run_doctor
from umx.memory import add_fact
from umx.models import (
    ConsolidationStatus,
    Fact,
    MemoryType,
    Scope,
    SourceType,
    Verification,
)
from umx.scope import config_path
from umx.search import rebuild_index
from umx.search_semantic import ensure_embeddings, embeddings_available, inspect_embedding_cache_state


def _make_fact(text: str, topic: str = "general", **overrides) -> Fact:
    values = {
        "fact_id": overrides.pop("fact_id", "01TESTFACT0000000000004300"),
        "text": text,
        "scope": Scope.PROJECT,
        "topic": topic,
        "encoding_strength": 3,
        "memory_type": MemoryType.EXPLICIT_SEMANTIC,
        "verification": Verification.SELF_REPORTED,
        "source_type": SourceType.TOOL_OUTPUT,
        "source_tool": "codex",
        "source_session": "sess-embed-001",
        "consolidation_status": ConsolidationStatus.FRAGILE,
    }
    values.update(overrides)
    return Fact(**values)


def test_fixture_embedding_provider_writes_provider_signature(
    project_repo: Path,
) -> None:
    fact = _make_fact("deploy uses the green cluster", fact_id="01TESTFACT0000000000004301")
    add_fact(project_repo, fact, auto_commit=False)

    cfg = default_config()
    cfg.search.backend = "hybrid"
    cfg.search.embedding.provider = "fixture"

    assert embeddings_available(cfg) is True

    rebuild_index(project_repo, with_embeddings=True, config=cfg)

    payload = json.loads((project_repo / ".umx.json").read_text())
    assert payload["embedding_config"] == {
        "provider": "fixture",
        "model": "all-MiniLM-L6-v2",
        "model_version": "v1.0",
    }
    assert payload["facts"][fact.fact_id]["embedding_provider"] == "fixture"
    assert isinstance(payload["facts"][fact.fact_id]["embedding"], list)
    assert len(payload["facts"][fact.fact_id]["embedding"]) == 3


def test_embedding_config_change_requires_rebuild_and_blocks_partial_refresh(
    project_repo: Path,
) -> None:
    fact = _make_fact("deploy uses the green cluster", fact_id="01TESTFACT0000000000004302")
    add_fact(project_repo, fact, auto_commit=False)

    cfg = default_config()
    cfg.search.backend = "hybrid"
    cfg.search.embedding.provider = "fixture"
    rebuild_index(project_repo, with_embeddings=True, config=cfg)

    switched = default_config()
    switched.search.backend = "hybrid"
    switched.search.embedding.provider = "sentence-transformers"

    state = inspect_embedding_cache_state(project_repo, config=switched)
    assert state.needs_rebuild is True
    assert "run `umx rebuild-index --embeddings`" in (state.message or "")

    result = ensure_embeddings(project_repo, [fact], config=switched, force=False)
    assert result.updated == 0
    assert result.needs_rebuild is True
    assert "run `umx rebuild-index --embeddings`" in (result.message or "")

    payload = json.loads((project_repo / ".umx.json").read_text())
    assert payload["embedding_config"]["provider"] == "fixture"
    assert payload["facts"][fact.fact_id]["embedding_provider"] == "fixture"


def test_legacy_compatible_cache_upgrades_to_repo_signature(
    project_repo: Path,
) -> None:
    fact = _make_fact("deploy uses the green cluster", fact_id="01TESTFACT0000000000004304")
    add_fact(project_repo, fact, auto_commit=False)

    cfg = default_config()
    cfg.search.backend = "hybrid"
    cfg.search.embedding.provider = "fixture"
    rebuild_index(project_repo, with_embeddings=True, config=cfg)

    cache_path = project_repo / ".umx.json"
    payload = json.loads(cache_path.read_text())
    payload.pop("embedding_config")
    cache_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")

    state = inspect_embedding_cache_state(project_repo, config=cfg)
    assert state.state == "legacy-compatible"

    result = ensure_embeddings(project_repo, [fact], config=cfg, force=False)
    assert result.updated == 0

    upgraded = json.loads(cache_path.read_text())
    assert upgraded["embedding_config"] == {
        "provider": "fixture",
        "model": "all-MiniLM-L6-v2",
        "model_version": "v1.0",
    }
    assert inspect_embedding_cache_state(project_repo, config=cfg).state == "ready"


def test_search_and_doctor_surface_embedding_rebuild_message(
    project_dir: Path,
    project_repo: Path,
) -> None:
    fact = _make_fact(
        "deploy uses the green cluster",
        topic="deploy",
        fact_id="01TESTFACT0000000000004303",
    )
    add_fact(project_repo, fact, auto_commit=False)

    initial = default_config()
    initial.search.backend = "hybrid"
    initial.search.embedding.provider = "fixture"
    save_config(config_path(), initial)
    rebuild_index(project_repo, with_embeddings=True, config=initial)

    switched = default_config()
    switched.search.backend = "hybrid"
    switched.search.embedding.provider = "sentence-transformers"
    save_config(config_path(), switched)

    runner = CliRunner()
    result = runner.invoke(main, ["search", "--cwd", str(project_dir), "deploy"])
    combined = result.output
    assert result.exit_code == 0, result.output
    assert "run `umx rebuild-index --embeddings`" in combined
    assert "deploy uses the green cluster" in combined

    payload = run_doctor(project_dir)
    assert payload["embeddings"]["provider"] == "sentence-transformers"
    assert payload["embeddings"]["state"] == "needs-rebuild"
    assert "run `umx rebuild-index --embeddings`" in (payload["embeddings"]["message"] or "")
