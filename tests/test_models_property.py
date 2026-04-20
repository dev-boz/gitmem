from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from umx.memory import format_fact_line, parse_fact_line
from umx.models import (
    AppliesTo,
    CodeAnchor,
    ConsolidationStatus,
    Fact,
    MemoryType,
    Provenance,
    Scope,
    SourceType,
    TaskStatus,
    Verification,
    fact_from_dict,
)

_ULID_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_SAFE_TEXT_ALPHABET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 .,_/:()-"
_SAFE_TOKEN_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789._-"
_PATH_SEGMENT_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789._-"


@dataclass(frozen=True)
class MarkdownPathCase:
    repo_kind: str
    template: str
    scope: Scope
    memory_type: MemoryType

    def path_for(self, repo_dir: Path, topic: str) -> Path:
        return repo_dir / self.template.format(topic=topic)


MARKDOWN_PATH_CASES = (
    MarkdownPathCase("project", "facts/topics/{topic}.md", Scope.PROJECT, MemoryType.EXPLICIT_SEMANTIC),
    MarkdownPathCase("project", "episodic/topics/{topic}.md", Scope.PROJECT, MemoryType.EXPLICIT_EPISODIC),
    MarkdownPathCase("project", "principles/topics/{topic}.md", Scope.PROJECT, MemoryType.EXPLICIT_SEMANTIC),
    MarkdownPathCase("project", "local/private/{topic}.md", Scope.PROJECT_PRIVATE, MemoryType.EXPLICIT_SEMANTIC),
    MarkdownPathCase("project", "local/secret/{topic}.md", Scope.PROJECT_SECRET, MemoryType.EXPLICIT_SEMANTIC),
    MarkdownPathCase("project", "tools/{topic}.md", Scope.TOOL, MemoryType.EXPLICIT_SEMANTIC),
    MarkdownPathCase("project", "machines/{topic}.md", Scope.MACHINE, MemoryType.EXPLICIT_SEMANTIC),
    MarkdownPathCase("project", "folders/{topic}.md", Scope.FOLDER, MemoryType.EXPLICIT_SEMANTIC),
    MarkdownPathCase("project", "files/{topic}.md", Scope.FILE, MemoryType.EXPLICIT_SEMANTIC),
    MarkdownPathCase("user", "facts/topics/{topic}.md", Scope.USER, MemoryType.EXPLICIT_SEMANTIC),
    MarkdownPathCase("user", "episodic/topics/{topic}.md", Scope.USER, MemoryType.EXPLICIT_EPISODIC),
    MarkdownPathCase("user", "principles/topics/{topic}.md", Scope.USER, MemoryType.EXPLICIT_SEMANTIC),
)


def _safe_text(*, min_size: int = 1, max_size: int = 40) -> st.SearchStrategy[str]:
    return (
        st.text(alphabet=_SAFE_TEXT_ALPHABET, min_size=min_size, max_size=max_size)
        .filter(lambda value: value.strip() != "")
        .filter(lambda value: not value.startswith("[DEPRECATED]"))
        .filter(lambda value: "<!-- umx:" not in value)
    )


def _safe_token(*, min_size: int = 1, max_size: int = 20) -> st.SearchStrategy[str]:
    return st.text(alphabet=_SAFE_TOKEN_ALPHABET, min_size=min_size, max_size=max_size).filter(bool)


def _topic_strategy() -> st.SearchStrategy[str]:
    return st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789_-", min_size=1, max_size=20).filter(bool)


def _fact_id_strategy() -> st.SearchStrategy[str]:
    return st.text(alphabet=_ULID_ALPHABET, min_size=26, max_size=26)


def _source_tool_strategy() -> st.SearchStrategy[str]:
    plain = _safe_text(max_size=24)
    escaped_arrow = st.tuples(_safe_token(), _safe_token()).map(lambda parts: f"{parts[0]}-->{parts[1]}")
    return st.one_of(plain, escaped_arrow)


def _datetime_strategy() -> st.SearchStrategy[datetime]:
    return st.datetimes(
        min_value=datetime(2000, 1, 1),
        max_value=datetime(2035, 12, 31, 23, 59, 59),
        timezones=st.just(UTC),
    )


def _optional_datetime_strategy() -> st.SearchStrategy[datetime | None]:
    return st.one_of(st.none(), _datetime_strategy())


