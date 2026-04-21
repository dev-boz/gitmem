from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from click.testing import CliRunner

from umx.cli import main
from umx.memory import add_fact, iter_fact_files
from umx.models import (
    ConsolidationStatus,
    Fact,
    MemoryType,
    Scope,
    SourceType,
    Verification,
)
from umx.search import (
    incremental_rebuild,
    index_path,
    inject_candidate_ids,
    query_index,
    rebuild_index,
    search_sessions,
)
from umx.search_semantic import rerank_candidates
from umx.sessions import write_session


def _make_fact(text: str, topic: str = "general", **overrides) -> Fact:
    values = {
        "fact_id": overrides.pop("fact_id", "01TESTFACT0000000000000001"),
        "text": text,
        "scope": Scope.PROJECT,
        "topic": topic,
        "encoding_strength": 3,
        "memory_type": MemoryType.EXPLICIT_SEMANTIC,
        "verification": Verification.SELF_REPORTED,
        "source_type": SourceType.TOOL_OUTPUT,
        "source_tool": "codex",
        "source_session": "2026-04-11-session",
        "consolidation_status": ConsolidationStatus.FRAGILE,
    }
    values.update(overrides)
    return Fact(**values)


def test_incremental_rebuild_only_reindexes_changed(project_repo: Path) -> None:
    fact_a = _make_fact(
        "redis runs on port 6379",
        topic="infra",
        fact_id="01TESTFACT0000000000000010",
    )
    fact_b = _make_fact(
        "python version is 3.12",
        topic="toolchain",
        fact_id="01TESTFACT0000000000000011",
    )
    add_fact(project_repo, fact_a)
    add_fact(project_repo, fact_b)

    # Full rebuild first to seed file hashes
    rebuild_index(project_repo)

    # Modify only the infra file
    infra_path = project_repo / "facts" / "topics" / "infra.md"
    content = infra_path.read_text()
    infra_path.write_text(content.replace("6379", "6380"))

    count = incremental_rebuild(project_repo)
    assert count == 1  # only the changed file


def test_incremental_rebuild_first_call_does_full(project_repo: Path) -> None:
    fact = _make_fact(
        "node version is 20",
        topic="toolchain",
        fact_id="01TESTFACT0000000000000012",
    )
    add_fact(project_repo, fact)

    # No prior rebuild, so incremental should fall back to full
    count = incremental_rebuild(project_repo)
    assert count >= 1


def test_incremental_rebuild_schema_mismatch_does_full(project_repo: Path) -> None:
    fact = _make_fact(
        "node version is 20",
        topic="toolchain",
        fact_id="01TESTFACT0000000000000015",
    )
    add_fact(project_repo, fact)
    rebuild_index(project_repo)

    conn = sqlite3.connect(index_path(project_repo))
    conn.execute("UPDATE _meta SET value = '0' WHERE key = 'schema_version'")
    conn.commit()
    conn.close()

    count = incremental_rebuild(project_repo)
    assert count == len(iter_fact_files(project_repo))


def test_incremental_rebuild_legacy_index_without_source_paths_does_full(project_repo: Path) -> None:
    fact = _make_fact(
        "python version is 3.12",
        topic="toolchain",
        fact_id="01TESTFACT0000000000000016",
    )
    add_fact(project_repo, fact)
    rebuild_index(project_repo)

    conn = sqlite3.connect(index_path(project_repo))
    conn.execute("UPDATE memories SET source_path = NULL")
    conn.commit()
    conn.close()

    count = incremental_rebuild(project_repo)
    assert count == len(iter_fact_files(project_repo))


def test_incremental_rebuild_corrupt_file_hashes_does_full(project_repo: Path) -> None:
    fact = _make_fact(
        "python version is 3.12",
        topic="toolchain",
        fact_id="01TESTFACT0000000000000019",
    )
    add_fact(project_repo, fact)
    rebuild_index(project_repo)

    conn = sqlite3.connect(index_path(project_repo))
    conn.execute("UPDATE _meta SET value = 'not-json' WHERE key = 'file_hashes'")
    conn.commit()
    conn.close()

    count = incremental_rebuild(project_repo)
    assert count == len(iter_fact_files(project_repo))


