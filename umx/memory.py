from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from umx.config import UMXConfig, default_config, load_config
from umx.inline_metadata import parse_inline_metadata, serialize_inline_metadata
from umx.identity import generate_fact_id
from umx.models import (
    AppliesTo,
    CodeAnchor,
    ConsolidationStatus,
    Fact,
    MemoryType,
    Provenance,
    Scope,
    SourceType,
    TaskStatus,
    Verification,
    fact_from_dict,
    parse_datetime,
    utcnow,
)
from umx.schema import CURRENT_SCHEMA_VERSION


VERIFICATION_SHORT = {
    Verification.SELF_REPORTED: "sr",
    Verification.CORROBORATED: "cor",
    Verification.SOTA_REVIEWED: "sota",
    Verification.HUMAN_CONFIRMED: "hum",
}
VERIFICATION_LONG = {value: key for key, value in VERIFICATION_SHORT.items()}
FACT_PREFIX_RE = re.compile(r"^\[S:(?P<strength>\d+)\|V:(?P<verification>[a-z-]+)\]\s*")
_FACT_FILE_CACHE: dict[
    tuple[Path, bool],
    tuple[tuple[int, int, str, int, int, str], tuple[dict[str, Any], ...]],
] = {}


def _auto_commit_or_raise(repo_dir: Path, *, paths: list[Path] | None = None, message: str) -> None:
    from umx.git_ops import git_add_and_commit, git_commit_failure_message
    from umx.scope import config_path

    result = git_add_and_commit(
        repo_dir,
        paths=paths,
        message=message,
        config=load_config(config_path()),
    )
    if result.failed:
        raise RuntimeError(git_commit_failure_message(result, context="commit failed"))


def _kind_scope_memory_type(path: Path, repo_dir: Path) -> tuple[Scope, MemoryType, str]:
    relative = path.relative_to(repo_dir)
    parts = relative.parts
    repo_default_scope = Scope.USER if repo_dir.parent.name != "projects" else Scope.PROJECT
    if parts[:2] == ("facts", "topics"):
        return repo_default_scope, MemoryType.EXPLICIT_SEMANTIC, path.stem
    if parts[:2] == ("episodic", "topics"):
        return repo_default_scope, MemoryType.EXPLICIT_EPISODIC, path.stem
    if parts[:2] == ("principles", "topics"):
        return repo_default_scope, MemoryType.EXPLICIT_SEMANTIC, path.stem
    if parts[:2] == ("local", "private"):
        return Scope.PROJECT_PRIVATE, MemoryType.EXPLICIT_SEMANTIC, path.stem
    if parts[:2] == ("local", "secret"):
        return Scope.PROJECT_SECRET, MemoryType.EXPLICIT_SEMANTIC, path.stem
    if parts[0] == "tools":
        return Scope.TOOL, MemoryType.EXPLICIT_SEMANTIC, path.stem
    if parts[0] == "machines":
        return Scope.MACHINE, MemoryType.EXPLICIT_SEMANTIC, path.stem
    if parts[0] == "folders":
        return Scope.FOLDER, MemoryType.EXPLICIT_SEMANTIC, path.stem
    if parts[0] == "files":
        return Scope.FILE, MemoryType.EXPLICIT_SEMANTIC, path.stem
    return Scope.PROJECT, MemoryType.EXPLICIT_SEMANTIC, path.stem


def topic_path(repo_dir: Path, topic: str, kind: str = "facts") -> Path:
    return repo_dir / kind / "topics" / f"{topic}.md"


def cache_path_for(markdown_path: Path) -> Path:
    return markdown_path.with_suffix(".umx.json")


