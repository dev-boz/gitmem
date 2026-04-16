from __future__ import annotations

from pathlib import Path
from typing import Any

from umx.calibration import build_calibration_advice
from umx.config import load_config
from umx.dream.gates import read_dream_state
from umx.dream.processing import summarize_processing_log
from umx.git_ops import git_signing_payload
from umx.metrics import compute_metrics, health_flags
from umx.memory import load_all_facts
from umx.scope import config_path, discover_project_slug, project_memory_dir
from umx.tombstones import load_tombstones


def build_status_payload(cwd: Path) -> dict[str, Any]:
    repo = project_memory_dir(cwd)
    state = read_dream_state(repo)
    facts = load_all_facts(repo, include_superseded=False) if repo.exists() else []
    cfg = load_config(config_path())
    metrics = compute_metrics(repo, cfg)
    flags = health_flags(metrics)
    advice = build_calibration_advice(metrics, flags)
    hot_metric = metrics["hot_tier_utilisation"]["value"]
    sessions_dir = repo / "sessions"
    session_count = len(list(sessions_dir.glob("**/*.jsonl"))) if sessions_dir.exists() else 0
    tombstone_count = len(load_tombstones(repo)) if repo.exists() else 0
    fact_count = len(facts)
    return {
        "slug": discover_project_slug(cwd),
        "repo": str(repo),
        "fact_count": fact_count,
        "facts": fact_count,
        "tombstones": tombstone_count,
        "session_count": session_count,
        "pending_session_count": int(state.get("session_count", 0)),
        "last_dream": state.get("last_dream"),
        "processing": summarize_processing_log(repo, refs=("origin/main",)),
        "git": git_signing_payload(cfg),
        "hot_tier_tokens": int(round(hot_metric * cfg.memory.hot_tier_max_tokens)),
        "hot_tier_max": cfg.memory.hot_tier_max_tokens,
        "hot_tier_pct": int(round(hot_metric * 100)),
        "ok": len(flags) == 0,
        "flags": flags,
        "metrics": metrics,
        "advice": advice,
        "guidance": advice,
    }
