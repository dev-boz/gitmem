from __future__ import annotations

import json
from pathlib import Path
import subprocess
from unittest.mock import Mock, patch

from click.testing import CliRunner

from tests.secret_literals import AWS_ACCESS_KEY_ID
from umx.config import default_config, load_config, save_config
from umx.cli import main
from umx.github_ops import GitHubError
from umx.scope import config_path, init_local_umx, project_memory_dir
from umx.sessions import write_session
from umx.telemetry import telemetry_queue_path


def _seed_bare_remote(
    tmp_path: Path,
    name: str,
    relative_path: str,
    content: str,
    *,
    message: str,
) -> Path:
    remote = tmp_path / f"{name}.git"
    worktree = tmp_path / f"{name}-seed"
    subprocess.run(["git", "init", "--bare", str(remote)], capture_output=True, check=True)
    subprocess.run(["git", "init", "-b", "main", str(worktree)], capture_output=True, check=True)
    subprocess.run(
        ["git", "-C", str(worktree), "config", "user.name", "Test User"],
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(worktree), "config", "user.email", "test@example.com"],
        capture_output=True,
        check=True,
    )
    target = worktree / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    subprocess.run(["git", "-C", str(worktree), "add", "-A"], capture_output=True, check=True)
    subprocess.run(
        ["git", "-C", str(worktree), "commit", "-m", message],
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(worktree), "remote", "add", "origin", str(remote)],
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(worktree), "push", "-u", "origin", "main"],
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "--git-dir", str(remote), "symbolic-ref", "HEAD", "refs/heads/main"],
        capture_output=True,
        check=True,
    )
    return remote


def test_cli_init_and_status(project_dir, umx_home) -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["init", "--org", "memory-org"])
    assert result.exit_code == 0

    result = runner.invoke(main, ["init-project", "--cwd", str(project_dir), "--slug", "demo"])
    assert result.exit_code == 0

    status = runner.invoke(main, ["status", "--cwd", str(project_dir)])
    assert status.exit_code == 0
    payload = json.loads(status.output)
    assert payload["slug"] == "demo"
    assert "fact_count" in payload
    assert "tombstones" in payload
    assert "pending_session_count" in payload
    assert "ok" in payload
    assert "flags" in payload
    assert "guidance" in payload


def test_cli_status_suggests_creating_conventions_when_missing(project_dir, project_repo, umx_home) -> None:
    (project_repo / "CONVENTIONS.md").unlink()

    runner = CliRunner()
    status = runner.invoke(main, ["status", "--cwd", str(project_dir)])

    assert status.exit_code == 0, status.output
    payload = json.loads(status.output)
    assert payload["conventions_present"] is False
    assert any("CONVENTIONS.md missing" in flag for flag in payload["flags"])
    conventions_guidance = next(item for item in payload["guidance"] if item["metric"] == "conventions")
    assert "Dream review" in conventions_guidance["why_it_matters"]
    assert any("Create CONVENTIONS.md" in action for action in conventions_guidance["recommended_actions"])


def test_cli_meta_topic_includes_uncertainty_and_gap_status(project_dir, project_repo, umx_home) -> None:
    (project_repo / "meta" / "manifest.json").write_text(
        json.dumps(
            {
                "topics": {"devenv": {"fact_count": 2, "avg_strength": 4.0}},
                "uncertainty_hotspots": [{"topic": "devenv", "fragile_ratio": 1.0, "reason": "all facts fragile"}],
                "knowledge_gaps": [{"topic": "backups", "gap_signals": 2, "reason": "no facts extracted yet"}],
            },
            sort_keys=True,
        )
        + "\n"
    )

    runner = CliRunner()
    topic_result = runner.invoke(main, ["meta", "--cwd", str(project_dir), "--topic", "devenv"])
    gap_result = runner.invoke(main, ["meta", "--cwd", str(project_dir), "--topic", "backups"])

    assert topic_result.exit_code == 0, topic_result.output
    assert gap_result.exit_code == 0, gap_result.output
    topic_payload = json.loads(topic_result.output)
    gap_payload = json.loads(gap_result.output)
    assert topic_payload["topic"] == "devenv"
    assert topic_payload["fact_count"] == 2
    assert topic_payload["uncertainty_hotspot"] is True
    assert topic_payload["uncertainty"]["fragile_ratio"] == 1.0
    assert topic_payload["knowledge_gap"] is False
    assert gap_payload["topic"] == "backups"
    assert gap_payload["knowledge_gap"] is True
    assert gap_payload["gap"]["gap_signals"] == 2
    assert gap_payload["uncertainty_hotspot"] is False


