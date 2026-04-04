"""Tests for umx.inject — injection and relevance scoring."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from umx.inject import (
    build_injection_block,
    collect_facts_for_injection,
    inject_for_tool,
)
from umx.memory import add_fact, save_topic_facts
from umx.models import Fact, MemoryType, Scope, UmxConfig
from umx.scope import init_project


def _make_fact(text: str, topic: str = "general", **kwargs) -> Fact:
    defaults = dict(
        id=Fact.generate_id(),
        scope=Scope.PROJECT_TEAM,
        encoding_strength=3,
        memory_type=MemoryType.EXPLICIT_EPISODIC,
        confidence=0.8,
        created=datetime.now(timezone.utc),
    )
    defaults.update(kwargs)
    return Fact(text=text, topic=topic, **defaults)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / ".git").mkdir()
    init_project(project_root)
    return project_root


class TestBuildInjectionBlock:
    def test_empty_facts(self):
        assert build_injection_block([]) == ""

    def test_basic_block(self):
        facts = [
            _make_fact("postgres on 5433", topic="devenv"),
            _make_fact("use pytest -x", topic="testing"),
        ]
        block = build_injection_block(facts)
        assert "# Project Memory (umx)" in block
        assert "postgres on 5433" in block
        assert "use pytest -x" in block

    def test_grouped_by_topic(self):
        facts = [
            _make_fact("fact 1", topic="devenv"),
            _make_fact("fact 2", topic="devenv"),
            _make_fact("fact 3", topic="testing"),
        ]
        block = build_injection_block(facts)
        assert "## devenv" in block
        assert "## testing" in block

    def test_budget_limits_output(self):
        facts = [_make_fact(f"fact {i}" * 10, topic="t") for i in range(100)]
        block = build_injection_block(facts, max_tokens=100)
        # Should be shorter than all facts combined
        all_block = build_injection_block(facts, max_tokens=100000)
        assert len(block) < len(all_block)


class TestCollectFacts:
    def test_collects_from_project(self, project: Path):
        umx_dir = project / ".umx"
        add_fact(umx_dir, _make_fact("test fact 1"))
        add_fact(umx_dir, _make_fact("test fact 2"))

        facts = collect_facts_for_injection(project)
        assert len(facts) >= 2

    def test_empty_project(self, project: Path):
        facts = collect_facts_for_injection(project)
        assert facts == []


class TestInjectForTool:
    def test_returns_content(self, project: Path):
        umx_dir = project / ".umx"
        add_fact(umx_dir, _make_fact("important fact"))

        content = inject_for_tool(project, tool="aider")
        assert "important fact" in content

    def test_writes_to_output_path(self, project: Path):
        umx_dir = project / ".umx"
        add_fact(umx_dir, _make_fact("output fact"))

        output = project / "output.md"
        inject_for_tool(project, tool="aider", output_path=output)
        assert output.exists()
        assert "output fact" in output.read_text()