def _path_strategy() -> st.SearchStrategy[Path]:
    segment = st.text(alphabet=_PATH_SEGMENT_ALPHABET, min_size=1, max_size=12).filter(bool)
    return st.lists(segment, min_size=1, max_size=4).map(lambda parts: Path("/".join(parts)))


def _json_value_strategy() -> st.SearchStrategy[object]:
    scalar = st.one_of(
        st.none(),
        st.booleans(),
        st.integers(min_value=-10, max_value=10),
        st.floats(min_value=0, max_value=1, allow_nan=False, allow_infinity=False),
        _safe_text(max_size=20),
    )
    return st.recursive(
        scalar,
        lambda children: st.one_of(
            st.lists(children, max_size=3),
            st.dictionaries(_safe_token(max_size=12), children, max_size=3),
        ),
        max_leaves=8,
    )


@st.composite
def _fact_strategy(
    draw,
    *,
    scope: Scope | None = None,
    memory_type: MemoryType | None = None,
    topic: str | None = None,
) -> Fact:
    return Fact(
        fact_id=draw(_fact_id_strategy()),
        text=draw(_safe_text()),
        scope=scope or draw(st.sampled_from(list(Scope))),
        topic=topic or draw(_topic_strategy()),
        encoding_strength=draw(st.integers(min_value=1, max_value=9)),
        memory_type=memory_type or draw(st.sampled_from(list(MemoryType))),
        verification=draw(st.sampled_from(list(Verification))),
        source_type=draw(st.sampled_from(list(SourceType))),
        confidence=draw(st.floats(min_value=0, max_value=1, allow_nan=False, allow_infinity=False)),
        tags=draw(st.lists(_safe_token(), max_size=4, unique=True)),
        source_tool=draw(_source_tool_strategy()),
        source_session=draw(_safe_text(max_size=24)),
        corroborated_by_tools=draw(st.lists(_source_tool_strategy(), max_size=3)),
        corroborated_by_facts=draw(st.lists(_fact_id_strategy(), max_size=3, unique=True)),
        conflicts_with=draw(st.lists(_fact_id_strategy(), max_size=3, unique=True)),
        supersedes=draw(st.one_of(st.none(), _fact_id_strategy())),
        superseded_by=draw(st.one_of(st.none(), _fact_id_strategy())),
        consolidation_status=draw(st.sampled_from(list(ConsolidationStatus))),
        task_status=draw(st.one_of(st.none(), st.sampled_from(list(TaskStatus)))),
        last_retrieved=draw(_optional_datetime_strategy()),
        created=draw(_datetime_strategy()),
        last_referenced=draw(_optional_datetime_strategy()),
        expires_at=draw(_optional_datetime_strategy()),
        applies_to=draw(
            st.one_of(
                st.none(),
                st.builds(
                    AppliesTo,
                    env=st.one_of(st.just("*"), _safe_token()),
                    os=st.one_of(st.just("*"), _safe_token()),
                    machine=st.one_of(st.just("*"), _safe_token()),
                    branch=st.one_of(st.just("*"), _safe_token()),
                ),
            )
        ),
        provenance=draw(
            st.builds(
                Provenance,
                extracted_by=_safe_text(max_size=24),
                approved_by=st.one_of(st.none(), _safe_text(max_size=24)),
                approval_tier=st.one_of(st.none(), _safe_text(max_size=12)),
                pr=st.one_of(st.none(), _safe_text(max_size=12)),
                sessions=st.lists(_safe_text(max_size=24), max_size=3),
            )
        ),
        encoding_context=draw(st.dictionaries(_safe_token(max_size=12), _json_value_strategy(), max_size=3)),
        code_anchor=draw(
            st.one_of(
                st.none(),
                st.builds(
                    CodeAnchor,
                    repo=_safe_text(max_size=24),
                    path=_path_strategy().map(str),
                    git_sha=st.one_of(st.none(), _safe_text(max_size=24)),
                    line_range=st.one_of(
                        st.none(),
                        st.lists(st.integers(min_value=1, max_value=500), min_size=1, max_size=4),
                    ),
                ),
            )
        ),
        repo=draw(st.one_of(st.none(), _safe_text(max_size=24))),
        file_path=draw(st.one_of(st.none(), _path_strategy())),
    )