def test_cli_gaps_can_emit_gap_signal(project_dir, project_repo, umx_home) -> None:
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "gaps",
            "--cwd",
            str(project_dir),
            "--query",
            "fastboot timeout config",
            "--resolution-context",
            "agent read scripts/deploy.sh and found timeout=30 hardcoded",
            "--proposed-fact",
            "fastboot timeout is 30s by default on veyron",
            "--session",
            "session-gap-001",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["type"] == "gap"
    assert payload["query"] == "fastboot timeout config"
    assert payload["session"] == "session-gap-001"
    assert payload["ts"].endswith("Z")
    stored = (project_repo / "meta" / "gaps.jsonl").read_text().strip().splitlines()
    assert len(stored) == 1
    assert json.loads(stored[0]) == payload


def test_cli_inject_outputs_memory_block(project_dir, project_repo, user_repo) -> None:
    from umx.memory import add_fact
    from umx.models import (
        ConsolidationStatus,
        Fact,
        MemoryType,
        Scope,
        SourceType,
        Verification,
    )

    add_fact(
        project_repo,
        Fact(
            fact_id="01TESTFACT0000000000000200",
            text="postgres runs on 5433 in dev",
            scope=Scope.PROJECT,
            topic="devenv",
            encoding_strength=4,
            memory_type=MemoryType.EXPLICIT_SEMANTIC,
            verification=Verification.CORROBORATED,
            source_type=SourceType.GROUND_TRUTH_CODE,
            source_tool="codex",
            source_session="2026-04-11",
            consolidation_status=ConsolidationStatus.STABLE,
        ),
    )

    runner = CliRunner()
    result = runner.invoke(main, ["inject", "--cwd", str(project_dir), "--prompt", "postgres"])
    assert result.exit_code == 0
    assert "# UMX Memory" in result.output
    assert "postgres runs on 5433 in dev" in result.output


def test_cli_view_fact_and_health(project_dir, project_repo) -> None:
    from umx.memory import add_fact
    from umx.models import (
        ConsolidationStatus,
        Fact,
        MemoryType,
        Scope,
        SourceType,
        Verification,
    )

    fact = Fact(
        fact_id="01TESTFACT0000000000000201",
        text="deploys run through staging first",
        scope=Scope.PROJECT,
        topic="release",
        encoding_strength=4,
        memory_type=MemoryType.EXPLICIT_SEMANTIC,
        verification=Verification.CORROBORATED,
        source_type=SourceType.GROUND_TRUTH_CODE,
        source_tool="codex",
        source_session="2026-04-11",
        consolidation_status=ConsolidationStatus.STABLE,
    )
    add_fact(project_repo, fact)

    runner = CliRunner()
    view_result = runner.invoke(main, ["view", "--cwd", str(project_dir), "--fact", fact.fact_id])
    assert view_result.exit_code == 0
    payload = json.loads(view_result.output)
    assert payload["fact_id"] == fact.fact_id

    health_result = runner.invoke(main, ["health", "--cwd", str(project_dir)])
    assert health_result.exit_code == 0
    health = json.loads(health_result.output)
    assert "metrics" in health
    assert "flags" in health
    assert "guidance" in health


def test_cli_health_governance_json(project_dir, project_repo) -> None:
    payload = {
        "repo": str(project_memory_dir(project_dir)),
        "mode": "remote",
        "governed": True,
        "ok": True,
        "flags": [],
        "errors": [],
        "summary": {
            "open_governance_prs": 1,
            "reviewer_queue_depth": 0,
            "human_review_queue_depth": 0,
            "stale_branch_count": 0,
            "label_drift_count": 0,
            "stale_branch_days": 7,
        },
        "open_prs": [],
        "stale_branches": [],
        "last_l2_review": None,
        "label_drift": [],
    }
    runner = CliRunner()
    with patch("umx.cli.build_governance_health_payload", return_value=payload) as mock_build:
        result = runner.invoke(main, ["health", "--cwd", str(project_dir), "--governance"])

    assert result.exit_code == 0
    assert json.loads(result.output) == payload
    mock_build.assert_called_once()


