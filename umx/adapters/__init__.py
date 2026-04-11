from __future__ import annotations

from umx.adapters.aider import AiderAdapter
from umx.adapters.claude_code import ClaudeCodeAdapter
from umx.adapters.copilot import CopilotAdapter
from umx.adapters.generic import NativeMemoryAdapter

ADAPTER_REGISTRY: dict[str, type[NativeMemoryAdapter]] = {
    "claude-code": ClaudeCodeAdapter,
    "copilot": CopilotAdapter,
    "aider": AiderAdapter,
}


def get_adapter(name: str) -> NativeMemoryAdapter:
    """Return an adapter instance by name, falling back to generic."""
    cls = ADAPTER_REGISTRY.get(name, NativeMemoryAdapter)
    return cls()


def all_adapters() -> list[NativeMemoryAdapter]:
    """Return instances of all known adapters."""
    return [cls() for cls in ADAPTER_REGISTRY.values()]


def get_adapter_by_name(name: str) -> NativeMemoryAdapter:
    """Get adapter by name."""
    return get_adapter(name)


__all__ = [
    "NativeMemoryAdapter",
    "ClaudeCodeAdapter",
    "CopilotAdapter",
    "AiderAdapter",
    "ADAPTER_REGISTRY",
    "get_adapter",
    "get_adapter_by_name",
    "all_adapters",
]
