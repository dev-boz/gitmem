"""LLM-based fact extraction from session transcripts.

Extracts atomic facts from session logs/transcripts. Facts must be
atomic — never merged into narratives.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from umx.dream.providers import LLMClient
from umx.models import Fact, MemoryType, Scope, UmxConfig

EXTRACTION_PROMPT = """\
You are a fact extraction engine for a developer memory system.

RULES:
1. Extract ATOMIC facts only. Each fact must be a single, independent statement.
2. Do NOT merge multiple facts into one statement.
3. Do NOT add opinions, interpretations, or narrative.
4. Facts should be about: project configuration, conventions, architecture decisions, \
environment setup, known bugs, workarounds, tool preferences.
5. Ignore: routine code changes, debugging steps that led nowhere, general programming knowledge.
6. For each fact, assign a confidence score (0.0-1.0) based on how certain you are \
the statement is accurate from the context.

OUTPUT FORMAT (JSON array):
[
  {"text": "fact text here", "confidence": 0.95, "tags": ["tag1", "tag2"]},
  {"text": "another fact", "confidence": 0.8, "tags": ["tag"]}
]

If no facts are worth extracting, return an empty array: []
"""


def extract_facts_from_text(
    text: str,
    source_tool: str,
    topic: str,
    scope: Scope = Scope.PROJECT_TEAM,
    encoding_strength: int = 3,
    llm_client: LLMClient | None = None,
    config: UmxConfig | None = None,
) -> list[Fact]:
    """Extract atomic facts from text using LLM.

    Falls back to simple heuristic extraction if no LLM is available.

    Args:
        text: Session transcript or log content.
        source_tool: Name of the source tool.
        topic: Topic to assign extracted facts to.
        scope: Scope for the facts.
        encoding_strength: Base encoding strength (2 or 3).
        llm_client: Optional LLM client for AI extraction.
        config: Configuration.

    Returns:
        List of extracted Facts.
    """
    if config is None:
        config = UmxConfig()

    if llm_client and llm_client.is_available():
        return _llm_extract(
            text, source_tool, topic, scope, encoding_strength, llm_client
        )

    return _heuristic_extract(text, source_tool, topic, scope, encoding_strength)


def _llm_extract(
    text: str,
    source_tool: str,
    topic: str,
    scope: Scope,
    encoding_strength: int,
    llm_client: LLMClient,
) -> list[Fact]:
    """Extract facts using LLM."""
    # Truncate very long texts
    max_input = 8000
    if len(text) > max_input:
        text = text[:max_input] + "\n...[truncated]"

    messages = [
        {"role": "system", "content": EXTRACTION_PROMPT},
        {"role": "user", "content": f"Extract facts from this session content:\n\n{text}"},
    ]

    response = llm_client.chat_completion(messages, temperature=0.2)
    if not response:
        return _heuristic_extract(text, source_tool, topic, scope, encoding_strength)

    return _parse_llm_response(response, source_tool, topic, scope, encoding_strength)


def _parse_llm_response(
    response: str,
    source_tool: str,
    topic: str,
    scope: Scope,
    encoding_strength: int,
) -> list[Fact]:
    """Parse LLM extraction response into Facts."""
    # Extract JSON from response (handle markdown code blocks)
    json_match = re.search(r"\[.*\]", response, re.DOTALL)
    if not json_match:
        return []

    try:
        items = json.loads(json_match.group())
    except json.JSONDecodeError:
        return []

    facts: list[Fact] = []
    now = datetime.now(timezone.utc)

    for item in items:
        if not isinstance(item, dict) or "text" not in item:
            continue
        text = item["text"].strip()
        if not text:
            continue

        facts.append(Fact(
            id=Fact.generate_id(),
            text=text,
            scope=scope,
            topic=topic,
            encoding_strength=encoding_strength,
            memory_type=(
                MemoryType.EXPLICIT_EPISODIC
                if encoding_strength == 3
                else MemoryType.IMPLICIT
            ),
            confidence=float(item.get("confidence", 0.7)),
            tags=item.get("tags", []),
            source_tool=source_tool,
            source_session=now.isoformat(),
            created=now,
        ))

    return facts


def _heuristic_extract(
    text: str,
    source_tool: str,
    topic: str,
    scope: Scope,
    encoding_strength: int,
) -> list[Fact]:
    """Simple heuristic extraction when no LLM is available.

    Looks for common patterns like:
    - "uses X", "runs on port X", "configured to X"
    - Key-value patterns in configs
    - Explicit statements about the project
    """
    facts: list[Fact] = []
    now = datetime.now(timezone.utc)

    patterns = [
        # Port configurations
        (r"(?:runs?|listening|serving)\s+on\s+port\s+(\d+)", 0.85),
        # Database mentions
        (r"(?:using|uses|switched to|migrated to)\s+(postgres(?:ql)?|mysql|sqlite|mongodb|redis)", 0.8),
        # Test commands
        (r"(?:run|use)\s+(pytest|jest|mocha|vitest|cargo test)\s*(.*?)(?:\.|$)", 0.75),
        # Environment settings
        (r"(?:set|export)\s+(\w+)=(\S+)", 0.7),
        # Ignore/skip patterns
        (r"(?:ignore|skip|disable)\s+(.+?)(?:\s+in\s+(dev|prod|test))?(?:\.|$)", 0.7),
    ]

    for pattern, confidence in patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE | re.MULTILINE):
            fact_text = match.group(0).strip()
            if len(fact_text) < 10 or len(fact_text) > 200:
                continue

            facts.append(Fact(
                id=Fact.generate_id(),
                text=fact_text,
                scope=scope,
                topic=topic,
                encoding_strength=max(1, encoding_strength - 1),
                memory_type=MemoryType.IMPLICIT,
                confidence=confidence,
                source_tool=source_tool,
                source_session=now.isoformat(),
                created=now,
            ))

    return facts
