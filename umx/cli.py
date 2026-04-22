from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from time import perf_counter
from typing import Any, Callable

import click

from umx.budget import estimate_tokens
from umx.calibration import build_calibration_advice
from umx.collect import collect_session, parse_meta_pairs, summarize_collected_session
from umx.config import default_config, load_config, save_config
from umx.doctor import run_doctor
from umx.dream.gates import read_dream_state
from umx.dream.pipeline import DreamPipeline
from umx.dream.processing import summarize_processing_log
from umx.inject import emit_gap_signal, inject_for_tool
from umx.manifest import manifest_path, topic_status
from umx.metrics import compute_metrics, health_flags
from umx.memory import find_fact_by_id, load_all_facts, remove_fact, replace_fact
from umx.models import ConsolidationStatus, Scope, SourceType, Verification
from umx.push_safety import PushSafetyError, assert_push_safe
from umx.governance import (
    direct_fact_write_error,
    filter_governed_fact_paths,
    filter_non_operational_sync_paths,
    is_governed_mode,
    session_sync_error,
)
from umx.governance_health import (
    build_governance_health_payload,
    render_governance_health_human,
)
from umx.search import advance_session_state
from umx.redaction import RedactionError, validate_redaction_patterns
from umx.search_semantic import embedding_rebuild_message, embeddings_available
from umx.scope import (
    config_path,
    discover_project_slug,
    find_project_root,
    get_umx_home,
    init_local_umx,
    init_project_memory,
    next_available_project_slug,
    project_slug_in_use,
    project_memory_dir,
    user_memory_dir,
    validate_project_slug,
)
from umx.viewer.server import start as start_viewer
from umx.search import query_index, refresh_index, search_sessions
from umx.sessions import generate_session_id
from umx.supersession import walk_history
from umx.tasks import open_tasks
from umx.tombstones import forget_fact, forget_topic, load_tombstones
from umx.status import build_status_payload
from umx.telemetry import record_cli_invocation


def _cfg():
    return load_config(config_path())


def _parse_bool_value(value: str) -> bool:
    lowered = value.strip().lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    raise click.ClickException("expected a boolean value: true/false")


def _parse_redaction_patterns_value(value: str) -> list[str]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        patterns = [value]
    else:
        if isinstance(parsed, str):
            patterns = [parsed]
        elif isinstance(parsed, list):
            patterns = parsed
        else:
            raise click.ClickException(
                "redaction.patterns must be a regex string or a JSON array of regex strings"
            )
    try:
        return validate_redaction_patterns(patterns)
    except RedactionError as exc:
        raise click.ClickException(str(exc)) from exc


_SESSION_SWEEP_SUBCOMMANDS = frozenset({"collect", "dream", "sync", "capture"})


def _telemetry_command_path(ctx: click.Context) -> str:
    parts: list[str] = []
    current: click.Context | None = ctx
    while current is not None and current.parent is not None:
        if current.info_name:
            parts.append(current.info_name)
        current = current.parent
    return "/".join(reversed(parts)) or (ctx.info_name or "unknown")


def _telemetry_cwd(ctx: click.Context) -> Path | None:
    cwd = ctx.params.get("cwd")
    return cwd if isinstance(cwd, Path) else None


def _emit_cli_telemetry(
    ctx: click.Context,
    *,
    success: bool,
    duration_ms: int,
    error_kind: str | None = None,
) -> None:
    if _telemetry_command_path(ctx) == "config/set" and ctx.params.get("key") == "telemetry.enabled":
        return
    record_cli_invocation(
        _telemetry_command_path(ctx),
        cwd=_telemetry_cwd(ctx),
        success=success,
        duration_ms=duration_ms,
        error_kind=error_kind,
        config=ctx.meta.get("telemetry_config") or _cfg(),
    )


class TelemetryCommand(click.Command):
    def invoke(self, ctx: click.Context) -> Any:
        ctx.meta["telemetry_config"] = _cfg()
        started = perf_counter()
        try:
            result = super().invoke(ctx)
        except click.exceptions.Exit as exc:
            _emit_cli_telemetry(
                ctx,
                success=exc.exit_code == 0,
                duration_ms=int((perf_counter() - started) * 1000),
                error_kind=None if exc.exit_code == 0 else "Exit",
            )
            raise
        except click.Abort:
            _emit_cli_telemetry(
                ctx,
                success=False,
                duration_ms=int((perf_counter() - started) * 1000),
                error_kind="Abort",
            )
            raise
        except click.ClickException as exc:
            _emit_cli_telemetry(
                ctx,
                success=False,
                duration_ms=int((perf_counter() - started) * 1000),
                error_kind=type(exc).__name__,
            )
            raise
        except Exception as exc:
            _emit_cli_telemetry(
                ctx,
                success=False,
                duration_ms=int((perf_counter() - started) * 1000),
                error_kind=type(exc).__name__,
            )
            raise
        _emit_cli_telemetry(
            ctx,
            success=True,
            duration_ms=int((perf_counter() - started) * 1000),
        )
        return result


class TelemetryGroup(click.Group):
    command_class = TelemetryCommand
    group_class = type


def _require_direct_fact_write_allowed(operation: str) -> None:
    cfg = _cfg()
    if is_governed_mode(cfg.dream.mode):
        raise click.ClickException(
            direct_fact_write_error(cfg.dream.mode, operation)
        )


def _commit_repo(repo: Path, message: str, *, allow_governed: bool = False) -> bool:
    from umx.git_ops import changed_paths, git_add_and_commit, git_commit_failure_message

    cfg = _cfg()
    if not allow_governed:
        if is_governed_mode(cfg.dream.mode):
            governed_paths = filter_governed_fact_paths(changed_paths(repo), repo)
            if governed_paths:
                raise click.ClickException(
                    direct_fact_write_error(cfg.dream.mode, "this command")
                )

    result = git_add_and_commit(repo, message=message, config=cfg)
    if result.failed:
        raise click.ClickException(
            git_commit_failure_message(result, context="commit failed")
        )
    return result.committed


def _bootstrap_remote_repo(
    repo: Path,
    message: str,
    *,
    project_root: Path | None = None,
    config=None,
) -> None:
    from umx.git_ops import GitSignedHistoryError, assert_signed_commit_range, git_push

    _commit_repo(repo, message, allow_governed=True)
    try:
        assert_push_safe(
            repo,
            project_root=project_root,
            base_ref=None,
            branch="main",
            config=config or _cfg(),
            include_bridge=project_root is not None,
        )
    except PushSafetyError as exc:
        raise click.ClickException(str(exc)) from exc
    try:
        assert_signed_commit_range(
            repo,
            base_ref=None,
            head_ref="HEAD",
            config=config or _cfg(),
            operation="remote bootstrap",
        )
    except GitSignedHistoryError as exc:
        raise click.ClickException(str(exc)) from exc
    if not git_push(repo, branch="main", set_upstream=True):
        raise click.ClickException("push failed")


def _capture_batch_workers(target_count: int) -> int:
    return max(1, min(target_count, 4))


def _prepare_capture_batch(
    targets: list[Any],
    prepare_one: Callable[[Any], dict[str, Any]],
) -> list[dict[str, Any]]:
    if len(targets) < 2:
        return [prepare_one(target) for target in targets]
    with ThreadPoolExecutor(max_workers=_capture_batch_workers(len(targets))) as executor:
        return list(executor.map(prepare_one, targets))


def _persist_prepared_capture_batch(
    repo: Path,
    prepared: list[dict[str, Any]],
    *,
    config,
) -> list[dict[str, Any]]:
    from umx.sessions import write_session

    results: list[dict[str, Any]] = []
    for item in prepared:
        session_file = write_session(
            repo,
            meta=dict(item["meta"]),
            events=item["events"],
            config=config,
            auto_commit=False,
        )
        result = dict(item["result"])
        result["session_file"] = str(session_file)
        results.append(result)
    return results


def _emit_governance_protection_status(mode: str) -> None:
    if mode != "remote":
        return
    from umx.github_ops import plan_governance_protection

    plan = plan_governance_protection(mode)
    required_checks = ", ".join(plan.required_status_checks) or "<none>"
    status = "ready" if plan.eligible else "deferred"
    click.echo(
        "governance-protection: "
        f"{status} (branch {plan.target_branch}; require PR={str(plan.require_pull_request).lower()}; "
        f"status checks: {required_checks}; merge label: {plan.governance_merge_label})"
    )
    if plan.deferred_reason:
        click.echo(f"governance-protection reason: {plan.deferred_reason}")


