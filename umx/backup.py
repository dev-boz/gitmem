from __future__ import annotations

import json
import os
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath

from umx.git_ops import git_init, is_git_repo
from umx.search import _close_hot_connections

BACKUP_MANIFEST_NAME = "backup-manifest.json"
BACKUP_SNAPSHOT_DIRNAME = "snapshot"
BACKUP_FORMAT_VERSION = 1


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


def _manifest_path(path: Path) -> Path:
    return path / BACKUP_MANIFEST_NAME


def _snapshot_root(path: Path) -> Path:
    return path / BACKUP_SNAPSHOT_DIRNAME


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
