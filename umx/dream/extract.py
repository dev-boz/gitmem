from __future__ import annotations

import json
import re
from pathlib import Path

from umx.config import UMXConfig
from umx.dream.gates import read_dream_state
from umx.identity import generate_fact_id
from umx.models import (
    CodeAnchor,
    ConsolidationStatus,
    Fact,
    MemoryType,
    Scope,
    SourceType,
    Verification,
    Provenance,
)
from umx.redaction import redact_candidate_fact_text
from umx.sessions import iter_session_payloads, list_sessions, read_session

_SKIP_PREFIXES = (
    "i ",
    "i'm ",
    "i’m ",
    "i've ",
    "i’ve ",
    "i'll ",
    "i’ll ",
    "let me",
    "sure",
    "ok",
    "okay",
    "here's",
    "here is",
    "here are",
    "next ",
    "now ",
    "once ",
    "after ",
    "before ",
    "then ",
    "first ",
    "second ",
    "finally ",
    "the next ",
    "verification:",
    "state as of",
    "short version",
    "paste this into",
    "what was completed",
    "real dogfood result",
    "main thing still left",
    "concrete next tasks",
    "what is left before",
    "not release blockers",
    "files to open first",
    "useful commands",
    "important repo note",
    "context cleared",
    "added ",
    "updated ",
    "fixed ",
    "tightened ",
    "demoted ",
    "implemented ",
    "imported ",
    "confirmed ",
    "append ",
    "re-run ",
    "rerun ",
    "do one more ",
    "need ",
    "weak topics like ",
    "the system still ",
    "do not ",
    "don't ",
)

_LOW_SIGNAL_PHRASES = (
    "](",
    "capture code is in",
    "command/cli slice is green",
    "search fallback is now green",
    "automated coverage is passing",
    "now exposes",
    "now works",
    "release-ready",
    "test suite",
    "targeted tests",
    "full suite",
    "coverage",
    "verification",
    "audit trail",
    "handoff",
    "worktree",
    "dogfood pass",
    "next pass",
    "next session",
    "in progress",
    "still running",
    "still moving",
    "so far",
)

_VERB_PATTERN = re.compile(
    r"\b(?:is|are|was|were|has|have|had|does|do|did|can|could|should|would|will|shall"
    r"|runs?|uses?|requires?|creates?|returns?|provides?|contains?|supports?"
    r"|stores?|sends?|reads?|writes?|calls?|defines?|includes?|generates?"
    r"|implements?|handles?|processes?|accepts?|connects?|compiles?|builds?"
    r"|expects?|allows?|enables?|configures?|sets?|gets?|starts?|stops?"
    r"|loads?|saves?|parses?|maps?|converts?|validates?|checks?)\b",
    re.IGNORECASE,
)

_TOPIC_SKIP_WORDS = {
    "a", "an", "the", "this", "that", "these", "those",
    "with", "from", "they", "them", "their", "have", "does",
    "been", "will", "each", "when", "what", "were", "also",
    "only", "some", "than", "into", "redacted",
    "i", "im", "ive", "ill", "next", "now", "once", "after",
    "before", "then", "first", "second", "finally", "state",
    "short", "version", "verification", "full", "final", "current",
    "cli", "capture", "session", "sessions", "rollout", "dogfood",
    "dogfooding", "path", "paths", "tests", "test", "docs", "doc",
    "readme", "handoff", "plan", "uses", "runs", "works", "added",
    "updated", "fixed", "tightened", "demoted", "implemented",
    "imported", "confirmed", "retained", "passed", "green",
}


def _looks_factual(sentence: str) -> bool:
    stripped = sentence.strip()
    lowered = stripped.lower()
    if len(stripped) < 10 or len(stripped) > 200:
        return False
    if lowered.startswith(("```", "#")):
        return False
    if stripped.endswith(":"):
        return False
    if any(lowered.startswith(prefix) for prefix in _SKIP_PREFIXES):
        return False
    if stripped.endswith("?"):
        return False
    if any(phrase in lowered for phrase in _LOW_SIGNAL_PHRASES):
        return False
    return bool(_VERB_PATTERN.search(stripped))


def _extract_topic(sentence: str) -> str:
    words = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", sentence)
    for word in words:
        lowered = word.lower()
        if lowered in _TOPIC_SKIP_WORDS:
            continue
        if word[0].isupper() or "_" in word:
            return word.lower()
    for word in words:
        lowered = word.lower()
        if len(word) > 3 and lowered not in _TOPIC_SKIP_WORDS:
            return lowered
    return "general"


def _extract_sentences(text: str) -> list[str]:
    sentences: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("```"):
            continue
        if line.startswith(("- ", "* ")):
            line = line[2:].strip()
        if not line or line.startswith("#"):
            continue
        parts = re.split(r"(?<=[.!?;:])\s+", line)
        for part in parts:
            sentence = part.strip().rstrip(".!")
            if sentence:
                sentences.append(sentence)
    return sentences


