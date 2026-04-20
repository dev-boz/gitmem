from __future__ import annotations

from pathlib import Path

import pytest

from umx.dream.pr_render import (
    FACT_DELTA_BLOCK_VERSION,
    LEGACY_PR_BODY_MARKER,
    FactDeltaBlock,
    FactDeltaEntry,
    GovernancePRBodyError,
    assert_governance_pr_body,
    build_fact_delta_for_tombstones,
    build_fact_delta_from_facts,
    render_governance_pr_body,
    touched_fact_ids_from_fact_delta,
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
    fact_id: str,
    text: str,
    *,
    topic: str = "general",
    superseded_by: str | None = None,
    file_path: Path | None = None,
) -> Fact:
    return Fact(
        fact_id=fact_id,
        text=text,
        scope=Scope.PROJECT,
        topic=topic,
        encoding_strength=3,
        memory_type=MemoryType.EXPLICIT_SEMANTIC,
        verification=Verification.SELF_REPORTED,
        source_type=SourceType.TOOL_OUTPUT,
        source_tool="codex",
        source_session="sess-pr-render",
        consolidation_status=ConsolidationStatus.STABLE,
        superseded_by=superseded_by,
        file_path=file_path,
    )


def _legacy_dream_l1_body() -> str:
    return "\n".join([
        "## Dream L1 Extraction",
        "",
        "**Date:** 2026-04-17",
        "**Source sessions:** sess-legacy",
        "**Facts extracted:** 1",
        "**Encoding strength range:** 3-3",
        "",
        "### Facts",
        "",
        "- `01TESTLEGACYDREAM00000001` [general] (S:3, C:1.0) postgres runs on 5433 in dev",
        "",
        "### Provenance",
        "",
        "- Extracted by: dream/l1",
        "- Approval tier: L1",
        "",
        LEGACY_PR_BODY_MARKER,
    ])


def _legacy_promotion_body() -> str:
    return "\n".join([
        "## Cross-project promotion proposal preview",
        "",
        "This is a read-only preview for promoting a repeated project fact into user memory.",
        "No branch, commit, push, or pull request has been created.",
        "",
        "### Candidate",
        "",
        "- Key: `shared incident runbook lives in docs/incidents`",
        "- Text: Shared incident runbook lives in docs/incidents",
        "- Seen in 3 project repos: alpha, beta, gamma",
        "- Target repo: `user memory repo`",
        "- Target topic: `ops`",
        "- Target file: `facts/topics/ops.md`",
        "",
        "### Evidence",
        "",
        "- `alpha` / `FACT1` [topic: `ops`; strength: 3; created: 2026-04-17T00:00:00Z]",
        "",
        LEGACY_PR_BODY_MARKER,
    ])


