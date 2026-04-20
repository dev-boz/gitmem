from __future__ import annotations

from pathlib import Path

from umx.config import default_config, save_config
from umx.dream.conflict import resolve_conflict
from umx.fact_actions import merge_conflicts_action
from umx.memory import add_fact, read_fact_file, topic_path
from umx.merge import arbitrate_conflict
from umx.models import (
    ConsolidationStatus,
    Fact,
    MemoryType,
    Scope,
    SourceType,
    Verification,
)
from umx.scope import config_path
from umx.supersession import walk_history


def make_fact(
    fact_id: str,
    text: str,
    *,
    source_type: SourceType = SourceType.LLM_INFERENCE,
    encoding_strength: int = 3,
    verification: Verification = Verification.SELF_REPORTED,
    topic: str = "devenv",
    source_tool: str = "codex",
    source_session: str = "2026-04-17-session",
    **overrides,
) -> Fact:
    values = {
        "fact_id": fact_id,
        "text": text,
        "scope": Scope.PROJECT,
        "topic": topic,
        "encoding_strength": encoding_strength,
        "memory_type": MemoryType.EXPLICIT_SEMANTIC,
        "verification": verification,
        "source_type": source_type,
        "source_tool": source_tool,
        "source_session": source_session,
        "consolidation_status": ConsolidationStatus.FRAGILE,
    }
    values.update(overrides)
    return Fact(**values)


def test_resolve_conflict_left_operand_wins_tied_trust_scores() -> None:
    """Tied trust scores currently resolve in favor of the left operand."""

    left = make_fact("FACT_TIE_LEFT", "postgres runs on 5433 in dev")
    right = make_fact("FACT_TIE_RIGHT", "postgres runs on 5432 in dev")

    winner, loser = resolve_conflict(left, right, config=default_config())

    assert winner.fact_id == left.fact_id
    assert loser.fact_id == right.fact_id
    assert loser.superseded_by == winner.fact_id


def test_ground_truth_code_wins_even_when_passed_on_right() -> None:
    """The hard rule applies regardless of argument order when only one side is GT."""

    inferred = make_fact(
        "FACT_LLM",
        "postgres runs on 5432 in dev",
        source_type=SourceType.LLM_INFERENCE,
        encoding_strength=5,
        verification=Verification.HUMAN_CONFIRMED,
    )
    grounded = make_fact(
        "FACT_GT",
        "postgres runs on 5433 in dev",
        source_type=SourceType.GROUND_TRUTH_CODE,
        encoding_strength=1,
    )

    winner, loser, reason = arbitrate_conflict(inferred, grounded, default_config())

    assert winner.fact_id == grounded.fact_id
    assert loser.fact_id == inferred.fact_id
    assert "ground_truth_code beats non-ground-truth" == reason


def test_ground_truth_vs_ground_truth_falls_back_to_trust_score() -> None:
    """When both sides are GT, arbitration currently falls back to trust_score."""

    weaker = make_fact(
        "FACT_GT_WEAK",
        "postgres runs on 5432 in dev",
        source_type=SourceType.GROUND_TRUTH_CODE,
        encoding_strength=2,
        verification=Verification.SELF_REPORTED,
    )
    stronger = make_fact(
        "FACT_GT_STRONG",
        "postgres runs on 5433 in dev",
        source_type=SourceType.GROUND_TRUTH_CODE,
        encoding_strength=5,
        verification=Verification.HUMAN_CONFIRMED,
    )

    winner, loser, reason = arbitrate_conflict(weaker, stronger, default_config())

    assert winner.fact_id == stronger.fact_id
    assert loser.fact_id == weaker.fact_id
    assert reason.startswith("trust_score ")


def test_walk_history_returns_three_deep_manual_supersession_chain(project_repo: Path) -> None:
    """Manual markdown edits continue to produce a linear chain that history can walk."""

    original = make_fact("FACT_CHAIN_A", "postgres runs on 5432 in dev")
    add_fact(project_repo, original, auto_commit=False)
    path = topic_path(project_repo, "devenv")

    first_edit = path.read_text().replace("5432", "5433")
    path.write_text(first_edit)
    mid_facts = read_fact_file(path, repo_dir=project_repo)
    middle = next(fact for fact in mid_facts if fact.text.endswith("5433 in dev"))

    second_edit = path.read_text().replace("5433", "5434")
    path.write_text(second_edit)
    tip_facts = read_fact_file(path, repo_dir=project_repo)
    tip = next(fact for fact in tip_facts if fact.text.endswith("5434 in dev"))

    from_tip = walk_history(project_repo, tip.fact_id)
    from_middle = walk_history(project_repo, middle.fact_id)

    assert [fact.text for fact in from_tip] == [
        "postgres runs on 5432 in dev",
        "postgres runs on 5433 in dev",
        "postgres runs on 5434 in dev",
    ]
    assert [fact.text for fact in from_middle] == [fact.text for fact in from_tip]


def test_merge_conflicts_action_blocks_apply_but_allows_dry_run_in_governed_mode(
    project_dir: Path,
    project_repo: Path,
) -> None:
    """Governed mode still allows inspection via dry-run while blocking direct mutation."""

    add_fact(
        project_repo,
        make_fact("FACT_GOV_A", "postgres runs on 5433 in dev"),
        auto_commit=False,
    )
    add_fact(
        project_repo,
        make_fact("FACT_GOV_B", "postgres runs on 5432 in dev"),
        auto_commit=False,
    )
    cfg = default_config()
    cfg.dream.mode = "remote"
    save_config(config_path(), cfg)

    blocked = merge_conflicts_action(project_dir, dry_run=False)
    allowed = merge_conflicts_action(project_dir, dry_run=True)

    assert blocked.ok is False
    assert "writes governed fact state directly" in blocked.message
    assert allowed.ok is True
    assert len(allowed.results) == 1
