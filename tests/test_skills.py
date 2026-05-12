from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

import umx.inject as inject
import umx.search as search

from umx.cli import main
from umx.config import default_config, save_config
from umx.inject import build_injection_block
from umx.identity import generate_fact_id
from umx.memory import add_fact
from umx.models import (
    ConsolidationStatus,
    Fact,
    MemoryType,
    Scope,
    SourceType,
    Verification,
)
from umx.scope import config_path, ensure_repo_structure
from umx.skills import RetrievalDirectiveKind, SkillStatus, read_skill_file, resolve_skill
from umx.tombstones import forget_fact


def make_fact(
    text: str,
    *,
    topic: str = "database",
    scope: Scope = Scope.PROJECT,
    fact_id: str | None = None,
) -> Fact:
    return Fact(
        fact_id=fact_id or generate_fact_id(),
        text=text,
        scope=scope,
        topic=topic,
        encoding_strength=4,
        memory_type=MemoryType.EXPLICIT_SEMANTIC,
        verification=Verification.CORROBORATED,
        source_type=SourceType.GROUND_TRUTH_CODE,
        source_tool="test",
        source_session="skill-test",
        consolidation_status=ConsolidationStatus.STABLE,
    )


def write_skill(repo: Path, name: str, body: str) -> Path:
    path = repo / "skills" / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    return path


def test_read_skill_file_parses_top_level_fields(project_repo: Path) -> None:
    path = write_skill(
        project_repo,
        "database-debug",
        "# Database Debug\n\n"
        "name: database-debug\n"
        "version: 2\n"
        "skill_status: active\n\n"
        "## Triggers\n"
        "- pattern: `postgres`\n\n"
        "## Retrieval\n"
        "- load: `facts/topics/database.md`\n",
    )

    skill = read_skill_file(path, repo_dir=project_repo)[0]

    assert skill.name == "database-debug"
    assert skill.version == "2"
    assert skill.skill_status == SkillStatus.ACTIVE
    assert skill.title == "Database Debug"


def test_read_skill_file_parses_inline_metadata_fallback(project_repo: Path) -> None:
    path = write_skill(
        project_repo,
        "database-debug",
        "# Database Debug\n\n"
        "<!-- umx:{\"id\":\"SKILL001\",\"name\":\"database-debug\",\"v\":\"7\",\"ss\":\"retired\"} -->\n\n"
        "## Retrieval\n"
        "- load: `facts/topics/database.md`\n",
    )

    skill = read_skill_file(path, repo_dir=project_repo)[0]

    assert skill.skill_id == "SKILL001"
    assert skill.name == "database-debug"
    assert skill.version == "7"
    assert skill.skill_status == SkillStatus.RETIRED


def test_read_skill_file_parses_load_and_hint(project_repo: Path) -> None:
    path = write_skill(
        project_repo,
        "database-debug",
        "# Database Debug\n\n"
        "name: database-debug\n\n"
        "## Retrieval\n"
        "- load: `facts/topics/database.md`\n"
        "- hint: Check database port facts before assuming 5432.\n",
    )

    skill = read_skill_file(path, repo_dir=project_repo)[0]

    assert [directive.kind for directive in skill.directives] == [
        RetrievalDirectiveKind.LOAD,
        RetrievalDirectiveKind.HINT,
    ]
    assert skill.directives[0].value == "facts/topics/database.md"
    assert skill.directives[1].value == "Check database port facts before assuming 5432."


def test_resolve_skill_rejects_unsafe_paths(project_repo: Path) -> None:
    path = write_skill(
        project_repo,
        "database-debug",
        "# Database Debug\n\n"
        "## Retrieval\n"
        "- load: `../facts/topics/database.md`\n"
        "- load: `/etc/passwd`\n"
        "- load: `local/secret/secrets.md`\n",
    )

    resolution = resolve_skill(read_skill_file(path, repo_dir=project_repo)[0], project_repo)

    assert resolution.blocked_paths == [
        "../facts/topics/database.md",
        "/etc/passwd",
        "local/secret/secrets.md",
    ]
    assert resolution.routed_fact_ids == set()


def test_missing_load_path_is_reported_not_fatal(project_repo: Path) -> None:
    path = write_skill(
        project_repo,
        "database-debug",
        "# Database Debug\n\n"
        "## Retrieval\n"
        "- load: `facts/topics/missing.md`\n"
        "- hint: Check database port facts before assuming 5432.\n",
    )

    resolution = resolve_skill(read_skill_file(path, repo_dir=project_repo)[0], project_repo)

    assert resolution.missing_paths == ["facts/topics/missing.md"]
    assert resolution.hints == ["Check database port facts before assuming 5432."]
    assert resolution.directives_resolved == 1


