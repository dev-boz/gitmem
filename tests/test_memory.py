"""Tests for umx.memory — MEMORY.md, topic files, and fact I/O."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from umx.memory import (
    add_fact,
    build_memory_md,
    derive_json,
    find_fact_by_id,
    format_fact_line,
    load_all_facts,
    load_config,
    load_topic_facts,
    parse_fact_line,
    read_memory_md,
    remove_fact,
    save_config,
    save_topic_facts,
    write_memory_md,
)
from umx.models import Fact, MemoryType, Scope, UmxConfig


def _make_fact(
    id: str = "f_test",
    text: str = "test fact",
    topic: str = "general",
    **kwargs,
) -> Fact:
    defaults = dict(
        scope=Scope.PROJECT_TEAM,
        encoding_strength=3,
        memory_type=MemoryType.EXPLICIT_EPISODIC,
        confidence=0.8,
        created=datetime(2026, 4, 3, 20, 0, tzinfo=timezone.utc),
    )
    defaults.update(kwargs)
    return Fact(id=id, text=text, topic=topic, **defaults)


@pytest.fixture
def umx_dir(tmp_path: Path) -> Path:
    d = tmp_path / ".umx"
    d.mkdir()
    (d / "topics").mkdir()
    (d / "files").mkdir()
    return d


class TestParseFactLine:
    def test_full_annotated_line(self):
        line = '- [S:4] postgres runs on port 5433 <!-- umx: {"id":"f_001","conf":0.97,"corroborated_by":["aider"]} -->'
        fact = parse_fact_line(line, topic="devenv", scope=Scope.PROJECT_TEAM)
        assert fact is not None
        assert fact.id == "f_001"
        assert fact.text == "postgres runs on port 5433"
        assert fact.encoding_strength == 4
        assert fact.confidence == 0.97
        assert fact.corroborated_by == ["aider"]

    def test_line_without_metadata(self):
        line = "- [S:3] CORS warnings can be ignored"
        fact = parse_fact_line(line, topic="devenv", scope=Scope.PROJECT_TEAM)
        assert fact is not None
        assert fact.text == "CORS warnings can be ignored"
        assert fact.encoding_strength == 3
        assert fact.id.startswith("f_")

    def test_bare_line_promoted_to_s5(self):
        line = "- always run tests before merging"
        fact = parse_fact_line(line, topic="workflow", scope=Scope.PROJECT_TEAM)
        assert fact is not None
        assert fact.text == "always run tests before merging"
        assert fact.encoding_strength == 5
        assert fact.confidence == 1.0
        assert fact.memory_type == MemoryType.EXPLICIT_SEMANTIC

    def test_non_fact_line_returns_none(self):
        assert parse_fact_line("## Header", topic="t", scope=Scope.PROJECT_TEAM) is None
        assert parse_fact_line("", topic="t", scope=Scope.PROJECT_TEAM) is None
        assert parse_fact_line("some text", topic="t", scope=Scope.PROJECT_TEAM) is None


class TestFormatFactLine:
    def test_roundtrip(self):
        fact = _make_fact(
            id="f_001",
            text="postgres on 5433",
            corroborated_by=["aider"],
            tags=["db"],
        )
        line = format_fact_line(fact)
        assert "[S:3]" in line
        assert "postgres on 5433" in line
        assert "f_001" in line
        assert "<!-- umx:" in line

        # Parse it back
        parsed = parse_fact_line(line, topic="general", scope=Scope.PROJECT_TEAM)
        assert parsed is not None
        assert parsed.id == "f_001"
        assert parsed.text == "postgres on 5433"
        assert parsed.encoding_strength == 3


class TestTopicFiles:
    def test_save_and_load(self, umx_dir: Path):
        facts = [
            _make_fact(id="f_001", text="fact one", encoding_strength=4),
            _make_fact(id="f_002", text="fact two", encoding_strength=2),
        ]
        topic_path = umx_dir / "topics" / "devenv.md"
        save_topic_facts(topic_path, "devenv", facts)

        loaded = load_topic_facts(topic_path, "devenv", Scope.PROJECT_TEAM)
        assert len(loaded) == 2
        texts = {f.text for f in loaded}
        assert "fact one" in texts
        assert "fact two" in texts

    def test_sorted_by_strength_descending(self, umx_dir: Path):
        facts = [
            _make_fact(id="f_lo", text="low", encoding_strength=1),
            _make_fact(id="f_hi", text="high", encoding_strength=5),
        ]
        topic_path = umx_dir / "topics" / "test.md"
        save_topic_facts(topic_path, "test", facts)

        content = topic_path.read_text()
        lines = [l for l in content.splitlines() if l.startswith("- ")]
        assert "[S:5]" in lines[0]
        assert "[S:1]" in lines[1]

    def test_derive_json(self, umx_dir: Path):
        facts = [_make_fact(id="f_001", text="test")]
        topic_path = umx_dir / "topics" / "test.md"
        save_topic_facts(topic_path, "test", facts)
        derive_json(topic_path, facts)

        json_path = topic_path.with_suffix(".umx.json")
        assert json_path.exists()
        data = json.loads(json_path.read_text())
        assert len(data) == 1
        assert data[0]["id"] == "f_001"

    def test_load_nonexistent(self, umx_dir: Path):
        facts = load_topic_facts(
            umx_dir / "topics" / "nope.md", "nope", Scope.PROJECT_TEAM
        )
        assert facts == []


class TestLoadAllFacts:
    def test_loads_across_topics(self, umx_dir: Path):
        for topic in ["devenv", "auth", "testing"]:
            path = umx_dir / "topics" / f"{topic}.md"
            save_topic_facts(path, topic, [
                _make_fact(id=f"f_{topic}", text=f"{topic} fact", topic=topic),
            ])

        all_facts = load_all_facts(umx_dir, Scope.PROJECT_TEAM)
        assert len(all_facts) == 3
        topics = {f.topic for f in all_facts}
        assert topics == {"devenv", "auth", "testing"}


class TestMemoryMd:
    def test_build_memory_md(self, umx_dir: Path):
        save_topic_facts(
            umx_dir / "topics" / "devenv.md",
            "devenv",
            [
                _make_fact(id="f_1", text="fact 1", encoding_strength=4),
                _make_fact(id="f_2", text="fact 2", encoding_strength=3),
            ],
        )

        content = build_memory_md(umx_dir, scope="project_team")
        assert "umx memory index" in content
        assert "Devenv" in content
        assert "topics/devenv.md" in content

    def test_write_and_read(self, umx_dir: Path):
        content = "# test\nscope: project\n"
        write_memory_md(umx_dir, content)

        result = read_memory_md(umx_dir)
        assert result is not None
        assert "# test" in result

    def test_line_limit_enforced(self, umx_dir: Path):
        lines = ["line " + str(i) for i in range(300)]
        content = "\n".join(lines)
        write_memory_md(umx_dir, content)

        result = read_memory_md(umx_dir)
        assert result is not None
        assert len(result.splitlines()) <= 200


class TestAddRemoveFind:
    def test_add_fact(self, umx_dir: Path):
        fact = _make_fact(id="f_new", text="new fact")
        add_fact(umx_dir, fact)

        loaded = load_all_facts(umx_dir, Scope.PROJECT_TEAM)
        assert len(loaded) == 1
        assert loaded[0].id == "f_new"

    def test_add_duplicate_skipped(self, umx_dir: Path):
        fact = _make_fact(id="f_dup", text="duplicate")
        add_fact(umx_dir, fact)
        add_fact(umx_dir, fact)

        loaded = load_all_facts(umx_dir, Scope.PROJECT_TEAM)
        assert len(loaded) == 1

    def test_remove_fact(self, umx_dir: Path):
        fact = _make_fact(id="f_rm", text="to remove")
        add_fact(umx_dir, fact)
        assert remove_fact(umx_dir, "f_rm", "general", Scope.PROJECT_TEAM)

        loaded = load_all_facts(umx_dir, Scope.PROJECT_TEAM)
        assert len(loaded) == 0

    def test_remove_nonexistent(self, umx_dir: Path):
        assert not remove_fact(umx_dir, "f_nope", "general", Scope.PROJECT_TEAM)

    def test_find_fact_by_id(self, umx_dir: Path):
        fact = _make_fact(id="f_find", text="findable")
        add_fact(umx_dir, fact)

        found = find_fact_by_id(umx_dir, "f_find", Scope.PROJECT_TEAM)
        assert found is not None
        assert found.text == "findable"

    def test_find_nonexistent(self, umx_dir: Path):
        assert find_fact_by_id(umx_dir, "f_nope", Scope.PROJECT_TEAM) is None


class TestConfig:
    def test_save_and_load(self, umx_dir: Path):
        config = UmxConfig(decay_lambda=0.046, default_max_tokens=8000)
        save_config(umx_dir, config)

        loaded = load_config(umx_dir)
        assert loaded.decay_lambda == 0.046
        assert loaded.default_max_tokens == 8000

    def test_load_nonexistent_returns_defaults(self, umx_dir: Path):
        config = load_config(umx_dir)
        assert config.decay_lambda == 0.023
