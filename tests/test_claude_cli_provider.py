from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from umx.config import default_config
from umx.conventions import ConventionSet
from umx.dream.l2_review import (
    L2_CLAUDE_CLI_PROMPT_ID,
    L2_REVIEW_PROMPT_VERSION,
    REVIEW_COMMENT_MARKER,
    anthropic_l2_reviewer,
    claude_cli_l2_reviewer,
    nvidia_l2_reviewer,
    normalize_l2_reviewer_provider,
    select_l2_reviewer,
)
from umx.dream.providers import ProviderUnavailableError
from umx.dream.pipeline import DreamPipeline
from umx.governance import PRProposal
from umx.models import (
    ConsolidationStatus,
    Fact,
    MemoryType,
    Scope,
    SourceType,
    Verification,
)
from umx.providers import claude_cli as claude_cli_provider


FIXTURES_ROOT = Path(__file__).parent / "fixtures" / "l2_review"


def _make_fact(fact_id: str = "01TESTL2FIXTURE0000000001") -> Fact:
    return Fact(
        fact_id=fact_id,
        text="fixture fact for Claude CLI review",
        scope=Scope.PROJECT,
        topic="general",
        encoding_strength=3,
        memory_type=MemoryType.EXPLICIT_SEMANTIC,
        verification=Verification.SELF_REPORTED,
        source_type=SourceType.LLM_INFERENCE,
        confidence=0.9,
        source_tool="session-extract",
        source_session="sess-fixture",
        consolidation_status=ConsolidationStatus.STABLE,
    )


def _approve_payload() -> dict[str, object]:
    return {
        "action": "approve",
        "reason": "Clear, high-confidence local fact update with no destructive change.",
        "violations": [],
        "fact_notes": [
            {
                "fact_id": "01TESTL2FIXTURE0000000001",
                "summary": "fixture fact for Claude CLI review",
                "note": "Specific, local in impact, matches the diff.",
            }
        ],
    }


def _write_fake_claude(
    tmp_path: Path,
    *,
    stdout_payload: dict[str, object] | None = None,
    exit_code: int = 0,
    stderr: str = "",
) -> Path:
    """Write a synthetic `claude` shim that emits a deterministic JSON result."""

    payload = stdout_payload if stdout_payload is not None else {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": json.dumps(_approve_payload()),
        "model": "claude-opus-4-7",
        "usage": {"input_tokens": 321, "output_tokens": 87},
    }
    serialized = json.dumps(payload)
    script_path = tmp_path / "fake-claude"
    # Echo args/env into a sidecar so tests can assert flag wiring.
    sidecar = tmp_path / "fake-claude-args.json"
    script = (
        "#!/usr/bin/env python3\n"
        "import json, sys, os, pathlib\n"
        f"sidecar = pathlib.Path({str(sidecar)!r})\n"
        "stdin_data = sys.stdin.read()\n"
        "sidecar.write_text(json.dumps({'argv': sys.argv[1:], 'stdin': stdin_data}))\n"
        f"sys.stdout.write({serialized!r})\n"
        f"sys.stderr.write({stderr!r})\n"
        f"sys.exit({exit_code})\n"
    )
    script_path.write_text(script)
    script_path.chmod(script_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return script_path


def test_claude_cli_available_uses_env_override(tmp_path: Path, monkeypatch) -> None:
    fake = _write_fake_claude(tmp_path)
    monkeypatch.setenv(claude_cli_provider.CLAUDE_CLI_BINARY_ENV, str(fake))
    assert claude_cli_provider.claude_cli_available() is True


def test_claude_cli_available_returns_false_when_missing(monkeypatch) -> None:
    monkeypatch.setenv(claude_cli_provider.CLAUDE_CLI_BINARY_ENV, "/nonexistent/claude-binary")
    monkeypatch.setenv("PATH", "")
    assert claude_cli_provider.claude_cli_available() is False


def test_send_claude_cli_message_parses_success(tmp_path: Path, monkeypatch) -> None:
    fake = _write_fake_claude(tmp_path)
    monkeypatch.setenv(claude_cli_provider.CLAUDE_CLI_BINARY_ENV, str(fake))

    response = claude_cli_provider.send_claude_cli_message(
        model="claude-opus-4-7",
        system="be brief",
        prompt="hello",
    )

    assert response.model == "claude-opus-4-7"
    assert response.usage == {
        "input_tokens": 321,
        "output_tokens": 87,
        "total_tokens": 408,
    }
    assert json.loads(response.text)["action"] == "approve"

    sidecar = json.loads((tmp_path / "fake-claude-args.json").read_text())
    argv = sidecar["argv"]
    assert "--print" in argv
    assert "--output-format" in argv and argv[argv.index("--output-format") + 1] == "json"
    assert "--model" in argv and argv[argv.index("--model") + 1] == "claude-opus-4-7"
    assert "--append-system-prompt" in argv
    assert argv[argv.index("--append-system-prompt") + 1] == "be brief"
    assert "--no-session-persistence" in argv
    assert "--disallowedTools" in argv
    # Long prompt rides on stdin, not argv, so we never blow argv length limits.
    assert sidecar["stdin"] == "hello"


def test_send_claude_cli_message_propagates_cli_failure(tmp_path: Path, monkeypatch) -> None:
    fake = _write_fake_claude(tmp_path, exit_code=2, stderr="auth required\n")
    monkeypatch.setenv(claude_cli_provider.CLAUDE_CLI_BINARY_ENV, str(fake))

    with pytest.raises(RuntimeError, match="auth required"):
        claude_cli_provider.send_claude_cli_message(
            model="claude-opus-4-7",
            system="be brief",
            prompt="hello",
        )


def test_send_claude_cli_message_rejects_is_error_payload(tmp_path: Path, monkeypatch) -> None:
    fake = _write_fake_claude(
        tmp_path,
        stdout_payload={
            "type": "result",
            "subtype": "error",
            "is_error": True,
            "result": "model overloaded",
            "model": "claude-opus-4-7",
        },
    )
    monkeypatch.setenv(claude_cli_provider.CLAUDE_CLI_BINARY_ENV, str(fake))

    with pytest.raises(RuntimeError, match="model overloaded"):
        claude_cli_provider.send_claude_cli_message(
            model="claude-opus-4-7",
            system="be brief",
            prompt="hello",
        )


def test_send_claude_cli_message_rejects_non_json_output(tmp_path: Path, monkeypatch) -> None:
    fake = tmp_path / "fake-claude-text"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "sys.stdout.write('not json at all')\n"
        "sys.exit(0)\n"
    )
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv(claude_cli_provider.CLAUDE_CLI_BINARY_ENV, str(fake))

    with pytest.raises(RuntimeError, match="valid JSON"):
        claude_cli_provider.send_claude_cli_message(
            model="claude-opus-4-7",
            system="be brief",
            prompt="hello",
        )


