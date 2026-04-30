from __future__ import annotations

from umx import benchmark_llm
from umx.config import default_config


def test_normalize_benchmark_provider_accepts_codex() -> None:
    assert benchmark_llm.normalize_benchmark_provider("codex") == "codex-cli"
    assert benchmark_llm.normalize_benchmark_provider("codex-cli") == "codex-cli"


def test_normalize_benchmark_provider_accepts_gemini_and_opencode() -> None:
    assert benchmark_llm.normalize_benchmark_provider("gemini") == "gemini-cli"
    assert benchmark_llm.normalize_benchmark_provider("opencode") == "opencode-cli"


def test_resolve_benchmark_model_defaults_codex_to_gpt52() -> None:
    model = benchmark_llm.resolve_benchmark_model(
        "codex-cli",
        explicit_model=None,
        config=default_config(),
    )

    assert model == "gpt-5.2"


def test_resolve_benchmark_model_defaults_gemini_to_flash() -> None:
    model = benchmark_llm.resolve_benchmark_model(
        "gemini-cli",
        explicit_model=None,
        config=default_config(),
    )

    assert model == "gemini-2.5-flash"


def test_resolve_benchmark_model_defaults_opencode_to_big_pickle() -> None:
    model = benchmark_llm.resolve_benchmark_model(
        "opencode-cli",
        explicit_model=None,
        config=default_config(),
    )

    assert model == "opencode/big-pickle"
