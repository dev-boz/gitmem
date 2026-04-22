from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from umx.cli import main


def test_docs_quickstart_local_mode_smoke(tmp_path: Path, monkeypatch) -> None:
    umx_home = tmp_path / "umxhome"
    monkeypatch.setenv("UMX_HOME", str(umx_home))

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / ".git").mkdir()

    runner = CliRunner()

    init = runner.invoke(main, ["init"])
    assert init.exit_code == 0, init.output

    init_project = runner.invoke(
        main,
        ["init-project", "--cwd", str(project_dir), "--slug", "docs-quickstart"],
    )
    assert init_project.exit_code == 0, init_project.output

    collect = runner.invoke(
        main,
        ["collect", "--cwd", str(project_dir), "--tool", "aider"],
        input="postgres runs on port 5433 in dev.\n",
    )
    assert collect.exit_code == 0, collect.output
    collect_payload = json.loads(collect.output)
    assert collect_payload["tool"] == "aider"
    assert collect_payload["events_imported"] == 1

    dream = runner.invoke(main, ["dream", "--cwd", str(project_dir), "--force"])
    assert dream.exit_code == 0, dream.output
    dream_payload = json.loads(dream.output)
    assert dream_payload["status"] == "ok"

    search = runner.invoke(main, ["search", "--cwd", str(project_dir), "postgres"])
    assert search.exit_code == 0, search.output
    assert "5433" in search.output

    inject = runner.invoke(main, ["inject", "--cwd", str(project_dir), "--prompt", "postgres"])
    assert inject.exit_code == 0, inject.output
    assert "# UMX Memory" in inject.output
    assert "5433" in inject.output

    status = runner.invoke(main, ["status", "--cwd", str(project_dir)])
    assert status.exit_code == 0, status.output
    status_payload = json.loads(status.output)
    assert status_payload["slug"] == "docs-quickstart"
    assert status_payload["session_count"] >= 1
    assert status_payload["fact_count"] >= 1
