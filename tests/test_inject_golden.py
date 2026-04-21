from __future__ import annotations

import json
from pathlib import Path

import pytest

from umx.config import default_config, save_config
from umx.inject import build_injection_block
from umx.memory import add_fact
from umx.models import (
    ConsolidationStatus,
    Fact,
    MemoryType,
    Scope,
    SourceType,
    Verification,
)
from umx.scope import config_path, encode_scope_path
from umx.search import session_replay

FIXTURES_ROOT = Path(__file__).parent / "eval" / "inject" / "cases.json"
GOLDEN_CASES = json.loads(FIXTURES_ROOT.read_text(encoding="utf-8"))


def _build_fact(payload: dict[str, object]) -> Fact:
    scope = Scope(str(payload.get("scope", "project")))
    topic = str(payload.get("topic", "general"))
    if scope == Scope.FILE:
        topic = encode_scope_path(topic)
    return Fact(
        fact_id=str(payload["fact_id"]),
        text=str(payload["text"]),
        scope=scope,
        topic=topic,
        encoding_strength=int(payload.get("encoding_strength", 4)),
        memory_type=MemoryType.EXPLICIT_SEMANTIC,
        verification=Verification.CORROBORATED,
        source_type=SourceType.GROUND_TRUTH_CODE,
        source_tool="codex",
        source_session="inject-golden",
        consolidation_status=ConsolidationStatus.STABLE,
    )


def _run_case(
    case: dict[str, object],
    *,
    disclosure_slack_pct: float,
    project_repo: Path,
    project_dir: Path,
) -> list[str]:
    cfg = default_config()
    cfg.inject.disclosure_slack_pct = disclosure_slack_pct
    save_config(config_path(), cfg)
    session_id = f"inject-golden-{case['id']}-{str(disclosure_slack_pct).replace('.', '')}"
    build_injection_block(
        project_dir,
        prompt=str(case["prompt"]),
        file_paths=list(case.get("file_paths", [])),
        max_tokens=int(case["max_tokens"]),
        session_id=session_id,
    )
    return [
        str(row["fact_id"])
        for row in session_replay(project_repo, session_id)
        if row["event_kind"] == "inject" and row.get("item_kind") == "fact"
    ]


@pytest.mark.parametrize("case", GOLDEN_CASES, ids=[case["id"] for case in GOLDEN_CASES])
def test_injection_top_n_matches_golden_corpus_under_old_and_new_slack(
    case: dict[str, object],
    project_repo: Path,
    project_dir: Path,
) -> None:
    for payload in case["facts"]:
        add_fact(project_repo, _build_fact(payload))

    new_ids = _run_case(case, disclosure_slack_pct=0.20, project_repo=project_repo, project_dir=project_dir)
    old_ids = _run_case(case, disclosure_slack_pct=0.30, project_repo=project_repo, project_dir=project_dir)

    assert new_ids == list(case["expected_top_ids"])
    assert old_ids == list(case["expected_top_ids"])
