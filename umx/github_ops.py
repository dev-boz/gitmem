"""GitHub operations via the `gh` CLI.

All remote GitHub interaction (repo creation, PR management, workflow deployment)
is routed through this module. Requires `gh` to be installed and authenticated.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse, urlunparse

from umx.dream.pr_render import GovernancePRBodyError, touched_fact_ids_from_fact_delta
from umx.governance import (
    GOVERNANCE_LABEL_SPECS,
    GovernancePRConflictError,
    GovernancePROverlap,
    assert_governance_pr_body,
    pr_body_requires_fact_delta,
    reconcile_governance_label_set,
)

logger = logging.getLogger(__name__)

_GOVERNANCE_RULESET_NAME = "UMX Governance Main Protection"
_GOVERNANCE_PROTECTION_REFERENCE_PATH = (
    Path(__file__).resolve().parent
    / "templates"
    / "governance-branch-protection.reference.json"
)
_GH_API_ACCEPT_HEADER = "Accept: application/vnd.github+json"
_GH_API_VERSION_HEADER = "X-GitHub-Api-Version: 2022-11-28"


@dataclass(slots=True, frozen=True)
class GitHubRepoRef:
    owner: str | None
    name: str
    url: str | None = None


@dataclass(slots=True, frozen=True)
class OpenPullRequestSummary:
    number: int
    title: str
    url: str
    head_ref: str
    body: str
    labels: tuple[str, ...]


@dataclass(slots=True, frozen=True)
class RepositoryRulesetSummary:
    id: int
    name: str
    target: str
    source_type: str | None
    html_url: str | None


@dataclass(slots=True, frozen=True)
class GovernanceProtectionReference:
    default_branch: str
    require_pull_request: bool
    required_status_checks: tuple[str, ...]
    governance_merge_label: str
    override_cli_flag: str
    override_reason_flag: str
    override_audit_comment_marker: str


@dataclass(slots=True, frozen=True)
class GovernanceProtectionPlan:
    mode: str
    ruleset_name: str
    target_branch: str
    require_pull_request: bool
    required_status_checks: tuple[str, ...]
    governance_merge_label: str
    eligible: bool
    deferred_reason: str | None
    payload: dict[str, Any]


@dataclass(slots=True, frozen=True)
class GovernanceProtectionApplyResult:
    action: str
    plan: GovernanceProtectionPlan
    ruleset_id: int | None = None
    ruleset_url: str | None = None


class GitHubError(Exception):
    """Raised when a gh CLI operation fails."""


class GitHubRemoteIdentityError(RuntimeError):
    """Raised when a GitHub origin remote does not match the expected repo."""


@dataclass(slots=True, frozen=True)
class GhRetryPolicy:
    max_attempts: int = 3
    base_delay_seconds: float = 0.5
    backoff_multiplier: float = 2.0
    max_delay_seconds: float = 4.0
    rate_limit_delay_seconds: float = 2.0


class GhFailureKind(StrEnum):
    RATE_LIMIT = "rate_limit"
    TRANSIENT = "transient"
    PERMANENT = "permanent"


@dataclass(slots=True, frozen=True)
class GhFailureClassification:
    kind: GhFailureKind
    retryable: bool
    reason: str
    retry_after_seconds: float | None = None


_RETRY_AFTER_RE = re.compile(r"retry[- ]after[^0-9]*(\d+(?:\.\d+)?)", re.IGNORECASE)
_TRANSIENT_STATUS_RE = re.compile(r"\b(?:500|502|503|504)\b")


def _gh_output_text(result: subprocess.CompletedProcess[str]) -> str:
    stderr = result.stderr.strip()
    stdout = result.stdout.strip()
    if stderr and stdout:
        return f"{stderr}\n{stdout}"
    return stderr or stdout or f"exit code {result.returncode}"


def _retryable_pr_edit(args: tuple[str, ...]) -> bool:
    if len(args) < 4 or args[:2] != ("pr", "edit"):
        return False
    index = 3
    while index < len(args):
        flag = args[index]
        if flag not in {"--repo", "--add-label", "--remove-label"}:
            return False
        if index + 1 >= len(args):
            return False
        index += 2
    return True


def _retryable_gh_api(args: tuple[str, ...]) -> bool:
    if not args or args[0] != "api":
        return False
    method = "GET"
    index = 1
    while index < len(args):
        token = args[index]
        if token in {"--method", "-X"} and index + 1 < len(args):
            method = args[index + 1].upper()
            index += 2
            continue
        if token in {"-H", "--header", "--input"} and index + 1 < len(args):
            index += 2
            continue
        if token.startswith("-"):
            index += 1
            continue
        break
    return method == "GET"


def _retry_profile_for_args(args: tuple[str, ...]) -> str:
    if args[:2] in {("auth", "status"), ("repo", "view"), ("pr", "view"), ("pr", "list")}:
        return "safe"
    if _retryable_gh_api(args):
        return "safe"
    if args[:2] == ("label", "create") and "--force" in args:
        return "safe"
    if _retryable_pr_edit(args):
        return "safe"
    return "unsafe"


def _parse_retry_after_seconds(output: str) -> float | None:
    match = _RETRY_AFTER_RE.search(output)
    if not match:
        return None
    try:
        value = float(match.group(1))
    except ValueError:
        return None
    return value if value > 0 else None


def _classify_gh_failure(args: tuple[str, ...], result: subprocess.CompletedProcess[str]) -> GhFailureClassification:
    output = _gh_output_text(result).lower()
    if (
        "rate limit exceeded" in output
        or "secondary rate limit" in output
        or "retry-after" in output
        or "retry after" in output
        or re.search(r"\b429\b", output)
    ):
        return GhFailureClassification(
            kind=GhFailureKind.RATE_LIMIT,
            retryable=True,
            reason="rate limit",
            retry_after_seconds=_parse_retry_after_seconds(output),
        )
    if (
        _TRANSIENT_STATUS_RE.search(output)
        or "timeout" in output
        or "timed out" in output
        or "tls handshake timeout" in output
        or "connection reset" in output
        or "connection refused" in output
        or "temporary failure" in output
        or "network is unreachable" in output
        or "dial tcp" in output
        or re.search(r"\beof\b", output)
    ):
        return GhFailureClassification(
            kind=GhFailureKind.TRANSIENT,
            retryable=True,
            reason="transient failure",
        )
    return GhFailureClassification(
        kind=GhFailureKind.PERMANENT,
        retryable=False,
        reason="permanent failure",
    )


def _compute_retry_delay(
    retry_number: int,
    classification: GhFailureClassification,
    policy: GhRetryPolicy,
) -> float:
    if classification.kind == GhFailureKind.RATE_LIMIT:
        if classification.retry_after_seconds is not None:
            return max(classification.retry_after_seconds, policy.rate_limit_delay_seconds)
        return policy.rate_limit_delay_seconds
    delay = policy.base_delay_seconds * (policy.backoff_multiplier ** max(retry_number - 1, 0))
    return min(delay, policy.max_delay_seconds)


def _format_gh_error(
    args: tuple[str, ...],
    result: subprocess.CompletedProcess[str],
    classification: GhFailureClassification,
    attempts: int,
) -> str:
    message = f"gh {' '.join(args)}: {_gh_output_text(result)}"
    if classification.retryable:
        if attempts > 1:
            message += f" (after {attempts} attempts)"
        message += ". Next steps: verify network connectivity, gh auth, and GitHub rate limits, then retry."
    return message


def _env_int(name: str, default: int, *, minimum: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise GitHubError(f"invalid {name}: {raw!r}; expected integer >= {minimum}") from exc
    if value < minimum:
        raise GitHubError(f"invalid {name}: {raw!r}; expected integer >= {minimum}")
    return value


def _env_float(name: str, default: float, *, minimum: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise GitHubError(f"invalid {name}: {raw!r}; expected number >= {minimum}") from exc
    if value < minimum:
        raise GitHubError(f"invalid {name}: {raw!r}; expected number >= {minimum}")
    return value


def _gh_retry_policy_from_env() -> GhRetryPolicy:
    return GhRetryPolicy(
        max_attempts=_env_int("UMX_GH_RETRY_MAX_ATTEMPTS", 3, minimum=1),
        base_delay_seconds=_env_float("UMX_GH_RETRY_BASE_DELAY_SECONDS", 0.5, minimum=0.0),
        backoff_multiplier=_env_float("UMX_GH_RETRY_BACKOFF_MULTIPLIER", 2.0, minimum=1.0),
        max_delay_seconds=_env_float("UMX_GH_RETRY_MAX_DELAY_SECONDS", 4.0, minimum=0.0),
        rate_limit_delay_seconds=_env_float("UMX_GH_RATE_LIMIT_DELAY_SECONDS", 2.0, minimum=0.0),
    )


def _auth_status_unavailable(text: str) -> bool:
    lowered = text.lower()
    return (
        "gh cli is not installed" in lowered
        or "not logged in" in lowered
        or "authentication required" in lowered
        or "not authenticated" in lowered
    )


def _repo_view_not_found(text: str) -> bool:
    lowered = text.lower()
    return (
        "not found" in lowered
        or "could not resolve to a repository" in lowered
        or re.search(r"\b404\b", lowered) is not None
    )


def _run_gh(
    *args: str,
    check: bool = True,
    input_text: str | None = None,
    _policy: GhRetryPolicy | None = None,
    _runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    _sleep: Callable[[float], None] | None = None,
) -> subprocess.CompletedProcess[str]:
    policy = _policy or _gh_retry_policy_from_env()
    runner = subprocess.run if _runner is None else _runner
    sleep = time.sleep if _sleep is None else _sleep
    profile = _retry_profile_for_args(args)
    attempts = 0
    try:
        while True:
            attempts += 1
            result = runner(
                ["gh", *args],
                capture_output=True,
                text=True,
                check=False,
                input=input_text,
            )
            if result.returncode == 0:
                return result
            classification = _classify_gh_failure(args, result)
            should_retry = (
                profile == "safe"
                and classification.retryable
                and attempts < policy.max_attempts
            )
            if not should_retry:
                if check:
                    raise GitHubError(_format_gh_error(args, result, classification, attempts))
                return result
            delay = _compute_retry_delay(attempts, classification, policy)
            logger.warning(
                "gh %s failed with %s; retrying in %.2fs (%d/%d)",
                " ".join(args),
                classification.reason,
                delay,
                attempts + 1,
                policy.max_attempts,
            )
            sleep(delay)
    except FileNotFoundError:
        raise GitHubError("gh CLI is not installed; install from https://cli.github.com/")


def gh_available() -> bool:
    """Check if gh CLI is installed and authenticated."""
    try:
        result = _run_gh("auth", "status", check=False)
    except GitHubError as exc:
        if _auth_status_unavailable(str(exc)):
            return False
        raise
    if result.returncode == 0:
        return True
    detail = _gh_output_text(result)
    if _auth_status_unavailable(detail):
        return False
    classification = _classify_gh_failure(("auth", "status"), result)
    if classification.retryable:
        raise GitHubError(
            _format_gh_error(
                ("auth", "status"),
                result,
                classification,
                _gh_retry_policy_from_env().max_attempts,
            )
        )
    raise GitHubError(f"gh auth status: {detail}")


def _parse_github_repo_url(url: str) -> tuple[str | None, str | None]:
    stripped = url.strip()
    if not stripped:
        return None, None

    path: str | None = None
    parsed = urlparse(stripped)
    if parsed.scheme in {"https", "http"}:
        if parsed.hostname != "github.com":
            return None, None
        path = parsed.path.lstrip("/")
    elif parsed.scheme == "ssh":
        parsed = urlparse(stripped)
        if parsed.hostname != "github.com":
            return None, None
        path = parsed.path.lstrip("/")
    elif stripped.startswith("git@github.com:"):
        path = stripped.split(":", 1)[1]
    else:
        return None, None

    parts = [part for part in path.split("/") if part]
    if len(parts) < 2:
        return None, None
    owner = parts[-2]
    name = parts[-1]
    if name.endswith(".git"):
        name = name[:-4]
    return owner or None, name or None


def is_github_repo_url(url: str | None) -> bool:
    if not url:
        return False
    owner, name = _parse_github_repo_url(url)
    return owner is not None and name is not None


def redact_url_credentials(url: str | None) -> str | None:
    if not url:
        return url
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return url
    if parsed.username is None and parsed.password is None:
        return url
    host = parsed.hostname or ""
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"
    return urlunparse((parsed.scheme, host, parsed.path, parsed.params, parsed.query, parsed.fragment))


def expected_repo_ref(repo_dir: Path, *, config_org: str | None = None) -> GitHubRepoRef:
    """Resolve the expected repo identity for ``repo_dir`` from local scope only."""
    from umx.git_ops import git_remote_url
    from umx.scope import user_memory_dir

    name = "umx-user" if repo_dir.resolve() == user_memory_dir().resolve() else repo_dir.name
    return GitHubRepoRef(owner=config_org, name=name, url=git_remote_url(repo_dir))


def assert_expected_github_origin(
    repo_dir: Path,
    *,
    config_org: str | None = None,
    repo_label: str | None = None,
    operation: str = "GitHub sync",
) -> GitHubRepoRef:
    """Require GitHub origin remotes to match the expected scoped repo identity.

    Non-GitHub remotes are left unchanged so local bare repos used in tests keep
    working as they do today.
    """
    expected = expected_repo_ref(repo_dir, config_org=config_org)
    if not is_github_repo_url(expected.url):
        return expected

    remote_owner, remote_name = _parse_github_repo_url(str(expected.url))
    owner_mismatch = expected.owner is not None and remote_owner != expected.owner
    name_mismatch = remote_name != expected.name
    if not owner_mismatch and not name_mismatch:
        return GitHubRepoRef(owner=remote_owner, name=remote_name or expected.name, url=expected.url)

    actual_target = "/".join(part for part in [remote_owner, remote_name] if part) or "<unknown>"
    expected_target = "/".join(part for part in [expected.owner, expected.name] if part) or expected.name
    label = repo_label or "memory repo"
    raise GitHubRemoteIdentityError(
        f"{label} GitHub origin does not match the expected {operation} target: "
        f"expected {expected_target}, found {actual_target}"
    )


def resolve_repo_ref(repo_dir: Path, *, config_org: str | None = None) -> GitHubRepoRef:
    """Resolve the GitHub repo identity for ``repo_dir``.

    Prefer parsing a GitHub ``origin`` URL when available. If that fails,
    preserve the local user repo naming split by mapping ``~/.umx/user`` to
    ``umx-user`` and otherwise fall back to the local repo directory name.
    """
    from umx.git_ops import git_remote_url
    from umx.scope import user_memory_dir

    remote = git_remote_url(repo_dir)
    owner = config_org
    name: str | None = None
    if remote:
        remote_owner, remote_name = _parse_github_repo_url(remote)
        if remote_name:
            owner = remote_owner or config_org
            name = remote_name

    if not name:
        if repo_dir.resolve() == user_memory_dir().resolve():
            name = "umx-user"
        else:
            name = repo_dir.name

    return GitHubRepoRef(owner=owner, name=name, url=remote)


def load_governance_protection_reference(
    reference_path: Path | None = None,
) -> GovernanceProtectionReference:
    path = reference_path or _GOVERNANCE_PROTECTION_REFERENCE_PATH
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise GitHubError(f"governance protection reference not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise GitHubError(f"governance protection reference is not valid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise GitHubError("governance protection reference must be a JSON object")

    default_branch = payload.get("default_branch")
    require_pull_request = payload.get("require_pull_request")
    required_status_checks = payload.get("required_status_checks")
    governance_merge_label = payload.get("governance_merge_label")
    override = payload.get("override")

    if not isinstance(default_branch, str) or not default_branch.strip():
        raise GitHubError("governance protection reference missing default_branch")
    if not isinstance(require_pull_request, bool):
        raise GitHubError("governance protection reference missing require_pull_request")
    if not isinstance(required_status_checks, list) or not all(
        isinstance(item, str) and item.strip() for item in required_status_checks
    ):
        raise GitHubError("governance protection reference missing required_status_checks")
    if not isinstance(governance_merge_label, str) or not governance_merge_label.strip():
        raise GitHubError("governance protection reference missing governance_merge_label")
    if not isinstance(override, dict):
        raise GitHubError("governance protection reference missing override")

    override_cli_flag = override.get("cli_flag")
    override_reason_flag = override.get("reason_flag")
    override_audit_comment_marker = override.get("audit_comment_marker")
    if not isinstance(override_cli_flag, str) or not override_cli_flag.strip():
        raise GitHubError("governance protection reference missing override.cli_flag")
    if not isinstance(override_reason_flag, str) or not override_reason_flag.strip():
        raise GitHubError("governance protection reference missing override.reason_flag")
    if (
        not isinstance(override_audit_comment_marker, str)
        or not override_audit_comment_marker.strip()
    ):
        raise GitHubError(
            "governance protection reference missing override.audit_comment_marker"
        )

    return GovernanceProtectionReference(
        default_branch=default_branch.strip(),
        require_pull_request=require_pull_request,
        required_status_checks=tuple(item.strip() for item in required_status_checks),
        governance_merge_label=governance_merge_label.strip(),
        override_cli_flag=override_cli_flag.strip(),
        override_reason_flag=override_reason_flag.strip(),
        override_audit_comment_marker=override_audit_comment_marker.strip(),
    )


def build_governance_protection_ruleset_payload(
    reference: GovernanceProtectionReference,
) -> dict[str, Any]:
    rules: list[dict[str, Any]] = []
    if reference.require_pull_request:
        rules.append(
            {
                "type": "pull_request",
                "parameters": {
                    "dismiss_stale_reviews_on_push": False,
                    "require_code_owner_review": False,
                    "require_last_push_approval": False,
                    "required_approving_review_count": 0,
                    "required_review_thread_resolution": False,
                },
            }
        )
    if reference.required_status_checks:
        rules.append(
            {
                "type": "required_status_checks",
                "parameters": {
                    "do_not_enforce_on_create": False,
                    "required_status_checks": [
                        {"context": context, "integration_id": None}
                        for context in reference.required_status_checks
                    ],
                    "strict_required_status_checks_policy": False,
                },
            }
        )
    return {
        "name": _GOVERNANCE_RULESET_NAME,
        "target": "branch",
        "enforcement": "active",
        "bypass_actors": [],
        "conditions": {
            "ref_name": {
                "include": [f"refs/heads/{reference.default_branch}"],
                "exclude": [],
            }
        },
        "rules": rules,
    }


def plan_governance_protection(
    mode: str,
    *,
    direct_main_writes: bool = True,
    reference: GovernanceProtectionReference | None = None,
) -> GovernanceProtectionPlan:
    resolved_reference = (
        load_governance_protection_reference()
        if reference is None
        else reference
    )
    deferred_reason: str | None = None
    if mode != "remote":
        deferred_reason = "governance protection auto-setup is only supported in remote mode"
    elif direct_main_writes:
        deferred_reason = (
            "remote sync and governed maintenance flows still perform direct pushes "
            "to main; enabling require_pull_request would break umx sync"
        )
    return GovernanceProtectionPlan(
        mode=mode,
        ruleset_name=_GOVERNANCE_RULESET_NAME,
        target_branch=resolved_reference.default_branch,
        require_pull_request=resolved_reference.require_pull_request,
        required_status_checks=resolved_reference.required_status_checks,
        governance_merge_label=resolved_reference.governance_merge_label,
        eligible=deferred_reason is None,
        deferred_reason=deferred_reason,
        payload=build_governance_protection_ruleset_payload(resolved_reference),
    )


def _run_gh_api(
    endpoint: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    args = [
        "api",
        "--method",
        method.upper(),
        "-H",
        _GH_API_ACCEPT_HEADER,
        "-H",
        _GH_API_VERSION_HEADER,
        endpoint,
    ]
    input_text = None
    if payload is not None:
        args.extend(["--input", "-"])
        input_text = json.dumps(payload, sort_keys=True)
    return _run_gh(*args, check=check, input_text=input_text)


def _parse_json_object(stdout: str, *, context: str) -> dict[str, Any]:
    try:
        payload = json.loads(stdout or "{}")
    except json.JSONDecodeError as exc:
        raise GitHubError(f"{context} returned malformed JSON") from exc
    if not isinstance(payload, dict):
        raise GitHubError(f"{context} returned malformed JSON")
    return payload


def _parse_json_list(stdout: str, *, context: str) -> list[Any]:
    try:
        payload = json.loads(stdout or "[]")
    except json.JSONDecodeError as exc:
        raise GitHubError(f"{context} returned malformed JSON") from exc
    if not isinstance(payload, list):
        raise GitHubError(f"{context} returned malformed JSON")
    return payload


def list_repository_rulesets(org: str, repo_name: str) -> list[RepositoryRulesetSummary]:
    summaries: list[RepositoryRulesetSummary] = []
    page = 1
    while True:
        result = _run_gh_api(
            "repos/"
            f"{org}/{repo_name}/rulesets?includes_parents=false&targets=branch"
            f"&per_page=100&page={page}"
        )
        payload = _parse_json_list(
            result.stdout,
            context=f"gh api rulesets list page {page}",
        )
        if not payload:
            break
        for item in payload:
            if not isinstance(item, dict):
                raise GitHubError("gh api rulesets list returned malformed JSON")
            ruleset_id = item.get("id")
            name = item.get("name")
            target = item.get("target")
            source_type = item.get("source_type")
            links = item.get("_links")
            if (
                not isinstance(ruleset_id, int)
                or not isinstance(name, str)
                or not isinstance(target, str)
            ):
                raise GitHubError("gh api rulesets list returned malformed JSON")
            html_url = None
            if isinstance(links, dict):
                html = links.get("html")
                if isinstance(html, dict) and isinstance(html.get("href"), str):
                    html_url = html["href"]
            summaries.append(
                RepositoryRulesetSummary(
                    id=ruleset_id,
                    name=name,
                    target=target,
                    source_type=source_type if isinstance(source_type, str) else None,
                    html_url=html_url,
                )
            )
        if len(payload) < 100:
            break
        page += 1
    return summaries


def _get_repository_ruleset(org: str, repo_name: str, ruleset_id: int) -> dict[str, Any]:
    result = _run_gh_api(f"repos/{org}/{repo_name}/rulesets/{ruleset_id}")
    return _parse_json_object(result.stdout, context="gh api ruleset get")


def _ruleset_rule_key(rule: dict[str, Any]) -> str:
    rule_type = rule.get("type")
    return rule_type if isinstance(rule_type, str) else json.dumps(rule, sort_keys=True)


def _normalise_required_status_checks_rule(rule: dict[str, Any]) -> dict[str, Any]:
    parameters = rule.get("parameters")
    if not isinstance(parameters, dict):
        parameters = {}
    raw_checks = parameters.get("required_status_checks")
    if not isinstance(raw_checks, list):
        raw_checks = []
    checks: list[dict[str, Any]] = []
    for item in raw_checks:
        if not isinstance(item, dict):
            continue
        context = item.get("context")
        integration_id = item.get("integration_id")
        if not isinstance(context, str) or not context.strip():
            continue
        checks.append(
            {
                "context": context.strip(),
                "integration_id": integration_id if isinstance(integration_id, int) else None,
            }
        )
    return {
        "type": "required_status_checks",
        "parameters": {
            "do_not_enforce_on_create": bool(
                parameters.get("do_not_enforce_on_create", False)
            ),
            "required_status_checks": sorted(
                checks,
                key=lambda item: (item["context"], item["integration_id"] or -1),
            ),
            "strict_required_status_checks_policy": bool(
                parameters.get("strict_required_status_checks_policy", False)
            ),
        },
    }


def _normalise_pull_request_rule(rule: dict[str, Any]) -> dict[str, Any]:
    parameters = rule.get("parameters")
    if not isinstance(parameters, dict):
        parameters = {}
    return {
        "type": "pull_request",
        "parameters": {
            "dismiss_stale_reviews_on_push": bool(
                parameters.get("dismiss_stale_reviews_on_push", False)
            ),
            "require_code_owner_review": bool(
                parameters.get("require_code_owner_review", False)
            ),
            "require_last_push_approval": bool(
                parameters.get("require_last_push_approval", False)
            ),
            "required_approving_review_count": int(
                parameters.get("required_approving_review_count", 0)
            ),
            "required_review_thread_resolution": bool(
                parameters.get("required_review_thread_resolution", False)
            ),
        },
    }


def _canonicalise_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _canonicalise_json(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return sorted(
            (_canonicalise_json(item) for item in value),
            key=lambda item: json.dumps(item, sort_keys=True),
        )
    return value


def _normalise_ruleset_payload(payload: dict[str, Any]) -> dict[str, Any]:
    raw_conditions = payload.get("conditions")
    ref_name = raw_conditions.get("ref_name") if isinstance(raw_conditions, dict) else {}
    include = ref_name.get("include") if isinstance(ref_name, dict) else []
    exclude = ref_name.get("exclude") if isinstance(ref_name, dict) else []
    rules_payload = payload.get("rules")
    if not isinstance(rules_payload, list):
        rules_payload = []
    normalised_rules: list[dict[str, Any]] = []
    for rule in rules_payload:
        if not isinstance(rule, dict):
            continue
        rule_type = rule.get("type")
        if rule_type == "required_status_checks":
            normalised_rules.append(_normalise_required_status_checks_rule(rule))
            continue
        if rule_type == "pull_request":
            normalised_rules.append(_normalise_pull_request_rule(rule))
            continue
        normalised_rules.append(_canonicalise_json(rule))
    bypass_actors = payload.get("bypass_actors")
    return {
        "name": payload.get("name"),
        "target": payload.get("target"),
        "enforcement": payload.get("enforcement"),
        "bypass_actors": _canonicalise_json(
            bypass_actors if isinstance(bypass_actors, list) else []
        ),
        "conditions": {
            "ref_name": {
                "include": sorted(
                    item.strip()
                    for item in include
                    if isinstance(item, str) and item.strip()
                ),
                "exclude": sorted(
                    item.strip()
                    for item in exclude
                    if isinstance(item, str) and item.strip()
                ),
            }
        },
        "rules": sorted(normalised_rules, key=_ruleset_rule_key),
    }


def _ruleset_identity_matches(summary: RepositoryRulesetSummary, *, ruleset_name: str) -> bool:
    return summary.target == "branch" and summary.name == ruleset_name


def _ruleset_response_identity(payload: dict[str, Any]) -> tuple[int | None, str | None]:
    ruleset_id = payload.get("id")
    links = payload.get("_links")
    ruleset_url = None
    if isinstance(links, dict):
        html = links.get("html")
        if isinstance(html, dict) and isinstance(html.get("href"), str):
            ruleset_url = html["href"]
    return (ruleset_id if isinstance(ruleset_id, int) else None, ruleset_url)


def apply_governance_protection(
    org: str,
    repo_name: str,
    plan: GovernanceProtectionPlan,
) -> GovernanceProtectionApplyResult:
    if not plan.eligible:
        reason = plan.deferred_reason or "governance protection is not eligible"
        raise GitHubError(
            f"governance protection cannot be enabled for {org}/{repo_name}: {reason}"
        )

    matches = [
        summary
        for summary in list_repository_rulesets(org, repo_name)
        if _ruleset_identity_matches(summary, ruleset_name=plan.ruleset_name)
    ]
    if len(matches) > 1:
        raise GitHubError(
            f"multiple managed governance rulesets found for {org}/{repo_name}; "
            "resolve duplicates before enabling governance protection"
        )
    if not matches:
        created = _parse_json_object(
            _run_gh_api(
                f"repos/{org}/{repo_name}/rulesets",
                method="POST",
                payload=plan.payload,
            ).stdout,
            context="gh api ruleset create",
        )
        ruleset_id, ruleset_url = _ruleset_response_identity(created)
        return GovernanceProtectionApplyResult(
            action="created",
            plan=plan,
            ruleset_id=ruleset_id,
            ruleset_url=ruleset_url,
        )

    current_payload = _get_repository_ruleset(org, repo_name, matches[0].id)
    if _normalise_ruleset_payload(current_payload) == _normalise_ruleset_payload(plan.payload):
        ruleset_id, ruleset_url = _ruleset_response_identity(current_payload)
        return GovernanceProtectionApplyResult(
            action="unchanged",
            plan=plan,
            ruleset_id=ruleset_id,
            ruleset_url=ruleset_url,
        )

    updated = _parse_json_object(
        _run_gh_api(
            f"repos/{org}/{repo_name}/rulesets/{matches[0].id}",
            method="PATCH",
            payload=plan.payload,
        ).stdout,
        context="gh api ruleset update",
    )
    ruleset_id, ruleset_url = _ruleset_response_identity(updated)
    return GovernanceProtectionApplyResult(
        action="updated",
        plan=plan,
        ruleset_id=ruleset_id,
        ruleset_url=ruleset_url,
    )


def repo_exists(org: str, name: str) -> bool:
    """Check if a repo exists under the org."""
    args = ("repo", "view", f"{org}/{name}", "--json", "name")
    result = _run_gh(*args, check=False)
    if result.returncode == 0:
        return True
    detail = _gh_output_text(result)
    if _repo_view_not_found(detail):
        return False
    classification = _classify_gh_failure(args, result)
    if classification.retryable:
        raise GitHubError(
            _format_gh_error(
                args,
                result,
                classification,
                _gh_retry_policy_from_env().max_attempts,
            )
        )
    raise GitHubError(f"gh {' '.join(args)}: {detail}")


def list_open_pull_requests(org: str, repo_name: str) -> list[OpenPullRequestSummary]:
    result = _run_gh(
        "pr",
        "list",
        "--repo",
        f"{org}/{repo_name}",
        "--state",
        "open",
        "--base",
        "main",
        "--json",
        "number,title,url,headRefName,body,labels",
    )
    try:
        payload = json.loads(result.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise GitHubError("gh pr list returned malformed JSON") from exc
    if not isinstance(payload, list):
        raise GitHubError("gh pr list returned malformed JSON")

    summaries: list[OpenPullRequestSummary] = []
    for item in payload:
        if not isinstance(item, dict):
            raise GitHubError("gh pr list returned malformed JSON")
        number = item.get("number")
        title = item.get("title")
        url = item.get("url")
        head_ref = item.get("headRefName")
        body = item.get("body")
        labels_payload = item.get("labels")
        if (
            not isinstance(number, int)
            or not isinstance(title, str)
            or not isinstance(url, str)
            or not isinstance(head_ref, str)
            or not isinstance(body, str)
            or not isinstance(labels_payload, list)
        ):
            raise GitHubError("gh pr list returned malformed JSON")
        labels: list[str] = []
        for label_item in labels_payload:
            if not isinstance(label_item, dict):
                raise GitHubError("gh pr list returned malformed JSON")
            label_name = label_item.get("name")
            if isinstance(label_name, str) and label_name.strip():
                labels.append(label_name.strip())
        summaries.append(
            OpenPullRequestSummary(
                number=number,
                title=title,
                url=url,
                head_ref=head_ref,
                body=body,
                labels=tuple(labels),
            )
        )
    return summaries


def assert_no_open_governance_pr_overlap(
    org: str,
    repo_name: str,
    *,
    branch: str,
    body: str,
    labels: list[str] | None = None,
) -> None:
    if not pr_body_requires_fact_delta(labels, branch=branch):
        return
    if not repo_exists(org, repo_name):
        return
    payload = assert_governance_pr_body(body)
    if payload is None:
        return
    touched = touched_fact_ids_from_fact_delta(payload)
    if not touched:
        return

    overlaps: list[GovernancePROverlap] = []
    for open_pr in list_open_pull_requests(org, repo_name):
        try:
            open_payload = assert_governance_pr_body(open_pr.body)
        except GovernancePRBodyError:
            continue
        shared = tuple(sorted(touched & touched_fact_ids_from_fact_delta(open_payload)))
        if not shared:
            continue
        overlaps.append(
            GovernancePROverlap(
                pr_number=open_pr.number,
                pr_url=open_pr.url,
                pr_title=open_pr.title,
                head_ref=open_pr.head_ref,
                overlapping_fact_ids=shared,
            )
        )
    if overlaps:
        raise GovernancePRConflictError(overlaps)


def create_repo(org: str, name: str, private: bool = True) -> str:
    """Create a repo under the org. Returns the clone URL."""
    visibility = "--private" if private else "--public"
    result = _run_gh(
        "repo", "create", f"{org}/{name}",
        visibility,
        "--clone=false",
        "--description", f"umx memory repo for {name}",
    )
    url = f"https://github.com/{org}/{name}.git"
    logger.info("created repo %s/%s", org, name)
    return url


def ensure_repo(org: str, name: str, private: bool = True) -> str:
    """Create repo if it doesn't exist. Returns the clone URL."""
    if repo_exists(org, name):
        return f"https://github.com/{org}/{name}.git"
    return create_repo(org, name, private=private)


