from __future__ import annotations

import json
from pathlib import Path

import pytest

from umx.config import default_config
from umx.conventions import ConventionSet
from umx.dream.l2_review import (
    DEFAULT_NVIDIA_L2_MODEL,
    L2_NVIDIA_PROMPT_ID,
    L2_REVIEW_PROMPT_VERSION,
    REVIEW_COMMENT_MARKER,
    nvidia_l2_reviewer,
)
from umx.dream.providers import ProviderUnavailableError
from umx.governance import PRProposal
from umx.models import (
    ConsolidationStatus,
    Fact,
    MemoryType,
    Scope,
    SourceType,
    Verification,
)
from umx.providers.nvidia import NvidiaMessageResult, send_nvidia_message


FIXTURES_ROOT = Path(__file__).parent / "fixtures" / "l2_review"


def _make_fact(fact_id: str = "01TESTL2NVIDIA000000001") -> Fact:
    return Fact(
        fact_id=fact_id,
        text="fixture fact for NVIDIA review",
        scope=Scope.PROJECT,
        topic="general",
        encoding_strength=3,
        memory_type=MemoryType.EXPLICIT_SEMANTIC,
        verification=Verification.SELF_REPORTED,
        source_type=SourceType.LLM_INFERENCE,
        confidence=0.9,
        source_tool="session-extract",
        source_session="sess-nvidia",
        consolidation_status=ConsolidationStatus.STABLE,
    )


def _approve_payload() -> dict[str, object]:
    return {
        "action": "approve",
        "reason": "Clear, high-confidence local fact update with no destructive change.",
        "violations": [],
        "fact_notes": [
            {
                "fact_id": "01TESTL2NVIDIA000000001",
                "summary": "fixture fact for NVIDIA review",
                "note": "Specific, local in impact, matches the diff.",
            }
        ],
    }


class _FakeResponse:
    def __init__(self, payload: dict[str, object]):
        self._payload = payload

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


def test_send_nvidia_message_parses_success(monkeypatch) -> None:
    monkeypatch.setattr(
        "umx.providers.nvidia.urlopen",
        lambda request, timeout=30: _FakeResponse(
            {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(_approve_payload()),
                        }
                    }
                ],
                "model": DEFAULT_NVIDIA_L2_MODEL,
                "usage": {
                    "prompt_tokens": 321,
                    "completion_tokens": 87,
                    "total_tokens": 408,
                },
            }
        ),
    )

    response = send_nvidia_message(
        api_key="test",
        model=DEFAULT_NVIDIA_L2_MODEL,
        system="be brief",
        prompt="hello",
    )

    assert response.model == DEFAULT_NVIDIA_L2_MODEL
    assert response.usage == {"input_tokens": 321, "output_tokens": 87, "total_tokens": 408}
    assert json.loads(response.text)["action"] == "approve"


def test_nvidia_l2_reviewer_returns_structured_payload(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def fake_send(**kwargs) -> NvidiaMessageResult:
        seen.update(kwargs)
        return NvidiaMessageResult(
            text=json.dumps(_approve_payload()),
            model=str(kwargs["model"]),
            usage={"input_tokens": 321, "output_tokens": 87, "total_tokens": 408},
        )

    monkeypatch.setenv("NVIDIA_API_KEY", "test")
    monkeypatch.setattr("umx.providers.nvidia.send_nvidia_message", fake_send)

    pr = PRProposal(
        title="[dream/l2] nvidia fixture review",
        body=(FIXTURES_ROOT / "pr_body.md").read_text(),
        branch="dream/l1/nvidia-fixture-review",
        labels=["confidence:high", "impact:local", "type: extraction"],
        files_changed=["facts/topics/general.md"],
    )

    result = nvidia_l2_reviewer(
        pr,
        ConventionSet(topics={"general"}),
        [],
        [_make_fact()],
        default_config(),
    )

    assert seen["api_key"] == "test"
    assert seen["model"] == DEFAULT_NVIDIA_L2_MODEL
    assert result["action"] == "approve"
    assert result["model"] == DEFAULT_NVIDIA_L2_MODEL
    assert result["prompt_id"] == L2_NVIDIA_PROMPT_ID
    assert result["prompt_version"] == L2_REVIEW_PROMPT_VERSION
    assert result["usage"] == {
        "input_tokens": 321,
        "output_tokens": 87,
        "total_tokens": 408,
    }
    comment_body = str(result["comment_body"])
    assert REVIEW_COMMENT_MARKER in comment_body
    assert f"- Model: `{DEFAULT_NVIDIA_L2_MODEL}`" in comment_body


def test_nvidia_l2_reviewer_raises_without_key() -> None:
    pr = PRProposal(
        title="[dream/l2] missing nvidia key",
        body=(FIXTURES_ROOT / "pr_body.md").read_text(),
        branch="dream/l1/missing-nvidia-key",
        labels=["confidence:high", "impact:local", "type: extraction"],
        files_changed=["facts/topics/general.md"],
    )

    with pytest.raises(ProviderUnavailableError, match="NVIDIA_API_KEY"):
        nvidia_l2_reviewer(
            pr,
            ConventionSet(topics={"general"}),
            [],
            [_make_fact()],
            default_config(),
        )
