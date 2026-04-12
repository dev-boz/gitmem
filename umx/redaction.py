from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from math import log2
from typing import Any

from umx.config import UMXConfig, default_config


BUILTIN_PATTERNS: list[tuple[str, str]] = [
    ("aws-key", r"AKIA[0-9A-Z]{16}"),
    ("openai-key", r"sk-[A-Za-z0-9]{20,}"),
    ("anthropic-key", r"sk-ant-[A-Za-z0-9_-]{20,}"),
    ("bearer-token", r"Bearer\s+[A-Za-z0-9._-]{16,}"),
    ("jwt", r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9._-]+\.[A-Za-z0-9._-]+\b"),
    (
        "private-key",
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]+?-----END [A-Z ]*PRIVATE KEY-----",
    ),
    (
        "connection-string",
        r"\b[a-zA-Z][a-zA-Z0-9+.-]*://[^/\s:@]+:[^@\s]+@[^/\s]+\b",
    ),
]

ASSIGNMENT_PATTERNS = [
    r"(?P<prefix>\b(?:api[_-]?key|token|secret|password)\b\s*[:=]\s*[\"']?)(?P<value>[A-Za-z0-9+/=_-]{16,})(?P<suffix>[\"']?)",
    r"(?P<prefix>\bAuthorization\b\s*:\s*Bearer\s+)(?P<value>[A-Za-z0-9._-]{16,})(?P<suffix>)",
]


class RedactionError(RuntimeError):
    pass


@dataclass(slots=True)
class RedactionIssue:
    kind: str
    match: str


@dataclass(slots=True)
class RedactionResult:
    text: str
    issues: list[RedactionIssue] = field(default_factory=list)


def shannon_entropy(text: str) -> float:
    if not text:
        return 0.0
    counts = Counter(text)
    length = len(text)
    return -sum((count / length) * log2(count / length) for count in counts.values())


def redact_text(text: str, config: UMXConfig | None = None) -> RedactionResult:
    cfg = config or default_config()
    issues: list[RedactionIssue] = []
    result = text

    for kind, pattern in BUILTIN_PATTERNS + [
        ("custom", pattern) for pattern in cfg.sessions.redaction_patterns
    ]:
        compiled = re.compile(pattern)
        matches = list(compiled.finditer(result))
        for match in matches:
            issues.append(RedactionIssue(kind=kind, match=match.group(0)))
        result = compiled.sub(f"[REDACTED:{kind}]", result)

    for pattern in ASSIGNMENT_PATTERNS + cfg.sessions.entropy_assignment_patterns:
        compiled = re.compile(pattern, re.IGNORECASE)

        def replace(match: re.Match[str]) -> str:
            value = match.group("value")
            if len(value) < cfg.sessions.entropy_min_length:
                return match.group(0)
            if shannon_entropy(value) < cfg.sessions.entropy_threshold:
                return match.group(0)
            issues.append(RedactionIssue(kind="high-entropy", match=value))
            prefix = match.groupdict().get("prefix", "")
            suffix = match.groupdict().get("suffix", "")
            return f"{prefix}[REDACTED:high-entropy]{suffix}"

        result = compiled.sub(replace, result)

    return RedactionResult(text=result, issues=issues)


def _redact_obj(obj: Any, config: UMXConfig) -> Any:
    if isinstance(obj, str):
        return redact_text(obj, config).text
    if isinstance(obj, list):
        return [_redact_obj(item, config) for item in obj]
    if isinstance(obj, dict):
        return {key: _redact_obj(value, config) for key, value in obj.items()}
    return obj


def redact_json_record(record: dict[str, Any], config: UMXConfig | None = None) -> dict[str, Any]:
    cfg = config or default_config()
    try:
        return _redact_obj(record, cfg)
    except Exception as exc:  # pragma: no cover - defensive
        raise RedactionError(str(exc)) from exc


def redact_jsonl_lines(lines: list[dict[str, Any]], config: UMXConfig | None = None) -> list[dict[str, Any]]:
    cfg = config or default_config()
    return [redact_json_record(line, cfg) for line in lines]


def redact_candidate_fact_text(text: str, config: UMXConfig | None = None) -> str:
    return redact_text(text, config).text
