from __future__ import annotations

from pathlib import Path

import umx.memory as memory
from umx.identity import generate_fact_id
from umx.memory import add_fact, read_fact_file, topic_path
from umx.models import (
    ConsolidationStatus,
    Fact,
    MemoryType,
    Scope,
    SourceType,
    Verification,
)


def make_fact(text: str, topic: str = "general", **overrides) -> Fact:
    values = {
        "fact_id": generate_fact_id(),
        "text": text,
        "scope": Scope.PROJECT,
        "topic": topic,
        "encoding_strength": 3,
        "memory_type": MemoryType.EXPLICIT_SEMANTIC,
        "verification": Verification.SELF_REPORTED,
        "source_type": SourceType.TOOL_OUTPUT,
        "source_tool": "claude-code",
        "source_session": "2026-04-11-test",
        "consolidation_status": ConsolidationStatus.FRAGILE,
    }
    values.update(overrides)
    return Fact(**values)


def test_bare_markdown_line_becomes_human_confirmed(project_repo: Path) -> None:
    path = topic_path(project_repo, "devenv")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# devenv\n\n## Facts\n- postgres runs on port 5433 in dev\n")

    facts = read_fact_file(path, repo_dir=project_repo)

    assert len(facts) == 1
    fact = facts[0]
    assert fact.encoding_strength == 5
    assert fact.verification == Verification.HUMAN_CONFIRMED
    assert fact.source_type == SourceType.USER_PROMPT
    assert "<!-- umx:" in path.read_text()


def test_editing_existing_text_creates_supersession(project_repo: Path) -> None:
    original = make_fact("postgres runs on port 5432 in dev", topic="devenv")
    add_fact(project_repo, original)
    path = topic_path(project_repo, "devenv")
    original_text = path.read_text()
    path.write_text(original_text.replace("port 5432", "port 5433"))

    facts = read_fact_file(path, repo_dir=project_repo)

    assert len(facts) == 2
    old_fact = next(fact for fact in facts if fact.text.endswith("5432 in dev"))
    new_fact = next(fact for fact in facts if fact.text.endswith("5433 in dev"))
    assert old_fact.fact_id != new_fact.fact_id
    assert old_fact.superseded_by == new_fact.fact_id
    assert new_fact.supersedes == old_fact.fact_id
    assert new_fact.encoding_strength == 5


def test_read_fact_file_reuses_process_cache_until_file_changes(
    project_repo: Path,
    monkeypatch,
) -> None:
    fact = make_fact("postgres runs on port 5433 in dev", topic="devenv")
    add_fact(project_repo, fact)
    path = topic_path(project_repo, "devenv")
    original_parse = memory._parse_fact_lines
    calls = 0

    def counted_parse(path: Path, repo_dir: Path):
        nonlocal calls
        calls += 1
        return original_parse(path, repo_dir)

    monkeypatch.setattr(memory, "_parse_fact_lines", counted_parse)

    first = read_fact_file(path, repo_dir=project_repo)
    second = read_fact_file(path, repo_dir=project_repo)
    path.write_text(path.read_text().replace("5433", "5434"))
    third = read_fact_file(path, repo_dir=project_repo)

    assert len(first) == 1
    assert len(second) == 1
    assert any(entry.text.endswith("5434 in dev") for entry in third)
    assert calls == 2
