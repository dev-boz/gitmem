from __future__ import annotations

import re
from pathlib import Path

from umx.config import UMXConfig, default_config
from umx.identity import generate_fact_id
from umx.memory import load_all_facts
from umx.models import (
    ConsolidationStatus,
    Fact,
    MemoryType,
    Provenance,
    Scope,
    SourceType,
    Verification,
)
from umx.redaction import redact_candidate_fact_text


START_MARKER = "<!-- umx-start: do not edit manually -->"
END_MARKER = "<!-- umx-end -->"
BRIDGE_LINE_RE = re.compile(r"^\s*-\s+(?P<text>.+?)\s*$")


def _bridge_targets(config: UMXConfig | None, target_files: list[str] | None) -> list[str]:
    if target_files is not None:
        return target_files
    cfg = config or default_config()
    return list(cfg.bridge.targets)


def _bridge_facts(repo_dir: Path, max_facts: int) -> list[Fact]:
    eligible_scopes = {Scope.PROJECT, Scope.FOLDER, Scope.FILE}
    facts = [
        fact
        for fact in load_all_facts(repo_dir, include_superseded=False)
        if fact.scope in eligible_scopes
    ]
    facts.sort(
        key=lambda fact: (
            fact.encoding_strength,
            fact.verification.value,
            fact.created,
            fact.fact_id,
        ),
        reverse=True,
    )
    return facts[: max(0, max_facts)]


def render_bridge_block(facts: list[Fact]) -> str:
    lines = [START_MARKER]
    lines.extend(f"- {fact.text}" for fact in facts)
    lines.append(END_MARKER)
    return "\n".join(lines)


def write_bridge_file(path: Path, facts: list[Fact]) -> None:
    block = render_bridge_block(facts)
    existing = path.read_text() if path.exists() else ""
    if START_MARKER in existing and END_MARKER in existing:
        prefix, _, remainder = existing.partition(START_MARKER)
        _, _, suffix = remainder.partition(END_MARKER)
        path.write_text(prefix.rstrip("\n") + "\n" + block + suffix)
        return
    if existing and not existing.endswith("\n"):
        existing += "\n"
    path.write_text(existing + block + "\n")


def write_bridge(
    project_root: Path,
    repo_dir: Path,
    *,
    config: UMXConfig | None = None,
    target_files: list[str] | None = None,
) -> list[Path]:
    cfg = config or default_config()
    if not cfg.bridge.enabled and target_files is None:
        return []
    facts = _bridge_facts(repo_dir, cfg.bridge.max_facts)
    if not facts:
        return []
    written: list[Path] = []
    for target in _bridge_targets(cfg, target_files):
        path = project_root / target
        write_bridge_file(path, facts)
        written.append(path)
    return written


def remove_bridge(
    project_root: Path,
    *,
    config: UMXConfig | None = None,
    target_files: list[str] | None = None,
) -> list[Path]:
    updated: list[Path] = []
    for target in _bridge_targets(config, target_files):
        path = project_root / target
        if not path.exists():
            continue
        text = path.read_text()
        if START_MARKER not in text or END_MARKER not in text:
            continue
        prefix, _, remainder = text.partition(START_MARKER)
        _, _, suffix = remainder.partition(END_MARKER)
        cleaned = (prefix.rstrip() + "\n" + suffix.lstrip()).strip()
        path.write_text(cleaned + ("\n" if cleaned else ""))
        updated.append(path)
    return updated


def import_bridge_facts(
    project_root: Path,
    *,
    config: UMXConfig | None = None,
    target_files: list[str] | None = None,
    topic: str = "legacy-bridge",
) -> list[Fact]:
    imported: list[Fact] = []
    seen_text: set[str] = set()
    for target in _bridge_targets(config, target_files):
        path = project_root / target
        if not path.exists():
            continue
        text = path.read_text()
        if START_MARKER not in text or END_MARKER not in text:
            continue
        _, _, remainder = text.partition(START_MARKER)
        block, _, _ = remainder.partition(END_MARKER)
        for raw_line in block.splitlines():
            match = BRIDGE_LINE_RE.match(raw_line)
            if not match:
                continue
            fact_text = redact_candidate_fact_text(match.group("text").strip(), config)
            if not fact_text or fact_text in seen_text:
                continue
            seen_text.add(fact_text)
            imported.append(
                Fact(
                    fact_id=generate_fact_id(),
                    text=fact_text,
                    scope=Scope.PROJECT,
                    topic=topic,
                    encoding_strength=2,
                    memory_type=MemoryType.EXPLICIT_SEMANTIC,
                    verification=Verification.SELF_REPORTED,
                    source_type=SourceType.EXTERNAL_DOC,
                    confidence=0.4,
                    source_tool=f"legacy-bridge:{path.name}",
                    source_session=f"bridge:{path.name}",
                    consolidation_status=ConsolidationStatus.FRAGILE,
                    provenance=Provenance(
                        extracted_by="legacy-bridge",
                        sessions=[f"bridge:{path.name}"],
                    ),
                )
            )
    return imported