@click.group(cls=TelemetryGroup)
@click.pass_context
def main(ctx: click.Context) -> None:
    """UMX CLI."""
    if ctx.invoked_subcommand not in _SESSION_SWEEP_SUBCOMMANDS:
        return
    try:
        from umx.git_ops import safety_sweep
        from umx.scope import get_umx_home

        home = get_umx_home()
        if home.exists():
            user = home / "user"
            if user.exists():
                safety_sweep(user)
            projects = home / "projects"
            if projects.exists():
                for child in projects.iterdir():
                    if child.is_dir():
                        safety_sweep(child)
    except Exception:  # noqa: BLE001
        pass


@main.command("init")
@click.option("--org", default=None)
@click.option(
    "--mode",
    "init_mode",
    type=click.Choice(["local", "remote", "hybrid"]),
    default="local",
)
def init_cmd(org: str | None, init_mode: str) -> None:
    from umx.git_ops import GitCommitError

    config = _cfg()
    config.org = org
    config.dream.mode = init_mode
    try:
        home = init_local_umx(org)
    except GitCommitError as exc:
        raise click.ClickException(str(exc)) from exc
    save_config(config_path(), config)

    if init_mode in ("remote", "hybrid") and org:
        from umx.github_ops import GitHubError, deploy_workflows, gh_available, ensure_repo, set_remote

        try:
            if not gh_available():
                click.echo("warning: gh CLI not available; skipping remote setup")
            else:
                url = ensure_repo(org, "umx-user", private=True)
                set_remote(home / "user", url)
                if init_mode == "remote":
                    deploy_workflows(home / "user")
                _bootstrap_remote_repo(
                    home / "user",
                    "umx: bootstrap remote user memory",
                    config=config,
                )
                _emit_governance_protection_status(init_mode)
                click.echo(f"remote: {url}")
        except GitHubError as exc:
            raise click.ClickException(str(exc)) from exc

    click.echo(str(home))


@main.command("init-project")
@click.option("--cwd", type=click.Path(path_type=Path), default=Path.cwd)
@click.option("--slug", default=None)
@click.option("--yes", is_flag=True, default=False)
def init_project_cmd(cwd: Path, slug: str | None, yes: bool) -> None:
    from umx.git_ops import GitCommitError

    root = find_project_root(cwd)
    project_slug = slug or discover_project_slug(root)
    while True:
        try:
            project_slug = validate_project_slug(project_slug)
        except ValueError as exc:
            if slug is not None or yes:
                raise click.ClickException(str(exc)) from exc
            click.echo(str(exc))
            project_slug = click.prompt("Enter project slug")
            continue
        if not project_slug_in_use(project_slug, root):
            break
        if slug:
            raise click.ClickException(
                f"project slug '{project_slug}' is already in use; choose a different --slug"
            )
        if yes:
            project_slug = next_available_project_slug(project_slug, root)
            break
        click.echo(f"project slug '{project_slug}' is already in use")
        project_slug = click.prompt(
            "Enter project slug",
            default=next_available_project_slug(project_slug, root),
        )
    try:
        repo = init_project_memory(root, write_marker=True, slug=project_slug)
    except GitCommitError as exc:
        raise click.ClickException(str(exc)) from exc

    cfg = _cfg()
    if cfg.dream.mode in ("remote", "hybrid") and cfg.org:
        from umx.github_ops import (
            GitHubError,
            gh_available,
            ensure_repo,
            set_remote,
            deploy_workflows,
        )

        try:
            if not gh_available():
                click.echo("warning: gh CLI not available; skipping remote setup")
            else:
                project_slug = discover_project_slug(root)
                url = ensure_repo(cfg.org, project_slug, private=True)
                set_remote(repo, url)
                if cfg.dream.mode == "remote":
                    deploy_workflows(repo)
                _bootstrap_remote_repo(
                    repo,
                    "umx: bootstrap remote project memory",
                    project_root=root,
                    config=cfg,
                )
                _emit_governance_protection_status(cfg.dream.mode)
                click.echo(f"remote: {url}")
        except GitHubError as exc:
            raise click.ClickException(str(exc)) from exc

    click.echo(str(repo))


@main.command("inject")
@click.option("--cwd", type=click.Path(path_type=Path), default=Path.cwd)
@click.option("--tool", default=None)
@click.option("--prompt", default=None)
@click.option("--command", "command_text", default=None)
@click.option("--session", "session_id", default=None)
@click.option("--context-window", "context_window_tokens", type=int, default=None)
@click.option("--expand-fact", "expanded_facts", multiple=True)
@click.option("--file", "files", multiple=True)
@click.option("--max-tokens", type=int, default=4000)
def inject_cmd(
    cwd: Path,
    tool: str | None,
    prompt: str | None,
    command_text: str | None,
    session_id: str | None,
    context_window_tokens: int | None,
    expanded_facts: tuple[str],
    files: tuple[str],
    max_tokens: int,
) -> None:
    if session_id:
        observed_text = " ".join(
            part
            for part in [tool or "", command_text or "", prompt or "", *files]
            if part
        )
        advance_session_state(
            project_memory_dir(cwd),
            session_id,
            tool=tool,
            observed_tokens=estimate_tokens(observed_text) if observed_text else None,
            avg_tokens_per_turn=_cfg().inject.turn_token_estimate,
            context_window_tokens=context_window_tokens,
        )
    click.echo(
        inject_for_tool(
            cwd,
            tool=tool,
            prompt=prompt,
            file_paths=list(files),
            max_tokens=max_tokens,
            session_id=session_id,
            command_text=command_text,
            expanded_ids=set(expanded_facts),
            context_window_tokens=context_window_tokens,
        ),
        nl=False,
    )


@main.command("collect")
@click.option("--cwd", type=click.Path(path_type=Path), default=Path.cwd)
@click.option("--tool", required=True, help="Tool name for the collected session.")
@click.option(
    "--file",
    "input_file",
    type=click.Path(path_type=Path),
    default=None,
    help="Transcript or JSONL input file. When omitted, piped stdin wins; otherwise gitmem falls back to cwd/workspace/events.jsonl if present.",
)
@click.option(
    "--format",
    "input_format",
    type=click.Choice(["auto", "text", "jsonl"]),
    default="auto",
    show_default=True,
)
@click.option(
    "--role",
    "default_role",
    type=click.Choice(["assistant", "tool_result", "user"]),
    default="assistant",
    show_default=True,
    help="Fallback role for plain text input or JSONL records missing a role.",
)
@click.option("--session-id", default=None, help="Override the collected session ID.")
@click.option(
    "--meta",
    "meta_pairs",
    multiple=True,
    help="Extra metadata entries as key=value pairs.",
)
@click.option("--dry-run", is_flag=True, default=False)
def collect_cmd(
    cwd: Path,
    tool: str,
    input_file: Path | None,
    input_format: str,
    default_role: str,
    session_id: str | None,
    meta_pairs: tuple[str, ...],
    dry_run: bool,
) -> None:
    try:
        resolved_file = input_file
        stdin_text = ""
        if resolved_file is None:
            stdin_text = click.get_text_stream("stdin").read()
            if not stdin_text:
                default_file = cwd / "workspace" / "events.jsonl"
                if default_file.exists():
                    resolved_file = default_file
        if resolved_file is not None:
            if not resolved_file.exists():
                raise click.ClickException(f"Collect input file not found: {resolved_file}")
            raw_text = resolved_file.read_text(encoding="utf-8")
        else:
            raw_text = stdin_text
        extra_meta = parse_meta_pairs(meta_pairs)
        if dry_run:
            payload = summarize_collected_session(
                cwd,
                raw_text,
                tool=tool,
                input_format=input_format,
                default_role=default_role,
                session_id=session_id,
                extra_meta=extra_meta,
                source_file=resolved_file,
            )
        else:
            payload = collect_session(
                cwd,
                raw_text,
                tool=tool,
                input_format=input_format,
                default_role=default_role,
                session_id=session_id,
                extra_meta=extra_meta,
                source_file=resolved_file,
                config=_cfg(),
            )
    except (RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(payload, sort_keys=True))


