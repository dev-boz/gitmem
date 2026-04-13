from __future__ import annotations

from pathlib import Path

import pytest

from umx.mcp_server import UMXMCPServer
from umx.scope import ensure_repo_structure, init_local_umx, init_project_memory


@pytest.fixture
def server():
    return UMXMCPServer()


@pytest.fixture
def project_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "umxhome"
    monkeypatch.setenv("UMX_HOME", str(home))
    init_local_umx()
    from umx.config import default_config, save_config
    from umx.scope import config_path
    save_config(config_path(), default_config())

    project = tmp_path / "project"
    project.mkdir()
    (project / ".git").mkdir()
    init_project_memory(project)
    return project


def _add_fact(project_cwd: Path, topic: str = "general", text: str = "test fact", fact_id: str | None = None) -> str:
    from umx.memory import add_fact
    from umx.models import Fact, Scope, SourceType, Verification, ConsolidationStatus, MemoryType, Provenance
    from umx.scope import project_memory_dir
    from umx.identity import generate_fact_id

    repo = project_memory_dir(project_cwd)
    fact = Fact(
        fact_id=fact_id or generate_fact_id(),
        text=text,
        topic=topic,
        scope=Scope.PROJECT,
        encoding_strength=3,
        memory_type=MemoryType.EXPLICIT_SEMANTIC,
        verification=Verification.SELF_REPORTED,
        source_type=SourceType.LLM_INFERENCE,
        consolidation_status=ConsolidationStatus.STABLE,
        provenance=Provenance(extracted_by="test", sessions=[]),
    )
    add_fact(repo, fact, auto_commit=False)
    return fact.fact_id


class TestToolsListing:
    def test_returns_all_five_tools(self, server: UMXMCPServer):
        result = server.handle_tools_list({})
        tools = result["tools"]
        assert len(tools) == 5
        names = {t["name"] for t in tools}
        assert names == {"read_memory", "write_memory", "search_memory", "dream", "status"}

    def test_each_tool_has_schema(self, server: UMXMCPServer):
        result = server.handle_tools_list({})
        for tool in result["tools"]:
            assert "inputSchema" in tool
            assert tool["inputSchema"]["type"] == "object"


class TestReadMemory:
    def test_returns_injection_block(self, server: UMXMCPServer, project_cwd: Path):
        _add_fact(project_cwd, topic="architecture", text="Uses hexagonal architecture")
        result = server.handle_tools_call({
            "name": "read_memory",
            "arguments": {"cwd": str(project_cwd)},
        })
        assert result["isError"] is False
        import json
        content = json.loads(result["content"][0]["text"])
        assert "content" in content
        assert "UMX Memory" in content["content"]

    def test_with_query(self, server: UMXMCPServer, project_cwd: Path):
        from umx.scope import project_memory_dir
        from umx.search import rebuild_index

        _add_fact(project_cwd, topic="testing", text="Uses pytest for testing")
        repo = project_memory_dir(project_cwd)
        rebuild_index(repo)

        result = server.handle_tools_call({
            "name": "read_memory",
            "arguments": {"cwd": str(project_cwd), "query": "pytest"},
        })
        assert result["isError"] is False

    def test_with_fact_id(self, server: UMXMCPServer, project_cwd: Path):
        fact_id = _add_fact(project_cwd, topic="release", text="deploys run through staging first", fact_id="01TESTFACT0000000000000400")
        result = server.handle_tools_call({
            "name": "read_memory",
            "arguments": {"cwd": str(project_cwd), "fact_id": fact_id},
        })
        assert result["isError"] is False
        import json
        content = json.loads(result["content"][0]["text"])
        assert fact_id in content["content"]

    def test_with_prompt_and_file_paths(self, server: UMXMCPServer, project_cwd: Path):
        _add_fact(project_cwd, topic="devenv", text="postgres runs on 5433 in dev", fact_id="01TESTFACT0000000000000401")
        result = server.handle_tools_call({
            "name": "read_memory",
            "arguments": {
                "cwd": str(project_cwd),
                "prompt": "debug postgres",
                "file_paths": ["docker-compose.yml"],
                "session_id": "mcp-read-001",
                "context_window_tokens": 16000,
            },
        })
        assert result["isError"] is False
        import json
        payload = json.loads(result["content"][0]["text"])
        assert "UMX Memory" in payload["content"]


class TestSearchMemory:
    def test_returns_results(self, server: UMXMCPServer, project_cwd: Path):
        from umx.scope import project_memory_dir
        from umx.search import rebuild_index

        _add_fact(project_cwd, topic="deps", text="Depends on click library")
        repo = project_memory_dir(project_cwd)
        rebuild_index(repo)

        result = server.handle_tools_call({
            "name": "search_memory",
            "arguments": {"cwd": str(project_cwd), "query": "click"},
        })
        assert result["isError"] is False
        import json
        data = json.loads(result["content"][0]["text"])
        assert "results" in data
        assert len(data["results"]) >= 1
        assert data["results"][0]["topic"] == "deps"

    def test_empty_results(self, server: UMXMCPServer, project_cwd: Path):
        from umx.scope import project_memory_dir
        from umx.search import rebuild_index

        repo = project_memory_dir(project_cwd)
        rebuild_index(repo)

        result = server.handle_tools_call({
            "name": "search_memory",
            "arguments": {"cwd": str(project_cwd), "query": "nonexistent_xyzzy"},
        })
        assert result["isError"] is False
        import json
        data = json.loads(result["content"][0]["text"])
        assert data["results"] == []


