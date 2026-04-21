from __future__ import annotations

from pathlib import Path

from umx.config import UMXConfig
from umx.memory import (
    FACT_FILE_SCHEMA_VERSION,
    ensure_fact_file_schema_header_text,
    iter_fact_files,
    purge_fact_file_cache,
    read_fact_file_schema_version,
)

MIGRATION_ID = "0001_initial"


def can_apply(found_version: int) -> bool:
    return found_version < FACT_FILE_SCHEMA_VERSION


def apply(repo_dir: Path, *, config: UMXConfig | None = None) -> dict[str, object]:
    del config
    originals: dict[Path, str] = {}
    pending: dict[Path, str] = {}
    for path in iter_fact_files(repo_dir):
        found, _ = read_fact_file_schema_version(path)
        if found == FACT_FILE_SCHEMA_VERSION:
            continue
        original = path.read_text()
        updated = ensure_fact_file_schema_header_text(original, path=path)
        if updated == original:
            continue
        originals[path] = original
        pending[path] = updated

    changed_paths = sorted(pending, key=lambda item: item.relative_to(repo_dir).as_posix())
    written: list[Path] = []
    try:
        for path in changed_paths:
            path.write_text(pending[path])
            purge_fact_file_cache(path)
            written.append(path)
    except Exception:
        for path in written:
            path.write_text(originals[path])
            purge_fact_file_cache(path)
        raise

    relative_paths = [path.relative_to(repo_dir).as_posix() for path in changed_paths]
    return {
        "to_version": FACT_FILE_SCHEMA_VERSION,
        "applied": [
            f"set {relative_path} schema_version header to {FACT_FILE_SCHEMA_VERSION}"
            for relative_path in relative_paths
        ],
        "changed_files": relative_paths,
    }
