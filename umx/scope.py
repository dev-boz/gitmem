from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import quote, unquote

from umx.schema import ensure_memory_schema_header, schema_version_path, write_schema_version


DEFAULT_UMX_HOME = "~/.umx"
ROOT_SCOPE_SENTINEL = "__root__"


@dataclass(slots=True, frozen=True)
class ScopedMemoryOrphan:
    scope_kind: Literal["file", "folder"]
    memory_path: str
    scoped_path: str


def get_umx_home() -> Path:
    raw = os.environ.get("UMX_HOME", DEFAULT_UMX_HOME)
    return Path(raw).expanduser()


def config_path() -> Path:
    return get_umx_home() / "config.yaml"


def user_memory_dir() -> Path:
    return get_umx_home() / "user"


def normalize_repo_path(path: str | Path) -> str:
    path_obj = Path(path)
    parts = [part for part in path_obj.as_posix().split("/") if part and part != "."]
    cleaned: list[str] = []
    for part in parts:
        if part == "..":
            if cleaned:
                cleaned.pop()
            continue
        cleaned.append(part)
    return "/".join(cleaned)


def encode_scope_path(path: str | Path) -> str:
    normalized = normalize_repo_path(path)
    if not normalized:
        return ROOT_SCOPE_SENTINEL
    encoded_parts = [quote(part, safe="._-") for part in normalized.split("/")]
    return "---".join(encoded_parts)


def decode_scope_path(name: str) -> str:
    if name == ROOT_SCOPE_SENTINEL:
        return ""
    return "/".join(unquote(part) for part in name.split("---"))


def find_orphaned_scoped_memory(repo_dir: Path, project_root: Path) -> list[ScopedMemoryOrphan]:
    orphans: list[ScopedMemoryOrphan] = []
    for scope_kind, directory in (("file", "files"), ("folder", "folders")):
        scoped_dir = repo_dir / directory
        if not scoped_dir.exists():
            continue
        for path in sorted(scoped_dir.glob("*.md")):
            scoped_path = normalize_repo_path(decode_scope_path(path.stem))
            target = project_root if not scoped_path else project_root / scoped_path
            exists = target.is_file() if scope_kind == "file" else target.is_dir()
            if exists:
                continue
            orphans.append(
                ScopedMemoryOrphan(
                    scope_kind=scope_kind,
                    memory_path=path.relative_to(repo_dir).as_posix(),
                    scoped_path=scoped_path or ".",
                )
            )
    return orphans


def find_project_root(start: Path | None = None) -> Path:
    current = (start or Path.cwd()).resolve()
    for candidate in [current, *current.parents]:
        if (candidate / ".umx-project").exists() or (candidate / ".git").exists():
            return candidate
    return current


def is_memory_repo_root(path: Path) -> bool:
    return (
        (path / "meta").is_dir()
        and (path / "facts").is_dir()
        and (path / "sessions").is_dir()
        and (
            schema_version_path(path).exists()
            or (path / "meta" / "MEMORY.md").exists()
        )
    )


def find_memory_repo_root(start: Path | None = None) -> Path | None:
    current = (start or Path.cwd()).resolve()
    for candidate in [current, *current.parents]:
        if is_memory_repo_root(candidate):
            return candidate
    return None


def _slug_from_remote(cwd: Path) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(cwd), "config", "--get", "remote.origin.url"],
            capture_output=True,
            check=False,
            text=True,
        )
    except FileNotFoundError:
        return None
    if completed.returncode != 0:
        return None
    url = completed.stdout.strip()
    if not url:
        return None
    tail = url.rstrip("/").split("/")[-1]
    if tail.endswith(".git"):
        tail = tail[:-4]
    if ":" in tail:
        tail = tail.split(":")[-1]
    return tail or None


def discover_project_slug(cwd: Path | None = None) -> str:
    root = find_project_root(cwd)
    marker = root / ".umx-project"
    if marker.exists():
        text = marker.read_text().strip()
        if text:
            return text
    remote = _slug_from_remote(root)
    if remote:
        return remote
    return root.name


def project_memory_dir(cwd: Path | None = None) -> Path:
    return get_umx_home() / "projects" / discover_project_slug(cwd)


def ensure_repo_structure(repo_dir: Path, *, ensure_schema: bool = True) -> None:
    for relative in [
        "sessions",
        "episodic/topics",
        "facts/topics",
        "principles/topics",
        "procedures",
        "meta",
        "local/private",
        "local/secret",
        "local/quarantine",
        "folders",
        "files",
        "tools",
        "machines",
    ]:
        (repo_dir / relative).mkdir(parents=True, exist_ok=True)
    schema = schema_version_path(repo_dir)
    if ensure_schema and not schema.exists():
        write_schema_version(repo_dir)
    manifest = repo_dir / "meta" / "manifest.json"
    if not manifest.exists():
        manifest.write_text('{"topics": {}, "modules_seen": [], "uncertainty_hotspots": [], "knowledge_gaps": [], "last_rebuilt": null}\n')
    tombstones = repo_dir / "meta" / "tombstones.jsonl"
    if not tombstones.exists():
        tombstones.write_text("")
    gaps = repo_dir / "meta" / "gaps.jsonl"
    if not gaps.exists():
        gaps.write_text("")
    processing = repo_dir / "meta" / "processing.jsonl"
    if not processing.exists():
        processing.write_text("")
    memory = repo_dir / "meta" / "MEMORY.md"
    if ensure_schema and not memory.exists():
        ensure_memory_schema_header(repo_dir)


def init_local_umx(org: str | None = None) -> Path:
    home = get_umx_home()
    udir = user_memory_dir()
    ensure_repo_structure(udir)
    home.mkdir(parents=True, exist_ok=True)
    from umx.git_ops import git_init, is_git_repo

    if not is_git_repo(udir):
        git_init(udir)
    return home


def init_project_memory(cwd: Path | None = None, write_marker: bool = True) -> Path:
    root = find_project_root(cwd)
    slug = discover_project_slug(root)
    repo_dir = get_umx_home() / "projects" / slug
    ensure_repo_structure(repo_dir)
    from umx.git_ops import git_init, is_git_repo

    if not is_git_repo(repo_dir):
        git_init(repo_dir)
    conventions = repo_dir / "CONVENTIONS.md"
    if not conventions.exists():
        conventions.write_text(
            "# Project Conventions\n\n## Topic taxonomy\n- general: default topic\n\n## Fact phrasing\n- Atomic facts only\n- <=200 characters per fact\n\n## Entity vocabulary\n- Fill in project-specific vocabulary here\n"
        )
    if write_marker:
        (root / ".umx-project").write_text(f"{slug}\n")
    return repo_dir
