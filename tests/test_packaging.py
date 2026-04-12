from __future__ import annotations

from pathlib import Path
import tomllib


def test_pyproject_exposes_gitmem_and_umx_scripts() -> None:
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text())
    scripts = data["project"]["scripts"]

    assert scripts["umx"] == "umx.cli:main"
    assert scripts["gitmem"] == "umx.cli:main"
