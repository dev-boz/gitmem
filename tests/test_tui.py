from __future__ import annotations

import asyncio
from unittest.mock import patch

from click.testing import CliRunner

from umx.cli import main
from umx.memory import add_fact
from umx.models import (
    ConsolidationStatus,
    Fact,
    MemoryType,
    Scope,
    SourceType,
    Verification,
)
from umx.sessions import write_session
from umx.tui import GitmemTUIApp
from umx.tui.data import build_tui_snapshot, current_search_results


def _make_fact(
    fact_id: str,
    text: str,
    *,
    scope: Scope,
    topic: str,
    strength: int,
    source_tool: str,
    source_session: str,
    source_type: SourceType = SourceType.GROUND_TRUTH_CODE,
) -> Fact:
    return Fact(
        fact_id=fact_id,
        text=text,
        scope=scope,
        topic=topic,
        encoding_strength=strength,
        memory_type=MemoryType.EXPLICIT_SEMANTIC,
        verification=Verification.CORROBORATED,
        source_type=source_type,
        source_tool=source_tool,
        source_session=source_session,
        consolidation_status=ConsolidationStatus.STABLE,
    )


def _seed_tui_data(project_repo, user_repo) -> None:
    add_fact(
        project_repo,
        _make_fact(
            "FACT_TUI_001",
            "postgres runs on 5433 in dev",
            scope=Scope.PROJECT,
            topic="devenv",
            strength=4,
            source_tool="codex",
            source_session="sess-tui-001",
        ),
        auto_commit=False,
    )
    add_fact(
        project_repo,
        _make_fact(
            "FACT_TUI_002",
            "postgres runs on 5432 in dev",
            scope=Scope.PROJECT,
            topic="devenv",
            strength=3,
            source_tool="claude-code",
            source_session="sess-tui-002",
            source_type=SourceType.LLM_INFERENCE,
        ),
        auto_commit=False,
    )
    add_fact(
        user_repo,
        _make_fact(
            "FACT_TUI_003",
            "prefer concise release notes",
            scope=Scope.USER,
            topic="writing",
            strength=5,
            source_tool="human",
            source_session="sess-tui-user",
            source_type=SourceType.USER_PROMPT,
        ),
        auto_commit=False,
    )
    write_session(
        project_repo,
        {
            "session_id": "sess-tui-001",
            "tool": "codex",
            "started": "2026-05-01T00:00:00Z",
        },
        [
            {"role": "user", "content": "postgres deploy flow"},
            {"role": "assistant", "content": "postgres runs on 5433 in dev"},
        ],
        auto_commit=False,
    )


def test_build_tui_snapshot_surfaces_conflicts_sessions_and_user_facts(
    project_dir,
    project_repo,
    user_repo,
) -> None:
    _seed_tui_data(project_repo, user_repo)

    snapshot = build_tui_snapshot(project_dir)

    assert snapshot.hero.state == "bad"
    assert len(snapshot.facts) == 3
    assert len(snapshot.sessions) == 1
    assert len(snapshot.conflicts) == 1
    assert snapshot.activity_totals["codex"] == 1


def test_tui_search_results_include_fact_and_session_hits(
    project_dir,
    project_repo,
    user_repo,
) -> None:
    _seed_tui_data(project_repo, user_repo)

    snapshot = build_tui_snapshot(project_dir)
    results = current_search_results(snapshot, "postgres")

    assert any(item.kind == "fact" for item in results)
    assert any(item.kind == "session" for item in results)


def test_cli_tui_launches_textual_app(project_dir, project_repo) -> None:
    runner = CliRunner()

    with patch("umx.tui.GitmemTUIApp.run") as mock_run:
        result = runner.invoke(main, ["tui", "--cwd", str(project_dir)])

    assert result.exit_code == 0, result.output
    mock_run.assert_called_once_with()


def test_tui_refresh_snapshot_preserves_stale_data_on_io_errors(
    project_dir,
    project_repo,
    user_repo,
) -> None:
    _seed_tui_data(project_repo, user_repo)

    async def scenario() -> None:
        app = GitmemTUIApp(project_dir)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            snapshot = app.snapshot
            assert snapshot is not None

            with patch("umx.tui.app.build_tui_snapshot", side_effect=OSError("transient read failure")):
                app.refresh_snapshot(force=True)

            assert app.snapshot is snapshot
            assert app.state.notice == "refresh failed: transient read failure"

    asyncio.run(scenario())


def test_tui_navigation_and_fact_filtering(project_dir, project_repo, user_repo) -> None:
    _seed_tui_data(project_repo, user_repo)

    async def scenario() -> None:
        app = GitmemTUIApp(project_dir)
        async with app.run_test(size=(120, 40)) as pilot:
            assert app.state.current_page == "overview"

            app.action_page_facts()
            await pilot.pause()
            assert app.state.current_page == "facts"

            await pilot.press("down")
            await pilot.pause()
            assert app.state.fact_index == 1

            app.action_focus_filter()
            await pilot.pause()
            for char in "release":
                await pilot.press(char)
            await pilot.press("enter")
            await pilot.pause()

            body = app.render_body_plain(width=120)
            assert "prefer concise release notes" in body
            assert "postgres runs on 5433 in dev" not in body

    asyncio.run(scenario())


def test_tui_q_binding_exits_app(project_dir, project_repo, user_repo) -> None:
    _seed_tui_data(project_repo, user_repo)

    async def scenario() -> None:
        app = GitmemTUIApp(project_dir)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("q")
            await pilot.pause()
            assert app.is_running is False
            assert getattr(app, "_exit", False) is True

    asyncio.run(scenario())
