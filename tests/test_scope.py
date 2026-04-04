"""Tests for umx.scope — scope hierarchy resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

from umx.models import Scope
from umx.scope import (
    ScopeLayer,
    active_layers,
    ensure_scope_dirs,
    find_project_root,
    init_project,
    resolve_scopes,
    user_scope_dir,
)


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    """Create a mock project directory."""
    project = tmp_path / "myproject"
    project.mkdir()
    (project / ".git").mkdir()
    return project


@pytest.fixture
def umx_project(project_dir: Path) -> Path:
    """Create a project with .umx/ initialized."""
    init_project(project_dir)
    return project_dir


class TestFindProjectRoot:
    def test_finds_git_root(self, project_dir: Path):
        subdir = project_dir / "src" / "modules"
        subdir.mkdir(parents=True)
        root = find_project_root(subdir)
        assert root == project_dir

    def test_finds_umx_root(self, tmp_path: Path):
        project = tmp_path / "proj"
        project.mkdir()
        (project / ".umx").mkdir()
        root = find_project_root(project)
        assert root == project

    def test_no_root_found(self, tmp_path: Path):
        orphan = tmp_path / "orphan"
        orphan.mkdir()
        root = find_project_root(orphan)
        assert root is None


class TestResolveScopes:
    def test_user_scope_always_present(self, project_dir: Path):
        layers = resolve_scopes(project_dir)
        user_layers = [l for l in layers if l.scope == Scope.USER]
        assert len(user_layers) == 1
        assert user_layers[0].always_loaded

    def test_tool_scope_when_tool_specified(self, project_dir: Path):
        layers = resolve_scopes(project_dir, tool="aider")
        tool_layers = [l for l in layers if l.scope == Scope.TOOL]
        assert len(tool_layers) == 1

    def test_no_tool_scope_when_not_specified(self, project_dir: Path):
        layers = resolve_scopes(project_dir)
        tool_layers = [l for l in layers if l.scope == Scope.TOOL]
        assert len(tool_layers) == 0

    def test_project_scopes_present(self, project_dir: Path):
        layers = resolve_scopes(project_dir)
        local_layers = [l for l in layers if l.scope == Scope.PROJECT_LOCAL]
        team_layers = [l for l in layers if l.scope == Scope.PROJECT_TEAM]
        assert len(local_layers) == 1
        assert len(team_layers) == 1

    def test_folder_scopes_for_subdirectory(self, project_dir: Path):
        subdir = project_dir / "src" / "auth"
        subdir.mkdir(parents=True)
        layers = resolve_scopes(subdir)
        folder_layers = [l for l in layers if l.scope == Scope.FOLDER]
        assert len(folder_layers) >= 1

    def test_file_scope_when_file_specified(self, project_dir: Path):
        src = project_dir / "src"
        src.mkdir()
        target = src / "auth.py"
        target.touch()
        layers = resolve_scopes(project_dir, target_file=target)
        file_layers = [l for l in layers if l.scope == Scope.FILE]
        assert len(file_layers) == 1

    def test_resolution_order(self, project_dir: Path):
        layers = resolve_scopes(project_dir, tool="aider")
        scopes = [l.scope for l in layers]
        assert scopes[0] == Scope.USER
        assert scopes[1] == Scope.TOOL
        assert Scope.PROJECT_LOCAL in scopes
        assert Scope.PROJECT_TEAM in scopes


class TestActiveLayersFilter:
    def test_always_loaded_included(self, project_dir: Path):
        layers = resolve_scopes(project_dir)
        active = active_layers(layers)
        for l in active:
            assert l.always_loaded

    def test_lazy_excluded_by_default(self, project_dir: Path):
        src = project_dir / "src"
        src.mkdir()
        layers = resolve_scopes(src)
        active = active_layers(layers)
        folder = [l for l in active if l.scope == Scope.FOLDER]
        assert len(folder) == 0

    def test_lazy_included_when_requested(self, project_dir: Path):
        src = project_dir / "src"
        src.mkdir()
        (src / ".umx").mkdir()
        layers = resolve_scopes(src)
        active = active_layers(layers, include_lazy=True)
        folder = [l for l in active if l.scope == Scope.FOLDER]
        assert len(folder) >= 1


class TestInitProject:
    def test_creates_umx_structure(self, project_dir: Path):
        init_project(project_dir)
        umx_dir = project_dir / ".umx"
        assert umx_dir.is_dir()
        assert (umx_dir / "topics").is_dir()
        assert (umx_dir / "files").is_dir()
        assert (umx_dir / "local").is_dir()
        assert (umx_dir / "local" / "topics").is_dir()

    def test_updates_gitignore(self, project_dir: Path):
        init_project(project_dir)
        gitignore = project_dir / ".gitignore"
        assert gitignore.exists()
        content = gitignore.read_text()
        assert ".umx/local/" in content
        assert ".umx/dream.lock" in content
        assert "*.umx.json" in content

    def test_idempotent(self, project_dir: Path):
        init_project(project_dir)
        init_project(project_dir)
        gitignore = project_dir / ".gitignore"
        content = gitignore.read_text()
        assert content.count(".umx/local/") == 1


class TestScopeLayer:
    def test_derived_paths(self, tmp_path: Path):
        layer = ScopeLayer(
            scope=Scope.PROJECT_TEAM,
            path=tmp_path / ".umx",
            always_loaded=True,
        )
        assert layer.topics_dir == tmp_path / ".umx" / "topics"
        assert layer.files_dir == tmp_path / ".umx" / "files"
        assert layer.memory_md == tmp_path / ".umx" / "MEMORY.md"
        assert layer.config_yaml == tmp_path / ".umx" / "config.yaml"
