from __future__ import annotations

import json
from pathlib import Path

import pytest

from umx.config import default_config
from umx.dream import providers
from umx.dream.pipeline import DreamPipeline
from umx.governance import PRProposal
from umx.conventions import ConventionSet
from umx.identity import generate_fact_id
from umx.memory import load_all_facts
from umx.models import (
    ConsolidationStatus,
    Fact,
    MemoryType,
    Scope,
    SourceType,
    Verification,
)
from umx.sessions import write_session


L2_FIXTURES_ROOT = Path(__file__).parent / "fixtures" / "l2_review"
L2_APPROVE_FIXTURE = json.loads((L2_FIXTURES_ROOT / "anthropic_approve.json").read_text())


def _provider_fact(repo_dir: Path, session_id: str, text: str) -> Fact:
    return Fact(
        fact_id=generate_fact_id(),
        text=text,
        scope=Scope.PROJECT,
        topic="general",
        encoding_strength=2,
        memory_type=MemoryType.IMPLICIT,
        verification=Verification.SELF_REPORTED,
        source_type=SourceType.LLM_INFERENCE,
        confidence=0.6,
        source_tool="provider-extract",
        source_session=session_id,
        consolidation_status=ConsolidationStatus.FRAGILE,
        repo=repo_dir.name,
    )


def test_resolve_provider_plan_prefers_paid_then_rotation_then_local() -> None:
    cfg = default_config()
    cfg.dream.provider_rotation = ["groq", "openrouter"]
    cfg.dream.paid_provider = "anthropic"
    cfg.dream.paid_api_key = "secret"
    cfg.dream.local_model = "ollama/llama3.1"

    plan = providers.resolve_provider_plan(
        cfg,
        extractors={
            "anthropic": lambda *args: [],
            "groq": lambda *args: [],
            "openrouter": lambda *args: [],
            "local": lambda *args: [],
        },
    )

    assert plan == ["anthropic", "groq", "openrouter", "local"]


def test_run_session_provider_extraction_falls_back_to_second_provider(
    project_repo: Path,
) -> None:
    cfg = default_config()
    cfg.dream.provider_rotation = ["groq", "openrouter"]

    def groq(*_args) -> list[Fact]:
        raise providers.ProviderUnavailableError("rate limited")

    def openrouter(repo_dir: Path, session_id: str, _events: list[dict], _cfg) -> list[Fact]:
        return [_provider_fact(repo_dir, session_id, "openrouter extracted fact")]

    result = providers.run_session_provider_extraction(
        project_repo,
        "sess-rotate",
        [{"role": "assistant", "content": "provider-backed note"}],
        cfg,
        native_extractor=lambda: [_provider_fact(project_repo, "sess-rotate", "native fallback fact")],
        env={"GROQ_API_KEY": "test", "OPENROUTER_API_KEY": "test"},
        extractors={"groq": groq, "openrouter": openrouter},
    )

    assert result.native_only is False
    assert result.extracted_by == "provider:openrouter/openrouter"
    assert [attempt.status for attempt in result.attempts] == [
        providers.PROVIDER_STATUS_UNAVAILABLE,
        providers.PROVIDER_STATUS_SUCCESS,
    ]
    assert result.facts[0].provenance.extracted_by == "provider:openrouter/openrouter"
    assert "fell back before succeeding" in (result.notice or "")


def test_run_session_provider_extraction_falls_back_to_native(
    project_repo: Path,
) -> None:
    cfg = default_config()
    cfg.dream.provider_rotation = ["groq"]

    def groq(*_args) -> list[Fact]:
        raise providers.ProviderUnavailableError("quota exhausted")

    result = providers.run_session_provider_extraction(
        project_repo,
        "sess-native",
        [{"role": "assistant", "content": "native fallback note"}],
        cfg,
        native_extractor=lambda: [_provider_fact(project_repo, "sess-native", "native fallback fact")],
        env={"GROQ_API_KEY": "test"},
        extractors={"groq": groq},
    )

    assert result.native_only is True
    assert result.extracted_by == "native:session-heuristic"
    assert result.facts[0].provenance.extracted_by == "native:session-heuristic"
    assert "running native-only dream" in (result.notice or "")
    assert "groq (unavailable)" in (result.notice or "")


