from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from umx.calibration import build_calibration_advice
from umx.config import load_config
from umx.conventions import validate_conventions_file
from umx.dream.gates import DreamLock
from umx.dream.processing import summarize_processing_log
from umx.git_ops import git_signing_payload, git_signing_readiness
from umx.metrics import compute_metrics, health_flags
from umx.schema import detect_schema_state, repair_schema
from umx.search_semantic import embeddings_available
from umx.scope import find_orphaned_scoped_memory, find_project_root, get_umx_home, project_memory_dir


def _quarantine_summary(repo_dir: Path) -> dict[str, object]:
    quarantine_dir = repo_dir / "local" / "quarantine"
    if not quarantine_dir.exists():
        return {"count": 0, "files": []}
    files = sorted(
        path.relative_to(repo_dir).as_posix()
        for path in quarantine_dir.iterdir()
        if path.is_file() and not path.name.endswith(".meta.json")
    )
    return {"count": len(files), "files": files[:10]}


def _dream_lock_summary(repo_dir: Path, *, fix: bool) -> tuple[dict[str, Any], list[str]]:
    lock = DreamLock(repo_dir)
    summary: dict[str, Any] = {
        "present": lock.path.exists(),
        "stale": False,
        "path": lock.path.relative_to(repo_dir).as_posix(),
        "pid": None,
        "hostname": None,
        "started": None,
        "heartbeat": None,
    }
    fixes: list[str] = []
    if not lock.path.exists():
        return summary, fixes
    corrupted = False
    try:
        payload = json.loads(lock.path.read_text())
    except json.JSONDecodeError:
        payload = {}
        corrupted = True
    try:
        summary["stale"] = corrupted or lock.is_stale()
    except json.JSONDecodeError:
        corrupted = True
        summary["stale"] = True
    summary["pid"] = payload.get("pid")
    summary["hostname"] = payload.get("hostname")
    summary["started"] = payload.get("started")
    summary["heartbeat"] = payload.get("heartbeat")
    if fix and summary["stale"]:
        lock.release()
        fixes.append("cleared stale dream lock" if not corrupted else "cleared corrupt dream lock")
        summary["present"] = False
        summary["stale"] = False
    return summary, fixes


def run_doctor(cwd: Path | None = None, *, fix: bool = False) -> dict[str, object]:
    home = get_umx_home()
    cfg = load_config(home / "config.yaml")
    result: dict[str, object] = {
        "umx_home": str(home),
        "exists": home.exists(),
        "config_exists": (home / "config.yaml").exists(),
        "git_signing": git_signing_payload(cfg),
        "git_signing_readiness": {
            "format": "openpgp",
            "signing_key": None,
            "signer_program": "gpg",
            "signer_program_configured": None,
            "signer_available": False,
            "user_name": None,
            "user_email": None,
            "ready": False,
            "issues": [],
        },
        "fixes_applied": [],
    }

    # Convention validation
    try:
        project_root = find_project_root(cwd)
        repo_dir = project_memory_dir(cwd)
        readiness = git_signing_readiness(repo_dir, cfg)
        result["git_signing_readiness"] = {
            "format": readiness.format,
            "signing_key": readiness.signing_key,
            "signer_program": readiness.signer_program,
            "signer_program_configured": readiness.signer_program_configured,
            "signer_available": readiness.signer_available,
            "user_name": readiness.user_name,
            "user_email": readiness.user_email,
            "ready": readiness.ready,
            "issues": list(readiness.issues),
        }
        schema_state = detect_schema_state(repo_dir)
        result["schema"] = schema_state.to_dict()
        if fix and schema_state.fixable:
            repair = repair_schema(repo_dir, config=cfg)
            result["fixes_applied"].extend(repair.applied)
            result["schema"] = detect_schema_state(repo_dir).to_dict()
        conventions_path = repo_dir / "CONVENTIONS.md"
        conv_issues = validate_conventions_file(conventions_path)
        orphaned_scopes = find_orphaned_scoped_memory(repo_dir, project_root)
        dream_lock, lock_fixes = _dream_lock_summary(repo_dir, fix=fix)
        result["fixes_applied"].extend(lock_fixes)
        processing = summarize_processing_log(repo_dir, refs=("origin/main",))
        metrics = compute_metrics(repo_dir, cfg)
        flags = health_flags(metrics)
        advice = build_calibration_advice(metrics, flags)
        result["conventions_valid"] = len(conv_issues) == 0
        result["conventions_issues"] = conv_issues
        result["orphaned_scoped_memory_count"] = len(orphaned_scopes)
        result["orphaned_scoped_memory"] = [
            {
                "scope": orphan.scope_kind,
                "memory_path": orphan.memory_path,
                "scope_path": orphan.scoped_path,
            }
            for orphan in orphaned_scopes
        ]
        result["dream_lock"] = dream_lock
        result["processing"] = processing
        result["quarantine"] = _quarantine_summary(repo_dir)
        result["embeddings"] = {
            "backend": cfg.search.backend,
            "available": embeddings_available(),
            "enabled": cfg.search.backend == "hybrid",
        }
        result["health"] = {
            "ok": len(flags) == 0,
            "flags": flags,
            "guidance": advice,
            "hot_tier_pct": int(round(metrics["hot_tier_utilisation"]["value"] * 100)),
        }
        result["advice"] = advice
    except Exception:
        result["conventions_valid"] = False
        result["conventions_issues"] = ["could not locate project memory"]
        result["schema"] = detect_schema_state(project_memory_dir(cwd)).to_dict()
        result["orphaned_scoped_memory_count"] = 0
        result["orphaned_scoped_memory"] = []
        result["dream_lock"] = {
            "present": False,
            "stale": False,
            "path": "meta/dream.lock",
            "pid": None,
            "hostname": None,
            "started": None,
            "heartbeat": None,
        }
        result["processing"] = {
            "active_runs": 0,
            "active": [],
            "last_event": None,
            "last_completed": None,
            "last_failed": None,
        }
        result["quarantine"] = {"count": 0, "files": []}
        result["embeddings"] = {
            "backend": cfg.search.backend,
            "available": embeddings_available(),
            "enabled": cfg.search.backend == "hybrid",
        }
        result["health"] = {
            "ok": True,
            "flags": [],
            "guidance": [],
            "hot_tier_pct": 0,
        }
        result["advice"] = []

    return result
