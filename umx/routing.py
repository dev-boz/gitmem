from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Union

from umx.models import Scope


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_TITLE_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)
_SECTION_RE = re.compile(r"^##\s+(.+)$", re.MULTILINE)
_TRIGGER_RE = re.compile(r"^-\s+(task_class|node_id|capability_band):\s*(.+)$")


def _parse_frontmatter(text: str) -> dict[str, str]:
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}
    result: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        result[key.strip()] = value.strip().strip('"')
    return result


def _extract_section(text: str, section_name: str) -> str:
    matches = list(_SECTION_RE.finditer(text))
    for index, match in enumerate(matches):
        if match.group(1).strip().lower() != section_name.lower():
            continue
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        return text[start:end].strip()
    return ""


def _repo_default_scope(repo_dir: Path) -> Scope:
    return Scope.USER if repo_dir.parent.name != "projects" else Scope.PROJECT


@dataclass(slots=True)
class RouteCardTrigger:
    kind: str   # task_class | node_id | capability_band
    value: str


@dataclass(slots=True)
class RouteCard:
    route_card_id: str
    title: str
    node_id: str = ""
    task_class: str = ""
    capability_band: str = ""
    confidence: float = 1.0
    encoding_strength: int = 3
    lifecycle: str = "active"
    promoted_from: str = ""
    summary: str = ""
    guidance: str = ""
    evidence: str = ""
    triggers: list[RouteCardTrigger] = field(default_factory=list)
    scope: Scope = Scope.PROJECT
    file_path: Path | None = None

    @property
    def task_classes(self) -> list[str]:
        classes = []
        if self.task_class:
            classes.append(self.task_class)
        for t in self.triggers:
            if t.kind == "task_class" and t.value not in classes:
                classes.append(t.value)
        return classes

    @property
    def node_ids(self) -> list[str]:
        ids = []
        if self.node_id:
            ids.append(self.node_id)
        for t in self.triggers:
            if t.kind == "node_id" and t.value not in ids:
                ids.append(t.value)
        return ids

    @property
    def capability_bands(self) -> list[str]:
        bands = []
        if self.capability_band:
            bands.append(self.capability_band)
        for t in self.triggers:
            if t.kind == "capability_band" and t.value not in bands:
                bands.append(t.value)
        return bands

    def is_active(self) -> bool:
        return self.lifecycle == "active"

    def short_repr(self) -> str:
        return f"{self.node_id or '?'} for {', '.join(self.task_classes) or '?'} (conf={self.confidence:.2f})"


def _parse_triggers(triggers_text: str) -> list[RouteCardTrigger]:
    triggers: list[RouteCardTrigger] = []
    for line in triggers_text.splitlines():
        match = _TRIGGER_RE.match(line.strip())
        if match:
            triggers.append(RouteCardTrigger(kind=match.group(1), value=match.group(2).strip()))
    return triggers


def read_route_card_file(path: Path, repo_dir: Path) -> list[RouteCard]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    fm = _parse_frontmatter(text)
    title_match = _TITLE_RE.search(text)
    title = title_match.group(1).strip() if title_match else path.stem.replace("-", " ")
    triggers_text = _extract_section(text, "Triggers")
    triggers = _parse_triggers(triggers_text)
    try:
        confidence = float(fm.get("confidence", "1.0"))
    except ValueError:
        confidence = 1.0
    try:
        encoding_strength = int(fm.get("encoding_strength", "3"))
    except ValueError:
        encoding_strength = 3
    card = RouteCard(
        route_card_id=fm.get("route_card_id", path.stem),
        title=title,
        node_id=fm.get("node_id", ""),
        task_class=fm.get("task_class", ""),
        capability_band=fm.get("capability_band", ""),
        confidence=confidence,
        encoding_strength=encoding_strength,
        lifecycle=fm.get("lifecycle", "active"),
        promoted_from=fm.get("promoted_from", ""),
        summary=_extract_section(text, "Summary"),
        guidance=_extract_section(text, "Guidance"),
        evidence=_extract_section(text, "Evidence"),
        triggers=triggers,
        scope=_repo_default_scope(repo_dir),
        file_path=path,
    )
    return [card]


def iter_route_card_files(repo_dir: Path) -> list[Path]:
    routing_dir = repo_dir / "routing"
    if not routing_dir.exists():
        return []
    return sorted(routing_dir.glob("*.md"))


def load_all_route_cards(repo_dir: Path) -> list[RouteCard]:
    cards: list[RouteCard] = []
    for path in iter_route_card_files(repo_dir):
        cards.extend(read_route_card_file(path, repo_dir=repo_dir))
    return [c for c in cards if c.is_active()]


