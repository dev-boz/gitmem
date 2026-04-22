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
    facts = load_all_facts(repo, include_superseded=False, normalize=False) if repo.exists() else []
    cfg = load_config(config_path())
    metrics = compute_metrics(repo, cfg)
    flags = health_flags(metrics)
    advice = build_calibration_advice(metrics, flags)
    conventions_present = (repo / "CONVENTIONS.md").exists()
    if not conventions_present:
        conventions_flag = (
            "CONVENTIONS.md missing: create one so Dream review can enforce project taxonomy and phrasing."
        )
        flags = [*flags, conventions_flag]
        advice = [
            *advice,
            {
                "metric": "conventions",
                "label": "Conventions coverage",
                "severity": "warn",
                "status": "warn",
                "direction": "missing",
                "value": 0,
                "healthy_min": 1,
                "healthy_max": None,
                "signal": "CONVENTIONS.md is missing from the project memory repo.",
                "flag": conventions_flag,
                "why": "CONVENTIONS.md is missing from the project memory repo.",
                "why_it_matters": (
                    "Without CONVENTIONS.md, Dream review and linting lose the project-specific taxonomy "
                    "and phrasing rules they use to keep facts aligned."
                ),
                "recommended_actions": [
                    "Create CONVENTIONS.md in the project memory repo to define topic taxonomy, fact phrasing, and entity vocabulary.",
                    "Start from the default template and tailor it to the project's dominant modules, terms, and workflows.",
                ],
            },
        ]
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
        "conventions_present": conventions_present,
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
