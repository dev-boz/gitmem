from __future__ import annotations

from datetime import UTC, datetime

import pytest

from umx.budget import strip_inline_metadata
from umx.identity import generate_fact_id
from umx.memory import format_fact_line, parse_fact_line, topic_path
from umx.models import (
    AppliesTo,
    CodeAnchor,
    ConsolidationStatus,
    Fact,
    MemoryType,
    Provenance,
    Scope,
    SourceType,
    TaskStatus,
    Verification,
)


def _fact(text: str, topic: str = "deploy", **overrides) -> Fact:
    values = {
        "fact_id": overrides.pop("fact_id", generate_fact_id()),
        "text": text,
        "scope": Scope.PROJECT,
        "topic": topic,
        "encoding_strength": 3,
        "memory_type": MemoryType.EXPLICIT_SEMANTIC,
        "verification": Verification.SELF_REPORTED,
        "source_type": SourceType.TOOL_OUTPUT,
        "confidence": 0.88,
        "source_tool": "cursor",
        "source_session": "2026-04-15-inline",
        "consolidation_status": ConsolidationStatus.FRAGILE,
        "created": datetime(2026, 4, 15, 12, 0, tzinfo=UTC),
        "provenance": Provenance(
            extracted_by="gpt-5.4",
            sessions=["2026-04-15-inline"],
        ),
    }
    values.update(overrides)
    return Fact(**values)


def _round_trip(project_repo, fact: Fact) -> Fact:
    path = topic_path(project_repo, fact.topic)
    return parse_fact_line(
        format_fact_line(fact),
        repo_dir=project_repo,
        path=path,
    )


def _assert_round_trip(original: Fact, parsed: Fact) -> None:
    assert parsed is not None
    assert parsed.fact_id == original.fact_id
    assert parsed.text == original.text
    assert parsed.topic == original.topic
    assert parsed.encoding_strength == original.encoding_strength
    assert parsed.verification == original.verification
    assert parsed.source_type == original.source_type
    assert parsed.confidence == pytest.approx(round(original.confidence, 4))
    assert parsed.source_tool == original.source_tool
    assert parsed.source_session == original.source_session
    assert parsed.corroborated_by_tools == original.corroborated_by_tools
    assert parsed.corroborated_by_facts == original.corroborated_by_facts
    assert parsed.conflicts_with == original.conflicts_with
    assert parsed.supersedes == original.supersedes
    assert parsed.superseded_by == original.superseded_by
    assert parsed.consolidation_status == original.consolidation_status
    assert parsed.task_status == original.task_status
    assert parsed.created == original.created
    assert parsed.expires_at == original.expires_at
    assert parsed.applies_to == original.applies_to
    assert parsed.code_anchor == original.code_anchor
    assert parsed.provenance.extracted_by == original.provenance.extracted_by
    assert parsed.provenance.approved_by == original.provenance.approved_by
    assert parsed.provenance.approval_tier == original.provenance.approval_tier
    assert parsed.provenance.pr == original.provenance.pr


