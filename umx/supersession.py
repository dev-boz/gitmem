from __future__ import annotations

from umx.memory import load_all_facts
from umx.models import Fact


def walk_history(repo_dir, fact_id: str) -> list[Fact]:
    by_id = {fact.fact_id: fact for fact in load_all_facts(repo_dir, include_superseded=True)}
    if fact_id not in by_id:
        return []
    start = by_id[fact_id]
    while start.supersedes and start.supersedes in by_id:
        start = by_id[start.supersedes]
    chain = [start]
    while chain[-1].superseded_by and chain[-1].superseded_by in by_id:
        chain.append(by_id[chain[-1].superseded_by])
    return chain
