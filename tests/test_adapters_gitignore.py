from __future__ import annotations

from pathlib import Path

import pytest

from umx.adapters import all_adapters, get_adapter, ADAPTER_REGISTRY
from umx.adapters.claude_code import ClaudeCodeAdapter
from umx.adapters.copilot import CopilotAdapter
from umx.adapters.aider import AiderAdapter
from umx.dream.gitignore import (
    fact_references_path,
    load_gitignore,
    matches_gitignore,
    route_fact_scope,
    route_facts,
)
from umx.models import (
    CodeAnchor,
    ConsolidationStatus,
    Fact,
    MemoryType,
    Scope,
    SourceType,
    Verification,
)
from umx.identity import generate_fact_id


def _make_fact(text: str = "test fact", scope: Scope = Scope.PROJECT, code_anchor: CodeAnchor | None = None) -> Fact:
    return Fact(
        fact_id=generate_fact_id(),
        text=text,
        scope=scope,
        topic="general",
        encoding_strength=3,
        memory_type=MemoryType.IMPLICIT,
        verification=Verification.SELF_REPORTED,
        source_type=SourceType.TOOL_OUTPUT,
        confidence=0.6,
        source_tool="test",
        source_session="test",
        consolidation_status=ConsolidationStatus.FRAGILE,
        code_anchor=code_anchor,
    )


class TestAdapterRegistry:
    def test_all_adapters_returns_instances(self):
        adapters = all_adapters()
        assert len(adapters) == 3
        names = {a.name for a in adapters}
        assert names == {"claude-code", "copilot", "aider"}

    def test_get_adapter_known(self):
        adapter = get_adapter("claude-code")
        assert isinstance(adapter, ClaudeCodeAdapter)

    def test_get_adapter_unknown_returns_generic(self):
        adapter = get_adapter("unknown-tool")
        assert adapter.name == "generic"
        assert adapter.read_native_memory(Path("/nonexistent")) == []


class TestClaudeCodeAdapter:
    def test_reads_claude_md(self, tmp_path: Path):
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(
            "# Project Setup\n"
            "- The project uses Python 3.11 with pytest for testing\n"
            "- Database connections go through the pool manager\n"
            "\n"
            "# Conventions\n"
            "- All API responses use snake_case field names\n"
            "- Short\n"  # too short, should be skipped
        )
        adapter = ClaudeCodeAdapter()
        facts = adapter.read_native_memory(tmp_path)
        assert len(facts) == 3
        assert all(f.source_type == SourceType.TOOL_OUTPUT for f in facts)
        assert all(f.encoding_strength == 3 for f in facts)
        assert facts[0].topic == "project_setup"

    def test_skips_missing_file(self, tmp_path: Path):
        adapter = ClaudeCodeAdapter()
        facts = adapter.read_native_memory(tmp_path)
        assert facts == []


class TestCopilotAdapter:
    def test_reads_instructions(self, tmp_path: Path):
        gh_dir = tmp_path / ".github"
        gh_dir.mkdir()
        (gh_dir / "copilot-instructions.md").write_text(
            "# Coding Standards\n"
            "- Use TypeScript strict mode for all new files\n"
        )
        adapter = CopilotAdapter()
        facts = adapter.read_native_memory(tmp_path)
        assert len(facts) == 1
        assert "TypeScript" in facts[0].text


class TestAiderAdapter:
    def test_reads_history(self, tmp_path: Path):
        history = tmp_path / ".aider.chat.history.md"
        history.write_text(
            "> Always run tests before committing changes to the repo\n"
            "Some other content\n"
            "> Short\n"  # too short
        )
        adapter = AiderAdapter()
        facts = adapter.read_native_memory(tmp_path)
        assert len(facts) == 1
        assert "tests" in facts[0].text


class TestGitignoreRouting:
    def test_fact_references_code_anchor_path(self):
        fact = _make_fact(
            code_anchor=CodeAnchor(repo="test", path=".env")
        )
        assert fact_references_path(fact) == ".env"

    def test_fact_references_path_in_text(self):
        fact = _make_fact(text="The config is stored in config/secrets.yaml")
        assert fact_references_path(fact) == "config/secrets.yaml"

    def test_fact_no_path_reference(self):
        fact = _make_fact(text="The project uses Python 3.11")
        assert fact_references_path(fact) is None

    def test_route_fact_to_private_for_gitignored_path(self):
        fact = _make_fact(
            text="Database password is stored in .env",
            code_anchor=CodeAnchor(repo="test", path=".env"),
        )
        routed = route_fact_scope(fact, [".env", "node_modules"])
        assert routed.scope == Scope.PROJECT_PRIVATE

    def test_route_fact_unchanged_for_non_gitignored(self):
        fact = _make_fact(
            code_anchor=CodeAnchor(repo="test", path="src/main.py"),
        )
        routed = route_fact_scope(fact, [".env", "node_modules"])
        assert routed.scope == Scope.PROJECT

    def test_route_preserves_already_private(self):
        fact = _make_fact(scope=Scope.PROJECT_PRIVATE)
        routed = route_fact_scope(fact, [".env"])
        assert routed.scope == Scope.PROJECT_PRIVATE

    def test_route_facts_batch(self):
        facts = [
            _make_fact(
                text="Uses .env for config",
                code_anchor=CodeAnchor(repo="test", path=".env"),
            ),
            _make_fact(text="Uses Python 3.11"),
            _make_fact(
                text="Node deps in node_modules/react",
                code_anchor=CodeAnchor(repo="test", path="node_modules/react"),
            ),
        ]
        routed = route_facts(facts, [".env", "node_modules"])
        assert routed[0].scope == Scope.PROJECT_PRIVATE
        assert routed[1].scope == Scope.PROJECT
        assert routed[2].scope == Scope.PROJECT_PRIVATE

    def test_load_gitignore(self, tmp_path: Path):
        gi = tmp_path / ".gitignore"
        gi.write_text(
            "# comment\n"
            ".env\n"
            "node_modules/\n"
            "\n"
            "*.pyc\n"
        )
        patterns = load_gitignore(tmp_path)
        assert patterns == [".env", "node_modules", "*.pyc"]
