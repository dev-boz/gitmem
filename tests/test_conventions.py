from __future__ import annotations

from pathlib import Path

import pytest

from umx.conventions import (
    ConventionSet,
    is_valid_topic,
    normalize_fact_text,
    parse_conventions,
    suggest_topic,
    validate_conventions_file,
    validate_fact,
)
from umx.models import (
    ConsolidationStatus,
    Fact,
    MemoryType,
    Scope,
    SourceType,
    Verification,
)


def _make_fact(
    text: str = "PostgreSQL runs on port 5433",
    topic: str = "devenv",
    fact_id: str = "f-1",
) -> Fact:
    return Fact(
        fact_id=fact_id,
        text=text,
        scope=Scope.PROJECT,
        topic=topic,
        encoding_strength=3,
        memory_type=MemoryType.EXPLICIT_SEMANTIC,
        verification=Verification.SELF_REPORTED,
        source_type=SourceType.USER_PROMPT,
    )


# ── 1. Project-specific conventions ──────────────────────────────────────

def test_parse_project_conventions(tmp_path: Path) -> None:
    md = tmp_path / "CONVENTIONS.md"
    md.write_text(
        "# Conventions\n\n"
        "## Topic taxonomy\n"
        "- devenv: Development environment\n\n"
        "## Fact phrasing\n"
        "- Use present tense\n\n"
        "## Entity vocabulary\n"
        "- pg=PostgreSQL\n\n"
        "## Project-specific conventions\n"
        "- ports: PostgreSQL runs on 5433, not 5432\n"
        "- naming: Services use kebab-case, not camelCase\n"
        "- logging: All services log to stdout\n"
    )
    cs = parse_conventions(md)
    assert len(cs.project_conventions) == 3
    assert "ports: PostgreSQL runs on 5433, not 5432" in cs.project_conventions
    # schema_conflicts auto-detected from "not " keyword
    assert len(cs.schema_conflicts) == 2
    assert any("5433" in c for c in cs.schema_conflicts)
    assert any("kebab-case" in c for c in cs.schema_conflicts)


# ── 2. Hierarchical topics ───────────────────────────────────────────────

def test_parse_hierarchical_topics(tmp_path: Path) -> None:
    md = tmp_path / "CONVENTIONS.md"
    md.write_text(
        "# Conventions\n\n"
        "## Topic taxonomy\n"
        "- devenv: Development environment\n"
        "  - devenv/ports: Port configurations\n"
        "  - devenv/tools: Tool versions and configs\n"
        "- deploy: Deployment\n\n"
        "## Fact phrasing\n- Atomic facts only\n\n"
        "## Entity vocabulary\n"
    )
    cs = parse_conventions(md)
    assert "devenv" in cs.topics
    assert "devenv/ports" in cs.topics
    assert "devenv/tools" in cs.topics
    assert "deploy" in cs.topics
    assert cs.topic_descriptions["devenv"] == "Development environment"
    assert cs.topic_descriptions["devenv/ports"] == "Port configurations"


def test_is_valid_topic_hierarchy() -> None:
    cs = ConventionSet(topics={"devenv", "devenv/ports", "deploy"})
    assert is_valid_topic("devenv", cs)
    assert is_valid_topic("devenv/ports", cs)
    # Child of declared parent, parent exists
    assert is_valid_topic("devenv/tools", cs)
    assert not is_valid_topic("unknown", cs)
    assert not is_valid_topic("random/stuff", cs)


# ── 3. Entity vocabulary enforcement ────────────────────────────────────

def test_validate_entity_vocabulary_violation() -> None:
    cs = ConventionSet(
        topics={"devenv"},
        entity_vocabulary={"pg": "PostgreSQL", "k8s": "Kubernetes"},
    )
    fact = _make_fact(text="The pg cluster runs on k8s", topic="devenv")
    issues = validate_fact(fact, cs)
    assert any("'PostgreSQL'" in i and "'pg'" in i for i in issues)
    assert any("'Kubernetes'" in i and "'k8s'" in i for i in issues)


# ── 4. Atomicity check ──────────────────────────────────────────────────

