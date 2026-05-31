"""Tests for umx.routing — validate_route_card_l2 and promote_route_card."""
from __future__ import annotations

import pytest

from umx.routing import RouteCard, iter_route_card_files, promote_route_card, validate_route_card_l2


# ---------------------------------------------------------------------------
# validate_route_card_l2
# ---------------------------------------------------------------------------


def test_validate_route_card_l2_no_evidence_is_critical():
    """A card with no evidence string should yield CRITICAL and return False."""
    card = RouteCard(
        route_card_id="rc-001",
        title="Test Card",
        evidence="",
        summary="A sufficiently long summary for this test card.",
        confidence=0.7,
    )
    is_valid, issues = validate_route_card_l2(card)
    assert is_valid is False
    critical_issues = [i for i in issues if i.startswith("CRITICAL")]
    assert len(critical_issues) >= 1


def test_validate_route_card_l2_single_source_is_warning():
    """A card with one distinct evidence bullet is a single-source warning."""
    card = RouteCard(
        route_card_id="rc-002",
        title="Single Source Card",
        evidence="- only one source mentioned here\n- only one source mentioned here",
        summary="A sufficiently long summary for this test card.",
        confidence=0.7,
    )
    is_valid, issues = validate_route_card_l2(card)
    assert is_valid is True
    warning_issues = [i for i in issues if i.startswith("WARNING")]
    single_source_warnings = [i for i in warning_issues if "single evidence source" in i]
    assert len(single_source_warnings) == 1


def test_validate_route_card_l2_good_card_is_valid():
    """A card with multi-char evidence and a long summary should pass without CRITICAL issues."""
    # evidence="abc" → unique chars = {'a','b','c'} → len 3 → no single-source warning
    card = RouteCard(
        route_card_id="rc-003",
        title="Good Card",
        evidence="abcdefghij",
        summary="A sufficiently long summary for this test card.",
        confidence=0.7,
    )
    is_valid, issues = validate_route_card_l2(card)
    assert is_valid is True
    critical_issues = [i for i in issues if i.startswith("CRITICAL")]
    assert len(critical_issues) == 0


def test_validate_route_card_l2_high_confidence_no_evidence_double_critical():
    """confidence >= 0.9 with no evidence triggers two CRITICAL issues."""
    card = RouteCard(
        route_card_id="rc-004",
        title="Overconfident Card",
        evidence="",
        summary="A sufficiently long summary for this test card.",
        confidence=0.95,
    )
    is_valid, issues = validate_route_card_l2(card)
    assert is_valid is False
    critical_issues = [i for i in issues if i.startswith("CRITICAL")]
    assert len(critical_issues) == 2


def test_validate_route_card_l2_unrecognized_lifecycle_is_warning():
    """An unrecognized lifecycle value should produce a WARNING."""
    card = RouteCard(
        route_card_id="rc-005",
        title="Weird Lifecycle Card",
        evidence="abcde",
        summary="A sufficiently long summary for this test card.",
        confidence=0.7,
        lifecycle="pending",
    )
    is_valid, issues = validate_route_card_l2(card)
    lifecycle_warnings = [i for i in issues if "unrecognized lifecycle" in i]
    assert len(lifecycle_warnings) == 1


def test_validate_route_card_l2_short_summary_is_warning():
    """A summary shorter than 20 chars should produce a WARNING."""
    card = RouteCard(
        route_card_id="rc-006",
        title="Short Summary Card",
        evidence="abcde",
        summary="Too short",
        confidence=0.7,
    )
    is_valid, issues = validate_route_card_l2(card)
    summary_warnings = [i for i in issues if "summary" in i.lower()]
    assert len(summary_warnings) == 1


# ---------------------------------------------------------------------------
# promote_route_card
# ---------------------------------------------------------------------------


def test_promote_route_card_raises_on_critical(tmp_path):
    """A card with no evidence should raise ValueError on promote."""
    card = RouteCard(
        route_card_id="rc-raise",
        title="Raise Card",
        evidence="",
        summary="A sufficiently long summary for this test card.",
        confidence=0.5,
    )
    with pytest.raises(ValueError, match="failed L2 validation"):
        promote_route_card(card, tmp_path)


def test_promote_route_card_force_writes_despite_critical(tmp_path):
    """force=True writes the file even with CRITICAL issues."""
    card = RouteCard(
        route_card_id="rc-force",
        title="Force Write Card",
        evidence="",
        summary="A sufficiently long summary for this test card.",
        confidence=0.5,
    )
    path, issues = promote_route_card(card, tmp_path, force=True)
    assert path.exists()
    critical_issues = [i for i in issues if i.startswith("CRITICAL")]
    assert len(critical_issues) >= 1


def test_promote_route_card_writes_valid_card(tmp_path):
    """A valid card should be written to routing/ namespace without raising."""
    card = RouteCard(
        route_card_id="rc-valid",
        title="Valid Promotion Card",
        evidence="abcdefghij",
        summary="A sufficiently long summary for this test card.",
        confidence=0.7,
    )
    path, issues = promote_route_card(card, tmp_path)
    assert path.exists()
    assert (tmp_path / "routing").is_dir()
    critical_issues = [i for i in issues if i.startswith("CRITICAL")]
    assert len(critical_issues) == 0


def test_promote_route_card_updates_routing_index(tmp_path):
    card = RouteCard(
        route_card_id="rc-index",
        title="Index Card",
        node_id="planner",
        task_class="implementation",
        capability_band="standard",
        evidence="abcdefghij",
        summary="A sufficiently long summary for this route card index test.",
        confidence=0.8,
    )

    path, _ = promote_route_card(card, tmp_path)

    index_path = tmp_path / "routing" / "ROUTING.md"
    assert index_path.exists()
    assert iter_route_card_files(tmp_path) == [path]
    index_text = index_path.read_text()
    assert "[rc-index](./rc-index.md)" in index_text
    assert "| implementation | planner |" in index_text
