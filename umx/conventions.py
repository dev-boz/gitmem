from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from umx.models import Fact


_PAST_TENSE_PATTERNS = [
    re.compile(r"^was\s", re.IGNORECASE),
    re.compile(r"^were\s", re.IGNORECASE),
    re.compile(r"^used\s", re.IGNORECASE),
    re.compile(r"^had\s", re.IGNORECASE),
    re.compile(r"^did\s", re.IGNORECASE),
    re.compile(r"\bwas\s+\w+ed\b", re.IGNORECASE),
    re.compile(r"\bhas been\b", re.IGNORECASE),
    re.compile(r"\bhad been\b", re.IGNORECASE),
    re.compile(r"\bwere\s+\w+ed\b", re.IGNORECASE),
]

_COMPOUND_PATTERNS = [
    re.compile(r"\band also\b", re.IGNORECASE),
    re.compile(r",\s*and\s", re.IGNORECASE),
    re.compile(r"\.\s+[A-Z]"),  # multi-sentence
]


@dataclass(slots=True)
class ConventionSet:
    topics: set[str] = field(default_factory=set)
    topic_descriptions: dict[str, str] = field(default_factory=dict)
    phrasing_rules: list[str] = field(default_factory=list)
    entity_vocabulary: dict[str, str] = field(default_factory=dict)
    project_conventions: list[str] = field(default_factory=list)
    schema_conflicts: list[str] = field(default_factory=list)


def parse_conventions(path: Path) -> ConventionSet:
    if not path.exists():
        return ConventionSet()
    current: str | None = None
    result = ConventionSet()
    parent_topic: str | None = None
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("## "):
            current = line[3:].strip().lower()
            parent_topic = None
            continue
        if not line.startswith("- "):
            continue
        # Detect indentation for hierarchy
        indent = len(raw_line) - len(raw_line.lstrip())
        item = line[2:].strip()
        if current == "topic taxonomy":
            parts = item.split(":", 1)
            topic = parts[0].strip()
            desc = parts[1].strip() if len(parts) > 1 else ""
            if topic:
                result.topics.add(topic)
                if desc:
                    result.topic_descriptions[topic] = desc
                if indent <= 0:
                    parent_topic = topic
                elif parent_topic and "/" not in topic:
                    # Auto-qualify child topics
                    qualified = f"{parent_topic}/{topic}"
                    result.topics.add(qualified)
                    if desc:
                        result.topic_descriptions[qualified] = desc
        elif current == "fact phrasing":
            result.phrasing_rules.append(item)
        elif current == "entity vocabulary":
            if "=" in item:
                key, value = item.split("=", 1)
                result.entity_vocabulary[key.strip()] = value.strip()
        elif current == "project-specific conventions":
            result.project_conventions.append(item)
            # Auto-detect schema conflicts
            lower_item = item.lower()
            if "not " in lower_item or "instead of" in lower_item:
                result.schema_conflicts.append(item)
    return result


def summarize_conventions(path: Path, max_lines: int = 12) -> str:
    if not path.exists():
        return ""
    lines = [line.rstrip() for line in path.read_text().splitlines() if line.strip()]
    return "\n".join(lines[:max_lines])


def is_valid_topic(topic: str, conventions: ConventionSet) -> bool:
    """Check if topic or its parent exists in taxonomy."""
    if topic in conventions.topics:
        return True
    # Check if parent topic exists (e.g. "devenv/ports" -> "devenv")
    if "/" in topic:
        parent = topic.rsplit("/", 1)[0]
        return parent in conventions.topics
    return False


def suggest_topic(text: str, conventions: ConventionSet) -> str | None:
    """Suggest best-matching topic from taxonomy based on text keywords."""
    if not conventions.topics:
        return None
    lower_text = text.lower()
    best: str | None = None
    best_score = 0
    for topic in conventions.topics:
        score = 0
        # Check topic name as keyword
        topic_words = topic.replace("/", " ").split()
        for word in topic_words:
            if word.lower() in lower_text:
                score += 1
        # Check description keywords
        desc = conventions.topic_descriptions.get(topic, "").lower()
        if desc:
            for desc_word in desc.split():
                if len(desc_word) > 3 and desc_word in lower_text:
                    score += 1
        if score > best_score:
            best_score = score
            best = topic
    return best if best_score > 0 else None


def _find_topic_alias(topic: str, conventions: ConventionSet) -> str | None:
    """If topic is an alias (key in entity_vocabulary), return canonical form."""
    # Check entity vocabulary for topic aliases
    if topic in conventions.entity_vocabulary:
        canonical = conventions.entity_vocabulary[topic]
        if canonical in conventions.topics:
            return canonical
    # Check if topic matches a description of a declared topic
    lower = topic.lower()
    for declared, desc in conventions.topic_descriptions.items():
        if lower == desc.lower() and declared != topic:
            return declared
    return None


