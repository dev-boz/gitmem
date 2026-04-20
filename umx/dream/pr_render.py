from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from umx.models import Fact

FACT_DELTA_BLOCK_VERSION = 1
FACT_DELTA_START_MARKER = "<!-- umx:fact-delta:start -->"
FACT_DELTA_END_MARKER = "<!-- umx:fact-delta:end -->"
LEGACY_PR_BODY_MARKER = "<!-- umx:legacy-pr-body -->"
_LEGACY_DREAM_L1_MARKERS = (
    "## Dream L1 Extraction",
    "### Facts",
    "### Provenance",
    "- Extracted by: dream/l1",
    "- Approval tier: L1",
)
_LEGACY_PROMOTION_MARKERS = (
    "## Cross-project promotion proposal preview",
    "### Candidate",
    "### Evidence",
    "- Target repo: `user memory repo`",
)

_FACT_DELTA_BLOCK_RE = re.compile(
    rf"{re.escape(FACT_DELTA_START_MARKER)}\s*```json\s*(?P<payload>\{{.*?\}})\s*```\s*{re.escape(FACT_DELTA_END_MARKER)}",
    re.DOTALL,
)


@dataclass(slots=True, frozen=True)
class FactDeltaEntry:
    topic: str
    path: str
    summary: str
    fact_id: str | None = None
    encoding_strength: int | None = None
    superseded_by: str | None = None
    source_fact_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "topic": self.topic,
            "path": self.path,
            "summary": self.summary,
        }
        if self.fact_id:
            payload["fact_id"] = self.fact_id
        if self.encoding_strength is not None:
            payload["encoding_strength"] = self.encoding_strength
        if self.superseded_by:
            payload["superseded_by"] = self.superseded_by
        if self.source_fact_ids:
            payload["source_fact_ids"] = list(self.source_fact_ids)
        return payload


@dataclass(slots=True, frozen=True)
class FactDeltaBlock:
    added: tuple[FactDeltaEntry, ...] = ()
    modified: tuple[FactDeltaEntry, ...] = ()
    superseded: tuple[FactDeltaEntry, ...] = ()
    tombstoned: tuple[FactDeltaEntry, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": FACT_DELTA_BLOCK_VERSION,
            "added": [entry.to_dict() for entry in self.added],
            "modified": [entry.to_dict() for entry in self.modified],
            "superseded": [entry.to_dict() for entry in self.superseded],
            "tombstoned": [entry.to_dict() for entry in self.tombstoned],
        }


class GovernancePRBodyError(ValueError):
    pass


def _template_text() -> str:
    return (Path(__file__).parents[1] / "templates" / "pr-body.md").read_text(encoding="utf-8")


def _summary(text: str, *, limit: int = 120) -> str:
    compact = " ".join(text.split())
    return compact if len(compact) <= limit else f"{compact[: limit - 3].rstrip()}..."


def _relative_fact_path(fact: Fact, repo_dir: Path) -> str:
    if fact.file_path is not None:
        try:
            return fact.file_path.relative_to(repo_dir).as_posix()
        except ValueError:
            return fact.file_path.as_posix()
    return f"facts/topics/{fact.topic}.md"


def _entry_from_fact(fact: Fact, repo_dir: Path) -> FactDeltaEntry:
    return FactDeltaEntry(
        fact_id=fact.fact_id,
        topic=fact.topic,
        path=_relative_fact_path(fact, repo_dir),
        summary=_summary(fact.text),
        encoding_strength=fact.encoding_strength,
        superseded_by=fact.superseded_by,
    )


def build_fact_delta_from_facts(facts: list[Fact], repo_dir: Path) -> FactDeltaBlock:
    added: list[FactDeltaEntry] = []
    superseded: list[FactDeltaEntry] = []
    for fact in sorted(facts, key=lambda item: (_relative_fact_path(item, repo_dir), item.fact_id)):
        entry = _entry_from_fact(fact, repo_dir)
        if fact.superseded_by:
            superseded.append(entry)
        else:
            added.append(entry)
    return FactDeltaBlock(added=tuple(added), superseded=tuple(superseded))


def build_fact_delta_for_promotion(
    *,
    topic: str,
    path: str,
    summary: str,
    source_fact_ids: list[str] | None = None,
    fact_id: str | None = None,
) -> FactDeltaBlock:
    return FactDeltaBlock(
        added=(
            FactDeltaEntry(
                fact_id=fact_id,
                topic=topic,
                path=path,
                summary=_summary(summary),
                source_fact_ids=tuple(sorted(source_fact_ids or [])),
            ),
        ),
    )


def build_fact_delta_for_tombstones(
    facts: list[Fact],
    repo_dir: Path,
) -> FactDeltaBlock:
    tombstoned = tuple(
        _entry_from_fact(fact, repo_dir)
        for fact in sorted(
            facts,
            key=lambda item: (_relative_fact_path(item, repo_dir), item.fact_id),
        )
    )
    return FactDeltaBlock(tombstoned=tombstoned)