def test_trigger_activation_routes_matching_facts(project_repo: Path, project_dir: Path) -> None:
    fact = make_fact("postgres runs on 5433 in dev")
    add_fact(project_repo, fact, auto_commit=False)
    write_skill(
        project_repo,
        "database-debug",
        "# Database Debug\n\n"
        "name: database-debug\n\n"
        "## Triggers\n"
        "- pattern: `postgres`\n\n"
        "## Retrieval\n"
        "- load: `facts/topics/database.md`\n",
    )

    block = build_injection_block(project_dir, prompt="debug postgres connection")

    assert "postgres runs on 5433 in dev" in block
    assert "## Skills" not in block


def test_explicit_skill_activation_works(project_repo: Path, project_dir: Path) -> None:
    fact = make_fact("postgres runs on 5433 in dev")
    add_fact(project_repo, fact, auto_commit=False)
    write_skill(
        project_repo,
        "database-debug",
        "# Database Debug\n\n"
        "name: database-debug\n\n"
        "## Retrieval\n"
        "- load: `facts/topics/database.md`\n",
    )

    block = build_injection_block(project_dir, prompt="please inspect @skill:database-debug")

    assert "postgres runs on 5433 in dev" in block


def test_skills_can_be_disabled(project_repo: Path, project_dir: Path) -> None:
    cfg = default_config()
    cfg.skills.enabled = False
    save_config(config_path(), cfg)
    write_skill(
        project_repo,
        "database-debug",
        "# Database Debug\n\n"
        "name: database-debug\n\n"
        "## Triggers\n"
        "- pattern: `postgres`\n\n"
        "## Retrieval\n"
        "- hint: Check database port facts before assuming 5432.\n",
    )

    block = build_injection_block(project_dir, prompt="postgres @skill:database-debug")

    assert "## Skill Hints" not in block
    assert "Check database port facts before assuming 5432." not in block


def test_routed_facts_are_deduped_with_normal_retrieval(project_repo: Path, project_dir: Path) -> None:
    fact = make_fact("postgres runs on 5433 in dev")
    add_fact(project_repo, fact, auto_commit=False)
    write_skill(
        project_repo,
        "database-debug",
        "# Database Debug\n\n"
        "name: database-debug\n\n"
        "## Triggers\n"
        "- pattern: `postgres`\n\n"
        "## Retrieval\n"
        "- load: `facts/topics/database.md`\n",
    )

    block = build_injection_block(project_dir, prompt="postgres")

    assert block.count("postgres runs on 5433 in dev") == 1


def test_routed_facts_respect_tombstones(project_repo: Path, project_dir: Path) -> None:
    fact = make_fact("postgres runs on 5433 in dev")
    add_fact(project_repo, fact, auto_commit=False)
    forget_fact(project_repo, fact.fact_id)
    write_skill(
        project_repo,
        "database-debug",
        "# Database Debug\n\n"
        "name: database-debug\n\n"
        "## Triggers\n"
        "- pattern: `postgres`\n\n"
        "## Retrieval\n"
        "- load: `facts/topics/database.md`\n",
    )

    block = build_injection_block(project_dir, prompt="postgres")

    assert "postgres runs on 5433 in dev" not in block


def test_local_secret_facts_are_not_routed(project_repo: Path, project_dir: Path) -> None:
    secret_fact = make_fact(
        "database password is hunter2",
        topic="secrets",
        scope=Scope.PROJECT_SECRET,
    )
    add_fact(project_repo, secret_fact, auto_commit=False)
    path = write_skill(
        project_repo,
        "database-debug",
        "# Database Debug\n\n"
        "name: database-debug\n\n"
        "## Triggers\n"
        "- pattern: `database`\n\n"
        "## Retrieval\n"
        "- load: `local/secret/secrets.md`\n",
    )

    resolution = resolve_skill(read_skill_file(path, repo_dir=project_repo)[0], project_repo)
    block = build_injection_block(project_dir, prompt="database")

    assert "local/secret/secrets.md" in resolution.blocked_paths
    assert "database password is hunter2" not in block


