""".gitignore parsing for extraction exclusion rules.

During Gather, parses .gitignore and blocks extraction of facts
from interactions heavily referencing gitignored paths. Facts
referencing gitignored content are routed to .umx/local/.
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path


class GitignoreFilter:
    """Parses .gitignore and provides path matching."""

    def __init__(self, patterns: list[str] | None = None) -> None:
        self._patterns: list[str] = []
        self._negations: list[str] = []
        if patterns:
            self._parse_patterns(patterns)

    @classmethod
    def from_file(cls, gitignore_path: Path) -> GitignoreFilter:
        """Create filter from a .gitignore file."""
        if not gitignore_path.exists():
            return cls()
        content = gitignore_path.read_text()
        lines = [
            line.strip() for line in content.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        return cls(lines)

    @classmethod
    def from_project(cls, project_root: Path) -> GitignoreFilter:
        """Create filter from a project's .gitignore."""
        return cls.from_file(project_root / ".gitignore")

    def _parse_patterns(self, lines: list[str]) -> None:
        for line in lines:
            if line.startswith("!"):
                self._negations.append(line[1:])
            else:
                self._patterns.append(line)

    def is_ignored(self, path: str) -> bool:
        """Check if a path matches any gitignore pattern."""
        # Normalise
        path = path.lstrip("/").rstrip("/")

        for neg in self._negations:
            if self._matches(path, neg):
                return False

        for pattern in self._patterns:
            if self._matches(path, pattern):
                return True

        return False

    def _matches(self, path: str, pattern: str) -> bool:
        """Check if path matches a single gitignore pattern."""
        pattern = pattern.rstrip("/")

        # If pattern has no slash, match against basename
        if "/" not in pattern:
            basename = path.rsplit("/", 1)[-1] if "/" in path else path
            if fnmatch.fnmatch(basename, pattern):
                return True
            if fnmatch.fnmatch(path, f"**/{pattern}"):
                return True
        else:
            # Match full path
            if fnmatch.fnmatch(path, pattern):
                return True
            if fnmatch.fnmatch(path, f"{pattern}/**"):
                return True

        # Check if any directory component matches
        parts = path.split("/")
        for i, part in enumerate(parts):
            if fnmatch.fnmatch(part, pattern.rstrip("/")):
                return True

        return False

    def filter_sensitive_facts(
        self,
        text: str,
    ) -> bool:
        """Check if text references gitignored paths heavily.

        Returns True if the text should be routed to local/ instead of team memory.
        """
        # Common sensitive patterns
        sensitive_indicators = [
            r"\.env\b",
            r"secrets?\.",
            r"\.pem\b",
            r"api[_-]?key",
            r"password",
            r"token",
            r"credential",
        ]

        for indicator in sensitive_indicators:
            if re.search(indicator, text, re.IGNORECASE):
                return True

        # Check for paths matching gitignore patterns
        potential_paths = re.findall(r"[\w./\\-]+", text)
        ignored_count = sum(1 for p in potential_paths if self.is_ignored(p))

        # If more than half of referenced paths are ignored, route to local
        return ignored_count > len(potential_paths) // 2 if potential_paths else False