@pytest.mark.parametrize(
    ("label", "fact"),
    [
        (
            "minimal-fragile",
            _fact("deploy steps live in docs/deploy.md"),
        ),
        (
            "stable-corroborated",
            _fact(
                "staging deploys require release manager approval",
                encoding_strength=4,
                verification=Verification.CORROBORATED,
                confidence=0.97,
                corroborated_by_tools=["aider", "codex"],
                corroborated_by_facts=["01TESTFACTCORROBORATED0001"],
                consolidation_status=ConsolidationStatus.STABLE,
                provenance=Provenance(
                    extracted_by="gpt-5.4",
                    approved_by="claude-sonnet-4.6",
                    approval_tier="l2",
                    pr="#47",
                    sessions=["2026-04-15-inline"],
                ),
            ),
        ),
        (
            "conflict-supersession-task-expiry",
            _fact(
                "database backfill waits on ops sign-off",
                conflicts_with=["01TESTFACTCONFLICT0000001"],
                supersedes="01TESTFACTSUPERSEDES00001",
                superseded_by="01TESTFACTSUPERSEDED00001",
                task_status=TaskStatus.BLOCKED,
                expires_at=datetime(2026, 4, 30, 9, 0, tzinfo=UTC),
            ),
        ),
        (
            "ground-truth-code-anchor",
            _fact(
                "deploy script pins the rollout window",
                verification=Verification.HUMAN_CONFIRMED,
                source_type=SourceType.GROUND_TRUTH_CODE,
                source_tool="codex",
                code_anchor=CodeAnchor(
                    repo="project",
                    path="scripts/deploy.py",
                    git_sha="abc123",
                    line_range=[10, 14],
                ),
                consolidation_status=ConsolidationStatus.STABLE,
            ),
        ),
        (
            "applies-to-scope",
            _fact(
                "rollout checklist differs on staging",
                applies_to=AppliesTo(env="staging", os="linux", machine="runner", branch="main"),
            ),
        ),
        (
            "arrow-escaped-value",
            _fact(
                "cursor bridge output stays local",
                source_tool="cursor-->shim",
            ),
        ),
    ],
)
def test_inline_metadata_round_trip_corpus(project_repo, label: str, fact: Fact) -> None:
    parsed = _round_trip(project_repo, fact)
    _assert_round_trip(fact, parsed)


def test_format_fact_line_uses_canonical_metadata_order(project_repo) -> None:
    fact = _fact(
        "canonical ordering stays stable",
        corroborated_by_tools=["aider"],
        corroborated_by_facts=["01TESTFACTCORROBORATED0002"],
        conflicts_with=["01TESTFACTCONFLICT0000002"],
        supersedes="01TESTFACTSUPERSEDES00002",
        superseded_by="01TESTFACTSUPERSEDED00002",
        task_status=TaskStatus.OPEN,
        expires_at=datetime(2026, 4, 20, 8, 0, tzinfo=UTC),
        applies_to=AppliesTo(env="prod", os="linux", machine="worker", branch="main"),
        code_anchor=CodeAnchor(repo="project", path="deploy.py"),
        provenance=Provenance(
            extracted_by="gpt-5.4",
            approved_by="claude-sonnet-4.6",
            approval_tier="l2",
            pr="#99",
            sessions=["2026-04-15-inline"],
        ),
    )

    line = format_fact_line(fact)
    metadata = line.split("<!-- umx:", 1)[1].rsplit("-->", 1)[0].strip()
    expected_order = [
        "id",
        "conf",
        "cort",
        "corf",
        "pr",
        "src",
        "xby",
        "aby",
        "ss",
        "st",
        "cr",
        "v",
        "cs",
        "at",
        "ca",
        "cw",
        "ex",
        "sby",
        "sup",
        "tier",
        "ts",
    ]

    positions = [metadata.index(f'"{key}":') for key in expected_order]
    assert positions == sorted(positions)


def test_strip_inline_metadata_handles_escaped_arrow_sequences(project_repo) -> None:
    fact = _fact(
        "budget stripping keeps the visible fact text",
        source_tool="cursor-->shim",
    )
    line = format_fact_line(fact)

    assert line.count("-->") == 1
    assert "\\u002d\\u002d\\u003e" in line
    assert strip_inline_metadata(line) == "- [S:3|V:sr] budget stripping keeps the visible fact text"


def test_parse_fact_line_handles_legacy_raw_arrow_metadata(project_repo) -> None:
    path = topic_path(project_repo, "deploy")
    line = (
        '- [S:3|V:sr] legacy metadata still parses '
        '<!-- umx:{"id":"01TESTFACTLEGACYARROW0001","conf":1.0,"cort":[],"corf":[],"src":"cursor-->shim",'
        '"xby":"manual","ss":"sess-legacy","st":"tool_output","cr":"2026-04-15T12:00:00Z",'
        '"v":"self-reported","cs":"fragile"} -->'
    )

    parsed = parse_fact_line(line, repo_dir=project_repo, path=path)

    assert parsed is not None
    assert parsed.source_tool == "cursor-->shim"
    assert strip_inline_metadata(line) == "- [S:3|V:sr] legacy metadata still parses"
