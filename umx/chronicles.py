from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from umx.budget import estimate_tokens
from umx.models import Fact, Scope


_NUMBER_RE = re.compile(r"\b\d+(?:[._-]\d+)*%?\b")
_TEMPORAL_RE = re.compile(
    r"\b(?:today|tomorrow|yesterday|now|current|previous|next|before|after|until|since|"
    r"jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|q[1-4]|20\d{2})\b",
    re.IGNORECASE,
)
_TASK_CLASS_RULES = (
    ("debugging", re.compile(r"\b(debug|bug|fix|failure|trace|error|test|regression)\b", re.IGNORECASE)),
    ("planning", re.compile(r"\b(plan|roadmap|timeline|milestone|handover|next)\b", re.IGNORECASE)),
    ("implementation", re.compile(r"\b(implement|build|code|refactor|add|wire)\b", re.IGNORECASE)),
    ("review", re.compile(r"\b(review|audit|verify|inspect|risk)\b", re.IGNORECASE)),
)


@dataclass(slots=True, frozen=True)
class ContextLayer:
    name: str
    path: Path
    content: str
    token_count: int


def infer_task_class(*parts: str | None) -> str:
    text = " ".join(part for part in parts if part).strip()
    for task_class, pattern in _TASK_CLASS_RULES:
        if pattern.search(text):
            return task_class
    return "general"


def _active_facts(facts: list[Fact]) -> list[Fact]:
    return [
        fact
        for fact in facts
        if fact.superseded_by is None and fact.scope != Scope.PROJECT_SECRET
    ]


def _layer_header(title: str, now: datetime, fact_count: int) -> list[str]:
    stamp = now.date().isoformat()
    return [
        f"# {title}",
        "",
        f"generated_at: {now.isoformat().replace('+00:00', 'Z')}",
        f"source_fact_count: {fact_count}",
        "",
    ]


def _digest_layer(facts: list[Fact], now: datetime) -> str:
    lines = _layer_header("Memory Digest", now, len(facts))
    for fact in sorted(facts, key=lambda item: (item.topic, item.created, item.fact_id)):
        lines.append(f"- {fact.topic}: {fact.text} [id:{fact.fact_id}]")
    return "\n".join(lines).rstrip() + "\n"


def _numeric_layer(facts: list[Fact], now: datetime) -> str:
    lines = _layer_header("Numeric Memory", now, len(facts))
    numeric = [fact for fact in facts if _NUMBER_RE.search(fact.text)]
    if not numeric:
        lines.append("- No numeric facts found in the active memory window.")
    for fact in sorted(numeric, key=lambda item: (item.topic, item.text)):
        lines.append(f"- {fact.text} [id:{fact.fact_id}]")
    return "\n".join(lines).rstrip() + "\n"


def _temporal_layer(facts: list[Fact], now: datetime) -> str:
    lines = _layer_header("Temporal Memory", now, len(facts))
    temporal = [fact for fact in facts if _TEMPORAL_RE.search(fact.text)]
    if not temporal:
        temporal = facts
    for fact in sorted(temporal, key=lambda item: (item.created, item.topic, item.fact_id)):
        lines.append(f"- {fact.created.date().isoformat()}: {fact.text} [id:{fact.fact_id}]")
    return "\n".join(lines).rstrip() + "\n"


def _narrative_layer(facts: list[Fact], now: datetime) -> str:
    lines = _layer_header("Narrative Memory", now, len(facts))
    by_topic: dict[str, list[Fact]] = {}
    for fact in facts:
        by_topic.setdefault(fact.topic, []).append(fact)
    if not by_topic:
        lines.append("- No active facts available.")
    for topic in sorted(by_topic):
        topic_facts = sorted(by_topic[topic], key=lambda item: (item.created, item.fact_id))
        lines.extend(["", f"## {topic}"])
        for fact in topic_facts[:12]:
            lines.append(f"- {fact.text} [id:{fact.fact_id}]")
    return "\n".join(lines).rstrip() + "\n"


def context_layers_root(repo_dir: Path) -> Path:
    return repo_dir / "context" / "layers"


def generate_context_layers(
    repo_dir: Path,
    facts: list[Fact],
    now: datetime,
    *,
    task_class: str = "general",
) -> Path:
    active = _active_facts(facts)
    layer_dir = context_layers_root(repo_dir) / f"{task_class}-{now.date().isoformat()}"
    layer_dir.mkdir(parents=True, exist_ok=True)
    layers = {
        "digest.md": _digest_layer(active, now),
        "numeric.md": _numeric_layer(active, now),
        "temporal.md": _temporal_layer(active, now),
        "narrative.md": _narrative_layer(active, now),
    }
    for name, content in layers.items():
        (layer_dir / name).write_text(content, encoding="utf-8")
    return layer_dir


def _latest_layer_dir(repo_dir: Path, task_class: str | None = None) -> Path | None:
    root = context_layers_root(repo_dir)
    if not root.exists():
        return None
    candidates = [path for path in root.iterdir() if path.is_dir()]
    if task_class:
        prefixed = [path for path in candidates if path.name.startswith(f"{task_class}-")]
        if prefixed:
            candidates = prefixed
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.name)


def select_context_layers(
    repo_dir: Path,
    *,
    task_class: str,
    budget: int,
) -> list[ContextLayer]:
    layer_dir = _latest_layer_dir(repo_dir, task_class) or _latest_layer_dir(repo_dir)
    if layer_dir is None:
        return []
    selected: list[ContextLayer] = []

    def add_layer(name: str, *, required: bool = False) -> None:
        path = layer_dir / f"{name}.md"
        if not path.exists():
            return
        content = path.read_text(encoding="utf-8")
        token_count = estimate_tokens(content)
        used = sum(layer.token_count for layer in selected)
        if not required and used + token_count > budget:
            return
        selected.append(ContextLayer(name=name, path=path, content=content, token_count=token_count))

    add_layer("digest", required=True)
    optional_by_task = {
        "debugging": ["numeric", "temporal", "narrative"],
        "planning": ["temporal", "narrative", "numeric"],
        "implementation": ["narrative", "numeric", "temporal"],
        "review": ["narrative", "temporal", "numeric"],
        "general": ["narrative", "temporal", "numeric"],
    }
    for layer_name in optional_by_task.get(task_class, optional_by_task["general"]):
        add_layer(layer_name)
    return selected
