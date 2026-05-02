from __future__ import annotations

from pathlib import Path

import pytest

from umx.config import default_config, save_config
from umx.scope import config_path, init_local_umx, init_project_memory, project_memory_dir, user_memory_dir


@pytest.fixture(autouse=True)
def isolate_provider_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "ANTHROPIC_API_KEY",
        "NVIDIA_API_KEY",
        "GROQ_API_KEY",
        "OPENAI_API_KEY",
        "VOYAGE_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "UMX_PROVIDER",
        "UMX_L2_REVIEW_PROVIDER",
    ):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def umx_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "umxhome"
    monkeypatch.setenv("UMX_HOME", str(home))
    init_local_umx()
    save_config(config_path(), default_config())
    return home


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    (project / ".git").mkdir()
    return project


@pytest.fixture
def project_repo(umx_home: Path, project_dir: Path) -> Path:
    init_project_memory(project_dir)
    return project_memory_dir(project_dir)


@pytest.fixture
def user_repo(umx_home: Path) -> Path:
    return user_memory_dir()