@main.command("dream")
@click.option("--cwd", type=click.Path(path_type=Path), default=Path.cwd)
@click.option("--force", is_flag=True, default=False)
@click.option("--force-reason", default=None, help="Audit reason required when --force bypasses L2 approval gating")
@click.option("--force-lint", is_flag=True, default=False)
@click.option("--mode", type=click.Choice(["local", "remote", "hybrid"]), default=None)
@click.option("--tier", type=click.Choice(["l1", "l2"]), default=None)
@click.option(
    "--pr", "pr_number", type=int, default=None, help="PR number for L2 review"
)
@click.option("--head-sha", default=None, help="Expected PR head commit SHA for L2 review")
def dream_cmd(
    cwd: Path,
    force: bool,
    force_reason: str | None,
    force_lint: bool,
    mode: str | None,
    tier: str | None,
    pr_number: int | None,
    head_sha: str | None,
) -> None:
    cfg = _cfg()
    raw_force_reason = force_reason
    force_reason = force_reason.strip() if force_reason is not None else None
    if mode:
        cfg.dream.mode = mode
    if tier == "l2" and pr_number is None:
        raise click.UsageError("--tier l2 requires --pr <number>")
    if head_sha and tier != "l2":
        raise click.UsageError("--head-sha requires --tier l2")
    if raw_force_reason is not None and not force:
        raise click.UsageError("--force-reason requires --force")
    if force_reason and tier != "l2":
        raise click.UsageError("--force-reason requires --tier l2")
    if force and tier == "l2" and force_reason == "":
        raise click.UsageError("--force-reason cannot be blank")
    if tier == "l2":
        assert pr_number is not None
        output = DreamPipeline(cwd, config=cfg).review_pr(
            pr_number,
            expected_head_sha=head_sha,
            force_merge=force,
            force_reason=force_reason,
        )
        click.echo(json.dumps(output, sort_keys=True))
        if output.get("status") == "error":
            raise click.exceptions.Exit(1)
        return
    result = DreamPipeline(cwd, config=cfg).run(force=force, force_lint=force_lint)
    output = {
        "status": result.status,
        "added": result.added,
        "pruned": result.pruned,
        "findings": result.findings,
        "lint": result.lint,
        "message": result.message,
    }
    if result.pr_proposal:
        output["pr_proposal"] = {
            "title": result.pr_proposal.title,
            "branch": result.pr_proposal.branch,
            "labels": result.pr_proposal.labels,
            "files_changed": result.pr_proposal.files_changed,
        }
    click.echo(json.dumps(output, sort_keys=True))


