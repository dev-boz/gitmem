from __future__ import annotations

from umx.calibration import build_calibration_advice
from umx.metrics import health_flags


def test_build_calibration_advice_emits_actions_for_warn_metrics() -> None:
    metrics = {
        "hot_tier_utilisation": {
            "value": 0.95,
            "healthy_min": 0.5,
            "healthy_max": 0.9,
            "signal": "Low means memory is underused; high means the hot tier likely needs trimming.",
            "status": "warn",
        }
    }
    advice = build_calibration_advice(metrics, health_flags(metrics))

    assert len(advice) == 1
    assert advice[0]["metric"] == "hot_tier_utilisation"
    assert advice[0]["label"] == "Hot tier utilisation"
    assert advice[0]["severity"] == "warn"
    assert advice[0]["direction"] == "high"
    assert "needs trimming" in advice[0]["why"]
    assert "prompt budget" in advice[0]["why_it_matters"]
    assert "Hot tier utilisation out of range" in advice[0]["flag"]
    assert any("Trim duplicate or low-signal entries" in action for action in advice[0]["recommended_actions"])


def test_build_calibration_advice_stays_metric_specific() -> None:
    metrics = {
        "injection_precision": {
            "value": 0.1,
            "healthy_min": 0.3,
            "healthy_max": None,
            "signal": "Below 0.3 means retrieval relevance or context budget is poorly calibrated.",
            "status": "warn",
        },
        "contradiction_rate": {
            "value": 0.2,
            "healthy_min": None,
            "healthy_max": 0.05,
            "signal": "High contradiction load suggests extraction noise or genuine repo inconsistency.",
            "status": "warn",
        },
        "entrenchment_index": {
            "value": 0.7,
            "healthy_min": None,
            "healthy_max": 0.2,
            "signal": "High entrenchment indicates strong facts are accumulating without grounded verification.",
            "status": "warn",
        },
        "staleness_ratio": {
            "value": 0.8,
            "healthy_min": None,
            "healthy_max": 0.4,
            "signal": "High staleness means old facts are not being revisited or pruned aggressively enough.",
            "status": "warn",
        },
    }

    advice = {item["metric"]: item for item in build_calibration_advice(metrics, health_flags(metrics))}

    assert "injected-but-uncited facts" in " ".join(advice["injection_precision"]["recommended_actions"]).lower()
    assert "competing claims" in advice["contradiction_rate"]["why_it_matters"]
    assert "inference-backed" in " ".join(advice["entrenchment_index"]["recommended_actions"])
    assert "over a month" in advice["staleness_ratio"]["why_it_matters"]
