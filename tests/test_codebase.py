from __future__ import annotations

import json
from pathlib import Path

import yaml
from click.testing import CliRunner

from umx.cli import main
from umx.codebase import (
    build_codemap,
    lookup_task_type_docs,
    onboarding_unit_path,
    write_codemap,
    write_onboarding_unit,
)


def test_build_codemap_extracts_python_structure(project_dir: Path) -> None:
    pkg_dir = project_dir / "pkg"
    pkg_dir.mkdir()
    (pkg_dir / "util.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    (pkg_dir / "app.py").write_text(
        "from pkg.util import helper\n\n"
        "def main():\n"
        "    return helper()\n",
        encoding="utf-8",
    )

    codemap = build_codemap(project_dir, project_name="project")

    app = codemap["modules"]["pkg/app.py"]
    util = codemap["modules"]["pkg/util.py"]
    assert codemap["schema_version"] == "0.6"
    assert codemap["project"] == "project"
    assert app["exports"] == ["main"]
    assert app["entry_points"] == ["pkg/app.py::main"]
    assert app["imports"] == ["pkg/util.py"]
    assert util["exports"] == ["helper"]


def test_write_codemap_creates_codebase_artifact(project_dir: Path, project_repo: Path) -> None:
    (project_dir / "worker.py").write_text("def run():\n    return True\n", encoding="utf-8")

    path = write_codemap(project_repo, project_dir, project_name="demo")
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert path == project_repo / "codebase" / "codemap.json"
    assert payload["project"] == "demo"
    assert "worker.py" in payload["modules"]


def test_write_onboarding_unit_includes_drift_hash_and_sections(project_dir: Path, project_repo: Path) -> None:
    auth_dir = project_dir / "src" / "auth"
    auth_dir.mkdir(parents=True)
    (auth_dir / "__init__.py").write_text("", encoding="utf-8")
    (auth_dir / "session.py").write_text("TOKEN_TTL = 3600\n", encoding="utf-8")

    path = write_onboarding_unit(
        project_repo,
        "src/auth",
        project_root=project_dir,
        purpose="Authentication flows and session management.",
        invariants=["Tokens are short-lived.", "Session revocation must remain explicit."],
        gotchas=["Do not bypass revocation checks."],
        related_refs=["facts/topics/auth.md", "procedures/auth-debug.md"],
    )

    text = path.read_text(encoding="utf-8")
    assert path == onboarding_unit_path(project_repo, "src/auth")
    assert "drift_hash:" in text
    assert "## Key invariants" in text
    assert "## Fragile areas" in text
    assert "- procedures/auth-debug.md" in text


def test_lookup_task_type_docs_supports_dotted_prefix(project_repo: Path) -> None:
    registry_path = project_repo / "codebase" / "docs" / "registry.yaml"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(
        yaml.safe_dump(
            {
                "schema_version": "0.6",
                "task_type_docs": {
                    "implementation": {
                        "owned_by": "docs/IMPLEMENTATION.md",
                        "procedures": ["procedures/impl-checklist.md"],
                    }
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    docs = lookup_task_type_docs(project_repo, "implementation.bugfix")

    assert docs is not None
    assert docs["owned_by"] == "docs/IMPLEMENTATION.md"
    assert docs["procedures"] == ["procedures/impl-checklist.md"]


def test_codemap_cli_writes_artifact(project_dir: Path, project_repo: Path) -> None:
    (project_dir / "tool.py").write_text("def cli():\n    return 0\n", encoding="utf-8")
    runner = CliRunner()

    result = runner.invoke(main, ["codemap", "--cwd", str(project_dir)])

    assert result.exit_code == 0, result.output
    written_path = Path(result.output.strip())
    assert written_path == project_repo / "codebase" / "codemap.json"
    assert written_path.exists()
