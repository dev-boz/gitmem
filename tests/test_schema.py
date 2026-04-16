from __future__ import annotations

import json

from click.testing import CliRunner

from umx.cli import main
from umx.doctor import run_doctor
from umx.schema import CURRENT_SCHEMA_VERSION, detect_schema_state, repair_schema


def test_detect_schema_state_reports_missing_schema_file(project_repo) -> None:
    schema_path = project_repo / "meta" / "schema_version"
    schema_path.unlink()

    state = detect_schema_state(project_repo)

    assert state.found is None
    assert state.expected == CURRENT_SCHEMA_VERSION
    assert state.state == "missing"
    assert state.status == "warn"
    assert state.fixable is True
    assert state.pending == [
        f"write meta/schema_version={CURRENT_SCHEMA_VERSION}",
        f"update meta/MEMORY.md schema_version to {CURRENT_SCHEMA_VERSION}",
        "rebuild local search indexes",
    ]


def test_repair_schema_updates_repo_metadata_and_index(project_repo) -> None:
    (project_repo / "meta" / "schema_version").write_text("1\n")
    (project_repo / "meta" / "MEMORY.md").write_text("# umx memory index\nschema_version: 1\n")

    repair = repair_schema(project_repo)

    assert repair.from_version == 1
    assert repair.to_version == CURRENT_SCHEMA_VERSION
    assert repair.rebuilt_index is True
    assert (project_repo / "meta" / "schema_version").read_text() == f"{CURRENT_SCHEMA_VERSION}\n"
    assert "schema_version: 2" in (project_repo / "meta" / "MEMORY.md").read_text()
    assert (project_repo / "meta" / "index.sqlite").exists()


def test_detect_schema_state_reports_future_unsupported_schema(project_repo) -> None:
    (project_repo / "meta" / "schema_version").write_text(f"{CURRENT_SCHEMA_VERSION + 1}\n")

    state = detect_schema_state(project_repo)

    assert state.found == CURRENT_SCHEMA_VERSION + 1
    assert state.state == "future-unsupported"
    assert state.status == "error"
    assert state.fixable is False


def test_doctor_fix_repairs_stale_schema(project_dir, project_repo) -> None:
    (project_repo / "meta" / "schema_version").write_text("1\n")

    payload = run_doctor(project_dir, fix=True)

    assert payload["schema"]["state"] == "current"
    assert payload["schema"]["status"] == "ok"
    assert payload["schema"]["found"] == CURRENT_SCHEMA_VERSION
    assert payload["fixes_applied"] == [
        f"set meta/schema_version to {CURRENT_SCHEMA_VERSION}",
        "rebuilt local search indexes",
    ]


def test_doctor_fix_repairs_missing_schema_and_reports_file_write(project_dir, project_repo) -> None:
    (project_repo / "meta" / "schema_version").unlink()

    payload = run_doctor(project_dir, fix=True)

    assert payload["schema"]["state"] == "current"
    assert payload["fixes_applied"] == [
        f"set meta/schema_version to {CURRENT_SCHEMA_VERSION}",
        "rebuilt local search indexes",
    ]


def test_dream_blocks_when_schema_requires_migration(project_dir, project_repo) -> None:
    (project_repo / "meta" / "schema_version").write_text("1\n")

    runner = CliRunner()
    result = runner.invoke(main, ["dream", "--cwd", str(project_dir), "--force"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "error"
    assert "schema 1 is older than supported schema 2" in payload["message"]
    assert "umx doctor --cwd" in payload["message"]


def test_dream_blocks_when_schema_file_is_missing(project_dir, project_repo) -> None:
    (project_repo / "meta" / "schema_version").unlink()

    runner = CliRunner()
    result = runner.invoke(main, ["dream", "--cwd", str(project_dir), "--force"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "error"
    assert "schema_version is missing" in payload["message"]
    assert "umx doctor --cwd" in payload["message"]
    assert not (project_repo / "meta" / "index.sqlite").exists()


def test_cli_doctor_fix_repairs_stale_schema(project_dir, project_repo) -> None:
    (project_repo / "meta" / "schema_version").write_text("1\n")

    runner = CliRunner()
    result = runner.invoke(main, ["doctor", "--cwd", str(project_dir), "--fix"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["schema"]["state"] == "current"
    assert payload["fixes_applied"] == [
        f"set meta/schema_version to {CURRENT_SCHEMA_VERSION}",
        "rebuilt local search indexes",
    ]
