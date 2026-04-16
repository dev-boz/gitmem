from __future__ import annotations

import json

from click.testing import CliRunner

from umx.cli import main
from umx.conventions import ConventionSet
from umx.doctor import run_doctor
from umx.dream.lint import generate_lint_findings
from umx.scope import find_orphaned_scoped_memory


def test_find_orphaned_scoped_memory_reports_missing_paths(project_dir, project_repo) -> None:
    (project_dir / "src").mkdir()
    (project_dir / "src" / "live.py").write_text("print('ok')\n")
    (project_dir / "docs").mkdir()
    (project_dir / "docs" / "api spec.md").write_text("# spec\n")
    (project_dir / "root").write_text("root file\n")

    (project_repo / "files" / "src---live.py.md").write_text("# live\n")
    (project_repo / "files" / "src---missing.py.md").write_text("# missing\n")
    (project_repo / "files" / "docs---api%20spec.md.md").write_text("# spec\n")
    (project_repo / "files" / "root.md").write_text("# root file\n")
    (project_repo / "folders" / "docs.md").write_text("# docs\n")
    (project_repo / "folders" / "__root__.md").write_text("# project root\n")
    (project_repo / "folders" / "legacy.md").write_text("# legacy\n")

    orphans = find_orphaned_scoped_memory(project_repo, project_dir)

    assert [(orphan.scope_kind, orphan.memory_path, orphan.scoped_path) for orphan in orphans] == [
        ("file", "files/src---missing.py.md", "src/missing.py"),
        ("folder", "folders/legacy.md", "legacy"),
    ]


def test_generate_lint_findings_reports_orphaned_scopes(project_dir, project_repo) -> None:
    (project_repo / "files" / "src---missing.py.md").write_text("# missing\n")
    (project_repo / "folders" / "legacy.md").write_text("# legacy\n")

    findings = generate_lint_findings(
        [],
        conventions=ConventionSet(),
        repo_dir=project_repo,
        project_root=project_dir,
    )

    assert findings == [
        {
            "kind": "orphaned-scope",
            "message": "files/src---missing.py.md targets missing file path src/missing.py",
        },
        {
            "kind": "orphaned-scope",
            "message": "folders/legacy.md targets missing folder path legacy",
        },
    ]


def test_run_doctor_surfaces_orphaned_scopes(project_dir, project_repo) -> None:
    (project_repo / "files" / "src---missing.py.md").write_text("# missing\n")

    payload = run_doctor(project_dir)

    assert payload["orphaned_scoped_memory_count"] == 1
    assert payload["orphaned_scoped_memory"] == [
        {
            "scope": "file",
            "memory_path": "files/src---missing.py.md",
            "scope_path": "src/missing.py",
        }
    ]


def test_cli_doctor_accepts_cwd_and_surfaces_orphaned_scopes(project_dir, project_repo) -> None:
    (project_repo / "files" / "src---missing.py.md").write_text("# missing\n")

    runner = CliRunner()
    result = runner.invoke(main, ["doctor", "--cwd", str(project_dir)])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["orphaned_scoped_memory_count"] == 1
    assert payload["orphaned_scoped_memory"] == [
        {
            "scope": "file",
            "memory_path": "files/src---missing.py.md",
            "scope_path": "src/missing.py",
        }
    ]