def set_remote(repo_dir: Path, url: str, remote: str = "origin") -> None:
    """Set (or update) the git remote origin for a local repo."""
    from umx.git_ops import _run_git
    existing = _run_git(repo_dir, "remote", "get-url", remote)
    if existing.returncode == 0:
        if existing.stdout.strip() == url:
            return
        _run_git(repo_dir, "remote", "set-url", remote, url)
    else:
        _run_git(repo_dir, "remote", "add", remote, url)


def push_branch(repo_dir: Path, branch: str, set_upstream: bool = True) -> bool:
    """Push a branch to origin."""
    from umx.git_ops import git_push
    return git_push(repo_dir, branch=branch, set_upstream=set_upstream)


def push_main(repo_dir: Path) -> bool:
    """Push main to origin (for session sync)."""
    from umx.git_ops import git_push
    return git_push(repo_dir, branch="main")


def ensure_governance_labels(
    org: str,
    repo_name: str,
    labels: list[str] | None = None,
) -> bool:
    """Ensure governance labels exist on the repo."""
    requested = (
        list(GOVERNANCE_LABEL_SPECS)
        if labels is None
        else [label for label in dict.fromkeys(labels) if label in GOVERNANCE_LABEL_SPECS]
    )
    if not requested:
        return True
    ok = True
    for label in requested:
        color, description = GOVERNANCE_LABEL_SPECS[label]
        result = _run_gh(
            "label",
            "create",
            label,
            "--repo",
            f"{org}/{repo_name}",
            "--color",
            color,
            "--description",
            description,
            "--force",
            check=False,
        )
        ok = ok and result.returncode == 0
    return ok