def test_skill_hints_render_under_skill_hints(project_repo: Path, project_dir: Path) -> None:
    fact = make_fact("postgres runs on 5433 in dev")
    add_fact(project_repo, fact, auto_commit=False)
    write_skill(
        project_repo,
        "database-debug",
        "# Database Debug\n\n"
        "name: database-debug\n\n"
        "## Triggers\n"
        "- pattern: `postgres`\n\n"
        "## Retrieval\n"
        "- load: `facts/topics/database.md`\n"
        "- hint: Check database port facts before assuming 5432.\n"
        "- query: database defaults\n",
    )

    block = build_injection_block(project_dir, prompt="postgres")

    assert "## Skill Hints" in block
    assert "Check database port facts before assuming 5432." in block
    assert "## Skills" not in block
    assert "[query:" not in block


def test_record_skill_load_is_called_with_session(
    project_repo: Path,
    project_dir: Path,
    monkeypatch,
) -> None:
    fact = make_fact("postgres runs on 5433 in dev")
    add_fact(project_repo, fact, auto_commit=False)
    write_skill(
        project_repo,
        "database-debug",
        "# Database Debug\n\n"
        "name: database-debug\n\n"
        "## Triggers\n"
        "- pattern: `postgres`\n\n"
        "## Retrieval\n"
        "- load: `facts/topics/database.md`\n",
    )
    calls: list[dict[str, object]] = []

    def fake_record_skill_load(repo_dir: Path, **kwargs) -> int:
        calls.append({"repo_dir": repo_dir, **kwargs})
        return 1

    monkeypatch.setattr(inject, "record_skill_load", fake_record_skill_load)
    monkeypatch.setattr(inject, "record_skill_retrievals", lambda *args, **kwargs: None)

    block = build_injection_block(project_dir, prompt="postgres", session_id="sess-skill-001")

    assert "postgres runs on 5433 in dev" in block
    assert calls
    assert calls[0]["session_id"] == "sess-skill-001"
    assert calls[0]["load_trigger"] == "trigger"


def test_skill_routed_facts_record_normal_usage_events(project_repo: Path, project_dir: Path) -> None:
    fact = make_fact("postgres runs on 5433 in dev")
    add_fact(project_repo, fact, auto_commit=False)
    write_skill(
        project_repo,
        "database-debug",
        "# Database Debug\n\n"
        "name: database-debug\n\n"
        "## Triggers\n"
        "- pattern: `postgres`\n\n"
        "## Retrieval\n"
        "- load: `facts/topics/database.md`\n",
    )

    build_injection_block(project_dir, prompt="postgres", session_id="sess-skill-usage")

    replay = search.session_replay(project_repo, "sess-skill-usage")
    assert any(row["event_kind"] == "inject" and row["fact_id"] == fact.fact_id for row in replay)
    conn = search._connect(search.usage_path(project_repo))
    try:
        rows = conn.execute("SELECT * FROM skill_retrievals").fetchall()
    finally:
        conn.close()
    assert rows
    assert rows[0]["selected_for_injection"] == 1
    assert rows[0]["used_in_output"] == 0

    search.record_reference(project_repo, fact.fact_id, session_id="sess-skill-usage")

    conn = search._connect(search.usage_path(project_repo))
    try:
        rows = conn.execute("SELECT * FROM skill_retrievals").fetchall()
    finally:
        conn.close()
    assert rows[0]["used_in_output"] == 1


def test_skill_test_command_shows_facts_hints_missing_and_blocked_paths(
    project_repo: Path,
    project_dir: Path,
) -> None:
    fact = make_fact("postgres runs on 5433 in dev")
    add_fact(project_repo, fact, auto_commit=False)
    write_skill(
        project_repo,
        "database-debug",
        "# Database Debug\n\n"
        "name: database-debug\n"
        "version: 2\n\n"
        "## Retrieval\n"
        "- load: `facts/topics/database.md`\n"
        "- load: `facts/topics/missing.md`\n"
        "- load: `local/secret/secrets.md`\n"
        "- query: database defaults\n"
        "- hint: Check database port facts before assuming 5432.\n",
    )

    result = CliRunner().invoke(
        main,
        ["skill", "test", "--cwd", str(project_dir), "--name", "database-debug"],
    )

    assert result.exit_code == 0
    assert "Activated skill: database-debug" in result.output
    assert "postgres runs on 5433 in dev" in result.output
    assert "Check database port facts before assuming 5432." in result.output
    assert "facts/topics/missing.md" in result.output
    assert "local/secret/secrets.md" in result.output
    assert "query: database defaults" in result.output
    assert "Estimated tokens:" in result.output


def test_ensure_repo_structure_creates_skills_dir(tmp_path: Path) -> None:
    repo = tmp_path / "repo"

    ensure_repo_structure(repo)

    assert (repo / "skills").is_dir()
