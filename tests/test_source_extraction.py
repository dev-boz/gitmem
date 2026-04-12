from __future__ import annotations

import json
from pathlib import Path

from umx.conventions import (
    ConventionSet,
    apply_conventions_to_fact,
    normalize_entity,
)
from umx.dream.extract import (
    extract_file_references,
    source_files_to_facts,
)
from umx.dream.pipeline import DreamPipeline
from umx.inject import emit_gap_signal
from umx.memory import load_all_facts
from umx.models import (
    ConsolidationStatus,
    Fact,
    MemoryType,
    Scope,
    SourceType,
    Verification,
    Provenance,
)
from umx.sessions import write_session


def test_extract_file_references_from_session() -> None:
    events = [
        {"role": "user", "content": "Can you check src/main.py?"},
        {
            "role": "assistant",
            "content": "I read src/main.py and found the config in config/settings.yaml",
        },
        {
            "role": "tool_result",
            "content": "File content from lib/utils.py:\ndef helper(): pass",
        },
    ]
    refs = extract_file_references(events)
    assert "src/main.py" in refs
    assert "config/settings.yaml" in refs
    assert "lib/utils.py" in refs


def test_extract_file_references_ignores_pycache() -> None:
    events = [
        {"role": "assistant", "content": "Found __pycache__/module.cpython-311.pyc"},
    ]
    refs = extract_file_references(events)
    assert len(refs) == 0


def test_extract_file_references_ignores_assistant_status_mentions_without_read_evidence() -> None:
    events = [
        {
            "role": "assistant",
            "content": (
                "Updated umx/codex_capture.py and tests/test_codex_capture.py. "
                "README.md also changed."
            ),
        },
        {
            "role": "assistant",
            "content": "I read docs/plan.md and found the key constraints in config/settings.yaml.",
        },
    ]

    refs = extract_file_references(events)

    assert "docs/plan.md" in refs
    assert "config/settings.yaml" in refs
    assert "umx/codex_capture.py" not in refs
    assert "tests/test_codex_capture.py" not in refs
    assert "README.md" not in refs


def test_source_files_to_facts_python(
    project_dir: Path, project_repo: Path
) -> None:
    # Create a Python source file in the project
    src_dir = project_dir / "src"
    src_dir.mkdir()
    (src_dir / "app.py").write_text(
        '"""The application server handles HTTP requests."""\n'
        "import flask\n"
        "from redis import Redis\n"
        "\n"
        'APP_NAME = "myapp"\n'
        "DEFAULT_PORT = 8080\n"
        "# TODO: add authentication middleware\n"
        'DB_URL = "https://db.example.com/main"\n'
    )

    # Create a session referencing that file
    write_session(
        project_repo,
        meta={"session_id": "2026-02-01-src1"},
        events=[
            {"role": "user", "content": "Check src/app.py"},
            {"role": "assistant", "content": "I read src/app.py and found the config."},
        ],
    )

    from umx.sessions import list_sessions

    session_paths = list_sessions(project_repo)
    facts = source_files_to_facts(project_repo, project_dir, session_paths)

    assert len(facts) >= 1
    texts = [f.text for f in facts]
    # Should find imports
    assert any("flask" in t for t in texts)
    # Should find constants
    assert any("APP_NAME" in t for t in texts)
    # Should find TODO
    assert any("TODO" in t and "authentication" in t for t in texts)

    for fact in facts:
        assert fact.source_type == SourceType.GROUND_TRUTH_CODE
        assert fact.encoding_strength == 3
        assert fact.confidence == 0.9
        assert fact.consolidation_status == ConsolidationStatus.STABLE
        assert fact.code_anchor is not None
        assert fact.code_anchor.path == "src/app.py"


def test_source_files_to_facts_config_yaml(
    project_dir: Path, project_repo: Path
) -> None:
    cfg_dir = project_dir / "config"
    cfg_dir.mkdir()
    (cfg_dir / "database.yaml").write_text(
        "host: localhost\n"
        "port: 5432\n"
        "name: mydb\n"
    )

    write_session(
        project_repo,
        meta={"session_id": "2026-02-02-cfg1"},
        events=[
            {"role": "assistant", "content": "The config is at config/database.yaml"},
        ],
    )

    from umx.sessions import list_sessions

    session_paths = list_sessions(project_repo)
    facts = source_files_to_facts(project_repo, project_dir, session_paths)

    texts = [f.text for f in facts]
    assert any("port" in t.lower() and "5432" in t for t in texts)
    assert any("host" in t.lower() and "localhost" in t for t in texts)


def test_source_files_to_facts_skips_binary(
    project_dir: Path, project_repo: Path
) -> None:
    (project_dir / "image.py").write_bytes(b"\x89PNG\r\n\x00\x00binary content")

    write_session(
        project_repo,
        meta={"session_id": "2026-02-03-bin1"},
        events=[
            {"role": "assistant", "content": "Found image.py in the project"},
        ],
    )

    from umx.sessions import list_sessions

    session_paths = list_sessions(project_repo)
    facts = source_files_to_facts(project_repo, project_dir, session_paths)

    assert all("image.py" not in f.text for f in facts)


def test_source_files_to_facts_skips_large(
    project_dir: Path, project_repo: Path
) -> None:
    (project_dir / "big.py").write_text("x = 1\n" * 20_000)  # >100KB

    write_session(
        project_repo,
        meta={"session_id": "2026-02-04-big1"},
        events=[
            {"role": "assistant", "content": "Check big.py for the constant"},
        ],
    )

    from umx.sessions import list_sessions

    session_paths = list_sessions(project_repo)
    facts = source_files_to_facts(project_repo, project_dir, session_paths)

    assert all("big.py" not in (f.code_anchor.path if f.code_anchor else "") for f in facts)


