from __future__ import annotations

from pathlib import Path


def append_notice(repo_dir: Path, message: str) -> Path:
    path = repo_dir / "meta" / "NOTICE"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(message.rstrip() + "\n")
    return path
