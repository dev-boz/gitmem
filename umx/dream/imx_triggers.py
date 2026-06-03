"""IMX dream-trigger ingestion for the gitmem Dream pipeline.

IMX writes trigger records to ~/.imx/state/dream-triggers.jsonl when it
detects conditions that warrant memory consolidation or procedure review.
This module reads those records and converts them into Dream candidates.

Allowed trigger_type values (from IMX spec v0.6 §11.3):
  query_gap, route_failure, context_saturation, large_task_completion,
  entrenchment_risk, procedure_regression, policy_drift
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_TRIGGERS_PATH = Path.home() / ".imx" / "state" / "dream-triggers.jsonl"

KNOWN_TRIGGER_TYPES = frozenset({
    "query_gap",
    "route_failure",
    "context_saturation",
    "large_task_completion",
    "entrenchment_risk",
    "procedure_regression",
    "policy_drift",
})


@dataclass(slots=True)
class ImxDreamTrigger:
    trigger_type: str
    source: str
    query: str = ""
    ts: str = ""
    context: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)

    @property
    def task_id(self) -> str | None:
        return self.context.get("task_id")

    @property
    def task_class(self) -> str | None:
        return self.context.get("task_class")

    @property
    def risk_tier(self) -> str | None:
        return self.context.get("risk_tier")

    @property
    def is_known_type(self) -> bool:
        return self.trigger_type in KNOWN_TRIGGER_TYPES

    def to_candidate_text(self) -> str:
        parts = [f"IMX dream trigger: {self.trigger_type}"]
        if self.query:
            parts.append(f"Query: {self.query}")
        if self.task_class:
            parts.append(f"Task class: {self.task_class}")
        if self.risk_tier:
            parts.append(f"Risk tier: {self.risk_tier}")
        if self.task_id:
            parts.append(f"Task ID: {self.task_id}")
        if self.ts:
            parts.append(f"Triggered at: {self.ts}")
        return "\n".join(parts)


def _utc_now_str() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_imx_triggers(
    triggers_path: Path | None = None,
    *,
    since: str | None = None,
    max_records: int = 500,
) -> list[ImxDreamTrigger]:
    """Read IMX dream-trigger records from the JSONL file.

    Args:
        triggers_path: Override path; defaults to ~/.imx/state/dream-triggers.jsonl
        since: ISO 8601 timestamp; skip records older than this
        max_records: Safety cap on records read per call
    """
    path = triggers_path or _DEFAULT_TRIGGERS_PATH
    if not path.exists():
        return []

    since_dt: datetime | None = None
    if since:
        try:
            since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
        except ValueError:
            logger.warning("Invalid 'since' timestamp: %s", since)

    triggers: list[ImxDreamTrigger] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        logger.warning("Could not read IMX triggers from %s: %s", path, exc)
        return []

    for line in lines[-max_records:]:
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            logger.debug("Skipping corrupt trigger record: %s", exc)
            continue
        if not isinstance(record, dict):
            continue
        ts = record.get("ts", "")
        if since_dt and ts:
            try:
                record_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                if record_dt <= since_dt:
                    continue
            except ValueError:
                pass
        triggers.append(ImxDreamTrigger(
            trigger_type=record.get("trigger_type", "unknown"),
            source=record.get("source", "imx"),
            query=record.get("query", ""),
            ts=ts,
            context=record.get("context", {}),
            raw=record,
        ))

    return triggers


def filter_actionable_triggers(
    triggers: list[ImxDreamTrigger],
    *,
    allowed_types: frozenset[str] | None = None,
) -> list[ImxDreamTrigger]:
    """Return triggers that are actionable for the Dream pipeline."""
    allowed = allowed_types if allowed_types is not None else KNOWN_TRIGGER_TYPES
    return [t for t in triggers if t.trigger_type in allowed]


def triggers_to_dream_candidates(
    triggers: list[ImxDreamTrigger],
) -> list[dict]:
    """Convert IMX dream triggers to Dream pipeline candidate dicts."""
    candidates = []
    for trigger in triggers:
        candidates.append({
            "source": f"imx:{trigger.source}",
            "trigger_type": trigger.trigger_type,
            "content": trigger.to_candidate_text(),
            "task_id": trigger.task_id,
            "task_class": trigger.task_class,
            "ts": trigger.ts or _utc_now_str(),
            "metadata": {
                "risk_tier": trigger.risk_tier,
                "query": trigger.query,
                "imx_context": trigger.context,
            },
        })
    return candidates


def ingest_imx_triggers(
    triggers_path: Path | None = None,
    *,
    since: str | None = None,
    max_records: int = 500,
    allowed_types: frozenset[str] | None = None,
) -> list[dict]:
    """Top-level entry point: read, filter, and convert IMX triggers to candidates.

    Returns a list of Dream candidate dicts ready for the pipeline to process.
    """
    raw = read_imx_triggers(triggers_path, since=since, max_records=max_records)
    actionable = filter_actionable_triggers(raw, allowed_types=allowed_types)
    if not actionable:
        return []
    logger.info("IMX trigger ingestion: %d/%d actionable triggers", len(actionable), len(raw))
    return triggers_to_dream_candidates(actionable)
