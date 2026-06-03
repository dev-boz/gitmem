from __future__ import annotations

from pathlib import Path

from umx.config import UMXConfig
from umx.dream import extract_llm
from umx.dream.extract_llm import (
    make_cli_extractor,
    parse_extracted_facts,
    _coerce_json_array,
)
from umx.dream.extract import _facts_from_session_payload
from umx.dream.providers import run_session_provider_extraction
from umx.models import ConsolidationStatus, SourceType


def test_coerce_json_array_handles_bare_fenced_and_wrapped() -> None:
    assert _coerce_json_array('[{"text":"a"}]') == [{"text": "a"}]
    assert _coerce_json_array('prose\n```json\n[{"text":"b"}]\n```\nmore') == [{"text": "b"}]
    assert _coerce_json_array('{"facts": [{"text":"c"}]}') == [{"text": "c"}]
    assert _coerce_json_array("not json at all") == []
    assert _coerce_json_array("") == []


def test_parse_extracted_facts_builds_fragile_candidates(project_repo: Path) -> None:
    text = (
        '[{"text": "The staging API runs on port 8443.", "topic": "Deploy", "strength": 3},'
        ' {"text": "", "topic": "x", "strength": 2},'  # empty -> skipped
        ' {"text": "The staging API runs on port 8443.", "topic": "deploy"}]'  # dup -> skipped
    )
    facts = parse_extracted_facts(text, project_repo, "sess-1", source_tool="opencode-extract")

    assert len(facts) == 1
    fact = facts[0]
    assert fact.text == "The staging API runs on port 8443."
    assert fact.topic == "deploy"  # lowercased
    assert fact.encoding_strength == 3
    assert fact.source_type == SourceType.LLM_INFERENCE
    assert fact.consolidation_status == ConsolidationStatus.FRAGILE
    assert fact.source_tool == "opencode-extract"
    assert fact.source_session == "sess-1"


def test_parse_extracted_facts_clamps_strength_and_falls_back_topic(project_repo: Path) -> None:
    facts = parse_extracted_facts(
        '[{"text": "Redis backs the cache layer."}, {"text": "Nine.", "strength": 9}]',
        project_repo,
        "sess-2",
        source_tool="opencode-extract",
    )
    assert facts[0].topic  # derived, non-empty
    assert facts[0].encoding_strength == 2  # default
    assert facts[1].encoding_strength == 3  # clamped from 9


def test_make_cli_extractor_drives_sender(monkeypatch, project_repo: Path) -> None:
    calls = {}

    class _Result:
        text = '[{"text": "The build pins node 20.", "topic": "build", "strength": 2}]'

    def fake_send(*, model, system, prompt, timeout=None):
        calls["model"] = model
        calls["prompt"] = prompt
        return _Result()

    monkeypatch.setitem(extract_llm.CLI_SENDERS, "opencode", (fake_send, lambda b=None: True))

    extractor = make_cli_extractor("opencode", "opencode/deepseek-v4-flash-free")
    events = [{"role": "assistant", "content": "We pinned node 20 in the Dockerfile."}]
    facts = extractor(project_repo, "sess-3", events, UMXConfig())

    assert calls["model"] == "opencode/deepseek-v4-flash-free"
    assert "node 20" in calls["prompt"]
    assert len(facts) == 1
    assert facts[0].text == "The build pins node 20."


def test_run_session_extraction_uses_configured_cli_provider(monkeypatch, project_repo: Path) -> None:
    class _Result:
        text = '[{"text": "Postgres listens on 5433 in dev.", "topic": "database", "strength": 3}]'

    monkeypatch.setitem(
        extract_llm.CLI_SENDERS,
        "opencode",
        (lambda **kw: _Result(), lambda b=None: True),
    )

    config = UMXConfig()
    config.dream.extract_provider = "opencode"
    config.dream.extract_model = "opencode/deepseek-v4-flash-free"

    events = [{"role": "assistant", "content": "Postgres is on port 5433 locally."}]
    result = run_session_provider_extraction(
        project_repo,
        "sess-4",
        events,
        config,
        native_extractor=lambda: _facts_from_session_payload(project_repo, "sess-4", events, config),
    )

    assert result.native_only is False
    assert any("5433" in fact.text for fact in result.facts)
    # extracted_by is stamped with the provider, not the native heuristic.
    assert result.extracted_by != "native:session-heuristic"


def test_run_session_extraction_falls_back_to_native_when_cli_unavailable(
    monkeypatch, project_repo: Path
) -> None:
    monkeypatch.setitem(
        extract_llm.CLI_SENDERS,
        "opencode",
        (lambda **kw: None, lambda b=None: False),  # not available
    )
    config = UMXConfig()
    config.dream.extract_provider = "opencode"

    events = [{"role": "assistant", "content": "The server uses port 8080 by default."}]
    sentinel = object()
    result = run_session_provider_extraction(
        project_repo,
        "sess-5",
        events,
        config,
        native_extractor=lambda: _facts_from_session_payload(project_repo, "sess-5", events, config),
    )
    assert result.native_only is True
    assert sentinel is sentinel  # native path taken without raising
