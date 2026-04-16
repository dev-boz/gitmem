from __future__ import annotations

from typing import Any

from umx.metrics import HEALTH_METRIC_LABELS


def _warn_direction(payload: dict[str, Any]) -> str | None:
    value = float(payload.get("value", 0.0))
    healthy_min = payload.get("healthy_min")
    healthy_max = payload.get("healthy_max")
    if healthy_min is not None and value < healthy_min:
        return "low"
    if healthy_max is not None and value > healthy_max:
        return "high"
    return None


def _metric_advice(metric: str, payload: dict[str, Any]) -> tuple[str, list[str]]:
    value = float(payload.get("value", 0.0))
    healthy_min = payload.get("healthy_min")
    healthy_max = payload.get("healthy_max")
    if metric == "injection_precision":
        return (
            "Low precision means injected facts are consuming prompt budget without changing the assistant output.",
            [
                "Review injected-but-uncited facts and prune ones that never influence answers.",
                "Tighten retrieval prompts or topic tags so only directly relevant facts are injected.",
                "Prefer code-grounded or recently cited facts in hot paths before broad summaries.",
            ],
        )
    if metric == "fact_churn_rate":
        return (
            "High churn means stored facts are being superseded faster than memory can stabilise around the current repo state.",
            [
                "Re-run capture or Dream soon after major refactors so memory catches up to the new code shape.",
                "Keep volatile implementation details weaker until they survive at least one stable cycle.",
                "Ground fast-changing topics to code or docs before strengthening them.",
            ],
        )
    if metric == "contradiction_rate":
        return (
            "High contradiction load means active memory contains competing claims, which makes retrieval noisy and lowers trust.",
            [
                "Inspect conflicting topics and supersede or merge losing facts instead of leaving both active.",
                "Verify disputed facts against code, docs, or a human-confirmed source before raising strength.",
                "Trim extraction noise in areas where multiple sessions keep restating the same topic differently.",
            ],
        )
    if metric == "entrenchment_index":
        return (
            "High entrenchment means strong facts are sticking without code grounding or human confirmation.",
            [
                "Audit S:4+ facts that are still inference-backed and either verify or downgrade them.",
                "Promote grounded replacements instead of repeatedly reinforcing unverified summaries.",
                "Bias strengthening toward code-grounded and human-confirmed facts for high-impact topics.",
            ],
        )
    if metric == "hot_tier_utilisation":
        if healthy_max is not None and value > healthy_max:
            return (
                "High hot-tier utilisation means too much memory is competing for prompt budget, so relevant facts are easier to crowd out.",
                [
                    "Trim duplicate or low-signal entries from MEMORY.md, conventions, and principle summaries.",
                    "Demote stale implementation detail so only durable, high-yield facts stay hot.",
                    "Keep the hot tier focused on facts that are cited often or gate important decisions.",
                ],
            )
        if healthy_min is not None and value < healthy_min:
            return (
                "Low hot-tier utilisation means high-value memory is not filling enough of the active context budget.",
                [
                    "Promote the repo's most reused conventions, principles, and durable facts into hot-tier memory.",
                    "Check whether important facts are stranded in cold storage or overly narrow topics.",
                    "Use recent cited facts as the seed set for what deserves hot-tier space.",
                ],
            )
    if metric == "staleness_ratio":
        return (
            "High staleness means too many active facts have gone unused or unreviewed for over a month.",
            [
                "Review old topics and prune facts that no longer affect active work.",
                "Reconfirm still-relevant facts against code or recent sessions instead of letting them age in place.",
                "Use stale clusters as a shortlist for Dream cleanup or manual curation.",
            ],
        )
    return ("", [])


def build_calibration_advice(
    metrics: dict[str, dict[str, Any]],
    flags: list[str] | None = None,
) -> list[dict[str, Any]]:
    active_flags = flags or []
    advice: list[dict[str, Any]] = []
    for metric, payload in metrics.items():
        if payload.get("status") != "warn":
            continue
        direction = _warn_direction(payload)
        if direction is None:
            continue
        why_it_matters, recommended_actions = _metric_advice(metric, payload)
        label = HEALTH_METRIC_LABELS.get(metric, metric)
        flag_prefix = f"{label} out of range:"
        advice.append(
            {
                "metric": metric,
                "label": label,
                "severity": payload.get("status", "warn"),
                "status": payload.get("status", "warn"),
                "direction": direction,
                "value": payload.get("value"),
                "healthy_min": payload.get("healthy_min"),
                "healthy_max": payload.get("healthy_max"),
                "signal": payload.get("signal", ""),
                "flag": next((flag for flag in active_flags if flag.startswith(flag_prefix)), None),
                "why": payload.get("signal", ""),
                "why_it_matters": why_it_matters or payload.get("signal", ""),
                "recommended_actions": recommended_actions,
            }
        )
    return advice
