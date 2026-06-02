from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from umx.models import isoformat_z, parse_datetime, utcnow


_FRONTMATTER_RE = re.compile(r"\A---\s*\n(?P<meta>.*?)\n---\s*\n?(?P<body>.*)\Z", re.DOTALL)
_TERM_RE = re.compile(r"[a-zA-Z0-9_./-]+")
_INVALIDATION_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "change",
    "changes",
    "code",
    "file",
    "for",
    "from",
    "if",
    "in",
    "is",
    "it",
    "moves",
    "of",
    "on",
    "or",
    "support",
    "supports",
    "the",
    "to",
    "when",
    "with",
}


@dataclass(slots=True, frozen=True)
class ReasoningArtifact:
    artifact_id: str
    conclusion: str
    evidence: list[str]
    confidence: float = 1.0
    invalidates_when: list[str] | None = None
    created_at: datetime | None = None
    status: str = "active"
    invalidated_at: datetime | None = None
    invalidation_reason: str | None = None
    path: Path | None = None
    body: str = ""

    @property
    def is_active(self) -> bool:
        return self.status != "invalidated"

    def index_text(self) -> str:
        return "\n".join(
            [
                self.conclusion,
                *self.evidence,
                *(self.invalidates_when or []),
                self.body,
            ]
        ).strip()

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "artifact_id": self.artifact_id,
            "kind": "reasoning_artifact",
            "conclusion": self.conclusion,
            "evidence": list(self.evidence),
            "confidence": self.confidence,
            "invalidates_when": list(self.invalidates_when or []),
            "created_at": isoformat_z(self.created_at),
            "status": self.status,
        }
        if self.invalidated_at:
            payload["invalidated_at"] = isoformat_z(self.invalidated_at)
        if self.invalidation_reason:
            payload["invalidation_reason"] = self.invalidation_reason
        if self.path:
            payload["path"] = self.path.as_posix()
        return payload


def reasoning_artifact_dir(repo_dir: Path) -> Path:
    return repo_dir / "memory" / "artifacts"


def iter_reasoning_artifact_paths(repo_dir: Path) -> list[Path]:
    paths: list[Path] = []
    for directory in (reasoning_artifact_dir(repo_dir), repo_dir / "artifacts"):
        if directory.exists():
            paths.extend(sorted(path for path in directory.glob("*.md") if path.is_file()))
    return sorted({path.resolve(): path for path in paths}.values())


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        stripped = value.strip()
        return [stripped] if stripped else []
    return []


def parse_reasoning_artifact(path: Path) -> ReasoningArtifact | None:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    match = _FRONTMATTER_RE.match(text)
    if match is None:
        return None
    try:
        metadata = yaml.safe_load(match.group("meta")) or {}
    except yaml.YAMLError:
        return None
    if not isinstance(metadata, dict):
        return None
    if metadata.get("kind") not in {None, "reasoning_artifact"}:
        return None
    conclusion = str(metadata.get("conclusion") or "").strip()
    if not conclusion:
        return None
    try:
        confidence = float(metadata.get("confidence", 1.0))
    except (TypeError, ValueError):
        confidence = 1.0
    return ReasoningArtifact(
        artifact_id=str(metadata.get("artifact_id") or path.stem).strip() or path.stem,
        conclusion=conclusion,
        evidence=_string_list(metadata.get("evidence")),
        confidence=confidence,
        invalidates_when=_string_list(metadata.get("invalidates_when")),
        created_at=parse_datetime(str(metadata.get("created_at") or "")) or utcnow(),
        status=str(metadata.get("status") or "active"),
        invalidated_at=parse_datetime(str(metadata.get("invalidated_at") or "")),
        invalidation_reason=(
            str(metadata["invalidation_reason"])
            if metadata.get("invalidation_reason") is not None
            else None
        ),
        path=path,
        body=match.group("body").rstrip() + "\n" if match.group("body").strip() else "",
    )


def load_reasoning_artifacts(repo_dir: Path, *, active_only: bool = False) -> list[ReasoningArtifact]:
    artifacts = [
        artifact
        for path in iter_reasoning_artifact_paths(repo_dir)
        if (artifact := parse_reasoning_artifact(path)) is not None
    ]
    if active_only:
        artifacts = [artifact for artifact in artifacts if artifact.is_active]
    return artifacts


def artifact_relative_path(repo_dir: Path, artifact: ReasoningArtifact) -> str:
    if artifact.path is None:
        return f"memory/artifacts/{artifact.artifact_id}.md"
    try:
        return artifact.path.relative_to(repo_dir).as_posix()
    except ValueError:
        return artifact.path.as_posix()