def test_validate_atomicity() -> None:
    cs = ConventionSet(topics={"devenv"})
    # Semicolon compound
    issues1 = validate_fact(_make_fact("Uses Redis; also uses Postgres"), cs)
    assert any("atomic" in i for i in issues1)
    # "and also" compound
    issues2 = validate_fact(_make_fact("Uses Redis and also uses Postgres"), cs)
    assert any("atomic" in i for i in issues2)
    # ", and" compound
    issues3 = validate_fact(_make_fact("Uses Redis, and uses Postgres"), cs)
    assert any("atomic" in i for i in issues3)
    # Multi-sentence
    issues4 = validate_fact(_make_fact("Uses Redis. Also uses Postgres"), cs)
    assert any("atomic" in i for i in issues4)
    # Clean fact — no atomicity issue
    issues5 = validate_fact(_make_fact("Uses Redis for caching"), cs)
    assert not any("atomic" in i for i in issues5)


# ── 5. Tense enforcement ────────────────────────────────────────────────

def test_validate_tense() -> None:
    cs = ConventionSet(
        topics={"devenv"},
        phrasing_rules=["Use present tense"],
    )
    # "was" prefix
    issues1 = validate_fact(_make_fact("Was using PostgreSQL 14"), cs)
    assert any("present-tense" in i for i in issues1)
    # "had been"
    issues2 = validate_fact(_make_fact("Service had been deployed to AWS"), cs)
    assert any("present-tense" in i for i in issues2)
    # "has been"
    issues3 = validate_fact(_make_fact("Config has been updated recently"), cs)
    assert any("present-tense" in i for i in issues3)
    # Present tense — no issue
    issues4 = validate_fact(_make_fact("Service runs on port 5433"), cs)
    assert not any("present-tense" in i for i in issues4)


def test_validate_tense_no_rule() -> None:
    """No tense issue when no phrasing rule mentions present tense."""
    cs = ConventionSet(
        topics={"devenv"},
        phrasing_rules=["Atomic facts only"],
    )
    issues = validate_fact(_make_fact("Was using PostgreSQL 14"), cs)
    assert not any("present-tense" in i for i in issues)


# ── 6. Length validation ────────────────────────────────────────────────

def test_validate_length() -> None:
    cs = ConventionSet(topics={"devenv"})
    # Too short
    issues_short = validate_fact(_make_fact("Hi"), cs)
    assert any("too short" in i for i in issues_short)
    # Too long
    long_text = "x" * 201
    issues_long = validate_fact(_make_fact(long_text), cs)
    assert any("200 characters" in i for i in issues_long)
    # Just right
    good_text = "x" * 50
    issues_good = validate_fact(_make_fact(good_text), cs)
    assert not any("short" in i or "200" in i for i in issues_good)


# ── 7. Duplicate text detection ──────────────────────────────────────────

def test_validate_duplicate() -> None:
    cs = ConventionSet(topics={"devenv"})
    f1 = _make_fact("PostgreSQL runs on port 5433", fact_id="f-1")
    f2 = _make_fact("PostgreSQL runs on port 5433", fact_id="f-2")
    f3 = _make_fact("Redis runs on port 6379", fact_id="f-3")
    issues = validate_fact(f1, cs, all_facts=[f1, f2, f3])
    assert any("duplicate" in i for i in issues)
    issues_no_dup = validate_fact(f3, cs, all_facts=[f1, f2, f3])
    assert not any("duplicate" in i for i in issues_no_dup)


# ── 8. Normalize fact text ───────────────────────────────────────────────

def test_normalize_fact_text() -> None:
    cs = ConventionSet(entity_vocabulary={"pg": "PostgreSQL", "k8s": "Kubernetes"})
    # Alias replacement + trailing period + capitalize + collapse spaces
    result = normalize_fact_text("the pg  cluster runs on  k8s.", cs)
    assert result == "The PostgreSQL cluster runs on Kubernetes"

    # Already clean
    result2 = normalize_fact_text("Service runs fine", cs)
    assert result2 == "Service runs fine"

    # Capitalization
    result3 = normalize_fact_text("lower case start", cs)
    assert result3 == "Lower case start"


# ── 9. Suggest topic ────────────────────────────────────────────────────

def test_suggest_topic() -> None:
    cs = ConventionSet(
        topics={"devenv", "devenv/ports", "deploy", "testing"},
        topic_descriptions={
            "devenv": "Development environment",
            "devenv/ports": "Port configurations",
            "deploy": "Deployment and infrastructure",
            "testing": "Test framework and strategy",
        },
    )
    assert suggest_topic("port 5433 configuration", cs) == "devenv/ports"
    assert suggest_topic("deployment pipeline for prod", cs) == "deploy"
    assert suggest_topic("completely unrelated text xyz", cs) is None