def validate_fact(
    fact: Fact,
    conventions: ConventionSet,
    *,
    all_facts: list[Fact] | None = None,
) -> list[str]:
    issues: list[str] = []
    text = fact.text.strip()

    # Topic validation with alias detection
    if conventions.topics and fact.topic not in conventions.topics:
        alias_target = _find_topic_alias(fact.topic, conventions)
        if alias_target:
            issues.append(
                f"topic '{fact.topic}' is an alias; use canonical topic '{alias_target}'"
            )
        elif not is_valid_topic(fact.topic, conventions):
            issues.append(f"topic '{fact.topic}' is not declared in CONVENTIONS.md")

    # Length validation
    if len(text) < 10:
        issues.append("fact is too short (less than 10 characters)")
    if len(text) > 200:
        issues.append("fact exceeds 200 characters")

    # Atomicity check (expanded)
    if "; " in text:
        issues.append("fact may not be atomic")
    for pattern in _COMPOUND_PATTERNS:
        if pattern.search(text):
            issues.append("fact may not be atomic (compound phrasing detected)")
            break

    # Entity vocabulary enforcement
    for alias, canonical in conventions.entity_vocabulary.items():
        # Match whole word alias in text
        if re.search(r"\b" + re.escape(alias) + r"\b", text, re.IGNORECASE):
            issues.append(
                f"use canonical name '{canonical}' instead of '{alias}'"
            )

    # Tense enforcement
    lower = text.lower()
    if any("present tense" in rule.lower() for rule in conventions.phrasing_rules):
        for pattern in _PAST_TENSE_PATTERNS:
            if pattern.search(text):
                issues.append("fact appears to violate present-tense convention")
                break

    # Duplicate text detection
    if all_facts is not None:
        for other in all_facts:
            if other.fact_id != fact.fact_id and other.text.strip().lower() == text.lower():
                issues.append(f"duplicate text (same as {other.fact_id})")
                break

    return issues


def normalize_entity(text: str, vocabulary: dict[str, str]) -> str:
    """Replace entity aliases with canonical forms using single-pass to avoid cascading."""
    if not vocabulary:
        return text
    # Build single alternation pattern, longest-first to prevent partial matches
    sorted_aliases = sorted(vocabulary.keys(), key=len, reverse=True)
    pattern = re.compile(
        r"\b(" + "|".join(re.escape(alias) for alias in sorted_aliases) + r")\b"
    )
    return pattern.sub(lambda m: vocabulary[m.group(0)], text)


def apply_conventions_to_fact(fact: Fact, conventions: ConventionSet) -> Fact:
    """Apply conventions to normalize a fact.

    - Entity vocabulary substitution in text
    - Topic mapping (if topic alias exists in vocabulary)
    Returns updated fact clone.
    """
    if not conventions:
        return fact

    text = fact.text
    topic = fact.topic

    if conventions.entity_vocabulary:
        text = normalize_entity(text, conventions.entity_vocabulary)
        topic = normalize_entity(topic, {
            k.lower(): v.lower() for k, v in conventions.entity_vocabulary.items()
        })

    if text == fact.text and topic == fact.topic:
        return fact

    return fact.clone(text=text, topic=topic)


def normalize_fact_text(text: str, conventions: ConventionSet) -> str:
    """Auto-fix common convention violations."""
    # Replace entity aliases using single-pass approach
    result = normalize_entity(text, conventions.entity_vocabulary)
    # Collapse multiple spaces
    result = re.sub(r"  +", " ", result)
    # Trim trailing periods
    result = result.rstrip(".")
    # Strip whitespace
    result = result.strip()
    # Capitalize first letter
    if result:
        result = result[0].upper() + result[1:]
    return result


def validate_conventions_file(path: Path) -> list[str]:
    """Validate the CONVENTIONS.md file structure."""
    issues: list[str] = []
    if not path.exists():
        issues.append("CONVENTIONS.md file not found")
        return issues

    text = path.read_text()
    lines = text.splitlines()

    # Check required sections
    sections_found: set[str] = set()
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## "):
            sections_found.add(stripped[3:].strip().lower())

    required_sections = ["topic taxonomy", "fact phrasing", "entity vocabulary"]
    for section in required_sections:
        if section not in sections_found:
            issues.append(f"missing required section: '## {section.title()}'")

    # Parse and check for duplicate topics
    conventions = parse_conventions(path)
    topic_names: list[str] = []
    current: str | None = None
    for raw_line in lines:
        line = raw_line.strip()
        if line.startswith("## "):
            current = line[3:].strip().lower()
            continue
        if current == "topic taxonomy" and line.startswith("- "):
            topic = line[2:].strip().split(":", 1)[0].strip()
            if topic:
                topic_names.append(topic)
    seen: set[str] = set()
    for topic in topic_names:
        if topic in seen:
            issues.append(f"duplicate topic: '{topic}'")
        seen.add(topic)

    # Check for circular aliases in entity vocabulary
    for alias, canonical in conventions.entity_vocabulary.items():
        if canonical in conventions.entity_vocabulary:
            target = conventions.entity_vocabulary[canonical]
            if target == alias:
                issues.append(
                    f"circular alias: '{alias}' -> '{canonical}' -> '{alias}'"
                )

    # Check phrasing rules are actionable (non-empty and reasonable length)
    for rule in conventions.phrasing_rules:
        if len(rule.strip()) < 5:
            issues.append(f"phrasing rule too vague: '{rule}'")

    return issues
