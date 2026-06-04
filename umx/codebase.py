from __future__ import annotations

import ast
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from umx.scope import normalize_repo_path


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _tracked_project_files(project_root: Path) -> list[Path]:
    try:
        completed = subprocess.run(
            ["git", "-C", str(project_root), "ls-files"],
            capture_output=True,
            check=False,
            text=True,
        )
    except FileNotFoundError:
        completed = None

    if completed is not None and completed.returncode == 0:
        tracked = []
        for raw_line in completed.stdout.splitlines():
            relative = raw_line.strip()
            if not relative:
                continue
            path = project_root / relative
            if path.is_file():
                tracked.append(path)
        return sorted(tracked)

    return sorted(
        path
        for path in project_root.rglob("*")
        if path.is_file() and ".git" not in path.parts
    )


def _git_sha(project_root: Path) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(project_root), "rev-parse", "HEAD"],
            capture_output=True,
            check=False,
            text=True,
        )
    except FileNotFoundError:
        return ""
    if completed.returncode != 0:
        return ""
    return completed.stdout.strip()


def _resolve_import_target(project_root: Path, imported_module: str) -> str:
    parts = [part for part in imported_module.split(".") if part]
    if not parts:
        return imported_module
    module_path = project_root.joinpath(*parts).with_suffix(".py")
    if module_path.exists():
        return normalize_repo_path(module_path.relative_to(project_root))
    package_path = project_root.joinpath(*parts) / "__init__.py"
    if package_path.exists():
        return normalize_repo_path(package_path.relative_to(project_root))
    return imported_module