def match_route_cards(
    cards: list[RouteCard],
    *,
    task_class: str | None = None,
    node_id: str | None = None,
    capability_band: str | None = None,
    min_confidence: float = 0.0,
) -> list[RouteCard]:
    """Return route cards matching the given query dimensions, sorted by confidence descending."""
    matched: list[RouteCard] = []
    for card in cards:
        if not card.is_active():
            continue
        if card.confidence < min_confidence:
            continue
        if task_class is not None:
            # Support dotted prefix matching: "implementation.bugfix" matches "implementation"
            tc_lower = task_class.lower()
            card_classes = [c.lower() for c in card.task_classes]
            if not any(tc_lower == c or tc_lower.startswith(c + ".") or c.startswith(tc_lower + ".") for c in card_classes):
                continue
        if node_id is not None:
            if node_id not in card.node_ids:
                continue
        if capability_band is not None:
            if capability_band not in card.capability_bands:
                continue
        matched.append(card)
    matched.sort(key=lambda c: c.confidence, reverse=True)
    return matched


def write_route_card_file(repo_dir: Path, card: RouteCard) -> Path:
    """Write a route card to the routing/ namespace. Creates directory if needed."""
    routing_dir = repo_dir / "routing"
    routing_dir.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9-]", "-", card.route_card_id.lower()).strip("-")
    path = routing_dir / f"{slug}.md"
    lines = [
        "---",
        f"route_card_id: {card.route_card_id}",
        'schema_version: "0.6"',
    ]
    if card.node_id:
        lines.append(f"node_id: {card.node_id}")
    if card.task_class:
        lines.append(f"task_class: {card.task_class}")
    if card.capability_band:
        lines.append(f"capability_band: {card.capability_band}")
    lines.extend([
        f"confidence: {card.confidence:.4f}",
        f"encoding_strength: {card.encoding_strength}",
        f"lifecycle: {card.lifecycle}",
    ])
    if card.promoted_from:
        lines.append(f"promoted_from: {card.promoted_from}")
    lines.append("---")
    lines.append("")
    lines.append(f"# Route Card: {card.title}")
    if card.summary:
        lines.extend(["", "## Summary", "", card.summary])
    if card.evidence:
        lines.extend(["", "## Evidence", "", card.evidence])
    if card.guidance:
        lines.extend(["", "## Guidance", "", card.guidance])
    trigger_lines = [
        f"- {t.kind}: {t.value}"
        for t in card.triggers
    ]
    if trigger_lines:
        lines.extend(["", "## Triggers", ""] + trigger_lines)
    content = "\n".join(lines) + "\n"
    path.write_text(content, encoding="utf-8")
    return path


def validate_route_card_l2(card: RouteCard) -> tuple[bool, list[str]]:
    """L2 skeptical validation before a route card becomes canonical.

    Returns (is_valid, issues). Cards with CRITICAL issues should not be promoted.
    """
    issues = []

    if not card.evidence:
        issues.append("CRITICAL: no evidence entries — route card lacks empirical backing")

    if card.confidence >= 0.9 and not card.evidence:
        issues.append("CRITICAL: confidence >= 0.9 with no evidence is suspect")

    # Single source check — evidence is a Markdown string; count distinct bullet lines
    evidence_lines = [
        ln.lstrip("- ").strip()
        for ln in card.evidence.splitlines()
        if ln.strip().startswith("-") and ln.strip() != "-"
    ]
    if evidence_lines and len(set(evidence_lines)) == 1:
        issues.append("WARNING: single evidence source — entrenchment risk (echo chamber)")

    valid_lifecycles = {"draft", "active", "deprecated", "archived"}
    if card.lifecycle not in valid_lifecycles:
        issues.append(f"WARNING: unrecognized lifecycle '{card.lifecycle}'")

    if not card.summary or len(card.summary.strip()) < 20:
        issues.append("WARNING: summary is missing or too short")

    has_critical = any(i.startswith("CRITICAL") for i in issues)
    return (not has_critical, issues)


def promote_route_card(
    card: RouteCard,
    repo_dir,
    *,
    force: bool = False,
) -> tuple[Path, list[str]]:
    """Write route card to routing/ namespace after L2 validation.

    Returns (path_written, issues). Raises ValueError on CRITICAL issues unless force=True.
    """
    is_valid, issues = validate_route_card_l2(card)
    if not is_valid and not force:
        critical = [i for i in issues if i.startswith("CRITICAL")]
        raise ValueError(
            f"Route card '{card.route_card_id}' failed L2 validation: {critical}"
        )
    path = write_route_card_file(repo_dir, card)
    return path, issues