def add_pr_labels(org: str, repo_name: str, pr_number: int, labels: list[str]) -> bool:
    """Add labels to an existing PR."""
    if not labels:
        return True
    if not ensure_governance_labels(org, repo_name, labels):
        return False
    args = ["pr", "edit", str(pr_number), "--repo", f"{org}/{repo_name}"]
    for label in labels:
        args.extend(["--add-label", label])
    result = _run_gh(*args, check=False)
    return result.returncode == 0


def read_pr_labels(org: str, repo_name: str, pr_number: int) -> list[str] | None:
    result = _run_gh(
        "pr",
        "view",
        str(pr_number),
        "--repo",
        f"{org}/{repo_name}",
        "--json",
        "labels",
        check=False,
    )
    if result.returncode != 0:
        logger.warning("PR label read failed: %s", result.stderr.strip())
        return None
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return None
    labels = payload.get("labels")
    if not isinstance(labels, list):
        return None
    parsed: list[str] = []
    for item in labels:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            continue
        label = str(item.get("name")).strip()
        if not label:
            continue
        governance_like = (
            label == "human-review"
            or label.startswith(("state:", "type:", "confidence:", "impact:"))
        )
        if governance_like and label not in GOVERNANCE_LABEL_SPECS:
            logger.warning("unknown governance label on PR #%s: %s", pr_number, label)
            return None
        parsed.append(label)
    return parsed