def render_governance_pr_body(
    *,
    heading: str,
    summary_lines: list[str],
    fact_delta: FactDeltaBlock,
) -> str:
    summary = "\n".join(summary_lines).strip()
    if summary:
        summary += "\n"
    fact_delta_json = json.dumps(fact_delta.to_dict(), indent=2, sort_keys=True)
    return _template_text().format(
        heading=heading,
        summary=summary,
        fact_delta_json=fact_delta_json,
        fact_delta_start=FACT_DELTA_START_MARKER,
        fact_delta_end=FACT_DELTA_END_MARKER,
    )


def parse_governance_pr_body(
    body: str,
    *,
    allow_legacy: bool = False,
) -> dict[str, Any] | None:
    match = _FACT_DELTA_BLOCK_RE.search(body)
    if match is None:
        if allow_legacy and LEGACY_PR_BODY_MARKER in body:
            if _looks_like_legacy_governance_pr_body(body):
                return None
            raise GovernancePRBodyError(
                "governance PR body uses the legacy backfill marker but does not match a "
                "recognized pre-fact-delta template"
            )
        suffix = (
            f" or add {LEGACY_PR_BODY_MARKER} to backfill a recognized legacy governance PR"
            if allow_legacy
            else ""
        )
        raise GovernancePRBodyError(
            "governance PR body is missing the required fact-delta block"
            f"{suffix}"
        )
    try:
        payload = json.loads(match.group("payload"))
    except json.JSONDecodeError as exc:
        raise GovernancePRBodyError("governance PR body contains malformed fact-delta JSON") from exc
    _validate_fact_delta_payload(payload)
    return payload


def assert_governance_pr_body(
    body: str,
    *,
    allow_legacy: bool = False,
) -> dict[str, Any] | None:
    return parse_governance_pr_body(body, allow_legacy=allow_legacy)


def touched_fact_ids_from_fact_delta(payload: dict[str, Any]) -> frozenset[str]:
    touched: set[str] = set()
    for section in ("added", "modified", "superseded", "tombstoned"):
        entries = payload.get(section)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            fact_id = entry.get("fact_id")
            if isinstance(fact_id, str) and fact_id.strip():
                touched.add(fact_id.strip())
    return frozenset(touched)


def _looks_like_legacy_governance_pr_body(body: str) -> bool:
    stripped = body.strip()
    return all(marker in stripped for marker in _LEGACY_DREAM_L1_MARKERS) or all(
        marker in stripped for marker in _LEGACY_PROMOTION_MARKERS
    )


def _validate_fact_delta_payload(payload: Any) -> None:
    if not isinstance(payload, dict):
        raise GovernancePRBodyError("fact-delta payload must be a JSON object")
    version = payload.get("version")
    if version != FACT_DELTA_BLOCK_VERSION:
        raise GovernancePRBodyError(
            f"unsupported fact-delta block version: {version!r}; expected {FACT_DELTA_BLOCK_VERSION}"
        )
    for section in ("added", "modified", "superseded", "tombstoned"):
        entries = payload.get(section)
        if not isinstance(entries, list):
            raise GovernancePRBodyError(f"fact-delta section `{section}` must be a list")
        for index, entry in enumerate(entries):
            _validate_fact_delta_entry(section, index, entry)


def _validate_fact_delta_entry(section: str, index: int, entry: Any) -> None:
    if not isinstance(entry, dict):
        raise GovernancePRBodyError(
            f"fact-delta section `{section}` entry {index} must be a JSON object"
        )
    for key in ("topic", "path", "summary"):
        value = entry.get(key)
        if not isinstance(value, str) or not value.strip():
            raise GovernancePRBodyError(
                f"fact-delta section `{section}` entry {index} is missing `{key}`"
            )
    fact_id = entry.get("fact_id")
    if fact_id is not None and (not isinstance(fact_id, str) or not fact_id.strip()):
        raise GovernancePRBodyError(
            f"fact-delta section `{section}` entry {index} has invalid `fact_id`"
        )
    if section == "tombstoned" and (
        not isinstance(fact_id, str) or not fact_id.strip()
    ):
        raise GovernancePRBodyError(
            f"fact-delta section `{section}` entry {index} must include `fact_id`"
        )
    encoding_strength = entry.get("encoding_strength")
    if encoding_strength is not None and not isinstance(encoding_strength, int):
        raise GovernancePRBodyError(
            f"fact-delta section `{section}` entry {index} has invalid `encoding_strength`"
        )
    source_fact_ids = entry.get("source_fact_ids")
    if source_fact_ids is not None:
        if not isinstance(source_fact_ids, list) or not all(
            isinstance(item, str) and item.strip() for item in source_fact_ids
        ):
            raise GovernancePRBodyError(
                f"fact-delta section `{section}` entry {index} has invalid `source_fact_ids`"
            )