def _facts_from_session_payload(
    repo_dir: Path,
    session_id: str,
    events: list[dict],
    config: UMXConfig | None = None,
) -> list[Fact]:
    facts: list[Fact] = []
    if not events:
        return facts

    start = 1 if events and "_meta" in events[0] else 0
    for event in events[start:]:
        role = event.get("role")
        if role not in ("assistant", "tool_result"):
            continue
        content = event.get("content", "")
        if not isinstance(content, str) or not content:
            continue

        sentences = _extract_sentences(content)
        for sentence in sentences:
            if not _looks_factual(sentence):
                continue
            text = redact_candidate_fact_text(sentence, config)
            if not text:
                continue
            topic = _extract_topic(text)
            facts.append(
                Fact(
                    fact_id=generate_fact_id(),
                    text=text,
                    scope=Scope.PROJECT,
                    topic=topic,
                    encoding_strength=2,
                    memory_type=MemoryType.IMPLICIT,
                    verification=Verification.SELF_REPORTED,
                    source_type=SourceType.LLM_INFERENCE,
                    confidence=0.5,
                    source_tool="session-extract",
                    source_session=session_id,
                    consolidation_status=ConsolidationStatus.FRAGILE,
                    provenance=Provenance(
                        extracted_by="dream-gather",
                        sessions=[session_id],
                    ),
                    repo=repo_dir.name,
                )
            )
    return facts


def session_records_to_facts(
    repo_dir: Path,
    config: UMXConfig | None = None,
    *,
    include_archived: bool = True,
    session_ids: set[str] | None = None,
    skip_gathered: bool = True,
) -> list[Fact]:
    state = read_dream_state(repo_dir)
    gathered: list[str] = list(state.get("last_gathered_sessions", []))
    gathered_set = set(gathered)

    facts: list[Fact] = []

    for session_id, events in iter_session_payloads(
        repo_dir,
        include_archived=include_archived,
        session_ids=session_ids,
    ):
        if skip_gathered and session_id in gathered_set:
            continue
        facts.extend(_facts_from_session_payload(repo_dir, session_id, events, config))
    return facts


def mark_sessions_gathered(repo_dir: Path, session_ids: list[str]) -> None:
    state = read_dream_state(repo_dir)
    existing: list[str] = list(state.get("last_gathered_sessions", []))
    merged = list(dict.fromkeys(existing + session_ids))
    # Cap at last 500 entries to prevent unbounded growth
    if len(merged) > 500:
        merged = merged[-500:]
    state["last_gathered_sessions"] = merged
    state_path = repo_dir / "meta" / "dream-state.json"
    state_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


def gap_records_to_facts(repo_dir: Path) -> list[Fact]:
    path = repo_dir / "meta" / "gaps.jsonl"
    if not path.exists():
        return []
    facts: list[Fact] = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        text = redact_candidate_fact_text(record.get("proposed_fact", ""))
        if not text:
            continue
        query = record.get("query", "general")
        topic = query.split()[0].lower() if query else "general"
        session = record.get("session", "gap")
        facts.append(
            Fact(
                fact_id=generate_fact_id(),
                text=text,
                scope=Scope.PROJECT,
                topic=topic,
                encoding_strength=1,
                memory_type=MemoryType.IMPLICIT,
                verification=Verification.SELF_REPORTED,
                source_type=SourceType.LLM_INFERENCE,
                confidence=0.5,
                source_tool="gap",
                source_session=session,
                consolidation_status=ConsolidationStatus.FRAGILE,
                provenance=Provenance(extracted_by="gap-signal", sessions=[session]),
                repo=repo_dir.name,
            )
        )
    return facts


def clear_gap_records(repo_dir: Path) -> None:
    path = repo_dir / "meta" / "gaps.jsonl"
    if path.exists():
        path.write_text("")


_FILE_PATH_PATTERN = re.compile(
    r"""(?:^|[\s"'`(])"""
    r"""((?:[\w./-]+/)?[\w.-]+\.(?:py|js|ts|tsx|jsx|go|rs|rb|java|c|cpp|h|hpp"""
    r"""|yaml|yml|json|toml|ini|cfg|conf|md|txt|sh|bash|sql|html|css|xml"""
    r"""|proto|graphql|Dockerfile|Makefile|\.env[.\w]*)"""
    r""")"""
)

_IGNORE_PATTERNS = {
    "__pycache__", ".git", "node_modules", ".tox", ".mypy_cache",
    ".pytest_cache", "dist", "build", ".egg-info", ".venv", "venv",
}

_MAX_FILE_SIZE = 100_000  # 100KB
_DOC_SUFFIXES = {"md", "rst", "txt"}