def test_cli_health_governance_human(project_dir, project_repo) -> None:
    payload = {
        "repo": str(project_memory_dir(project_dir)),
        "mode": "remote",
        "governed": True,
        "ok": False,
        "flags": ["1 governance PR(s) awaiting L2 review"],
        "errors": [],
        "summary": {
            "open_governance_prs": 1,
            "reviewer_queue_depth": 1,
            "human_review_queue_depth": 0,
            "stale_branch_count": 0,
            "label_drift_count": 0,
            "stale_branch_days": 7,
        },
        "open_prs": [],
        "stale_branches": [],
        "last_l2_review": None,
        "label_drift": [],
    }
    runner = CliRunner()
    with (
        patch("umx.cli.build_governance_health_payload", return_value=payload),
        patch(
            "umx.cli.render_governance_health_human",
            return_value="Governance health: warn\nOpen governance PRs: 1",
        ) as mock_render,
    ):
        result = runner.invoke(
            main,
            ["health", "--cwd", str(project_dir), "--governance", "--format", "human"],
        )

    assert result.exit_code == 0
    assert result.output.strip() == "Governance health: warn\nOpen governance PRs: 1"
    mock_render.assert_called_once_with(payload)


def test_cli_health_rejects_human_format_without_governance(project_dir, project_repo) -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["health", "--cwd", str(project_dir), "--format", "human"])

    assert result.exit_code != 0
    assert "--format is only supported with --governance" in result.output


def test_cli_view_launches_viewer_by_default(project_dir, project_repo) -> None:
    runner = CliRunner()
    server = Mock()
    server.serve_forever.side_effect = KeyboardInterrupt
    with patch("umx.cli.start_viewer", return_value=("http://127.0.0.1:43123", server)) as mock_run:
        result = runner.invoke(main, ["view", "--cwd", str(project_dir)])

    assert result.exit_code == 0
    assert result.output.strip() == "http://127.0.0.1:43123"
    mock_run.assert_called_once_with(project_dir)
    server.serve_forever.assert_called_once_with()


def test_cli_status_remains_json_when_hot_tier_warns(project_dir, project_repo, umx_home) -> None:
    cfg = default_config()
    cfg.memory.hot_tier_max_tokens = 1
    save_config(config_path(), cfg)
    (project_repo / "MEMORY.md").write_text("# Memory\n\n" + ("hot token\n" * 40))

    runner = CliRunner()
    result = runner.invoke(main, ["status", "--cwd", str(project_dir)])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["metrics"]["hot_tier_utilisation"]["status"] == "warn"
    assert payload["ok"] is False
    assert any("Hot tier utilisation" in flag for flag in payload["flags"])
    assert any(item["metric"] == "hot_tier_utilisation" for item in payload["advice"])
    assert payload["guidance"] == payload["advice"]
    assert any("prompt budget" in item["why_it_matters"] for item in payload["guidance"])


def test_cli_status_counts_session_files_and_preserves_pending_counter(project_dir, project_repo) -> None:
    write_session(
        project_repo,
        {"session_id": "2026-04-16-status", "tool": "cursor"},
        [{"role": "assistant", "content": "health surfaces stay aligned"}],
        auto_commit=False,
    )
    (project_repo / "meta" / "dream-state.json").write_text(
        json.dumps({"last_dream": None, "session_count": 7}, sort_keys=True) + "\n"
    )

    runner = CliRunner()
    result = runner.invoke(main, ["status", "--cwd", str(project_dir)])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["session_count"] == 1
    assert payload["pending_session_count"] == 7


def test_cli_status_surfaces_git_signing_config(project_dir, umx_home) -> None:
    cfg = default_config()
    cfg.git.sign_commits = True
    cfg.git.require_signed_commits = True
    save_config(config_path(), cfg)

    runner = CliRunner()
    result = runner.invoke(main, ["status", "--cwd", str(project_dir)])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["git"] == {
        "enabled": True,
        "sign_commits": True,
        "require_signed_commits": True,
    }


def test_cli_config_set_redaction_patterns_roundtrips_to_sessions_config(umx_home) -> None:
    runner = CliRunner()

    result = runner.invoke(
        main,
        ["config", "set", "redaction.patterns", r"customer-\d+"],
    )

    assert result.exit_code == 0
    cfg = load_config(config_path())
    assert cfg.sessions.redaction_patterns == [r"customer-\d+"]

    result = runner.invoke(
        main,
        ["config", "set", "redaction.patterns", '["customer-\\\\d+", "ticket-[A-Z]+"]'],
    )

    assert result.exit_code == 0
    assert result.output.strip() == "redaction.patterns"
    cfg = load_config(config_path())
    assert cfg.sessions.redaction_patterns == [r"customer-\d+", r"ticket-[A-Z]+"]


