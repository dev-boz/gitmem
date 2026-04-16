"""Tests for security fixes: path traversal, secret scope, gitignore gaps."""
from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from umx.cli import main
from umx.git_ops import git_init
from umx.models import (
    ConsolidationStatus,
    Fact,
    MemoryType,
    Scope,
    SourceType,
    Verification,
)


def test_secret_key_rejects_path_traversal(umx_home: Path) -> None:
    runner = CliRunner()
    for bad_key in ["../../../etc/passwd", "foo/bar", "..secret", ".hidden"]:
        result = runner.invoke(main, ["secret", "set", bad_key, "val"])
        assert result.exit_code != 0, f"Expected rejection for key: {bad_key}"
        assert "Invalid secret key" in result.output


def test_secret_key_allows_simple_names(umx_home: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["secret", "set", "my-api-key", "s3cret"])
    assert result.exit_code == 0

    result = runner.invoke(main, ["secret", "get", "my-api-key"])
    assert result.exit_code == 0
    assert "s3cret" in result.output


def test_gitignore_includes_jsonl(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git_init(repo)
    content = (repo / ".gitignore").read_text()
    assert "meta/*.jsonl" in content
    assert "!meta/tombstones.jsonl" in content
    assert "!meta/processing.jsonl" in content


def test_project_secret_excluded_from_injection(project_dir: Path, project_repo: Path, user_repo: Path) -> None:
    from umx.memory import add_fact

    add_fact(
        project_repo,
        Fact(
            fact_id="01TESTSECRET000000000000001",
            text="database password is hunter2",
            scope=Scope.PROJECT_SECRET,
            topic="secrets",
            encoding_strength=5,
            memory_type=MemoryType.EXPLICIT_SEMANTIC,
            verification=Verification.CORROBORATED,
            source_type=SourceType.GROUND_TRUTH_CODE,
            consolidation_status=ConsolidationStatus.STABLE,
        ),
    )
    add_fact(
        project_repo,
        Fact(
            fact_id="01TESTNORMAL000000000000001",
            text="app runs on port 8080",
            scope=Scope.PROJECT,
            topic="config",
            encoding_strength=4,
            memory_type=MemoryType.EXPLICIT_SEMANTIC,
            verification=Verification.CORROBORATED,
            source_type=SourceType.GROUND_TRUTH_CODE,
            consolidation_status=ConsolidationStatus.STABLE,
        ),
    )

    runner = CliRunner()
    result = runner.invoke(main, ["inject", "--cwd", str(project_dir), "--prompt", "database password"])
    assert result.exit_code == 0
    assert "hunter2" not in result.output
    assert "port 8080" in result.output


def test_extract_topic_uses_redacted_text() -> None:
    from umx.dream.extract import _extract_topic

    # The topic extractor should work on whatever text is passed to it
    redacted = "The [REDACTED] service uses PostgreSQL"
    topic = _extract_topic(redacted)
    # Should not be an empty/null topic
    assert topic
    # Should not contain raw sensitive data if we passed redacted text
    assert "secret_api_key" not in topic
