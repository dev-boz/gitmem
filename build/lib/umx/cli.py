"""CLI for umx — Universal Memory Exchange.

Commands:
  umx init       Initialize .umx/ in current project
  umx inject     Inject memory context for a tool
  umx collect    Collect tool memory after session
  umx dream      Run the dream pipeline
  umx view       View memory facts
  umx status     Show memory status
  umx conflicts  Show conflict entries
  umx forget     Remove a topic
  umx promote    Move a fact to a different scope
  umx add        Add a fact manually
"""

from __future__ import annotations

import sys
from pathlib import Path

import click

from umx import __version__


@click.group()
@click.version_option(version=__version__, prog_name="umx")
def main() -> None:
    """umx — Universal Memory Exchange.

    Tool-agnostic, filesystem-native memory for CLI agents.
    """
    pass


@main.command()
@click.option("--cwd", default=".", help="Project root directory.")
def init(cwd: str) -> None:
    """Initialize .umx/ structure in a project."""
    from umx.scope import init_project

    project_root = Path(cwd).resolve()
    init_project(project_root)

    # Create default config
    from umx.memory import save_config
    from umx.models import UmxConfig

    umx_dir = project_root / ".umx"
    save_config(umx_dir, UmxConfig())

    click.echo(f"Initialized .umx/ in {project_root}")


@main.command()
@click.option("--cwd", default=".", help="Working directory.")
@click.option("--tool", required=True, help="Tool name (aider, claude-code, etc).")
@click.option("--max-tokens", type=int, default=None, help="Context budget in tokens.")
def inject(cwd: str, tool: str, max_tokens: int | None) -> None:
    """Inject memory context for a tool."""
    from umx.inject import inject_for_tool

    cwd_path = Path(cwd).resolve()
    content = inject_for_tool(cwd_path, tool=tool, max_tokens=max_tokens)

    if content:
        click.echo(content)
    else:
        click.echo("No memory facts to inject.", err=True)


@main.command()
@click.option("--cwd", default=".", help="Working directory.")
@click.option("--tool", required=True, help="Tool name.")
def collect(cwd: str, tool: str) -> None:
    """Collect tool memory after session end."""
    from umx.hooks.session_end import on_session_end

    cwd_path = Path(cwd).resolve()
    result = on_session_end(cwd_path, tool=tool)

    click.echo(f"Session collected for {result['tool']}")
    click.echo(f"  Sessions since last dream: {result['session_count']}")
    if result["dream_triggered"]:
        click.echo("  Dream pipeline triggered ✓")


@main.command()
@click.option("--cwd", default=".", help="Project root directory.")
@click.option("--force", is_flag=True, help="Bypass time and session gates.")
def dream(cwd: str, force: bool) -> None:
    """Run the dream pipeline (Orient → Gather → Consolidate → Prune)."""
    from umx.dream.pipeline import DreamPipeline
    from umx.memory import load_config

    project_root = Path(cwd).resolve()
    umx_dir = project_root / ".umx"

    if not umx_dir.exists():
        click.echo("No .umx/ found. Run `umx init` first.", err=True)
        sys.exit(1)

    config = load_config(umx_dir)
    pipeline = DreamPipeline(project_root, config=config, force=force)
    status = pipeline.run()

    click.echo(f"Dream complete: {status.value}")
    click.echo(f"  New facts:     {len(pipeline.new_facts)}")
    click.echo(f"  Removed facts: {len(pipeline.removed_facts)}")
    click.echo(f"  Conflicts:     {len(pipeline.conflicts)}")
    if pipeline.skipped_sources:
        click.echo(f"  Skipped:       {', '.join(pipeline.skipped_sources)}")


