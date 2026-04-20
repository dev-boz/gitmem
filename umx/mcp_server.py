from __future__ import annotations

import json
import sys
from pathlib import Path

from umx.budget import estimate_tokens

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "umx"
SERVER_VERSION = "0.9.2"

TOOL_DEFINITIONS = [
    {
        "name": "read_memory",
        "description": "Read memory for current project. Returns the injection block.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "cwd": {"type": "string", "description": "Working directory path"},
                "query": {"type": "string", "description": "Optional query to filter facts"},
                "fact_id": {"type": "string", "description": "Optional fact id for exact lookup"},
                "prompt": {"type": "string", "description": "Prompt text for cue-based injection"},
                "file_paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Touched/read file paths for scoped retrieval and procedure matching",
                },
                "max_tokens": {"type": "integer", "description": "Max tokens for injection block"},
                "tool": {"type": "string", "description": "Tool name for context"},
                "command_text": {"type": "string", "description": "Command or tool arguments for pre-tool matching"},
                "session_id": {"type": "string", "description": "Session identifier for telemetry-aware injection"},
                "parent_session_id": {"type": "string", "description": "Parent session id for subagent relay"},
                "context_window_tokens": {"type": "integer", "description": "Full model context window for attention refresh"},
            },
            "required": ["cwd"],
        },
    },
    {
        "name": "write_memory",
        "description": "Write a session event to the in-memory buffer.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "cwd": {"type": "string", "description": "Working directory path"},
                "session_id": {"type": "string", "description": "Session identifier"},
                "context_window_tokens": {"type": "integer", "description": "Optional model context window"},
                "event": {
                    "type": "object",
                    "description": "Event with role, content, and optional ts",
                    "properties": {
                        "role": {"type": "string"},
                        "content": {"type": "string"},
                        "ts": {"type": "string"},
                    },
                    "required": ["role", "content"],
                },
            },
            "required": ["cwd", "session_id", "event"],
        },
    },
    {
        "name": "search_memory",
        "description": "Search facts using full-text search.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "cwd": {"type": "string", "description": "Working directory path"},
                "query": {"type": "string", "description": "Search query"},
            },
            "required": ["cwd", "query"],
        },
    },
    {
        "name": "dream",
        "description": "Trigger the dream consolidation pipeline.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "cwd": {"type": "string", "description": "Working directory path"},
                "force": {"type": "boolean", "description": "Force dream even if gates not met"},
            },
            "required": ["cwd"],
        },
    },
    {
        "name": "status",
        "description": "Get memory status for current project.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "cwd": {"type": "string", "description": "Working directory path"},
            },
            "required": ["cwd"],
        },
    },
]


def _jsonrpc_response(id: object, result: object) -> dict:
    return {"jsonrpc": "2.0", "id": id, "result": result}