def remove_pr_labels(org: str, repo_name: str, pr_number: int, labels: list[str]) -> bool:
    if not labels:
        return True
    args = ["pr", "edit", str(pr_number), "--repo", f"{org}/{repo_name}"]
    for label in labels:
        args.extend(["--remove-label", label])
    result = _run_gh(*args, check=False)
    return result.returncode == 0


def reconcile_pr_labels(
    org: str,
    repo_name: str,
    pr_number: int,
    desired_labels: list[str],
    *,
    current_labels: list[str] | None = None,
) -> bool:
    if current_labels is None:
        current_labels = read_pr_labels(org, repo_name, pr_number)
    if current_labels is None:
        return False
    desired = list(dict.fromkeys(desired_labels))
    if not ensure_governance_labels(org, repo_name, desired):
        return False
    add, remove = reconcile_governance_label_set(current_labels, desired)
    if not add and not remove:
        return True
    args = ["pr", "edit", str(pr_number), "--repo", f"{org}/{repo_name}"]
    for label in add:
        args.extend(["--add-label", label])
    for label in remove:
        args.extend(["--remove-label", label])
    result = _run_gh(*args, check=False)
    return result.returncode == 0


def create_pr(
    org: str,
    repo_name: str,
    branch: str,
    title: str,
    body: str,
    labels: list[str] | None = None,
) -> int | None:
    """Open a PR on GitHub. Returns the PR number or None on failure."""
    if pr_body_requires_fact_delta(labels, branch=branch):
        assert_governance_pr_body(body)
        assert_no_open_governance_pr_overlap(
            org,
            repo_name,
            branch=branch,
            body=body,
            labels=labels,
        )
    if labels and not ensure_governance_labels(org, repo_name, labels):
        return None
    args = [
        "pr", "create",
        "--repo", f"{org}/{repo_name}",
        "--head", branch,
        "--base", "main",
        "--title", title,
        "--body", body,
    ]
    if labels:
        for label in labels:
            args.extend(["--label", label])
    result = _run_gh(*args, check=False)
    if result.returncode != 0:
        logger.warning("PR creation failed: %s", result.stderr.strip())
        return None
    # gh pr create prints the URL; extract PR number from it
    url = result.stdout.strip()
    if "/" in url:
        try:
            return int(url.rstrip("/").split("/")[-1])
        except ValueError:
            pass
    return None


