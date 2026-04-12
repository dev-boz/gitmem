from __future__ import annotations

from pathlib import Path

from umx.config import UMXConfig, default_config
from umx.dream.extract import session_records_to_facts
from umx.memory import load_all_facts
from umx.models import Fact
from umx.sessions import iter_session_payloads


def rederive_from_sessions(
    repo_dir: Path,
    session_ids: list[str] | None = None,
    config: UMXConfig | None = None,
) -> list[Fact]:
    """Re-extract facts from raw sessions.

    If session_ids is None, re-derive from all sessions.
    Returns newly extracted facts (does not commit — caller decides).
    """
    return session_records_to_facts(
        repo_dir,
        config,
        include_archived=True,
        session_ids=set(session_ids) if session_ids else None,
        skip_gathered=False,
    )


def compare_derived(existing: list[Fact], rederived: list[Fact]) -> dict:
    """Compare existing facts against rederived ones.

    Uses text-based matching to find correspondences.
    """
    existing_texts = {f.text: f for f in existing}
    rederived_texts = {f.text: f for f in rederived}

    matching = 0
    divergent: list[dict] = []

    existing_matched: set[str] = set()
    rederived_matched: set[str] = set()

    # Match by text equality
    for text in existing_texts:
        if text in rederived_texts:
            matching += 1
            existing_matched.add(text)
            rederived_matched.add(text)

    # Facts in existing but not in rederived
    missing_from_rederived = len(existing_texts) - len(existing_matched)

    # Facts in rederived but not in existing
    missing_from_existing = len(rederived_texts) - len(rederived_matched)

    return {
        "matching": matching,
        "divergent": divergent,
        "missing_from_existing": missing_from_existing,
        "missing_from_rederived": missing_from_rederived,
    }


def audit_report(repo_dir: Path, config: UMXConfig) -> dict:
    """Full audit: rederive all facts and compare.

    Returns report dict with statistics and divergences.
    """
    existing = load_all_facts(repo_dir, include_superseded=False)
    sessions = list(iter_session_payloads(repo_dir, include_archived=True))

    # Source type breakdown
    source_types: dict[str, int] = {}
    for fact in existing:
        key = fact.source_type.value
        source_types[key] = source_types.get(key, 0) + 1

    # Provenance stats
    sessions_with_facts: set[str] = set()
    for fact in existing:
        if fact.source_session and fact.source_session != "manual":
            sessions_with_facts.add(fact.source_session)

    report: dict = {
        "total_facts": len(existing),
        "total_sessions": len(sessions),
        "sessions_with_derived_facts": len(sessions_with_facts),
        "source_types": source_types,
    }
    return report
