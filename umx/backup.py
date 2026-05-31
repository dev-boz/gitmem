from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath

from umx.git_ops import git_init, is_git_repo
from umx.identity import generate_fact_id
from umx.memory import add_fact, load_all_facts, replace_fact
from umx.models import (
    ConsolidationStatus,
    Fact,
    MemoryType,
    Provenance,
    Scope,
    SourceType,
    Verification,
)
from umx.search import _close_hot_connections

BACKUP_MANIFEST_NAME = "backup-manifest.json"
BACKUP_SNAPSHOT_DIRNAME = "snapshot"
BACKUP_FORMAT_VERSION = 1
MEMORIES_MANIFEST_NAME = ".umx-memories-manifest.json"
MEMORIES_FORMAT_VERSION = 1
MEMORIES_METADATA_PREFIX = "<!-- umx-memory: "
MEMORIES_METADATA_SUFFIX = " -->"
_MEMORIES_TOPIC_RE = re.compile(r"[^a-z0-9._-]+")
_MEMORIES_EXPORTABLE_SCOPES = frozenset(
    {Scope.PROJECT, Scope.USER, Scope.TOOL, Scope.MACHINE, Scope.FOLDER, Scope.FILE}
)


@dataclass(slots=True, frozen=True)
class BackupManifest:
    format_version: int = BACKUP_FORMAT_VERSION
    entries: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(slots=True, frozen=True)
class ExportResult:
    out_dir: str
    files_copied: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(slots=True, frozen=True)
class ImportResult:
    source_dir: str
    changed_files: list[str] = field(default_factory=list)
    forced: bool = False

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(slots=True, frozen=True)
class MemoriesManifestEntry:
    path: str
    fact_id: str
    topic: str
    scope: str
    body_sha256: str


@dataclass(slots=True, frozen=True)
class MemoriesManifest:
    format_version: int = MEMORIES_FORMAT_VERSION
    entries: list[MemoriesManifestEntry] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(slots=True, frozen=True)
class MemoriesInspection:
    source_dir: str
    files_found: int
    changes_found: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(slots=True, frozen=True)
class _MemoriesImportOperation:
    relative_path: str
    text: str
    topic: str
    original_fact: Fact | None = None


@dataclass(slots=True, frozen=True)
class _MemoriesImportPlan:
    source_dir: str
    files_found: int
    operations: list[_MemoriesImportOperation] = field(default_factory=list)
    consumed_paths: list[str] = field(default_factory=list)


def _manifest_path(path: Path) -> Path:
    return path / BACKUP_MANIFEST_NAME


def _snapshot_root(path: Path) -> Path:
    return path / BACKUP_SNAPSHOT_DIRNAME


def _memories_manifest_path(path: Path) -> Path:
    return path / MEMORIES_MANIFEST_NAME


def _iter_repo_files(repo_dir: Path) -> list[str]:
    files: list[str] = []
    for root, dirnames, filenames in os.walk(repo_dir, topdown=True, followlinks=False):
        root_path = Path(root)
        kept_dirs: list[str] = []
        for dirname in dirnames:
            path = root_path / dirname
            relative = path.relative_to(repo_dir)
            if relative.parts[0] == ".git":
                continue
            if path.is_symlink():
                raise RuntimeError(
                    f"backup source contains symlinked directory: {relative.as_posix()}"
                )
            kept_dirs.append(dirname)
        dirnames[:] = kept_dirs
        for filename in filenames:
            path = root_path / filename
            relative = path.relative_to(repo_dir)
            if relative.parts[0] == ".git":
                continue
            if path.is_symlink():
                raise RuntimeError(
                    f"backup source contains symlinked file: {relative.as_posix()}"
                )
            files.append(relative.as_posix())
    return files