def test_dream_pipeline_records_native_only_fallback_notice(
    monkeypatch,
    project_dir: Path,
    project_repo: Path,
) -> None:
    cfg = default_config()
    cfg.dream.provider_rotation = ["groq"]

    def groq(*_args) -> list[Fact]:
        raise providers.ProviderUnavailableError("quota exhausted")

    monkeypatch.setattr(
        providers,
        "SESSION_PROVIDER_EXTRACTORS",
        {"groq": groq},
    )
    monkeypatch.setenv("GROQ_API_KEY", "test")
    write_session(
        project_repo,
        {"session_id": "2026-04-15-provider-native"},
        [{"role": "assistant", "content": "The app uses port 8081."}],
        auto_commit=False,
    )

    result = DreamPipeline(project_dir, config=cfg).run(force=True)

    assert result.status == "ok"
    assert "extraction: native-only" in (result.message or "")
    notice = (project_repo / "meta" / "NOTICE").read_text()
    assert "All transcript extraction providers failed or were unavailable" in notice
    memory_md = (project_repo / "meta" / "MEMORY.md").read_text()
    assert "dream_provider: native:session-heuristic" in memory_md
    assert "dream_status: partial" in memory_md


def test_dream_pipeline_marks_provider_backed_extraction(
    monkeypatch,
    project_dir: Path,
    project_repo: Path,
) -> None:
    cfg = default_config()
    cfg.dream.provider_rotation = ["groq"]

    def groq(repo_dir: Path, session_id: str, _events: list[dict], _cfg) -> list[Fact]:
        return [_provider_fact(repo_dir, session_id, "Provider says the app uses port 9090.")]

    monkeypatch.setattr(
        providers,
        "SESSION_PROVIDER_EXTRACTORS",
        {"groq": groq},
    )
    monkeypatch.setenv("GROQ_API_KEY", "test")
    write_session(
        project_repo,
        {"session_id": "2026-04-15-provider-backed"},
        [{"role": "assistant", "content": "The app uses port 9090."}],
        auto_commit=False,
    )

    result = DreamPipeline(project_dir, config=cfg).run(force=True)

    assert result.status == "ok"
    assert "extraction: provider-backed" in (result.message or "")
    memory_md = (project_repo / "meta" / "MEMORY.md").read_text()
    assert "dream_provider: provider:groq/groq" in memory_md
    assert "dream_status: partial" not in memory_md
    stored_facts = load_all_facts(project_repo, include_superseded=False)
    assert any(
        fact.provenance.extracted_by == "provider:groq/groq"
        for fact in stored_facts
    )


def test_registered_provider_without_credentials_is_skipped(
    project_repo: Path,
) -> None:
    cfg = default_config()
    cfg.dream.provider_rotation = ["groq"]
    called = 0

    def groq(*_args) -> list[Fact]:
        nonlocal called
        called += 1
        return [_provider_fact(project_repo, "sess-skip", "should not run")]

    result = providers.run_session_provider_extraction(
        project_repo,
        "sess-skip",
        [{"role": "assistant", "content": "skip due to missing key"}],
        cfg,
        native_extractor=lambda: [_provider_fact(project_repo, "sess-skip", "native fallback fact")],
        extractors={"groq": groq},
    )

    assert called == 0
    assert result.native_only is True
    assert any(
        attempt.detail == "missing provider credentials"
        for attempt in result.attempts
    )


