""".umx/NOTICE writer for degradation alerts.

Surfaces status to tools at session start.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from umx.models import DreamStatus


def write_notice(umx_dir: Path, message: str) -> None:
    """Append a notice to .umx/NOTICE."""
    notice_path = umx_dir / "NOTICE"
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"[{now}] {message}\n"

    with notice_path.open("a") as f:
        f.write(line)


def clear_notice(umx_dir: Path) -> None:
    """Clear the notice file after a successful full dream."""
    notice_path = umx_dir / "NOTICE"
    if notice_path.exists():
        notice_path.unlink()


def read_notice(umx_dir: Path) -> str | None:
    """Read the current notice, if any."""
    notice_path = umx_dir / "NOTICE"
    if notice_path.exists():
        return notice_path.read_text().strip()
    return None


def write_dream_log(
    umx_dir: Path,
    status: DreamStatus,
    facts_added: int = 0,
    facts_removed: int = 0,
    facts_conflicted: int = 0,
    provider: str = "",
    skipped_sources: list[str] | None = None,
    error: str = "",
) -> None:
    """Write dream status to .umx/dream.log."""
    log_path = umx_dir / "dream.log"
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    lines = [
        f"[{now}] Dream status: {status.value}",
        f"  Facts added: {facts_added}",
        f"  Facts removed: {facts_removed}",
        f"  Facts conflicted: {facts_conflicted}",
    ]
    if provider:
        lines.append(f"  Provider: {provider}")
    if skipped_sources:
        lines.append(f"  Skipped sources: {', '.join(skipped_sources)}")
    if error:
        lines.append(f"  Error: {error}")
    lines.append("")

    with log_path.open("a") as f:
        f.write("\n".join(lines) + "\n")
