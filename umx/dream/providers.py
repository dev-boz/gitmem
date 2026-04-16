from __future__ import annotations

import os
import shutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from umx.config import UMXConfig
from umx.models import Fact, Provenance

PROVIDER_LOCAL = "local"
PROVIDER_STATUS_SUCCESS = "success"
PROVIDER_STATUS_FAILED = "failed"
PROVIDER_STATUS_UNAVAILABLE = "unavailable"

PROVIDER_API_ENV_VARS = {
    "anthropic": "ANTHROPIC_API_KEY",
    "cerebras": "CEREBRAS_API_KEY",
    "glm": "GLM_API_KEY",
    "groq": "GROQ_API_KEY",
    "minimax": "MINIMAX_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}


@dataclass(slots=True)
class ProviderAttempt:
    provider: str
    status: str
    detail: str | None = None
    model: str | None = None


@dataclass(slots=True)
class ProviderExtractionResult:
    session_id: str
    facts: list[Fact]
    attempts: list[ProviderAttempt] = field(default_factory=list)
    extracted_by: str = "native:session-heuristic"
    native_only: bool = True
    notice: str | None = None


class ProviderUnavailableError(RuntimeError):
    """Raised when a provider cannot be used for the current run."""


SessionProviderExtractor = Callable[[Path, str, list[dict], UMXConfig], list[Fact]]
SESSION_PROVIDER_EXTRACTORS: dict[str, SessionProviderExtractor] = {}
ReviewProviderReviewer = Callable[[object, object, list[Fact], list[Fact] | None, UMXConfig], dict[str, object]]
REVIEW_PROVIDER_REVIEWERS: dict[str, ReviewProviderReviewer] = {}


@dataclass(slots=True)
class ProviderReviewResult:
    action: str
    reason: str
    reviewed_by: str
    violations: list[str] = field(default_factory=list)
    attempts: list[ProviderAttempt] = field(default_factory=list)
    model_backed: bool = False


def _extracted_by_label(provider: str, config: UMXConfig) -> str:
    if provider == PROVIDER_LOCAL:
        return f"local:{config.dream.local_model or 'configured-model'}"
    if provider == config.dream.paid_provider:
        return f"provider:{provider}/paid"
    return f"provider:{provider}/{provider}"


def _stamp_extracted_by(facts: list[Fact], extracted_by: str) -> list[Fact]:
    stamped: list[Fact] = []
    for fact in facts:
        provenance = fact.provenance.to_dict()
        provenance["extracted_by"] = extracted_by
        stamped.append(
            fact.clone(
                provenance=Provenance.from_dict(provenance),
            )
        )
    return stamped


def _provider_key(provider: str, config: UMXConfig, env: Mapping[str, str]) -> str | None:
    if provider == PROVIDER_LOCAL:
        return config.dream.local_model
    if provider == config.dream.paid_provider and config.dream.paid_api_key:
        return config.dream.paid_api_key
    env_var = PROVIDER_API_ENV_VARS.get(provider)
    if env_var:
        return env.get(env_var)
    return None


def resolve_provider_plan(
    config: UMXConfig,
    *,
    env: Mapping[str, str] | None = None,
    extractors: Mapping[str, SessionProviderExtractor] | None = None,
) -> list[str]:
    active_env = env or os.environ
    available_extractors = extractors or SESSION_PROVIDER_EXTRACTORS
    plan: list[str] = []
    if config.dream.paid_provider and (
        _provider_key(config.dream.paid_provider, config, active_env)
        or config.dream.paid_provider in available_extractors
    ):
        plan.append(config.dream.paid_provider)
    for provider in config.dream.provider_rotation:
        if _provider_key(provider, config, active_env) or provider in available_extractors:
            plan.append(provider)
    if config.dream.local_model:
        plan.append(PROVIDER_LOCAL)
    return list(dict.fromkeys(plan))


def provider_execution_notice(result: ProviderExtractionResult) -> str | None:
    if not result.native_only:
        failed = [
            f"{attempt.provider} ({attempt.status})"
            for attempt in result.attempts
            if attempt.status != PROVIDER_STATUS_SUCCESS
        ]
        if failed:
            return (
                f"Provider extraction fell back before succeeding with {result.extracted_by}. "
                f"Attempts: {', '.join(failed)}."
            )
        return None

    if result.attempts:
        attempts = ", ".join(
            f"{attempt.provider} ({attempt.status})"
            for attempt in result.attempts
        )
        return (
            "All transcript extraction providers failed or were unavailable; "
            f"running native-only dream. Attempts: {attempts}."
        )
    return available_provider_notice()


def run_session_provider_extraction(
    repo_dir: Path,
    session_id: str,
    events: list[dict],
    config: UMXConfig | None,
    *,
    native_extractor: Callable[[], list[Fact]],
    env: Mapping[str, str] | None = None,
    extractors: Mapping[str, SessionProviderExtractor] | None = None,
) -> ProviderExtractionResult:
    cfg = config or UMXConfig()
    active_env = env or os.environ
    registered = extractors or SESSION_PROVIDER_EXTRACTORS
    attempts: list[ProviderAttempt] = []
    for provider in resolve_provider_plan(cfg, env=active_env, extractors=registered):
        extractor = registered.get(provider)
        model = cfg.dream.local_model if provider == PROVIDER_LOCAL else provider
        if extractor is None:
            attempts.append(
                ProviderAttempt(
                    provider=provider,
                    status=PROVIDER_STATUS_UNAVAILABLE,
                    detail="no extractor registered",
                    model=model,
                )
            )
            continue
        if provider != PROVIDER_LOCAL and not _provider_key(provider, cfg, active_env):
            attempts.append(
                ProviderAttempt(
                    provider=provider,
                    status=PROVIDER_STATUS_UNAVAILABLE,
                    detail="missing provider credentials",
                    model=model,
                )
            )
            continue
        try:
            facts = extractor(repo_dir, session_id, events, cfg)
        except ProviderUnavailableError as exc:
            attempts.append(
                ProviderAttempt(
                    provider=provider,
                    status=PROVIDER_STATUS_UNAVAILABLE,
                    detail=str(exc),
                    model=model,
                )
            )
            continue
        except Exception as exc:
            attempts.append(
                ProviderAttempt(
                    provider=provider,
                    status=PROVIDER_STATUS_FAILED,
                    detail=str(exc),
                    model=model,
                )
            )
            continue

        attempts.append(
            ProviderAttempt(
                provider=provider,
                status=PROVIDER_STATUS_SUCCESS,
                model=model,
            )
        )
        extracted_by = _extracted_by_label(provider, cfg)
        result = ProviderExtractionResult(
            session_id=session_id,
            facts=_stamp_extracted_by(facts, extracted_by),
            attempts=attempts,
            extracted_by=extracted_by,
            native_only=False,
        )
        result.notice = provider_execution_notice(result)
        return result

    native_result = ProviderExtractionResult(
        session_id=session_id,
        facts=_stamp_extracted_by(native_extractor(), "native:session-heuristic"),
        attempts=attempts,
        extracted_by="native:session-heuristic",
        native_only=True,
    )
    native_result.notice = provider_execution_notice(native_result)
    return native_result


def run_l2_review_with_providers(
    pr: object,
    conventions: object,
    existing_facts: list[Fact],
    new_facts: list[Fact] | None,
    config: UMXConfig | None,
    *,
    fallback_reviewer: Callable[[object, object, list[Fact], list[Fact] | None], dict[str, object]],
    env: Mapping[str, str] | None = None,
    reviewers: Mapping[str, ReviewProviderReviewer] | None = None,
) -> ProviderReviewResult:
    cfg = config or UMXConfig()
    active_env = env or os.environ
    registered = reviewers or REVIEW_PROVIDER_REVIEWERS
    attempts: list[ProviderAttempt] = []
    for provider in resolve_provider_plan(cfg, env=active_env, extractors=registered):
        reviewer = registered.get(provider)
        model = cfg.dream.local_model if provider == PROVIDER_LOCAL else provider
        if reviewer is None:
            attempts.append(
                ProviderAttempt(
                    provider=provider,
                    status=PROVIDER_STATUS_UNAVAILABLE,
                    detail="no reviewer registered",
                    model=model,
                )
            )
            continue
        if provider != PROVIDER_LOCAL and not _provider_key(provider, cfg, active_env):
            attempts.append(
                ProviderAttempt(
                    provider=provider,
                    status=PROVIDER_STATUS_UNAVAILABLE,
                    detail="missing provider credentials",
                    model=model,
                )
            )
            continue
        try:
            decision = reviewer(pr, conventions, existing_facts, new_facts, cfg)
        except ProviderUnavailableError as exc:
            attempts.append(
                ProviderAttempt(
                    provider=provider,
                    status=PROVIDER_STATUS_UNAVAILABLE,
                    detail=str(exc),
                    model=model,
                )
            )
            continue
        except Exception as exc:
            attempts.append(
                ProviderAttempt(
                    provider=provider,
                    status=PROVIDER_STATUS_FAILED,
                    detail=str(exc),
                    model=model,
                )
            )
            continue
        attempts.append(
            ProviderAttempt(
                provider=provider,
                status=PROVIDER_STATUS_SUCCESS,
                model=model,
            )
        )
        return ProviderReviewResult(
            action=str(decision["action"]),
            reason=str(decision["reason"]),
            reviewed_by=_extracted_by_label(provider, cfg),
            violations=list(decision.get("violations", [])),
            attempts=attempts,
            model_backed=True,
        )

    fallback = fallback_reviewer(pr, conventions, existing_facts, new_facts)
    return ProviderReviewResult(
        action=str(fallback["action"]),
        reason=str(fallback["reason"]),
        reviewed_by="native:l2-rules",
        violations=list(fallback.get("violations", [])),
        attempts=attempts,
        model_backed=False,
    )


def detected_capture_backends(home: Path | None = None) -> list[str]:
    root = home or Path.home()
    checks = (
        ("codex", root / ".codex" / "sessions"),
        ("copilot", root / ".copilot" / "session-state"),
        ("claude-code", root / ".claude" / "projects"),
        ("gemini", root / ".gemini"),
        ("opencode", root / ".local" / "share" / "opencode" / "opencode.db"),
        ("amp", root / ".local" / "share" / "amp" / "threads"),
    )
    return [name for name, path in checks if path.exists()]


def detected_external_dream_agents(
    which: callable | None = None,
) -> list[str]:
    lookup = which or shutil.which
    checks = (
        ("amp", "amp"),
        ("cursor", "cursor"),
        ("qodo", "qodo"),
        ("jules", "jules"),
    )
    return [name for name, binary in checks if lookup(binary)]


def missing_external_dream_agents(
    which: callable | None = None,
) -> list[str]:
    lookup = which or shutil.which
    checks = (
        ("amp", "amp"),
        ("cursor", "cursor"),
        ("qodo", "qodo"),
        ("jules", "jules"),
    )
    return [name for name, binary in checks if not lookup(binary)]


def available_provider_notice() -> str:
    parts = ["No transcript extraction provider configured; running native-only dream."]
    capture_backends = detected_capture_backends()
    if capture_backends:
        parts.append(
            "Detected local capture sources: " + ", ".join(capture_backends) + "."
        )
    external_agents = detected_external_dream_agents()
    if external_agents:
        parts.append(
            "Detected external dream-agent CLIs: "
            + ", ".join(external_agents)
            + "."
        )
    missing_agents = missing_external_dream_agents()
    if missing_agents:
        parts.append("Not installed here: " + ", ".join(missing_agents) + ".")
    return " ".join(parts)