def test_source_files_to_facts_markdown_is_external_doc_and_fragile(
    project_dir: Path, project_repo: Path
) -> None:
    docs_dir = project_dir / "docs"
    docs_dir.mkdir()
    (docs_dir / "plan.md").write_text(
        "# Plan\n\n"
        "- The backup worker runs every hour\n"
        "- Deploys run through staging first\n"
    )

    write_session(
        project_repo,
        meta={"session_id": "2026-02-05-doc1"},
        events=[
            {"role": "assistant", "content": "I read docs/plan.md and pulled the key constraints."},
        ],
    )

    from umx.sessions import list_sessions

    session_paths = list_sessions(project_repo)
    facts = source_files_to_facts(project_repo, project_dir, session_paths)

    doc_facts = [f for f in facts if f.code_anchor and f.code_anchor.path == "docs/plan.md"]
    assert len(doc_facts) >= 1
    assert all(f.source_type == SourceType.EXTERNAL_DOC for f in doc_facts)
    assert all(f.encoding_strength == 2 for f in doc_facts)
    assert all(f.consolidation_status == ConsolidationStatus.FRAGILE for f in doc_facts)


def test_source_files_to_facts_ignores_user_only_path_mentions(
    project_dir: Path, project_repo: Path
) -> None:
    docs_dir = project_dir / "docs"
    docs_dir.mkdir()
    (docs_dir / "plan.md").write_text(
        "# Plan\n\n"
        "- The backup worker runs every hour\n"
    )

    write_session(
        project_repo,
        meta={"session_id": "2026-02-06-doc2"},
        events=[
            {"role": "user", "content": "Continue from docs/plan.md"},
            {"role": "assistant", "content": "I am focusing on dogfooding readiness."},
        ],
    )

    from umx.sessions import list_sessions

    session_paths = list_sessions(project_repo)
    facts = source_files_to_facts(project_repo, project_dir, session_paths)

    assert all((f.code_anchor.path if f.code_anchor else "") != "docs/plan.md" for f in facts)


def test_normalize_entity() -> None:
    vocab = {"pg": "PostgreSQL", "k8s": "Kubernetes"}
    assert normalize_entity("We use pg for storage", vocab) == "We use PostgreSQL for storage"
    assert normalize_entity("Deploy on k8s", vocab) == "Deploy on Kubernetes"
    # Should not replace partial matches
    assert normalize_entity("the pkg is ready", vocab) == "the pkg is ready"


def test_apply_conventions_to_fact() -> None:
    conventions = ConventionSet(
        entity_vocabulary={"pg": "PostgreSQL", "redis": "Redis"},
    )
    fact = Fact(
        fact_id="test-001",
        text="pg stores session data",
        scope=Scope.PROJECT,
        topic="pg",
        encoding_strength=2,
        memory_type=MemoryType.IMPLICIT,
        verification=Verification.SELF_REPORTED,
        source_type=SourceType.LLM_INFERENCE,
        confidence=0.5,
        provenance=Provenance(extracted_by="test"),
    )

    result = apply_conventions_to_fact(fact, conventions)
    assert result.text == "PostgreSQL stores session data"
    assert result.topic == "postgresql"
    assert result.fact_id == fact.fact_id


def test_apply_conventions_no_change() -> None:
    conventions = ConventionSet(
        entity_vocabulary={"pg": "PostgreSQL"},
    )
    fact = Fact(
        fact_id="test-002",
        text="The server runs on port 8080",
        scope=Scope.PROJECT,
        topic="server",
        encoding_strength=2,
        memory_type=MemoryType.IMPLICIT,
        verification=Verification.SELF_REPORTED,
        source_type=SourceType.LLM_INFERENCE,
        confidence=0.5,
        provenance=Provenance(extracted_by="test"),
    )

    result = apply_conventions_to_fact(fact, conventions)
    # Should return the original object when no changes
    assert result is fact


def test_apply_conventions_empty() -> None:
    fact = Fact(
        fact_id="test-003",
        text="Something",
        scope=Scope.PROJECT,
        topic="general",
        encoding_strength=2,
        memory_type=MemoryType.IMPLICIT,
        verification=Verification.SELF_REPORTED,
        source_type=SourceType.LLM_INFERENCE,
        confidence=0.5,
        provenance=Provenance(extracted_by="test"),
    )
    # Empty conventions should be a no-op
    result = apply_conventions_to_fact(fact, ConventionSet())
    assert result is fact


def test_gather_includes_source_files(
    project_dir: Path, project_repo: Path
) -> None:
    # Create a source file
    src_dir = project_dir / "lib"
    src_dir.mkdir()
    (src_dir / "config.py").write_text(
        "# NOTE: primary database config\n"
        'DATABASE_HOST = "localhost"\n'
        "DATABASE_PORT = 5432\n"
    )

    # Create a session referencing it
    write_session(
        project_repo,
        meta={"session_id": "2026-03-01-gather1"},
        events=[
            {"role": "user", "content": "What does lib/config.py contain?"},
            {
                "role": "assistant",
                "content": "lib/config.py defines the database connection settings.",
            },
        ],
    )

    pipeline = DreamPipeline(project_dir)
    pipeline.orient()
    candidates = pipeline.gather()

    # Should have both session facts and source file facts
    source_facts = [
        f for f in candidates if f.source_type == SourceType.GROUND_TRUTH_CODE
    ]
    assert len(source_facts) >= 1

    # Source facts should have code anchors
    for sf in source_facts:
        assert sf.code_anchor is not None
        assert sf.code_anchor.path == "lib/config.py"
