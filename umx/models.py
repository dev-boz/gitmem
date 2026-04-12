from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any


def utcnow() -> datetime:
    return datetime.now(tz=UTC)


def isoformat_z(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.replace("Z", "+00:00")
    return datetime.fromisoformat(text)


class Scope(str, Enum):
    USER = "user"
    TOOL = "tool"
    MACHINE = "machine"
    PROJECT = "project"
    PROJECT_PRIVATE = "project_private"
    PROJECT_SECRET = "project_secret"
    FOLDER = "folder"
    FILE = "file"


class MemoryType(str, Enum):
    EXPLICIT_SEMANTIC = "explicit_semantic"
    EXPLICIT_EPISODIC = "explicit_episodic"
    IMPLICIT = "implicit"


class Verification(str, Enum):
    SELF_REPORTED = "self-reported"
    CORROBORATED = "corroborated"
    SOTA_REVIEWED = "sota-reviewed"
    HUMAN_CONFIRMED = "human-confirmed"


class SourceType(str, Enum):
    GROUND_TRUTH_CODE = "ground_truth_code"
    USER_PROMPT = "user_prompt"
    TOOL_OUTPUT = "tool_output"
    LLM_INFERENCE = "llm_inference"
    DREAM_CONSOLIDATION = "dream_consolidation"
    EXTERNAL_DOC = "external_doc"


class ConsolidationStatus(str, Enum):
    FRAGILE = "fragile"
    STABLE = "stable"


class TaskStatus(str, Enum):
    OPEN = "open"
    BLOCKED = "blocked"
    RESOLVED = "resolved"
    ABANDONED = "abandoned"


@dataclass(slots=True)
class AppliesTo:
    env: str = "*"
    os: str = "*"
    machine: str = "*"
    branch: str = "*"

    def normalized(self) -> "AppliesTo":
        return AppliesTo(
            env=self.env or "*",
            os=self.os or "*",
            machine=self.machine or "*",
            branch=self.branch or "*",
        )

    def overlaps(self, other: "AppliesTo | None") -> bool:
        left = self.normalized()
        right = (other or AppliesTo()).normalized()
        return all(
            getattr(left, key) == getattr(right, key)
            or getattr(left, key) == "*"
            or getattr(right, key) == "*"
            for key in ("env", "os", "machine", "branch")
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "env": self.env,
            "os": self.os,
            "machine": self.machine,
            "branch": self.branch,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "AppliesTo | None":
        if not data:
            return None
        if isinstance(data, cls):
            return data
        return cls(
            env=data.get("env", "*"),
            os=data.get("os", "*"),
            machine=data.get("machine", "*"),
            branch=data.get("branch", "*"),
        )


@dataclass(slots=True)
class CodeAnchor:
    repo: str
    path: str
    git_sha: str | None = None
    line_range: list[int] | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"repo": self.repo, "path": self.path}
        if self.git_sha:
            data["git_sha"] = self.git_sha
        if self.line_range:
            data["line_range"] = list(self.line_range)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "CodeAnchor | None":
        if not data:
            return None
        if isinstance(data, cls):
            return data
        return cls(
            repo=data["repo"],
            path=data["path"],
            git_sha=data.get("git_sha"),
            line_range=data.get("line_range"),
        )


@dataclass(slots=True)
class Provenance:
    extracted_by: str = "manual"
    approved_by: str | None = None
    approval_tier: str | None = None
    pr: str | None = None
    sessions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"extracted_by": self.extracted_by, "sessions": list(self.sessions)}
        if self.approved_by:
            data["approved_by"] = self.approved_by
        if self.approval_tier:
            data["approval_tier"] = self.approval_tier
        if self.pr:
            data["pr"] = self.pr
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "Provenance":
        if not data:
            return cls()
        if isinstance(data, cls):
            return data
        return cls(
            extracted_by=data.get("extracted_by", "manual"),
            approved_by=data.get("approved_by"),
            approval_tier=data.get("approval_tier"),
            pr=data.get("pr"),
            sessions=list(data.get("sessions", [])),
        )


