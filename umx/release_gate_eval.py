from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from umx.config import UMXConfig
from umx.eval_compare import compare_eval_reports
from umx.inject_eval import run_inject_eval
from umx.long_memory_eval import run_long_memory_eval
from umx.retrieval_eval import run_retrieval_eval


def run_release_gate_eval(
    out_dir: Path,
    config: UMXConfig,
    *,
    inject_cases: Path = Path("tests") / "eval" / "inject",
    long_memory_smoke_cases: Path = Path("tests") / "eval" / "long_memory",
    retrieval_smoke_cases: Path = Path("tests") / "eval" / "retrieval",
    long_memory_release_cases: Path | None = None,
    retrieval_release_cases: Path | None = None,
    long_memory_release_min_pass_rate: float = 1.0,
    retrieval_release_min_pass_rate: float = 1.0,
    long_memory_baseline: Path | None = None,
    retrieval_baseline: Path | None = None,
) -> dict[str, Any]:
    if long_memory_baseline is not None and long_memory_release_cases is None:
        raise RuntimeError("--long-memory-baseline requires --long-memory-release-cases")
    if retrieval_baseline is not None and retrieval_release_cases is None:
        raise RuntimeError("--retrieval-baseline requires --retrieval-release-cases")

    out_dir = out_dir.resolve()
    local_dir = out_dir / "local"
    release_dir = out_dir / "release"

    smoke_payloads = {
        "inject": _write_json(
            local_dir / "inject.smoke.json",
            run_inject_eval(inject_cases, config),
        ),
        "long-memory": _write_json(
            local_dir / "long-memory.smoke.json",
            run_long_memory_eval(long_memory_smoke_cases, config),
        ),
        "retrieval": _write_json(
            local_dir / "retrieval.smoke.json",
            run_retrieval_eval(retrieval_smoke_cases, config),
        ),
    }

    release_payloads: dict[str, dict[str, Any] | None] = {
        "long-memory": None,
        "retrieval": None,
    }
    compare_payloads: dict[str, dict[str, Any] | None] = {
        "long-memory": None,
        "retrieval": None,
    }

    if long_memory_release_cases is not None:
        artifact_path = release_dir / "longmemeval.release.json"
        release_payloads["long-memory"] = _write_json(
            artifact_path,
            run_long_memory_eval(
                long_memory_release_cases,
                config,
                min_pass_rate=long_memory_release_min_pass_rate,
            ),
        )
        if long_memory_baseline is not None:
            compare_payloads["long-memory"] = _write_json(
                release_dir / "longmemeval.compare.json",
                compare_eval_reports(long_memory_baseline, artifact_path),
            )

    if retrieval_release_cases is not None:
        artifact_path = release_dir / "hotpotqa.release.json"
        release_payloads["retrieval"] = _write_json(
            artifact_path,
            run_retrieval_eval(
                retrieval_release_cases,
                config,
                min_pass_rate=retrieval_release_min_pass_rate,
            ),
        )
        if retrieval_baseline is not None:
            compare_payloads["retrieval"] = _write_json(
                release_dir / "hotpotqa.compare.json",
                compare_eval_reports(retrieval_baseline, artifact_path),
            )

    summary = {
        "status": _summary_status(smoke_payloads, release_payloads, compare_payloads),
        "out_dir": str(out_dir),
        "release_capture_only": any(
            bool(payload is not None and payload.get("capture_only"))
            for payload in release_payloads.values()
        ),
        "smoke": {
            name: _summary_entry(local_dir / _smoke_filename(name), payload)
            for name, payload in smoke_payloads.items()
        },
        "release": {
            "long-memory": (
                _summary_entry(release_dir / "longmemeval.release.json", release_payloads["long-memory"])
                if release_payloads["long-memory"] is not None
                else None
            ),
            "retrieval": (
                _summary_entry(release_dir / "hotpotqa.release.json", release_payloads["retrieval"])
                if release_payloads["retrieval"] is not None
                else None
            ),
        },
        "compare": {
            "long-memory": (
                _summary_entry(release_dir / "longmemeval.compare.json", compare_payloads["long-memory"])
                if compare_payloads["long-memory"] is not None
                else None
            ),
            "retrieval": (
                _summary_entry(release_dir / "hotpotqa.compare.json", compare_payloads["retrieval"])
                if compare_payloads["retrieval"] is not None
                else None
            ),
        },
    }
    _write_json(out_dir / "summary.json", summary)
    return summary


def _write_json(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return payload


def _smoke_filename(name: str) -> str:
    if name == "inject":
        return "inject.smoke.json"
    return f"{name}.smoke.json"


def _summary_entry(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    capture_only = bool(payload.get("capture_only"))
    return {
        "artifact_path": str(path),
        "status": "captured" if capture_only else payload.get("status"),
        "eval_status": payload.get("status"),
        "capture_only": capture_only,
        "gate_passed": bool(payload.get("gate_passed", payload.get("status") == "ok")),
        "suite": payload.get("suite"),
        "cases_path": payload.get("cases_path"),
        "pass_rate": payload.get("pass_rate"),
        "min_pass_rate": payload.get("min_pass_rate"),
    }


def _summary_status(
    smoke_payloads: dict[str, dict[str, Any]],
    release_payloads: dict[str, dict[str, Any] | None],
    compare_payloads: dict[str, dict[str, Any] | None],
) -> str:
    for payload in smoke_payloads.values():
        if payload.get("status") != "ok":
            return "error"
    for payload in list(release_payloads.values()) + list(compare_payloads.values()):
        if payload is not None and payload.get("status") != "ok":
            return "error"
    return "ok"