def test_cli_config_set_redaction_patterns_rejects_invalid_regex(umx_home) -> None:
    runner = CliRunner()

    result = runner.invoke(main, ["config", "set", "redaction.patterns", "["])

    assert result.exit_code != 0
    assert "invalid redaction pattern" in result.output

    result = runner.invoke(main, ["config", "set", "redaction.patterns", r"(a+)+$"])

    assert result.exit_code != 0
    assert "unsafe regex constructs" in result.output


def test_cli_config_set_telemetry_enabled_roundtrips_to_config(umx_home, monkeypatch) -> None:
    runner = CliRunner()
    called = {"count": 0}

    def _unexpected(*args, **kwargs):
        called["count"] += 1
        raise AssertionError("opt-in command should not upload telemetry")

    monkeypatch.setattr("umx.telemetry.urlopen", _unexpected)

    result = runner.invoke(main, ["config", "set", "telemetry.enabled", "true"])

    assert result.exit_code == 0, result.output
    assert result.output.strip() == "telemetry.enabled"
    cfg = load_config(config_path())
    assert cfg.telemetry.enabled is True
    assert called["count"] == 0
    assert not telemetry_queue_path().exists()


def test_cli_init_preserves_existing_telemetry_config(umx_home) -> None:
    cfg = default_config()
    cfg.telemetry.enabled = True
    save_config(config_path(), cfg)

    runner = CliRunner()
    result = runner.invoke(main, ["init"])

    assert result.exit_code == 0, result.output
    reloaded = load_config(config_path())
    assert reloaded.telemetry.enabled is True