def test_send_claude_cli_message_errors_when_binary_missing(monkeypatch) -> None:
    monkeypatch.setenv(claude_cli_provider.CLAUDE_CLI_BINARY_ENV, "/nonexistent/claude-binary")
    monkeypatch.setenv("PATH", "")

    with pytest.raises(RuntimeError, match="Claude Code CLI not found"):
        claude_cli_provider.send_claude_cli_message(
            model="claude-opus-4-7",
            system="be brief",
            prompt="hello",
        )


def test_select_l2_reviewer_resolves_known_aliases() -> None:
    assert select_l2_reviewer(None) is anthropic_l2_reviewer
    assert select_l2_reviewer("") is anthropic_l2_reviewer
    assert select_l2_reviewer("anthropic") is anthropic_l2_reviewer
    assert select_l2_reviewer("ANTHROPIC-API") is anthropic_l2_reviewer
    assert select_l2_reviewer("claude-cli") is claude_cli_l2_reviewer
    assert select_l2_reviewer("Claude-Code") is claude_cli_l2_reviewer
    assert select_l2_reviewer("oauth") is claude_cli_l2_reviewer
    assert select_l2_reviewer("nvidia") is nvidia_l2_reviewer


def test_select_l2_reviewer_rejects_unknown_provider() -> None:
    with pytest.raises(RuntimeError, match="unknown L2 reviewer provider"):
        select_l2_reviewer("openai")


def test_claude_cli_l2_reviewer_returns_structured_payload(tmp_path: Path, monkeypatch) -> None:
    fake = _write_fake_claude(tmp_path)
    monkeypatch.setenv(claude_cli_provider.CLAUDE_CLI_BINARY_ENV, str(fake))

    pr = PRProposal(
        title="[dream/l2] cli fixture review",
        body=(FIXTURES_ROOT / "pr_body.md").read_text(),
        branch="dream/l1/cli-fixture-review",
        labels=["confidence:high", "impact:local", "type: extraction"],
        files_changed=["facts/topics/general.md"],
    )

    result = claude_cli_l2_reviewer(
        pr,
        ConventionSet(topics={"general"}),
        [],
        [_make_fact()],
        default_config(),
    )

    assert result["action"] == "approve"
    assert result["model"] == "claude-opus-4-7"
    assert result["prompt_id"] == L2_CLAUDE_CLI_PROMPT_ID
    assert result["prompt_version"] == L2_REVIEW_PROMPT_VERSION
    assert result["usage"] == {
        "input_tokens": 321,
        "output_tokens": 87,
        "total_tokens": 408,
    }
    comment_body = str(result["comment_body"])
    assert REVIEW_COMMENT_MARKER in comment_body
    assert "- Model: `claude-opus-4-7`" in comment_body