@main.command("view")
@click.option("--cwd", type=click.Path(path_type=Path), default=Path.cwd)
@click.option("--min-strength", type=int, default=1)
@click.option("--fact", "fact_id", default=None)
@click.option("--list", "list_only", is_flag=True, default=False)
def view_cmd(
    cwd: Path, min_strength: int, fact_id: str | None, list_only: bool
) -> None:
    repo = project_memory_dir(cwd)
    if fact_id:
        fact = find_fact_by_id(repo, fact_id)
        if fact is None:
            fact = find_fact_by_id(user_memory_dir(), fact_id)
        if fact is None:
            raise click.ClickException(f"fact not found: {fact_id}")
        click.echo(json.dumps(fact.to_dict(), sort_keys=True))
        return
    if not list_only:
        url, server = start_viewer(cwd)
        click.echo(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
        return
    facts = [
        fact
        for fact in load_all_facts(repo, include_superseded=False)
        if fact.encoding_strength >= min_strength
    ]
    for fact in facts:
        click.echo(f"{fact.fact_id} [{fact.topic}] {fact.text}")


@main.command("tui")
@click.option("--cwd", type=click.Path(path_type=Path), default=Path.cwd)
def tui_cmd(cwd: Path) -> None:
    url, server = start_viewer(cwd)
    click.echo(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


@main.command("status")
@click.option("--cwd", type=click.Path(path_type=Path), default=Path.cwd)
def status_cmd(cwd: Path) -> None:
    click.echo(json.dumps(build_status_payload(cwd), sort_keys=True))


@main.command("health")
@click.option("--cwd", type=click.Path(path_type=Path), default=Path.cwd)
@click.option("--governance", is_flag=True, default=False)
@click.option("--format", "output_format", type=click.Choice(["json", "human"]), default="json")
def health_cmd(cwd: Path, governance: bool, output_format: str) -> None:
    if not governance and output_format != "json":
        raise click.UsageError("--format is only supported with --governance")
    if governance:
        payload = build_governance_health_payload(cwd, _cfg())
        if output_format == "human":
            click.echo(render_governance_health_human(payload))
        else:
            click.echo(json.dumps(payload, sort_keys=True))
        return
    repo = project_memory_dir(cwd)
    metrics = compute_metrics(repo, _cfg())
    flags = health_flags(metrics)
    advice = build_calibration_advice(metrics, flags)
    click.echo(
        json.dumps(
            {
                "repo": str(repo),
                "ok": len(flags) == 0,
                "flags": flags,
                "advice": advice,
                "guidance": advice,
                "metrics": metrics,
            },
            sort_keys=True,
        )
    )


@main.command("conflicts")
@click.option("--cwd", type=click.Path(path_type=Path), default=Path.cwd)
def conflicts_cmd(cwd: Path) -> None:
    repo = project_memory_dir(cwd)
    for fact in load_all_facts(repo, include_superseded=True):
        if fact.conflicts_with:
            click.echo(f"{fact.fact_id}: {','.join(fact.conflicts_with)}")


@main.command("gaps")
@click.option("--cwd", type=click.Path(path_type=Path), default=Path.cwd)
@click.option("--query", default=None)
@click.option("--resolution-context", default=None)
@click.option("--proposed-fact", default=None)
@click.option("--session", "session_id", default=None)
def gaps_cmd(
    cwd: Path,
    query: str | None,
    resolution_context: str | None,
    proposed_fact: str | None,
    session_id: str | None,
) -> None:
    repo = project_memory_dir(cwd)
    emit_values = [query, resolution_context, proposed_fact, session_id]
    if any(value is not None for value in emit_values):
        if not all(value is not None for value in emit_values):
            raise click.ClickException(
                "gap emission requires --query, --resolution-context, --proposed-fact, and --session"
            )
        try:
            record = emit_gap_signal(
                repo,
                query=query,
                resolution_context=resolution_context,
                proposed_fact=proposed_fact,
                session=session_id,
            )
        except ValueError as exc:
            raise click.ClickException(str(exc)) from exc
        click.echo(json.dumps(record, sort_keys=True))
        return
    path = repo / "meta" / "gaps.jsonl"
    click.echo(path.read_text() if path.exists() else "", nl=False)


@main.command("forget")
@click.option("--cwd", type=click.Path(path_type=Path), default=Path.cwd)
@click.option("--fact", "fact_id", default=None)
@click.option("--topic", default=None)
@click.option("--governed", is_flag=True, default=False)
def forget_cmd(
    cwd: Path,
    fact_id: str | None,
    topic: str | None,
    governed: bool,
) -> None:
    from umx.fact_actions import (
        forget_fact_action,
        forget_fact_governed_action,
        forget_topic_action,
    )

    if fact_id:
        result = (
            forget_fact_governed_action(cwd, fact_id)
            if governed
            else forget_fact_action(cwd, fact_id)
        )
        if not result.ok and result.message:
            raise click.ClickException(result.message)
        click.echo(result.message)
        return
    if topic:
        if governed:
            raise click.ClickException("--governed currently supports --fact only")
        result = forget_topic_action(cwd, topic)
        if not result.ok:
            raise click.ClickException(result.message)
        click.echo(result.message)
        return
    raise click.UsageError("pass --fact or --topic")


@main.command("promote")
@click.option("--cwd", type=click.Path(path_type=Path), default=Path.cwd)
@click.option("--fact", "fact_id", required=True)
@click.option(
    "--to",
    "destination",
    type=click.Choice(["user", "project", "principle"]),
    required=True,
)
def promote_cmd(cwd: Path, fact_id: str, destination: str) -> None:
    from umx.fact_actions import promote_fact_action

    result = promote_fact_action(cwd, fact_id, destination)
    if not result.ok:
        raise click.ClickException(result.message)
    click.echo(result.message)


@main.command("confirm")
@click.option("--cwd", type=click.Path(path_type=Path), default=Path.cwd)
@click.option("--fact", "fact_id", required=True)
def confirm_cmd(cwd: Path, fact_id: str) -> None:
    from umx.fact_actions import confirm_fact_action

    result = confirm_fact_action(cwd, fact_id)
    if not result.ok:
        raise click.ClickException(result.message)
    click.echo(result.message)


@main.command("history")
@click.option("--cwd", type=click.Path(path_type=Path), default=Path.cwd)
@click.option("--fact", "fact_id", required=True)
def history_cmd(cwd: Path, fact_id: str) -> None:
    repo = project_memory_dir(cwd)
    for fact in walk_history(repo, fact_id):
        click.echo(f"{fact.fact_id} {fact.text}")


@main.command("resume")
@click.option("--cwd", type=click.Path(path_type=Path), default=Path.cwd)
@click.option("--include-abandoned", is_flag=True, default=False)
def resume_cmd(cwd: Path, include_abandoned: bool) -> None:
    repo = project_memory_dir(cwd)
    for fact in open_tasks(
        load_all_facts(repo, include_superseded=False),
        include_abandoned=include_abandoned,
    ):
        click.echo(f"{fact.fact_id} [{fact.task_status.value}] {fact.text}")


@main.command("meta")
@click.option("--cwd", type=click.Path(path_type=Path), default=Path.cwd)
@click.option("--topic", required=True)
def meta_cmd(cwd: Path, topic: str) -> None:
    path = manifest_path(project_memory_dir(cwd))
    data = json.loads(path.read_text()) if path.exists() else {"topics": {}}
    click.echo(json.dumps(topic_status(data, topic), sort_keys=True))


@main.command("merge")
@click.option("--cwd", type=click.Path(path_type=Path), default=Path.cwd)
@click.option("--dry-run", is_flag=True, default=False)
def merge_cmd(cwd: Path, dry_run: bool) -> None:
    from umx.fact_actions import merge_conflicts_action

    result = merge_conflicts_action(cwd, dry_run=dry_run)
    if not result.ok:
        raise click.ClickException(result.message)
    click.echo(json.dumps(result.results, sort_keys=True))


@main.command("audit")
@click.option("--cwd", type=click.Path(path_type=Path), default=Path.cwd)
@click.option("--rederive", is_flag=True, default=False)
@click.option("--cross-project", is_flag=True, default=False)
@click.option("--proposal-key")
@click.option("--session", "session_ids", multiple=True)
def audit_cmd(
    cwd: Path,
    rederive: bool,
    cross_project: bool,
    proposal_key: str | None,
    session_ids: tuple[str],
) -> None:
    from umx.audit import audit_report, compare_derived, rederive_from_sessions
    from umx.cross_project import build_cross_project_promotion_report, cross_project_audit_report

    cfg = _cfg()
    if proposal_key and not cross_project:
        raise click.ClickException("--proposal-key requires --cross-project")
    if proposal_key and rederive:
        raise click.ClickException("--proposal-key cannot be combined with --rederive")
    if proposal_key and session_ids:
        raise click.ClickException("--proposal-key cannot be combined with --session")
    if cross_project and rederive:
        raise click.ClickException("--cross-project cannot be combined with --rederive")
    if cross_project and session_ids:
        raise click.ClickException("--cross-project cannot be combined with --session")
    if cross_project:
        try:
            report = (
                build_cross_project_promotion_report(
                    get_umx_home(),
                    cfg,
                    candidate_key=proposal_key,
                )
                if proposal_key
                else cross_project_audit_report(get_umx_home(), cfg)
            )
        except LookupError as exc:
            raise click.ClickException(f"cross-project candidate not found: {exc.args[0]}") from exc
        click.echo(json.dumps(report, sort_keys=True))
        return
    repo = project_memory_dir(cwd)
    if rederive:
        ids = list(session_ids) if session_ids else None
        rederived = rederive_from_sessions(repo, session_ids=ids, config=cfg)
        existing = load_all_facts(repo, include_superseded=False)
        comparison = compare_derived(existing, rederived)
        click.echo(json.dumps(comparison, sort_keys=True))
    else:
        report = audit_report(repo, cfg)
        click.echo(json.dumps(report, sort_keys=True))


@main.command("propose")
@click.option("--cwd", type=click.Path(path_type=Path), default=Path.cwd)
@click.option("--cross-project", is_flag=True, default=False)
@click.option("--proposal-key")
@click.option("--push", is_flag=True, default=False)
@click.option("--open-pr", is_flag=True, default=False)
def propose_cmd(
    cwd: Path,
    cross_project: bool,
    proposal_key: str | None,
    push: bool,
    open_pr: bool,
) -> None:
    from umx.cross_project import (
        materialize_and_push_cross_project_promotion_branch,
        materialize_cross_project_promotion_branch,
        open_cross_project_promotion_pull_request,
    )

    _ = cwd
    if not cross_project:
        raise click.ClickException("--cross-project is required for propose")
    if not proposal_key:
        raise click.ClickException("--proposal-key is required for propose")
    if push and open_pr:
        raise click.ClickException("--push and --open-pr are separate steps; push first, then open the PR")
    try:
        result = (
            open_cross_project_promotion_pull_request(
                get_umx_home(),
                _cfg(),
                candidate_key=proposal_key,
            )
            if open_pr
            else materialize_and_push_cross_project_promotion_branch(
                get_umx_home(),
                _cfg(),
                candidate_key=proposal_key,
            )
            if push
            else materialize_cross_project_promotion_branch(
                get_umx_home(),
                _cfg(),
                candidate_key=proposal_key,
            )
        )
    except LookupError as exc:
        raise click.ClickException(
            f"cross-project candidate not found: {exc.args[0]}"
        ) from exc
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(result, sort_keys=True))


@main.command("sync")
@click.option("--cwd", type=click.Path(path_type=Path), default=Path.cwd)
def sync_cmd(cwd: Path) -> None:
    cfg = _cfg()
    mode = cfg.dream.mode
    if mode == "local":
        click.echo("local mode: nothing to sync")
        return

    from umx.git_ops import (
        GitSignedHistoryError,
        assert_signed_commit_range,
        changed_paths,
        diff_committed_paths_against_ref,
        git_add_and_commit,
        git_commit_failure_message,
        git_current_branch,
        git_fetch,
        git_pull_rebase,
        git_push,
        git_remote_url,
    )

    repo = project_memory_dir(cwd)
    remote = git_remote_url(repo)
    if not remote:
        click.echo("no remote configured; run 'umx setup-remote' first")
        return
    from umx.github_ops import GitHubRemoteIdentityError, assert_expected_github_origin

    try:
        assert_expected_github_origin(
            repo,
            config_org=cfg.org,
            repo_label="project memory repo",
            operation="sync",
        )
    except GitHubRemoteIdentityError as exc:
        raise click.ClickException(str(exc)) from exc

    pending = changed_paths(repo)
    if is_governed_mode(mode):
        current_branch = git_current_branch(repo)
        if current_branch != "main":
            raise click.ClickException(
                f"{mode} mode sync must run from main; current branch is {current_branch or 'detached'}"
            )
        blocked = filter_non_operational_sync_paths(pending, repo)
        if blocked:
            raise click.ClickException(session_sync_error(mode, repo, blocked))
        session_paths = [path for path in pending if path not in blocked]
        if session_paths:
            commit_result = git_add_and_commit(
                repo,
                paths=session_paths,
                message="umx: sync sessions",
                config=cfg,
            )
            if commit_result.failed:
                raise click.ClickException(
                    git_commit_failure_message(commit_result, context="commit failed")
                )

    if not git_fetch(repo):
        raise click.ClickException("fetch failed")
    if is_governed_mode(mode):
        blocked_committed = filter_non_operational_sync_paths(
            diff_committed_paths_against_ref(repo, "origin/main"),
            repo,
        )
        if blocked_committed:
            raise click.ClickException(session_sync_error(mode, repo, blocked_committed))
    if not git_pull_rebase(repo, config=cfg):
        raise click.ClickException("pull --rebase failed")
    try:
        assert_push_safe(
            repo,
            project_root=find_project_root(cwd),
            base_ref="origin/main",
            branch="main",
            config=cfg,
            include_bridge=True,
        )
    except PushSafetyError as exc:
        raise click.ClickException(str(exc)) from exc
    try:
        assert_signed_commit_range(
            repo,
            base_ref="origin/main",
            head_ref="HEAD",
            config=cfg,
            operation="sync",
        )
    except GitSignedHistoryError as exc:
        raise click.ClickException(str(exc)) from exc
    if not git_push(repo):
        raise click.ClickException("push failed")
    click.echo(f"synced with {remote}")


@main.command("setup-remote")
@click.option("--cwd", type=click.Path(path_type=Path), default=Path.cwd)
@click.option(
    "--mode", "new_mode", type=click.Choice(["remote", "hybrid"]), default="hybrid"
)
def setup_remote_cmd(cwd: Path, new_mode: str) -> None:
    """Connect an existing project to a GitHub memory repo."""
    cfg = _cfg()
    if not cfg.org:
        raise click.ClickException(
            "no org configured; run 'umx init --org <org> --mode remote' first"
        )

    from umx.github_ops import GitHubError, gh_available, ensure_repo, set_remote, deploy_workflows

    root = find_project_root(cwd)
    repo = project_memory_dir(root)
    slug = discover_project_slug(root)

    try:
        if not gh_available():
            raise click.ClickException("gh CLI not available")
        url = ensure_repo(cfg.org, slug, private=True)
        set_remote(repo, url)

        if new_mode == "remote":
            deploy_workflows(repo)

        cfg.dream.mode = new_mode
        save_config(config_path(), cfg)

        _bootstrap_remote_repo(
            repo,
            "umx: bootstrap remote project memory",
            project_root=root,
            config=cfg,
        )
        _emit_governance_protection_status(new_mode)

        click.echo(f"mode: {new_mode}")
        click.echo(f"remote: {url}")
    except GitHubError as exc:
        raise click.ClickException(str(exc)) from exc


@main.command("purge")
@click.option("--cwd", type=click.Path(path_type=Path), default=Path.cwd)
@click.option("--session", "session_id", required=True)
@click.option("--dry-run", is_flag=True, default=False)
def purge_cmd(cwd: Path, session_id: str, dry_run: bool) -> None:
    from umx.purge import purge_session

    repo = project_memory_dir(cwd)
    if not dry_run:
        _require_direct_fact_write_allowed("umx purge")
    if dry_run:
        from umx.memory import iter_fact_files, read_fact_file

        count = 0
        for path in iter_fact_files(repo):
            for fact in read_fact_file(path, repo_dir=repo):
                if (
                    fact.source_session == session_id
                    or session_id in fact.provenance.sessions
                ):
                    count += 1
        click.echo(
            json.dumps({"dry_run": True, "facts_would_remove": count}, sort_keys=True)
        )
    else:
        result = purge_session(repo, session_id)
        if result["session_removed"] or result["facts_removed"]:
            _commit_repo(repo, f"umx: purge session {session_id}")
        click.echo(json.dumps(result, sort_keys=True))


@main.command("rebuild-index")
@click.option("--cwd", type=click.Path(path_type=Path), default=Path.cwd)
@click.option("--embeddings", is_flag=True, default=False)
def rebuild_index_cmd(cwd: Path, embeddings: bool) -> None:
    repo = project_memory_dir(cwd)
    cfg = _cfg()
    if embeddings and not embeddings_available(cfg):
        click.echo(
            f"embedding provider '{cfg.search.embedding.provider}' is unavailable; rebuilding lexical index only",
            err=True,
        )
    refresh_index(repo, with_embeddings=embeddings, config=cfg)
    if cfg.search.backend == "hybrid" and not embeddings:
        message = embedding_rebuild_message(repo, config=cfg)
        if message:
            click.echo(message, err=True)
    click.echo(str(repo / "meta" / "index.sqlite"))


@main.command("archive-sessions")
@click.option("--cwd", type=click.Path(path_type=Path), default=Path.cwd)
def archive_sessions_cmd(cwd: Path) -> None:
    from umx.sessions import archive_sessions

    repo = project_memory_dir(cwd)
    result = archive_sessions(repo, config=_cfg())
    if result["archived_sessions"]:
        _commit_repo(repo, "umx: archive sessions")
    click.echo(json.dumps(result, sort_keys=True))


@main.command("init-actions")
@click.option("--dir", "target_dir", type=click.Path(path_type=Path), default=Path.cwd)
def init_actions_cmd(target_dir: Path) -> None:
    from umx.actions import write_workflow_templates

    written = write_workflow_templates(target_dir)
    click.echo(json.dumps([str(path) for path in written], sort_keys=True))


@main.command("migrate-scope")
@click.option("--cwd", type=click.Path(path_type=Path), default=Path.cwd)
@click.option("--from", "old_path", required=True)
@click.option("--to", "new_path", required=True)
def migrate_scope_cmd(cwd: Path, old_path: str, new_path: str) -> None:
    repo = project_memory_dir(cwd)
    _require_direct_fact_write_allowed("umx migrate-scope")
    old = repo / old_path
    new = repo / new_path
    new.parent.mkdir(parents=True, exist_ok=True)
    old.rename(new)
    _commit_repo(repo, f"umx: migrate scope {old_path} -> {new_path}")
    click.echo(str(new))


@main.command("doctor")
@click.option("--cwd", type=click.Path(path_type=Path), default=Path.cwd)
@click.option("--fix", is_flag=True, default=False)
def doctor_cmd(cwd: Path, fix: bool) -> None:
    click.echo(json.dumps(run_doctor(cwd, fix=fix), sort_keys=True))


@main.command("migrate")
@click.option("--cwd", type=click.Path(path_type=Path), default=Path.cwd)
def migrate_cmd(cwd: Path) -> None:
    from umx.migrations import run_migrations

    _require_direct_fact_write_allowed("umx migrate")
    try:
        repo = project_memory_dir(cwd)
        payload = run_migrations(repo, config=_cfg()).to_dict()
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(payload, sort_keys=True))


