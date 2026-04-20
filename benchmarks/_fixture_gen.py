from __future__ import annotations

import gzip
import json
import os
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from umx.git_ops import git_add_and_commit, raise_for_git_commit_failure
from umx.memory import save_repository_facts
from umx.models import (
    ConsolidationStatus,
    Fact,
    MemoryType,
    Provenance,
    Scope,
    SourceType,
    Verification,
)
from umx.scope import init_project_memory
from umx.search import rebuild_index
from umx.sessions import archive_path, session_index_path, write_session

_SEED_DIR = Path(__file__).parent / "fixtures" / "seed"
_BENCHMARK_START = datetime(2025, 1, 1, tzinfo=UTC)
_ACTIVE_SESSION_START = datetime(2026, 1, 1, tzinfo=UTC)


@dataclass(slots=True, frozen=True)
class RepoScale:
    fact_count: int
    active_session_count: int = 0
    archived_session_count: int = 0

    @property
    def total_sessions(self) -> int:
        return self.active_session_count + self.archived_session_count


@dataclass(slots=True, frozen=True)
class PreparedRepo:
    slug: str
    project_dir: Path
    project_repo: Path
    scale: RepoScale


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(0, value)


def inject_scale() -> RepoScale:
    return RepoScale(fact_count=_env_int("UMX_BENCH_FACTS", 10_000))


def dream_scale() -> RepoScale:
    total_sessions = _env_int("UMX_BENCH_SESSIONS", 100_000)
    active_sessions = min(_env_int("UMX_BENCH_ACTIVE_SESSIONS", 128), total_sessions)
    return RepoScale(
        fact_count=_env_int("UMX_BENCH_FACTS", 10_000),
        active_session_count=active_sessions,
        archived_session_count=total_sessions - active_sessions,
    )


def ingest_batch_count() -> int:
    return max(1, _env_int("UMX_BENCH_INGEST_BATCH", 50))