@st.composite
def _markdown_fact_case(draw) -> tuple[MarkdownPathCase, Fact]:
    case = draw(st.sampled_from(MARKDOWN_PATH_CASES))
    topic = draw(_topic_strategy())
    return case, draw(_fact_strategy(scope=case.scope, memory_type=case.memory_type, topic=topic))


@settings(max_examples=75, deadline=None)
@given(fact=_fact_strategy())
def test_fact_dict_round_trip_preserves_full_model(fact: Fact) -> None:
    assert fact_from_dict(fact.to_dict()) == fact


@settings(
    max_examples=75,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(case_and_fact=_markdown_fact_case())
def test_fact_markdown_round_trip_preserves_current_surface(
    project_repo: Path,
    user_repo: Path,
    case_and_fact: tuple[MarkdownPathCase, Fact],
) -> None:
    case, fact = case_and_fact
    repo_dir = project_repo if case.repo_kind == "project" else user_repo
    path = case.path_for(repo_dir, fact.topic)

    parsed = parse_fact_line(format_fact_line(fact), repo_dir=repo_dir, path=path)

    assert parsed is not None
    assert parsed.fact_id == fact.fact_id
    assert parsed.text == fact.text.strip()
    assert parsed.scope == case.scope
    assert parsed.topic == fact.topic
    assert parsed.encoding_strength == fact.encoding_strength
    assert parsed.memory_type == case.memory_type
    assert parsed.verification == fact.verification
    assert parsed.source_type == fact.source_type
    assert parsed.confidence == pytest.approx(round(fact.confidence, 4))
    assert parsed.source_tool == fact.source_tool
    assert parsed.source_session == fact.source_session
    assert parsed.corroborated_by_tools == fact.corroborated_by_tools
    assert parsed.corroborated_by_facts == fact.corroborated_by_facts
    assert parsed.conflicts_with == fact.conflicts_with
    assert parsed.supersedes == fact.supersedes
    assert parsed.superseded_by == fact.superseded_by
    assert parsed.consolidation_status == fact.consolidation_status
    assert parsed.task_status == fact.task_status
    assert parsed.created == fact.created
    assert parsed.expires_at == fact.expires_at
    assert parsed.applies_to == fact.applies_to
    assert parsed.code_anchor == fact.code_anchor
    assert parsed.provenance.extracted_by == fact.provenance.extracted_by
    assert parsed.provenance.approved_by == fact.provenance.approved_by
    assert parsed.provenance.approval_tier == fact.provenance.approval_tier
    assert parsed.provenance.pr == fact.provenance.pr
    assert parsed.provenance.sessions == [fact.source_session]
    assert parsed.tags == []
    assert parsed.last_retrieved is None
    assert parsed.last_referenced is None
    assert parsed.encoding_context == {}
    assert parsed.repo == repo_dir.name
    assert parsed.file_path == path


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("scope", "not-a-scope"),
        ("source_type", "not-a-source-type"),
    ],
)
def test_fact_from_dict_rejects_invalid_enum_values(field: str, value: str) -> None:
    payload = Fact(
        fact_id="01TESTFACTMODELPROPERTY0001",
        text="enum validation",
        scope=Scope.PROJECT,
        topic="models",
        encoding_strength=3,
        memory_type=MemoryType.EXPLICIT_SEMANTIC,
        verification=Verification.SELF_REPORTED,
        source_type=SourceType.TOOL_OUTPUT,
    ).to_dict()
    payload[field] = value

    with pytest.raises(ValueError):
        fact_from_dict(payload)


def test_parse_fact_line_rejects_invalid_source_type(project_repo: Path) -> None:
    path = project_repo / "facts" / "topics" / "deploy.md"
    line = (
        '- [S:3|V:sr] invalid source type '
        '<!-- umx:{"id":"01TESTFACTINVALIDSOURCE0001","conf":1.0,"cort":[],"corf":[],"src":"manual",'
        '"xby":"manual","ss":"sess-invalid","st":"not-a-source-type","cr":"2026-04-15T12:00:00Z",'
        '"v":"self-reported","cs":"fragile"} -->'
    )

    with pytest.raises(ValueError):
        parse_fact_line(line, repo_dir=project_repo, path=path)
