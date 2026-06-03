from __future__ import annotations

import json
from pathlib import Path

import pytest

import umx.memory as memory
from umx.identity import generate_fact_id
from umx.memory import FactDataIntegrityError, add_fact, read_fact_file, save_repository_facts, topic_path
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


def test_add_fact_merges_same_fact_id_metadata_without_downgrading_strength(project_repo: Path) -> None:
    fact_id = generate_fact_id()
    stronger = make_fact(
        "postgres runs on port 5433 in dev",
        topic="devenv",
        fact_id=fact_id,
        encoding_strength=5,
        verification=Verification.HUMAN_CONFIRMED,
        tags=["trusted"],
        source_tool="human",
    )
    weaker = make_fact(
        "postgres runs on port 5433 in dev",
        topic="devenv",
        fact_id=fact_id,
        encoding_strength=2,
        verification=Verification.SELF_REPORTED,
        tags=["repeat-observation"],
        source_tool="codex",
    )

    add_fact(project_repo, stronger, auto_commit=False)
    add_fact(project_repo, weaker, auto_commit=False)

    facts = read_fact_file(topic_path(project_repo, "devenv"), repo_dir=project_repo)

    assert len(facts) == 1
    merged = facts[0]
    assert merged.encoding_strength == 5
    assert merged.verification == Verification.HUMAN_CONFIRMED
    assert merged.tags == ["trusted", "repeat-observation"]
    assert merged.corroborated_by_tools == ["codex"]


def test_add_fact_quarantines_same_fact_id_with_divergent_text(project_repo: Path) -> None:
    fact_id = generate_fact_id()
    add_fact(
        project_repo,
        make_fact("postgres runs on port 5433 in dev", topic="devenv", fact_id=fact_id),
        auto_commit=False,
    )

    with pytest.raises(FactDataIntegrityError, match=f"same fact_id collision for {fact_id}"):
        add_fact(
            project_repo,
            make_fact("postgres runs on port 5432 in dev", topic="devenv", fact_id=fact_id),
            auto_commit=False,
        )

    reports = sorted((project_repo / "local" / "quarantine").glob(f"fact-id-collision-{fact_id}-*.json"))
    assert len(reports) == 1
    payload = json.loads(reports[0].read_text())
    assert payload["reason"] == "same fact_id with divergent text"
    assert payload["existing"]["fact"]["text"] == "postgres runs on port 5433 in dev"
    assert payload["incoming"]["fact"]["text"] == "postgres runs on port 5432 in dev"
    facts = read_fact_file(topic_path(project_repo, "devenv"), repo_dir=project_repo)
    assert len(facts) == 1
    assert facts[0].text == "postgres runs on port 5433 in dev"


def test_save_repository_facts_merges_same_fact_id_entries(project_repo: Path) -> None:
    fact_id = generate_fact_id()
    weaker = make_fact(
        "postgres runs on port 5433 in dev",
        topic="devenv",
        fact_id=fact_id,
        encoding_strength=2,
        verification=Verification.SELF_REPORTED,
        tags=["first-source"],
        source_tool="codex",
    )
    stronger = make_fact(
        "postgres runs on port 5433 in dev",
        topic="devenv",
        fact_id=fact_id,
        encoding_strength=4,
        verification=Verification.CORROBORATED,
        tags=["second-source"],
        source_tool="cli",
    )

    save_repository_facts(project_repo, [weaker, stronger], auto_commit=False)

    facts = read_fact_file(topic_path(project_repo, "devenv"), repo_dir=project_repo)
    assert len(facts) == 1
    merged = facts[0]
    assert merged.encoding_strength == 4
    assert merged.verification == Verification.CORROBORATED
    assert merged.tags == ["first-source", "second-source"]
    assert merged.corroborated_by_tools == ["codex"]


def test_same_fact_id_merge_filters_null_metadata_and_deep_merges_encoding_context(project_repo: Path) -> None:
    fact_id = generate_fact_id()
    existing = make_fact(
        "postgres runs on port 5433 in dev",
        topic="devenv",
        fact_id=fact_id,
        tags=["trusted", None],
        corroborated_by_tools=["tool-a", None],
        encoding_context={
            "cross_project_repos": ["repo-a"],
            "cross_project_occurrences": [{"fact_id": "FACT-A", "repo": "repo-a"}],
            "corroborating_source_weights": [0.5],
        },
        source_tool="human",
    )
    incoming = make_fact(
        "postgres runs on port 5433 in dev",
        topic="devenv",
        fact_id=fact_id,
        tags=[None, "repeat-observation"],
        encoding_context={
            "cross_project_repos": ["repo-b"],
            "cross_project_occurrences": [{"fact_id": "FACT-B", "repo": "repo-b"}],
            "corroborating_source_weights": [1.5],
        },
        source_tool="codex",
    )

    merged = memory._merge_same_fact_id(existing, incoming, repo_dir=project_repo)
    assert merged.tags == ["trusted", "repeat-observation"]
    assert merged.corroborated_by_tools == ["tool-a", "codex"]
    assert merged.encoding_context["cross_project_repos"] == ["repo-a", "repo-b"]
    assert merged.encoding_context["cross_project_occurrences"] == [
        {"fact_id": "FACT-A", "repo": "repo-a"},
        {"fact_id": "FACT-B", "repo": "repo-b"},
    ]
    assert merged.encoding_context["corroborating_source_weights"] == [0.5, 1.5]
