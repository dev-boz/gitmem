from __future__ import annotations

import json
from pathlib import Path

import click

from umx.budget import estimate_tokens
from umx.config import default_config, load_config, save_config
from umx.doctor import run_doctor
from umx.dream.gates import increment_session_count, read_dream_state
from umx.dream.pipeline import DreamPipeline
from umx.inject import inject_for_tool
from umx.manifest import manifest_path
from umx.metrics import compute_metrics, health_flags
from umx.memory import find_fact_by_id, load_all_facts, remove_fact, replace_fact
from umx.models import ConsolidationStatus, Scope, SourceType, Verification
from umx.search import advance_session_state
from umx.scope import (
    config_path,
    discover_project_slug,
    find_project_root,
    get_umx_home,
    init_local_umx,
    init_project_memory,
    project_memory_dir,
    user_memory_dir,
)
from umx.viewer.server import start as start_viewer
from umx.search import query_index, rebuild_index, search_sessions
from umx.sessions import generate_session_id
from umx.supersession import walk_history
from umx.tasks import open_tasks
from umx.tombstones import forget_fact, forget_topic, load_tombstones


def _cfg():
    return load_config(config_path())


def _commit_repo(repo: Path, message: str) -> bool:
    from umx.git_ops import git_add_and_commit

    return git_add_and_commit(repo, message=message)


def _bootstrap_remote_repo(repo: Path, message: str) -> None:
    from umx.git_ops import git_push

    _commit_repo(repo, message)
    git_push(repo, branch="main", set_upstream=True)


@click.group()
def main() -> None:
    """UMX CLI."""
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
@click.option("--mode", "init_mode", type=click.Choice(["local", "remote", "hybrid"]), default="local")
def init_cmd(org: str | None, init_mode: str) -> None:
    config = default_config()
    config.org = org
    config.dream.mode = init_mode
    home = init_local_umx(org)
    save_config(config_path(), config)

    if init_mode in ("remote", "hybrid") and org:
        from umx.github_ops import gh_available, ensure_repo, set_remote
        if not gh_available():
            click.echo("warning: gh CLI not available; skipping remote setup")
        else:
            url = ensure_repo(org, "umx-user", private=True)
            set_remote(home / "user", url)
            _bootstrap_remote_repo(home / "user", "umx: bootstrap remote user memory")
            click.echo(f"remote: {url}")

    click.echo(str(home))


@main.command("init-project")
@click.option("--cwd", type=click.Path(path_type=Path), default=Path.cwd)
@click.option("--slug", default=None)
def init_project_cmd(cwd: Path, slug: str | None) -> None:
    root = find_project_root(cwd)
    if slug:
        (root / ".umx-project").write_text(f"{slug}\n")
    repo = init_project_memory(root, write_marker=True)

    cfg = _cfg()
    if cfg.dream.mode in ("remote", "hybrid") and cfg.org:
        from umx.github_ops import gh_available, ensure_repo, set_remote, deploy_workflows
        if not gh_available():
            click.echo("warning: gh CLI not available; skipping remote setup")
        else:
            project_slug = discover_project_slug(root)
            url = ensure_repo(cfg.org, project_slug, private=True)
            set_remote(repo, url)
            if cfg.dream.mode == "remote":
                deploy_workflows(repo)
            _bootstrap_remote_repo(repo, "umx: bootstrap remote project memory")
            click.echo(f"remote: {url}")

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
        observed_text = " ".join(part for part in [tool or "", command_text or "", prompt or "", *files] if part)
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
def collect_cmd(cwd: Path) -> None:
    repo = project_memory_dir(cwd)
    count = increment_session_count(repo)
    click.echo(f"session_count={count}")