def _compact_metadata(fact: Fact) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": fact.fact_id,
        "conf": round(fact.confidence, 4),
        "cort": list(fact.corroborated_by_tools),
        "corf": list(fact.corroborated_by_facts),
        "src": fact.source_tool,
        "xby": fact.provenance.extracted_by,
        "ss": fact.source_session,
        "st": fact.source_type.value,
        "cr": fact.created.isoformat().replace("+00:00", "Z"),
        "v": fact.verification.value,
        "cs": fact.consolidation_status.value,
    }
    if fact.provenance.pr:
        data["pr"] = fact.provenance.pr
    if fact.provenance.approved_by:
        data["aby"] = fact.provenance.approved_by
    if fact.provenance.approval_tier:
        data["tier"] = fact.provenance.approval_tier
    if fact.conflicts_with:
        data["cw"] = list(fact.conflicts_with)
    if fact.supersedes:
        data["sup"] = fact.supersedes
    if fact.superseded_by:
        data["sby"] = fact.superseded_by
    if fact.task_status:
        data["ts"] = fact.task_status.value
    if fact.expires_at:
        data["ex"] = fact.expires_at.isoformat().replace("+00:00", "Z")
    if fact.applies_to:
        data["at"] = fact.applies_to.to_dict()
    if fact.code_anchor:
        data["ca"] = fact.code_anchor.to_dict()
    return data


def format_fact_line(fact: Fact) -> str:
    verification = VERIFICATION_SHORT.get(fact.verification, fact.verification.value)
    metadata = serialize_inline_metadata(_compact_metadata(fact))
    return f"- [S:{fact.encoding_strength}|V:{verification}] {fact.text} <!-- umx:{metadata} -->"


def _cache_payload(facts: list[Fact], existing: dict[str, Any] | None = None) -> dict[str, Any]:
    existing_facts = (existing or {}).get("facts", {})
    payload: dict[str, Any] = {"facts": {}}
    for fact in facts:
        preserved = {
            key: value
            for key, value in existing_facts.get(fact.fact_id, {}).items()
            if key not in {"text", "created"}
        }
        payload["facts"][fact.fact_id] = {
            "text": fact.text,
            "created": fact.created.isoformat().replace("+00:00", "Z"),
            **preserved,
        }
    return payload


def _load_cache(path: Path) -> dict[str, Any]:
    cache_path = cache_path_for(path)
    if not cache_path.exists():
        return {"facts": {}}
    return json.loads(cache_path.read_text())


def _save_cache(path: Path, facts: list[Fact]) -> None:
    cache_path = cache_path_for(path)
    existing = _load_cache(path)
    cache_path.write_text(json.dumps(_cache_payload(facts, existing), indent=2, sort_keys=True))


def _fact_cache_key(path: Path, *, normalize: bool) -> tuple[Path, bool]:
    return path.resolve(), normalize


