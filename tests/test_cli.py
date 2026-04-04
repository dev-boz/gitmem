"""Tests for umx.cli — CLI commands."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from umx.cli import main
from umx.memory import add_fact
from umx.models import Fact, MemoryType, Scope


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def project(tmp_path: Path) -> Path:
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / ".git").mkdir()
    return project_root


class TestInit:
    def test_init_creates_structure(self, runner: CliRunner, project: Path):
        result = runner.invoke(main, ["init", "--cwd", str(project)])
        assert result.exit_code == 0
        assert "Initialized" in result.output
        assert (project / ".umx").is_dir()
        assert (project / ".umx" / "topics").is_dir()
        assert (project / ".umx" / "local").is_dir()
        assert (project / ".umx" / "config.yaml").exists()

    def test_init_idempotent(self, runner: CliRunner, project: Path):
        runner.invoke(main, ["init", "--cwd", str(project)])
        result = runner.invoke(main, ["init", "--cwd", str(project)])
        assert result.exit_code == 0


class TestAdd:
    def test_add_fact(self, runner: CliRunner, project: Path):
        runner.invoke(main, ["init", "--cwd", str(project)])
        result = runner.invoke(main, [
            "add", "--cwd", str(project),
            "--text", "postgres runs on port 5433",
            "--topic", "devenv",
            "--tags", "database,environment",
        ])
        assert result.exit_code == 0
        assert "Added fact" in result.output

    def test_add_fact_appears_in_view(self, runner: CliRunner, project: Path):
        runner.invoke(main, ["init", "--cwd", str(project)])
        runner.invoke(main, [
            "add", "--cwd", str(project),
            "--text", "use pytest -x for fast runs",
            "--topic", "testing",
        ])
        result = runner.invoke(main, ["view", "--cwd", str(project)])
        assert result.exit_code == 0
        assert "use pytest -x for fast runs" in result.output


class TestView:
    def test_view_empty(self, runner: CliRunner, project: Path):
        runner.invoke(main, ["init", "--cwd", str(project)])
        result = runner.invoke(main, ["view", "--cwd", str(project)])
        assert result.exit_code == 0
        assert "No facts found" in result.output

    def test_view_with_min_strength(self, runner: CliRunner, project: Path):
        runner.invoke(main, ["init", "--cwd", str(project)])
        runner.invoke(main, [
            "add", "--cwd", str(project),
            "--text", "strong fact",
        ])

        umx_dir = project / ".umx"
        weak = Fact(
            id=Fact.generate_id(),
            text="weak fact",
            scope=Scope.PROJECT_TEAM,
            topic="general",
            encoding_strength=1,
            memory_type=MemoryType.IMPLICIT,
            confidence=0.5,
        )
        add_fact(umx_dir, weak)

        result = runner.invoke(main, [
            "view", "--cwd", str(project), "--min-strength", "4",
        ])
        assert "strong fact" in result.output
        assert "weak fact" not in result.output

    def test_view_with_topic_filter(self, runner: CliRunner, project: Path):
        runner.invoke(main, ["init", "--cwd", str(project)])
        runner.invoke(main, [
            "add", "--cwd", str(project),
            "--text", "db fact", "--topic", "database",
        ])
        runner.invoke(main, [
            "add", "--cwd", str(project),
            "--text", "auth fact", "--topic", "auth",
        ])

        result = runner.invoke(main, [
            "view", "--cwd", str(project), "--topic", "database",
        ])
        assert "db fact" in result.output
        assert "auth fact" not in result.output


class TestStatus:
    def test_status_shows_counts(self, runner: CliRunner, project: Path):
        runner.invoke(main, ["init", "--cwd", str(project)])
        runner.invoke(main, [
            "add", "--cwd", str(project),
            "--text", "fact 1",
        ])
        runner.invoke(main, [
            "add", "--cwd", str(project),
            "--text", "fact 2",
        ])
        result = runner.invoke(main, ["status", "--cwd", str(project)])
        assert result.exit_code == 0
        assert "Team facts:" in result.output


class TestForget:
    def test_forget_topic(self, runner: CliRunner, project: Path):
        runner.invoke(main, ["init", "--cwd", str(project)])
        runner.invoke(main, [
            "add", "--cwd", str(project),
            "--text", "to forget", "--topic", "temp",
        ])
        result = runner.invoke(main, [
            "forget", "--cwd", str(project), "--topic", "temp",
        ])
        assert result.exit_code == 0
        assert "Forgot topic" in result.output

        # Verify gone
        result = runner.invoke(main, ["view", "--cwd", str(project)])
        assert "to forget" not in result.output

    def test_forget_nonexistent(self, runner: CliRunner, project: Path):
        runner.invoke(main, ["init", "--cwd", str(project)])
        result = runner.invoke(main, [
            "forget", "--cwd", str(project), "--topic", "nope",
        ])
        assert result.exit_code == 0
        assert "not found" in result.output


class TestInject:
    def test_inject_outputs_facts(self, runner: CliRunner, project: Path):
        runner.invoke(main, ["init", "--cwd", str(project)])
        runner.invoke(main, [
            "add", "--cwd", str(project),
            "--text", "injectable fact",
        ])
        result = runner.invoke(main, [
            "inject", "--cwd", str(project), "--tool", "aider",
        ])
        assert result.exit_code == 0
        assert "injectable fact" in result.output

    def test_inject_empty(self, runner: CliRunner, project: Path):
        runner.invoke(main, ["init", "--cwd", str(project)])
        result = runner.invoke(main, [
            "inject", "--cwd", str(project), "--tool", "aider",
        ])
        assert "No memory facts" in result.output


class TestDream:
    def test_dream_no_umx(self, runner: CliRunner, project: Path):
        result = runner.invoke(main, [
            "dream", "--cwd", str(project),
        ])
        assert result.exit_code != 0
        assert "No .umx/ found" in result.output

    def test_dream_runs(self, runner: CliRunner, project: Path):
        runner.invoke(main, ["init", "--cwd", str(project)])
        runner.invoke(main, [
            "add", "--cwd", str(project),
            "--text", "test fact for dream",
        ])
        result = runner.invoke(main, [
            "dream", "--cwd", str(project), "--force",
        ])
        assert result.exit_code == 0
        assert "Dream complete" in result.output


class TestConflicts:
    def test_no_conflicts(self, runner: CliRunner, project: Path):
        runner.invoke(main, ["init", "--cwd", str(project)])
        result = runner.invoke(main, [
            "conflicts", "--cwd", str(project),
        ])
        assert result.exit_code == 0
        assert "No conflicts" in result.output
