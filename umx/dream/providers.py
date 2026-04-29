from __future__ import annotations

import os
import shutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from umx.config import UMXConfig
from umx.models import Fact, Provenance

PROVIDER_LOCAL = "local"
PROVIDER_STATUS_SUCCESS = "success"
PROVIDER_STATUS_FAILED = "failed"
PROVIDER_STATUS_UNAVAILABLE = "unavailable"
REVIEW_PROVIDER_ENV = "UMX_L2_REVIEW_PROVIDER"

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
    comment_body: str | None = None
    fact_notes: list[dict[str, str]] = field(default_factory=list)
    usage: dict[str, int] | None = None
    model: str | None = None
    prompt_id: str | None = None
    prompt_version: str | None = None


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
    registered = dict(reviewers or REVIEW_PROVIDER_REVIEWERS)
    if "anthropic" not in registered:
        from umx.dream.l2_review import anthropic_l2_reviewer

        registered["anthropic"] = anthropic_l2_reviewer
    if "claude-cli" not in registered:
        from umx.dream.l2_review import claude_cli_l2_reviewer

        registered["claude-cli"] = claude_cli_l2_reviewer
    attempts: list[ProviderAttempt] = []
    required_provider = _required_review_provider(cfg, active_env, registered)
    for provider in _resolve_review_provider_plan(
        cfg,
        active_env,
        registered,
        required_provider=required_provider,
    ):
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
            if provider == required_provider:
                raise ProviderUnavailableError(f"{provider} reviewer is not registered")
            continue
        provider_requires_key = provider != PROVIDER_LOCAL and provider in PROVIDER_API_ENV_VARS
        if provider_requires_key and not _provider_key(provider, cfg, active_env):
            attempts.append(
                ProviderAttempt(
                    provider=provider,
                    status=PROVIDER_STATUS_UNAVAILABLE,
                    detail="missing provider credentials",
                    model=model,
                )
            )
            if provider == required_provider:
                raise ProviderUnavailableError(_missing_review_provider_credentials(provider))
            continue
        try:
            decision = _invoke_reviewer_with_env(
                provider,
                reviewer,
                pr,
                conventions,
                existing_facts,
                new_facts,
                cfg,
                active_env,
            )
            action = str(decision["action"])
            reason = str(decision["reason"])
            violations = [str(item) for item in list(decision.get("violations", []))]
            comment_body = _coerce_optional_string(decision.get("comment_body"))
            fact_notes = _coerce_fact_notes(decision.get("fact_notes"))
            usage = _coerce_usage(decision.get("usage"))
            review_model = _coerce_optional_string(decision.get("model")) or model
            prompt_id = _coerce_optional_string(decision.get("prompt_id"))
            prompt_version = _coerce_optional_string(decision.get("prompt_version"))
        except ProviderUnavailableError as exc:
            attempts.append(
                ProviderAttempt(
                    provider=provider,
                    status=PROVIDER_STATUS_UNAVAILABLE,
                    detail=str(exc),
                    model=model,
                )
            )
            if provider == required_provider:
                raise
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
            if provider == required_provider:
                raise RuntimeError(f"{provider} L2 review failed: {exc}") from exc
            continue
        attempts.append(
            ProviderAttempt(
                provider=provider,
                status=PROVIDER_STATUS_SUCCESS,
                model=model,
            )
        )
        return ProviderReviewResult(
            action=action,
            reason=reason,
            reviewed_by=_extracted_by_label(provider, cfg),
            violations=violations,
            attempts=attempts,
            model_backed=True,
            comment_body=comment_body,
            fact_notes=fact_notes,
            usage=usage,
            model=review_model,
            prompt_id=prompt_id,
            prompt_version=prompt_version,
        )

    if required_provider is not None:
        raise ProviderUnavailableError(f"{required_provider} L2 review could not be completed")
    fallback = fallback_reviewer(pr, conventions, existing_facts, new_facts)
    return ProviderReviewResult(
        action=str(fallback["action"]),
        reason=str(fallback["reason"]),
        reviewed_by="native:l2-rules",
        violations=list(fallback.get("violations", [])),
        attempts=attempts,
        model_backed=False,
    )