def _content_digest(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def _fact_cache_fingerprint(path: Path) -> tuple[int, int, str, int, int, str]:
    source_stat = path.stat()
    source_digest = _content_digest(path)
    cache_path = cache_path_for(path)
    if cache_path.exists():
        cache_stat = cache_path.stat()
        cache_digest = _content_digest(cache_path)
        return (
            source_stat.st_mtime_ns,
            source_stat.st_size,
            source_digest,
            cache_stat.st_mtime_ns,
            cache_stat.st_size,
            cache_digest,
        )
    return (
        source_stat.st_mtime_ns,
        source_stat.st_size,
        source_digest,
        -1,
        -1,
        "",
    )


def _purge_fact_file_cache(path: Path) -> None:
    resolved = path.resolve()
    _FACT_FILE_CACHE.pop((resolved, True), None)
    _FACT_FILE_CACHE.pop((resolved, False), None)


def _parse_verification(value: str) -> Verification:
    if value in VERIFICATION_LONG:
        return VERIFICATION_LONG[value]
    for verification in Verification:
        if verification.value == value:
            return verification
    raise ValueError(f"unknown verification value: {value}")


def parse_fact_line(line: str, *, repo_dir: Path, path: Path) -> Fact | None:
    stripped = line.strip()
    if not stripped.startswith("- "):
        return None
    content = stripped[2:].strip()
    text_part, metadata = parse_inline_metadata(content)

    prefix = FACT_PREFIX_RE.match(text_part)
    if prefix:
        strength = int(prefix.group("strength"))
        verification = _parse_verification(prefix.group("verification"))
        text = text_part[prefix.end() :].strip()
    else:
        strength = 5
        verification = Verification.HUMAN_CONFIRMED
        text = text_part.strip()

    if text.startswith("[DEPRECATED]"):
        text = text.replace("[DEPRECATED]", "", 1).strip()

    scope, memory_type, derived_topic = _kind_scope_memory_type(path, repo_dir)
    fact = Fact(
        fact_id=metadata.get("id", generate_fact_id()),
        text=text,
        scope=scope,
        topic=derived_topic,
        encoding_strength=strength,
        memory_type=memory_type,
        verification=Verification(metadata.get("v", verification.value)),
        source_type=SourceType(metadata.get("st", SourceType.USER_PROMPT.value)),
        confidence=float(metadata.get("conf", 1.0)),
        tags=[],
        source_tool=metadata.get("src", "manual"),
        source_session=metadata.get("ss", "manual"),
        corroborated_by_tools=list(metadata.get("cort", [])),
        corroborated_by_facts=list(metadata.get("corf", [])),
        conflicts_with=list(metadata.get("cw", [])),
        supersedes=metadata.get("sup"),
        superseded_by=metadata.get("sby"),
        consolidation_status=ConsolidationStatus(
            metadata.get("cs", ConsolidationStatus.STABLE.value if strength >= 5 else ConsolidationStatus.FRAGILE.value)
        ),
        task_status=TaskStatus(metadata["ts"]) if metadata.get("ts") else None,
        created=parse_datetime(metadata.get("cr")) or utcnow(),
        expires_at=parse_datetime(metadata.get("ex")),
        applies_to=AppliesTo.from_dict(metadata.get("at")),
        provenance=Provenance(
            extracted_by=metadata.get("xby", "manual"),
            approved_by=metadata.get("aby"),
            approval_tier=metadata.get("tier"),
            pr=metadata.get("pr"),
            sessions=[metadata["ss"]] if metadata.get("ss") else [],
        ),
        code_anchor=CodeAnchor.from_dict(metadata.get("ca")),
        repo=repo_dir.name,
        file_path=path,
    )
    if not metadata:
        fact.encoding_strength = 5
        fact.verification = Verification.HUMAN_CONFIRMED
        fact.source_type = SourceType.USER_PROMPT
        fact.source_tool = "human"
        fact.source_session = "manual-edit"
        fact.consolidation_status = ConsolidationStatus.STABLE
    return fact


def _parse_fact_lines(path: Path, repo_dir: Path) -> list[Fact]:
    return [
        fact
        for line in path.read_text().splitlines()
        if (fact := parse_fact_line(line, repo_dir=repo_dir, path=path))
    ]


def read_fact_file(
    path: Path,
    repo_dir: Path,
    normalize: bool = True,
    *,
    use_cache: bool = True,
) -> list[Fact]:
    if not path.exists():
        return []
    cache_key = _fact_cache_key(path, normalize=normalize)
    fingerprint = _fact_cache_fingerprint(path)
    if use_cache:
        cached = _FACT_FILE_CACHE.get(cache_key)
        if cached is not None and cached[0] == fingerprint:
            return [fact_from_dict(dict(item)) for item in cached[1]]
    parsed = _parse_fact_lines(path, repo_dir)
    if not use_cache:
        return parsed
    cache = _load_cache(path)
    cached_facts = cache.get("facts", {})
    rewritten: list[Fact] = []
    changed = False
    for fact in parsed:
        cached = cached_facts.get(fact.fact_id)
        if cached and cached.get("text") != fact.text:
            changed = True
            previous = fact.clone(text=cached["text"], superseded_by=None)
            new_id = generate_fact_id()
            previous.superseded_by = new_id
            rewritten.append(previous)
            rewritten.append(
                fact.clone(
                    fact_id=new_id,
                    encoding_strength=5,
                    verification=Verification.HUMAN_CONFIRMED,
                    source_type=SourceType.USER_PROMPT,
                    source_tool="human",
                    source_session="manual-edit",
                    consolidation_status=ConsolidationStatus.STABLE,
                    supersedes=fact.fact_id,
                    superseded_by=None,
                    provenance=Provenance(extracted_by="manual", sessions=["manual-edit"]),
                )
            )
        else:
            rewritten.append(fact)
            if fact.source_session == "manual-edit":
                changed = True
    if normalize and (changed or any(fact.source_session == "manual-edit" for fact in rewritten)):
        write_fact_file(path, rewritten, repo_dir=repo_dir)
        fingerprint = _fact_cache_fingerprint(path)
    _FACT_FILE_CACHE[cache_key] = (
        fingerprint,
        tuple(fact.to_dict() for fact in rewritten),
    )
    return [fact_from_dict(fact.to_dict()) for fact in rewritten]


def write_fact_file(path: Path, facts: list[Fact], repo_dir: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(
        facts,
        key=lambda fact: (
            fact.topic,
            fact.created,
            fact.fact_id,
        ),
    )
    header = f"# {path.stem}\n\n## Facts\n"
    body = "\n".join(format_fact_line(fact) for fact in ordered)
    path.write_text(f"{header}{body}\n" if body else header)
    _save_cache(path, ordered)
    _purge_fact_file_cache(path)


def add_fact(
    repo_dir: Path,
    fact: Fact,
    kind: str = "facts",
    *,
    auto_commit: bool = True,
    normalize: bool = True,
) -> Path:
    path = target_path_for_fact(repo_dir, fact)
    if fact.scope in {Scope.PROJECT, Scope.USER} and fact.memory_type == MemoryType.EXPLICIT_EPISODIC:
        path = topic_path(repo_dir, fact.topic, kind="episodic")
    elif fact.scope in {Scope.PROJECT, Scope.USER} and kind != "facts":
        path = topic_path(repo_dir, fact.topic, kind=kind)
    facts = read_fact_file(path, repo_dir=repo_dir, normalize=normalize)
    facts.append(fact.clone(repo=repo_dir.name, file_path=path))
    write_fact_file(path, facts, repo_dir=repo_dir)
    if auto_commit:
        _auto_commit_or_raise(
            repo_dir,
            paths=[path, cache_path_for(path)],
            message=f"umx: add fact to {path.stem}",
        )
    return path


def append_fact_preserving_existing(
    repo_dir: Path,
    fact: Fact,
    kind: str = "facts",
) -> Path:
    path = target_path_for_fact(repo_dir, fact)
    if fact.scope in {Scope.PROJECT, Scope.USER} and fact.memory_type == MemoryType.EXPLICIT_EPISODIC:
        path = topic_path(repo_dir, fact.topic, kind="episodic")
    elif fact.scope in {Scope.PROJECT, Scope.USER} and kind != "facts":
        path = topic_path(repo_dir, fact.topic, kind=kind)
    path.parent.mkdir(parents=True, exist_ok=True)
    materialized = fact.clone(repo=repo_dir.name, file_path=path)
    current = path.read_text() if path.exists() else ""
    existing_facts = _parse_fact_lines(path, repo_dir) if current else []
    if current:
        separator = "" if current.endswith("\n") else "\n"
        path.write_text(f"{current}{separator}{format_fact_line(materialized)}\n")
    else:
        path.write_text(
            f"# {path.stem}\n\n## Facts\n{format_fact_line(materialized)}\n"
        )
    _save_cache(path, [*existing_facts, materialized])
    _purge_fact_file_cache(path)
    return path


def load_all_facts(
    repo_dir: Path,
    include_superseded: bool = True,
    *,
    normalize: bool = True,
    use_cache: bool = True,
) -> list[Fact]:
    facts: list[Fact] = []
    for path in iter_fact_files(repo_dir):
        facts.extend(
            read_fact_file(
                path,
                repo_dir=repo_dir,
                normalize=normalize,
                use_cache=use_cache,
            )
        )
    if include_superseded:
        return facts
    return [fact for fact in facts if fact.superseded_by is None]


def iter_fact_files(repo_dir: Path) -> list[Path]:
    patterns = [
        "facts/topics/*.md",
        "episodic/topics/*.md",
        "principles/topics/*.md",
        "local/private/*.md",
        "local/secret/*.md",
        "tools/*.md",
        "machines/*.md",
        "folders/*.md",
        "files/*.md",
    ]
    files: list[Path] = []
    for pattern in patterns:
        files.extend(sorted(repo_dir.glob(pattern)))
    return files


def find_fact_by_id(repo_dir: Path, fact_id: str) -> Fact | None:
    for fact in load_all_facts(repo_dir, include_superseded=True):
        if fact.fact_id == fact_id:
            return fact
    return None


def replace_fact(repo_dir: Path, updated: Fact) -> bool:
    for path in iter_fact_files(repo_dir):
        facts = read_fact_file(path, repo_dir=repo_dir)
        replaced = False
        for index, fact in enumerate(facts):
            if fact.fact_id == updated.fact_id:
                facts[index] = updated.clone(file_path=path, repo=repo_dir.name)
                replaced = True
        if replaced:
            write_fact_file(path, facts, repo_dir=repo_dir)
            return True
    return False


def remove_fact(repo_dir: Path, fact_id: str) -> Fact | None:
    for path in iter_fact_files(repo_dir):
        facts = read_fact_file(path, repo_dir=repo_dir)
        kept = [fact for fact in facts if fact.fact_id != fact_id]
        if len(kept) != len(facts):
            removed = next(fact for fact in facts if fact.fact_id == fact_id)
            write_fact_file(path, kept, repo_dir=repo_dir)
            return removed
    return None


def target_path_for_fact(repo_dir: Path, fact: Fact) -> Path:
    if fact.file_path and repo_dir in fact.file_path.parents:
        return fact.file_path
    if fact.scope == Scope.PROJECT_PRIVATE:
        return repo_dir / "local" / "private" / f"{fact.topic}.md"
    if fact.scope == Scope.PROJECT_SECRET:
        return repo_dir / "local" / "secret" / f"{fact.topic}.md"
    if fact.scope == Scope.USER:
        return repo_dir / "facts" / "topics" / f"{fact.topic}.md"
    if fact.scope == Scope.TOOL:
        return repo_dir / "tools" / f"{fact.topic}.md"
    if fact.scope == Scope.MACHINE:
        return repo_dir / "machines" / f"{fact.topic}.md"
    if fact.scope == Scope.FOLDER:
        return repo_dir / "folders" / f"{fact.topic}.md"
    if fact.scope == Scope.FILE:
        return repo_dir / "files" / f"{fact.topic}.md"
    if fact.memory_type == MemoryType.EXPLICIT_EPISODIC:
        return repo_dir / "episodic" / "topics" / f"{fact.topic}.md"
    return repo_dir / "facts" / "topics" / f"{fact.topic}.md"


def save_repository_facts(repo_dir: Path, facts: list[Fact], *, auto_commit: bool = True) -> None:
    grouped: dict[Path, list[Fact]] = {}
    for fact in facts:
        path = target_path_for_fact(repo_dir, fact)
        grouped.setdefault(path, []).append(fact.clone(file_path=path, repo=repo_dir.name))
    existing = set(iter_fact_files(repo_dir))
    for path in existing - set(grouped):
        path.unlink(missing_ok=True)
        cache_path_for(path).unlink(missing_ok=True)
        _purge_fact_file_cache(path)
    for path, path_facts in grouped.items():
        write_fact_file(path, path_facts, repo_dir=repo_dir)
    if auto_commit:
        _auto_commit_or_raise(repo_dir, message="umx: save repository facts")


def read_memory_md(repo_dir: Path) -> str:
    path = repo_dir / "meta" / "MEMORY.md"
    return path.read_text() if path.exists() else ""


def _estimate_tokens(text: str) -> int:
    return max(1, (len(text) + 3) // 4)


def write_memory_md(
    repo_dir: Path,
    facts: list[Fact],
    *,
    last_dream: str | None = None,
    session_count: int | None = None,
    dream_provider: str | None = None,
    dream_partial: bool = False,
    config: UMXConfig | None = None,
    auto_commit: bool = True,
) -> None:
    from umx.scope import config_path as _config_path
    from umx.strength import relevance_score

    cfg = config or load_config(_config_path())
    max_tokens = cfg.memory.hot_tier_max_tokens
    index_max_lines = cfg.memory.index_max_lines

    # Build topic stats from ALL non-superseded facts (for index)
    topic_stats: dict[str, list[Fact]] = {}
    for fact in facts:
        topic_stats.setdefault(fact.topic, []).append(fact)

    # Score all facts with a synthetic "project overview" query
    overview_keywords: set[str] = set()
    for topic in topic_stats:
        overview_keywords.update(re.findall(r"[a-zA-Z0-9_]+", topic.lower()))
    overview_keywords.update({"project", "overview", "architecture", "setup"})

    scored: dict[str, float] = {}
    for fact in facts:
        scored[fact.fact_id] = relevance_score(
            fact,
            target_scope=Scope.PROJECT,
            keywords=overview_keywords,
            config=cfg,
        )

    # Protected floor: USER S:4+ and open/blocked tasks
    protected_ids: set[str] = set()
    for fact in facts:
        if fact.scope == Scope.USER and fact.encoding_strength >= 4:
            protected_ids.add(fact.fact_id)
        if fact.task_status in {TaskStatus.OPEN, TaskStatus.BLOCKED}:
            protected_ids.add(fact.fact_id)

    # Pack protected facts first
    hot_facts: list[Fact] = []
    hot_ids: set[str] = set()
    used_tokens = 0

    protected = [f for f in facts if f.fact_id in protected_ids]
    protected.sort(key=lambda f: scored.get(f.fact_id, 0.0), reverse=True)
    for fact in protected:
        tokens = _estimate_tokens(fact.text)
        if used_tokens + tokens > max_tokens and hot_facts:
            # Protected facts that won't fit still get included, but log a warning
            import logging
            logging.getLogger(__name__).warning(
                "Protected facts exceed hot-tier budget (%d/%d tokens)", used_tokens + tokens, max_tokens
            )
        used_tokens += tokens
        hot_facts.append(fact)
        hot_ids.add(fact.fact_id)

    # Pack remaining by packing_score = relevance / tokens, descending
    remaining = [f for f in facts if f.fact_id not in hot_ids]
    remaining.sort(
        key=lambda f: scored.get(f.fact_id, 0.0) / max(1, _estimate_tokens(f.text)),
        reverse=True,
    )
    for fact in remaining:
        tokens = _estimate_tokens(fact.text)
        if used_tokens + tokens > max_tokens:
            continue
        hot_facts.append(fact)
        hot_ids.add(fact.fact_id)
        used_tokens += tokens

    # Build output
    lines = [
        "# umx memory index",
        "scope: project",
        f"schema_version: {CURRENT_SCHEMA_VERSION}",
        f"last_dream: {last_dream or 'never'}",
        f"session_count: {session_count or 0}",
    ]
    if dream_provider:
        lines.append(f"dream_provider: {dream_provider}")
    if dream_partial:
        lines.append("dream_status: partial")
    lines.extend([
        "",
        "## Index",
        "| Topic | File | Updated | Avg strength | Facts |",
        "|---|---|---|---:|---:|",
    ])

    sorted_topics = sorted(topic_stats.items())
    total_topics = len(sorted_topics)
    shown_topics = min(total_topics, index_max_lines)
    for topic, topic_facts in sorted_topics[:shown_topics]:
        avg_strength = sum(f.encoding_strength for f in topic_facts) / max(1, len(topic_facts))
        updated = max(topic_facts, key=lambda fact: fact.created).created.date().isoformat()
        lines.append(
            f"| {topic} | facts/topics/{topic}.md | {updated} | {avg_strength:.1f} | {len(topic_facts)} |"
        )
    if total_topics > index_max_lines:
        lines.append(f"<!-- umx: {total_topics} topics, showing top {shown_topics} -->")

    # Hot Facts section — only hot-tier selected facts
    hot_by_topic: dict[str, list[Fact]] = {}
    for fact in hot_facts:
        hot_by_topic.setdefault(fact.topic, []).append(fact)

    lines.extend(["", "## Hot Facts"])
    for topic, topic_facts in sorted(hot_by_topic.items()):
        lines.append(f"### {topic}")
        for fact in topic_facts:
            lines.append(f"- {fact.text}")

    # Capacity warning
    pct = int(round(used_tokens / max_tokens * 100)) if max_tokens > 0 else 0
    if pct > 90:
        lines.append(f"<!-- umx: hot tier at {pct}% capacity -->")

    md_path = repo_dir / "meta" / "MEMORY.md"
    md_path.write_text("\n".join(lines) + "\n")
    if auto_commit:
        _auto_commit_or_raise(repo_dir, paths=[md_path], message="umx: update MEMORY.md")