def test_render_governance_pr_body_round_trips_fact_delta_block(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    fact = _make_fact(
        "01TESTPRRENDER000000000001",
        "postgres runs on 5433 in dev",
        topic="devenv",
        file_path=repo_dir / "facts" / "topics" / "devenv.md",
    )

    body = render_governance_pr_body(
        heading="Dream L1 Extraction",
        summary_lines=["- deterministic body"],
        fact_delta=build_fact_delta_from_facts([fact], repo_dir),
    )

    payload = assert_governance_pr_body(body)
    assert payload is not None
    assert payload["version"] == FACT_DELTA_BLOCK_VERSION
    assert payload["added"][0]["fact_id"] == fact.fact_id
    assert payload["added"][0]["path"] == "facts/topics/devenv.md"
    assert payload["superseded"] == []


def test_build_fact_delta_from_facts_classifies_superseded_entries(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    fact = _make_fact(
        "01TESTPRRENDER000000000002",
        "legacy deploy path is obsolete",
        topic="deploy",
        superseded_by="01TESTPRRENDER000000000003",
        file_path=repo_dir / "facts" / "topics" / "deploy.md",
    )

    block = build_fact_delta_from_facts([fact], repo_dir)

    assert block.added == ()
    assert block.superseded[0].fact_id == fact.fact_id
    assert block.superseded[0].superseded_by == "01TESTPRRENDER000000000003"


def test_touched_fact_ids_from_fact_delta_uses_target_fact_ids_only() -> None:
    payload = {
        "version": FACT_DELTA_BLOCK_VERSION,
        "added": [
            {
                "fact_id": "FACT-ADD-1",
                "topic": "ops",
                "path": "facts/topics/ops.md",
                "summary": "added",
                "source_fact_ids": ["SOURCE-1", "SOURCE-2"],
            }
        ],
        "modified": [],
        "superseded": [
            {
                "fact_id": "FACT-SUP-1",
                "topic": "ops",
                "path": "facts/topics/ops.md",
                "summary": "superseded",
                "superseded_by": "FACT-SUP-2",
            }
        ],
        "tombstoned": [
            {
                "fact_id": "FACT-TOMBSTONE-1",
                "topic": "ops",
                "path": "facts/topics/ops.md",
                "summary": "tombstoned fact",
            }
        ],
    }

    assert touched_fact_ids_from_fact_delta(payload) == frozenset(
        {"FACT-ADD-1", "FACT-SUP-1", "FACT-TOMBSTONE-1"}
    )


def test_build_fact_delta_for_tombstones_marks_entries_as_tombstoned(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    fact = _make_fact(
        "01TESTPRRENDER000000000004",
        "legacy deploy path is obsolete",
        topic="deploy",
        file_path=repo_dir / "facts" / "topics" / "deploy.md",
    )

    block = build_fact_delta_for_tombstones([fact], repo_dir)

    assert block.added == ()
    assert block.tombstoned[0].fact_id == fact.fact_id
    assert block.tombstoned[0].path == "facts/topics/deploy.md"


def test_assert_governance_pr_body_rejects_missing_block_without_legacy_marker() -> None:
    with pytest.raises(GovernancePRBodyError, match="required fact-delta block"):
        assert_governance_pr_body("plain body", allow_legacy=True)


def test_assert_governance_pr_body_allows_legacy_dream_backfill() -> None:
    assert assert_governance_pr_body(_legacy_dream_l1_body(), allow_legacy=True) is None


def test_assert_governance_pr_body_allows_legacy_promotion_backfill() -> None:
    assert assert_governance_pr_body(_legacy_promotion_body(), allow_legacy=True) is None


def test_assert_governance_pr_body_rejects_unrecognized_legacy_marker() -> None:
    with pytest.raises(GovernancePRBodyError, match="recognized pre-fact-delta template"):
        assert_governance_pr_body(f"plain body\n\n{LEGACY_PR_BODY_MARKER}", allow_legacy=True)


def test_assert_governance_pr_body_rejects_unknown_version() -> None:
    body = render_governance_pr_body(
        heading="Dream L1 Extraction",
        summary_lines=["- invalid version"],
        fact_delta=FactDeltaBlock(added=(FactDeltaEntry(topic="devenv", path="facts/topics/devenv.md", summary="postgres"),)),
    ).replace(f'"version": {FACT_DELTA_BLOCK_VERSION}', '"version": 99')

    with pytest.raises(GovernancePRBodyError, match="unsupported fact-delta block version"):
        assert_governance_pr_body(body)


def test_assert_governance_pr_body_rejects_malformed_json() -> None:
    body = render_governance_pr_body(
        heading="Dream L1 Extraction",
        summary_lines=["- malformed"],
        fact_delta=FactDeltaBlock(added=(FactDeltaEntry(topic="devenv", path="facts/topics/devenv.md", summary="postgres"),)),
    ).replace('"summary": "postgres"', '"summary":')

    with pytest.raises(GovernancePRBodyError, match="malformed fact-delta JSON"):
        assert_governance_pr_body(body)


def test_assert_governance_pr_body_rejects_invalid_entry_shape() -> None:
    body = render_governance_pr_body(
        heading="Dream L1 Extraction",
        summary_lines=["- invalid entry"],
        fact_delta=FactDeltaBlock(added=(FactDeltaEntry(topic="devenv", path="facts/topics/devenv.md", summary="postgres"),)),
    ).replace('"topic": "devenv"', '"topic": ""')

    with pytest.raises(GovernancePRBodyError, match="missing `topic`"):
        assert_governance_pr_body(body)


def test_assert_governance_pr_body_requires_fact_id_for_tombstoned_entries() -> None:
    body = render_governance_pr_body(
        heading="Governed fact tombstone proposal",
        summary_lines=["- tombstone"],
        fact_delta=FactDeltaBlock(
            tombstoned=(
                FactDeltaEntry(
                    topic="ops",
                    path="facts/topics/ops.md",
                    summary="remove obsolete fact",
                ),
            ),
        ),
    )

    with pytest.raises(GovernancePRBodyError, match="must include `fact_id`"):
        assert_governance_pr_body(body)