def read_pr_body(org: str, repo_name: str, pr_number: int) -> str | None:
    """Read a PR body via the gh CLI."""
    result = _run_gh(
        "pr",
        "view",
        str(pr_number),
        "--repo",
        f"{org}/{repo_name}",
        "--json",
        "body",
        check=False,
    )
    if result.returncode != 0:
        logger.warning("PR read failed: %s", result.stderr.strip())
        return None
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return None
    body = payload.get("body")
    return body if isinstance(body, str) else None


def merge_pr(
    org: str,
    repo_name: str,
    pr_number: int,
    method: str = "squash",
    *,
    admin: bool = False,
    match_head_commit: str | None = None,
) -> bool:
    """Merge a PR. Returns True on success."""
    args = [
        "pr", "merge", str(pr_number),
        "--repo", f"{org}/{repo_name}",
        f"--{method}",
        "--delete-branch",
    ]
    if admin:
        args.append("--admin")
    if match_head_commit:
        args.extend(["--match-head-commit", match_head_commit])
    result = _run_gh(
        *args,
        check=False,
    )
    return result.returncode == 0


def comment_pr(org: str, repo_name: str, pr_number: int, body: str) -> bool:
    """Add a comment to a PR."""
    result = _run_gh(
        "pr", "comment", str(pr_number),
        "--repo", f"{org}/{repo_name}",
        "--body", body,
        check=False,
    )
    return result.returncode == 0


def close_pr(org: str, repo_name: str, pr_number: int, comment: str | None = None) -> bool:
    """Close a PR without merging."""
    if comment:
        comment_pr(org, repo_name, pr_number, comment)
    result = _run_gh(
        "pr", "close", str(pr_number),
        "--repo", f"{org}/{repo_name}",
        check=False,
    )
    return result.returncode == 0


def deploy_workflows(repo_dir: Path) -> list[Path]:
    """Write GitHub Actions workflow templates to the repo."""
    from umx.actions import write_workflow_templates
    return write_workflow_templates(repo_dir)