def test_incremental_rebuild_preserves_same_topic_in_other_scope(project_repo: Path) -> None:
    project_fact = _make_fact(
        "project deployment notes use port 6380",
        topic="shared-topic",
        fact_id="01TESTFACT0000000000000017",
        scope=Scope.PROJECT,
    )
    tool_fact = _make_fact(
        "tool wrapper still uses make ship",
        topic="shared-topic",
        fact_id="01TESTFACT0000000000000018",
        scope=Scope.TOOL,
    )
    add_fact(project_repo, project_fact)
    add_fact(project_repo, tool_fact)
    rebuild_index(project_repo)

    project_path = project_repo / "facts" / "topics" / "shared-topic.md"
    content = project_path.read_text()
    project_path.write_text(content.replace("6380", "6381"))

    count = incremental_rebuild(project_repo)

    assert count == 1
    project_hits = query_index(project_repo, "6381")
    tool_hits = query_index(project_repo, "wrapper ship")
    assert any("6381" in row.text for row in project_hits)
    assert inject_candidate_ids(project_repo, '"6381"', limit=5)
    assert incremental_rebuild(project_repo) == 0
    assert any(row.fact_id == tool_fact.fact_id for row in tool_hits)


def test_inject_candidate_ids_require_current_index(project_repo: Path) -> None:
    fact = _make_fact(
        "redis runs on port 6379",
        topic="infra",
        fact_id="01TESTFACT0000000000000013",
    )
    add_fact(project_repo, fact)

    assert inject_candidate_ids(project_repo, '"redis"', limit=5) == []

    rebuild_index(project_repo)
    assert inject_candidate_ids(project_repo, '"redis"', limit=5) == [fact.fact_id]

    updated = _make_fact(
        "vector search index uses sqlite fts",
        topic="search",
        fact_id="01TESTFACT0000000000000014",
    )
    add_fact(project_repo, updated)
    assert inject_candidate_ids(project_repo, '"vector"', limit=5) == []

    incremental_rebuild(project_repo)
    assert inject_candidate_ids(project_repo, '"vector"', limit=5) == [updated.fact_id]


def test_search_sessions_finds_content(project_repo: Path) -> None:
    write_session(
        project_repo,
        {"session_id": "2026-01-15-abc123"},
        [
            {"role": "user", "content": "How do I configure postgres?"},
            {"role": "assistant", "content": "Set PGHOST and PGPORT in your env."},
        ],
    )
    write_session(
        project_repo,
        {"session_id": "2026-01-16-def456"},
        [
            {"role": "user", "content": "What is the redis port?"},
            {"role": "assistant", "content": "Redis defaults to 6379."},
        ],
    )

    results = search_sessions(project_repo, "postgres")
    assert len(results) > 0
    assert any("postgres" in r["content_snippet"].lower() for r in results)
    # Should not match redis-only session for "postgres"
    session_ids = {r["session_id"] for r in results}
    assert "2026-01-15-abc123" in session_ids


def test_search_sessions_empty_query(project_repo: Path) -> None:
    results = search_sessions(project_repo, "")
    assert results == []


def test_semantic_rerank_graceful_degradation() -> None:
    candidates = [("fact-1", 5.0), ("fact-2", 3.0), ("fact-3", 1.0)]
    result = rerank_candidates(candidates, query="test query")
    # Without sentence-transformers installed, should return unchanged
    assert result == candidates


def test_semantic_rerank_empty_candidates() -> None:
    result = rerank_candidates([], query="test")
    assert result == []


def test_semantic_rerank_no_query() -> None:
    candidates = [("fact-1", 5.0)]
    result = rerank_candidates(candidates, query="")
    assert result == candidates


def test_search_cli_raw_flag(project_repo: Path, project_dir: Path) -> None:
    write_session(
        project_repo,
        {"session_id": "2026-02-01-raw001"},
        [
            {"role": "user", "content": "Deploy the application to staging"},
            {"role": "assistant", "content": "Running deploy script now."},
        ],
    )
    runner = CliRunner()
    result = runner.invoke(main, ["search", "--cwd", str(project_dir), "--raw", "deploy"])
    assert result.exit_code == 0
    assert "2026-02-01-raw001" in result.output
    assert "deploy" in result.output.lower()


def test_search_cli_without_raw_flag(project_repo: Path, project_dir: Path) -> None:
    fact = _make_fact(
        "staging uses port 8080",
        topic="deploy",
        fact_id="01TESTFACT0000000000000020",
    )
    add_fact(project_repo, fact)
    rebuild_index(project_repo)

    runner = CliRunner()
    result = runner.invoke(main, ["search", "--cwd", str(project_dir), "staging"])
    assert result.exit_code == 0
    assert "staging uses port 8080" in result.output


def test_search_handles_hyphenated_queries(project_repo: Path, project_dir: Path) -> None:
    fact = _make_fact(
        "The Codex rollout path is first class now",
        topic="codex",
        fact_id="01TESTFACT0000000000000021",
    )
    add_fact(project_repo, fact)
    rebuild_index(project_repo)

    direct = query_index(project_repo, "first-class Codex")
    assert any(row.fact_id == fact.fact_id for row in direct)

    runner = CliRunner()
    result = runner.invoke(main, ["search", "--cwd", str(project_dir), "first-class Codex"])
    assert result.exit_code == 0, result.output
    assert "first class now" in result.output