def test_provider_zero_fact_success_still_marks_session_gathered(
    monkeypatch,
    project_dir: Path,
    project_repo: Path,
) -> None:
    cfg = default_config()
    cfg.dream.provider_rotation = ["groq"]
    calls = 0

    def groq(_repo_dir: Path, _session_id: str, _events: list[dict], _cfg) -> list[Fact]:
        nonlocal calls
        calls += 1
        return []

    monkeypatch.setattr(
        providers,
        "SESSION_PROVIDER_EXTRACTORS",
        {"groq": groq},
    )
    monkeypatch.setenv("GROQ_API_KEY", "test")
    write_session(
        project_repo,
        {"session_id": "2026-04-15-provider-empty"},
        [{"role": "assistant", "content": "nothing durable here"}],
        auto_commit=False,
    )

    first = DreamPipeline(project_dir, config=cfg).run(force=True)
    second = DreamPipeline(project_dir, config=cfg).run(force=True)

    assert first.status == "ok"
    assert second.status == "ok"
    assert calls == 1
    state = (project_repo / "meta" / "dream-state.json").read_text()
    assert "2026-04-15-provider-empty" in state
    notice_path = project_repo / "meta" / "NOTICE"
    if notice_path.exists():
        assert "No transcript extraction provider configured" not in notice_path.read_text()


def test_run_l2_review_with_provider_reviewer(project_repo: Path) -> None:
    cfg = default_config()
    cfg.dream.provider_rotation = ["groq"]
    pr = PRProposal(
        title="[dream/l2] test",
        body="",
        branch="dream/l1/provider-review",
        labels=["confidence:high", "impact:local", "type: extraction"],
        files_changed=["facts/topics/general.md"],
    )
    fact = _provider_fact(project_repo, "sess-review", "review this fact")

    def groq_review(_pr, _conventions, existing_facts, new_facts, _cfg) -> dict[str, object]:
        assert existing_facts
        assert new_facts
        return {
            "action": "approve",
            "reason": "provider review approved",
            "violations": [],
        }

    result = providers.run_l2_review_with_providers(
        pr,
        ConventionSet(),
        [fact],
        [fact],
        cfg,
        fallback_reviewer=lambda *_args: {
            "action": "reject",
            "reason": "fallback should not run",
            "violations": [],
        },
        env={"GROQ_API_KEY": "test"},
        reviewers={"groq": groq_review},
    )

    assert result.action == "approve"
    assert result.reason == "provider review approved"
    assert result.reviewed_by == "provider:groq/groq"
    assert result.model_backed is True


def test_run_l2_review_prefers_anthropic_when_key_present(project_repo: Path) -> None:
    cfg = default_config()
    cfg.dream.provider_rotation = ["groq"]
    pr = PRProposal(
        title="[dream/l2] test",
        body="",
        branch="dream/l1/provider-review",
        labels=["confidence:high", "impact:local", "type: extraction"],
        files_changed=["facts/topics/general.md"],
    )
    fact = _provider_fact(project_repo, "sess-review", "review this fact")
    calls: list[str] = []

    def anthropic_review(_pr, _conventions, _existing_facts, _new_facts, _cfg) -> dict[str, object]:
        calls.append("anthropic")
        return {
            "action": "approve",
            "reason": "anthropic review approved",
            "violations": [],
        }

    def groq_review(_pr, _conventions, _existing_facts, _new_facts, _cfg) -> dict[str, object]:
        calls.append("groq")
        return {
            "action": "reject",
            "reason": "groq should not run first",
            "violations": [],
        }

    result = providers.run_l2_review_with_providers(
        pr,
        ConventionSet(),
        [fact],
        [fact],
        cfg,
        fallback_reviewer=lambda *_args: {
            "action": "reject",
            "reason": "fallback should not run",
            "violations": [],
        },
        env={"ANTHROPIC_API_KEY": "test", "GROQ_API_KEY": "test"},
        reviewers={"anthropic": anthropic_review, "groq": groq_review},
    )

    assert calls == ["anthropic"]
    assert result.reviewed_by == "provider:anthropic/anthropic"
    assert result.model_backed is True


