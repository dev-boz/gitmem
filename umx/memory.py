"""Memory read/write — MEMORY.md, topic files, and JSON derivation.

Markdown is the canonical storage format. JSON is derived, not authoritative.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from umx.models import (
    Fact,
    MemoryType,
    Scope,
    TopicIndex,
    UmxConfig,
)

# Pattern for inline metadata: <!-- umx: {...} -->
_META_PATTERN = re.compile(
    r"<!--\s*umx:\s*(\{.*?\})\s*-->",
    re.DOTALL,
)
# Pattern for strength prefix: [S:N]
_STRENGTH_PATTERN = re.compile(r"\[S:(\d)\]")
# Pattern for a fact line: - [S:N] text <!-- umx: {...} -->
_FACT_LINE_PATTERN = re.compile(
    r"^-\s+\[S:(\d)\]\s+(.*?)(?:\s*<!--\s*umx:\s*(\{.*?\})\s*-->)?\s*$"
)


def load_config(umx_dir: Path) -> UmxConfig:
    """Load config from .umx/config.yaml, falling back to defaults."""
    config_path = umx_dir / "config.yaml"
    if config_path.exists():
        with config_path.open() as f:
            data = yaml.safe_load(f) or {}
        return UmxConfig.from_dict(data)
    return UmxConfig()


def save_config(umx_dir: Path, config: UmxConfig) -> None:
    """Save config to .umx/config.yaml."""
    umx_dir.mkdir(parents=True, exist_ok=True)
    config_path = umx_dir / "config.yaml"
    data = {
        k: v for k, v in config.__dict__.items()
        if not k.startswith("_")
    }
    with config_path.open("w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)


# ──────────────────────────────────────────────────────────────
#  Topic file parsing and writing
# ──────────────────────────────────────────────────────────────


def parse_fact_line(line: str, topic: str, scope: Scope) -> Fact | None:
    """Parse a single fact line from a topic markdown file.

    Handles both annotated and bare lines:
      - [S:4] some fact <!-- umx: {"id":"f_001","conf":0.97,...} -->
      - [S:3] some fact
      - some fact (bare — assigned strength 5)
    """
    line = line.strip()
    if not line.startswith("-"):
        return None

    # Try full pattern with metadata
    m = _FACT_LINE_PATTERN.match(line)
    if m:
        strength = int(m.group(1))
        text = m.group(2).strip()
        meta_json = m.group(3)

        meta: dict[str, Any] = {}
        if meta_json:
            try:
                meta = json.loads(meta_json)
            except json.JSONDecodeError:
                pass

        return Fact(
            id=meta.get("id", Fact.generate_id()),
            text=text,
            scope=scope,
            topic=topic,
            encoding_strength=strength,
            memory_type=_strength_to_memory_type(strength),
            confidence=meta.get("conf", 0.8),
            tags=meta.get("tags", []),
            source_tool=meta.get("source_tool", ""),
            source_session=meta.get("source_session", ""),
            corroborated_by=meta.get("corroborated_by", []),
            last_retrieved=(
                datetime.fromisoformat(meta["last_retrieved"])
                if meta.get("last_retrieved")
                else None
            ),
            created=(
                datetime.fromisoformat(meta["created"])
                if meta.get("created")
                else datetime.now(timezone.utc)
            ),
        )

    # Bare line (no strength prefix): user-added, promote to S:5
    bare = line.lstrip("- ").strip()
    if bare:
        return Fact(
            id=Fact.generate_id(),
            text=bare,
            scope=scope,
            topic=topic,
            encoding_strength=5,
            memory_type=MemoryType.EXPLICIT_SEMANTIC,
            confidence=1.0,
        )

    return None


def _strength_to_memory_type(strength: int) -> MemoryType:
    if strength >= 4:
        return MemoryType.EXPLICIT_SEMANTIC
    elif strength == 3:
        return MemoryType.EXPLICIT_EPISODIC
    else:
        return MemoryType.IMPLICIT


def format_fact_line(fact: Fact) -> str:
    """Format a fact as a markdown line with inline metadata."""
    meta = {
        "id": fact.id,
        "conf": fact.confidence,
        "corroborated_by": fact.corroborated_by,
    }
    if fact.tags:
        meta["tags"] = fact.tags
    if fact.source_tool:
        meta["source_tool"] = fact.source_tool
    if fact.source_session:
        meta["source_session"] = fact.source_session
    if fact.last_retrieved:
        meta["last_retrieved"] = fact.last_retrieved.isoformat()
    meta["created"] = fact.created.isoformat()

    meta_str = json.dumps(meta, separators=(",", ":"))
    return f"- [S:{fact.encoding_strength}] {fact.text} <!-- umx: {meta_str} -->"


def load_topic_facts(
    topic_path: Path,
    topic: str,
    scope: Scope,
) -> list[Fact]:
    """Load all facts from a topic markdown file."""
    if not topic_path.exists():
        return []

    facts: list[Fact] = []
    content = topic_path.read_text()
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("-"):
            fact = parse_fact_line(stripped, topic=topic, scope=scope)
            if fact:
                facts.append(fact)
    return facts


def save_topic_facts(
    topic_path: Path,
    topic: str,
    facts: list[Fact],
) -> None:
    """Write facts to a topic markdown file."""
    topic_path.parent.mkdir(parents=True, exist_ok=True)

    lines = [f"## {topic}", ""]
    for fact in sorted(facts, key=lambda f: -f.encoding_strength):
        lines.append(format_fact_line(fact))
    lines.append("")

    topic_path.write_text("\n".join(lines))


def derive_json(topic_path: Path, facts: list[Fact]) -> None:
    """Derive JSON cache from parsed facts.

    JSON path is the same as the markdown path but with .umx.json extension.
    """
    json_path = topic_path.with_suffix(".umx.json")
    data = [f.to_dict() for f in facts]
    with json_path.open("w") as f:
        json.dump(data, f, indent=2, default=str)


def load_all_facts(umx_dir: Path, scope: Scope) -> list[Fact]:
    """Load all facts from all topic files in a .umx/ directory."""
    topics_dir = umx_dir / "topics"
    if not topics_dir.exists():
        return []

    all_facts: list[Fact] = []
    for md_file in sorted(topics_dir.glob("*.md")):
        topic = md_file.stem
        facts = load_topic_facts(md_file, topic=topic, scope=scope)
        all_facts.extend(facts)
    return all_facts


# ──────────────────────────────────────────────────────────────
#  MEMORY.md
# ──────────────────────────────────────────────────────────────


def build_memory_md(
    umx_dir: Path,
    scope: str,
    session_count: int = 0,
    last_dream: str = "",
) -> str:
    """Build the MEMORY.md content from topic files."""
    topics_dir = umx_dir / "topics"
    rows: list[TopicIndex] = []

    if topics_dir.exists():
        for md_file in sorted(topics_dir.glob("*.md")):
            topic = md_file.stem
            facts = load_topic_facts(
                md_file,
                topic=topic,
                scope=Scope(scope) if scope in [s.value for s in Scope] else Scope.PROJECT_TEAM,
            )
            if facts:
                avg_str = round(
                    sum(f.encoding_strength for f in facts) / len(facts), 1
                )
                # Get file modification time
                mtime = datetime.fromtimestamp(
                    md_file.stat().st_mtime, tz=timezone.utc
                )
                rows.append(TopicIndex(
                    topic=topic.replace("_", " ").title(),
                    file=f"topics/{md_file.name}",
                    updated=mtime.strftime("%Y-%m-%d"),
                    avg_strength=avg_str,
                ))

    lines = [
        "# umx memory index",
        f"scope: {scope}",
        f"last_dream: {last_dream or 'never'}",
        f"session_count: {session_count}",
        "",
        "## Index",
        "| Topic | File | Updated | Avg strength |",
        "|-------|------|---------|--------------|",
    ]

    for row in rows:
        lines.append(
            f"| {row.topic} | {row.file} | {row.updated} | {row.avg_strength} |"
        )

    lines.append("")
    return "\n".join(lines)


def write_memory_md(umx_dir: Path, content: str) -> None:
    """Write MEMORY.md, enforcing size constraints."""
    memory_path = umx_dir / "MEMORY.md"
    lines = content.splitlines()

    # Enforce 200-line limit
    if len(lines) > 200:
        lines = lines[:200]
        content = "\n".join(lines) + "\n"

    # Enforce 25KB limit
    encoded = content.encode("utf-8")
    if len(encoded) > 25 * 1024:
        while len(content.encode("utf-8")) > 25 * 1024 and lines:
            lines.pop()
            content = "\n".join(lines) + "\n"

    memory_path.write_text(content)


def read_memory_md(umx_dir: Path) -> str | None:
    """Read MEMORY.md if it exists."""
    memory_path = umx_dir / "MEMORY.md"
    if memory_path.exists():
        return memory_path.read_text()
    return None


def add_fact(
    umx_dir: Path,
    fact: Fact,
) -> None:
    """Add a single fact to the appropriate topic file."""
    topics_dir = umx_dir / "topics"
    topics_dir.mkdir(parents=True, exist_ok=True)

    topic_path = topics_dir / f"{fact.topic}.md"
    existing = load_topic_facts(topic_path, topic=fact.topic, scope=fact.scope)

    # Check for duplicate by ID
    existing_ids = {f.id for f in existing}
    if fact.id not in existing_ids:
        existing.append(fact)

    save_topic_facts(topic_path, fact.topic, existing)
    derive_json(topic_path, existing)


def remove_fact(umx_dir: Path, fact_id: str, topic: str, scope: Scope) -> bool:
    """Remove a fact by ID from a topic file."""
    topic_path = umx_dir / "topics" / f"{topic}.md"
    facts = load_topic_facts(topic_path, topic=topic, scope=scope)
    new_facts = [f for f in facts if f.id != fact_id]
    if len(new_facts) < len(facts):
        save_topic_facts(topic_path, topic, new_facts)
        derive_json(topic_path, new_facts)
        return True
    return False


def find_fact_by_id(umx_dir: Path, fact_id: str, scope: Scope) -> Fact | None:
    """Find a fact by ID across all topic files."""
    all_facts = load_all_facts(umx_dir, scope)
    for fact in all_facts:
        if fact.id == fact_id:
            return fact
    return None
