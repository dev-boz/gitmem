from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from umx.aip import main as aip_main, mem_main
from umx.cli import main
from umx.config import default_config, save_config
from umx.dream.gates import read_dream_state
from umx.git_ops import GitCommitResult
from umx.memory import add_fact
from umx.models import (
    ConsolidationStatus,
    Fact,
    MemoryType,
    Scope,
    SourceType,
    Verification,
)
from umx.scope import config_path
from umx.sessions import read_session, session_path, write_session


def _make_fact(text: str, topic: str = "general", **overrides) -> Fact:
    values = {
        "fact_id": overrides.pop("fact_id", "01TESTFACT0000000000000800"),
        "text": text,
        "scope": Scope.PROJECT,
        "topic": topic,
        "encoding_strength": 3,
        "memory_type": MemoryType.EXPLICIT_SEMANTIC,
        "verification": Verification.SELF_REPORTED,
        "source_type": SourceType.TOOL_OUTPUT,
        "source_tool": "codex",
        "source_session": "sess-cli-001",
        "consolidation_status": ConsolidationStatus.FRAGILE,
    }
    values.update(overrides)
    return Fact(**values)


def test_cli_init_actions_writes_templates(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["init-actions", "--dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert len(payload) == 3
    assert (tmp_path / ".github" / "workflows" / "approval-gate.yml").exists()
    assert (tmp_path / ".github" / "workflows" / "l1-dream.yml").exists()
    assert (tmp_path / ".github" / "workflows" / "l2-review.yml").exists()


def test_cli_archive_sessions_moves_old_sessions(project_dir: Path, project_repo: Path) -> None:
    write_session(
        project_repo,
        {
            "session_id": "2020-01-15-archivecli",
            "started": "2020-01-15T00:00:00Z",
            "tool": "codex",
        },
        [{"ts": "2020-01-15T00:00:01Z", "role": "assistant", "content": "postgres runs on 5433 in dev"}],
        auto_commit=False,
    )

    runner = CliRunner()
    result = runner.invoke(main, ["archive-sessions", "--cwd", str(project_dir)])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["archived_sessions"] == 1
    assert not session_path(project_repo, "2020-01-15-archivecli").exists()
    assert (project_repo / "sessions" / "2020" / "01" / "2020-01-archive.jsonl.gz").exists()


def test_cli_rebuild_index_with_embeddings_writes_cache(
    monkeypatch, project_dir: Path, project_repo: Path
) -> None:
    fact = _make_fact(
        "deploy to staging via make ship",
        topic="deploy",
        fact_id="01TESTFACT0000000000000801",
    )
    add_fact(project_repo, fact, auto_commit=False)

    monkeypatch.setattr("umx.search_semantic.embed_fact", lambda fact, config=None: [1.0, 0.0, 0.0])

    runner = CliRunner()
    result = runner.invoke(main, ["rebuild-index", "--cwd", str(project_dir), "--embeddings"])
    assert result.exit_code == 0, result.output

    cache_path = project_repo / ".umx.json"
    payload = json.loads(cache_path.read_text())
    assert fact.fact_id in payload["facts"]
    assert payload["facts"][fact.fact_id]["embedding"] == [1.0, 0.0, 0.0]


def test_cli_dream_force_lint_overrides_never_interval(
    project_dir: Path,
    project_repo: Path,
) -> None:
    cfg = default_config()
    cfg.dream.lint_interval = "never"
    save_config(config_path(), cfg)

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["dream", "--cwd", str(project_dir), "--force", "--force-lint"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["lint"] == {"ran": True, "reason": "forced"}
    cache_payload = json.loads((project_repo / ".umx.json").read_text())
    assert "last_lint" in cache_payload["dream"]


def test_cli_collect_stdin_writes_session(project_dir: Path, project_repo: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["collect", "--cwd", str(project_dir), "--tool", "aider"],
        input="postgres runs on 5433 in dev\n",
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["tool"] == "aider"
    assert payload["input_format"] == "text"
    assert payload["events_imported"] == 1
    assert payload["session_count"] == 1

    session_file = session_path(project_repo, payload["umx_session_id"])
    records = read_session(session_file)
    assert records[0]["_meta"]["tool"] == "aider"
    assert records[0]["_meta"]["source"] == "manual-collect"
    assert records[1]["role"] == "assistant"
    assert records[1]["content"] == "postgres runs on 5433 in dev"
    assert read_dream_state(project_repo)["session_count"] == 1


def test_cli_collect_jsonl_preserves_meta_and_events(
    tmp_path: Path,
    project_dir: Path,
    project_repo: Path,
) -> None:
    input_file = tmp_path / "collect.jsonl"
    input_file.write_text(
        '\n'.join(
            [
                '{"_meta":{"session_id":"2026-04-15-collectjsonl","model":"haiku"}}',
                '{"role":"user","content":"check deploy docs"}',
                '{"content":"deploy uses the shared cluster","ts":"2026-04-15T23:55:00Z"}',
            ]
        )
        + '\n'
    )

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "collect",
            "--cwd",
            str(project_dir),
            "--tool",
            "cursor",
            "--file",
            str(input_file),
            "--format",
            "jsonl",
            "--role",
            "tool_result",
            "--meta",
            "source_label=fixture",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["tool"] == "cursor"
    assert payload["input_format"] == "jsonl"
    assert payload["events_imported"] == 2
    assert payload["source_file"] == str(input_file)

    records = read_session(session_path(project_repo, payload["umx_session_id"]))
    assert records[0]["_meta"]["session_id"] == "2026-04-15-collectjsonl"
    assert records[0]["_meta"]["tool"] == "cursor"
    assert records[0]["_meta"]["model"] == "haiku"
    assert records[0]["_meta"]["source"] == "manual-collect-jsonl"
    assert records[0]["_meta"]["source_label"] == "fixture"
    assert records[1]["role"] == "user"
    assert records[2]["role"] == "tool_result"
    assert records[2]["content"] == "deploy uses the shared cluster"
    assert records[2]["ts"] == "2026-04-15T23:55:00Z"


def test_cli_collect_dry_run_defaults_to_workspace_events_and_is_idempotent(
    project_dir: Path,
    project_repo: Path,
) -> None:
    workspace = project_dir / "workspace"
    workspace.mkdir()
    events_file = workspace / "events.jsonl"
    events_file.write_text(
        '\n'.join(
            [
                '{"_meta":{"session_id":"2026-04-15-workspacecollect"}}',
                '{"role":"assistant","content":"shared deploy steps live in docs/deploy.md"}',
            ]
        )
        + '\n'
    )

    runner = CliRunner()
    dry_run = runner.invoke(
        main,
        ["collect", "--cwd", str(project_dir), "--tool", "qodo", "--dry-run"],
    )

    assert dry_run.exit_code == 0, dry_run.output
    dry_payload = json.loads(dry_run.output)
    assert dry_payload["dry_run"] is True
    assert dry_payload["tool"] == "qodo"
    assert dry_payload["input_format"] == "jsonl"
    assert dry_payload["source_file"] == str(events_file)
    assert dry_payload["new_session"] is True
    assert not session_path(project_repo, "2026-04-15-workspacecollect").exists()

    first = runner.invoke(main, ["collect", "--cwd", str(project_dir), "--tool", "qodo"])
    assert first.exit_code == 0, first.output
    first_payload = json.loads(first.output)
    assert first_payload["new_session"] is True
    assert first_payload["session_count"] == 1

    second = runner.invoke(main, ["collect", "--cwd", str(project_dir), "--tool", "qodo"])
    assert second.exit_code == 0, second.output
    second_payload = json.loads(second.output)
    assert second_payload["new_session"] is False
    assert second_payload["session_count"] is None
    assert read_dream_state(project_repo)["session_count"] == 1


def test_cli_collect_rejects_invalid_meta(project_dir: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["collect", "--cwd", str(project_dir), "--tool", "cursor", "--meta", "broken"],
        input="context\n",
    )

    assert result.exit_code != 0
    assert "Invalid --meta value" in result.output


def test_cli_collect_rejects_invalid_jsonl(project_dir: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["collect", "--cwd", str(project_dir), "--tool", "cursor", "--format", "jsonl"],
        input='{"role":"assistant","content":"ok"}\n{"broken"\n',
    )

    assert result.exit_code != 0
    assert "Collected JSONL is invalid on line 2" in result.output


def test_cli_collect_errors_without_incrementing_when_commit_fails(
    monkeypatch,
    project_dir: Path,
    project_repo: Path,
) -> None:
    monkeypatch.setattr(
        "umx.collect.git_add_and_commit",
        lambda *args, **kwargs: GitCommitResult.failed_result(stderr="gpg failed"),
    )

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["collect", "--cwd", str(project_dir), "--tool", "cursor"],
        input="manual transcript\n",
    )

    assert result.exit_code != 0
    assert "Failed to commit collected session." in result.output
    assert read_dream_state(project_repo)["session_count"] == 0


def test_cli_collect_preserves_noop_commit_behavior(
    monkeypatch,
    project_dir: Path,
    project_repo: Path,
) -> None:
    monkeypatch.setattr("umx.collect.git_path_exists_at_ref", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        "umx.collect.git_add_and_commit",
        lambda *args, **kwargs: GitCommitResult.noop_result(),
    )

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["collect", "--cwd", str(project_dir), "--tool", "cursor"],
        input="manual transcript\n",
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["new_session"] is False
    assert payload["session_count"] is None
    assert read_dream_state(project_repo)["session_count"] == 0


def test_aip_mem_namespace_forwards_status(project_dir: Path, project_repo: Path) -> None:
    runner = CliRunner()

    result = runner.invoke(aip_main, ["mem", "status", "--cwd", str(project_dir)])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert "fact_count" in payload
    assert "facts" in payload
    assert "tombstones" in payload
    assert "session_count" in payload
    assert "pending_session_count" in payload
    assert "processing" in payload


def test_aip_mem_entrypoint_forwards_view(project_dir: Path, project_repo: Path) -> None:
    fact = _make_fact(
        "document staging rollout steps",
        topic="deploy",
        fact_id="01TESTFACT0000000000000802",
    )
    add_fact(project_repo, fact, auto_commit=False)

    runner = CliRunner()
    result = runner.invoke(mem_main, ["view", "--cwd", str(project_dir), "--fact", fact.fact_id])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["fact_id"] == fact.fact_id


def test_aip_mem_entrypoint_forwards_collect(project_dir: Path, project_repo: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        aip_main,
        ["mem", "collect", "--cwd", str(project_dir), "--tool", "jules"],
        input="prod deploy uses the release checklist\n",
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["tool"] == "jules"
    records = read_session(session_path(project_repo, payload["umx_session_id"]))
    assert records[0]["_meta"]["tool"] == "jules"