_ASSISTANT_FILE_EVIDENCE_PATTERNS = (
    re.compile(r"\b(?:read|opened|inspected|checked|reviewed|parsed|looked at|looked into)\b", re.IGNORECASE),
    re.compile(r"\b(?:found|saw)\b.*\b(?:in|from)\b", re.IGNORECASE),
    re.compile(r"\b(?:check|see)\b.*\.(?:py|js|ts|tsx|jsx|go|rs|rb|java|c|cpp|h|hpp|yaml|yml|json|toml|ini|cfg|conf|md|txt|sh|sql)\b", re.IGNORECASE),
    re.compile(r"\b(?:config|file|module|class|function|docs?|document|plan)\b.*\b(?:is at|lives at|located at)\b", re.IGNORECASE),
    re.compile(
        r"\.(?:py|js|ts|tsx|jsx|go|rs|rb|java|c|cpp|h|hpp|yaml|yml|json|toml|ini|cfg|conf|md|txt|sh|sql)\b"
        r".*\b(?:defines?|contains?|uses?|stores?|configures?|reads?|writes?|imports?|implements?|handles?"
        r"|provides?|returns?|supports?|sets?)\b",
        re.IGNORECASE,
    ),
)


def _is_binary(path: Path) -> bool:
    try:
        chunk = path.read_bytes()[:8192]
        return b"\x00" in chunk
    except OSError:
        return True


def extract_file_references(events: list[dict]) -> set[str]:
    """Parse session events to find file paths that were read/written."""
    paths: set[str] = set()
    for event in events:
        role = event.get("role")
        content = event.get("content", "")
        if not isinstance(content, str):
            continue

        candidate_chunks: list[str]
        if role == "tool_result":
            candidate_chunks = [content]
        elif role == "assistant":
            candidate_chunks = [
                sentence
                for sentence in _extract_sentences(content)
                if any(pattern.search(sentence) for pattern in _ASSISTANT_FILE_EVIDENCE_PATTERNS)
            ]
        else:
            candidate_chunks = []

        for chunk in candidate_chunks:
            for match in _FILE_PATH_PATTERN.finditer(chunk):
                candidate = match.group(1)
                if any(part in _IGNORE_PATTERNS for part in candidate.split("/")):
                    continue
                paths.add(candidate)
    return paths


_TODO_PATTERN = re.compile(
    r"#\s*(?:TODO|FIXME|NOTE|HACK|XXX)[:\s]+(.*)", re.IGNORECASE
)
_CONST_PATTERN = re.compile(
    r"^([A-Z][A-Z0-9_]{2,})\s*[:=]\s*(.+)", re.MULTILINE
)
_IMPORT_PATTERN = re.compile(
    r"^(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))", re.MULTILINE
)
_ENV_VAR_PATTERN = re.compile(
    r"""os\.(?:environ|getenv)\s*[\[(]\s*["']([A-Z_][A-Z0-9_]*)["']"""
)
_PORT_PATTERN = re.compile(
    r"\bport\s*[:=]\s*(\d{2,5})\b", re.IGNORECASE
)
_URL_PATTERN = re.compile(
    r"""["'](https?://[^\s"']+)["']"""
)
_DOCSTRING_PATTERN = re.compile(
    r'"""(.*?)"""', re.DOTALL
)


def _extract_from_python(content: str, path_str: str) -> list[str]:
    statements: list[str] = []
    for m in _IMPORT_PATTERN.finditer(content):
        module = m.group(1) or m.group(2)
        statements.append(f"{path_str} imports {module}")
    for m in _CONST_PATTERN.finditer(content):
        val = m.group(2).strip().strip("\"'")
        if len(val) <= 100:
            statements.append(f"{path_str} defines {m.group(1)} = {val}")
    for m in _DOCSTRING_PATTERN.finditer(content):
        doc = m.group(1).strip()
        first_line = doc.split("\n")[0].strip()
        if 10 <= len(first_line) <= 200 and _VERB_PATTERN.search(first_line):
            statements.append(first_line)
    for m in _TODO_PATTERN.finditer(content):
        statements.append(f"TODO in {path_str}: {m.group(1).strip()}")
    for m in _ENV_VAR_PATTERN.finditer(content):
        statements.append(f"{path_str} reads environment variable {m.group(1)}")
    for m in _PORT_PATTERN.finditer(content):
        statements.append(f"{path_str} uses port {m.group(1)}")
    for m in _URL_PATTERN.finditer(content):
        statements.append(f"{path_str} references URL {m.group(1)}")
    return statements


