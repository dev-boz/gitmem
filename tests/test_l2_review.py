from __future__ import annotations

import json
from pathlib import Path

from umx.config import default_config
from umx.conventions import ConventionSet
from umx.dream.l2_review import (
    L2_REVIEW_PROMPT_ID,
    L2_REVIEW_PROMPT_VERSION,
    REVIEW_COMMENT_MARKER,
    anthropic_l2_reviewer,
    build_l2_review_context,
)
from umx.governance import PRProposal
from umx.models import (
    ConsolidationStatus,
    Fact,
    MemoryType,
    Provenance,
    Scope,
    SourceType,
    Verification,
)
from umx.providers.anthropic import AnthropicMessageResult
from umx.scope import ensure_repo_structure
from umx.sessions import write_session


FIXTURES_ROOT = Path(__file__).parent / "fixtures" / "l2_review"
ANTHROPIC_APPROVE_FIXTURE = json.loads((FIXTURES_ROOT / "anthropic_approve.json").read_text())


def _make_fact() -> Fact:
    return Fact(
        fact_id="01TESTL2FIXTURE0000000001",
        text="fixture fact for Anthropic review",
        scope=Scope.PROJECT,
        topic="general",
        encoding_strength=3,
        memory_type=MemoryType.EXPLICIT_SEMANTIC,
        verification=Verification.SELF_REPORTED,
        source_type=SourceType.LLM_INFERENCE,
        confidence=0.9,
        source_tool="session-extract",
        source_session="sess-fixture",
        consolidation_status=ConsolidationStatus.STABLE,
    )


def test_anthropic_l2_reviewer_parses_fixture_and_renders_comment(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(
        "umx.providers.anthropic.send_anthropic_message",
        lambda **kwargs: AnthropicMessageResult(
            text=str(ANTHROPIC_APPROVE_FIXTURE["text"]),
            model=str(ANTHROPIC_APPROVE_FIXTURE["model"]),
            usage=dict(ANTHROPIC_APPROVE_FIXTURE["usage"]),
        ),
    )
    pr = PRProposal(
        title="[dream/l2] fixture review",
        body=(FIXTURES_ROOT / "pr_body.md").read_text(),
        branch="dream/l1/fixture-review",
        labels=["confidence:high", "impact:local", "type: extraction"],
        files_changed=["facts/topics/general.md"],
    )

    result = anthropic_l2_reviewer(
        pr,
        ConventionSet(topics={"general"}),
        [],
        [_make_fact()],
        default_config(),
    )

    assert result["action"] == "approve"
    assert result["reason"] == "Clear, high-confidence local fact update with no destructive change."
    assert result["model"] == "claude-opus-4-7"
    assert result["prompt_id"] == L2_REVIEW_PROMPT_ID
    assert result["prompt_version"] == L2_REVIEW_PROMPT_VERSION
    assert result["usage"] == {"input_tokens": 321, "output_tokens": 87, "total_tokens": 408}
    assert result["fact_notes"] == [
        {
            "fact_id": "01TESTL2FIXTURE0000000001",
            "summary": "fixture fact for Anthropic review",
            "note": "The proposed fact is specific, local in impact, and matches the diff.",
        }
    ]
    comment_body = str(result["comment_body"])
    assert REVIEW_COMMENT_MARKER in comment_body
    assert "- Model: `claude-opus-4-7`" in comment_body
    assert "- Tokens: in 321, out 87, total 408" in comment_body


def test_build_l2_review_context_includes_source_sessions_and_manifest(tmp_path: Path) -> None:
    repo = tmp_path / "memory"
    ensure_repo_structure(repo)
    write_session(
        repo,
        {"session_id": "2026-01-15-source", "tool": "copilot"},
        [
            {
                "role": "assistant",
                "content": "Deploys require smoke checks before release.",
            },
        ],
        auto_commit=False,
    )
    (repo / "meta" / "manifest.json").write_text(
        json.dumps(
            {
                "topics": {
                    "deploy": {
                        "avg_strength": 3.5,
                        "fact_count": 2,
                        "fragile_count": 1,
                        "last_updated": "2026-01-15",
                    },
                },
                "uncertainty_hotspots": [
                    {"topic": "deploy", "reason": "1 of 2 facts still fragile"},
                ],
                "knowledge_gaps": [
                    {"topic": "release", "reason": "missing rollback coverage"},
                ],
                "last_rebuilt": "2026-01-15T00:00:00Z",
            },
            sort_keys=True,
        )
        + "\n"
    )
    fact = _make_fact().clone(
        topic="deploy",
        text="Deploys require smoke checks before release.",
        source_session="2026-01-15-source",
        provenance=Provenance(
            extracted_by="native:session-heuristic",
            sessions=["2026-01-15-source"],
        ),
    )
    pr = PRProposal(
        title="[dream/l2] context review",
        body="",
        branch="dream/l1/context-review",
        labels=["confidence:high", "impact:local", "type: extraction"],
        files_changed=["facts/topics/deploy.md"],
    )

    context = build_l2_review_context(
        pr,
        ConventionSet(topics={"deploy"}),
        [],
        [fact],
        repo_dir=repo,
    )

    assert context["missing_source_sessions"] == []
    assert context["source_sessions"][0]["session_id"] == "2026-01-15-source"
    assert "smoke checks before release" in context["source_sessions"][0]["excerpt"]
    assert context["manifest"]["topics"]["deploy"]["fact_count"] == 2
    assert context["manifest"]["uncertainty_hotspots"][0]["topic"] == "deploy"
    assert context["manifest"]["knowledge_gaps"][0]["topic"] == "release"
