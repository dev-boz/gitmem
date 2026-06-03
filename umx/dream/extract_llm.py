"""Provider-agnostic, headless CLI-backed L1 dream extraction.

The native heuristic extractor (`_facts_from_session_payload`) is always
available, but real LLM extraction was never wired into the dream pipeline.
This module drives any of the existing headless CLI providers
(opencode / gemini / claude / codex) to turn a session transcript into
structured facts, using the CLI's own authentication — no API key required.

Selection is config-driven (`dream.extract_provider` + `dream.extract_model`),
so swapping providers (opencode today, antigravity tomorrow) is a config change.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable

from umx.config import UMXConfig
from umx.identity import generate_fact_id
from umx.models import (
    ConsolidationStatus,
    Fact,
    MemoryType,
    Provenance,
    Scope,
    SourceType,
    Verification,
)
from umx.providers.claude_cli import claude_cli_available, send_claude_cli_message
from umx.providers.codex_cli import codex_cli_available, send_codex_cli_message
from umx.providers.gemini_cli import gemini_cli_available, send_gemini_cli_message
from umx.providers.opencode_cli import opencode_cli_available, send_opencode_cli_message
from umx.redaction import redact_candidate_fact_text

# provider name -> (send_message, availability_check). All senders share the
# signature (model, system, prompt, binary, timeout, extra_args, runner) and
# return an object exposing `.text`.
CLI_SENDERS: dict[str, tuple[Callable[..., object], Callable[..., bool]]] = {
    "opencode": (send_opencode_cli_message, opencode_cli_available),
    "gemini-cli": (send_gemini_cli_message, gemini_cli_available),
    "claude-cli": (send_claude_cli_message, claude_cli_available),
    "codex-cli": (send_codex_cli_message, codex_cli_available),
}

# Free / cheap defaults used when `dream.extract_model` is unset.
DEFAULT_CLI_MODELS: dict[str, str] = {
    "opencode": "opencode/deepseek-v4-flash-free",
    "gemini-cli": "gemini-2.5-flash",
    "claude-cli": "claude-haiku-4-5",
    "codex-cli": "gpt-5.2-codex",
}

_MAX_TRANSCRIPT_CHARS = 24_000
_MAX_FACTS = 25
_JSON_ARRAY_RE = re.compile(r"\[.*\]", re.DOTALL)
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(?P<payload>[\[{].*[\]}])\s*```", re.DOTALL)

_EXTRACTION_SYSTEM_PROMPT = (
    "You extract durable, reusable facts from an AI coding assistant session "
    "transcript for a long-term memory system.\n\n"
    "Output ONLY a JSON array. Each element is an object: "
    '{"text": string, "topic": string, "strength": integer 1-3}.\n'
    "- text: one self-contained fact in present tense "
    '(e.g. "The staging API runs on port 8443.").\n'
    '- topic: a short lowercase slug (e.g. "deploy", "database", "auth").\n'
    "- strength: 1 = incidental mention, 2 = clearly stated by the assistant, "
    "3 = verified against code or command output.\n\n"
    "Only include facts useful to a FUTURE session: configuration, conventions, "
    "decisions, gotchas, commands, file/system facts, architecture.\n"
    "Exclude chit-chat, transient task status, apologies, secrets, and anything "
    "not durable. Maximum 25 facts. If nothing durable, output []."
)


def is_cli_provider(provider: str | None) -> bool:
    return provider in CLI_SENDERS


def cli_extractor_available(provider: str, *, binary: str | None = None) -> bool:
    entry = CLI_SENDERS.get(provider)
    if entry is None:
        return False
    _, available = entry
    try:
        return bool(available(binary))
    except Exception:
        return False


def resolve_extract_model(provider: str, config: UMXConfig) -> str | None:
    return config.dream.extract_model or DEFAULT_CLI_MODELS.get(provider)


def _transcript_from_events(events: list[dict]) -> str:
    lines: list[str] = []
    for event in events:
        if not isinstance(event, dict) or "_meta" in event:
            continue
        role = event.get("role")
        content = event.get("content", "")
        if not isinstance(content, str) or not content.strip():
            continue
        lines.append(f"{role or 'unknown'}: {content.strip()}")
    transcript = "\n".join(lines)
    if len(transcript) > _MAX_TRANSCRIPT_CHARS:
        # Keep the tail — later turns usually hold conclusions/decisions.
        transcript = transcript[-_MAX_TRANSCRIPT_CHARS:]
    return transcript


def _extraction_user_prompt(transcript: str) -> str:
    return (
        "Extract durable facts from this session transcript. "
        "Return only the JSON array.\n\n"
        "<transcript>\n"
        f"{transcript}\n"
        "</transcript>"
    )


def _coerce_json_array(text: str) -> list:
    if not text or not text.strip():
        return []
    candidates: list[str] = [text.strip()]
    fence = _JSON_FENCE_RE.search(text)
    if fence:
        candidates.insert(0, fence.group("payload"))
    array = _JSON_ARRAY_RE.search(text)
    if array:
        candidates.append(array.group(0))
    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            for key in ("facts", "items", "results"):
                inner = payload.get(key)
                if isinstance(inner, list):
                    return inner
    return []


def _fallback_topic(text: str) -> str:
    for token in re.split(r"\W+", text.lower()):
        if len(token) > 2:
            return token
    return "general"


def parse_extracted_facts(
    text: str,
    repo_dir: Path,
    session_id: str,
    *,
    source_tool: str,
    config: UMXConfig | None = None,
) -> list[Fact]:
    """Parse an LLM extraction response into fragile S:1-3 candidate facts."""
    facts: list[Fact] = []
    seen: set[str] = set()
    for item in _coerce_json_array(text)[:_MAX_FACTS]:
        if not isinstance(item, dict):
            continue
        raw_text = item.get("text")
        if not isinstance(raw_text, str) or not raw_text.strip():
            continue
        redacted = redact_candidate_fact_text(raw_text.strip(), config)
        if not redacted:
            continue
        key = redacted.lower()
        if key in seen:
            continue
        seen.add(key)
        try:
            strength = int(item.get("strength", 2))
        except (TypeError, ValueError):
            strength = 2
        strength = max(1, min(3, strength))
        topic = item.get("topic")
        if not isinstance(topic, str) or not topic.strip():
            topic = _fallback_topic(redacted)
        facts.append(
            Fact(
                fact_id=generate_fact_id(),
                text=redacted,
                scope=Scope.PROJECT,
                topic=topic.strip().lower(),
                encoding_strength=strength,
                memory_type=MemoryType.IMPLICIT,
                verification=Verification.SELF_REPORTED,
                source_type=SourceType.LLM_INFERENCE,
                confidence=0.6,
                source_tool=source_tool,
                source_session=session_id,
                consolidation_status=ConsolidationStatus.FRAGILE,
                provenance=Provenance(extracted_by=source_tool, sessions=[session_id]),
                repo=repo_dir.name,
            )
        )
    return facts


def make_cli_extractor(
    provider: str,
    model: str,
    *,
    timeout: int | None = None,
) -> Callable[[Path, str, list[dict], UMXConfig], list[Fact]]:
    """Build a SessionProviderExtractor that drives a headless CLI provider."""
    if provider not in CLI_SENDERS:
        raise ValueError(f"unknown CLI extraction provider: {provider}")
    send, _ = CLI_SENDERS[provider]
    source_tool = f"{provider}-extract"

    def _extractor(
        repo_dir: Path,
        session_id: str,
        events: list[dict],
        config: UMXConfig,
    ) -> list[Fact]:
        transcript = _transcript_from_events(events)
        if not transcript.strip():
            return []
        result = send(
            model=model,
            system=_EXTRACTION_SYSTEM_PROMPT,
            prompt=_extraction_user_prompt(transcript),
            timeout=timeout,
        )
        return parse_extracted_facts(
            getattr(result, "text", "") or "",
            repo_dir,
            session_id,
            source_tool=source_tool,
            config=config,
        )

    return _extractor
