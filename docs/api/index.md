# API reference

gitmem is still a **CLI-first** project. The stable Python API is intentionally small.

The generated pages in this section cover the curated public module surface used for configuration, persisted fact models, and the stdio MCP entrypoint:

- [`umx.config`](config.md)
- [`umx.models`](models.md)
- [`umx.mcp_server`](mcp-server.md)

## Public API policy

Only symbols exported through each module's `__all__` are treated as public API for this reference. Other names in `umx/` remain implementation detail unless documented here.
