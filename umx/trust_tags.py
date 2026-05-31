from __future__ import annotations

SELF_DERIVED_QUARANTINE_TAG = "quarantine:self-derived"
UNTRUSTED_SOURCE_TAG = "untrusted_source"
CONTAMINATION_RISK_TAG = "contamination_risk"

REVIEW_RISK_TAGS = frozenset({UNTRUSTED_SOURCE_TAG, CONTAMINATION_RISK_TAG})

__all__ = [
    "CONTAMINATION_RISK_TAG",
    "REVIEW_RISK_TAGS",
    "SELF_DERIVED_QUARANTINE_TAG",
    "UNTRUSTED_SOURCE_TAG",
]
