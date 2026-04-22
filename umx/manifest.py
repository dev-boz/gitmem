from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from umx.models import ConsolidationStatus, Fact


def manifest_path(repo_dir: Path) -> Path:
    return repo_dir / "meta" / "manifest.json"


def topic_status(manifest: dict[str, Any], topic: str) -> dict[str, Any]:
    topics = manifest.get("topics") if isinstance(manifest, dict) else None
    payload = dict(topics.get(topic, {})) if isinstance(topics, dict) and isinstance(topics.get(topic), dict) else {}
    uncertainty = _topic_entry(manifest.get("uncertainty_hotspots"), topic) if isinstance(manifest, dict) else None
    gap = _topic_entry(manifest.get("knowledge_gaps"), topic) if isinstance(manifest, dict) else None
    payload["topic"] = topic
    payload["uncertainty_hotspot"] = uncertainty is not None
    payload["knowledge_gap"] = gap is not None
    if uncertainty is not None:
        payload["uncertainty"] = uncertainty
    if gap is not None:
        payload["gap"] = gap
    return payload


def _topic_entry(items: Any, topic: str) -> dict[str, Any] | None:
    if not isinstance(items, list):
        return None
    for item in items:
        if isinstance(item, str) and item == topic:
            return {"topic": topic}
        if isinstance(item, dict) and item.get("topic") == topic:
            return item
    return None


def rebuild_manifest(repo_dir: Path, facts: list[Fact], now: datetime) -> dict:
    topics: dict[str, dict] = {}
    modules_seen: set[str] = set()
    gap_path = repo_dir / "meta" / "gaps.jsonl"
    gap_signals: dict[str, int] = {}
    if gap_path.exists():
        for line in gap_path.read_text().splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            query = record.get("query", "")
            if query:
                gap_signals[query] = gap_signals.get(query, 0) + 1
    for fact in facts:
        entry = topics.setdefault(
            fact.topic,
            {"fact_count": 0, "avg_strength": 0.0, "fragile_count": 0, "last_updated": None},
        )
        entry["fact_count"] += 1
        entry["avg_strength"] += fact.encoding_strength
        if fact.consolidation_status == ConsolidationStatus.FRAGILE:
            entry["fragile_count"] += 1
        last_updated = fact.created.date().isoformat()
        if not entry["last_updated"] or last_updated > entry["last_updated"]:
            entry["last_updated"] = last_updated
        if fact.code_anchor:
            modules_seen.add(str(Path(fact.code_anchor.path).parent.as_posix()))
    uncertainty_hotspots = []
    for topic, entry in topics.items():
        entry["avg_strength"] = round(entry["avg_strength"] / max(1, entry["fact_count"]), 2)
        if entry["fact_count"] and entry["fragile_count"] / entry["fact_count"] >= 0.5:
            uncertainty_hotspots.append(
                {
                    "topic": topic,
                    "fragile_ratio": round(entry["fragile_count"] / entry["fact_count"], 2),
                    "reason": f"{entry['fragile_count']} of {entry['fact_count']} facts still fragile",
                }
            )
    knowledge_gaps = []
    for query, count in gap_signals.items():
        topic = query.split()[0]
        if topic not in topics:
            knowledge_gaps.append(
                {
                    "topic": topic,
                    "gap_signals": count,
                    "fact_count": 0,
                    "reason": f"{count} gap signals, no facts extracted yet",
                }
            )
    manifest = {
        "topics": topics,
        "modules_seen": sorted(m for m in modules_seen if m and m != "."),
        "uncertainty_hotspots": uncertainty_hotspots,
        "knowledge_gaps": knowledge_gaps,
        "last_rebuilt": now.isoformat().replace("+00:00", "Z"),
    }
    manifest_path(repo_dir).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest
