from __future__ import annotations

from collections.abc import Iterable, Iterator
import json
import re
from pathlib import Path

from umx.config import UMXConfig
from umx.continuity import list_handover_paths
from umx.dream.gates import read_dream_state
from umx.dream.providers import ProviderExtractionResult, run_session_provider_extraction
from umx.git_ops import git_blob_sha
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
from umx.trust_tags import UNTRUSTED_SOURCE_TAG

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
    "focused slices are clean",
    "session-extract candidates",
    "in progress",
    "still running",
    "still moving",
    "so far",
)

_CODEX_LOW_SIGNAL_PHRASES = (
    "full-suite",
    "stricter check",
    "earlier snapshot",
)

_FIRST_PERSON_PROGRESS_PATTERN = re.compile(
    r"\b(?:i['’]m|i am)\s+"
    r"(?:picking|collecting|leaving|reading|verifying|checking|inspecting|looking"
    r"|moving|waiting|measuring|tuning|locking|running|starting|continuing"
    r"|focusing|shifting|editing|writing|updating|recording|staging|committing"
    r"|pushing|summarizing|logging|polling|creating|closing|finishing)\b"
    r"|\b(?:i['’]ll|i will)\s+"
    r"(?:pick|collect|leave|read|verify|check|inspect|look|move|wait|measure"
    r"|tune|lock|run|start|continue|focus|shift|edit|write|update|record"
    r"|stage|commit|push|summarize|log|poll|create|close|finish|stop)\b",
    re.IGNORECASE,
)

_OPERATIONAL_STATUS_PATTERNS = (
    re.compile(r"\bstill\s+(?:going|running|empty|pending)\b", re.IGNORECASE),
    re.compile(r"\bin the background\b", re.IGNORECASE),
    re.compile(r"\bsuite is green\b", re.IGNORECASE),
    re.compile(r"^that means\b", re.IGNORECASE),
    re.compile(r"^if\b.*\bneeds?\s+work\b", re.IGNORECASE),
)

_CODEX_META_PATTERNS = (
    re.compile(r"\bleft\s+(?:alone|untouched)\b", re.IGNORECASE),
    re.compile(r"\bcommit\s+(?:is|was)\s+(?:created|pushed?)\b", re.IGNORECASE),
    re.compile(r"\brepo\s+is\s+using\b.*\bidentity\b", re.IGNORECASE),
    re.compile(r"^if this repo is still pinned to\b", re.IGNORECASE),
    re.compile(r"\bdogfooding_tests_results_changes\.md\b", re.IGNORECASE),
    re.compile(r"\bwrong author\b", re.IGNORECASE),
)

_VERB_PATTERN = re.compile(
    r"\b(?:is|are|was|were|has|have|had|does|do|did|can|could|should|would|will|shall"
    r"|runs?|uses?|requires?|creates?|returns?|provides?|contains?|supports?"
    r"|stores?|sends?|reads?|writes?|calls?|defines?|includes?|generates?"
    r"|implements?|handles?|processes?|accepts?|connects?|compiles?|builds?"
    r"|expects?|allows?|enables?|configures?|sets?|gets?|starts?|stops?"
    r"|loads?|saves?|parses?|maps?|converts?|validates?|checks?|expires?)\b",
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


def _session_meta(events: list[dict]) -> dict[str, object]:
    if not events:
        return {}
    meta = events[0].get("_meta")
    if isinstance(meta, dict):
        return meta
    return {}


def _is_codex_rollout_session(events: list[dict]) -> bool:
    meta = _session_meta(events)
    return meta.get("tool") == "codex" or meta.get("source") == "codex-rollout"


def _looks_operational_status(sentence: str, *, codex_rollout: bool = False) -> bool:
    if _FIRST_PERSON_PROGRESS_PATTERN.search(sentence):
        return True
    if codex_rollout and any(pattern.search(sentence) for pattern in _CODEX_META_PATTERNS):
        return True
    return any(pattern.search(sentence) for pattern in _OPERATIONAL_STATUS_PATTERNS)


def _looks_factual(sentence: str, *, codex_rollout: bool = False) -> bool:
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
    if codex_rollout and any(phrase in lowered for phrase in _CODEX_LOW_SIGNAL_PHRASES):
        return False
    if _looks_operational_status(stripped, codex_rollout=codex_rollout):
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
    is_codex_rollout = _is_codex_rollout_session(events)
    for event in events[start:]:
        role = event.get("role")
        if role not in ("assistant", "tool_result"):
            continue
        content = event.get("content", "")
        if not isinstance(content, str) or not content:
            continue

        sentences = _extract_sentences(content)
        for sentence in sentences:
            if not _looks_factual(sentence, codex_rollout=is_codex_rollout):
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


def _session_payloads_to_facts_with_report(
    repo_dir: Path,
    payloads: Iterable[tuple[str, list[dict]]],
    config: UMXConfig | None = None,
    *,
    skip_gathered: bool = True,
    skip_session_ids: set[str] | None = None,
) -> tuple[list[Fact], list[ProviderExtractionResult]]:
    state = read_dream_state(repo_dir)
    gathered: list[str] = list(state.get("last_gathered_sessions", []))
    gathered_set = set(gathered)
    skipped = skip_session_ids or set()

    facts: list[Fact] = []
    reports: list[ProviderExtractionResult] = []
    seen_session_ids: set[str] = set()

    for session_id, events in payloads:
        if not session_id or session_id in skipped or session_id in seen_session_ids:
            continue
        seen_session_ids.add(session_id)
        if skip_gathered and session_id in gathered_set:
            continue
        result = run_session_provider_extraction(
            repo_dir,
            session_id,
            events,
            config,
            native_extractor=lambda session_id=session_id, events=events: _facts_from_session_payload(
                repo_dir,
                session_id,
                events,
                config,
            ),
        )
        facts.extend(result.facts)
        reports.append(result)
    return facts, reports


def session_records_to_facts(
    repo_dir: Path,
    config: UMXConfig | None = None,
    *,
    include_archived: bool = True,
    session_ids: set[str] | None = None,
    skip_gathered: bool = True,
) -> list[Fact]:
    facts, _ = session_records_to_facts_with_report(
        repo_dir,
        config=config,
        include_archived=include_archived,
        session_ids=session_ids,
        skip_gathered=skip_gathered,
    )
    return facts


def session_records_to_facts_with_report(
    repo_dir: Path,
    config: UMXConfig | None = None,
    *,
    include_archived: bool = True,
    session_ids: set[str] | None = None,
    skip_gathered: bool = True,
) -> tuple[list[Fact], list[ProviderExtractionResult]]:
    return _session_payloads_to_facts_with_report(
        repo_dir,
        iter_session_payloads(
            repo_dir,
            include_archived=include_archived,
            session_ids=session_ids,
        ),
        config,
        skip_gathered=skip_gathered,
    )


def list_workspace_transcripts(project_root: Path) -> list[Path]:
    transcripts_dir = project_root / "workspace" / "transcripts"
    if not transcripts_dir.exists():
        return []
    return sorted(transcripts_dir.glob("*.jsonl"))


def iter_workspace_transcript_payloads(project_root: Path) -> Iterator[tuple[str, list[dict]]]:
    for path in list_workspace_transcripts(project_root):
        events = [event for event in read_session(path) if isinstance(event, dict)]
        if not events:
            continue
        yield path.stem, events


def workspace_transcript_records_to_facts_with_report(
    project_root: Path,
    repo_dir: Path,
    config: UMXConfig | None = None,
    *,
    skip_gathered: bool = True,
    skip_session_ids: set[str] | None = None,
) -> tuple[list[Fact], list[ProviderExtractionResult]]:
    return _session_payloads_to_facts_with_report(
        repo_dir,
        iter_workspace_transcript_payloads(project_root),
        config,
        skip_gathered=skip_gathered,
        skip_session_ids=skip_session_ids,
    )


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


def _workspace_candidate_fact(
    repo_dir: Path,
    text: str,
    *,
    source_session: str,
    source_tool: str,
    config: UMXConfig | None = None,
    encoding_context: dict[str, object] | None = None,
) -> Fact | None:
    redacted = redact_candidate_fact_text(text, config)
    if not redacted:
        return None
    return Fact(
        fact_id=generate_fact_id(),
        text=redacted,
        scope=Scope.PROJECT,
        topic=_extract_topic(redacted),
        encoding_strength=1,
        memory_type=MemoryType.IMPLICIT,
        verification=Verification.SELF_REPORTED,
        source_type=SourceType.LLM_INFERENCE,
        confidence=0.5,
        source_tool=source_tool,
        source_session=source_session,
        consolidation_status=ConsolidationStatus.FRAGILE,
        provenance=Provenance(
            extracted_by="workspace-dream-candidate",
            sessions=[source_session],
        ),
        encoding_context=encoding_context or {},
        repo=repo_dir.name,
    )


def _workspace_candidate_records(path: Path) -> list[dict]:
    suffix = path.suffix.lower()
    if suffix == ".jsonl":
        try:
            raw_lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        records: list[dict] = []
        for line in raw_lines:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                records.append(record)
        return records
    if suffix == ".json":
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        if isinstance(payload, dict):
            return [payload]
        if isinstance(payload, list):
            return [record for record in payload if isinstance(record, dict)]
    return []


def workspace_dream_candidates_to_facts(
    project_root: Path,
    repo_dir: Path,
    config: UMXConfig | None = None,
) -> list[Fact]:
    candidates_dir = project_root / "workspace" / "dream-candidates"
    if not candidates_dir.exists():
        return []

    facts: list[Fact] = []
    seen_texts: set[str] = set()
    for path in sorted(candidates_dir.rglob("*")):
        if not path.is_file():
            continue

        if path.suffix.lower() in {".json", ".jsonl"}:
            for record in _workspace_candidate_records(path):
                content = record.get("content") or record.get("proposed_fact") or record.get("text")
                if not isinstance(content, str) or not content.strip():
                    continue
                key = content.strip().lower()
                if key in seen_texts:
                    continue
                seen_texts.add(key)
                source_tool = record.get("source")
                if not isinstance(source_tool, str) or not source_tool.strip():
                    source_tool = "workspace-dream-candidate"
                source_session = record.get("session_id")
                if not isinstance(source_session, str) or not source_session.strip():
                    source_session = path.stem
                encoding_context = {
                    key: value
                    for key, value in {
                        "metadata": record.get("metadata"),
                        "task_class": record.get("task_class"),
                        "trigger_type": record.get("trigger_type"),
                        "workspace_candidate_path": str(path.relative_to(project_root)),
                    }.items()
                    if value is not None
                }
                fact = _workspace_candidate_fact(
                    repo_dir,
                    content,
                    source_session=source_session,
                    source_tool=source_tool,
                    config=config,
                    encoding_context=encoding_context,
                )
                if fact is not None:
                    facts.append(fact)
            continue

        for sentence in _extract_from_markdown(path.read_text(encoding="utf-8"), path.name):
            key = sentence.strip().lower()
            if key in seen_texts:
                continue
            seen_texts.add(key)
            fact = _workspace_candidate_fact(
                repo_dir,
                sentence,
                source_session=path.stem,
                source_tool="workspace-dream-candidate",
                config=config,
                encoding_context={"workspace_candidate_path": str(path.relative_to(project_root))},
            )
            if fact is not None:
                facts.append(fact)
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


def handover_records_to_facts(
    repo_dir: Path,
    config: UMXConfig | None = None,
) -> list[Fact]:
    facts: list[Fact] = []
    seen_texts: set[str] = set()
    for path in list_handover_paths(repo_dir):
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        try:
            relative = path.relative_to(repo_dir).as_posix()
        except ValueError:
            relative = path.as_posix()
        source_session = f"handover:{path.stem}"
        for statement in _extract_from_markdown(content, relative):
            redacted = redact_candidate_fact_text(statement, config)
            if not redacted:
                continue
            key = redacted.strip().lower()
            if key in seen_texts:
                continue
            seen_texts.add(key)
            facts.append(
                Fact(
                    fact_id=generate_fact_id(),
                    text=redacted,
                    scope=Scope.PROJECT,
                    topic=_extract_topic(redacted),
                    encoding_strength=3,
                    memory_type=MemoryType.EXPLICIT_SEMANTIC,
                    verification=Verification.SELF_REPORTED,
                    source_type=SourceType.TOOL_OUTPUT,
                    confidence=0.6,
                    source_tool="handover",
                    source_session=source_session,
                    consolidation_status=ConsolidationStatus.FRAGILE,
                    provenance=Provenance(
                        extracted_by="handover",
                        sessions=[source_session],
                    ),
                    encoding_context={"handover_path": relative},
                    repo=repo_dir.name,
                )
            )
    return facts


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
            tags = [UNTRUSTED_SOURCE_TAG]
        else:
            source_type = SourceType.GROUND_TRUTH_CODE
            encoding_strength = 3
            confidence = 0.9
            consolidation_status = ConsolidationStatus.STABLE
            tags = []
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
                    tags=tags,
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
                        git_sha=git_blob_sha(full_path),
                    ),
                    repo=repo_dir.name,
                )
            )
    return facts
