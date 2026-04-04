"""Tests for umx.bridge — legacy file bridge."""

from __future__ import annotations

from pathlib import Path

import pytest

from umx.bridge import UMX_END_MARKER, UMX_START_MARKER, remove_bridge, write_bridge
from umx.memory import add_fact
from umx.models import Fact, MemoryType, Scope
from umx.scope import init_project


def _make_fact(text: str) -> Fact:
    return Fact(
        id=Fact.generate_id(),
        text=text,
        scope=Scope.PROJECT_TEAM,
        topic="general",
        encoding_strength=4,
        memory_type=MemoryType.EXPLICIT_SEMANTIC,
        confidence=0.9,
    )


@pytest.fixture
def project(tmp_path: Path) -> Path:
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / ".git").mkdir()
    init_project(project_root)
    return project_root


class TestWriteBridge:
    def test_creates_new_files(self, project: Path):
        umx_dir = project / ".umx"
        add_fact(umx_dir, _make_fact("postgres on 5433"))
        add_fact(umx_dir, _make_fact("use pytest -x"))

        written = write_bridge(project)
        assert len(written) == 2

        for path in written:
            content = path.read_text()
            assert UMX_START_MARKER in content
            assert UMX_END_MARKER in content
            assert "postgres on 5433" in content

    def test_updates_existing_file(self, project: Path):
        claude_md = project / "CLAUDE.md"
        claude_md.write_text("# Project Info\n\nExisting content.\n")

        umx_dir = project / ".umx"
        add_fact(umx_dir, _make_fact("new fact"))

        write_bridge(project, target_files=["CLAUDE.md"])
        content = claude_md.read_text()

        assert "Existing content." in content
        assert "new fact" in content
        assert UMX_START_MARKER in content

    def test_replaces_existing_bridge(self, project: Path):
        claude_md = project / "CLAUDE.md"
        umx_dir = project / ".umx"

        add_fact(umx_dir, _make_fact("first version"))
        write_bridge(project, target_files=["CLAUDE.md"])

        # Clear and add new facts
        add_fact(umx_dir, _make_fact("second version"))
        write_bridge(project, target_files=["CLAUDE.md"])

        content = claude_md.read_text()
        assert content.count(UMX_START_MARKER) == 1

    def test_no_facts_no_write(self, project: Path):
        written = write_bridge(project)
        assert written == []


class TestRemoveBridge:
    def test_removes_bridge_section(self, project: Path):
        umx_dir = project / ".umx"
        add_fact(umx_dir, _make_fact("temp fact"))
        write_bridge(project, target_files=["CLAUDE.md"])

        # Verify it was written
        assert UMX_START_MARKER in (project / "CLAUDE.md").read_text()

        remove_bridge(project, target_files=["CLAUDE.md"])
        content = (project / "CLAUDE.md").read_text()
        assert UMX_START_MARKER not in content
        assert "temp fact" not in content
