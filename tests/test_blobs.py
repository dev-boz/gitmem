from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from umx.blobs import (
    BlobError,
    blobs_dir,
    find_unreferenced_blobs,
    get_blob,
    list_blobs,
    parse_blob_key,
    purge_unreferenced,
    referenced_blob_keys,
    stale_blob_summary,
    store_blob,
)
from umx.cli import main

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"fake-screenshot-bytes" * 4


def test_store_roundtrips_by_content_hash(project_repo: Path) -> None:
    ref = store_blob(project_repo, PNG_BYTES, filename="screenshot-auth-flow.png")
    assert len(ref.prefix) == 8
    assert ref.filename == "screenshot-auth-flow.png"
    assert ref.path(project_repo).exists()
    assert get_blob(project_repo, ref.key) == PNG_BYTES


def test_store_is_content_addressed_and_dedupes(project_repo: Path) -> None:
    first = store_blob(project_repo, PNG_BYTES, filename="a.png")
    second = store_blob(project_repo, PNG_BYTES, filename="a.png")
    assert first.key == second.key
    assert len(list_blobs(project_repo)) == 1

    different = store_blob(project_repo, b"other bytes", filename="a.png")
    assert different.prefix != first.prefix
    assert len(list_blobs(project_repo)) == 2


def test_store_file_uses_source_name(project_repo: Path, tmp_path: Path) -> None:
    src = tmp_path / "diff.png"
    src.write_bytes(PNG_BYTES)
    ref = store_blob(project_repo, src)
    assert ref.filename == "diff.png"
    assert get_blob(project_repo, ref.key) == PNG_BYTES


def test_get_missing_blob_raises(project_repo: Path) -> None:
    with pytest.raises(BlobError):
        get_blob(project_repo, "deadbeef/missing.png")


def test_parse_blob_key_rejects_bad_input(project_repo: Path) -> None:
    with pytest.raises(BlobError):
        parse_blob_key("no-slash")
    with pytest.raises(BlobError):
        parse_blob_key("XYZ/foo.png")
    with pytest.raises(BlobError):
        parse_blob_key("deadbeef/../escape")


def test_referenced_keys_detected_from_fact_text(project_repo: Path) -> None:
    ref = store_blob(project_repo, PNG_BYTES, filename="screenshot-auth-flow.png")
    topic = project_repo / "facts" / "topics" / "auth.md"
    topic.parent.mkdir(parents=True, exist_ok=True)
    topic.write_text(f"- Auth flow confirmed [blob:{ref.key}]\n")

    assert ref.key in referenced_blob_keys(project_repo)
    assert find_unreferenced_blobs(project_repo) == []


def test_purge_removes_only_unreferenced(project_repo: Path) -> None:
    kept = store_blob(project_repo, PNG_BYTES, filename="kept.png")
    orphan = store_blob(project_repo, b"orphan bytes here", filename="orphan.png")
    topic = project_repo / "facts" / "topics" / "auth.md"
    topic.parent.mkdir(parents=True, exist_ok=True)
    topic.write_text(f"- referenced [blob:{kept.key}]\n")

    dry = purge_unreferenced(project_repo, dry_run=True)
    assert [r.key for r in dry] == [orphan.key]
    assert orphan.path(project_repo).exists()

    purged = purge_unreferenced(project_repo)
    assert [r.key for r in purged] == [orphan.key]
    assert not orphan.path(project_repo).exists()
    assert kept.path(project_repo).exists()


def test_stale_blob_summary_counts(project_repo: Path) -> None:
    store_blob(project_repo, b"orphan", filename="orphan.png")
    summary = stale_blob_summary(project_repo)
    assert summary["total"] == 1
    assert summary["unreferenced_count"] == 1


def test_cli_blob_store_get_list_purge(project_repo: Path, project_dir: Path, tmp_path: Path) -> None:
    runner = CliRunner()
    src = tmp_path / "shot.png"
    src.write_bytes(PNG_BYTES)

    stored = runner.invoke(main, ["blob", "store", "--cwd", str(project_dir), str(src)])
    assert stored.exit_code == 0, stored.output
    key = stored.output.strip()
    assert key.endswith("/shot.png")

    out = tmp_path / "fetched.png"
    got = runner.invoke(main, ["blob", "get", "--cwd", str(project_dir), "--output", str(out), key])
    assert got.exit_code == 0, got.output
    assert out.read_bytes() == PNG_BYTES

    listed = runner.invoke(main, ["blob", "list", "--cwd", str(project_dir)])
    assert listed.exit_code == 0
    assert key in listed.output

    purged = runner.invoke(main, ["blob", "purge", "--cwd", str(project_dir)])
    assert purged.exit_code == 0
    assert key in purged.output
    assert not list_blobs(project_repo)
