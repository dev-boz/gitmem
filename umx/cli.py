from __future__ import annotations

import json
from pathlib import Path

import click

from umx.config import default_config, load_config, save_config
from umx.doctor import run_doctor
from umx.dream.gates import increment_session_count, read_dream_state
from umx.dream.pipeline import DreamPipeline
from umx.inject import inject_for_tool
from umx.manifest import manifest_path
from umx.memory import find_fact_by_id, load_all_facts, remove_fact, replace_fact
from umx.models import ConsolidationStatus, Scope, SourceType, Verification
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
from umx.search import query_index, rebuild_index, search_sessions
from umx.sessions import generate_session_id
from umx.supersession import walk_history
from umx.tasks import open_tasks
from umx.tombstones import forget_fact, forget_topic, load_tombstones


def _cfg():
    return load_config(config_path())


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
def init_cmd(org: str | None) -> None:
    config = default_config()
    config.org = org
    home = init_local_umx(org)
    save_config(config_path(), config)
    click.echo(str(home))


@main.command("init-project")
@click.option("--cwd", type=click.Path(path_type=Path), default=Path.cwd)
@click.option("--slug", default=None)
def init_project_cmd(cwd: Path, slug: str | None) -> None:
    root = find_project_root(cwd)
    if slug:
        (root / ".umx-project").write_text(f"{slug}\n")
    repo = init_project_memory(root, write_marker=True)
    click.echo(str(repo))


@main.command("inject")
@click.option("--cwd", type=click.Path(path_type=Path), default=Path.cwd)
@click.option("--tool", default=None)
@click.option("--prompt", default=None)
@click.option("--file", "files", multiple=True)
@click.option("--max-tokens", type=int, default=4000)
def inject_cmd(cwd: Path, tool: str | None, prompt: str | None, files: tuple[str], max_tokens: int) -> None:
    click.echo(
        inject_for_tool(
            cwd,
            tool=tool,
            prompt=prompt,
            file_paths=list(files),
            max_tokens=max_tokens,
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
def view_cmd(cwd: Path, min_strength: int) -> None:
    repo = project_memory_dir(cwd)
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
    view_cmd.callback(cwd=cwd, min_strength=1)


@main.command("status")
@click.option("--cwd", type=click.Path(path_type=Path), default=Path.cwd)
def status_cmd(cwd: Path) -> None:
    repo = project_memory_dir(cwd)
    state = read_dream_state(repo)
    facts = load_all_facts(repo, include_superseded=False) if repo.exists() else []
    cfg = _cfg()
    max_tokens = cfg.memory.hot_tier_max_tokens
    used_tokens = sum(max(1, (len(f.text) + 3) // 4) for f in facts)
    pct = int(round(used_tokens / max_tokens * 100)) if max_tokens > 0 else 0
    output = {
        "slug": discover_project_slug(cwd),
        "repo": str(repo),
        "facts": len(facts),
        "tombstones": len(load_tombstones(repo)) if repo.exists() else 0,
        "session_count": state.get("session_count", 0),
        "last_dream": state.get("last_dream"),
        "hot_tier_tokens": used_tokens,
        "hot_tier_max": max_tokens,
        "hot_tier_pct": pct,
    }
    click.echo(json.dumps(output, sort_keys=True))
    if pct > 90:
        click.echo(f"\u26a0 Hot tier near capacity")


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
        click.echo(removed.fact_id if removed else "")
        return
    if topic:
        removed = forget_topic(repo, topic)
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
        click.echo("no remote configured")
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
        click.echo(json.dumps(result, sort_keys=True))


@main.command("rebuild-index")
@click.option("--cwd", type=click.Path(path_type=Path), default=Path.cwd)
def rebuild_index_cmd(cwd: Path) -> None:
    repo = project_memory_dir(cwd)
    rebuild_index(repo)
    click.echo(str(repo / "meta" / "index.sqlite"))


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
    click.echo(str(new))


@main.command("doctor")
def doctor_cmd() -> None:
    click.echo(json.dumps(run_doctor(), sort_keys=True))


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
        click.echo(json.dumps({"imported": len(facts)}, sort_keys=True))


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