@main.group("eval")
def eval_group() -> None:
    """Evaluation tools."""


@eval_group.command("l2-review")
@click.option(
    "--cases",
    "cases_path",
    type=click.Path(path_type=Path),
    default=Path("tests") / "eval" / "l2_reviewer",
)
@click.option("--case", "case_id", default=None)
@click.option("--min-pass-rate", type=float, default=0.85)
def eval_l2_review_cmd(cases_path: Path, case_id: str | None, min_pass_rate: float) -> None:
    from umx.dream.l2_eval import run_l2_review_eval

    try:
        payload = run_l2_review_eval(
            cases_path,
            _cfg(),
            case_id=case_id,
            min_pass_rate=min_pass_rate,
        )
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(payload))
    if payload["status"] != "ok":
        raise click.exceptions.Exit(1)


@main.group("config")
def config_group() -> None:
    """Config operations."""


@config_group.command("set")
@click.argument("key")
@click.argument("value")
def config_set_cmd(key: str, value: str) -> None:
    cfg = _cfg()
    if key == "redaction.patterns":
        cfg.sessions.redaction_patterns = _parse_redaction_patterns_value(value)
    elif key == "telemetry.enabled":
        cfg.telemetry.enabled = _parse_bool_value(value)
    else:
        raise click.ClickException(f"unsupported config key: {key}")
    save_config(config_path(), cfg)
    click.echo(key)


@main.group("shim")
def shim_group() -> None:
    """Wrapper shims for tool integration."""


@main.group("bridge")
def bridge_group() -> None:
    """Legacy project-repo bridge operations."""


@main.group("hooks")
def hooks_group() -> None:
    """Hook integration helpers."""


@hooks_group.group("claude-code")
def hooks_claude_code_group() -> None:
    """Claude Code live hook workflow helpers."""


def _emit_hook_response(payload: dict | None) -> None:
    if payload:
        click.echo(json.dumps(payload, sort_keys=True))


def _run_generic_shim(
    cwd: Path,
    output: Path | None,
    max_tokens: int,
    *,
    tool: str | None = None,
) -> None:
    from umx.shim.generic import generate_prompt, write_context_file

    if output:
        path = write_context_file(
            cwd, output_path=output, tool=tool, max_tokens=max_tokens
        )
        click.echo(str(path))
    else:
        click.echo(generate_prompt(cwd, tool=tool, max_tokens=max_tokens), nl=False)


@hooks_claude_code_group.command("print")
@click.option("--command", "command_prefix", default="umx")
def hooks_claude_code_print_cmd(command_prefix: str) -> None:
    from umx.claude_code_hooks import claude_code_hook_config

    click.echo(json.dumps(claude_code_hook_config(command_prefix), indent=2, sort_keys=True))


@hooks_claude_code_group.command("install")
@click.option("--cwd", type=click.Path(path_type=Path), default=Path.cwd)
@click.option(
    "--scope",
    type=click.Choice(["user", "project", "local"]),
    default="local",
    show_default=True,
)
@click.option("--command", "command_prefix", default="umx")
def hooks_claude_code_install_cmd(cwd: Path, scope: str, command_prefix: str) -> None:
    from umx.claude_code_hooks import install_claude_code_hooks

    settings_path = install_claude_code_hooks(
        cwd,
        scope=scope,
        command_prefix=command_prefix,
    )
    click.echo(json.dumps({"installed": str(settings_path), "scope": scope}, sort_keys=True))