@dataclass(slots=True)
class Fact:
    fact_id: str
    text: str
    scope: Scope
    topic: str
    encoding_strength: int
    memory_type: MemoryType
    verification: Verification
    source_type: SourceType
    confidence: float = 1.0
    tags: list[str] = field(default_factory=list)
    source_tool: str = "manual"
    source_session: str = "manual"
    corroborated_by_tools: list[str] = field(default_factory=list)
    corroborated_by_facts: list[str] = field(default_factory=list)
    conflicts_with: list[str] = field(default_factory=list)
    supersedes: str | None = None
    superseded_by: str | None = None
    consolidation_status: ConsolidationStatus = ConsolidationStatus.FRAGILE
    task_status: TaskStatus | None = None
    last_retrieved: datetime | None = None
    created: datetime = field(default_factory=utcnow)
    last_referenced: datetime | None = None
    expires_at: datetime | None = None
    applies_to: AppliesTo | None = None
    provenance: Provenance = field(default_factory=Provenance)
    encoding_context: dict[str, Any] = field(default_factory=dict)
    code_anchor: CodeAnchor | None = None
    repo: str | None = None
    file_path: Path | None = None

    @property
    def is_active(self) -> bool:
        return self.superseded_by is None

    @property
    def is_fragile(self) -> bool:
        return self.consolidation_status == ConsolidationStatus.FRAGILE

    def clone(self, **updates: Any) -> "Fact":
        values = self.to_dict()
        values.update(updates)
        return fact_from_dict(values)

    def to_dict(self) -> dict[str, Any]:
        return {
            "fact_id": self.fact_id,
            "text": self.text,
            "scope": self.scope.value,
            "topic": self.topic,
            "encoding_strength": self.encoding_strength,
            "memory_type": self.memory_type.value,
            "verification": self.verification.value,
            "source_type": self.source_type.value,
            "confidence": self.confidence,
            "tags": list(self.tags),
            "source_tool": self.source_tool,
            "source_session": self.source_session,
            "corroborated_by_tools": list(self.corroborated_by_tools),
            "corroborated_by_facts": list(self.corroborated_by_facts),
            "conflicts_with": list(self.conflicts_with),
            "supersedes": self.supersedes,
            "superseded_by": self.superseded_by,
            "consolidation_status": self.consolidation_status.value,
            "task_status": self.task_status.value if self.task_status else None,
            "last_retrieved": isoformat_z(self.last_retrieved),
            "created": isoformat_z(self.created),
            "last_referenced": isoformat_z(self.last_referenced),
            "expires_at": isoformat_z(self.expires_at),
            "applies_to": self.applies_to.to_dict() if self.applies_to else None,
            "provenance": self.provenance.to_dict(),
            "encoding_context": dict(self.encoding_context),
            "code_anchor": self.code_anchor.to_dict() if self.code_anchor else None,
            "repo": self.repo,
            "file_path": str(self.file_path) if self.file_path else None,
        }


def fact_from_dict(data: dict[str, Any]) -> Fact:
    return Fact(
        fact_id=data["fact_id"],
        text=data["text"],
        scope=data["scope"] if isinstance(data["scope"], Scope) else Scope(data["scope"]),
        topic=data["topic"],
        encoding_strength=int(data["encoding_strength"]),
        memory_type=data["memory_type"] if isinstance(data["memory_type"], MemoryType) else MemoryType(data["memory_type"]),
        verification=data["verification"] if isinstance(data["verification"], Verification) else Verification(data["verification"]),
        source_type=data["source_type"] if isinstance(data["source_type"], SourceType) else SourceType(data["source_type"]),
        confidence=float(data.get("confidence", 1.0)),
        tags=list(data.get("tags", [])),
        source_tool=data.get("source_tool", "manual"),
        source_session=data.get("source_session", "manual"),
        corroborated_by_tools=list(data.get("corroborated_by_tools", [])),
        corroborated_by_facts=list(data.get("corroborated_by_facts", [])),
        conflicts_with=list(data.get("conflicts_with", [])),
        supersedes=data.get("supersedes"),
        superseded_by=data.get("superseded_by"),
        consolidation_status=data.get("consolidation_status")
        if isinstance(data.get("consolidation_status"), ConsolidationStatus)
        else ConsolidationStatus(data.get("consolidation_status", ConsolidationStatus.FRAGILE.value)),
        task_status=(
            data["task_status"]
            if isinstance(data.get("task_status"), TaskStatus)
            else TaskStatus(data["task_status"])
        )
        if data.get("task_status")
        else None,
        last_retrieved=parse_datetime(data.get("last_retrieved")),
        created=parse_datetime(data.get("created")) or utcnow(),
        last_referenced=parse_datetime(data.get("last_referenced")),
        expires_at=parse_datetime(data.get("expires_at")),
        applies_to=AppliesTo.from_dict(data.get("applies_to")),
        provenance=Provenance.from_dict(data.get("provenance")),
        encoding_context=dict(data.get("encoding_context", {})),
        code_anchor=CodeAnchor.from_dict(data.get("code_anchor")),
        repo=data.get("repo"),
        file_path=Path(data["file_path"]) if data.get("file_path") else None,
    )
