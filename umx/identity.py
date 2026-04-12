from __future__ import annotations

import hashlib
import os
import time


_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _encode_crockford(value: int, length: int) -> str:
    chars = []
    for _ in range(length):
        chars.append(_ALPHABET[value & 0x1F])
        value >>= 5
    return "".join(reversed(chars))


def generate_fact_id() -> str:
    """Generate a lexicographically sortable ULID-like identifier."""
    millis = int(time.time() * 1000)
    timestamp = _encode_crockford(millis, 10)
    randomness = int.from_bytes(os.urandom(10), "big")
    suffix = _encode_crockford(randomness, 16)
    return timestamp + suffix


def semantic_dedup_key(text: str, scope: str, topic: str) -> str:
    payload = f"{text.strip().lower()}\x00{scope}\x00{topic}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