def _extract_from_config(content: str, path_str: str) -> list[str]:
    statements: list[str] = []
    for m in _TODO_PATTERN.finditer(content):
        statements.append(f"TODO in {path_str}: {m.group(1).strip()}")
    for m in _PORT_PATTERN.finditer(content):
        statements.append(f"{path_str} configures port {m.group(1)}")
    for m in _URL_PATTERN.finditer(content):
        statements.append(f"{path_str} references URL {m.group(1)}")
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("//"):
            continue
        if ":" in stripped or "=" in stripped:
            sep = ":" if ":" in stripped else "="
            key, _, val = stripped.partition(sep)
            key = key.strip().strip('"').strip("'")
            val = val.strip().strip(",").strip('"').strip("'")
            if key and val and 1 < len(val) <= 80 and not val.startswith("{") and not val.startswith("["):
                stmt = f"{path_str} sets {key} to {val}"
                if len(stmt) <= 200:
                    statements.append(stmt)
    return statements


def _extract_from_markdown(content: str, path_str: str) -> list[str]:
    statements: list[str] = []
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if stripped.startswith("- ") or stripped.startswith("* "):
            stripped = stripped[2:].strip()
        if 10 <= len(stripped) <= 200 and _VERB_PATTERN.search(stripped):
            statements.append(stripped)
    return statements


def _extract_statements(content: str, path_str: str) -> list[str]:
    suffix = path_str.rsplit(".", 1)[-1].lower() if "." in path_str else ""
    if suffix == "py":
        return _extract_from_python(content, path_str)
    if suffix in {"yaml", "yml", "json", "toml", "ini", "cfg", "conf"}:
        return _extract_from_config(content, path_str)
    if suffix == "md":
        return _extract_from_markdown(content, path_str)
    # Generic: extract TODOs, ports, URLs, env vars
    statements: list[str] = []
    for m in _TODO_PATTERN.finditer(content):
        statements.append(f"TODO in {path_str}: {m.group(1).strip()}")
    for m in _PORT_PATTERN.finditer(content):
        statements.append(f"{path_str} uses port {m.group(1)}")
    for m in _URL_PATTERN.finditer(content):
        statements.append(f"{path_str} references URL {m.group(1)}")
    return statements


def source_files_to_facts(
    repo_dir: Path,
    project_root: Path,
    session_paths: list[Path],
) -> list[Fact]:
    """Extract facts from source files referenced in sessions."""
    all_refs: set[str] = set()
    session_ids: list[str] = []
    for spath in session_paths:
        events = read_session(spath)
        if not events:
            continue
        session_ids.append(spath.stem)
        # Only assistant/tool-result references imply the file was actually read.
        read_events = [
            event
            for event in events
            if event.get("role") in ("assistant", "tool_result")
        ]
        all_refs.update(extract_file_references(read_events))

    facts: list[Fact] = []
    seen_texts: set[str] = set()
    for ref in sorted(all_refs):
        full_path = project_root / ref
        # Path traversal guard: reject paths escaping project root
        try:
            if not full_path.resolve().is_relative_to(project_root.resolve()):
                continue
        except (ValueError, OSError):
            continue
        if not full_path.is_file():
            continue
        if any(part in _IGNORE_PATTERNS for part in full_path.parts):
            continue
        try:
            size = full_path.stat().st_size
        except OSError:
            continue
        if size > _MAX_FILE_SIZE:
            continue
        if _is_binary(full_path):
            continue

        try:
            content = full_path.read_text(errors="replace")
        except OSError:
            continue

        statements = _extract_statements(content, ref)
        suffix = ref.rsplit(".", 1)[-1].lower() if "." in ref else ""
        if suffix in _DOC_SUFFIXES:
            source_type = SourceType.EXTERNAL_DOC
            encoding_strength = 2
            confidence = 0.7
            consolidation_status = ConsolidationStatus.FRAGILE
        else:
            source_type = SourceType.GROUND_TRUTH_CODE
            encoding_strength = 3
            confidence = 0.9
            consolidation_status = ConsolidationStatus.STABLE
        for stmt in statements:
            key = stmt.strip().lower()
            if key in seen_texts:
                continue
            seen_texts.add(key)
            topic = _extract_topic(stmt)
            facts.append(
                Fact(
                    fact_id=generate_fact_id(),
                    text=stmt,
                    scope=Scope.PROJECT,
                    topic=topic,
                    encoding_strength=encoding_strength,
                    memory_type=MemoryType.IMPLICIT,
                    verification=Verification.SELF_REPORTED,
                    source_type=source_type,
                    confidence=confidence,
                    source_tool="source-extract",
                    source_session=session_ids[0] if session_ids else "source",
                    consolidation_status=consolidation_status,
                    provenance=Provenance(
                        extracted_by="dream-gather-source",
                        sessions=list(session_ids),
                    ),
                    code_anchor=CodeAnchor(
                        repo=repo_dir.name,
                        path=ref,
                    ),
                    repo=repo_dir.name,
                )
            )
    return facts
