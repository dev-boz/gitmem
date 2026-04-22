from __future__ import annotations

import json

from click.testing import CliRunner

from umx.cli import main
from umx.conventions import ConventionSet
from umx.doctor import run_doctor
from umx.dream.lint import generate_lint_findings
from umx.git_ops import git_blob_sha
from umx.models import CodeAnchor, ConsolidationStatus, Fact, MemoryType, Scope, SourceType, Verification
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


def test_generate_lint_findings_reports_drifted_code_anchor(project_dir, project_repo) -> None:
    src_dir = project_dir / "src"
    src_dir.mkdir()
    source_path = src_dir / "app.py"
    source_path.write_text("DATABASE_PORT = 5432\n")

    fact = Fact(
        fact_id="01TESTSCOPELINT000000001",
        text="database port is 5432",
        scope=Scope.PROJECT,
        topic="devenv",
        encoding_strength=4,
        memory_type=MemoryType.EXPLICIT_SEMANTIC,
        verification=Verification.CORROBORATED,
        source_type=SourceType.GROUND_TRUTH_CODE,
        source_tool="manual",
        source_session="2026-04-22-scope-lint",
        consolidation_status=ConsolidationStatus.STABLE,
        code_anchor=CodeAnchor(repo=project_repo.name, path="src/app.py", git_sha=git_blob_sha(source_path)),
    )
    source_path.write_text("DATABASE_PORT = 5433\n")

    findings = generate_lint_findings(
        [fact],
        conventions=ConventionSet(),
        repo_dir=project_repo,
        project_root=project_dir,
    )

    assert {"kind": "stale-reference", "message": f"{fact.fact_id} points to stale path src/app.py"} in findings


def test_generate_lint_findings_reports_missing_anchor_for_non_ground_truth_fact(project_dir, project_repo) -> None:
    fact = Fact(
        fact_id="01TESTSCOPELINT000000002",
        text="release notes are tracked in docs/releases.md",
        scope=Scope.PROJECT,
        topic="docs",
        encoding_strength=3,
        memory_type=MemoryType.EXPLICIT_SEMANTIC,
        verification=Verification.SELF_REPORTED,
        source_type=SourceType.TOOL_OUTPUT,
        source_tool="codex",
        source_session="2026-04-22-scope-lint",
        consolidation_status=ConsolidationStatus.FRAGILE,
        code_anchor=CodeAnchor(repo=project_repo.name, path="docs/releases.md"),
    )

    findings = generate_lint_findings(
        [fact],
        conventions=ConventionSet(),
        repo_dir=project_repo,
        project_root=project_dir,
    )

    assert {"kind": "stale-reference", "message": f"{fact.fact_id} points to missing path docs/releases.md"} in findings


def test_generate_lint_findings_reports_directory_anchor_as_missing(project_dir, project_repo) -> None:
    (project_dir / "docs").mkdir()

    fact = Fact(
        fact_id="01TESTSCOPELINT000000003",
        text="deployment docs live under docs/",
        scope=Scope.PROJECT,
        topic="docs",
        encoding_strength=4,
        memory_type=MemoryType.EXPLICIT_SEMANTIC,
        verification=Verification.CORROBORATED,
        source_type=SourceType.GROUND_TRUTH_CODE,
        source_tool="manual",
        source_session="2026-04-22-scope-lint",
        consolidation_status=ConsolidationStatus.STABLE,
        code_anchor=CodeAnchor(repo=project_repo.name, path="docs", git_sha="deadbeef"),
    )

    findings = generate_lint_findings(
        [fact],
        conventions=ConventionSet(),
        repo_dir=project_repo,
        project_root=project_dir,
    )

    assert {"kind": "stale-reference", "message": f"{fact.fact_id} points to missing path docs"} in findings


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
