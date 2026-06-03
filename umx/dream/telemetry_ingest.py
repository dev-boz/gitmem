"""Distilled telemetry ingestion from IMX task outcomes.

Reads ~/.imx/telemetry/tasks.jsonl and extracts stable routing patterns
worth consolidating into gitmem memory (procedures, route cards, facts).

Only distilled summaries are promoted — raw telemetry never lands in gitmem.
Per spec: preserve failure_type and controllability in extracted facts.
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_TASKS_JSONL = Path.home() / ".imx" / "telemetry" / "tasks.jsonl"

KNOWN_OUTCOMES = frozenset({"succeeded", "partial", "failed", "blocked", "escalated"})
KNOWN_FAILURE_TYPES = frozenset({
    "context_exceeded", "capability_gap", "quality_failure",
    "refusal", "timeout", "infrastructure", "cascade",
})


@dataclass
class TelemetryRecord:
    task_id: str
    node_id: str = ""
    task_class: str = ""
    harness_fingerprint: str = ""
    outcome: str = ""
    failure_type: str | None = None
    controllability: str | None = None
    chain_depth: int = 0
    estimated_cost_usd: float | None = None
    tokens_total: int | None = None
    completed_at: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "TelemetryRecord":
        return cls(
            task_id=d.get("task_id", ""),
            node_id=d.get("node_id", ""),
            task_class=d.get("task_class", ""),
            harness_fingerprint=d.get("harness_fingerprint", ""),
            outcome=d.get("outcome", ""),
            failure_type=d.get("failure_type"),
            controllability=d.get("controllability"),
            chain_depth=int(d.get("chain_depth", 0)),
            estimated_cost_usd=d.get("estimated_cost_usd"),
            tokens_total=d.get("tokens_total"),
            completed_at=d.get("completed_at", ""),
        )

    @property
    def is_controllable_failure(self) -> bool:
        return self.outcome == "failed" and self.controllability == "controllable"

    @property
    def is_infrastructure_failure(self) -> bool:
        return self.failure_type == "infrastructure"


@dataclass
class NodeTaskPattern:
    """Aggregated pattern for a node × task_class pair."""
    node_id: str
    task_class: str
    success_count: int = 0
    failure_count: int = 0
    failure_types: dict[str, int] = field(default_factory=dict)
    total_cost_usd: float = 0.0
    sample_count: int = 0

    @property
    def success_rate(self) -> float:
        total = self.success_count + self.failure_count
        return self.success_count / total if total else 0.0

    @property
    def is_stable_success(self) -> bool:
        return self.success_rate >= 0.8 and self.sample_count >= 5

    @property
    def is_repeated_failure(self) -> bool:
        return self.failure_count >= 3 and self.success_rate < 0.5

    def dominant_failure_type(self) -> str | None:
        if not self.failure_types:
            return None
        return max(self.failure_types, key=self.failure_types.get)

    def to_candidate_text(self) -> str:
        lines = [f"Routing pattern: {self.task_class} → {self.node_id}"]
        lines.append(f"Success rate: {self.success_rate:.0%} ({self.success_count}/{self.success_count + self.failure_count} tasks)")
        if self.dominant_failure_type():
            lines.append(f"Dominant failure type: {self.dominant_failure_type()}")
        if self.total_cost_usd:
            avg = self.total_cost_usd / max(self.sample_count, 1)
            lines.append(f"Average cost: ${avg:.4f}/task")
        if self.is_stable_success:
            lines.append("Status: STABLE SUCCESS — promote as route card candidate")
        elif self.is_repeated_failure:
            lines.append("Status: REPEATED FAILURE — consider avoiding this node for this task class")
        return "\n".join(lines)


def read_telemetry_records(
    path: Path | None = None,
    *,
    since: str | None = None,
    max_records: int = 1000,
) -> list[TelemetryRecord]:
    """Read IMX task telemetry records."""
    p = path or DEFAULT_TASKS_JSONL
    if not p.exists():
        return []

    since_ts: str | None = since
    records = []
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        logger.warning("Cannot read telemetry: %s", exc)
        return []

    for line in lines[-max_records:]:
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if since_ts and d.get("completed_at", "") <= since_ts:
            continue
        records.append(TelemetryRecord.from_dict(d))

    return records


def aggregate_patterns(records: list[TelemetryRecord]) -> list[NodeTaskPattern]:
    """Aggregate telemetry records into node × task_class patterns."""
    patterns: dict[tuple[str, str], NodeTaskPattern] = {}

    for rec in records:
        if not rec.node_id or not rec.task_class:
            continue
        key = (rec.node_id, rec.task_class)
        if key not in patterns:
            patterns[key] = NodeTaskPattern(node_id=rec.node_id, task_class=rec.task_class)
        pat = patterns[key]
        pat.sample_count += 1
        if rec.outcome == "succeeded":
            pat.success_count += 1
        elif rec.outcome in ("failed", "partial"):
            pat.failure_count += 1
            if rec.failure_type:
                pat.failure_types[rec.failure_type] = pat.failure_types.get(rec.failure_type, 0) + 1
        if rec.estimated_cost_usd:
            pat.total_cost_usd += rec.estimated_cost_usd

    return list(patterns.values())


def patterns_to_dream_candidates(patterns: list[NodeTaskPattern]) -> list[dict]:
    """Convert stable/failing patterns to Dream candidate dicts."""
    candidates = []
    for pat in patterns:
        if pat.is_stable_success:
            candidates.append({
                "source": "imx:telemetry",
                "trigger_type": "large_task_completion",
                "content": pat.to_candidate_text(),
                "task_class": pat.task_class,
                "metadata": {
                    "node_id": pat.node_id,
                    "success_rate": pat.success_rate,
                    "sample_count": pat.sample_count,
                    "pattern_type": "stable_success",
                },
            })
        elif pat.is_repeated_failure:
            candidates.append({
                "source": "imx:telemetry",
                "trigger_type": "route_failure",
                "content": pat.to_candidate_text(),
                "task_class": pat.task_class,
                "metadata": {
                    "node_id": pat.node_id,
                    "failure_rate": 1 - pat.success_rate,
                    "dominant_failure_type": pat.dominant_failure_type(),
                    "sample_count": pat.sample_count,
                    "pattern_type": "repeated_failure",
                },
            })
    return candidates


def ingest_imx_telemetry(
    path: Path | None = None,
    *,
    since: str | None = None,
    min_samples: int = 3,
) -> list[dict]:
    """Top-level entry: read IMX telemetry and return Dream candidates."""
    records = read_telemetry_records(path, since=since)
    if not records:
        return []
    patterns = aggregate_patterns(records)
    # Only promote patterns with enough samples
    patterns = [p for p in patterns if p.sample_count >= min_samples]
    candidates = patterns_to_dream_candidates(patterns)
    logger.info("Telemetry ingest: %d records → %d patterns → %d candidates",
                len(records), len(patterns), len(candidates))
    return candidates
