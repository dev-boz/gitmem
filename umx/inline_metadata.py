from __future__ import annotations

import json
from typing import Any, Mapping

INLINE_METADATA_PREFIX = "<!-- umx:"
INLINE_METADATA_SUFFIX = "-->"
INLINE_METADATA_FIELD_ORDER = (
    "id",
    "conf",
    "cort",
    "corf",
    "pr",
    "src",
    "xby",
    "aby",
    "ss",
    "st",
    "cr",
    "v",
    "cs",
)


def order_inline_metadata(data: Mapping[str, Any]) -> dict[str, Any]:
    ordered: dict[str, Any] = {}
    for key in INLINE_METADATA_FIELD_ORDER:
        if key in data:
            ordered[key] = data[key]
    for key in sorted(key for key in data if key not in INLINE_METADATA_FIELD_ORDER):
        ordered[key] = data[key]
    return ordered


def serialize_inline_metadata(data: Mapping[str, Any]) -> str:
    payload = json.dumps(
        order_inline_metadata(data),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return payload.replace("-->", "\\u002d\\u002d\\u003e")


def split_inline_metadata(line: str) -> tuple[str, str | None]:
    start = line.find(INLINE_METADATA_PREFIX)
    if start == -1:
        return line, None
    end = line.rfind(INLINE_METADATA_SUFFIX)
    if end == -1 or end < start:
        return line, None
    prefix = line[:start].rstrip()
    suffix = line[end + len(INLINE_METADATA_SUFFIX) :].lstrip()
    text = f"{prefix} {suffix}".strip() if suffix else prefix
    payload = line[start + len(INLINE_METADATA_PREFIX) : end].strip()
    return text, payload


def parse_inline_metadata(line: str) -> tuple[str, dict[str, Any]]:
    text, payload = split_inline_metadata(line)
    if payload is None:
        return text, {}
    return text, json.loads(payload)


def strip_inline_metadata(text: str) -> str:
    return "\n".join(split_inline_metadata(line)[0] for line in text.splitlines()).strip()