def test_run_l2_review_respects_explicit_provider_override(project_repo: Path) -> None:
    cfg = default_config()
    cfg.dream.provider_rotation = ["groq"]
    pr = PRProposal(
        title="[dream/l2] test",
        body="",
        branch="dream/l1/provider-review",
        labels=["confidence:high", "impact:local", "type: extraction"],
        files_changed=["facts/topics/general.md"],
    )
    fact = _provider_fact(project_repo, "sess-review", "review this fact")
    calls: list[str] = []

    def anthropic_review(_pr, _conventions, _existing_facts, _new_facts, _cfg) -> dict[str, object]:
        calls.append("anthropic")
        return {
            "action": "reject",
            "reason": "anthropic should not run first",
            "violations": [],
        }

    def claude_cli_review(_pr, _conventions, _existing_facts, _new_facts, _cfg) -> dict[str, object]:
        calls.append("claude-cli")
        return {
            "action": "approve",
            "reason": "claude cli review approved",
            "violations": [],
        }

    result = providers.run_l2_review_with_providers(
        pr,
        ConventionSet(),
        [fact],
        [fact],
        cfg,
        fallback_reviewer=lambda *_args: {
            "action": "reject",
            "reason": "fallback should not run",
            "violations": [],
        },
        env={"ANTHROPIC_API_KEY": "test", "UMX_L2_REVIEW_PROVIDER": "claude-cli"},
        reviewers={"anthropic": anthropic_review, "claude-cli": claude_cli_review},
    )

    assert calls == ["claude-cli"]
    assert result.reviewed_by == "provider:claude-cli/claude-cli"
    assert result.model_backed is True


def test_run_l2_review_requires_anthropic_key_on_github_actions(project_repo: Path) -> None:
    cfg = default_config()
    cfg.dream.paid_provider = "anthropic"
    pr = PRProposal(
        title="[dream/l2] test",
        body="",
        branch="dream/l1/provider-review",
        labels=["confidence:high", "impact:local", "type: extraction"],
        files_changed=["facts/topics/general.md"],
    )
    fact = _provider_fact(project_repo, "sess-review", "review this fact")

    with pytest.raises(providers.ProviderUnavailableError, match="ANTHROPIC_API_KEY"):
        providers.run_l2_review_with_providers(
            pr,
            ConventionSet(),
            [fact],
            [fact],
            cfg,
            fallback_reviewer=lambda *_args: {
                "action": "reject",
                "reason": "fallback should not run",
                "violations": [],
            },
            env={},
            reviewers={
                "anthropic": lambda *_args: {
                    "action": "approve",
                    "reason": "should not run without key",
                    "violations": [],
                }
            },
        )


def test_run_l2_review_with_builtin_anthropic_reviewer_uses_env_mapping(
    project_repo: Path,
    monkeypatch,
) -> None:
    from umx.providers.anthropic import AnthropicMessageResult

    cfg = default_config()
    pr = PRProposal(
        title="[dream/l2] test",
        body=(L2_FIXTURES_ROOT / "pr_body.md").read_text(),
        branch="dream/l1/provider-review",
        labels=["confidence:high", "impact:local", "type: extraction"],
        files_changed=["facts/topics/general.md"],
    )
    fact = Fact(
        fact_id="01TESTL2FIXTURE0000000001",
        text="fixture fact for Anthropic review",
        scope=Scope.PROJECT,
        topic="general",
        encoding_strength=3,
        memory_type=MemoryType.EXPLICIT_SEMANTIC,
        verification=Verification.SELF_REPORTED,
        source_type=SourceType.LLM_INFERENCE,
        confidence=0.9,
        source_tool="provider-review",
        source_session="sess-anthropic",
        consolidation_status=ConsolidationStatus.STABLE,
    )
    monkeypatch.setattr(
        "umx.providers.anthropic.send_anthropic_message",
        lambda **kwargs: AnthropicMessageResult(
            text=str(L2_APPROVE_FIXTURE["text"]),
            model=str(L2_APPROVE_FIXTURE["model"]),
            usage=dict(L2_APPROVE_FIXTURE["usage"]),
        ),
    )

    result = providers.run_l2_review_with_providers(
        pr,
        ConventionSet(topics={"general"}),
        [],
        [fact],
        cfg,
        fallback_reviewer=lambda *_args: {
            "action": "reject",
            "reason": "fallback should not run",
            "violations": [],
        },
        env={"ANTHROPIC_API_KEY": "test"},
    )

    assert result.action == "approve"
    assert result.reviewed_by == "provider:anthropic/anthropic"
    assert result.model == "claude-opus-4-7"
    assert result.usage == {"input_tokens": 321, "output_tokens": 87, "total_tokens": 408}