def _resolve_review_provider_plan(
    config: UMXConfig,
    env: Mapping[str, str],
    reviewers: Mapping[str, ReviewProviderReviewer],
    *,
    required_provider: str | None = None,
) -> list[str]:
    plan = resolve_provider_plan(config, env=env, extractors=reviewers)
    if required_provider is not None:
        return [required_provider, *(provider for provider in plan if provider != required_provider)]
    preferred_provider = _preferred_review_provider(env, reviewers)
    if preferred_provider is not None and preferred_provider in reviewers and preferred_provider not in plan:
        plan.insert(0, preferred_provider)
    return list(dict.fromkeys(plan))


def _preferred_review_provider(
    env: Mapping[str, str],
    reviewers: Mapping[str, ReviewProviderReviewer],
) -> str | None:
    if "anthropic" in reviewers and env.get(PROVIDER_API_ENV_VARS["anthropic"]):
        return "anthropic"
    return None


def _required_review_provider(
    config: UMXConfig,
    env: Mapping[str, str],
    reviewers: Mapping[str, ReviewProviderReviewer],
) -> str | None:
    requested = env.get(REVIEW_PROVIDER_ENV)
    if requested:
        name = requested.strip().lower()
        if name not in reviewers:
            raise ProviderUnavailableError(
                f"unknown L2 reviewer provider: {requested!r} "
                f"(expected one of: {', '.join(sorted(reviewers))})"
            )
        return name
    if "anthropic" not in reviewers:
        return None
    if env.get(PROVIDER_API_ENV_VARS["anthropic"]):
        return "anthropic"
    if config.dream.paid_provider == "anthropic":
        return "anthropic"
    return None


def _missing_review_provider_credentials(provider: str) -> str:
    env_var = PROVIDER_API_ENV_VARS.get(provider)
    if env_var:
        return f"{env_var} is required for {provider}-backed L2 review"
    return f"credentials are required for {provider}-backed L2 review"


def _coerce_optional_string(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError("expected a string")
    stripped = value.strip()
    return stripped or None


def _coerce_usage(value: Any) -> dict[str, int] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise TypeError("usage must be a mapping")
    usage: dict[str, int] = {}
    for key in ("input_tokens", "output_tokens", "total_tokens"):
        if key not in value or value[key] is None:
            continue
        usage[key] = int(value[key])
    if "total_tokens" not in usage and {"input_tokens", "output_tokens"} <= set(usage):
        usage["total_tokens"] = usage["input_tokens"] + usage["output_tokens"]
    return usage or None


def _coerce_fact_notes(value: Any) -> list[dict[str, str]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise TypeError("fact_notes must be a list")
    normalized: list[dict[str, str]] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise TypeError(f"fact note {index} must be an object")
        note = _coerce_optional_string(item.get("note"))
        if note is None:
            raise TypeError(f"fact note {index} is missing `note`")
        payload: dict[str, str] = {"note": note}
        fact_id = _coerce_optional_string(item.get("fact_id"))
        if fact_id is not None:
            payload["fact_id"] = fact_id
        summary = _coerce_optional_string(item.get("summary"))
        if summary is not None:
            payload["summary"] = summary
        normalized.append(payload)
    return normalized


def _invoke_reviewer_with_env(
    provider: str,
    reviewer: ReviewProviderReviewer,
    pr: object,
    conventions: object,
    existing_facts: list[Fact],
    new_facts: list[Fact] | None,
    config: UMXConfig,
    env: Mapping[str, str],
) -> dict[str, object]:
    env_var = PROVIDER_API_ENV_VARS.get(provider)
    if env_var is None or env_var not in env:
        return reviewer(pr, conventions, existing_facts, new_facts, config)

    injected = env.get(env_var)
    if injected is None:
        return reviewer(pr, conventions, existing_facts, new_facts, config)

    had_original = env_var in os.environ
    original = os.environ.get(env_var)
    os.environ[env_var] = injected
    try:
        return reviewer(pr, conventions, existing_facts, new_facts, config)
    finally:
        if had_original and original is not None:
            os.environ[env_var] = original
        else:
            os.environ.pop(env_var, None)


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
