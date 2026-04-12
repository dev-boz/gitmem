from __future__ import annotations

import json
import re
from dataclasses import asdict
from dataclasses import dataclass
from pathlib import Path

from umx.memory import find_fact_by_id, load_all_facts, remove_fact
from umx.models import Fact, parse_datetime, utcnow


@dataclass(slots=True)
class Tombstone:
    fact_id: str | None
    match: str | None
    reason: str
    author: str
    created: str
    suppress_from: list[str]
    expires_at: str | None = None

    def expired(self) -> bool:
        if not self.expires_at:
            return False
        expires = parse_datetime(self.expires_at)
        return bool(expires and expires <= utcnow())


def tombstones_path(repo_dir: Path) -> Path:
    return repo_dir / "meta" / "tombstones.jsonl"


def load_tombstones(repo_dir: Path) -> list[Tombstone]:
    path = tombstones_path(repo_dir)
    if not path.exists():
        return []
    tombstones: list[Tombstone] = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        tombstones.append(Tombstone(**json.loads(line)))
    return tombstones


def append_tombstone(repo_dir: Path, tombstone: Tombstone) -> None:
    path = tombstones_path(repo_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(asdict(tombstone), sort_keys=True) + "\n")


def is_suppressed(fact: Fact, tombstones: list[Tombstone], phase: str = "gather") -> bool:
    for tombstone in tombstones:
        if tombstone.expired():
            continue
        if phase not in tombstone.suppress_from:
            continue
        if tombstone.fact_id and tombstone.fact_id == fact.fact_id:
            return True
        if tombstone.match and re.search(tombstone.match, fact.text):
            return True
    return False


def forget_fact(repo_dir: Path, fact_id: str, author: str = "human", reason: str | None = None) -> Fact | None:
    fact = find_fact_by_id(repo_dir, fact_id)
    if not fact:
        return None
    append_tombstone(
        repo_dir,
        Tombstone(
            fact_id=fact.fact_id,
            match=re.escape(fact.text),
            reason=reason or f"forgot fact {fact.fact_id}",
            author=author,
            created=utcnow().isoformat().replace("+00:00", "Z"),
            suppress_from=["gather", "rederive", "audit"],
        ),
    )
    remove_fact(repo_dir, fact_id)
    return fact


def forget_topic(repo_dir: Path, topic: str, author: str = "human", reason: str | None = None) -> list[Fact]:
    removed: list[Fact] = []
    for fact in [fact for fact in load_all_facts(repo_dir) if fact.topic == topic]:
        forgotten = forget_fact(repo_dir, fact.fact_id, author=author, reason=reason or f"forgot topic {topic}")
        if forgotten:
            removed.append(forgotten)
    return removed