# ── 10. Validate conventions file ───────────────────────────────────────

def test_validate_conventions_file_valid(tmp_path: Path) -> None:
    md = tmp_path / "CONVENTIONS.md"
    md.write_text(
        "# Conventions\n\n"
        "## Topic taxonomy\n"
        "- devenv: Development environment\n\n"
        "## Fact phrasing\n"
        "- Use present tense\n"
        "- Atomic facts only\n\n"
        "## Entity vocabulary\n"
        "- pg=PostgreSQL\n"
    )
    issues = validate_conventions_file(md)
    assert issues == []


def test_validate_conventions_file_missing_sections(tmp_path: Path) -> None:
    md = tmp_path / "CONVENTIONS.md"
    md.write_text("# Conventions\n\n## Topic taxonomy\n- general: default\n")
    issues = validate_conventions_file(md)
    assert any("Fact Phrasing" in i for i in issues)
    assert any("Entity Vocabulary" in i for i in issues)


def test_validate_conventions_file_duplicate_topic(tmp_path: Path) -> None:
    md = tmp_path / "CONVENTIONS.md"
    md.write_text(
        "# Conventions\n\n"
        "## Topic taxonomy\n"
        "- devenv: Development environment\n"
        "- devenv: Duplicate!\n\n"
        "## Fact phrasing\n- Atomic facts only\n\n"
        "## Entity vocabulary\n"
    )
    issues = validate_conventions_file(md)
    assert any("duplicate topic" in i for i in issues)


def test_validate_conventions_file_circular_alias(tmp_path: Path) -> None:
    md = tmp_path / "CONVENTIONS.md"
    md.write_text(
        "# Conventions\n\n"
        "## Topic taxonomy\n- devenv: Dev\n\n"
        "## Fact phrasing\n- Atomic facts only\n\n"
        "## Entity vocabulary\n"
        "- foo=bar\n"
        "- bar=foo\n"
    )
    issues = validate_conventions_file(md)
    assert any("circular" in i for i in issues)


def test_validate_conventions_file_not_found(tmp_path: Path) -> None:
    issues = validate_conventions_file(tmp_path / "MISSING.md")
    assert any("not found" in i for i in issues)


def test_validate_conventions_file_vague_rule(tmp_path: Path) -> None:
    md = tmp_path / "CONVENTIONS.md"
    md.write_text(
        "# Conventions\n\n"
        "## Topic taxonomy\n- devenv: Dev\n\n"
        "## Fact phrasing\n- ok\n\n"
        "## Entity vocabulary\n"
    )
    issues = validate_conventions_file(md)
    assert any("vague" in i for i in issues)


# ── 11. Convention-aware lint (integration) ──────────────────────────────

def test_convention_aware_lint() -> None:
    """Full integration: lint with all convention features active."""
    cs = ConventionSet(
        topics={"devenv", "devenv/ports", "deploy"},
        topic_descriptions={
            "devenv": "Development environment",
            "devenv/ports": "Port configurations",
        },
        phrasing_rules=["Use present tense"],
        entity_vocabulary={"pg": "PostgreSQL"},
    )

    # Good fact — no issues
    good = _make_fact("Service runs on port 5433", topic="devenv", fact_id="f-good")
    assert validate_fact(good, cs) == []

    # Bad: unknown topic
    bad_topic = _make_fact("Runs fine", topic="unknown", fact_id="f-bad-topic")
    issues = validate_fact(bad_topic, cs)
    assert any("not declared" in i for i in issues)

    # Bad: alias in text
    alias_use = _make_fact("The pg server is fast", topic="devenv", fact_id="f-alias")
    issues2 = validate_fact(alias_use, cs)
    assert any("'PostgreSQL'" in i for i in issues2)

    # Topic alias detection
    cs2 = ConventionSet(
        topics={"devenv"},
        entity_vocabulary={"dev-environment": "devenv"},
    )
    fact_alias = _make_fact("Test fact text here!", topic="dev-environment", fact_id="f-ta")
    issues3 = validate_fact(fact_alias, cs2)
    assert any("alias" in i and "canonical" in i for i in issues3)
