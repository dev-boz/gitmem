from __future__ import annotations

import json
from pathlib import Path

import pytest

from umx.adapters import all_adapters, get_adapter, ADAPTER_REGISTRY
from umx.adapters.claude_code import ClaudeCodeAdapter
from umx.adapters.copilot import CopilotAdapter
from umx.adapters.aider import AiderAdapter
from umx.adapters.gemini import GeminiAdapter
from umx.claude_code_capture import _project_hash
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
        memory_type=MemoryType.EXPLICIT_SEMANTIC,
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
        assert len(adapters) == 4
        names = {a.name for a in adapters}
        assert names == {"claude-code", "copilot", "gemini", "aider"}

    def test_get_adapter_known(self):
        adapter = get_adapter("claude-code")
        assert isinstance(adapter, ClaudeCodeAdapter)

    def test_get_gemini_adapter_known(self):
        adapter = get_adapter("gemini")
        assert isinstance(adapter, GeminiAdapter)

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
        assert all(f.memory_type == MemoryType.EXPLICIT_SEMANTIC for f in facts)
        assert all(f.encoding_strength == 3 for f in facts)
        assert facts[0].topic == "project_setup"

    def test_skips_missing_file(self, tmp_path: Path):
        adapter = ClaudeCodeAdapter()
        facts = adapter.read_native_memory(tmp_path)
        assert facts == []

    def test_reads_project_transcripts(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        project_root = tmp_path / "project"
        project_root.mkdir()
        fake_home = tmp_path / "home"
        monkeypatch.setattr(Path, "home", lambda: fake_home)

        session_path = (
            fake_home
            / ".claude"
            / "projects"
            / _project_hash(project_root)
            / "session-1.jsonl"
        )
        session_path.parent.mkdir(parents=True)
        session_records = [
            {
                "type": "system",
                "cwd": str(project_root),
                "version": "1.0.0",
                "slug": "project",
                "timestamp": "2026-01-15T10:00:00.000Z",
            },
            {
                "type": "assistant",
                "isSidechain": False,
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "The service uses FastAPI for its API layer."}],
                },
                "timestamp": "2026-01-15T10:00:05.000Z",
            },
        ]
        session_path.write_text(
            "".join(json.dumps(record) + "\n" for record in session_records),
            encoding="utf-8",
        )

        adapter = ClaudeCodeAdapter()
        facts = adapter.read_native_memory(project_root)

        assert len(facts) == 1
        assert facts[0].source_tool == "claude-code"
        assert facts[0].source_type == SourceType.TOOL_OUTPUT
        assert facts[0].memory_type == MemoryType.EXPLICIT_SEMANTIC
        assert "FastAPI" in facts[0].text
        assert facts[0].encoding_context["native_store_path"] == str(session_path)


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

    def test_reads_project_sessions_only(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        project_root = tmp_path / "project"
        (project_root / "src").mkdir(parents=True)
        fake_home = tmp_path / "home"
        monkeypatch.setattr(Path, "home", lambda: fake_home)

        project_session_path = (
            fake_home
            / ".copilot"
            / "session-state"
            / "session-1"
            / "events.jsonl"
        )
        project_session_path.parent.mkdir(parents=True)
        project_events = [
            {
                "type": "session.start",
                "data": {
                    "sessionId": "session-1",
                    "copilotVersion": "1.0.24",
                    "startTime": "2026-04-12T04:02:46.320Z",
                    "context": {"cwd": str(project_root / "src")},
                },
                "timestamp": "2026-04-12T04:02:46.471Z",
            },
            {
                "type": "assistant.message",
                "data": {"content": "The service uses Redis for caching."},
                "timestamp": "2026-04-12T04:05:05.000Z",
            },
        ]
        project_session_path.write_text(
            "".join(json.dumps(event, sort_keys=True) + "\n" for event in project_events),
            encoding="utf-8",
        )

        foreign_session_path = (
            fake_home
            / ".copilot"
            / "session-state"
            / "session-2"
            / "events.jsonl"
        )
        foreign_session_path.parent.mkdir(parents=True)
        foreign_events = [
            {
                "type": "session.start",
                "data": {
                    "sessionId": "session-2",
                    "copilotVersion": "1.0.24",
                    "startTime": "2026-04-12T04:02:46.320Z",
                    "context": {"cwd": str(tmp_path / "elsewhere")},
                },
                "timestamp": "2026-04-12T04:02:46.471Z",
            },
            {
                "type": "assistant.message",
                "data": {"content": "The background worker uses RabbitMQ."},
                "timestamp": "2026-04-12T04:05:05.000Z",
            },
        ]
        foreign_session_path.write_text(
            "".join(json.dumps(event, sort_keys=True) + "\n" for event in foreign_events),
            encoding="utf-8",
        )

        adapter = CopilotAdapter()
        facts = adapter.read_native_memory(project_root)

        assert len(facts) == 1
        assert facts[0].source_tool == "copilot"
        assert "Redis" in facts[0].text
        assert facts[0].encoding_context["native_store_path"] == str(project_session_path)


class TestGeminiAdapter:
    def test_reads_gemini_sessions(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        project_root = tmp_path / "project"
        project_root.mkdir()
        fake_home = tmp_path / "home"
        monkeypatch.setattr(Path, "home", lambda: fake_home)

        gemini_root = fake_home / ".gemini"
        projects_json = gemini_root / "projects.json"
        projects_json.parent.mkdir(parents=True)
        projects_json.write_text(
            json.dumps({"projects": {str(project_root.resolve()): "project-slug"}}),
            encoding="utf-8",
        )

        session_path = gemini_root / "tmp" / "project-slug" / "chats" / "session-1.json"
        session_path.parent.mkdir(parents=True)
        session_path.write_text(
            json.dumps(
                {
                    "sessionId": "gemini-session-1",
                    "startTime": "2026-04-10T10:00:00.000Z",
                    "lastUpdated": "2026-04-10T10:05:00.000Z",
                    "messages": [
                        {
                            "type": "user",
                            "timestamp": "2026-04-10T10:00:01.000Z",
                            "content": [{"text": "what stores the chat"}],
                        },
                        {
                            "type": "gemini",
                            "timestamp": "2026-04-10T10:00:05.000Z",
                            "content": "The CLI stores sessions in JSON files.",
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )

        adapter = GeminiAdapter()
        facts = adapter.read_native_memory(project_root)

        assert len(facts) == 1
        assert facts[0].source_tool == "gemini"
        assert facts[0].source_type == SourceType.TOOL_OUTPUT
        assert "JSON files" in facts[0].text
        assert facts[0].encoding_context["native_store_path"] == str(session_path)


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
