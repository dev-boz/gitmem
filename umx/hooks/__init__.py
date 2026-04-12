"""Hook entrypoints."""

from __future__ import annotations

from typing import Any

from umx.hooks import assistant_output, post_tool_use, pre_compact, pre_tool_use, session_end, session_start, subagent_start

__all__ = [
    "session_start",
    "session_end",
    "assistant_output",
    "pre_compact",
    "pre_tool_use",
    "post_tool_use",
    "subagent_start",
    "dispatch_hook",
]

_HOOKS = {
    "session_start": session_start.run,
    "session_end": session_end.run,
    "assistant_output": assistant_output.run,
    "pre_compact": pre_compact.run,
    "pre_tool_use": pre_tool_use.run,
    "post_tool_use": post_tool_use.run,
    "subagent_start": subagent_start.run,
}


def dispatch_hook(hook_name: str, **kwargs: Any) -> Any:
    """Route to the correct hook by name."""
    handler = _HOOKS.get(hook_name)
    if handler is None:
        raise ValueError(f"Unknown hook: {hook_name}")
    return handler(**kwargs)