def render_reasoning_artifact(artifact: ReasoningArtifact) -> str:
    metadata: dict[str, Any] = {
        "artifact_id": artifact.artifact_id,
        "kind": "reasoning_artifact",
        "conclusion": artifact.conclusion,
        "evidence": list(artifact.evidence),
        "confidence": float(artifact.confidence),
        "invalidates_when": list(artifact.invalidates_when or []),
        "created_at": isoformat_z(artifact.created_at or utcnow()),
        "status": artifact.status,
    }
    if artifact.invalidated_at:
        metadata["invalidated_at"] = isoformat_z(artifact.invalidated_at)
    if artifact.invalidation_reason:
        metadata["invalidation_reason"] = artifact.invalidation_reason
    frontmatter = yaml.safe_dump(metadata, sort_keys=False, allow_unicode=True).strip()
    body = artifact.body.rstrip()
    return f"---\n{frontmatter}\n---\n\n{body}\n" if body else f"---\n{frontmatter}\n---\n"


def write_reasoning_artifact(repo_dir: Path, artifact: ReasoningArtifact) -> Path:
    path = artifact.path or reasoning_artifact_dir(repo_dir) / f"{artifact.artifact_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_reasoning_artifact(replace(artifact, path=path)), encoding="utf-8")
    return path


def _git_changed_paths(project_root: Path) -> list[str] | None:
    if not (project_root / ".git").exists():
        return None
    try:
        completed = subprocess.run(
            ["git", "-C", str(project_root), "status", "--porcelain"],
            capture_output=True,
            check=False,
            text=True,
        )
    except (OSError, ValueError):
        return None
    if completed.returncode != 0:
        return None
    paths: list[str] = []
    for line in completed.stdout.splitlines():
        if not line.strip():
            continue
        file_part = line[3:]
        paths.extend(file_part.split(" -> ") if " -> " in file_part else [file_part])
    return [path for path in paths if path]


def _fallback_project_paths(project_root: Path) -> list[str]:
    paths: list[str] = []
    for path in project_root.rglob("*"):
        if len(paths) >= 2000:
            break
        if not path.is_file():
            continue
        try:
            relative = path.relative_to(project_root)
        except ValueError:
            continue
        if ".git" in relative.parts:
            continue
        paths.append(relative.as_posix())
    return paths


def _change_signals(project_root: Path) -> set[str]:
    changed = _git_changed_paths(project_root)
    if changed == []:
        return set()
    paths = changed if changed is not None else _fallback_project_paths(project_root)
    signals: set[str] = set()
    for path in paths:
        normalized = path.strip().lower()
        if not normalized:
            continue
        signals.add(normalized)
        path_obj = Path(normalized)
        signals.add(path_obj.name)
        signals.add(path_obj.stem)
        signals.update(part for part in path_obj.parts if part and part not in {".", ".."})
    return signals


def _condition_terms(condition: str) -> set[str]:
    return {
        term.lower()
        for term in _TERM_RE.findall(condition)
        if len(term) > 2 and term.lower() not in _INVALIDATION_STOPWORDS
    }


def _matches_change_signal(condition: str, signals: set[str]) -> bool:
    lowered = condition.lower()
    for signal in signals:
        if "/" in signal and signal in lowered:
            return True
    return bool(_condition_terms(condition) & signals)


def check_reasoning_artifact_invalidations(
    project_root: Path,
    repo_dir: Path,
    *,
    now: datetime | None = None,
) -> list[dict[str, str]]:
    signals = _change_signals(project_root)
    if not signals:
        return []
    stamp = now or datetime.now(tz=UTC)
    invalidated: list[dict[str, str]] = []
    for artifact in load_reasoning_artifacts(repo_dir, active_only=True):
        for condition in artifact.invalidates_when or []:
            if not _matches_change_signal(condition, signals):
                continue
            updated = replace(
                artifact,
                status="invalidated",
                invalidated_at=stamp,
                invalidation_reason=condition,
            )
            write_reasoning_artifact(repo_dir, updated)
            invalidated.append(
                {
                    "artifact_id": artifact.artifact_id,
                    "path": artifact_relative_path(repo_dir, artifact),
                    "reason": condition,
                }
            )
            break
    return invalidated


def artifact_search_payload(artifact: ReasoningArtifact, repo_dir: Path) -> dict[str, Any]:
    return {
        "id": artifact.artifact_id,
        "conclusion": artifact.conclusion,
        "evidence": json.dumps(artifact.evidence),
        "invalidates_when": json.dumps(artifact.invalidates_when or []),
        "confidence": artifact.confidence,
        "status": artifact.status,
        "created_at": isoformat_z(artifact.created_at or utcnow()),
        "source_path": artifact_relative_path(repo_dir, artifact),
        "body": artifact.body,
    }