def test_claude_cli_l2_reviewer_raises_when_cli_unavailable(monkeypatch) -> None:
    monkeypatch.setenv(claude_cli_provider.CLAUDE_CLI_BINARY_ENV, "/nonexistent/claude-binary")
    monkeypatch.setenv("PATH", "")

    pr = PRProposal(
        title="[dream/l2] missing cli",
        body=(FIXTURES_ROOT / "pr_body.md").read_text(),
        branch="dream/l1/missing-cli",
        labels=["confidence:high", "impact:local", "type: extraction"],
        files_changed=["facts/topics/general.md"],
    )

    with pytest.raises(ProviderUnavailableError, match="Claude Code CLI is not available"):
        claude_cli_l2_reviewer(
            pr,
            ConventionSet(topics={"general"}),
            [],
            [_make_fact()],
            default_config(),
        )


def test_cli_eval_l2_review_routes_through_claude_cli(monkeypatch) -> None:
    """`umx eval l2-review --provider claude-cli` must use the claude_cli reviewer."""

    from click.testing import CliRunner

    from umx.cli import main
    from umx.dream import l2_eval as l2_eval_module

    seen: dict[str, object] = {}

    def fake_run(cases_path, config, *, case_id=None, min_pass_rate=0.85, reviewer=None):
        seen["reviewer"] = reviewer
        return {
            "status": "ok",
            "prompt_id": L2_CLAUDE_CLI_PROMPT_ID,
            "prompt_version": L2_REVIEW_PROMPT_VERSION,
            "model": "claude-opus-4-7",
            "total": 0,
            "passed": 0,
            "pass_rate": 1.0,
            "min_pass_rate": min_pass_rate,
            "meets_case_count": True,
            "required_case_count": 20,
            "missing_buckets": [],
            "metadata_errors": [],
            "bucket_summary": {},
            "usage_totals": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
            "failures": [],
            "results": [],
        }

    monkeypatch.setattr(l2_eval_module, "run_l2_review_eval", fake_run)

    result = CliRunner().invoke(
        main,
        ["eval", "l2-review", "--provider", "claude-cli", "--cases", str(FIXTURES_ROOT)],
    )

    assert result.exit_code == 0, result.output
    assert seen["reviewer"] is claude_cli_l2_reviewer


def test_cli_eval_l2_review_rejects_unknown_provider() -> None:
    from click.testing import CliRunner

    from umx.cli import main

    result = CliRunner().invoke(
        main,
        ["eval", "l2-review", "--provider", "openai", "--cases", str(FIXTURES_ROOT)],
    )

    assert result.exit_code != 0
    assert "unknown L2 reviewer provider" in result.output


def test_normalize_l2_reviewer_provider_aliases() -> None:
    assert normalize_l2_reviewer_provider(None) is None
    assert normalize_l2_reviewer_provider("anthropic") == "anthropic"
    assert normalize_l2_reviewer_provider("oauth") == "claude-cli"
    assert normalize_l2_reviewer_provider("nvidia-api") == "nvidia"


def test_cli_dream_l2_routes_provider_override(monkeypatch, project_dir) -> None:
    from click.testing import CliRunner

    from umx.cli import main

    seen: dict[str, object] = {}

    def fake_review(
        self,
        pr_number,
        *,
        force_merge=False,
        force_reason=None,
        expected_head_sha=None,
        provider=None,
    ):
        seen["pr_number"] = pr_number
        seen["provider"] = provider
        seen["force_merge"] = force_merge
        seen["force_reason"] = force_reason
        seen["expected_head_sha"] = expected_head_sha
        return {"status": "ok"}

    monkeypatch.setattr(DreamPipeline, "review_pr", fake_review)

    result = CliRunner().invoke(
        main,
        ["dream", "--cwd", str(project_dir), "--tier", "l2", "--pr", "7", "--provider", "claude-cli"],
    )

    assert result.exit_code == 0, result.output
    assert seen["pr_number"] == 7
    assert seen["provider"] == "claude-cli"


def test_cli_dream_l2_rejects_unknown_provider(project_dir) -> None:
    from click.testing import CliRunner

    from umx.cli import main

    result = CliRunner().invoke(
        main,
        ["dream", "--cwd", str(project_dir), "--tier", "l2", "--pr", "7", "--provider", "openai"],
    )

    assert result.exit_code != 0
    assert "unknown L2 reviewer provider" in result.output
