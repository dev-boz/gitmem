from __future__ import annotations

import gzip
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

from click.testing import CliRunner

from umx.backup import BACKUP_MANIFEST_NAME, BACKUP_SNAPSHOT_DIRNAME, export_full, import_full
from umx.cli import main
from umx.memory import add_fact, topic_path
from umx.models import ConsolidationStatus, Fact, MemoryType, Scope, SourceType, Verification
from umx.scope import project_memory_dir
from umx.sessions import archive_path, session_index_path, session_path, write_session


def _export_manifest_path(backup_dir: Path) -> Path:
    return backup_dir / BACKUP_MANIFEST_NAME


def _backup_snapshot_root(backup_dir: Path) -> Path:
    return backup_dir / BACKUP_SNAPSHOT_DIRNAME


def _make_fact(
    fact_id: str,
    text: str,
    *,
    supersedes: str | None = None,
    superseded_by: str | None = None,
    created: datetime | None = None,
) -> Fact:
    return Fact(
        fact_id=fact_id,
        text=text,
        scope=Scope.PROJECT,
        topic="backup",
        encoding_strength=4,
        memory_type=MemoryType.EXPLICIT_SEMANTIC,
        verification=Verification.CORROBORATED,
        source_type=SourceType.TOOL_OUTPUT,
        source_tool="copilot",
        source_session="2026-04-20-backup",
        supersedes=supersedes,
        superseded_by=superseded_by,
        consolidation_status=ConsolidationStatus.STABLE,
        created=created or datetime(2026, 4, 20, 12, 0, tzinfo=UTC),
    )


def _seed_backup_source(repo: Path) -> list[str]:
    add_fact(
        repo,
        _make_fact(
            "01TESTFACT0000000000001001",
            "deploys used to run only on Fridays",
            superseded_by="01TESTFACT0000000000001002",
            created=datetime(2026, 4, 19, 12, 0, tzinfo=UTC),
        ),
        auto_commit=False,
    )
    add_fact(
        repo,
        _make_fact(
            "01TESTFACT0000000000001002",
            "deploys now run after staging verification every weekday",
            supersedes="01TESTFACT0000000000001001",
            created=datetime(2026, 4, 20, 12, 0, tzinfo=UTC),
        ),
        auto_commit=False,
    )

    active_session_id = "2026-04-20-backup-active"
    active_session = session_path(repo, active_session_id)
    write_session(
        repo,
        {
            "session_id": active_session_id,
            "tool": "copilot",
            "started": "2026-04-20T12:30:00Z",
        },
        [{"role": "assistant", "content": "active backup session"}],
        auto_commit=False,
    )

    archive_file = archive_path(repo, "2026", "01")
    archive_file.parent.mkdir(parents=True, exist_ok=True)
    archived_session_id = "2026-01-05-backup-archived"
    with gzip.open(archive_file, "wt", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "session_id": archived_session_id,
                    "payload": [
                        {"_meta": {"session_id": archived_session_id, "tool": "claude-code"}},
                        {"role": "assistant", "content": "archived backup session"},
                    ],
                },
                sort_keys=True,
            )
            + "\n"
        )
    archive_index = session_index_path(repo, "2026", "01")
    archive_index.write_text(
        json.dumps(
            [
                {
                    "archive": "sessions/2026/01/2026-01-archive.jsonl.gz",
                    "session_id": archived_session_id,
                    "tool": "claude-code",
                }
            ],
            sort_keys=True,
        )
        + "\n"
    )

    (repo / ".umx.json").write_text(
        json.dumps(
            {
                "dream": {"last_lint": "2026-04-20T12:00:00Z"},
                "sessions": {"last_archive_compaction": "2026-04-20T12:00:00Z"},
            },
            sort_keys=True,
        )
        + "\n"
    )
    (repo / "meta" / "index.sqlite").write_bytes(b"index-sqlite")
    (repo / "meta" / "index.sqlite-wal").write_bytes(b"index-sqlite-wal")
    (repo / "meta" / "index.sqlite-shm").write_bytes(b"index-sqlite-shm")
    (repo / "meta" / "usage.sqlite").write_bytes(b"usage-sqlite")
    (repo / "meta" / "usage.sqlite-wal").write_bytes(b"usage-sqlite-wal")
    (repo / "meta" / "usage.sqlite-shm").write_bytes(b"usage-sqlite-shm")
    (repo / "meta" / "dream-state.json").write_text(
        json.dumps({"last_dream": "2026-04-20T12:00:00Z", "session_count": 2}, sort_keys=True)
        + "\n"
    )
    (repo / BACKUP_MANIFEST_NAME).write_text("real repo file named like the sibling manifest\n")

    fact_file = topic_path(repo, "backup")
    return [
        fact_file.relative_to(repo).as_posix(),
        fact_file.with_suffix(".umx.json").relative_to(repo).as_posix(),
        active_session.relative_to(repo).as_posix(),
        archive_file.relative_to(repo).as_posix(),
        archive_index.relative_to(repo).as_posix(),
        ".umx.json",
        "meta/index.sqlite",
        "meta/index.sqlite-wal",
        "meta/index.sqlite-shm",
        "meta/usage.sqlite",
        "meta/usage.sqlite-wal",
        "meta/usage.sqlite-shm",
        "meta/dream-state.json",
        BACKUP_MANIFEST_NAME,
    ]