@hooks_claude_code_group.command("session-start")
@click.option("--payload-file", type=click.Path(path_type=Path), default=None)
def hooks_claude_code_session_start_cmd(payload_file: Path | None) -> None:
    from umx.claude_code_hooks import read_hook_payload, session_start_response

    _emit_hook_response(session_start_response(read_hook_payload(payload_file)))


@hooks_claude_code_group.command("pre-tool-use")
@click.option("--payload-file", type=click.Path(path_type=Path), default=None)
def hooks_claude_code_pre_tool_use_cmd(payload_file: Path | None) -> None:
    from umx.claude_code_hooks import pre_tool_use_response, read_hook_payload

    _emit_hook_response(pre_tool_use_response(read_hook_payload(payload_file)))


@hooks_claude_code_group.command("pre-compact")
@click.option("--payload-file", type=click.Path(path_type=Path), default=None)
def hooks_claude_code_pre_compact_cmd(payload_file: Path | None) -> None:
    from umx.claude_code_hooks import pre_compact_response, read_hook_payload

    pre_compact_response(read_hook_payload(payload_file))


@hooks_claude_code_group.command("session-end")
@click.option("--payload-file", type=click.Path(path_type=Path), default=None)
def hooks_claude_code_session_end_cmd(payload_file: Path | None) -> None:
    from umx.claude_code_hooks import read_hook_payload, session_end_response

    session_end_response(read_hook_payload(payload_file))


@bridge_group.command("sync")
@click.option("--cwd", type=click.Path(path_type=Path), default=Path.cwd)
@click.option("--target", "targets", multiple=True)
def bridge_sync_cmd(cwd: Path, targets: tuple[str]) -> None:
    from umx.bridge import write_bridge

    root = find_project_root(cwd)
    repo = project_memory_dir(cwd)
    written = write_bridge(
        root, repo, config=_cfg(), target_files=list(targets) or None
    )
    click.echo(json.dumps([str(path) for path in written], sort_keys=True))


@bridge_group.command("remove")
@click.option("--cwd", type=click.Path(path_type=Path), default=Path.cwd)
@click.option("--target", "targets", multiple=True)
def bridge_remove_cmd(cwd: Path, targets: tuple[str]) -> None:
    from umx.bridge import remove_bridge

    root = find_project_root(cwd)
    updated = remove_bridge(root, config=_cfg(), target_files=list(targets) or None)
    click.echo(json.dumps([str(path) for path in updated], sort_keys=True))


@bridge_group.command("import")
@click.option("--cwd", type=click.Path(path_type=Path), default=Path.cwd)
@click.option("--target", "targets", multiple=True)
@click.option("--topic", default="legacy-bridge")
@click.option("--dry-run", is_flag=True, default=False)
def bridge_import_cmd(
    cwd: Path, targets: tuple[str], topic: str, dry_run: bool
) -> None:
    from umx.bridge import import_bridge_facts
    from umx.memory import add_fact

    root = find_project_root(cwd)
    repo = project_memory_dir(cwd)
    if not dry_run:
        _require_direct_fact_write_allowed("umx bridge import")
    imported = import_bridge_facts(
        root,
        config=_cfg(),
        target_files=list(targets) or None,
        topic=topic,
    )
    if not dry_run:
        for fact in imported:
            add_fact(repo, fact, auto_commit=False)
        if imported:
            _commit_repo(repo, f"umx: import bridge facts to {topic}")
    click.echo(
        json.dumps({"dry_run": dry_run, "imported": len(imported)}, sort_keys=True)
    )


@shim_group.command("aider")
@click.option("--cwd", type=click.Path(path_type=Path), default=Path.cwd)
@click.option("--output", type=click.Path(path_type=Path), default=None)
@click.option("--max-tokens", type=int, default=4000)
def shim_aider_cmd(cwd: Path, output: Path | None, max_tokens: int) -> None:
    from umx.shim.aider import generate_aider_prompt, write_aider_memory_file

    if output:
        path = write_aider_memory_file(cwd, output_path=output, max_tokens=max_tokens)
        click.echo(str(path))
    else:
        click.echo(generate_aider_prompt(cwd, max_tokens=max_tokens), nl=False)


@shim_group.command("generic")
@click.option("--cwd", type=click.Path(path_type=Path), default=Path.cwd)
@click.option("--tool", default=None)
@click.option("--output", type=click.Path(path_type=Path), default=None)
@click.option("--max-tokens", type=int, default=4000)
def shim_generic_cmd(
    cwd: Path, tool: str | None, output: Path | None, max_tokens: int
) -> None:
    _run_generic_shim(cwd, output, max_tokens, tool=tool)


@shim_group.command("amp")
@click.option("--cwd", type=click.Path(path_type=Path), default=Path.cwd)
@click.option("--output", type=click.Path(path_type=Path), default=None)
@click.option("--max-tokens", type=int, default=4000)
def shim_amp_cmd(cwd: Path, output: Path | None, max_tokens: int) -> None:
    _run_generic_shim(cwd, output, max_tokens, tool="amp")


@shim_group.command("cursor")
@click.option("--cwd", type=click.Path(path_type=Path), default=Path.cwd)
@click.option("--output", type=click.Path(path_type=Path), default=None)
@click.option("--max-tokens", type=int, default=4000)
def shim_cursor_cmd(cwd: Path, output: Path | None, max_tokens: int) -> None:
    _run_generic_shim(cwd, output, max_tokens, tool="cursor")


@shim_group.command("jules")
@click.option("--cwd", type=click.Path(path_type=Path), default=Path.cwd)
@click.option("--output", type=click.Path(path_type=Path), default=None)
@click.option("--max-tokens", type=int, default=4000)
def shim_jules_cmd(cwd: Path, output: Path | None, max_tokens: int) -> None:
    _run_generic_shim(cwd, output, max_tokens, tool="jules")


@shim_group.command("qodo")
@click.option("--cwd", type=click.Path(path_type=Path), default=Path.cwd)
@click.option("--output", type=click.Path(path_type=Path), default=None)
@click.option("--max-tokens", type=int, default=4000)
def shim_qodo_cmd(cwd: Path, output: Path | None, max_tokens: int) -> None:
    _run_generic_shim(cwd, output, max_tokens, tool="qodo")


@main.group("secret")
def secret_group() -> None:
    """Secret operations."""


@secret_group.command("get")
@click.argument("key")
def secret_get(key: str) -> None:
    if "/" in key or "\\" in key or key.startswith("."):
        raise click.ClickException(
            "Invalid secret key: must not contain path separators or start with '.'"
        )
    path = get_umx_home() / "user" / "local" / "secret" / key
    click.echo(path.read_text() if path.exists() else "", nl=False)


@secret_group.command("set")
@click.argument("key")
@click.argument("value")
def secret_set(key: str, value: str) -> None:
    if "/" in key or "\\" in key or key.startswith("."):
        raise click.ClickException(
            "Invalid secret key: must not contain path separators or start with '.'"
        )
    path = get_umx_home() / "user" / "local" / "secret" / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value)
    click.echo(key)


@main.command("export")
@click.option("--cwd", type=click.Path(path_type=Path), default=Path.cwd)
@click.option("--out", type=click.Path(path_type=Path), required=True)
def export_cmd(cwd: Path, out: Path) -> None:
    from umx.backup import export_full

    repo = project_memory_dir(cwd)
    try:
        payload = export_full(repo, out).to_dict()
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(payload, sort_keys=True))


@main.command("import")
@click.option("--cwd", type=click.Path(path_type=Path), default=Path.cwd)
@click.option(
    "--adapter",
    type=click.Choice(["claude-code", "copilot", "aider", "generic"]),
    required=False,
)
@click.option("--full", "full_backup", type=click.Path(path_type=Path), default=None)
@click.option("--force", is_flag=True, default=False)
@click.option("--dry-run", is_flag=True, default=False)
def import_cmd(
    cwd: Path,
    adapter: str | None,
    full_backup: Path | None,
    force: bool,
    dry_run: bool,
) -> None:
    from umx.adapters import get_adapter_by_name
    from umx.backup import import_full, inspect_backup_dir, target_contains_backup_data
    from umx.memory import add_fact

    if (adapter is None) == (full_backup is None):
        raise click.ClickException("Provide exactly one of --adapter or --full.")
    if force and full_backup is None:
        raise click.ClickException("--force is only supported with --full.")

    repo = project_memory_dir(cwd)
    if full_backup is not None:
        try:
            manifest = inspect_backup_dir(full_backup)
        except RuntimeError as exc:
            raise click.ClickException(str(exc)) from exc
        if dry_run:
            click.echo(
                json.dumps(
                    {
                        "dry_run": True,
                        "files_found": len(manifest.files),
                        "force_required": target_contains_backup_data(repo),
                        "source_dir": str(full_backup.resolve()),
                    },
                    sort_keys=True,
                )
            )
            return
        _require_direct_fact_write_allowed("umx import")
        try:
            payload = import_full(full_backup, repo, force=force).to_dict()
        except RuntimeError as exc:
            raise click.ClickException(str(exc)) from exc
        _commit_repo(repo, "umx: import full backup")
        click.echo(json.dumps(payload, sort_keys=True))
        return

    root = find_project_root(cwd)
    if not dry_run:
        _require_direct_fact_write_allowed("umx import")
    adapter_inst = get_adapter_by_name(adapter)
    facts = adapter_inst.read_native_memory(root)
    if dry_run:
        click.echo(
            json.dumps({"dry_run": True, "facts_found": len(facts)}, sort_keys=True)
        )
    else:
        for fact in facts:
            add_fact(repo, fact, auto_commit=False)
        if facts:
            _commit_repo(repo, f"umx: import {adapter} memory")
        click.echo(json.dumps({"imported": len(facts)}, sort_keys=True))


