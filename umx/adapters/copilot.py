"""Copilot native memory adapter.

Format TBD — placeholder adapter.
"""

from __future__ import annotations

from pathlib import Path

from umx.models import Fact


class CopilotAdapter:
    """Adapter for GitHub Copilot's native memory."""

    tool_name = "copilot"

    def read_native_memory(self, project_root: Path) -> list[Fact]:
        """Read facts from Copilot's memory store.

        Format TBD — returns empty list until format is documented.
        """
        return []