@main.command()
@click.option("--cwd", default=".", help="Working directory.")
@click.option("--scope", default=None, help="Filter by scope (project, user, etc).")
@click.option("--min-strength", type=int, default=None, help="Minimum encoding strength.")
@click.option("--topic", default=None, help="Filter by topic.")
def view(
    cwd: str,
    scope: str | None,
    min_strength: int | None,
    topic: str | None,
) -> None:
    """View memory facts."""
    from umx.memory import load_all_facts
    from umx.models import Scope
    from umx.scope import resolve_scopes, iter_existing_layers

    cwd_path = Path(cwd).resolve()
    layers = resolve_scopes(cwd_path)

    all_facts = []
    for layer in iter_existing_layers(layers):
        facts = load_all_facts(layer.path, layer.scope)
        all_facts.extend(facts)

    # Filter
    if scope:
        try:
            scope_enum = Scope(scope)
            all_facts = [f for f in all_facts if f.scope == scope_enum]
        except ValueError:
            click.echo(f"Unknown scope: {scope}", err=True)
            sys.exit(1)

    if min_strength is not None:
        all_facts = [f for f in all_facts if f.encoding_strength >= min_strength]

    if topic:
        all_facts = [f for f in all_facts if f.topic == topic]

    if not all_facts:
        click.echo("No facts found.")
        return

    # Group by topic
    topics: dict[str, list] = {}
    for fact in sorted(all_facts, key=lambda f: (-f.encoding_strength, f.topic)):
        topics.setdefault(fact.topic, []).append(fact)

    for topic_name, facts in topics.items():
        click.echo(f"\n## {topic_name}")
        for f in facts:
            corr = f" (+{','.join(f.corroborated_by)})" if f.corroborated_by else ""
            click.echo(
                f"  [S:{f.encoding_strength}] {f.text} "
                f"(conf:{f.confidence:.2f}, {f.source_tool or '?'}{corr})"
            )


@main.command()
@click.option("--cwd", default=".", help="Working directory.")
def status(cwd: str) -> None:
    """Show memory status."""
    from umx.dream.notice import read_notice
    from umx.memory import load_all_facts, read_memory_md
    from umx.models import Scope
    from umx.scope import find_project_root

    cwd_path = Path(cwd).resolve()
    project_root = find_project_root(cwd_path)

    if not project_root:
        click.echo("No project found (no .git or .umx directory).", err=True)
        sys.exit(1)

    umx_dir = project_root / ".umx"
    if not umx_dir.exists():
        click.echo("No .umx/ found. Run `umx init` first.", err=True)
        sys.exit(1)

    # Count facts
    team_facts = load_all_facts(umx_dir, Scope.PROJECT_TEAM)
    local_dir = umx_dir / "local"
    local_facts = load_all_facts(local_dir, Scope.PROJECT_LOCAL) if local_dir.exists() else []

    click.echo(f"umx status for {project_root}")
    click.echo(f"  Team facts:  {len(team_facts)}")
    click.echo(f"  Local facts: {len(local_facts)}")
    click.echo(f"  Total:       {len(team_facts) + len(local_facts)}")

    # Strength distribution
    if team_facts or local_facts:
        all_facts = team_facts + local_facts
        dist = {}
        for f in all_facts:
            dist[f.encoding_strength] = dist.get(f.encoding_strength, 0) + 1
        click.echo("  Strength distribution:")
        for s in sorted(dist.keys(), reverse=True):
            click.echo(f"    S:{s} → {dist[s]}")

    # Memory.md
    memory = read_memory_md(umx_dir)
    if memory:
        for line in memory.splitlines()[:5]:
            if line.startswith("last_dream:") or line.startswith("session_count:"):
                click.echo(f"  {line.strip()}")

    # Notice
    notice = read_notice(umx_dir)
    if notice:
        click.echo(f"\n⚠️  {notice}")


@main.command()
@click.option("--cwd", default=".", help="Working directory.")
def conflicts(cwd: str) -> None:
    """Show conflict entries."""
    from umx.dream.conflict import load_conflicts

    cwd_path = Path(cwd).resolve()
    umx_dir = cwd_path / ".umx"

    if not umx_dir.exists():
        click.echo("No .umx/ found.", err=True)
        sys.exit(1)

    entries = load_conflicts(umx_dir)
    if not entries:
        click.echo("No conflicts.")
        return

    for entry in entries:
        status_str = entry.get("status", "?")
        desc = entry.get("description", "")
        click.echo(f"\n[{status_str}] {desc}")
        for line in entry.get("lines", []):
            click.echo(f"  {line}")


