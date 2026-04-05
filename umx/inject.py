"""Injection point handlers and relevance scoring.

Facts are ordered by relevance_score descending within each injection point.
When context budget is exhausted, lowest-relevance facts are excluded.
"""

from __future__ import annotations

from pathlib import Path

from umx.budget import enforce_budget
from umx.memory import load_all_facts, load_config, load_topic_facts
from umx.models import Fact, Scope, UmxConfig
from umx.scope import (
    ScopeLayer,
    active_layers,
    resolve_scopes,
)
from umx.strength import composite_score, relevance_score


def collect_facts_for_injection(
    cwd: Path,
    tool: str | None = None,
    target_file: Path | None = None,
    keywords: list[str] | None = None,
    config: UmxConfig | None = None,
) -> list[Fact]:
    """Collect all relevant facts for injection, sorted by relevance.

    Walks the scope hierarchy and collects facts from all active layers.
    """
    if config is None:
        # Try to load from project config — check PROJECT_TEAM first
        layers = resolve_scopes(cwd, tool=tool, target_file=target_file)
        for layer in layers:
            if layer.scope == Scope.PROJECT_TEAM:
                cfg_path = layer.path / "config.yaml"
                if cfg_path.exists():
                    config = load_config(layer.path)
                    break
        if config is None:
            config = UmxConfig()

    layers = resolve_scopes(cwd, tool=tool, target_file=target_file)
    all_facts: list[Fact] = []

    for layer in active_layers(layers, include_lazy=target_file is not None):
        if not layer.path.exists():
            continue
        if layer.scope == Scope.FILE:
            # FILE layers point at a specific .md file, not a .umx/ directory
            topic = layer.path.stem  # e.g. "main.py" from "main.py.md"
            facts = load_topic_facts(layer.path, topic=topic, scope=Scope.FILE)
        else:
            facts = load_all_facts(layer.path, layer.scope)
        all_facts.extend(facts)

    # Determine target scope for relevance
    target_scope = Scope.PROJECT_TEAM
    if target_file:
        target_scope = Scope.FILE
    elif cwd != cwd.parent:
        target_scope = Scope.FOLDER

    # Score by composite relevance: combined relevance + composite signals
    scored = [
        (
            fact,
            relevance_score(
                fact,
                target_scope=target_scope,
                keywords=keywords,
                config=config,
            ),
            composite_score(fact, config=config),
        )
        for fact in all_facts
    ]
    # Sort by relevance first, then composite_score as tiebreaker
    scored.sort(key=lambda x: (-x[1], -x[2]))

    return [fact for fact, _, _ in scored]


def build_injection_block(
    facts: list[Fact],
    max_tokens: int | None = None,
    config: UmxConfig | None = None,
    header: str = "# Project Memory (umx)",
) -> str:
    """Build the injection text block from prioritised facts.

    Returns a markdown-formatted block ready for injection.
    """
    if config is None:
        config = UmxConfig()

    selected = enforce_budget(facts, max_tokens=max_tokens, config=config)

    if not selected:
        return ""

    lines = [header, ""]
    current_topic = ""
    for fact in selected:
        if fact.topic != current_topic:
            current_topic = fact.topic
            lines.append(f"## {current_topic}")
        lines.append(f"- {fact.text}")
    lines.append("")

    return "\n".join(lines)


def inject_for_tool(
    cwd: Path,
    tool: str,
    max_tokens: int | None = None,
    output_path: Path | None = None,
) -> str:
    """Generate injection content for a specific tool.

    If output_path is provided, writes the content to that file.
    Returns the injection content.
    """
    facts = collect_facts_for_injection(cwd, tool=tool)
    content = build_injection_block(facts, max_tokens=max_tokens)

    if output_path and content:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(content)

    return content