def _copy_file(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def _collect_entries(files: list[str]) -> list[str]:
    return sorted({path.split("/", 1)[0] for path in files})


def _write_manifest(out_dir: Path, manifest: BackupManifest) -> None:
    _manifest_path(out_dir).write_text(json.dumps(manifest.to_dict(), sort_keys=True) + "\n")


def _write_memories_manifest(out_dir: Path, manifest: MemoriesManifest) -> None:
    _memories_manifest_path(out_dir).write_text(
        json.dumps(manifest.to_dict(), sort_keys=True) + "\n"
    )


def _validate_relative_path(value: str) -> str:
    if "\\" in value:
        raise RuntimeError(f"invalid backup path {value!r}")
    pure = PurePosixPath(value)
    normalized = pure.as_posix()
    if (
        not value
        or pure.is_absolute()
        or normalized != value
        or any(part in {"", ".", ".."} for part in pure.parts)
        or pure.parts[0] == ".git"
    ):
        raise RuntimeError(f"invalid backup path {value!r}")
    return normalized


def _memories_manifest_map(out_dir: Path) -> dict[str, MemoriesManifestEntry]:
    manifest_path = _memories_manifest_path(out_dir)
    if not manifest_path.exists():
        return {}
    try:
        payload = json.loads(manifest_path.read_text())
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid memories manifest: {manifest_path}") from exc
    if payload.get("format_version") != MEMORIES_FORMAT_VERSION:
        raise RuntimeError(
            "unsupported memories format "
            f"{payload.get('format_version')!r}; expected {MEMORIES_FORMAT_VERSION}"
        )
    raw_entries = payload.get("entries")
    if not isinstance(raw_entries, list):
        raise RuntimeError(f"invalid memories manifest entries in {manifest_path}")
    entries: dict[str, MemoriesManifestEntry] = {}
    for item in raw_entries:
        if not isinstance(item, dict):
            raise RuntimeError(f"invalid memories manifest entries in {manifest_path}")
        relative = _validate_relative_path(str(item.get("path", "")))
        fact_id = item.get("fact_id")
        topic = item.get("topic")
        scope = item.get("scope")
        body_sha256 = item.get("body_sha256")
        if not all(isinstance(value, str) and value for value in (fact_id, topic, scope, body_sha256)):
            raise RuntimeError(f"invalid memories manifest entries in {manifest_path}")
        if relative in entries:
            raise RuntimeError(f"duplicate memories manifest path {relative!r} in {manifest_path}")
        entries[relative] = MemoriesManifestEntry(
            path=relative,
            fact_id=fact_id,
            topic=topic,
            scope=scope,
            body_sha256=body_sha256,
        )
    return entries


def _slugify_memories_topic(topic: str) -> str:
    slug = _MEMORIES_TOPIC_RE.sub("-", topic.strip().lower()).strip("-.")
    return re.sub(r"-{2,}", "-", slug) or "memory"


def _normalize_memories_text(text: str) -> str:
    return " ".join(part.strip() for part in text.splitlines() if part.strip()).strip()


def _memories_body_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _parse_memories_file(text: str) -> tuple[dict[str, object], str]:
    lines = text.splitlines()
    if lines and lines[0].startswith(MEMORIES_METADATA_PREFIX) and lines[0].endswith(
        MEMORIES_METADATA_SUFFIX
    ):
        payload = lines[0][len(MEMORIES_METADATA_PREFIX) : -len(MEMORIES_METADATA_SUFFIX)]
        try:
            metadata = json.loads(payload)
        except json.JSONDecodeError:
            metadata = {}
        if isinstance(metadata, dict):
            return metadata, "\n".join(lines[1:])
    return {}, text


def _render_memories_file(fact: Fact) -> str:
    metadata = json.dumps(
        {
            "fact_id": fact.fact_id,
            "scope": fact.scope.value,
            "topic": fact.topic,
        },
        sort_keys=True,
    )
    return f"{MEMORIES_METADATA_PREFIX}{metadata}{MEMORIES_METADATA_SUFFIX}\n{fact.text.rstrip()}\n"


def _memories_relative_path(fact: Fact) -> str:
    return f"{_slugify_memories_topic(fact.topic)}--{fact.fact_id}.md"


def _memories_projection_facts(repo_dir: Path) -> list[Fact]:
    facts = [
        fact
        for fact in load_all_facts(repo_dir, include_superseded=False, use_cache=False)
        if fact.is_active
        and fact.memory_type == MemoryType.EXPLICIT_SEMANTIC
        and fact.scope in _MEMORIES_EXPORTABLE_SCOPES
    ]
    facts.sort(key=lambda fact: (fact.topic, fact.created, fact.fact_id))
    return facts


def _memories_projection_entries(
    repo_dir: Path,
) -> tuple[list[MemoriesManifestEntry], dict[str, str]]:
    entries: list[MemoriesManifestEntry] = []
    rendered: dict[str, str] = {}
    for fact in _memories_projection_facts(repo_dir):
        relative = _memories_relative_path(fact)
        normalized_text = _normalize_memories_text(fact.text)
        rendered[relative] = _render_memories_file(fact)
        entries.append(
            MemoriesManifestEntry(
                path=relative,
                fact_id=fact.fact_id,
                topic=fact.topic,
                scope=fact.scope.value,
                body_sha256=_memories_body_hash(normalized_text),
            )
        )
    return entries, rendered


def _memories_file_hash(path: Path) -> str:
    _, body = _parse_memories_file(path.read_text())
    return _memories_body_hash(_normalize_memories_text(body))


def _sync_memories_projection(
    repo_dir: Path,
    out_dir: Path,
    *,
    allow_dirty_paths: set[str] | None = None,
    cleanup_paths: set[str] | None = None,
) -> ExportResult:
    dest = out_dir.resolve()
    if dest.exists() and not dest.is_dir():
        raise RuntimeError(f"memories destination is not a directory: {dest}")
    dest.mkdir(parents=True, exist_ok=True)
    manifest_map = _memories_manifest_map(dest)
    allowed_dirty = allow_dirty_paths or set()
    for relative, entry in manifest_map.items():
        path = dest.joinpath(*PurePosixPath(relative).parts)
        if not path.exists() or relative in allowed_dirty:
            continue
        if _memories_file_hash(path) != entry.body_sha256:
            raise RuntimeError(
                "memories projection contains unimported changes: "
                f"{relative}; rerun `umx import --memories` first"
            )

    entries, rendered = _memories_projection_entries(repo_dir)
    next_paths = {entry.path for entry in entries}
    stale_paths = (set(manifest_map) - next_paths) | (cleanup_paths or set())
    for relative in sorted(stale_paths):
        path = dest.joinpath(*PurePosixPath(relative).parts)
        if path.exists():
            path.unlink()

    for relative, content in rendered.items():
        path = dest.joinpath(*PurePosixPath(relative).parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    _write_memories_manifest(
        dest,
        MemoriesManifest(entries=sorted(entries, key=lambda entry: entry.path)),
    )
    return ExportResult(
        out_dir=str(dest),
        files_copied=sorted(entry.path for entry in entries),
    )


def _memories_topic_from_path(path: PurePosixPath) -> str:
    stem = path.stem
    if "--" in stem:
        stem = stem.rsplit("--", 1)[0]
    return _slugify_memories_topic(stem)


def _memories_projection_fact(topic: str, text: str) -> Fact:
    return Fact(
        fact_id=generate_fact_id(),
        text=text,
        scope=Scope.PROJECT,
        topic=topic,
        encoding_strength=5,
        memory_type=MemoryType.EXPLICIT_SEMANTIC,
        verification=Verification.HUMAN_CONFIRMED,
        source_type=SourceType.USER_PROMPT,
        source_tool="memories-projection",
        source_session="manual-edit",
        consolidation_status=ConsolidationStatus.STABLE,
        provenance=Provenance(
            extracted_by="memories-projection",
            sessions=["manual-edit"],
        ),
    )


def _memories_projection_successor(fact: Fact, new_text: str) -> tuple[Fact, Fact]:
    new_id = generate_fact_id()
    previous = fact.clone(superseded_by=new_id)
    updated = fact.clone(
        fact_id=new_id,
        text=new_text,
        encoding_strength=5,
        verification=Verification.HUMAN_CONFIRMED,
        source_type=SourceType.USER_PROMPT,
        source_tool="memories-projection",
        source_session="manual-edit",
        consolidation_status=ConsolidationStatus.STABLE,
        supersedes=fact.fact_id,
        superseded_by=None,
        provenance=Provenance(
            extracted_by="memories-projection",
            sessions=["manual-edit"],
        ),
    )
    return previous, updated


def _build_memories_import_plan(source_dir: Path, repo_dir: Path) -> _MemoriesImportPlan:
    source = source_dir.resolve()
    if not source.exists() or not source.is_dir():
        raise RuntimeError(f"memories source directory not found: {source}")
    manifest_map = _memories_manifest_map(source)
    facts_by_id = {
        fact.fact_id: fact
        for fact in load_all_facts(repo_dir, include_superseded=True, use_cache=False)
    }
    operations: list[_MemoriesImportOperation] = []
    consumed_paths: set[str] = set()
    edited_fact_ids: set[str] = set()
    files_found = 0
    for path in sorted(candidate for candidate in source.rglob("*.md") if candidate.is_file()):
        if path.is_symlink():
            raise RuntimeError(f"memories source contains symlinked file: {path}")
        relative = _validate_relative_path(path.relative_to(source).as_posix())
        files_found += 1
        metadata, raw_text = _parse_memories_file(path.read_text())
        normalized_text = _normalize_memories_text(raw_text)
        manifest_entry = manifest_map.get(relative)
        if not normalized_text:
            consumed_paths.add(relative)
            continue
        if manifest_entry and _memories_body_hash(normalized_text) == manifest_entry.body_sha256:
            continue
        metadata_fact_id = metadata.get("fact_id")
        fact_id = (
            manifest_entry.fact_id
            if manifest_entry is not None
            else metadata_fact_id if isinstance(metadata_fact_id, str) and metadata_fact_id else None
        )
        original_fact = facts_by_id.get(fact_id) if fact_id else None
        if original_fact is not None:
            if original_fact.superseded_by is not None:
                raise RuntimeError(
                    "memories file references superseded fact "
                    f"{original_fact.fact_id}; use active fact "
                    f"{original_fact.superseded_by} instead"
                )
            if original_fact.fact_id in edited_fact_ids:
                raise RuntimeError(
                    "multiple memories projection files target the same fact: "
                    f"{original_fact.fact_id}"
                )
            topic = original_fact.topic
            if _normalize_memories_text(original_fact.text) == normalized_text:
                consumed_paths.add(relative)
                continue
            edited_fact_ids.add(original_fact.fact_id)
        else:
            metadata_topic = metadata.get("topic")
            if isinstance(metadata_topic, str) and metadata_topic.strip():
                topic = _slugify_memories_topic(metadata_topic)
            elif manifest_entry is not None:
                topic = manifest_entry.topic
            else:
                topic = _memories_topic_from_path(PurePosixPath(relative))
        operations.append(
            _MemoriesImportOperation(
                relative_path=relative,
                text=normalized_text,
                topic=topic,
                original_fact=original_fact,
            )
        )
        consumed_paths.add(relative)
    return _MemoriesImportPlan(
        source_dir=str(source),
        files_found=files_found,
        operations=operations,
        consumed_paths=sorted(consumed_paths),
    )


def inspect_backup_dir(source_dir: Path) -> BackupManifest:
    resolved = source_dir.resolve()
    if not resolved.exists() or not resolved.is_dir():
        raise RuntimeError(f"backup source directory not found: {resolved}")
    snapshot_root = _snapshot_root(resolved)
    if not snapshot_root.exists() or not snapshot_root.is_dir():
        raise RuntimeError(f"backup snapshot directory not found: {snapshot_root}")
    if snapshot_root.is_symlink():
        raise RuntimeError(f"backup snapshot directory must not be a symlink: {snapshot_root}")
    manifest_path = _manifest_path(resolved)
    if not manifest_path.exists():
        raise RuntimeError(f"backup manifest not found: {manifest_path}")
    payload = json.loads(manifest_path.read_text())
    if payload.get("format_version") != BACKUP_FORMAT_VERSION:
        raise RuntimeError(
            f"unsupported backup format {payload.get('format_version')!r}; expected {BACKUP_FORMAT_VERSION}"
        )
    entries = payload.get("entries")
    files = payload.get("files")
    if not isinstance(entries, list) or not all(isinstance(item, str) for item in entries):
        raise RuntimeError(f"invalid backup manifest entries in {manifest_path}")
    if not isinstance(files, list) or not all(isinstance(item, str) for item in files):
        raise RuntimeError(f"invalid backup manifest files in {manifest_path}")
    validated_files = [_validate_relative_path(item) for item in files]
    if len(set(validated_files)) != len(validated_files):
        raise RuntimeError(f"duplicate backup manifest files in {manifest_path}")
    manifest = BackupManifest(entries=_collect_entries(validated_files), files=sorted(validated_files))
    _preflight_backup_files(snapshot_root, manifest)
    return manifest


def target_contains_backup_data(repo_dir: Path) -> bool:
    if not repo_dir.exists():
        return False
    return bool(_iter_repo_files(repo_dir))


def _clear_target_repo(repo_dir: Path) -> None:
    if not repo_dir.exists():
        return
    for path in repo_dir.iterdir():
        if path.name == ".git":
            continue
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()


def _preflight_backup_files(source_dir: Path, manifest: BackupManifest) -> None:
    if source_dir.is_symlink():
        raise RuntimeError(f"backup snapshot directory must not be a symlink: {source_dir}")
    invalid: list[str] = []
    for relative in manifest.files:
        parts = PurePosixPath(relative).parts
        path = source_dir.joinpath(*parts)
        if any(source_dir.joinpath(*parts[: index + 1]).is_symlink() for index in range(len(parts))):
            invalid.append(relative)
            continue
        try:
            resolved = path.resolve(strict=True)
        except FileNotFoundError:
            invalid.append(relative)
            continue
        try:
            resolved.relative_to(source_dir.resolve())
        except ValueError:
            invalid.append(relative)
            continue
        if not path.is_file():
            invalid.append(relative)
    if invalid:
        raise RuntimeError(
            "backup snapshot is incomplete; missing or invalid file(s): "
            + ", ".join(invalid[:5])
        )


def export_full(repo_dir: Path, out_dir: Path) -> ExportResult:
    repo = repo_dir.resolve()
    dest = out_dir.resolve()
    if not repo.exists():
        raise RuntimeError(f"memory repo not found: {repo}")
    if dest == repo or repo in dest.parents:
        raise RuntimeError("backup destination must be outside the memory repo")
    if dest.exists():
        if not dest.is_dir():
            raise RuntimeError(f"backup destination is not a directory: {dest}")
        if any(dest.iterdir()):
            raise RuntimeError(f"backup destination must be empty: {dest}")
    else:
        dest.mkdir(parents=True, exist_ok=True)
    manifest_path = _manifest_path(dest)
    if manifest_path.exists():
        raise RuntimeError(f"backup manifest already exists: {manifest_path}")
    snapshot_root = _snapshot_root(dest)
    if snapshot_root.exists():
        raise RuntimeError(f"backup snapshot directory already exists: {snapshot_root}")
    snapshot_root.mkdir(parents=True, exist_ok=True)

    _close_hot_connections()

    files = _iter_repo_files(repo)
    for relative in files:
        _copy_file(repo / relative, snapshot_root.joinpath(*PurePosixPath(relative).parts))

    manifest = BackupManifest(entries=_collect_entries(files), files=files)
    _write_manifest(dest, manifest)
    return ExportResult(out_dir=str(dest), files_copied=manifest.files)


def export_memories(repo_dir: Path, out_dir: Path) -> ExportResult:
    repo = repo_dir.resolve()
    if not repo.exists():
        raise RuntimeError(f"memory repo not found: {repo}")
    return _sync_memories_projection(repo, out_dir)


def inspect_memories_dir(source_dir: Path, repo_dir: Path) -> MemoriesInspection:
    plan = _build_memories_import_plan(source_dir, repo_dir)
    return MemoriesInspection(
        source_dir=plan.source_dir,
        files_found=plan.files_found,
        changes_found=len(plan.operations),
    )


def import_memories(source_dir: Path, repo_dir: Path) -> ImportResult:
    plan = _build_memories_import_plan(source_dir, repo_dir)
    repo = repo_dir.resolve()
    changed_files: list[str] = []
    for operation in plan.operations:
        if operation.original_fact is not None:
            previous, updated = _memories_projection_successor(
                operation.original_fact,
                operation.text,
            )
            if not replace_fact(repo, previous):
                raise RuntimeError(
                    f"failed to prepare supersession for {operation.original_fact.fact_id}"
                )
            add_fact(repo, updated, auto_commit=False)
        else:
            add_fact(
                repo,
                _memories_projection_fact(operation.topic, operation.text),
                auto_commit=False,
            )
        changed_files.append(operation.relative_path)
    _sync_memories_projection(
        repo,
        Path(plan.source_dir),
        allow_dirty_paths=set(plan.consumed_paths),
        cleanup_paths=set(plan.consumed_paths),
    )
    return ImportResult(
        source_dir=plan.source_dir,
        changed_files=changed_files,
        forced=False,
    )


def import_full(source_dir: Path, repo_dir: Path, *, force: bool = False) -> ImportResult:
    manifest = inspect_backup_dir(source_dir)
    resolved_source = source_dir.resolve()
    snapshot_root = _snapshot_root(resolved_source)
    _preflight_backup_files(snapshot_root, manifest)
    repo = repo_dir.resolve()
    if (
        resolved_source == repo
        or resolved_source in repo.parents
        or repo in resolved_source.parents
    ):
        raise RuntimeError("backup source directory must not overlap the target repo")
    had_data = target_contains_backup_data(repo)
    if had_data and not force:
        raise RuntimeError(f"target repo already contains backup data: {repo}; rerun with --force")

    _close_hot_connections()

    if force:
        _clear_target_repo(repo)
    if not is_git_repo(repo):
        git_init(repo)

    for relative in manifest.files:
        parts = PurePosixPath(relative).parts
        _copy_file(snapshot_root.joinpath(*parts), repo.joinpath(*parts))

    return ImportResult(
        source_dir=str(resolved_source),
        changed_files=manifest.files,
        forced=had_data and force,
    )