def _seed_facts() -> list[dict[str, str]]:
    payload = json.loads((_SEED_DIR / "facts.json").read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("benchmark fact seed must be a list")
    seeds: list[dict[str, str]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        topic = str(item.get("topic") or "").strip()
        text = str(item.get("text") or "").strip()
        if topic and text:
            seeds.append({"topic": topic, "text": text})
    if not seeds:
        raise ValueError("benchmark fact seed is empty")
    return seeds


def _seed_session_payload() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rows = [
        json.loads(line)
        for line in (_SEED_DIR / "session.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not rows or "_meta" not in rows[0]:
        raise ValueError("benchmark session seed must start with a _meta row")
    meta = dict(rows[0]["_meta"])
    events = [dict(row) for row in rows[1:] if isinstance(row, dict)]
    if not events:
        raise ValueError("benchmark session seed must include events")
    return meta, events


def _project_dir(root: Path, slug: str) -> Path:
    project_dir = root / slug
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / ".git").mkdir(exist_ok=True)
    return project_dir


def _make_fact(seed: dict[str, str], repo_name: str, index: int, *, bucket_count: int) -> Fact:
    bucket = index % max(1, bucket_count)
    created = _BENCHMARK_START + timedelta(minutes=index)
    return Fact(
        fact_id=f"01BENCHFACT{index:015d}",
        text=(
            f"{seed['text']} Benchmark fact {index} for {seed['topic']} bucket {bucket}. "
            f"Keywords: staging smoke test rollback postgres search."
        ),
        scope=Scope.PROJECT,
        topic=f"{seed['topic']}-{bucket:03d}",
        encoding_strength=4,
        memory_type=MemoryType.EXPLICIT_SEMANTIC,
        verification=Verification.CORROBORATED,
        source_type=SourceType.GROUND_TRUTH_CODE,
        confidence=0.9,
        source_tool="benchmark-fixture",
        source_session=f"bench-fact-{index:06d}",
        consolidation_status=ConsolidationStatus.STABLE,
        provenance=Provenance(extracted_by="benchmark-fixture"),
        created=created,
        repo=repo_name,
    )


def _session_payload(
    slug: str,
    index: int,
    *,
    started: datetime,
    seed_meta: dict[str, Any],
    seed_events: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    session_id = f"{started.date().isoformat()}-bench-{index:06d}"
    meta = dict(seed_meta)
    meta.update(
        {
            "session_id": session_id,
            "tool": "benchmark",
            "project": slug,
            "source": "benchmark-seed",
            "started": started.isoformat().replace("+00:00", "Z"),
            "ended": (started + timedelta(minutes=2)).isoformat().replace("+00:00", "Z"),
            "duration_seconds": 120,
        }
    )
    events: list[dict[str, Any]] = []
    for event_index, event in enumerate(seed_events):
        mutated = dict(event)
        content = str(mutated.get("content") or "").strip()
        mutated["content"] = (
            f"{content} Benchmark session {index} topic {index % 17} "
            f"variant {event_index}."
        ).strip()
        events.append(mutated)
    return meta, events


def _write_archived_sessions(repo_dir: Path, slug: str, count: int) -> None:
    if count <= 0:
        return
    seed_meta, seed_events = _seed_session_payload()
    current_month: tuple[str, str] | None = None
    archive_handle = None
    try:
        for offset in range(count):
            started = _BENCHMARK_START + timedelta(minutes=offset * 5)
            year = started.strftime("%Y")
            month = started.strftime("%m")
            month_key = (year, month)
            if current_month != month_key:
                if archive_handle is not None:
                    archive_handle.close()
                archive_file = archive_path(repo_dir, year, month)
                archive_file.parent.mkdir(parents=True, exist_ok=True)
                archive_handle = gzip.open(archive_file, "wt", encoding="utf-8")
                session_index_path(repo_dir, year, month).write_text("{}\n", encoding="utf-8")
                current_month = month_key
            meta, events = _session_payload(
                slug,
                offset,
                started=started,
                seed_meta=seed_meta,
                seed_events=seed_events,
            )
            payload = [{"_meta": meta}, *events]
            record = {"session_id": meta["session_id"], "payload": payload}
            archive_handle.write(json.dumps(record, sort_keys=True) + "\n")
    finally:
        if archive_handle is not None:
            archive_handle.close()


def _write_active_sessions(repo_dir: Path, slug: str, count: int) -> None:
    if count <= 0:
        return
    seed_meta, seed_events = _seed_session_payload()
    for offset in range(count):
        started = _ACTIVE_SESSION_START + timedelta(minutes=offset * 7)
        meta, events = _session_payload(
            slug,
            100_000 + offset,
            started=started,
            seed_meta=seed_meta,
            seed_events=seed_events,
        )
        write_session(repo_dir, meta, events, auto_commit=False)


def build_prepared_repo(root: Path, *, slug: str, scale: RepoScale) -> PreparedRepo:
    project_dir = _project_dir(root, slug)
    project_repo = init_project_memory(project_dir, slug=slug)
    seeds = _seed_facts()
    bucket_count = min(64, max(8, scale.fact_count // 500)) if scale.fact_count else 1
    facts = [
        _make_fact(seeds[index % len(seeds)], project_repo.name, index, bucket_count=bucket_count)
        for index in range(scale.fact_count)
    ]
    if facts:
        save_repository_facts(project_repo, facts, auto_commit=False)
    _write_active_sessions(project_repo, slug, scale.active_session_count)
    _write_archived_sessions(project_repo, slug, scale.archived_session_count)
    commit = git_add_and_commit(project_repo, message="umx: benchmark fixture")
    raise_for_git_commit_failure(commit, context="benchmark fixture commit failed")
    if scale.fact_count:
        rebuild_index(project_repo)
    return PreparedRepo(
        slug=slug,
        project_dir=project_dir,
        project_repo=project_repo,
        scale=scale,
    )


def clone_prepared_repo(prepared: PreparedRepo, root: Path, *, slug: str) -> PreparedRepo:
    project_dir = _project_dir(root, slug)
    project_repo = init_project_memory(project_dir, slug=slug)
    shutil.rmtree(project_repo)
    shutil.copytree(prepared.project_repo, project_repo)
    (project_dir / ".umx-project").write_text(f"{slug}\n", encoding="utf-8")
    return PreparedRepo(
        slug=slug,
        project_dir=project_dir,
        project_repo=project_repo,
        scale=prepared.scale,
    )
