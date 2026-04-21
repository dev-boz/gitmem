from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

import umx.memory as memory
from umx.cli import main
from umx.config import default_config, save_config
from umx.doctor import run_doctor
from umx.memory import FACT_FILE_SCHEMA_VERSION, add_fact, format_fact_line, read_fact_file_schema_version, topic_path
from umx.migrations import available_migrations, run_migrations
from umx.models import ConsolidationStatus, Fact, MemoryType, Scope, SourceType, Verification
from umx.scope import config_path


def make_fact(fact_id: str, text: str, *, topic: str = "devenv") -> Fact:
    return Fact(
        fact_id=fact_id,
        text=text,
        scope=Scope.PROJECT,
        topic=topic,
        encoding_strength=3,
        memory_type=MemoryType.EXPLICIT_SEMANTIC,
        verification=Verification.SELF_REPORTED,
        source_type=SourceType.TOOL_OUTPUT,
        source_tool="codex",
        source_session="2026-04-18-test",
        consolidation_status=ConsolidationStatus.FRAGILE,
    )


def write_legacy_fact_file(path: Path, body: str, *, schema_version: int | None = None) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    schema_line = f"schema_version: {schema_version}\n" if schema_version is not None else ""
    text = f"# {path.stem}\n{schema_line}\n## Facts\n{body}\n"
    path.write_text(text)
    return text


def test_run_migrations_repairs_synthetic_v0_fact_files(project_repo: Path) -> None:
    path = topic_path(project_repo, "devenv")
    body = "- legacy fact without file schema header"
    original = write_legacy_fact_file(path, body)
    memory.read_fact_file(path, repo_dir=project_repo, normalize=False)

    result = run_migrations(project_repo)

    assert available_migrations() == ["0001_initial"]
    assert result.from_version == 0
    assert result.to_version == FACT_FILE_SCHEMA_VERSION
    assert result.applied_migrations == ["0001_initial"]
    assert result.changed_files == ["facts/topics/devenv.md"]
    assert read_fact_file_schema_version(path) == (FACT_FILE_SCHEMA_VERSION, str(FACT_FILE_SCHEMA_VERSION))
    assert path.read_text().split("## Facts\n", 1)[1] == original.split("## Facts\n", 1)[1]
    assert (path.resolve(), True) not in memory._FACT_FILE_CACHE
    assert (path.resolve(), False) not in memory._FACT_FILE_CACHE


def test_run_migrations_is_idempotent_for_current_fact_files(project_repo: Path) -> None:
    path = topic_path(project_repo, "devenv")
    write_legacy_fact_file(path, "- legacy fact")
    run_migrations(project_repo)
    migrated = path.read_text()

    result = run_migrations(project_repo)

    assert result.from_version == FACT_FILE_SCHEMA_VERSION
    assert result.to_version == FACT_FILE_SCHEMA_VERSION
    assert result.applied_migrations == []
    assert result.applied == []
    assert result.changed_files == []
    assert path.read_text() == migrated


def test_run_migrations_rolls_back_partial_header_writes(project_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first_path = topic_path(project_repo, "alpha")
    second_path = project_repo / "tools" / "zeta.md"
    first_original = write_legacy_fact_file(first_path, "- alpha body")
    second_original = write_legacy_fact_file(second_path, "- zeta body")

    original_write_text = Path.write_text

    def flaky_write_text(self: Path, data: str, *args, **kwargs):
        if self == second_path and "schema_version:" in data and "schema_version:" not in second_original:
            raise OSError("boom")
        return original_write_text(self, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", flaky_write_text)

    with pytest.raises(OSError, match="boom"):
        run_migrations(project_repo)

    assert first_path.read_text() == first_original
    assert second_path.read_text() == second_original
    assert read_fact_file_schema_version(first_path) == (None, None)
    assert read_fact_file_schema_version(second_path) == (None, None)


def test_new_fact_writes_include_file_schema_header(project_repo: Path) -> None:
    fact = make_fact("01TESTFACT0000000000000901", "postgres runs on 5433 in dev")

    add_fact(project_repo, fact, auto_commit=False)

    path = topic_path(project_repo, "devenv")
    assert f"schema_version: {FACT_FILE_SCHEMA_VERSION}" in path.read_text()


def test_doctor_warns_when_fact_file_schema_headers_are_missing_or_stale(
    project_dir: Path,
    project_repo: Path,
) -> None:
    missing_path = topic_path(project_repo, "devenv")
    stale_path = project_repo / "tools" / "build.md"
    write_legacy_fact_file(missing_path, "- missing header fact")
    write_legacy_fact_file(stale_path, "- stale header fact", schema_version=0)

    payload = run_doctor(project_dir, fix=True)

    assert payload["fact_file_schema"]["status"] == "warn"
    assert payload["fact_file_schema"]["state"] == "needs-migration"
    assert payload["fact_file_schema"]["from_version"] == 0
    assert payload["fact_file_schema"]["missing"] == [
        {"path": "facts/topics/devenv.md", "found": None, "raw": None}
    ]
    assert payload["fact_file_schema"]["stale"] == [
        {"path": "tools/build.md", "found": 0, "raw": "0"}
    ]
    assert "facts/topics/devenv.md" not in payload["fixes_applied"]
    assert read_fact_file_schema_version(missing_path) == (None, None)


def test_cli_migrate_updates_fact_file_headers_and_returns_json(project_dir: Path, project_repo: Path) -> None:
    path = topic_path(project_repo, "devenv")
    write_legacy_fact_file(path, format_fact_line(make_fact("01TESTFACT0000000000000902", "cli migration fact")))

    runner = CliRunner()
    result = runner.invoke(main, ["migrate", "--cwd", str(project_dir)])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload == {
        "applied": [f"set facts/topics/devenv.md schema_version header to {FACT_FILE_SCHEMA_VERSION}"],
        "applied_migrations": ["0001_initial"],
        "changed_files": ["facts/topics/devenv.md"],
        "from_version": 0,
        "to_version": FACT_FILE_SCHEMA_VERSION,
    }
    assert read_fact_file_schema_version(path) == (FACT_FILE_SCHEMA_VERSION, str(FACT_FILE_SCHEMA_VERSION))


def test_cli_migrate_respects_governance(project_dir: Path, project_repo: Path) -> None:
    cfg = default_config()
    cfg.dream.mode = "remote"
    save_config(config_path(), cfg)
    write_legacy_fact_file(topic_path(project_repo, "devenv"), "- governed migration fact")

    runner = CliRunner()
    result = runner.invoke(main, ["migrate", "--cwd", str(project_dir)])

    assert result.exit_code != 0
    assert "writes governed fact state directly" in result.output
