"""Conflict detection and score-based resolution.

Handles conflicting facts: same topic but contradictory text.
Generates conflicts.md for user review.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from umx.models import ConflictEntry, Fact, UmxConfig
from umx.strength import composite_score


def detect_conflicts(facts: list[Fact], config: UmxConfig | None = None) -> list[ConflictEntry]:
    """Detect conflicts between facts in the same topic.

    Simple heuristic: facts in the same topic with contradictory
    keywords (e.g., different port numbers, different tool names).
    """
    if config is None:
        config = UmxConfig()

    conflicts: list[ConflictEntry] = []
    topic_groups: dict[str, list[Fact]] = {}

    for fact in facts:
        topic_groups.setdefault(fact.topic, []).append(fact)

    for topic, group in topic_groups.items():
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                if _might_conflict(a, b):
                    score_a = composite_score(a, config)
                    score_b = composite_score(b, config)
                    winner = a if score_a >= score_b else b

                    conflicts.append(ConflictEntry(
                        topic=topic,
                        description=_conflict_description(a, b),
                        fact_a=a,
                        fact_b=b,
                        resolution=(
                            f"Fact {'A' if winner is a else 'B'} wins on score "
                            f"({max(score_a, score_b):.2f} vs {min(score_a, score_b):.2f}) "
                            f"— pending user confirmation"
                        ),
                        status="OPEN",
                    ))

    return conflicts


def _might_conflict(a: Fact, b: Fact) -> bool:
    """Heuristic: two facts might conflict if they share key tokens
    but differ in values (e.g., port numbers, on/off states)."""
    # Extract numbers from both texts
    nums_a = set(re.findall(r"\b\d+\b", a.text))
    nums_b = set(re.findall(r"\b\d+\b", b.text))

    # Extract significant words (>3 chars)
    words_a = {w.lower() for w in re.findall(r"\b\w{4,}\b", a.text)}
    words_b = {w.lower() for w in re.findall(r"\b\w{4,}\b", b.text)}

    # Shared subject words but different numbers → potential conflict
    shared_words = words_a & words_b
    if shared_words and nums_a and nums_b and nums_a != nums_b:
        return True

    # Opposite states
    negation_pairs = [
        ({"enable", "enabled", "true", "yes", "on"},
         {"disable", "disabled", "false", "no", "off"}),
    ]
    for pos, neg in negation_pairs:
        a_words = {w.lower() for w in a.text.split()}
        b_words = {w.lower() for w in b.text.split()}
        if (a_words & pos and b_words & neg) or (a_words & neg and b_words & pos):
            if shared_words:
                return True

    return False


def _conflict_description(a: Fact, b: Fact) -> str:
    """Generate a human-readable conflict description."""
    # Find shared words
    words_a = {w.lower() for w in re.findall(r"\b\w{4,}\b", a.text)}
    words_b = {w.lower() for w in re.findall(r"\b\w{4,}\b", b.text)}
    shared = words_a & words_b
    subject = " ".join(sorted(shared)[:3]) if shared else a.topic
    return f"{a.topic} · {subject} · {a.id} vs {b.id}"


def write_conflicts_md(
    umx_dir: Path,
    conflicts: list[ConflictEntry],
) -> None:
    """Write conflicts.md with current conflict entries."""
    conflicts_path = umx_dir / "conflicts.md"
    lines = ["# Conflicts", ""]

    for entry in conflicts:
        status_label = entry.status
        lines.append(
            f"## [{status_label}] {entry.description}"
        )
        score_a = composite_score(entry.fact_a)
        score_b = composite_score(entry.fact_b)
        lines.append(
            f"- Fact A [{entry.fact_a.id}]: \"{entry.fact_a.text}\" "
            f"— {entry.fact_a.source_tool or 'unknown'} "
            f"(S:{entry.fact_a.encoding_strength}, score:{score_a:.2f}, "
            f"{entry.fact_a.created.strftime('%Y-%m-%d')})"
        )
        lines.append(
            f"- Fact B [{entry.fact_b.id}]: \"{entry.fact_b.text}\" "
            f"— {entry.fact_b.source_tool or 'unknown'} "
            f"(S:{entry.fact_b.encoding_strength}, score:{score_b:.2f}, "
            f"{entry.fact_b.created.strftime('%Y-%m-%d')})"
        )
        lines.append(f"- Resolution: {entry.resolution}")
        lines.append(f"- Override: edit viewer to confirm or swap")
        lines.append("")

    conflicts_path.write_text("\n".join(lines))


def load_conflicts(umx_dir: Path) -> list[dict]:
    """Load existing conflicts from conflicts.md (simple parser)."""
    conflicts_path = umx_dir / "conflicts.md"
    if not conflicts_path.exists():
        return []

    content = conflicts_path.read_text()
    entries = []
    current: dict | None = None

    for line in content.splitlines():
        if line.startswith("## ["):
            if current:
                entries.append(current)
            # Parse status and description
            m = re.match(r"## \[(\w+)\] (.+)", line)
            if m:
                current = {
                    "status": m.group(1),
                    "description": m.group(2),
                    "lines": [],
                }
        elif current and line.startswith("- "):
            current["lines"].append(line)

    if current:
        entries.append(current)

    return entries
