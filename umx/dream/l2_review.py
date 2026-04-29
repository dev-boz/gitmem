from __future__ import annotations

import json
import re
from typing import Any

from umx.config import UMXConfig
from umx.conventions import ConventionSet
from umx.dream.pr_render import assert_governance_pr_body
from umx.dream.providers import ProviderUnavailableError
from umx.governance import PRProposal
from umx.models import Fact
from umx.providers import anthropic as anthropic_provider
from umx.providers import claude_cli as claude_cli_provider


L2_REVIEW_PROMPT_ID = "anthropic-l2-review"
L2_REVIEW_PROMPT_VERSION = "v1"
L2_CLAUDE_CLI_PROMPT_ID = "claude-cli-l2-review"
REVIEW_COMMENT_MARKER = "<!-- umx:l2-review -->"
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(?P<payload>\{.*\})\s*```", re.DOTALL)
_VALID_ACTIONS = frozenset({"approve", "reject", "escalate"})

ANTHROPIC_PROVIDER_ALIASES = frozenset({"anthropic", "anthropic-api", "api"})
CLAUDE_CLI_PROVIDER_ALIASES = frozenset({"claude-cli", "claude-code", "cli", "oauth"})


def normalize_l2_reviewer_provider(provider: str | None) -> str | None:
    if provider is None:
        return None
    name = provider.strip().lower()
    if not name or name in ANTHROPIC_PROVIDER_ALIASES:
        return "anthropic"
    if name in CLAUDE_CLI_PROVIDER_ALIASES:
        return "claude-cli"
    raise RuntimeError(
        f"unknown L2 reviewer provider: {provider!r} "
        f"(expected one of: anthropic, claude-cli)"
    )


def anthropic_l2_reviewer(
    pr: PRProposal,
    conventions: ConventionSet,
    existing_facts: list[Fact],
    new_facts: list[Fact] | None,
    config: UMXConfig,
) -> dict[str, object]:
    api_key = config.dream.paid_api_key if config.dream.paid_provider == "anthropic" else None
    if not api_key:
        import os

        api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise ProviderUnavailableError("ANTHROPIC_API_KEY is required for Anthropic-backed L2 review")

    context = build_l2_review_context(pr, conventions, existing_facts, new_facts)
    response = anthropic_provider.send_anthropic_message(
        api_key=api_key,
        model=config.dream.l2_model,
        system=_review_system_prompt(),
        prompt=_review_user_prompt(context),
    )
    return _build_review_payload(
        response_text=response.text,
        response_model=response.model,
        usage=response.usage,
        existing_facts=existing_facts,
        new_facts=new_facts,
        prompt_id=L2_REVIEW_PROMPT_ID,
    )


def claude_cli_l2_reviewer(
    pr: PRProposal,
    conventions: ConventionSet,
    existing_facts: list[Fact],
    new_facts: list[Fact] | None,
    config: UMXConfig,
) -> dict[str, object]:
    """L2 reviewer backed by the Claude Code CLI (`claude -p`).

    Uses the user's existing Claude Code OAuth session, so no
    `ANTHROPIC_API_KEY` is required. Falls back with a clear error if the
    CLI is not installed or not authenticated.
    """

    if not claude_cli_provider.claude_cli_available():
        raise ProviderUnavailableError(
            "Claude Code CLI is not available; install `claude` and run `claude login` "
            "or set UMX_CLAUDE_CLI_BIN to the binary path"
        )

    context = build_l2_review_context(pr, conventions, existing_facts, new_facts)
    response = claude_cli_provider.send_claude_cli_message(
        model=config.dream.l2_model,
        system=_review_system_prompt(),
        prompt=_review_user_prompt(context),
    )
    return _build_review_payload(
        response_text=response.text,
        response_model=response.model,
        usage=response.usage,
        existing_facts=existing_facts,
        new_facts=new_facts,
        prompt_id=L2_CLAUDE_CLI_PROMPT_ID,
    )


def select_l2_reviewer(provider: str | None):
    """Resolve a provider name to the matching reviewer callable.

    ``None`` and ``""`` map to the Anthropic API reviewer for backward
    compatibility with code that does not opt into the new selector.
    """

    name = normalize_l2_reviewer_provider(provider)
    if name is None or name == "anthropic":
        return anthropic_l2_reviewer
    if name == "claude-cli":
        return claude_cli_l2_reviewer
    raise RuntimeError("unknown L2 reviewer provider")


def _build_review_payload(
    *,
    response_text: str,
    response_model: str,
    usage: dict[str, int],
    existing_facts: list[Fact],
    new_facts: list[Fact] | None,
    prompt_id: str,
) -> dict[str, object]:
    parsed = parse_l2_review_response(response_text)
    _validate_fact_notes(parsed["fact_notes"], existing_facts, new_facts)
    return {
        "action": parsed["action"],
        "reason": parsed["reason"],
        "violations": parsed["violations"],
        "fact_notes": parsed["fact_notes"],
        "comment_body": render_l2_review_comment(
            action=str(parsed["action"]),
            reason=str(parsed["reason"]),
            fact_notes=parsed["fact_notes"],
            violations=parsed["violations"],
            model=response_model,
            usage=usage,
        ),
        "usage": usage,
        "model": response_model,
        "prompt_id": prompt_id,
        "prompt_version": L2_REVIEW_PROMPT_VERSION,
    }


def build_l2_review_context(
    pr: PRProposal,
    conventions: ConventionSet,
    existing_facts: list[Fact],
    new_facts: list[Fact] | None,
) -> dict[str, Any]:
    fact_delta = assert_governance_pr_body(pr.body, allow_legacy=True) if pr.body else None
    return {
        "pull_request": {
            "title": pr.title,
            "branch": pr.branch,
            "labels": list(pr.labels),
            "files_changed": list(pr.files_changed),
            "fact_delta": fact_delta,
        },
        "review_policy": {
            "approve_only_if": [
                "the change is high confidence",
                "the impact is local rather than global",
                "the diff is non-destructive",
                "the facts are atomic, scoped correctly, and consistent with conventions",
            ],
            "escalate_if": [
                "the change is global in impact",
                "the diff is destructive or rewrites existing strong facts",
                "there is uncertainty, contradiction risk, or governance ambiguity",
            ],
            "reject_if": [
                "the proposed facts are malformed",
                "the topic or phrasing violates conventions",
                "the change appears hallucinated, unsupported, or over-broad",
            ],
        },
        "conventions": {
            "topics": sorted(conventions.topics),
            "topic_descriptions": dict(sorted(conventions.topic_descriptions.items())),
            "phrasing_rules": list(conventions.phrasing_rules),
            "entity_vocabulary": dict(sorted(conventions.entity_vocabulary.items())),
            "project_conventions": list(conventions.project_conventions),
        },
        "existing_facts": [_fact_payload(fact) for fact in existing_facts],
        "proposed_facts": [_fact_payload(fact) for fact in list(new_facts or [])],
    }


def parse_l2_review_response(text: str) -> dict[str, object]:
    payload = _extract_json_payload(text)
    if not isinstance(payload, dict):
        raise RuntimeError("L2 reviewer response must be a JSON object")

    action = payload.get("action")
    if not isinstance(action, str) or action not in _VALID_ACTIONS:
        raise RuntimeError("L2 reviewer response is missing a valid `action`")
    reason = payload.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise RuntimeError("L2 reviewer response is missing `reason`")

    violations_payload = payload.get("violations", [])
    if not isinstance(violations_payload, list):
        raise RuntimeError("L2 reviewer response field `violations` must be a list")
    violations = [str(item).strip() for item in violations_payload if str(item).strip()]

    fact_notes_payload = payload.get("fact_notes", [])
    if not isinstance(fact_notes_payload, list):
        raise RuntimeError("L2 reviewer response field `fact_notes` must be a list")
    fact_notes: list[dict[str, str]] = []
    for index, note in enumerate(fact_notes_payload):
        if not isinstance(note, dict):
            raise RuntimeError(f"L2 reviewer fact note {index} must be an object")
        note_text = note.get("note")
        if not isinstance(note_text, str) or not note_text.strip():
            raise RuntimeError(f"L2 reviewer fact note {index} is missing `note`")
        normalized: dict[str, str] = {"note": note_text.strip()}
        fact_id = note.get("fact_id")
        if fact_id is not None:
            if not isinstance(fact_id, str) or not fact_id.strip():
                raise RuntimeError(f"L2 reviewer fact note {index} has invalid `fact_id`")
            normalized["fact_id"] = fact_id.strip()
        summary = note.get("summary")
        if summary is not None:
            if not isinstance(summary, str) or not summary.strip():
                raise RuntimeError(f"L2 reviewer fact note {index} has invalid `summary`")
            normalized["summary"] = summary.strip()
        fact_notes.append(normalized)

    return {
        "action": action,
        "reason": reason.strip(),
        "violations": violations,
        "fact_notes": fact_notes,
    }


def render_l2_review_comment(
    *,
    action: str,
    reason: str,
    fact_notes: list[dict[str, str]],
    violations: list[str],
    model: str,
    usage: dict[str, int] | None = None,
) -> str:
    lines = [
        REVIEW_COMMENT_MARKER,
        "## L2 Review",
        "",
        f"- Action: `{action}`",
        f"- Model: `{model}`",
        f"- Reason: {reason}",
    ]
    if usage:
        input_tokens = usage.get("input_tokens")
        output_tokens = usage.get("output_tokens")
        total_tokens = usage.get("total_tokens")
        token_summary = ", ".join(
            segment
            for segment in (
                f"in {input_tokens}" if input_tokens is not None else "",
                f"out {output_tokens}" if output_tokens is not None else "",
                f"total {total_tokens}" if total_tokens is not None else "",
            )
            if segment
        )
        if token_summary:
            lines.append(f"- Tokens: {token_summary}")
    if violations:
        lines.extend(["", "### Violations", ""])
        lines.extend(f"- {item}" for item in violations)
    if fact_notes:
        lines.extend(["", "### Fact Notes", ""])
        for note in fact_notes:
            fact_ref = f"`{note['fact_id']}`" if "fact_id" in note else "proposed fact"
            summary = f" — {note['summary']}" if "summary" in note else ""
            lines.append(f"- {fact_ref}{summary}: {note['note']}")
    return "\n".join(lines)


def _review_system_prompt() -> str:
    return (
        "You are the L2 reviewer for git-native governed memory pull requests. "
        "Return only JSON. Decide whether the change should be approve, reject, or escalate. "
        "Be strict about convention violations, destructive/global changes, and hallucination risk."
    )


def _review_user_prompt(context: dict[str, Any]) -> str:
    schema = {
        "action": "approve | reject | escalate",
        "reason": "short single-sentence explanation",
        "violations": ["list of concrete problems, if any"],
        "fact_notes": [
            {
                "fact_id": "optional fact id",
                "summary": "optional short summary",
                "note": "brief per-fact review note",
            }
        ],
    }
    return (
        "Review the governance PR context below and return ONLY JSON with this shape:\n"
        f"{json.dumps(schema, indent=2, sort_keys=True)}\n\n"
        "Context:\n"
        f"{json.dumps(context, indent=2, sort_keys=True)}"
    )


def _extract_json_payload(text: str) -> Any:
    stripped = text.strip()
    match = _JSON_FENCE_RE.search(stripped)
    if match is not None:
        stripped = match.group("payload").strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise RuntimeError("L2 reviewer response did not contain JSON") from None
        return json.loads(stripped[start : end + 1])


def _fact_payload(fact: Fact) -> dict[str, Any]:
    return {
        "fact_id": fact.fact_id,
        "text": fact.text,
        "topic": fact.topic,
        "scope": fact.scope.value,
        "encoding_strength": fact.encoding_strength,
        "source_type": fact.source_type.value,
        "confidence": fact.confidence,
        "superseded_by": fact.superseded_by,
        "supersedes": fact.supersedes,
        "conflicts_with": list(fact.conflicts_with),
    }


def _validate_fact_notes(
    fact_notes: list[dict[str, str]],
    existing_facts: list[Fact],
    new_facts: list[Fact] | None,
) -> None:
    valid_ids = {fact.fact_id for fact in existing_facts}
    valid_ids.update(fact.fact_id for fact in list(new_facts or []))
    for note in fact_notes:
        fact_id = note.get("fact_id")
        if fact_id is not None and fact_id not in valid_ids:
            raise RuntimeError(f"L2 reviewer fact note references unknown fact_id: {fact_id}")
