from __future__ import annotations

from umx import benchmark_llm
from umx.config import default_config


def test_normalize_benchmark_provider_accepts_codex() -> None:
    assert benchmark_llm.normalize_benchmark_provider("codex") == "codex-cli"
    assert benchmark_llm.normalize_benchmark_provider("codex-cli") == "codex-cli"


def test_resolve_benchmark_model_defaults_codex_to_gpt52() -> None:
    model = benchmark_llm.resolve_benchmark_model(
        "codex-cli",
        explicit_model=None,
        config=default_config(),
    )

    assert model == "gpt-5.2"