def test_export_full_and_import_full_preserve_repo_bytes(project_repo: Path, umx_home: Path, tmp_path: Path) -> None:
    expected = _seed_backup_source(project_repo)
    backup_dir = tmp_path / "backup-export"

    exported = export_full(project_repo, backup_dir)
    assert BACKUP_MANIFEST_NAME in exported.files_copied
    assert "meta/index.sqlite-wal" in exported.files_copied

    restored_repo = umx_home / "projects" / "restored-backup"
    imported = import_full(backup_dir, restored_repo)

    assert imported.forced is False
    for relative in expected:
        assert (restored_repo / relative).read_bytes() == (project_repo / relative).read_bytes()


def test_cli_export_full_returns_json_and_writes_manifest(project_dir: Path, project_repo: Path, tmp_path: Path) -> None:
    expected = _seed_backup_source(project_repo)
    backup_dir = tmp_path / "cli-export"

    runner = CliRunner()
    result = runner.invoke(main, ["export", "--cwd", str(project_dir), "--out", str(backup_dir)])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["out_dir"] == str(backup_dir.resolve())
    assert set(expected).issubset(set(payload["files_copied"]))
    manifest_path = _export_manifest_path(backup_dir)
    manifest = json.loads(manifest_path.read_text())
    assert manifest["format_version"] == 1
    assert "meta" in manifest["entries"]
    assert (_backup_snapshot_root(backup_dir) / "facts" / "topics" / "backup.md").exists()


def test_cli_import_full_dry_run_and_force_restore(
    project_dir: Path,
    project_repo: Path,
    tmp_path: Path,
) -> None:
    _seed_backup_source(project_repo)
    backup_dir = tmp_path / "round-trip-export"
    export_full(project_repo, backup_dir)

    target_dir = tmp_path / "restore-target"
    target_dir.mkdir()
    (target_dir / ".git").mkdir()
    target_repo = project_memory_dir(target_dir)
    private_note = target_repo / "local" / "private" / "note.md"
    private_note.parent.mkdir(parents=True, exist_ok=True)
    private_note.write_text("keep this private memory safe\n")

    runner = CliRunner()
    dry_run = runner.invoke(
        main,
        ["import", "--cwd", str(target_dir), "--full", str(backup_dir), "--dry-run"],
    )
    assert dry_run.exit_code == 0, dry_run.output
    dry_run_payload = json.loads(dry_run.output)
    assert dry_run_payload["dry_run"] is True
    assert dry_run_payload["force_required"] is True

    refused = runner.invoke(main, ["import", "--cwd", str(target_dir), "--full", str(backup_dir)])
    assert refused.exit_code != 0
    assert "target repo already contains backup data" in refused.output

    restored = runner.invoke(
        main,
        ["import", "--cwd", str(target_dir), "--full", str(backup_dir), "--force"],
    )
    assert restored.exit_code == 0, restored.output
    payload = json.loads(restored.output)
    assert payload["forced"] is True

    fact_file = topic_path(project_repo, "backup")
    restored_fact_file = target_repo / fact_file.relative_to(project_repo)
    assert restored_fact_file.read_bytes() == fact_file.read_bytes()


def test_import_full_preflights_manifest_files_before_force_clear(
    project_repo: Path,
    umx_home: Path,
    tmp_path: Path,
) -> None:
    _seed_backup_source(project_repo)
    backup_dir = tmp_path / "truncated-export"
    export_full(project_repo, backup_dir)
    (_backup_snapshot_root(backup_dir) / "facts" / "topics" / "backup.md").unlink()

    target_repo = umx_home / "projects" / "preflight-target"
    sentinel = target_repo / "local" / "private" / "note.md"
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    sentinel.write_text("sentinel survives failed restore\n")

    try:
        import_full(backup_dir, target_repo, force=True)
    except RuntimeError as exc:
        assert "backup snapshot is incomplete" in str(exc)
    else:
        raise AssertionError("expected import_full to fail for truncated backup")

    assert sentinel.read_text() == "sentinel survives failed restore\n"