@main.command("mcp")
def mcp_cmd() -> None:
    """Start MCP server (stdio transport)."""
    from umx.mcp_server import run

    run()


@main.group("capture")
def capture_group() -> None:
    """Transcript capture operations."""


@capture_group.command("codex")
@click.option("--cwd", type=click.Path(path_type=Path), default=Path.cwd)
@click.option(
    "--file",
    "rollout_file",
    type=click.Path(path_type=Path),
    default=None,
    help="Specific Codex rollout JSONL file to import.",
)
@click.option(
    "--source-root",
    type=click.Path(path_type=Path),
    default=None,
    help="Codex home or sessions directory. Defaults to ~/.codex.",
)
@click.option("--dry-run", is_flag=True, default=False)
def capture_codex_cmd(
    cwd: Path,
    rollout_file: Path | None,
    source_root: Path | None,
    dry_run: bool,
) -> None:
    from umx.codex_capture import (
        capture_codex_rollout,
        latest_codex_rollout_path,
        parse_codex_rollout,
    )

    target = rollout_file or latest_codex_rollout_path(source_root)
    if target is None:
        raise click.ClickException("No Codex rollout files found.")
    if not target.exists():
        raise click.ClickException(f"Codex rollout file not found: {target}")

    if dry_run:
        transcript = parse_codex_rollout(target)
        click.echo(
            json.dumps(
                {
                    "dry_run": True,
                    "source_file": str(target),
                    "tool": "codex",
                    "umx_session_id": transcript.umx_session_id,
                    "events_imported": len(transcript.events),
                },
                sort_keys=True,
            )
        )
        return

    click.echo(
        json.dumps(capture_codex_rollout(cwd, target, config=_cfg()), sort_keys=True)
    )


@capture_group.command("copilot")
@click.option("--cwd", type=click.Path(path_type=Path), default=Path.cwd)
@click.option(
    "--file",
    "session_file",
    type=click.Path(path_type=Path),
    default=None,
    help="Specific Copilot events.jsonl file to import.",
)
@click.option(
    "--source-root",
    type=click.Path(path_type=Path),
    default=None,
    help="Copilot session-state directory. Defaults to ~/.copilot/session-state/.",
)
@click.option("--dry-run", is_flag=True, default=False)
def capture_copilot_cmd(
    cwd: Path,
    session_file: Path | None,
    source_root: Path | None,
    dry_run: bool,
) -> None:
    from umx.copilot_capture import (
        capture_copilot_session,
        latest_copilot_session_path,
        parse_copilot_session,
    )

    target = session_file or latest_copilot_session_path(source_root)
    if target is None:
        raise click.ClickException("No Copilot session logs found.")
    if not target.exists():
        raise click.ClickException(f"Copilot session file not found: {target}")

    if dry_run:
        transcript = parse_copilot_session(target)
        click.echo(
            json.dumps(
                {
                    "dry_run": True,
                    "source_file": str(target),
                    "tool": "copilot",
                    "umx_session_id": transcript.umx_session_id,
                    "events_imported": len(transcript.events),
                    "model": transcript.model,
                },
                sort_keys=True,
            )
        )
        return

    click.echo(
        json.dumps(capture_copilot_session(cwd, target, config=_cfg()), sort_keys=True)
    )


@capture_group.command("claude-code")
@click.option("--cwd", type=click.Path(path_type=Path), default=Path.cwd)
@click.option(
    "--file",
    "session_file",
    type=click.Path(path_type=Path),
    default=None,
    help="Specific Claude Code session JSONL file to import.",
)
@click.option(
    "--source-root",
    type=click.Path(path_type=Path),
    default=None,
    help="Override default ~/.claude/projects/ root.",
)
@click.option(
    "--all",
    "capture_all",
    is_flag=True,
    default=False,
    help="Import all sessions for this project, not just the latest.",
)
@click.option("--dry-run", is_flag=True, default=False)
def capture_claude_code_cmd(
    cwd: Path,
    session_file: Path | None,
    source_root: Path | None,
    capture_all: bool,
    dry_run: bool,
) -> None:
    from umx.claude_code_capture import (
        capture_claude_code_session,
        latest_claude_code_session_path,
        list_claude_code_sessions,
        parse_claude_code_session,
        prepare_claude_code_capture,
    )

    root = find_project_root(cwd)
    if session_file:
        targets = [session_file]
    elif capture_all:
        targets = list_claude_code_sessions(project_root=root, source_root=source_root)
        if not targets:
            raise click.ClickException(
                "No Claude Code session files found for this project."
            )
    else:
        target = latest_claude_code_session_path(
            project_root=root, source_root=source_root
        )
        if target is None:
            raise click.ClickException(
                "No Claude Code session files found for this project."
            )
        targets = [target]

    if dry_run:
        results = []
        if capture_all and len(targets) > 1:
            prepared = _prepare_capture_batch(targets, prepare_claude_code_capture)
            results = [{**item["result"], "dry_run": True} for item in prepared]
        else:
            for path in targets:
                if not path.exists():
                    raise click.ClickException(f"Session file not found: {path}")
                transcript = parse_claude_code_session(path)
                results.append(
                    {
                        "dry_run": True,
                        "source_file": str(path),
                        "tool": "claude-code",
                        "umx_session_id": transcript.umx_session_id,
                        "events_imported": len(transcript.events),
                    }
                )
        click.echo(json.dumps(results if capture_all else results[0], sort_keys=True))
        return

    results = []
    cfg = _cfg()
    for path in targets:
        if not path.exists():
            raise click.ClickException(f"Session file not found: {path}")
    repo = project_memory_dir(root)
    if capture_all and len(targets) > 1:
        prepared = _prepare_capture_batch(targets, prepare_claude_code_capture)
        results = _persist_prepared_capture_batch(repo, prepared, config=cfg)
    else:
        for path in targets:
            results.append(capture_claude_code_session(cwd, path, config=cfg))
    if results:
        from umx.git_ops import git_add_and_commit, git_commit_failure_message

        commit_result = git_add_and_commit(
            repo,
            message="umx: capture claude-code sessions",
            config=cfg,
        )
        if commit_result.failed:
            raise click.ClickException(
                git_commit_failure_message(commit_result, context="commit failed")
            )
    click.echo(json.dumps(results if capture_all else results[0], sort_keys=True))