def _module_name_from_relative(relative: Path) -> str:
    stem = relative.with_suffix("")
    parts = list(stem.parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _python_imports(tree: ast.AST, relative: Path, project_root: Path) -> list[str]:
    package_parts = list(relative.with_suffix("").parts[:-1])
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(_resolve_import_target(project_root, alias.name))
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                keep = max(0, len(package_parts) - node.level + 1)
                base_parts = package_parts[:keep]
            else:
                base_parts = []
            module_parts = node.module.split(".") if node.module else []
            imported = ".".join([*base_parts, *module_parts])
            if imported:
                imports.append(_resolve_import_target(project_root, imported))
    return sorted(dict.fromkeys(imports))


def _python_exports(tree: ast.AST) -> list[str]:
    exports = [
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ]
    return sorted(dict.fromkeys(exports))


def _python_entry_points(tree: ast.AST, relative: Path) -> list[str]:
    exports = _python_exports(tree)
    relative_path = normalize_repo_path(relative)
    candidates = [name for name in exports if name in {"main", "cli", "run"}]
    if relative.name == "__main__.py":
        candidates.append("__main__")
    return sorted(dict.fromkeys(f"{relative_path}::{name}" for name in candidates))


def build_codemap(project_root: Path, *, project_name: str | None = None) -> dict[str, Any]:
    """Build a derived codemap artifact for a project repository."""
    project_root = Path(project_root)
    modules: dict[str, dict[str, Any]] = {}
    for path in _tracked_project_files(project_root):
        relative = path.relative_to(project_root)
        relative_str = normalize_repo_path(relative)
        summary: dict[str, Any] = {
            "path": relative_str,
            "entry_points": [],
            "exports": [],
            "imports": [],
        }
        if path.suffix == ".py":
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except (OSError, SyntaxError, UnicodeDecodeError):
                tree = None
            if tree is not None:
                summary["entry_points"] = _python_entry_points(tree, relative)
                summary["exports"] = _python_exports(tree)
                summary["imports"] = _python_imports(tree, relative, project_root)
                summary["module_name"] = _module_name_from_relative(relative)
        modules[relative_str] = summary

    return {
        "schema_version": "0.6",
        "project": project_name or project_root.name,
        "generated_at": _utc_now(),
        "git_sha": _git_sha(project_root),
        "modules": modules,
    }


def write_codemap(memory_repo_dir: Path, project_root: Path, *, project_name: str | None = None) -> Path:
    memory_repo_dir = Path(memory_repo_dir)
    path = memory_repo_dir / "codebase" / "codemap.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = build_codemap(project_root, project_name=project_name)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def onboarding_slug(described_path: str) -> str:
    normalized = normalize_repo_path(described_path)
    if not normalized:
        return "root.md"
    return normalized.replace("/", "-") + ".md"


def onboarding_unit_path(memory_repo_dir: Path, described_path: str) -> Path:
    return Path(memory_repo_dir) / "codebase" / "onboarding" / onboarding_slug(described_path)


def compute_drift_hash(project_root: Path, source_paths: list[str]) -> str:
    hasher = hashlib.sha256()
    project_root = Path(project_root)
    for source_path in sorted(normalize_repo_path(path) for path in source_paths if normalize_repo_path(path)):
        hasher.update(source_path.encode("utf-8"))
        target = project_root / source_path
        if target.is_file():
            hasher.update(target.read_bytes())
        elif target.is_dir():
            for child in sorted(
                path
                for path in target.rglob("*")
                if path.is_file() and ".git" not in path.parts
            ):
                relative = normalize_repo_path(child.relative_to(project_root))
                hasher.update(relative.encode("utf-8"))
                hasher.update(child.read_bytes())
        else:
            hasher.update(b"<missing>")
    return hasher.hexdigest()


def write_onboarding_unit(
    memory_repo_dir: Path,
    described_path: str,
    *,
    project_root: Path,
    purpose: str,
    invariants: list[str] | None = None,
    gotchas: list[str] | None = None,
    related_refs: list[str] | None = None,
    source_paths: list[str] | None = None,
) -> Path:
    normalized_path = normalize_repo_path(described_path)
    if source_paths is None:
        target = Path(project_root) / normalized_path if normalized_path else Path(project_root)
        if target.is_dir():
            source_paths = [
                normalize_repo_path(path.relative_to(project_root))
                for path in sorted(
                    child
                    for child in target.rglob("*")
                    if child.is_file() and ".git" not in child.parts
                )
            ]
        elif normalized_path:
            source_paths = [normalized_path]
        else:
            source_paths = []

    header = {
        "schema_version": "0.6",
        "described_path": normalized_path,
        "drift_hash": compute_drift_hash(project_root, source_paths),
        "source_paths": source_paths,
    }
    sections = [
        f"# Onboarding: {normalized_path or '.'}",
        "",
        "## Purpose",
        "",
        purpose.strip(),
    ]
    if invariants:
        sections.extend(["", "## Key invariants", ""])
        sections.extend(f"- {item}" for item in invariants)
    if gotchas:
        sections.extend(["", "## Fragile areas", ""])
        sections.extend(f"- {item}" for item in gotchas)
    if related_refs:
        sections.extend(["", "## Read first", ""])
        sections.extend(f"- {item}" for item in related_refs)

    path = onboarding_unit_path(memory_repo_dir, described_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    frontmatter = yaml.safe_dump(header, sort_keys=False).strip()
    content = f"---\n{frontmatter}\n---\n\n" + "\n".join(sections).rstrip() + "\n"
    path.write_text(content, encoding="utf-8")
    return path


def read_onboarding_unit(path: Path) -> dict[str, Any] | None:
    """Parse the YAML frontmatter header of an onboarding unit.

    Returns the header dict, or ``None`` if the file is unreadable or has no
    well-formed ``---`` frontmatter block.
    """
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return None
    if not text.startswith("---"):
        return None
    parts = text.split("---", 2)
    if len(parts) < 3:
        return None
    try:
        header = yaml.safe_load(parts[1])
    except yaml.YAMLError:
        return None
    return header if isinstance(header, dict) else None


def iter_onboarding_units(memory_repo_dir: Path) -> list[Path]:
    """Return the onboarding unit markdown files for a memory repo, sorted."""
    root = Path(memory_repo_dir) / "codebase" / "onboarding"
    if not root.is_dir():
        return []
    return sorted(path for path in root.glob("*.md") if path.is_file())


def check_onboarding_drift(memory_repo_dir: Path, project_root: Path) -> list[dict[str, Any]]:
    """Detect onboarding units whose source files have drifted (spec §3b).

    Each onboarding unit records a ``drift_hash`` over the source files it
    describes. When the SHA of those files changes the unit is stale and needs
    re-validation. This recomputes the hash for every stored unit and returns
    one record per unit whose stored hash no longer matches the current source
    state (including units that record no ``drift_hash`` at all).
    """
    drifted: list[dict[str, Any]] = []
    project_root = Path(project_root)
    for unit_path in iter_onboarding_units(memory_repo_dir):
        header = read_onboarding_unit(unit_path)
        if header is None:
            continue
        source_paths = header.get("source_paths") or []
        if not isinstance(source_paths, list):
            continue
        stored = header.get("drift_hash")
        current = compute_drift_hash(project_root, [str(item) for item in source_paths])
        if stored != current:
            drifted.append(
                {
                    "unit": unit_path.name,
                    "described_path": header.get("described_path", ""),
                    "stored_drift_hash": stored,
                    "current_drift_hash": current,
                }
            )
    return drifted


def read_docs_registry(memory_repo_dir: Path) -> dict[str, Any]:
    root = Path(memory_repo_dir) / "codebase" / "docs"
    json_path = root / "registry.json"
    yaml_path = root / "registry.yaml"
    yml_path = root / "registry.yml"
    if json_path.exists():
        payload = json.loads(json_path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    for path in (yaml_path, yml_path):
        if path.exists():
            payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            return payload if isinstance(payload, dict) else {}
    return {}


def lookup_task_type_docs(memory_repo_dir: Path, task_type: str) -> dict[str, Any] | None:
    registry = read_docs_registry(memory_repo_dir)
    docs = registry.get("task_type_docs")
    if not isinstance(docs, dict):
        return None
    if task_type in docs and isinstance(docs[task_type], dict):
        return docs[task_type]
    parts = task_type.split(".")
    while len(parts) > 1:
        parts.pop()
        candidate = ".".join(parts)
        value = docs.get(candidate)
        if isinstance(value, dict):
            return value
    return None