def test_cli_import_full_dry_run_fails_for_truncated_backup(project_dir: Path, project_repo: Path, tmp_path: Path) -> None:
    _seed_backup_source(project_repo)
    backup_dir = tmp_path / "dry-run-truncated"
    export_full(project_repo, backup_dir)
    (_backup_snapshot_root(backup_dir) / "facts" / "topics" / "backup.md").unlink()

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["import", "--cwd", str(project_dir), "--full", str(backup_dir), "--dry-run"],
    )

    assert result.exit_code != 0
    assert "backup snapshot is incomplete" in result.output


def test_import_full_rejects_manifest_path_escape(project_repo: Path, umx_home: Path, tmp_path: Path) -> None:
    _seed_backup_source(project_repo)
    backup_dir = tmp_path / "invalid-manifest"
    export_full(project_repo, backup_dir)
    manifest_path = _export_manifest_path(backup_dir)
    manifest = json.loads(manifest_path.read_text())
    manifest["files"] = ["../escape.txt"]
    manifest_path.write_text(json.dumps(manifest, sort_keys=True) + "\n")

    try:
        import_full(backup_dir, umx_home / "projects" / "escape-target", force=True)
    except RuntimeError as exc:
        assert "invalid backup path" in str(exc)
    else:
        raise AssertionError("expected import_full to reject manifest path escape")


def test_import_full_rejects_symlinked_snapshot_parent(project_repo: Path, umx_home: Path, tmp_path: Path) -> None:
    _seed_backup_source(project_repo)
    backup_dir = tmp_path / "symlink-parent"
    export_full(project_repo, backup_dir)
    snapshot_root = _backup_snapshot_root(backup_dir)
    outside = tmp_path / "outside"
    (outside / "topics").mkdir(parents=True)
    (outside / "topics" / "backup.md").write_text("escape through symlinked parent\n")
    shutil.rmtree(snapshot_root / "facts")
    (snapshot_root / "facts").symlink_to(outside, target_is_directory=True)

    try:
        import_full(backup_dir, umx_home / "projects" / "symlink-parent-target", force=True)
    except RuntimeError as exc:
        assert "backup snapshot is incomplete" in str(exc)
    else:
        raise AssertionError("expected import_full to reject symlinked snapshot parent")


def test_import_full_rejects_symlinked_snapshot_root(project_repo: Path, umx_home: Path, tmp_path: Path) -> None:
    _seed_backup_source(project_repo)
    backup_dir = tmp_path / "symlink-root"
    export_full(project_repo, backup_dir)
    snapshot_root = _backup_snapshot_root(backup_dir)
    relocated = tmp_path / "relocated-snapshot"
    snapshot_root.rename(relocated)
    snapshot_root.symlink_to(relocated, target_is_directory=True)
    target_repo = umx_home / "projects" / "symlink-root-target"
    sentinel = target_repo / "local" / "private" / "existing.md"
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    sentinel.write_text("target survives symlink-root rejection\n")

    try:
        import_full(backup_dir, target_repo, force=True)
    except RuntimeError as exc:
        assert "backup snapshot directory must not be a symlink" in str(exc)
    else:
        raise AssertionError("expected import_full to reject symlinked snapshot root")

    assert sentinel.read_text() == "target survives symlink-root rejection\n"


def test_import_full_rejects_backup_source_inside_target_repo(
    project_repo: Path,
    umx_home: Path,
) -> None:
    _seed_backup_source(project_repo)
    target_repo = umx_home / "projects" / "overlap-target"
    sentinel = target_repo / "local" / "private" / "existing.md"
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    sentinel.write_text("existing target data\n")
    backup_dir = target_repo / "restore-input"
    export_full(project_repo, backup_dir)

    try:
        import_full(backup_dir, target_repo, force=True)
    except RuntimeError as exc:
        assert "must not overlap the target repo" in str(exc)
    else:
        raise AssertionError("expected import_full to reject overlapping backup source")

    assert sentinel.read_text() == "existing target data\n"
    assert _export_manifest_path(backup_dir).exists()
    assert (_backup_snapshot_root(backup_dir) / "facts" / "topics" / "backup.md").exists()


def test_cli_import_requires_exactly_one_source(project_dir: Path) -> None:
    runner = CliRunner()

    result = runner.invoke(main, ["import", "--cwd", str(project_dir)])

    assert result.exit_code != 0
    assert "Provide exactly one of --adapter or --full." in result.output