@main.command()
@click.option("--cwd", default=".", help="Working directory.")
@click.option("--topic", required=True, help="Topic to forget.")
def forget(cwd: str, topic: str) -> None:
    """Remove a topic and all its facts."""
    cwd_path = Path(cwd).resolve()
    umx_dir = cwd_path / ".umx"
    topics_dir = umx_dir / "topics"

    topic_path = topics_dir / f"{topic}.md"
    json_path = topics_dir / f"{topic}.umx.json"

    removed = False
    if topic_path.exists():
        topic_path.unlink()
        removed = True
    if json_path.exists():
        json_path.unlink()
        removed = True

    if removed:
        click.echo(f"Forgot topic: {topic}")
    else:
        click.echo(f"Topic not found: {topic}", err=True)


@main.command()
@click.option("--cwd", default=".", help="Working directory.")
@click.option("--fact", "fact_id", required=True, help="Fact ID to promote.")
@click.option("--to", "target", required=True, help="Target scope (project, user).")
def promote(cwd: str, fact_id: str, target: str) -> None:
    """Move a fact to a different scope."""
    from umx.memory import add_fact, find_fact_by_id, remove_fact
    from umx.models import Scope

    cwd_path = Path(cwd).resolve()

    try:
        target_scope = Scope(target)
    except ValueError:
        click.echo(f"Unknown scope: {target}", err=True)
        sys.exit(1)

    # Find the fact
    umx_dir = cwd_path / ".umx"
    fact = find_fact_by_id(umx_dir, fact_id, Scope.PROJECT_TEAM)
    if not fact:
        local_dir = umx_dir / "local"
        fact = find_fact_by_id(local_dir, fact_id, Scope.PROJECT_LOCAL)

    if not fact:
        click.echo(f"Fact not found: {fact_id}", err=True)
        sys.exit(1)

    old_scope = fact.scope
    remove_fact(umx_dir, fact_id, fact.topic, old_scope)
    fact.scope = target_scope

    # Determine target directory
    if target_scope == Scope.USER:
        from umx.scope import user_scope_dir
        target_dir = user_scope_dir()
    elif target_scope == Scope.PROJECT_LOCAL:
        target_dir = umx_dir / "local"
    else:
        target_dir = umx_dir

    add_fact(target_dir, fact)
    click.echo(f"Promoted {fact_id} from {old_scope.value} → {target_scope.value}")


@main.command()
@click.option("--cwd", default=".", help="Working directory.")
@click.option("--text", required=True, help="Fact text.")
@click.option("--topic", default="general", help="Topic name.")
@click.option("--scope", default="project_team", help="Scope.")
@click.option("--tags", default="", help="Comma-separated tags.")
def add(cwd: str, text: str, topic: str, scope: str, tags: str) -> None:
    """Add a fact manually (strength 5 — ground truth)."""
    from umx.memory import add_fact as _add_fact
    from umx.models import Fact, MemoryType, Scope

    cwd_path = Path(cwd).resolve()
    umx_dir = cwd_path / ".umx"

    try:
        scope_enum = Scope(scope)
    except ValueError:
        click.echo(f"Unknown scope: {scope}", err=True)
        sys.exit(1)

    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []

    fact = Fact(
        id=Fact.generate_id(),
        text=text,
        scope=scope_enum,
        topic=topic,
        encoding_strength=5,
        memory_type=MemoryType.EXPLICIT_SEMANTIC,
        confidence=1.0,
        tags=tag_list,
        source_tool="manual",
    )

    if scope_enum == Scope.PROJECT_LOCAL:
        target_dir = umx_dir / "local"
    elif scope_enum == Scope.USER:
        from umx.scope import user_scope_dir
        target_dir = user_scope_dir()
    else:
        target_dir = umx_dir

    _add_fact(target_dir, fact)
    click.echo(f"Added fact {fact.id}: {text}")


if __name__ == "__main__":
    main()
