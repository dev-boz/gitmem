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


def _is_regex_quantifier(pattern: str, index: int) -> bool:
    if index >= len(pattern):
        return False
    if pattern[index] in "*+?":
        return True
    if pattern[index] != "{":
        return False
    closing = pattern.find("}", index + 1)
    return closing != -1


def _find_unsafe_redaction_construct(pattern: str) -> str | None:
    group_stack: list[int] = []
    escaped = False
    in_class = False

    for index, char in enumerate(pattern):
        if escaped:
            if char.isdigit():
                return "backreferences"
            escaped = False
            continue

        if char == "\\":
            escaped = True
            continue

        if in_class:
            if char == "]":
                in_class = False
            continue

        if char == "[":
            in_class = True
            continue

        if char == "." and _is_regex_quantifier(pattern, index + 1):
            return "wildcard repeaters"

        if char == "(":
            if pattern.startswith("(?=", index) or pattern.startswith("(?!", index):
                return "lookarounds"
            if pattern.startswith("(?<=", index) or pattern.startswith("(?<!", index):
                return "lookarounds"
            if pattern.startswith("(?P=", index):
                return "backreferences"
            group_stack.append(index)
            continue

        if char == ")" and group_stack:
            group_stack.pop()
            if _is_regex_quantifier(pattern, index + 1):
                return "quantified groups"

    return None


def validate_redaction_patterns(patterns: list[str]) -> list[str]:
    validated: list[str] = []
    for pattern in patterns:
        if not isinstance(pattern, str) or not pattern.strip():
            raise RedactionError(
                "redaction.patterns must contain only non-empty regex strings"
            )
        try:
            re.compile(pattern)
        except re.error as exc:
            raise RedactionError(f"invalid redaction pattern {pattern!r}: {exc}") from exc
        unsafe_construct = _find_unsafe_redaction_construct(pattern)
        if unsafe_construct is not None:
            raise RedactionError(
                "redaction.patterns forbids unsafe regex constructs "
                f"({unsafe_construct}); use a simpler token-shape regex"
            )
        validated.append(pattern)
    return validated


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
    custom_patterns = validate_redaction_patterns(cfg.sessions.redaction_patterns)

    for kind, pattern in BUILTIN_PATTERNS + [("custom", pattern) for pattern in custom_patterns]:
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


def _redact_obj_with_issues(obj: Any, config: UMXConfig) -> tuple[Any, list[RedactionIssue]]:
    if isinstance(obj, str):
        result = redact_text(obj, config)
        return result.text, list(result.issues)
    if isinstance(obj, list):
        items: list[Any] = []
        issues: list[RedactionIssue] = []
        for item in obj:
            redacted_item, item_issues = _redact_obj_with_issues(item, config)
            items.append(redacted_item)
            issues.extend(item_issues)
        return items, issues
    if isinstance(obj, dict):
        redacted: dict[str, Any] = {}
        issues: list[RedactionIssue] = []
        for key, value in obj.items():
            redacted_value, value_issues = _redact_obj_with_issues(value, config)
            redacted[key] = redacted_value
            issues.extend(value_issues)
        return redacted, issues
    return obj, []


def redact_json_record(record: dict[str, Any], config: UMXConfig | None = None) -> dict[str, Any]:
    cfg = config or default_config()
    try:
        return _redact_obj(record, cfg)
    except Exception as exc:  # pragma: no cover - defensive
        raise RedactionError(str(exc)) from exc


def redact_json_record_with_issues(
    record: dict[str, Any],
    config: UMXConfig | None = None,
) -> tuple[dict[str, Any], list[RedactionIssue]]:
    cfg = config or default_config()
    try:
        redacted, issues = _redact_obj_with_issues(record, cfg)
    except Exception as exc:  # pragma: no cover - defensive
        raise RedactionError(str(exc)) from exc
    if not isinstance(redacted, dict):  # pragma: no cover - structural guard
        raise RedactionError("redaction produced a non-dict JSON record")
    return redacted, issues


def redact_jsonl_lines(lines: list[dict[str, Any]], config: UMXConfig | None = None) -> list[dict[str, Any]]:
    cfg = config or default_config()
    return [redact_json_record(line, cfg) for line in lines]


def redact_jsonl_lines_with_issues(
    lines: list[dict[str, Any]],
    config: UMXConfig | None = None,
) -> tuple[list[dict[str, Any]], list[RedactionIssue]]:
    cfg = config or default_config()
    redacted: list[dict[str, Any]] = []
    issues: list[RedactionIssue] = []
    for line in lines:
        redacted_line, line_issues = redact_json_record_with_issues(line, cfg)
        redacted.append(redacted_line)
        issues.extend(line_issues)
    return redacted, issues


def redact_candidate_fact_text(text: str, config: UMXConfig | None = None) -> str:
    return redact_text(text, config).text
