from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from umx.cli import main
from umx.identity import generate_fact_id
from umx.memory import add_fact
from umx.models import (
    ConsolidationStatus,
    Fact,
    MemoryType,
    Scope,
    SourceType,
    Verification,
)
from umx.scope import ensure_repo_structure


def _add_sample_fact(repo: Path, text: str = "test uses port 5432", topic: str = "devenv") -> Fact:
    fact = Fact(
        fact_id=generate_fact_id(),
        text=text,
        scope=Scope.PROJECT,
        topic=topic,
        encoding_strength=4,
        memory_type=MemoryType.EXPLICIT_SEMANTIC,
        verification=Verification.CORROBORATED,
        source_type=SourceType.GROUND_TRUTH_CODE,
        source_tool="test",
        source_session="test-session",
        consolidation_status=ConsolidationStatus.STABLE,
    )
    add_fact(repo, fact, auto_commit=False)
    return fact


def test_aider_shim_generates_injection_block(project_dir, project_repo, user_repo) -> None:
    from umx.shim.aider import generate_aider_prompt

    _add_sample_fact(project_repo)
    result = generate_aider_prompt(project_dir)
    assert "# UMX Memory" in result
    assert "test uses port 5432" in result


def test_aider_shim_writes_memory_file(project_dir, project_repo, user_repo, tmp_path) -> None:
    from umx.shim.aider import write_aider_memory_file

    _add_sample_fact(project_repo)
    output = tmp_path / "aider-ctx.md"
    path = write_aider_memory_file(project_dir, output_path=output)
    assert path == output
    assert path.exists()
    content = path.read_text()
    assert "# UMX Memory" in content
    assert "test uses port 5432" in content


def test_aider_shim_default_output_path(project_dir, project_repo, user_repo) -> None:
    from umx.shim.aider import write_aider_memory_file

    _add_sample_fact(project_repo)
    path = write_aider_memory_file(project_dir)
    assert path == project_dir / ".umx-aider-context.md"
    assert path.exists()


def test_aider_shim_run(project_dir, project_repo, user_repo) -> None:
    from umx.shim.aider import run

    _add_sample_fact(project_repo)
    result = run(cwd=project_dir)
    assert isinstance(result, str)
    assert "# UMX Memory" in result


def test_generic_shim_generates_injection_block(project_dir, project_repo, user_repo) -> None:
    from umx.shim.generic import generate_prompt

    _add_sample_fact(project_repo, text="redis on 6379")
    result = generate_prompt(project_dir)
    assert "# UMX Memory" in result
    assert "redis on 6379" in result


def test_generic_shim_with_tool_name(project_dir, project_repo, user_repo) -> None:
    from umx.shim.generic import generate_prompt

    _add_sample_fact(project_repo)
    result = generate_prompt(project_dir, tool="vibe")
    assert "# UMX Memory" in result


def test_generic_shim_writes_context_file(project_dir, project_repo, user_repo, tmp_path) -> None:
    from umx.shim.generic import write_context_file

    _add_sample_fact(project_repo, text="api runs on 8080")
    output = tmp_path / "context.md"
    path = write_context_file(project_dir, output_path=output)
    assert path == output
    assert path.exists()
    content = path.read_text()
    assert "# UMX Memory" in content
    assert "api runs on 8080" in content


def test_generic_shim_run(project_dir, project_repo, user_repo) -> None:
    from umx.shim.generic import run

    _add_sample_fact(project_repo)
    result = run(cwd=project_dir)
    assert isinstance(result, str)
    assert "# UMX Memory" in result


def test_shim_init_exports() -> None:
    from umx.shim import aider_shim, generic_shim

    assert callable(aider_shim)
    assert callable(generic_shim)


def test_cli_shim_aider(project_dir, project_repo, user_repo) -> None:
    _add_sample_fact(project_repo, text="db password is rotated weekly")
    runner = CliRunner()
    result = runner.invoke(main, ["shim", "aider", "--cwd", str(project_dir)])
    assert result.exit_code == 0
    assert "# UMX Memory" in result.output
    assert "db password is rotated weekly" in result.output


def test_cli_shim_aider_with_output(project_dir, project_repo, user_repo, tmp_path) -> None:
    _add_sample_fact(project_repo)
    output = tmp_path / "aider-out.md"
    runner = CliRunner()
    result = runner.invoke(main, ["shim", "aider", "--cwd", str(project_dir), "--output", str(output)])
    assert result.exit_code == 0
    assert output.exists()
    assert "# UMX Memory" in output.read_text()


def test_cli_shim_generic(project_dir, project_repo, user_repo) -> None:
    _add_sample_fact(project_repo, text="deploy via k8s")
    runner = CliRunner()
    result = runner.invoke(main, ["shim", "generic", "--cwd", str(project_dir)])
    assert result.exit_code == 0
    assert "# UMX Memory" in result.output
    assert "deploy via k8s" in result.output


def test_cli_shim_generic_with_output(project_dir, project_repo, user_repo, tmp_path) -> None:
    _add_sample_fact(project_repo)
    output = tmp_path / "generic-out.md"
    runner = CliRunner()
    result = runner.invoke(main, ["shim", "generic", "--cwd", str(project_dir), "--output", str(output)])
    assert result.exit_code == 0
    assert output.exists()
    assert "# UMX Memory" in output.read_text()


def test_cli_shim_generic_with_tool(project_dir, project_repo, user_repo) -> None:
    _add_sample_fact(project_repo)
    runner = CliRunner()
    result = runner.invoke(main, ["shim", "generic", "--cwd", str(project_dir), "--tool", "amp"])
    assert result.exit_code == 0
    assert "# UMX Memory" in result.output