class TestStatus:
    def test_returns_expected_fields(self, server: UMXMCPServer, project_cwd: Path):
        _add_fact(project_cwd, text="Some fact")
        result = server.handle_tools_call({
            "name": "status",
            "arguments": {"cwd": str(project_cwd)},
        })
        assert result["isError"] is False
        import json
        data = json.loads(result["content"][0]["text"])
        assert "fact_count" in data
        assert "session_count" in data
        assert "last_dream" in data
        assert "hot_tier_tokens" in data
        assert "hot_tier_max" in data
        assert "hot_tier_pct" in data
        assert "metrics" in data
        assert data["fact_count"] >= 1


class TestDream:
    def test_trigger_works(self, server: UMXMCPServer, project_cwd: Path):
        result = server.handle_tools_call({
            "name": "dream",
            "arguments": {"cwd": str(project_cwd), "force": True},
        })
        assert result["isError"] is False
        import json
        data = json.loads(result["content"][0]["text"])
        assert "status" in data
        assert data["status"] in ("ok", "skipped")


class TestWriteMemory:
    def test_buffers_events(self, server: UMXMCPServer, project_cwd: Path):
        result1 = server.handle_tools_call({
            "name": "write_memory",
            "arguments": {
                "cwd": str(project_cwd),
                "session_id": "test-session-001",
                "event": {"role": "user", "content": "Hello"},
            },
        })
        assert result1["isError"] is False

        result2 = server.handle_tools_call({
            "name": "write_memory",
            "arguments": {
                "cwd": str(project_cwd),
                "session_id": "test-session-001",
                "event": {"role": "assistant", "content": "Hi there"},
            },
        })
        assert result2["isError"] is False

        assert len(server._session_buffers["test-session-001"]) == 2
        assert server._session_buffers["test-session-001"][0]["role"] == "user"
        assert server._session_buffers["test-session-001"][1]["role"] == "assistant"

    def test_separate_sessions(self, server: UMXMCPServer, project_cwd: Path):
        server.handle_tools_call({
            "name": "write_memory",
            "arguments": {
                "cwd": str(project_cwd),
                "session_id": "s1",
                "event": {"role": "user", "content": "A"},
            },
        })
        server.handle_tools_call({
            "name": "write_memory",
            "arguments": {
                "cwd": str(project_cwd),
                "session_id": "s2",
                "event": {"role": "user", "content": "B"},
            },
        })
        assert len(server._session_buffers["s1"]) == 1
        assert len(server._session_buffers["s2"]) == 1

    def test_persists_session_file(self, server: UMXMCPServer, project_cwd: Path):
        from umx.scope import project_memory_dir
        from umx.sessions import read_session, session_path

        session_id = "mcp-session-001"
        server.handle_tools_call({
            "name": "write_memory",
            "arguments": {
                "cwd": str(project_cwd),
                "session_id": session_id,
                "event": {"role": "user", "content": "Hello"},
            },
        })
        repo = project_memory_dir(project_cwd)
        path = session_path(repo, session_id)
        assert path.exists()
        session = read_session(path)
        assert session[0]["_meta"]["session_id"] == session_id
        assert session[1]["content"] == "Hello"


class TestJSONRPCErrorHandling:
    def test_unknown_method(self, server: UMXMCPServer):
        response = server.dispatch({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "unknown/method",
            "params": {},
        })
        assert "error" in response
        assert response["error"]["code"] == -32601
        assert "Method not found" in response["error"]["message"]

    def test_unknown_tool(self, server: UMXMCPServer):
        response = server.dispatch({
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "nonexistent_tool", "arguments": {}},
        })
        assert "result" in response
        assert response["result"]["isError"] is True

    def test_initialize(self, server: UMXMCPServer):
        response = server.dispatch({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {},
        })
        assert response["result"]["protocolVersion"] == "2024-11-05"
        assert response["result"]["serverInfo"]["name"] == "umx"

    def test_notification_returns_none(self, server: UMXMCPServer):
        response = server.dispatch({
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {},
        })
        assert response is None

    def test_tools_list_via_dispatch(self, server: UMXMCPServer):
        response = server.dispatch({
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/list",
            "params": {},
        })
        assert "result" in response
        assert len(response["result"]["tools"]) == 5

    def test_ping_returns_empty_result(self, server: UMXMCPServer):
        response = server.dispatch({
            "jsonrpc": "2.0",
            "id": 4,
            "method": "ping",
            "params": {},
        })
        assert "result" in response
        assert response["result"] == {}

    def test_resources_list_returns_empty(self, server: UMXMCPServer):
        response = server.dispatch({
            "jsonrpc": "2.0",
            "id": 5,
            "method": "resources/list",
            "params": {},
        })
        assert "result" in response
        assert response["result"]["resources"] == []

    def test_prompts_list_returns_empty(self, server: UMXMCPServer):
        response = server.dispatch({
            "jsonrpc": "2.0",
            "id": 6,
            "method": "prompts/list",
            "params": {},
        })
        assert "result" in response
        assert response["result"]["prompts"] == []

    def test_any_notification_returns_none(self, server: UMXMCPServer):
        for notification in ["notifications/initialized", "notifications/cancelled", "notifications/progress"]:
            response = server.dispatch({
                "jsonrpc": "2.0",
                "method": notification,
                "params": {},
            })
            assert response is None, f"Expected None for {notification}"
