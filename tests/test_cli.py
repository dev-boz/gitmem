from __future__ import annotations

import json
from unittest.mock import Mock, patch

from click.testing import CliRunner

from umx.config import default_config, save_config
from umx.cli import main
from umx.scope import config_path


def test_cli_init_and_status(project_dir, umx_home) -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["init", "--org", "memory-org"])
    assert result.exit_code == 0

    result = runner.invoke(main, ["init-project", "--cwd", str(project_dir), "--slug", "demo"])
    assert result.exit_code == 0

    status = runner.invoke(main, ["status", "--cwd", str(project_dir)])
    assert status.exit_code == 0
    payload = json.loads(status.output)
    assert payload["slug"] == "demo"


def test_cli_inject_outputs_memory_block(project_dir, project_repo, user_repo) -> None:
    from umx.memory import add_fact
    from umx.models import (
        ConsolidationStatus,
        Fact,
        MemoryType,
        Scope,
        SourceType,
        Verification,
    )

    add_fact(
        project_repo,
        Fact(
            fact_id="01TESTFACT0000000000000200",
            text="postgres runs on 5433 in dev",
            scope=Scope.PROJECT,
            topic="devenv",
            encoding_strength=4,
            memory_type=MemoryType.EXPLICIT_SEMANTIC,
            verification=Verification.CORROBORATED,
            source_type=SourceType.GROUND_TRUTH_CODE,
            source_tool="codex",
            source_session="2026-04-11",
            consolidation_status=ConsolidationStatus.STABLE,
        ),
    )

    runner = CliRunner()
    result = runner.invoke(main, ["inject", "--cwd", str(project_dir), "--prompt", "postgres"])
    assert result.exit_code == 0
    assert "# UMX Memory" in result.output
    assert "postgres runs on 5433 in dev" in result.output


def test_cli_view_fact_and_health(project_dir, project_repo) -> None:
    from umx.memory import add_fact
    from umx.models import (
        ConsolidationStatus,
        Fact,
        MemoryType,
        Scope,
        SourceType,
        Verification,
    )

    fact = Fact(
        fact_id="01TESTFACT0000000000000201",
        text="deploys run through staging first",
        scope=Scope.PROJECT,
        topic="release",
        encoding_strength=4,
        memory_type=MemoryType.EXPLICIT_SEMANTIC,
        verification=Verification.CORROBORATED,
        source_type=SourceType.GROUND_TRUTH_CODE,
        source_tool="codex",
        source_session="2026-04-11",
        consolidation_status=ConsolidationStatus.STABLE,
    )
    add_fact(project_repo, fact)

    runner = CliRunner()
    view_result = runner.invoke(main, ["view", "--cwd", str(project_dir), "--fact", fact.fact_id])
    assert view_result.exit_code == 0
    payload = json.loads(view_result.output)
    assert payload["fact_id"] == fact.fact_id

    health_result = runner.invoke(main, ["health", "--cwd", str(project_dir)])
    assert health_result.exit_code == 0
    health = json.loads(health_result.output)
    assert "metrics" in health
    assert "flags" in health


def test_cli_view_launches_viewer_by_default(project_dir, project_repo) -> None:
    runner = CliRunner()
    server = Mock()
    server.serve_forever.side_effect = KeyboardInterrupt
    with patch("umx.cli.start_viewer", return_value=("http://127.0.0.1:43123", server)) as mock_run:
        result = runner.invoke(main, ["view", "--cwd", str(project_dir)])

    assert result.exit_code == 0
    assert result.output.strip() == "http://127.0.0.1:43123"
    mock_run.assert_called_once_with(project_dir)
    server.serve_forever.assert_called_once_with()


def test_cli_status_remains_json_when_hot_tier_warns(project_dir, project_repo, umx_home) -> None:
    cfg = default_config()
    cfg.memory.hot_tier_max_tokens = 1
    save_config(config_path(), cfg)
    (project_repo / "MEMORY.md").write_text("# Memory\n\n" + ("hot token\n" * 40))

    runner = CliRunner()
    result = runner.invoke(main, ["status", "--cwd", str(project_dir)])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["metrics"]["hot_tier_utilisation"]["status"] == "warn"
