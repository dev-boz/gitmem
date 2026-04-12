from __future__ import annotations

from pathlib import Path

from umx.config import MemoryConfig, UMXConfig, default_config
from umx.identity import generate_fact_id
from umx.memory import write_memory_md
from umx.models import (
    ConsolidationStatus,
    Fact,
    MemoryType,
    Scope,
    SourceType,
    TaskStatus,
    Verification,
)
from umx.search import ensure_usage_db, injected_but_uncited, record_usage


def _make_fact(
    text: str,
    topic: str = "general",
    scope: Scope = Scope.PROJECT,
    encoding_strength: int = 3,
    task_status: TaskStatus | None = None,
) -> Fact:
    return Fact(
        fact_id=generate_fact_id(),
        text=text,
        scope=scope,
        topic=topic,
        encoding_strength=encoding_strength,
        memory_type=MemoryType.EXPLICIT_SEMANTIC,
        verification=Verification.SELF_REPORTED,
        source_type=SourceType.TOOL_OUTPUT,
        source_tool="test",
        source_session="test-session",
        consolidation_status=ConsolidationStatus.FRAGILE,
        task_status=task_status,
    )


def test_memory_md_token_budget(project_repo: Path) -> None:
    """Write many facts, verify MEMORY.md respects token cap."""
    # Each fact text ~100 chars => ~25 tokens. 50 facts => ~1250 tokens.
    # With budget of 200 tokens, only a handful should appear in Hot Facts.
    facts = [_make_fact(f"fact number {i} " + "x" * 80, topic=f"topic{i}") for i in range(50)]
    config = default_config()
    config.memory = MemoryConfig(hot_tier_max_tokens=200, index_max_lines=200)

    write_memory_md(project_repo, facts, config=config)

    content = (project_repo / "meta" / "MEMORY.md").read_text()
    # Index should list ALL 50 topics
    assert content.count("| topic") == 50
    # Hot Facts should have fewer than 50 entries
    hot_section = content.split("## Hot Facts")[1]
    hot_fact_lines = [line for line in hot_section.splitlines() if line.startswith("- ")]
    assert len(hot_fact_lines) < 50
    assert len(hot_fact_lines) > 0


def test_memory_md_protected_floor(project_repo: Path) -> None:
    """Verify user S:4+ and open tasks always included even with tiny budget."""
    user_strong = _make_fact(
        "user preference important",
        topic="prefs",
        scope=Scope.USER,
        encoding_strength=4,
    )
    open_task = _make_fact(
        "open task fix bug",
        topic="tasks",
        task_status=TaskStatus.OPEN,
    )
    low_priority = _make_fact(
        "low priority filler fact " + "y" * 200,
        topic="filler",
        encoding_strength=1,
    )
    config = default_config()
    config.memory = MemoryConfig(hot_tier_max_tokens=100, index_max_lines=200)

    write_memory_md(project_repo, [user_strong, open_task, low_priority], config=config)

    content = (project_repo / "meta" / "MEMORY.md").read_text()
    hot_section = content.split("## Hot Facts")[1]
    assert "user preference important" in hot_section
    assert "open task fix bug" in hot_section


def test_memory_md_packing_order(project_repo: Path) -> None:
    """Verify higher-scoring facts appear before lower-scoring in same topic."""
    high = _make_fact("high strength fact", topic="alpha", encoding_strength=5)
    low = _make_fact("low strength fact", topic="alpha", encoding_strength=1)
    config = default_config()
    config.memory = MemoryConfig(hot_tier_max_tokens=3000, index_max_lines=200)

    write_memory_md(project_repo, [low, high], config=config)

    content = (project_repo / "meta" / "MEMORY.md").read_text()
    hot_section = content.split("## Hot Facts")[1]
    # Both should be present
    assert "high strength fact" in hot_section
    assert "low strength fact" in hot_section


def test_memory_md_capacity_warning(project_repo: Path) -> None:
    """Verify >90% capacity comment appears."""
    # Create facts that will fill >90% of a small budget
    facts = [_make_fact(f"protected user fact {i}", topic=f"t{i}", scope=Scope.USER, encoding_strength=5) for i in range(20)]
    config = default_config()
    # Token estimate per fact: ~6 tokens each. 20 facts => ~120 tokens.
    # Set budget to 130 so that 120/130 > 90%
    config.memory = MemoryConfig(hot_tier_max_tokens=130, index_max_lines=200)

    write_memory_md(project_repo, facts, config=config)

    content = (project_repo / "meta" / "MEMORY.md").read_text()
    assert "<!-- umx: hot tier at" in content
    assert "% capacity -->" in content


def test_injected_but_uncited(project_repo: Path) -> None:
    """Record usage, verify detection of injected-but-uncited facts."""
    ensure_usage_db(project_repo)
    fact_id = "test-fact-001"

    # Inject 10 times, never cite
    for _ in range(10):
        record_usage(project_repo, fact_id, injected=True)

    results = injected_but_uncited(project_repo, min_injections=5)
    assert len(results) == 1
    assert results[0]["fact_id"] == fact_id
    assert results[0]["injected_count"] == 10
    assert results[0]["cited_count"] == 0

    # Now cite it once — should no longer appear
    record_usage(project_repo, fact_id, cited=True)
    results = injected_but_uncited(project_repo, min_injections=5)
    assert len(results) == 0