@capture_group.command("gemini")
@click.option("--cwd", type=click.Path(path_type=Path), default=Path.cwd)
@click.option(
    "--file",
    "session_file",
    type=click.Path(path_type=Path),
    default=None,
    help="Specific Gemini session JSON file to import.",
)
@click.option(
    "--source-root",
    type=click.Path(path_type=Path),
    default=None,
    help="Override default ~/.gemini/ root.",
)
@click.option(
    "--all",
    "capture_all",
    is_flag=True,
    default=False,
    help="Import all sessions for this project, not just the latest.",
)
@click.option("--dry-run", is_flag=True, default=False)
def capture_gemini_cmd(
    cwd: Path,
    session_file: Path | None,
    source_root: Path | None,
    capture_all: bool,
    dry_run: bool,
) -> None:
    from umx.gemini_capture import (
        capture_gemini_session,
        latest_gemini_session_path,
        list_gemini_sessions,
        parse_gemini_session,
        prepare_gemini_capture,
    )

    root = find_project_root(cwd)
    if session_file:
        targets = [session_file]
    elif capture_all:
        targets = list_gemini_sessions(project_root=root, source_root=source_root)
        if not targets:
            raise click.ClickException("No Gemini session files found for this project.")
    else:
        target = latest_gemini_session_path(project_root=root, source_root=source_root)
        if target is None:
            raise click.ClickException("No Gemini session files found for this project.")
        targets = [target]

    if dry_run:
        results = []
        if capture_all and len(targets) > 1:
            prepared = _prepare_capture_batch(targets, prepare_gemini_capture)
            results = [{**item["result"], "dry_run": True} for item in prepared]
        else:
            for path in targets:
                if not path.exists():
                    raise click.ClickException(f"Session file not found: {path}")
                transcript = parse_gemini_session(path)
                results.append(
                    {
                        "dry_run": True,
                        "source_file": str(path),
                        "tool": "gemini",
                        "umx_session_id": transcript.umx_session_id,
                        "events_imported": len(transcript.events),
                    }
                )
        click.echo(json.dumps(results if capture_all else results[0], sort_keys=True))
        return

    results = []
    cfg = _cfg()
    repo = project_memory_dir(root)
    if capture_all and len(targets) > 1:
        prepared = _prepare_capture_batch(targets, prepare_gemini_capture)
        results = _persist_prepared_capture_batch(repo, prepared, config=cfg)
    else:
        for path in targets:
            results.append(capture_gemini_session(cwd, path, config=cfg))
    if results:
        from umx.git_ops import git_add_and_commit, git_commit_failure_message

        commit_result = git_add_and_commit(
            repo,
            message="umx: capture gemini sessions",
            config=cfg,
        )
        if commit_result.failed:
            raise click.ClickException(
                git_commit_failure_message(commit_result, context="commit failed")
            )
    click.echo(json.dumps(results if capture_all else results[0], sort_keys=True))


@capture_group.command("opencode")
@click.option("--cwd", type=click.Path(path_type=Path), default=Path.cwd)
@click.option(
    "--db",
    "db_path",
    type=click.Path(path_type=Path),
    default=None,
    help="OpenCode SQLite DB path. Defaults to ~/.local/share/opencode/opencode.db.",
)
@click.option(
    "--session-id",
    default=None,
    help="Specific OpenCode session ID to import.",
)
@click.option(
    "--all",
    "capture_all",
    is_flag=True,
    default=False,
    help="Import all sessions from the OpenCode DB.",
)
@click.option("--dry-run", is_flag=True, default=False)
def capture_opencode_cmd(
    cwd: Path,
    db_path: Path | None,
    session_id: str | None,
    capture_all: bool,
    dry_run: bool,
) -> None:
    from umx.opencode_capture import (
        capture_opencode_session,
        latest_opencode_session,
        list_opencode_sessions,
    )

    root = find_project_root(cwd)
    if db_path is not None and not db_path.exists():
        raise click.ClickException(f"OpenCode DB not found: {db_path}")

    if session_id:
        candidates = list_opencode_sessions(source_root=db_path)
        targets = [
            session for session in candidates if session.session_id == session_id
        ]
        if not targets:
            raise click.ClickException(f"OpenCode session not found: {session_id}")
    elif capture_all:
        targets = list_opencode_sessions(source_root=db_path)
        if not targets:
            raise click.ClickException("No OpenCode sessions found.")
    else:
        target = latest_opencode_session(project_root=root, source_root=db_path)
        if target is None:
            raise click.ClickException("No OpenCode sessions found for this project.")
        targets = [target]

    if dry_run:
        results = []
        for session in targets:
            results.append(
                {
                    "dry_run": True,
                    "source_session_id": session.session_id,
                    "tool": "opencode",
                    "umx_session_id": session.umx_session_id,
                    "events_imported": len(session.events),
                }
            )
        click.echo(json.dumps(results if capture_all else results[0], sort_keys=True))
        return

    results = []
    cfg = _cfg()
    for session in targets:
        results.append(capture_opencode_session(cwd, session, config=cfg))
    if results:
        from umx.git_ops import git_add_and_commit, git_commit_failure_message

        repo = project_memory_dir(root)
        commit_result = git_add_and_commit(
            repo,
            message="umx: capture opencode sessions",
            config=cfg,
        )
        if commit_result.failed:
            raise click.ClickException(
                git_commit_failure_message(commit_result, context="commit failed")
            )
    click.echo(json.dumps(results if capture_all else results[0], sort_keys=True))


@capture_group.command("amp")
@click.option("--cwd", type=click.Path(path_type=Path), default=Path.cwd)
@click.option(
    "--file",
    "thread_file",
    type=click.Path(path_type=Path),
    default=None,
    help="Specific Amp thread JSON file to import.",
)
@click.option(
    "--source-root",
    type=click.Path(path_type=Path),
    default=None,
    help="Override default ~/.local/share/amp/ root.",
)
@click.option(
    "--thread-id",
    default=None,
    help="Specific Amp thread ID to import.",
)
@click.option(
    "--all",
    "capture_all",
    is_flag=True,
    default=False,
    help="Import all Amp threads for this project, not just the latest.",
)
@click.option("--dry-run", is_flag=True, default=False)
def capture_amp_cmd(
    cwd: Path,
    thread_file: Path | None,
    source_root: Path | None,
    thread_id: str | None,
    capture_all: bool,
    dry_run: bool,
) -> None:
    from umx.amp_capture import (
        capture_amp_thread,
        latest_amp_thread_path,
        list_amp_threads,
        parse_amp_thread,
        prepare_amp_capture,
    )

    root = find_project_root(cwd)
    if thread_file:
        targets = [thread_file]
    elif thread_id:
        candidates = list_amp_threads(source_root=source_root)
        targets = [path for path in candidates if path.stem == thread_id]
        if not targets:
            raise click.ClickException(f"Amp thread not found: {thread_id}")
    elif capture_all:
        targets = list_amp_threads(project_root=root, source_root=source_root)
        if not targets:
            raise click.ClickException("No Amp thread files found for this project.")
    else:
        target = latest_amp_thread_path(project_root=root, source_root=source_root)
        if target is None:
            raise click.ClickException("No Amp thread files found for this project.")
        targets = [target]

    if dry_run:
        results = []
        if capture_all and len(targets) > 1:
            prepared = _prepare_capture_batch(targets, prepare_amp_capture)
            results = [{**item["result"], "dry_run": True} for item in prepared]
        else:
            for path in targets:
                if not path.exists():
                    raise click.ClickException(f"Thread file not found: {path}")
                transcript = parse_amp_thread(path)
                results.append(
                    {
                        "dry_run": True,
                        "source_file": str(path),
                        "tool": "amp",
                        "umx_session_id": transcript.umx_session_id,
                        "events_imported": len(transcript.events),
                    }
                )
        click.echo(json.dumps(results if capture_all else results[0], sort_keys=True))
        return

    results = []
    cfg = _cfg()
    for path in targets:
        if not path.exists():
            raise click.ClickException(f"Thread file not found: {path}")
    repo = project_memory_dir(root)
    if capture_all and len(targets) > 1:
        prepared = _prepare_capture_batch(targets, prepare_amp_capture)
        results = _persist_prepared_capture_batch(repo, prepared, config=cfg)
    else:
        for path in targets:
            results.append(capture_amp_thread(cwd, path, config=cfg))
    if results:
        from umx.git_ops import git_add_and_commit, git_commit_failure_message

        commit_result = git_add_and_commit(
            repo,
            message="umx: capture amp sessions",
            config=cfg,
        )
        if commit_result.failed:
            raise click.ClickException(
                git_commit_failure_message(commit_result, context="commit failed")
            )
    click.echo(json.dumps(results if capture_all else results[0], sort_keys=True))


@main.command("search")
@click.option("--cwd", type=click.Path(path_type=Path), default=Path.cwd)
@click.option(
    "--raw",
    is_flag=True,
    default=False,
    help="Search raw session files instead of the fact index.",
)
@click.argument("query")
def search_cmd(cwd: Path, raw: bool, query: str) -> None:
    repo = project_memory_dir(cwd)
    if raw:
        for result in search_sessions(repo, query):
            click.echo(
                f"{result['session_id']} [{result['role']}] "
                f"(score={result['score']:.2f}) {result['content_snippet']}"
            )
    else:
        cfg = _cfg()
        message = embedding_rebuild_message(repo, config=cfg)
        if cfg.search.backend == "hybrid" and message:
            click.echo(message, err=True)
        for fact in query_index(repo, query, config=cfg):
            click.echo(f"{fact.fact_id} [{fact.topic}] {fact.text}")


if __name__ == "__main__":
    main()
