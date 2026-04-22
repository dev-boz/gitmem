from __future__ import annotations

from pathlib import Path

from umx.git_ops import git_blob_sha
from umx.models import Fact, SourceType


def _anchor_path(project_root: Path, fact: Fact) -> Path | None:
    if fact.code_anchor is None:
        return None
    anchor_path = project_root / fact.code_anchor.path
    try:
        anchor_path.resolve(strict=False).relative_to(project_root.resolve())
    except ValueError:
        return None
    return anchor_path


def anchor_current_sha(project_root: Path, fact: Fact) -> str | None:
    anchor_path = _anchor_path(project_root, fact)
    if anchor_path is None:
        return None
    if not anchor_path.is_file():
        return None
    return git_blob_sha(anchor_path)


def code_anchor_status(project_root: Path, fact: Fact) -> str | None:
    if fact.code_anchor is None:
        return None
    anchor_path = _anchor_path(project_root, fact)
    if anchor_path is None or not anchor_path.is_file():
        return "missing"
    if fact.source_type != SourceType.GROUND_TRUTH_CODE:
        return None
    current_sha = anchor_current_sha(project_root, fact)
    if fact.code_anchor.git_sha and current_sha and current_sha != fact.code_anchor.git_sha:
        return "drifted"
    return None


def code_anchor_is_stale(project_root: Path, fact: Fact) -> bool:
    return code_anchor_status(project_root, fact) is not None
