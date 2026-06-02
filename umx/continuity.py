from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(slots=True, frozen=True)
class HandoverWriteResult:
    latest_path: Path
    archive_path: Path


def _now() -> datetime:
    return datetime.now(tz=UTC)


def diary_path(repo_dir: Path) -> Path:
    return repo_dir / "local" / "diary.md"


def handover_path(repo_dir: Path) -> Path:
    return repo_dir / "local" / "handover.md"


def handover_archive_dir(repo_dir: Path) -> Path:
    return repo_dir / "local" / "handovers"


def _bullet_lines(text: str) -> list[str]:
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    return [
        line if line.startswith(("- ", "* ")) else f"- {line}"
        for line in lines
    ]


def append_diary_entry(repo_dir: Path, text: str, *, now: datetime | None = None) -> Path:
    body_lines = _bullet_lines(text)
    if not body_lines:
        raise ValueError("diary entry must not be empty")
    stamp = (now or _now()).date().isoformat()
    path = diary_path(repo_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8").rstrip() if path.exists() else ""
    section_header = f"## {stamp}"
    if not existing:
        content = "# Diary\n\n" + section_header + "\n\n" + "\n".join(body_lines) + "\n"
    elif section_header in existing.splitlines():
        content = existing + "\n" + "\n".join(body_lines) + "\n"
    else:
        content = existing + "\n\n" + section_header + "\n\n" + "\n".join(body_lines) + "\n"
    path.write_text(content, encoding="utf-8")
    return path


def read_diary(repo_dir: Path) -> str:
    path = diary_path(repo_dir)
    return path.read_text(encoding="utf-8") if path.exists() else ""


def render_handover(text: str, *, now: datetime | None = None) -> str:
    stripped = text.strip()
    if not stripped:
        raise ValueError("handover text must not be empty")
    if stripped.startswith("# Handover"):
        return stripped.rstrip() + "\n"
    stamp = (now or _now()).isoformat().replace("+00:00", "Z")
    return f"# Handover — {stamp}\n\n## Active context\n{stripped}\n"


def write_handover(repo_dir: Path, text: str, *, now: datetime | None = None) -> HandoverWriteResult:
    stamp = now or _now()
    rendered = render_handover(text, now=stamp)
    latest = handover_path(repo_dir)
    archive = handover_archive_dir(repo_dir) / f"{stamp.date().isoformat()}.md"
    latest.parent.mkdir(parents=True, exist_ok=True)
    archive.parent.mkdir(parents=True, exist_ok=True)
    latest.write_text(rendered, encoding="utf-8")
    archive.write_text(rendered, encoding="utf-8")
    return HandoverWriteResult(latest_path=latest, archive_path=archive)


def read_handover(repo_dir: Path) -> str:
    path = handover_path(repo_dir)
    return path.read_text(encoding="utf-8") if path.exists() else ""


def list_handover_paths(repo_dir: Path) -> list[Path]:
    paths: list[Path] = []
    latest = handover_path(repo_dir)
    if latest.exists():
        paths.append(latest)
    archive_dir = handover_archive_dir(repo_dir)
    if archive_dir.exists():
        paths.extend(sorted(path for path in archive_dir.glob("*.md") if path.is_file()))
    return sorted({path.resolve(): path for path in paths}.values(), key=lambda path: path.as_posix())
