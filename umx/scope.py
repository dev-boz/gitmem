"""Scope hierarchy resolution.

Memory is stored in .umx/ directories at each level of the filesystem.
Resolution mirrors .gitignore and .editorconfig: walk up from CWD, most
specific scope first.

Resolution order:
  ~/.umx/                        # 1. user-global
  ~/.umx/tools/<tool>.md         # 2. tool-specific
  <root>/.umx/local/             # 3. project-local (private, higher priority)
  <root>/.umx/                   # 4. project-team (committed)
  <dir>/.umx/                    # 5. folder (lazy)
  <dir>/.umx/files/<file>.md     # 6. file (lazy)
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from umx.models import Scope


@dataclass
class ScopeLayer:
    """A resolved scope layer with its path and type."""

    scope: Scope
    path: Path
    always_loaded: bool

    @property
    def topics_dir(self) -> Path:
        return self.path / "topics"

    @property
    def files_dir(self) -> Path:
        return self.path / "files"

    @property
    def memory_md(self) -> Path:
        return self.path / "MEMORY.md"

    @property
    def config_yaml(self) -> Path:
        return self.path / "config.yaml"


def user_scope_dir() -> Path:
    """Return the user-global .umx directory (~/.umx/)."""
    return Path.home() / ".umx"


def find_project_root(cwd: Path) -> Path | None:
    """Find the project root by walking up from cwd.

    A project root is identified by the presence of .git/.
    Falls back to .umx/ only at the filesystem root level (not in subdirs
    that may have folder-level .umx/ directories).
    """
    current = cwd.resolve()

    # First pass: look for .git (definitive project root)
    for parent in [current, *current.parents]:
        if (parent / ".git").exists():
            return parent
        if parent == parent.parent:
            break

    # Second pass: look for .umx without .git
    for parent in [current, *current.parents]:
        if (parent / ".umx").exists():
            return parent
        if parent == parent.parent:
            break

    return None


def resolve_scopes(
    cwd: Path,
    tool: str | None = None,
    target_file: Path | None = None,
) -> list[ScopeLayer]:
    """Resolve the full scope hierarchy for a given CWD.

    Returns layers in resolution order (most general → most specific).
    The injection order is reversed: most specific first for override semantics.

    Args:
        cwd: Current working directory.
        tool: Optional tool name for tool-specific scope.
        target_file: Optional file path for file-level scope.

    Returns:
        List of ScopeLayer objects in resolution order (user → file).
    """
    layers: list[ScopeLayer] = []

    # 1. User-global
    user_dir = user_scope_dir()
    layers.append(ScopeLayer(
        scope=Scope.USER,
        path=user_dir,
        always_loaded=True,
    ))

    # 2. Tool-specific
    if tool:
        tool_dir = user_dir / "tools"
        layers.append(ScopeLayer(
            scope=Scope.TOOL,
            path=tool_dir,
            always_loaded=True,
        ))

    # 3-4. Project scopes
    project_root = find_project_root(cwd)
    if project_root:
        # 3. Project-local (private, higher priority for reading)
        local_dir = project_root / ".umx" / "local"
        layers.append(ScopeLayer(
            scope=Scope.PROJECT_LOCAL,
            path=local_dir,
            always_loaded=True,
        ))

        # 4. Project-team (committed)
        team_dir = project_root / ".umx"
        layers.append(ScopeLayer(
            scope=Scope.PROJECT_TEAM,
            path=team_dir,
            always_loaded=True,
        ))

    # 5. Folder scopes (walk from project root to cwd)
    if project_root:
        resolved_cwd = cwd.resolve()
        try:
            rel = resolved_cwd.relative_to(project_root)
        except ValueError:
            rel = Path()

        # Walk each intermediate directory
        current = project_root
        for part in rel.parts:
            current = current / part
            folder_umx = current / ".umx"
            if current != project_root:
                layers.append(ScopeLayer(
                    scope=Scope.FOLDER,
                    path=folder_umx,
                    always_loaded=False,
                ))

    # 6. File scope
    if target_file and project_root:
        file_path = target_file.resolve()
        parent_umx = file_path.parent / ".umx" / "files"
        layers.append(ScopeLayer(
            scope=Scope.FILE,
            path=parent_umx,
            always_loaded=False,
        ))

    return layers


def active_layers(
    layers: list[ScopeLayer],
    include_lazy: bool = False,
) -> list[ScopeLayer]:
    """Filter to layers that should be loaded.

    Args:
        layers: Full scope hierarchy.
        include_lazy: If True, include lazy-loaded layers too.

    Returns:
        Filtered list of active layers.
    """
    return [
        layer for layer in layers
        if layer.always_loaded or (include_lazy and layer.path.exists())
    ]


def iter_existing_layers(layers: list[ScopeLayer]) -> Iterator[ScopeLayer]:
    """Yield only layers whose directory actually exists on disk."""
    for layer in layers:
        if layer.path.exists():
            yield layer


def ensure_scope_dirs(layer: ScopeLayer) -> None:
    """Create the directory structure for a scope layer."""
    layer.path.mkdir(parents=True, exist_ok=True)
    layer.topics_dir.mkdir(exist_ok=True)
    layer.files_dir.mkdir(exist_ok=True)


def init_project(project_root: Path) -> None:
    """Initialize .umx/ structure for a project.

    Creates:
      .umx/
      .umx/topics/
      .umx/files/
      .umx/local/
      .umx/local/topics/
    """
    umx_dir = project_root / ".umx"
    umx_dir.mkdir(exist_ok=True)
    (umx_dir / "topics").mkdir(exist_ok=True)
    (umx_dir / "files").mkdir(exist_ok=True)

    local_dir = umx_dir / "local"
    local_dir.mkdir(exist_ok=True)
    (local_dir / "topics").mkdir(exist_ok=True)

    # Write default .gitignore entries
    gitignore_path = umx_dir.parent / ".gitignore"
    gitignore_entries = [
        ".umx/local/",
        ".umx/dream.lock",
        ".umx/dream.log",
        ".umx/NOTICE",
        "*.umx.json",
    ]
    existing = ""
    if gitignore_path.exists():
        existing = gitignore_path.read_text()

    new_entries = []
    for entry in gitignore_entries:
        if entry not in existing:
            new_entries.append(entry)

    if new_entries:
        with gitignore_path.open("a") as f:
            if existing and not existing.endswith("\n"):
                f.write("\n")
            f.write("\n# umx memory\n")
            for entry in new_entries:
                f.write(f"{entry}\n")