def test_cli_init_project_remote_bootstraps_main(project_dir, umx_home, tmp_path) -> None:
    cfg = default_config()
    cfg.org = "memory-org"
    cfg.dream.mode = "remote"
    save_config(config_path(), cfg)

    remote = tmp_path / "memory.git"
    subprocess.run(["git", "init", "--bare", str(remote)], capture_output=True, check=True)

    runner = CliRunner()
    with patch("umx.github_ops.gh_available", return_value=True), patch(
        "umx.github_ops.ensure_repo",
        return_value=str(remote),
    ):
        result = runner.invoke(main, ["init-project", "--cwd", str(project_dir), "--slug", "demo"])

    assert result.exit_code == 0, result.output
    assert "governance-protection: deferred" in result.output
    assert "direct pushes to main" in result.output

    repo = project_memory_dir(project_dir)
    verify = subprocess.run(
        ["git", "--git-dir", str(remote), "rev-parse", "--verify", "main"],
        capture_output=True,
        text=True,
    )
    assert verify.returncode == 0

    tree = subprocess.run(
        ["git", "--git-dir", str(remote), "ls-tree", "-r", "--name-only", "main"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert ".github/workflows/approval-gate.yml" in tree.stdout
    assert ".github/workflows/l1-dream.yml" in tree.stdout
    assert ".github/workflows/l2-review.yml" in tree.stdout

    log = subprocess.run(
        ["git", "-C", str(repo), "log", "--oneline"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "bootstrap remote project memory" in log.stdout


def test_cli_init_remote_bootstraps_user_workflows(umx_home, user_repo, tmp_path) -> None:
    remote = tmp_path / "user-memory.git"
    subprocess.run(["git", "init", "--bare", str(remote)], capture_output=True, check=True)

    runner = CliRunner()
    with patch("umx.github_ops.gh_available", return_value=True), patch(
        "umx.github_ops.ensure_repo",
        return_value=str(remote),
    ):
        result = runner.invoke(main, ["init", "--org", "memory-org", "--mode", "remote"])

    assert result.exit_code == 0, result.output
    assert "governance-protection: deferred" in result.output
    assert "direct pushes to main" in result.output
    assert (user_repo / ".github" / "workflows" / "approval-gate.yml").exists()
    assert (user_repo / ".github" / "workflows" / "l1-dream.yml").exists()
    assert (user_repo / ".github" / "workflows" / "l2-review.yml").exists()

    tree = subprocess.run(
        ["git", "--git-dir", str(remote), "ls-tree", "-r", "--name-only", "main"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert ".github/workflows/approval-gate.yml" in tree.stdout
    assert ".github/workflows/l1-dream.yml" in tree.stdout
    assert ".github/workflows/l2-review.yml" in tree.stdout


def test_cli_init_hybrid_attaches_existing_user_remote_repo(umx_home, tmp_path, monkeypatch) -> None:
    remote = _seed_bare_remote(
        tmp_path,
        "existing-user-memory",
        "facts/topics/seed.md",
        "# seed\n\n## Facts\n- existing user remote memory\n",
        message="seed remote user memory",
    )
    fresh_home = tmp_path / "fresh-home"
    monkeypatch.setenv("UMX_HOME", str(fresh_home))

    runner = CliRunner()
    with patch("umx.github_ops.gh_available", return_value=True), patch(
        "umx.github_ops.ensure_repo",
        return_value=str(remote),
    ), patch("umx.cli.assert_push_safe"), patch("umx.git_ops.assert_signed_commit_range"):
        result = runner.invoke(main, ["init", "--org", "memory-org", "--mode", "hybrid"])

    assert result.exit_code == 0, result.output
    fresh_user_repo = fresh_home / "user"
    assert (fresh_user_repo / "facts" / "topics" / "seed.md").exists()
    remote_url = subprocess.run(
        ["git", "-C", str(fresh_user_repo), "remote", "get-url", "origin"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert remote_url.stdout.strip() == str(remote)


def test_cli_init_project_attaches_existing_project_remote_repo(project_dir, umx_home, tmp_path, monkeypatch) -> None:
    remote = _seed_bare_remote(
        tmp_path,
        "existing-project-memory",
        "facts/topics/seed.md",
        "# seed\n\n## Facts\n- existing project remote memory\n",
        message="seed remote project memory",
    )
    fresh_home = tmp_path / "fresh-home"
    monkeypatch.setenv("UMX_HOME", str(fresh_home))
    init_local_umx()
    cfg = default_config()
    cfg.org = "memory-org"
    cfg.dream.mode = "hybrid"
    save_config(config_path(), cfg)

    runner = CliRunner()
    with patch("umx.github_ops.gh_available", return_value=True), patch(
        "umx.github_ops.ensure_repo",
        return_value=str(remote),
    ), patch("umx.cli.assert_push_safe"), patch("umx.git_ops.assert_signed_commit_range"):
        result = runner.invoke(main, ["init-project", "--cwd", str(project_dir), "--slug", "project"])

    assert result.exit_code == 0, result.output
    attached_repo = fresh_home / "projects" / "project"
    assert (attached_repo / "facts" / "topics" / "seed.md").exists()
    remote_url = subprocess.run(
        ["git", "-C", str(attached_repo), "remote", "get-url", "origin"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert remote_url.stdout.strip() == str(remote)


def test_cli_eval_inject_exits_nonzero_on_gate_failure(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "umx.inject_eval.run_inject_eval",
        lambda *args, **kwargs: {
            "status": "error",
            "total": 3,
            "passed": 2,
            "pass_rate": 2 / 3,
            "min_pass_rate": 1.0,
            "disclosure_slack_pct": 0.2,
            "failures": [{"case": "broken-case"}],
            "results": [],
        },
    )

    result = CliRunner().invoke(main, ["eval", "inject", "--cases", str(tmp_path)])

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["status"] == "error"


def test_cli_eval_inject_forwards_slack_override(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def _run(cases_path, config, *, case_id=None, min_pass_rate=1.0, disclosure_slack_pct=None):
        captured["cases_path"] = cases_path
        captured["case_id"] = case_id
        captured["min_pass_rate"] = min_pass_rate
        captured["disclosure_slack_pct"] = disclosure_slack_pct
        return {
            "status": "ok",
            "total": 1,
            "passed": 1,
            "pass_rate": 1.0,
            "min_pass_rate": min_pass_rate,
            "disclosure_slack_pct": disclosure_slack_pct,
            "failures": [],
            "results": [],
        }

    monkeypatch.setattr("umx.inject_eval.run_inject_eval", _run)

    result = CliRunner().invoke(
        main,
        [
            "eval",
            "inject",
            "--cases",
            str(tmp_path),
            "--case",
            "postgres-ranking",
            "--min-pass-rate",
            "0.9",
            "--disclosure-slack-pct",
            "0.15",
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured == {
        "cases_path": tmp_path,
        "case_id": "postgres-ranking",
        "min_pass_rate": 0.9,
        "disclosure_slack_pct": 0.15,
    }


def test_cli_dream_rejects_blank_force_reason(umx_home) -> None:
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["dream", "--tier", "l2", "--pr", "1", "--force", "--force-reason", "   "],
    )

    assert result.exit_code != 0
    assert "--force-reason cannot be blank" in result.output


def test_cli_doctor_surfaces_git_signing_config(umx_home) -> None:
    cfg = default_config()
    cfg.git.require_signed_commits = True
    save_config(config_path(), cfg)

    runner = CliRunner()
    result = runner.invoke(main, ["doctor"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["git_signing"] == {
        "enabled": True,
        "sign_commits": False,
        "require_signed_commits": True,
    }
    assert "guidance" in payload["health"]


def test_cli_init_project_surfaces_git_init_commit_failure(project_dir, umx_home) -> None:
    from umx.git_ops import GitCommitError, GitCommitResult

    runner = CliRunner()
    failure = GitCommitError(
        "git init commit failed: gpg failed to sign the data",
        GitCommitResult.failed_result(returncode=128, stderr="gpg failed to sign the data"),
    )

    with patch("umx.cli.init_project_memory", side_effect=failure):
        result = runner.invoke(main, ["init-project", "--cwd", str(project_dir)])

    assert result.exit_code != 0
    assert "git init commit failed: gpg failed to sign the data" in result.output


def test_cli_init_project_prompts_for_disambiguated_slug_on_collision(project_dir, umx_home) -> None:
    ((umx_home / "projects") / project_dir.name).mkdir(parents=True)

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["init-project", "--cwd", str(project_dir)],
        input="project-2\n",
    )

    assert result.exit_code == 0, result.output
    assert "Enter project slug [project-2]" in result.output
    assert (project_dir / ".umx-project").read_text().strip() == "project-2"
    assert ((umx_home / "projects") / "project-2").exists()


def test_cli_init_project_yes_auto_appends_on_collision(project_dir, umx_home) -> None:
    projects_dir = umx_home / "projects"
    (projects_dir / project_dir.name).mkdir(parents=True)
    (projects_dir / f"{project_dir.name}-2").mkdir(parents=True)

    runner = CliRunner()
    result = runner.invoke(main, ["init-project", "--cwd", str(project_dir), "--yes"])

    assert result.exit_code == 0, result.output
    assert "Enter project slug" not in result.output
    assert (project_dir / ".umx-project").read_text().strip() == "project-3"
    assert (projects_dir / "project-3").exists()


def test_cli_init_project_slug_override_bypasses_collision_prompt(project_dir, umx_home) -> None:
    ((umx_home / "projects") / project_dir.name).mkdir(parents=True)

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["init-project", "--cwd", str(project_dir), "--slug", "project-custom"],
    )

    assert result.exit_code == 0, result.output
    assert "Enter project slug" not in result.output
    assert (project_dir / ".umx-project").read_text().strip() == "project-custom"
    assert ((umx_home / "projects") / "project-custom").exists()


def test_cli_init_project_rejects_unsafe_slug_override(project_dir) -> None:
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["init-project", "--cwd", str(project_dir), "--slug", "../../escape"],
    )

    assert result.exit_code != 0
    assert "Invalid project slug" in result.output


def test_cli_init_project_reprompts_after_invalid_slug_input(project_dir, umx_home) -> None:
    ((umx_home / "projects") / project_dir.name).mkdir(parents=True)

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["init-project", "--cwd", str(project_dir)],
        input="../../escape\nproject-2\n",
    )

    assert result.exit_code == 0, result.output
    assert "Invalid project slug" in result.output
    assert (project_dir / ".umx-project").read_text().strip() == "project-2"


def test_cli_setup_remote_blocks_unsafe_bootstrap(project_dir, project_repo, tmp_path) -> None:
    from umx.git_ops import git_add_and_commit

    cfg = default_config()
    cfg.org = "memory-org"
    save_config(config_path(), cfg)

    fact_path = project_repo / "facts" / "topics" / "deploy.md"
    fact_path.parent.mkdir(parents=True, exist_ok=True)
    fact_path.write_text(f"# deploy\n\n## Facts\n- aws key {AWS_ACCESS_KEY_ID}\n")
    git_add_and_commit(project_repo, message="unsafe bootstrap")

    remote = tmp_path / "unsafe-bootstrap.git"
    subprocess.run(["git", "init", "--bare", str(remote)], capture_output=True, check=True)

    runner = CliRunner()
    with patch("umx.github_ops.gh_available", return_value=True), patch(
        "umx.github_ops.ensure_repo",
        return_value=str(remote),
    ):
        result = runner.invoke(main, ["setup-remote", "--cwd", str(project_dir), "--mode", "remote"])

    assert result.exit_code != 0
    assert "push safety blocked" in result.output


def test_cli_setup_remote_remote_reports_deferred_governance_protection(
    project_dir,
    project_repo,
    tmp_path,
) -> None:
    cfg = default_config()
    cfg.org = "memory-org"
    save_config(config_path(), cfg)

    remote = tmp_path / "setup-remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], capture_output=True, check=True)

    runner = CliRunner()
    with patch("umx.github_ops.gh_available", return_value=True), patch(
        "umx.github_ops.ensure_repo",
        return_value=str(remote),
    ):
        result = runner.invoke(main, ["setup-remote", "--cwd", str(project_dir), "--mode", "remote"])

    assert result.exit_code == 0, result.output
    assert "governance-protection: deferred" in result.output
    assert "direct pushes to main" in result.output

    tree = subprocess.run(
        ["git", "--git-dir", str(remote), "ls-tree", "-r", "--name-only", "main"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert ".github/workflows/approval-gate.yml" in tree.stdout
    assert ".github/workflows/l1-dream.yml" in tree.stdout
    assert ".github/workflows/l2-review.yml" in tree.stdout


def test_cli_setup_remote_attaches_existing_remote_repo_for_fresh_local_project_repo(
    project_dir,
    project_repo,
    tmp_path,
) -> None:
    cfg = default_config()
    cfg.org = "memory-org"
    cfg.dream.mode = "hybrid"
    save_config(config_path(), cfg)

    remote = _seed_bare_remote(
        tmp_path,
        "setup-remote-existing-project",
        "facts/topics/seed.md",
        "# seed\n\n## Facts\n- existing remote setup memory\n",
        message="seed setup remote project memory",
    )

    runner = CliRunner()
    with patch("umx.github_ops.gh_available", return_value=True), patch(
        "umx.github_ops.ensure_repo",
        return_value=str(remote),
    ), patch("umx.cli.assert_push_safe"), patch("umx.git_ops.assert_signed_commit_range"):
        result = runner.invoke(main, ["setup-remote", "--cwd", str(project_dir), "--mode", "hybrid"])

    assert result.exit_code == 0, result.output
    assert (project_repo / "facts" / "topics" / "seed.md").exists()
    remote_url = subprocess.run(
        ["git", "-C", str(project_repo), "remote", "get-url", "origin"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert remote_url.stdout.strip() == str(remote)


def test_cli_setup_remote_rejects_unsigned_bootstrap_history(project_dir, project_repo, tmp_path) -> None:
    cfg = default_config()
    cfg.org = "memory-org"
    cfg.git.require_signed_commits = True
    save_config(config_path(), cfg)

    memory_path = project_repo / "meta" / "MEMORY.md"
    memory_path.parent.mkdir(parents=True, exist_ok=True)
    memory_path.write_text("# Memory\n\nbootstrap history is currently unsigned\n")
    subprocess.run(["git", "-C", str(project_repo), "add", str(memory_path)], capture_output=True, check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(project_repo),
            "-c",
            "user.name=Test User",
            "-c",
            "user.email=test@example.com",
            "commit",
            "--no-gpg-sign",
            "-m",
            "unsigned bootstrap history",
        ],
        capture_output=True,
        check=True,
    )

    remote = tmp_path / "unsigned-bootstrap.git"
    subprocess.run(["git", "init", "--bare", str(remote)], capture_output=True, check=True)

    runner = CliRunner()
    with patch("umx.github_ops.gh_available", return_value=True), patch(
        "umx.github_ops.ensure_repo",
        return_value=str(remote),
    ), patch("umx.cli._commit_repo", return_value=False):
        result = runner.invoke(main, ["setup-remote", "--cwd", str(project_dir), "--mode", "remote"])

    assert result.exit_code != 0
    assert "unsigned or invalid commit signatures" in result.output


def test_cli_setup_remote_surfaces_github_retry_next_steps(project_dir) -> None:
    cfg = default_config()
    cfg.org = "memory-org"
    save_config(config_path(), cfg)

    runner = CliRunner()
    with patch("umx.github_ops.gh_available", return_value=True), patch(
        "umx.github_ops.ensure_repo",
        side_effect=GitHubError(
            "gh repo create memory-org/demo: 503 service unavailable (after 3 attempts). "
            "Next steps: verify network connectivity, gh auth, and GitHub rate limits, then retry."
        ),
    ):
        result = runner.invoke(main, ["setup-remote", "--cwd", str(project_dir), "--mode", "remote"])

    assert result.exit_code != 0
    assert "Next steps: verify network connectivity, gh auth, and GitHub rate limits, then retry." in result.output


def test_cli_setup_remote_surfaces_auth_status_retry_next_steps(project_dir) -> None:
    cfg = default_config()
    cfg.org = "memory-org"
    save_config(config_path(), cfg)

    runner = CliRunner()
    with patch(
        "umx.github_ops.gh_available",
        side_effect=GitHubError(
            "gh auth status: 503 service unavailable (after 3 attempts). "
            "Next steps: verify network connectivity, gh auth, and GitHub rate limits, then retry."
        ),
    ):
        result = runner.invoke(main, ["setup-remote", "--cwd", str(project_dir), "--mode", "remote"])

    assert result.exit_code != 0
    assert "Next steps: verify network connectivity, gh auth, and GitHub rate limits, then retry." in result.output


def test_cli_sync_returns_nonzero_when_push_fails(project_dir, project_repo, tmp_path, monkeypatch) -> None:
    from umx.git_ops import git_add_and_commit, git_push

    remote = tmp_path / "sync-push-fails.git"
    subprocess.run(["git", "init", "--bare", str(remote)], capture_output=True, check=True)
    subprocess.run(
        ["git", "-C", str(project_repo), "remote", "add", "origin", str(remote)],
        capture_output=True,
        check=True,
    )
    git_add_and_commit(project_repo, message="sync push fail baseline")
    git_push(project_repo)

    cfg = default_config()
    cfg.org = "memory-org"
    cfg.dream.mode = "remote"
    save_config(config_path(), cfg)

    sessions_dir = project_repo / "sessions" / "2026" / "01"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    (sessions_dir / "2026-01-15-sync-fail.jsonl").write_text('{"_meta":{"session_id":"2026-01-15-sync-fail"}}\n')

    monkeypatch.setattr("umx.git_ops.git_pull_rebase", lambda *args, **kwargs: True)
    monkeypatch.setattr("umx.git_ops.git_push", lambda *args, **kwargs: False)

    result = CliRunner().invoke(main, ["sync", "--cwd", str(project_dir)])

    assert result.exit_code != 0
    assert "push failed" in result.output


def test_cli_sync_rejects_unsigned_commits_when_required(
    project_dir,
    project_repo,
    tmp_path,
    monkeypatch,
) -> None:
    from umx.git_ops import git_add_and_commit, git_push

    remote = tmp_path / "sync-signed-history.git"
    subprocess.run(["git", "init", "--bare", str(remote)], capture_output=True, check=True)
    subprocess.run(
        ["git", "-C", str(project_repo), "remote", "add", "origin", str(remote)],
        capture_output=True,
        check=True,
    )
    git_add_and_commit(project_repo, message="sync signed-history baseline")
    git_push(project_repo)

    cfg = default_config()
    cfg.org = "memory-org"
    cfg.dream.mode = "remote"
    cfg.git.require_signed_commits = True
    save_config(config_path(), cfg)

    sessions_dir = project_repo / "sessions" / "2026" / "01"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    session_path = sessions_dir / "2026-01-15-signed-history.jsonl"
    session_path.write_text('{"_meta":{"session_id":"2026-01-15-signed-history"}}\n')
    subprocess.run(["git", "-C", str(project_repo), "add", str(session_path)], capture_output=True, check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(project_repo),
            "-c",
            "user.name=Test User",
            "-c",
            "user.email=test@example.com",
            "commit",
            "--no-gpg-sign",
            "-m",
            "unsigned sync commit",
        ],
        capture_output=True,
        check=True,
    )
    monkeypatch.setattr("umx.git_ops.git_pull_rebase", lambda *args, **kwargs: True)

    result = CliRunner().invoke(main, ["sync", "--cwd", str(project_dir)])

    assert result.exit_code != 0
    assert "unsigned or invalid commit signatures" in result.output