def _jsonrpc_error(id: object, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": id, "error": {"code": code, "message": message}}


def _read_framed_message() -> str | None:
    headers: dict[str, str] = {}
    while True:
        line = sys.stdin.readline()
        if not line:
            return None
        if line.startswith("{"):
            return line.strip()
        stripped = line.strip()
        if not stripped:
            break
        if ":" in stripped:
            key, value = stripped.split(":", 1)
            headers[key.strip().lower()] = value.strip()
    content_length = int(headers.get("content-length", "0"))
    if content_length <= 0:
        return None
    return sys.stdin.read(content_length)


def _write_framed_message(payload: dict) -> None:
    body = json.dumps(payload)
    sys.stdout.write(f"Content-Length: {len(body.encode('utf-8'))}\r\n\r\n{body}")
    sys.stdout.flush()


class UMXMCPServer:
    def __init__(self) -> None:
        self._session_buffers: dict[str, list[dict]] = {}

    def handle_initialize(self, params: dict) -> dict:
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        }

    def handle_tools_list(self, params: dict) -> dict:
        return {"tools": TOOL_DEFINITIONS}

    def handle_tools_call(self, params: dict) -> dict:
        name = params.get("name", "")
        args = params.get("arguments", {})
        handler = {
            "read_memory": self._tool_read_memory,
            "write_memory": self._tool_write_memory,
            "search_memory": self._tool_search_memory,
            "dream": self._tool_dream,
            "status": self._tool_status,
        }.get(name)
        if handler is None:
            return {
                "content": [{"type": "text", "text": f"Unknown tool: {name}"}],
                "isError": True,
            }
        try:
            result = handler(args)
            text = json.dumps(result, default=str)
            return {"content": [{"type": "text", "text": text}], "isError": False}
        except Exception as exc:
            return {
                "content": [{"type": "text", "text": f"Error: {exc}"}],
                "isError": True,
            }

    def _tool_read_memory(self, args: dict) -> dict:
        from umx.inject import inject_for_tool
        from umx.memory import find_fact_by_id
        from umx.scope import project_memory_dir, user_memory_dir
        from umx.search import advance_session_state, query_index

        cwd = Path(args["cwd"])
        query = args.get("query")
        fact_id = args.get("fact_id")
        prompt = args.get("prompt")
        file_paths = list(args.get("file_paths", []))
        max_tokens = args.get("max_tokens", 4000)
        tool = args.get("tool")
        command_text = args.get("command_text")
        session_id = args.get("session_id")
        parent_session_id = args.get("parent_session_id")
        context_window_tokens = args.get("context_window_tokens")

        if fact_id:
            repo = project_memory_dir(cwd)
            fact = find_fact_by_id(repo, fact_id) or find_fact_by_id(user_memory_dir(), fact_id)
            if fact is None:
                return {"content": "fact not found"}
            return {"content": json.dumps(fact.to_dict(), sort_keys=True)}

        if query and not prompt and not file_paths:
            repo = project_memory_dir(cwd)
            facts = query_index(repo, query)
            lines = [f"[{f.topic}] {f.text}" for f in facts]
            return {"content": "\n".join(lines) if lines else "No matching facts found."}

        if session_id:
            observed_text = " ".join(
                part
                for part in [prompt or "", tool or "", command_text or "", *file_paths]
                if part
            )
            advance_session_state(
                project_memory_dir(cwd),
                session_id,
                tool=tool,
                parent_session_id=parent_session_id,
                observed_tokens=estimate_tokens(observed_text) if observed_text else None,
                context_window_tokens=context_window_tokens,
            )

        block = inject_for_tool(
            cwd,
            tool=tool,
            prompt=prompt or query,
            file_paths=file_paths,
            max_tokens=max_tokens,
            command_text=command_text,
            session_id=session_id,
            parent_session_id=parent_session_id,
            context_window_tokens=context_window_tokens,
        )
        return {"content": block}

    def _tool_write_memory(self, args: dict) -> dict:
        from umx.session_runtime import record_session_event

        cwd = Path(args["cwd"])
        session_id = args["session_id"]
        context_window_tokens = args.get("context_window_tokens")
        event = args["event"]
        if session_id not in self._session_buffers:
            self._session_buffers[session_id] = []
        self._session_buffers[session_id].append(event)
        record_session_event(
            cwd,
            session_id,
            event,
            tool=event.get("tool", "mcp"),
            context_window_tokens=context_window_tokens,
            persist=True,
            auto_commit=False,
        )
        return {"ok": True}

    def _tool_search_memory(self, args: dict) -> dict:
        from umx.search import query_index
        from umx.scope import project_memory_dir

        cwd = Path(args["cwd"])
        query = args["query"]
        repo = project_memory_dir(cwd)
        facts = query_index(repo, query)
        return {
            "results": [
                {
                    "fact_id": f.fact_id,
                    "topic": f.topic,
                    "text": f.text,
                    "strength": f.encoding_strength,
                }
                for f in facts
            ]
        }

    def _tool_dream(self, args: dict) -> dict:
        from umx.dream.pipeline import DreamPipeline

        cwd = Path(args["cwd"])
        force = args.get("force", False)
        result = DreamPipeline(cwd).run(force=force)
        return {
            "status": result.status,
            "added": result.added,
            "pruned": result.pruned,
            "message": result.message,
        }

    def _tool_status(self, args: dict) -> dict:
        from umx.status import build_status_payload

        cwd = Path(args["cwd"])
        return build_status_payload(cwd)

    def handle_ping(self, params: dict) -> dict:
        return {}

    def handle_resources_list(self, params: dict) -> dict:
        return {"resources": []}

    def handle_prompts_list(self, params: dict) -> dict:
        return {"prompts": []}

    def dispatch(self, request: dict) -> dict | None:
        method = request.get("method", "")
        params = request.get("params", {})
        req_id = request.get("id")

        # Notifications must not be responded to
        if method.startswith("notifications/"):
            return None

        handler = {
            "initialize": self.handle_initialize,
            "ping": self.handle_ping,
            "tools/list": self.handle_tools_list,
            "tools/call": self.handle_tools_call,
            # Return empty collections for optional capability methods
            "resources/list": self.handle_resources_list,
            "prompts/list": self.handle_prompts_list,
        }.get(method)

        if handler is None:
            return _jsonrpc_error(req_id, -32601, f"Method not found: {method}")

        try:
            result = handler(params)
            return _jsonrpc_response(req_id, result)
        except Exception as exc:
            return _jsonrpc_error(req_id, -32603, str(exc))

    def run(self) -> None:
        while True:
            stripped = _read_framed_message()
            if stripped is None:
                return
            try:
                request = json.loads(stripped)
            except json.JSONDecodeError:
                resp = _jsonrpc_error(None, -32700, "Parse error")
                _write_framed_message(resp)
                continue
            response = self.dispatch(request)
            if response is not None:
                _write_framed_message(response)


def run() -> None:
    UMXMCPServer().run()
