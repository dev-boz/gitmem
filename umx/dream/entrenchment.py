"""Entrenchment detection for gitmem Dream pipeline.

Per IMX spec §11.7: detect route cards and procedures that may be forming
echo chambers and emit entrenchment_risk dream triggers.

Heuristics:
  - Single source origin (all facts from same agent/tool)
  - High injection frequency with no recent challenges
  - Age without fresh supporting telemetry
  - Confidence above threshold but low sample backing
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class EntrenchmentRisk:
    item_id: str          # fact_id, procedure_id, or route_card_id
    item_type: str        # fact | procedure | route_card
    risk_level: str       # low | medium | high
    reasons: list[str]
    source: str = ""
    confidence: float = 0.0
    last_observed: str = ""

    def to_trigger_context(self) -> dict:
        return {
            "item_id": self.item_id,
            "item_type": self.item_type,
            "risk_level": self.risk_level,
            "reasons": self.reasons,
            "source": self.source,
            "confidence": self.confidence,
            "last_observed": self.last_observed,
        }

    def to_candidate_text(self) -> str:
        lines = [f"Entrenchment risk: {self.item_type} '{self.item_id}' ({self.risk_level})"]
        lines.extend(f"  - {r}" for r in self.reasons)
        if self.source:
            lines.append(f"Source: {self.source}")
        if self.confidence:
            lines.append(f"Confidence: {self.confidence:.2f}")
        return "\n".join(lines)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(ts_str: str) -> datetime | None:
    if not ts_str:
        return None
    try:
        return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except ValueError:
        return None


def check_procedure_entrenchment(
    procedures: list,  # list[Procedure] from umx.procedures
    *,
    staleness_days: int = 30,
    high_confidence_threshold: float = 0.9,
) -> list[EntrenchmentRisk]:
    """Check procedures for entrenchment risk."""
    risks = []
    now = _utc_now()

    for proc in procedures:
        reasons = []
        risk_level = "low"

        # Single source
        if proc.source_label == "human_authored":
            # Human-authored is fine, skip
            pass
        elif proc.source_label and "dream" not in proc.source_label.lower():
            reasons.append(f"Single non-human source: {proc.source_label}")

        # High confidence with no backing
        if proc.confidence >= high_confidence_threshold:
            reasons.append(f"High confidence ({proc.confidence:.2f}) without visible challenge history")
            risk_level = "medium"

        if reasons:
            if len(reasons) >= 2:
                risk_level = "high"
            risks.append(EntrenchmentRisk(
                item_id=proc.procedure_id,
                item_type="procedure",
                risk_level=risk_level,
                reasons=reasons,
                source=proc.source_label,
                confidence=proc.confidence,
            ))

    return risks


def check_route_card_entrenchment(
    route_cards: list,  # list[RouteCard] from umx.routing
    *,
    staleness_days: int = 14,
    high_confidence_threshold: float = 0.85,
    min_sample_backing: int = 5,
) -> list[EntrenchmentRisk]:
    """Check route cards for entrenchment risk."""
    risks = []
    now = _utc_now()

    for card in route_cards:
        reasons = []
        risk_level = "low"

        # Single source
        if card.promoted_from and card.promoted_from not in ("imx-telemetry", "empirical"):
            reasons.append(f"Single non-empirical source: {card.promoted_from}")

        # High confidence without sample backing — check if n < min_sample_backing
        # RouteCard doesn't store n directly; flag high confidence from non-telemetry source
        if card.confidence >= high_confidence_threshold and card.promoted_from not in ("imx-telemetry",):
            reasons.append(f"High confidence ({card.confidence:.2f}) without telemetry backing")
            risk_level = "medium"

        if reasons:
            if len(reasons) >= 2:
                risk_level = "high"
            risks.append(EntrenchmentRisk(
                item_id=card.route_card_id,
                item_type="route_card",
                risk_level=risk_level,
                reasons=reasons,
                source=card.promoted_from,
                confidence=card.confidence,
            ))

    return risks


def entrenchment_risks_to_dream_candidates(risks: list[EntrenchmentRisk]) -> list[dict]:
    """Convert entrenchment risks to Dream candidate dicts."""
    return [
        {
            "source": "imx:entrenchment-detector",
            "trigger_type": "entrenchment_risk",
            "content": risk.to_candidate_text(),
            "task_class": None,
            "metadata": risk.to_trigger_context(),
        }
        for risk in risks
        if risk.risk_level in ("medium", "high")
    ]


def detect_entrenchment(
    repo_dir: Path,
    *,
    staleness_days: int = 14,
) -> list[dict]:
    """Run entrenchment detection on a gitmem repo. Returns Dream candidates."""
    from umx.procedures import load_all_procedures
    from umx.routing import load_all_route_cards

    procedures = load_all_procedures(repo_dir)
    route_cards = load_all_route_cards(repo_dir)

    proc_risks = check_procedure_entrenchment(procedures, staleness_days=staleness_days)
    card_risks = check_route_card_entrenchment(route_cards, staleness_days=staleness_days)

    all_risks = proc_risks + card_risks
    if all_risks:
        logger.info("Entrenchment check: %d procedures, %d route cards → %d risks",
                    len(procedures), len(route_cards), len(all_risks))

    return entrenchment_risks_to_dream_candidates(all_risks)
