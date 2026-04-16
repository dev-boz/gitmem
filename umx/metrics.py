from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from umx.budget import estimate_tokens
from umx.config import UMXConfig, default_config
from umx.conventions import summarize_conventions
from umx.memory import load_all_facts, read_memory_md
from umx.models import SourceType, Verification, parse_datetime
from umx.search import ensure_usage_db, usage_path

HEALTH_METRIC_LABELS = {
    "injection_precision": "Injection precision",
    "fact_churn_rate": "Fact churn rate",
    "contradiction_rate": "Contradiction rate",
    "entrenchment_index": "Entrenchment index",
    "hot_tier_utilisation": "Hot tier utilisation",
    "staleness_ratio": "Staleness ratio",
}


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _hot_tier_tokens(repo_dir: Path) -> int:
    memory_md = read_memory_md(repo_dir)
    memory_tokens = estimate_tokens(memory_md) if memory_md else 0
    conventions_tokens = estimate_tokens(summarize_conventions(repo_dir / "CONVENTIONS.md"))
    principle_tokens = sum(
        estimate_tokens(fact.text)
        for fact in load_all_facts(repo_dir, include_superseded=False)
        if fact.file_path and "principles/topics" in fact.file_path.as_posix()
    )
    return memory_tokens + conventions_tokens + principle_tokens


def _metric(value: float, *, healthy_min: float | None = None, healthy_max: float | None = None, signal: str) -> dict[str, Any]:
    status = "ok"
    if healthy_min is not None and value < healthy_min:
        status = "warn"
    if healthy_max is not None and value > healthy_max:
        status = "warn"
    return {
        "value": round(value, 4),
        "healthy_min": healthy_min,
        "healthy_max": healthy_max,
        "signal": signal,
        "status": status,
    }


def compute_metrics(repo_dir: Path, config: UMXConfig | None = None) -> dict[str, dict[str, Any]]:
    cfg = config or default_config()
    ensure_usage_db(repo_dir)
    conn = _connect(usage_path(repo_dir))
    row = conn.execute(
        """
        SELECT
          SUM(CASE WHEN event_kind = 'inject' AND item_kind = 'fact' THEN 1 ELSE 0 END) AS inject_total,
          SUM(CASE WHEN event_kind = 'inject' AND item_kind = 'fact' AND used_in_output = 1 THEN 1 ELSE 0 END) AS inject_used
        FROM usage_events
        """
    ).fetchone()
    usage_rows = {
        usage_row["fact_id"]: usage_row
        for usage_row in conn.execute("SELECT * FROM usage WHERE item_kind = 'fact'").fetchall()
    }
    conn.close()

    all_facts = load_all_facts(repo_dir, include_superseded=True) if repo_dir.exists() else []
    active_facts = [fact for fact in all_facts if fact.superseded_by is None]
    strong_facts = [fact for fact in active_facts if fact.encoding_strength >= 4]

    inject_total = int(row["inject_total"] or 0)
    inject_used = int(row["inject_used"] or 0)
    injection_precision = inject_used / inject_total if inject_total else 0.0

    churn_numerator = sum(1 for fact in all_facts if fact.supersedes)
    fact_churn_rate = churn_numerator / max(1, len(active_facts))

    contradiction_numerator = sum(1 for fact in active_facts if fact.conflicts_with)
    contradiction_rate = contradiction_numerator / max(1, len(active_facts))

    entrenchment_numerator = sum(
        1
        for fact in strong_facts
        if fact.source_type != SourceType.GROUND_TRUTH_CODE
        and fact.verification != Verification.HUMAN_CONFIRMED
    )
    entrenchment_index = entrenchment_numerator / max(1, len(strong_facts))

    hot_tier_tokens = _hot_tier_tokens(repo_dir)
    hot_tier_utilisation = hot_tier_tokens / max(1, cfg.memory.hot_tier_max_tokens)

    cutoff = datetime.now(tz=UTC) - timedelta(days=30)
    stale_count = 0
    for fact in active_facts:
        usage = usage_rows.get(fact.fact_id)
        stamp = parse_datetime(usage["last_referenced"]) if usage and usage["last_referenced"] else fact.created
        if stamp is None or stamp <= cutoff:
            stale_count += 1
    staleness_ratio = stale_count / max(1, len(active_facts))

    return {
        "injection_precision": _metric(
            injection_precision,
            healthy_min=0.3,
            signal="Below 0.3 means retrieval relevance or context budget is poorly calibrated.",
        ),
        "fact_churn_rate": _metric(
            fact_churn_rate,
            healthy_max=0.1,
            signal="High churn means the codebase is changing faster than memory can stabilise.",
        ),
        "contradiction_rate": _metric(
            contradiction_rate,
            healthy_max=0.05,
            signal="High contradiction load suggests extraction noise or genuine repo inconsistency.",
        ),
        "entrenchment_index": _metric(
            entrenchment_index,
            healthy_max=0.2,
            signal="High entrenchment indicates strong facts are accumulating without grounded verification.",
        ),
        "hot_tier_utilisation": _metric(
            hot_tier_utilisation,
            healthy_min=0.5,
            healthy_max=0.9,
            signal="Low means memory is underused; high means the hot tier likely needs trimming.",
        ),
        "staleness_ratio": _metric(
            staleness_ratio,
            healthy_max=0.4,
            signal="High staleness means old facts are not being revisited or pruned aggressively enough.",
        ),
    }


def health_flags(metrics: dict[str, dict[str, Any]]) -> list[str]:
    flags: list[str] = []
    for key, metric in metrics.items():
        if metric["status"] == "warn":
            flags.append(f"{HEALTH_METRIC_LABELS.get(key, key)} out of range: {metric['signal']}")
    return flags
