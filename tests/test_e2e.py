from __future__ import annotations

from click.testing import CliRunner

from umx.cli import main
from umx.dream.pipeline import DreamPipeline
from umx.hooks.session_end import run as session_end_run
from umx.memory import load_all_facts


def test_end_to_end_archived_session_still_drives_search_dream_and_bridge(
    project_dir,
    project_repo,
) -> None:
    result = session_end_run(
        cwd=project_dir,
        session_id="2020-01-15-e2e001",
        tool="codex",
        events=[
            {
                "ts": "2020-01-15T00:00:00Z",
                "role": "assistant",
                "content": "postgres runs on port 5433 in dev.",
            }
        ],
    )
    assert result["session_written"] is True
    assert result["archived_sessions"] >= 1

    runner = CliRunner()
    raw_search = runner.invoke(main, ["search", "--cwd", str(project_dir), "--raw", "postgres"])
    assert raw_search.exit_code == 0, raw_search.output
    assert "2020-01-15-e2e001" in raw_search.output

    dream = DreamPipeline(project_dir).run(force=True)
    assert dream.status == "ok"
    assert any("5433" in fact.text for fact in load_all_facts(project_repo, include_superseded=False))

    bridge_sync = runner.invoke(
        main,
        ["bridge", "sync", "--cwd", str(project_dir), "--target", "CLAUDE.md"],
    )
    assert bridge_sync.exit_code == 0, bridge_sync.output
    assert "5433" in (project_dir / "CLAUDE.md").read_text()
