from __future__ import annotations

from fnmatch import fnmatch
from pathlib import Path

from umx.models import Fact, Scope


def load_gitignore(project_root: Path) -> list[str]:
    path = project_root / ".gitignore"
    if not path.exists():
        return []
    patterns: list[str] = []
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        patterns.append(line.rstrip("/"))
    return patterns


def matches_gitignore(relative_path: str, patterns: list[str]) -> bool:
    clean = relative_path.strip("/")
    return any(
        fnmatch(clean, pattern) or clean.startswith(pattern.rstrip("/") + "/")
        for pattern in patterns
    )


# Common sensitive path fragments that should route to private
_SENSITIVE_FRAGMENTS = {
    ".env", "secrets", "credentials", "private_key",
    ".pem", ".key", "token", "password",
}


def fact_references_path(fact: Fact) -> str | None:
    """Extract a file path referenced by a fact, if any."""
    if fact.code_anchor and fact.code_anchor.path:
        return fact.code_anchor.path
    # Check fact text for path-like references
    for word in fact.text.split():
        if "/" in word and not word.startswith("http"):
            cleaned = word.strip(".,;:'\"()")
            if cleaned:
                return cleaned
    return None


def route_fact_scope(fact: Fact, gitignore_patterns: list[str]) -> Fact:
    """Route a fact to local/private scope if it references a gitignored path.

    .gitignore routing is a scope heuristic, not a security boundary.
    Redaction is the actual security boundary.
    """
    if fact.scope in {Scope.PROJECT_PRIVATE, Scope.PROJECT_SECRET}:
        return fact
    ref_path = fact_references_path(fact)
    if ref_path and matches_gitignore(ref_path, gitignore_patterns):
        return fact.clone(scope=Scope.PROJECT_PRIVATE)
    return fact


def route_facts(facts: list[Fact], gitignore_patterns: list[str]) -> list[Fact]:
    """Route a list of facts, moving gitignored-path facts to private scope."""
    return [route_fact_scope(fact, gitignore_patterns) for fact in facts]
