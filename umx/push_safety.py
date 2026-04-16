from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from umx.config import UMXConfig, default_config
from umx.git_ops import (
    diff_committed_paths_between_refs_strict,
    git_read_text_at_ref_strict,
    git_ref_exists,
)
from umx.redaction import redact_text


@dataclass(slots=True)
class PushSafetyFinding:
    path: str
    kind: str
    detail: str


@dataclass(slots=True)
class PushSafetyReport:
    branch: str
    base_ref: str
    findings: list[PushSafetyFinding] = field(default_factory=list)
    quarantine_path: str | None = None


class PushSafetyError(RuntimeError):
    def __init__(self, report: PushSafetyReport):
        self.report = report
        preview = ", ".join(
            f"{finding.path} ({finding.kind})"
            for finding in report.findings[:5]
        )
        if len(report.findings) > 5:
            preview += f", ... (+{len(report.findings) - 5} more)"
        message = f"push safety blocked {report.branch} push: {preview}"
        if report.quarantine_path:
            message += f"; details saved to {report.quarantine_path}"
        super().__init__(message)


def _quarantine_report_path(repo_dir: Path) -> Path:
    stamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    path = repo_dir / "local" / "quarantine" / f"push-safety-{stamp}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _write_quarantine_report(repo_dir: Path, report: PushSafetyReport) -> Path:
    path = _quarantine_report_path(repo_dir)
    payload = {
        "branch": report.branch,
        "base_ref": report.base_ref,
        "findings": [asdict(finding) for finding in report.findings],
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return path


def _is_memory_scan_target(relative: str) -> bool:
    return (
        relative == "meta/MEMORY.md"
        or (
            relative.endswith(".md")
            and (
                relative.startswith("facts/")
                or relative.startswith("episodic/")
                or relative.startswith("principles/")
            )
        )
    )


def _snapshot_scan_paths(repo_dir: Path, config: UMXConfig) -> list[Path]:
    paths = [
        *repo_dir.glob("facts/**/*.md"),
        *repo_dir.glob("episodic/**/*.md"),
        *repo_dir.glob("principles/**/*.md"),
    ]
    memory_path = repo_dir / "meta" / "MEMORY.md"
    if memory_path.exists():
        paths.append(memory_path)
    if config.sessions.redaction == "none":
        paths.extend(repo_dir.glob("sessions/**/*.jsonl"))
    return paths


def scan_text(path: str, text: str, config: UMXConfig) -> list[PushSafetyFinding]:
    result = redact_text(text, config)
    counts = Counter(issue.kind for issue in result.issues)
    return [
        PushSafetyFinding(
            path=path,
            kind=kind,
            detail=f"{count} hit(s)",
        )
        for kind, count in sorted(counts.items())
    ]


def validate_push(
    repo_dir: Path,
    *,
    project_root: Path | None = None,
    base_ref: str | None = "origin/main",
    branch: str = "main",
    head_ref: str = "HEAD",
    config: UMXConfig | None = None,
    include_bridge: bool = False,
) -> PushSafetyReport:
    cfg = config or default_config()
    report = PushSafetyReport(branch=branch, base_ref=base_ref or "repo-snapshot")

    if base_ref is None:
        committed_paths = _snapshot_scan_paths(repo_dir, cfg)
    else:
        if not git_ref_exists(repo_dir, base_ref):
            raise RuntimeError(f"missing base ref {base_ref}")
        committed_paths = diff_committed_paths_between_refs_strict(repo_dir, base_ref, head_ref)

    for path in committed_paths:
        relative = path.relative_to(repo_dir).as_posix()
        if cfg.sessions.redaction == "none" and relative.startswith("sessions/") and path.suffix == ".jsonl":
            report.findings.append(
                PushSafetyFinding(
                    path=relative,
                    kind="raw-session-push",
                    detail="sessions.redaction=none cannot be pushed",
                )
            )
            continue
        if not _is_memory_scan_target(relative):
            continue
        if head_ref == "HEAD":
            if not path.exists():
                continue
            content = path.read_text()
        else:
            content = git_read_text_at_ref_strict(repo_dir, head_ref, relative)
        report.findings.extend(scan_text(relative, content, cfg))

    if include_bridge and project_root is not None and cfg.bridge.enabled:
        for target in cfg.bridge.targets:
            path = project_root / target
            if not path.exists():
                continue
            relative = path.relative_to(project_root).as_posix()
            report.findings.extend(scan_text(relative, path.read_text(), cfg))

    return report


def assert_push_safe(
    repo_dir: Path,
    *,
    project_root: Path | None = None,
    base_ref: str | None = "origin/main",
    branch: str = "main",
    head_ref: str = "HEAD",
    config: UMXConfig | None = None,
    include_bridge: bool = False,
) -> None:
    try:
        report = validate_push(
            repo_dir,
            project_root=project_root,
            base_ref=base_ref,
            branch=branch,
            head_ref=head_ref,
            config=config,
            include_bridge=include_bridge,
        )
    except Exception as exc:  # pragma: no cover - defensive fail-closed path
        report = PushSafetyReport(
            branch=branch,
            base_ref=base_ref,
            findings=[
                PushSafetyFinding(
                    path="push-safety",
                    kind="scan-error",
                    detail=exc.__class__.__name__,
                )
            ],
        )
        report.quarantine_path = str(_write_quarantine_report(repo_dir, report))
        raise PushSafetyError(report) from exc

    if not report.findings:
        return
    report.quarantine_path = str(_write_quarantine_report(repo_dir, report))
    raise PushSafetyError(report)