@main.command("dream")
@click.option("--cwd", type=click.Path(path_type=Path), default=Path.cwd)
@click.option("--force", is_flag=True, default=False)
@click.option("--mode", type=click.Choice(["local", "remote", "hybrid"]), default=None)
@click.option("--tier", type=click.Choice(["l1", "l2"]), default=None)
@click.option("--pr", "pr_number", type=int, default=None, help="PR number for L2 review")
def dream_cmd(cwd: Path, force: bool, mode: str | None, tier: str | None, pr_number: int | None) -> None:
    cfg = _cfg()
    if mode:
        cfg.dream.mode = mode
    if tier == "l2" and pr_number is None:
        raise click.UsageError("--tier l2 requires --pr <number>")
    result = DreamPipeline(cwd, config=cfg).run(force=force)
    output = {
        "status": result.status,
        "added": result.added,
        "pruned": result.pruned,
        "findings": result.findings,
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
def view_cmd(cwd: Path, min_strength: int, fact_id: str | None, list_only: bool) -> None:
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
    repo = project_memory_dir(cwd)
    state = read_dream_state(repo)
    facts = load_all_facts(repo, include_superseded=False) if repo.exists() else []
    cfg = _cfg()
    metrics = compute_metrics(repo, cfg)
    hot_metric = metrics["hot_tier_utilisation"]["value"]
    hot_tokens = int(round(hot_metric * cfg.memory.hot_tier_max_tokens))
    output = {
        "slug": discover_project_slug(cwd),
        "repo": str(repo),
        "facts": len(facts),
        "tombstones": len(load_tombstones(repo)) if repo.exists() else 0,
        "session_count": state.get("session_count", 0),
        "last_dream": state.get("last_dream"),
        "hot_tier_tokens": hot_tokens,
        "hot_tier_max": cfg.memory.hot_tier_max_tokens,
        "hot_tier_pct": int(round(hot_metric * 100)),
        "metrics": metrics,
    }
    click.echo(json.dumps(output, sort_keys=True))


@main.command("health")
@click.option("--cwd", type=click.Path(path_type=Path), default=Path.cwd)
def health_cmd(cwd: Path) -> None:
    repo = project_memory_dir(cwd)
    metrics = compute_metrics(repo, _cfg())
    flags = health_flags(metrics)
    click.echo(
        json.dumps(
            {
                "repo": str(repo),
                "ok": len(flags) == 0,
                "flags": flags,
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
def gaps_cmd(cwd: Path) -> None:
    path = project_memory_dir(cwd) / "meta" / "gaps.jsonl"
    click.echo(path.read_text() if path.exists() else "", nl=False)


@main.command("forget")
@click.option("--cwd", type=click.Path(path_type=Path), default=Path.cwd)
@click.option("--fact", "fact_id", default=None)
@click.option("--topic", default=None)
def forget_cmd(cwd: Path, fact_id: str | None, topic: str | None) -> None:
    repo = project_memory_dir(cwd)
    if fact_id:
        removed = forget_fact(repo, fact_id)
        if removed:
            _commit_repo(repo, f"umx: forget {removed.fact_id}")
        click.echo(removed.fact_id if removed else "")
        return
    if topic:
        removed = forget_topic(repo, topic)
        if removed:
            _commit_repo(repo, f"umx: forget topic {topic}")
        click.echo(str(len(removed)))
        return
    raise click.UsageError("pass --fact or --topic")


@main.command("promote")
@click.option("--cwd", type=click.Path(path_type=Path), default=Path.cwd)
@click.option("--fact", "fact_id", required=True)
@click.option("--to", "destination", required=True)
def promote_cmd(cwd: Path, fact_id: str, destination: str) -> None:
    repo = project_memory_dir(cwd)
    fact = remove_fact(repo, fact_id)
    if not fact:
        raise click.ClickException(f"fact not found: {fact_id}")
    if destination == "user":
        target_repo = user_memory_dir()
        target_repo.mkdir(parents=True, exist_ok=True)
        add_path = target_repo / "facts" / "topics" / f"{fact.topic}.md"
        from umx.memory import add_fact

        add_fact(target_repo, fact.clone(scope=Scope.USER, file_path=add_path, repo=target_repo.name))
        _commit_repo(repo, f"umx: promote {fact.fact_id} to user")
        click.echo(f"{fact.fact_id} -> user")
        return
    raise click.ClickException(f"unsupported destination: {destination}")


@main.command("confirm")
@click.option("--cwd", type=click.Path(path_type=Path), default=Path.cwd)
@click.option("--fact", "fact_id", required=True)
def confirm_cmd(cwd: Path, fact_id: str) -> None:
    repo = project_memory_dir(cwd)
    fact = find_fact_by_id(repo, fact_id)
    if not fact:
        raise click.ClickException(f"fact not found: {fact_id}")
    updated = fact.clone(
        encoding_strength=5,
        verification=Verification.HUMAN_CONFIRMED,
        consolidation_status=ConsolidationStatus.STABLE,
    )
    replace_fact(repo, updated)
    _commit_repo(repo, f"umx: confirm {updated.fact_id}")
    click.echo(updated.fact_id)


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
    for fact in open_tasks(load_all_facts(repo, include_superseded=False), include_abandoned=include_abandoned):
        click.echo(f"{fact.fact_id} [{fact.task_status.value}] {fact.text}")


@main.command("meta")
@click.option("--cwd", type=click.Path(path_type=Path), default=Path.cwd)
@click.option("--topic", required=True)
def meta_cmd(cwd: Path, topic: str) -> None:
    path = manifest_path(project_memory_dir(cwd))
    data = json.loads(path.read_text()) if path.exists() else {"topics": {}}
    click.echo(json.dumps(data.get("topics", {}).get(topic, {}), sort_keys=True))


@main.command("merge")
@click.option("--cwd", type=click.Path(path_type=Path), default=Path.cwd)
@click.option("--dry-run", is_flag=True, default=False)
def merge_cmd(cwd: Path, dry_run: bool) -> None:
    from umx.merge import merge_all

    repo = project_memory_dir(cwd)
    results = merge_all(repo, _cfg(), dry_run=dry_run)
    if not dry_run and results:
        _commit_repo(repo, "umx: merge conflicts")
    click.echo(json.dumps(results, sort_keys=True))


@main.command("audit")
@click.option("--cwd", type=click.Path(path_type=Path), default=Path.cwd)
@click.option("--rederive", is_flag=True, default=False)
@click.option("--session", "session_ids", multiple=True)
def audit_cmd(cwd: Path, rederive: bool, session_ids: tuple[str]) -> None:
    from umx.audit import audit_report, compare_derived, rederive_from_sessions

    repo = project_memory_dir(cwd)
    cfg = _cfg()
    if rederive:
        ids = list(session_ids) if session_ids else None
        rederived = rederive_from_sessions(repo, session_ids=ids, config=cfg)
        existing = load_all_facts(repo, include_superseded=False)
        comparison = compare_derived(existing, rederived)
        click.echo(json.dumps(comparison, sort_keys=True))
    else:
        report = audit_report(repo, cfg)
        click.echo(json.dumps(report, sort_keys=True))


@main.command("sync")
@click.option("--cwd", type=click.Path(path_type=Path), default=Path.cwd)
def sync_cmd(cwd: Path) -> None:
    cfg = _cfg()
    mode = cfg.dream.mode
    if mode == "local":
        click.echo("local mode: nothing to sync")
        return

    from umx.git_ops import git_fetch, git_pull_rebase, git_push, git_remote_url

    repo = project_memory_dir(cwd)
    remote = git_remote_url(repo)
    if not remote:
        click.echo("no remote configured; run 'umx setup-remote' first")
        return

    if not git_fetch(repo):
        click.echo("fetch failed")
        return
    if not git_pull_rebase(repo):
        click.echo("pull --rebase failed")
        return
    if not git_push(repo):
        click.echo("push failed")
        return
    click.echo(f"synced with {remote}")


@main.command("setup-remote")
@click.option("--cwd", type=click.Path(path_type=Path), default=Path.cwd)
@click.option("--mode", "new_mode", type=click.Choice(["remote", "hybrid"]), default="hybrid")
def setup_remote_cmd(cwd: Path, new_mode: str) -> None:
    """Connect an existing project to a GitHub memory repo."""
    cfg = _cfg()
    if not cfg.org:
        raise click.ClickException("no org configured; run 'umx init --org <org> --mode remote' first")

    from umx.github_ops import gh_available, ensure_repo, set_remote, deploy_workflows

    if not gh_available():
        raise click.ClickException("gh CLI not available")

    root = find_project_root(cwd)
    repo = project_memory_dir(root)
    slug = discover_project_slug(root)

    url = ensure_repo(cfg.org, slug, private=True)
    set_remote(repo, url)

    if new_mode == "remote":
        deploy_workflows(repo)

    cfg.dream.mode = new_mode
    save_config(config_path(), cfg)

    _bootstrap_remote_repo(repo, "umx: bootstrap remote project memory")

    click.echo(f"mode: {new_mode}")
    click.echo(f"remote: {url}")


@main.command("purge")
@click.option("--cwd", type=click.Path(path_type=Path), default=Path.cwd)
@click.option("--session", "session_id", required=True)
@click.option("--dry-run", is_flag=True, default=False)
def purge_cmd(cwd: Path, session_id: str, dry_run: bool) -> None:
    from umx.purge import purge_session

    repo = project_memory_dir(cwd)
    if dry_run:
        from umx.memory import iter_fact_files, read_fact_file

        count = 0
        for path in iter_fact_files(repo):
            for fact in read_fact_file(path, repo_dir=repo):
                if fact.source_session == session_id or session_id in fact.provenance.sessions:
                    count += 1
        click.echo(json.dumps({"dry_run": True, "facts_would_remove": count}, sort_keys=True))
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
    rebuild_index(repo, with_embeddings=embeddings, config=_cfg())
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
    old = repo / old_path
    new = repo / new_path
    new.parent.mkdir(parents=True, exist_ok=True)
    old.rename(new)
    _commit_repo(repo, f"umx: migrate scope {old_path} -> {new_path}")
    click.echo(str(new))


@main.command("doctor")
def doctor_cmd() -> None:
    click.echo(json.dumps(run_doctor(), sort_keys=True))


@main.group("shim")
def shim_group() -> None:
    """Wrapper shims for tool integration."""


@main.group("bridge")
def bridge_group() -> None:
    """Legacy project-repo bridge operations."""


@bridge_group.command("sync")
@click.option("--cwd", type=click.Path(path_type=Path), default=Path.cwd)
@click.option("--target", "targets", multiple=True)
def bridge_sync_cmd(cwd: Path, targets: tuple[str]) -> None:
    from umx.bridge import write_bridge

    root = find_project_root(cwd)
    repo = project_memory_dir(cwd)
    written = write_bridge(root, repo, config=_cfg(), target_files=list(targets) or None)
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
def bridge_import_cmd(cwd: Path, targets: tuple[str], topic: str, dry_run: bool) -> None:
    from umx.bridge import import_bridge_facts
    from umx.memory import add_fact

    root = find_project_root(cwd)
    repo = project_memory_dir(cwd)
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
    click.echo(json.dumps({"dry_run": dry_run, "imported": len(imported)}, sort_keys=True))


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
def shim_generic_cmd(cwd: Path, tool: str | None, output: Path | None, max_tokens: int) -> None:
    from umx.shim.generic import generate_prompt, write_context_file

    if output:
        path = write_context_file(cwd, output_path=output, tool=tool, max_tokens=max_tokens)
        click.echo(str(path))
    else:
        click.echo(generate_prompt(cwd, tool=tool, max_tokens=max_tokens), nl=False)


@main.group("secret")
def secret_group() -> None:
    """Secret operations."""


@secret_group.command("get")
@click.argument("key")
def secret_get(key: str) -> None:
    if "/" in key or "\\" in key or key.startswith("."):
        raise click.ClickException("Invalid secret key: must not contain path separators or start with '.'")
    path = get_umx_home() / "user" / "local" / "secret" / key
    click.echo(path.read_text() if path.exists() else "", nl=False)


@secret_group.command("set")
@click.argument("key")
@click.argument("value")
def secret_set(key: str, value: str) -> None:
    if "/" in key or "\\" in key or key.startswith("."):
        raise click.ClickException("Invalid secret key: must not contain path separators or start with '.'")
    path = get_umx_home() / "user" / "local" / "secret" / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value)
    click.echo(key)


@main.command("import")
@click.option("--cwd", type=click.Path(path_type=Path), default=Path.cwd)
@click.option("--adapter", type=click.Choice(["claude-code", "copilot", "aider", "generic"]), required=True)
@click.option("--dry-run", is_flag=True, default=False)
def import_cmd(cwd: Path, adapter: str, dry_run: bool) -> None:
    from umx.adapters import get_adapter_by_name
    from umx.memory import add_fact

    root = find_project_root(cwd)
    repo = project_memory_dir(cwd)
    adapter_inst = get_adapter_by_name(adapter)
    facts = adapter_inst.read_native_memory(root)
    if dry_run:
        click.echo(json.dumps({"dry_run": True, "facts_found": len(facts)}, sort_keys=True))
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

    click.echo(json.dumps(capture_codex_rollout(cwd, target, config=_cfg()), sort_keys=True))


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

    click.echo(json.dumps(capture_copilot_session(cwd, target, config=_cfg()), sort_keys=True))


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
    )

    root = find_project_root(cwd)
    if session_file:
        targets = [session_file]
    elif capture_all:
        targets = list_claude_code_sessions(project_root=root, source_root=source_root)
        if not targets:
            raise click.ClickException("No Claude Code session files found for this project.")
    else:
        target = latest_claude_code_session_path(project_root=root, source_root=source_root)
        if target is None:
            raise click.ClickException("No Claude Code session files found for this project.")
        targets = [target]

    if dry_run:
        results = []
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
    for path in targets:
        if not path.exists():
            raise click.ClickException(f"Session file not found: {path}")
        results.append(capture_claude_code_session(cwd, path, config=_cfg()))
    if results:
        from umx.git_ops import git_add_and_commit
        repo = project_memory_dir(root)
        git_add_and_commit(repo, message="umx: capture claude-code sessions")
    click.echo(json.dumps(results if capture_all else results[0], sort_keys=True))


@main.command("search")
@click.option("--cwd", type=click.Path(path_type=Path), default=Path.cwd)
@click.option("--raw", is_flag=True, default=False, help="Search raw session files instead of the fact index.")
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
        for fact in query_index(repo, query):
            click.echo(f"{fact.fact_id} [{fact.topic}] {fact.text}")


if __name__ == "__main__":
    main()
