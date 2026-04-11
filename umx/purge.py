from __future__ import annotations

from pathlib import Path

from umx.memory import iter_fact_files, read_fact_file, remove_fact
from umx.sessions import session_path


def purge_session(repo_dir: Path, session_id: str) -> dict:
    """Remove a session and all facts derived from it.

    1. Find all facts with source_session == session_id or provenance.sessions containing it
    2. Remove those facts from markdown files
    3. Delete the session JSONL file
    4. Return stats: {session_removed: bool, facts_removed: int, files_modified: list}
    """
    # Collect fact IDs to remove
    fact_ids_to_remove: list[str] = []
    for path in iter_fact_files(repo_dir):
        for fact in read_fact_file(path, repo_dir=repo_dir):
            if fact.source_session == session_id:
                fact_ids_to_remove.append(fact.fact_id)
            elif session_id in fact.provenance.sessions:
                fact_ids_to_remove.append(fact.fact_id)

    # Remove facts
    files_modified: set[str] = set()
    for fact_id in fact_ids_to_remove:
        removed = remove_fact(repo_dir, fact_id)
        if removed and removed.file_path:
            files_modified.add(str(removed.file_path))

    # Remove session file
    spath = session_path(repo_dir, session_id)
    session_removed = spath.exists()
    if session_removed:
        spath.unlink()

    return {
        "session_removed": session_removed,
        "facts_removed": len(fact_ids_to_remove),
        "files_modified": sorted(files_modified),
    }
