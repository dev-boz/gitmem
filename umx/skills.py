"""Skill parsing and retrieval routing for gitmem."""

from __future__ import annotations

import fnmatch
import json
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path, PurePosixPath

from umx.identity import generate_fact_id
from umx.models import Scope, SkillStatus


@dataclass(slots=True)
class SkillTrigger:
    kind: str
    pattern: str


class RetrievalDirectiveKind(str, Enum):
    LOAD = "load"
    HINT = "hint"
    QUERY = "query"
    LINK = "link"


@dataclass(slots=True)
class RetrievalDirective:
    kind: RetrievalDirectiveKind
    value: str


@dataclass(slots=True)
class Skill:
    skill_id: str
    name: str
    version: str
    title: str
    description: str = ""
    triggers: list[SkillTrigger] = field(default_factory=list)
    directives: list[RetrievalDirective] = field(default_factory=list)
    skill_status: SkillStatus = SkillStatus.ACTIVE
    scope: Scope = Scope.PROJECT
    file_path: Path | None = None

    @property
    def text(self) -> str:
        parts = [self.title]
        if self.description:
            parts.append(self.description)
        return "\n".join(parts).strip()


@dataclass(slots=True)
class SkillResolution:
    skill: Skill
    routed_fact_ids: set[str]
    hints: list[str]
    directives_resolved: int
    missing_paths: list[str]
    blocked_paths: list[str]
    unsupported_directives: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SkillRetrieval:
    fact_id: str
    directive_kind: str
    directive_value: str


_META_RE = re.compile(r"<!--\s*(.*?)\s*-->", re.DOTALL)
_TITLE_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)
_SECTION_RE = re.compile(r"^##\s+(.+)$", re.MULTILINE)
_TOP_LEVEL_FIELD_RE = re.compile(r"^(?P<key>[a-zA-Z_][a-zA-Z0-9_-]*)\s*:\s*(?P<value>.+?)\s*$")
_DIRECTIVE_RE = re.compile(
    r"^\s*-\s*(load|search|hint|query|link)\s*:\s*(.+)$",
    re.MULTILINE,
)
_TRIGGER_KIND_COMMAND = "command"
_TRIGGER_KIND_FILE = "file"
_TRIGGER_KIND_PATTERN = "pattern"
_LOADABLE_PREFIXES = (
    "facts/topics/",
    "episodic/topics/",
    "principles/topics/",
    "local/private/",
    "tools/",
    "machines/",
    "folders/",
    "files/",
)
_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")


def _repo_default_scope(repo_dir: Path) -> Scope:
    return Scope.USER if repo_dir.parent.name != "projects" else Scope.PROJECT


def _extract_section(text: str, section_name: str) -> str:
    matches = list(_SECTION_RE.finditer(text))
    for index, match in enumerate(matches):
        if match.group(1).strip().lower() != section_name.lower():
            continue
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        return text[start:end].strip()
    return ""


def _parse_metadata(text: str) -> dict[str, str]:
    match = _META_RE.search(text)
    if not match:
        return {}
    payload = match.group(1).strip()
    if payload.startswith("umx:"):
        try:
            loaded = json.loads(payload[len("umx:") :].strip())
        except json.JSONDecodeError:
            return {}
        return {str(key): str(value) for key, value in loaded.items() if value is not None}
    pairs = re.findall(r"([a-zA-Z_]+):([^\s]+)", payload)
    return {key: value.strip("`") for key, value in pairs}


def _top_level_block(text: str) -> str:
    match = _SECTION_RE.search(text)
    if match is None:
        return text
    return text[: match.start()]


def _parse_top_level_fields(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for raw_line in _top_level_block(text).splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("<!--"):
            continue
        match = _TOP_LEVEL_FIELD_RE.match(line)
        if not match:
            continue
        fields[match.group("key").strip().lower()] = match.group("value").strip().strip("`")
    return fields


def _parse_triggers(text: str) -> list[SkillTrigger]:
    triggers: list[SkillTrigger] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line.startswith("- "):
            continue
        body = line[2:].strip()
        if ":" not in body:
            continue
        kind, value = body.split(":", 1)
        triggers.append(SkillTrigger(kind=kind.strip().lower(), pattern=value.strip().strip("`")))
    return triggers


def _parse_directives(text: str) -> list[RetrievalDirective]:
    directives: list[RetrievalDirective] = []
    for match in _DIRECTIVE_RE.finditer(text):
        raw_kind = match.group(1).strip().lower()
        value = match.group(2).strip().strip("`")
        kind = RetrievalDirectiveKind.LOAD if raw_kind == "search" else RetrievalDirectiveKind(raw_kind)
        directives.append(RetrievalDirective(kind=kind, value=value))
    return directives


def _field_value(
    top_level: dict[str, str],
    metadata: dict[str, str],
    *names: str,
    default: str,
) -> str:
    for name in names:
        lowered = name.lower()
        if lowered in top_level and top_level[lowered]:
            return top_level[lowered]
    for name in names:
        lowered = name.lower()
        if lowered in metadata and metadata[lowered]:
            return metadata[lowered]
    return default


def _parse_skill_status(value: str) -> SkillStatus:
    try:
        return SkillStatus(value.strip().lower())
    except ValueError:
        return SkillStatus.ACTIVE


def iter_skill_files(repo_dir: Path) -> list[Path]:
    skills_dir = repo_dir / "skills"
    if not skills_dir.is_dir():
        return []
    return sorted(skills_dir.glob("*.md"))


def read_skill_file(path: Path, repo_dir: Path) -> list[Skill]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    title_match = _TITLE_RE.search(text)
    title = title_match.group(1).strip() if title_match else path.stem.replace("-", " ").strip()
    metadata = _parse_metadata(text)
    top_level = _parse_top_level_fields(text)
    description = _extract_section(text, "Description").strip()
    return [
        Skill(
            skill_id=_field_value(top_level, metadata, "id", default=generate_fact_id()),
            name=_field_value(top_level, metadata, "name", default=path.stem),
            version=_field_value(top_level, metadata, "version", "v", default="1"),
            title=title,
            description=description,
            triggers=_parse_triggers(_extract_section(text, "Triggers")),
            directives=_parse_directives(_extract_section(text, "Retrieval")),
            skill_status=_parse_skill_status(
                _field_value(
                    top_level,
                    metadata,
                    "skill_status",
                    "ss",
                    default=SkillStatus.ACTIVE.value,
                )
            ),
            scope=_repo_default_scope(repo_dir),
            file_path=path,
        )
    ]


def load_all_skills(repo_dir: Path) -> list[Skill]:
    skills: list[Skill] = []
    for path in iter_skill_files(repo_dir):
        skills.extend(read_skill_file(path, repo_dir=repo_dir))
    return skills


def _matches_command(pattern: str, command_text: str) -> bool:
    if not command_text.strip():
        return False
    try:
        return re.search(pattern, command_text, re.IGNORECASE) is not None
    except re.error:
        return False


def _matches_file(pattern: str, file_paths: list[str]) -> bool:
    globs = [part.strip() for part in pattern.split("|") if part.strip()]
    if not globs or not file_paths:
        return False
    return any(fnmatch.fnmatch(path, candidate) for path in file_paths for candidate in globs)


def _matches_prompt(pattern: str, prompt_text: str) -> bool:
    if not prompt_text.strip():
        return False
    try:
        return re.search(pattern, prompt_text, re.IGNORECASE) is not None
    except re.error:
        return False


def match_skills_by_trigger(
    skills: list[Skill],
    *,
    tool: str | None = None,
    prompt: str | None = None,
    file_paths: list[str] | None = None,
    command_text: str | None = None,
) -> list[Skill]:
    command_signature = " ".join(part for part in [tool or "", command_text or ""] if part).strip()
    prompt_text = prompt or ""
    matched: list[Skill] = []
    for skill in skills:
        if skill.skill_status in {SkillStatus.DRAFT, SkillStatus.RETIRED}:
            continue
        for trigger in skill.triggers:
            if trigger.kind == _TRIGGER_KIND_COMMAND and _matches_command(trigger.pattern, command_signature):
                matched.append(skill)
                break
            if trigger.kind == _TRIGGER_KIND_FILE and _matches_file(trigger.pattern, file_paths or []):
                matched.append(skill)
                break
            if trigger.kind == _TRIGGER_KIND_PATTERN and _matches_prompt(trigger.pattern, prompt_text):
                matched.append(skill)
                break
    return matched


def match_skills_by_name(
    skills: list[Skill],
    name: str,
    *,
    activatable_only: bool = False,
) -> Skill | None:
    for skill in skills:
        if skill.name != name:
            continue
        if activatable_only and skill.skill_status in {SkillStatus.DRAFT, SkillStatus.RETIRED}:
            continue
        return skill
    return None


def _normalized_skill_path(value: str) -> str:
    return PurePosixPath(value.replace("\\", "/")).as_posix()


def _resolve_load_path(repo_dir: Path, value: str) -> tuple[Path | None, str, bool]:
    raw_value = value.strip().strip("`")
    if (
        not raw_value
        or raw_value.startswith("/")
        or raw_value.startswith("\\")
        or _WINDOWS_DRIVE_RE.match(raw_value)
    ):
        return None, raw_value, True
    candidate_parts = PurePosixPath(raw_value).parts
    if any(part == ".." for part in candidate_parts):
        return None, _normalized_skill_path(raw_value), True
    candidate = (repo_dir / PurePosixPath(raw_value)).resolve()
    repo_root = repo_dir.resolve()
    try:
        relative = candidate.relative_to(repo_root).as_posix()
    except ValueError:
        return None, _normalized_skill_path(raw_value), True
    if relative == "local/secret" or relative.startswith("local/secret/"):
        return None, relative, True
    if not any(relative.startswith(prefix) for prefix in _LOADABLE_PREFIXES):
        return None, relative, True
    return candidate, relative, False


def resolve_skill(skill: Skill, repo_dir: Path, *, config: object | None = None) -> SkillResolution:
    resolution, _ = resolve_skill_with_attribution(skill, repo_dir, config=config)
    return resolution


def resolve_skill_with_attribution(
    skill: Skill,
    repo_dir: Path,
    *,
    config: object | None = None,
) -> tuple[SkillResolution, list[SkillRetrieval]]:
    from umx.memory import read_fact_file

    max_directives = getattr(getattr(config, "skills", None), "max_directives_per_skill", 0)
    directives = skill.directives[: int(max_directives)] if max_directives else skill.directives
    routed_fact_ids: set[str] = set()
    hints: list[str] = []
    missing_paths: list[str] = []
    blocked_paths: list[str] = []
    unsupported_directives: list[str] = []
    directives_resolved = 0
    retrievals: list[SkillRetrieval] = []
    for directive in directives:
        if directive.kind == RetrievalDirectiveKind.HINT:
            hints.append(directive.value)
            directives_resolved += 1
            continue
        if directive.kind != RetrievalDirectiveKind.LOAD:
            unsupported_directives.append(f"{directive.kind.value}: {directive.value}")
            continue
        target_path, relative_path, blocked = _resolve_load_path(repo_dir, directive.value)
        if blocked:
            blocked_paths.append(relative_path)
            continue
        if target_path is None or not target_path.exists():
            missing_paths.append(relative_path)
            continue
        directives_resolved += 1
        for fact in read_fact_file(target_path, repo_dir=repo_dir):
            routed_fact_ids.add(fact.fact_id)
            retrievals.append(
                SkillRetrieval(
                    fact_id=fact.fact_id,
                    directive_kind=directive.kind.value,
                    directive_value=relative_path,
                )
            )
    return (
        SkillResolution(
            skill=skill,
            routed_fact_ids=routed_fact_ids,
            hints=hints,
            directives_resolved=directives_resolved,
            missing_paths=missing_paths,
            blocked_paths=blocked_paths,
            unsupported_directives=unsupported_directives,
        ),
        retrievals,
    )


__all__ = [
    "RetrievalDirective",
    "RetrievalDirectiveKind",
    "Skill",
    "SkillResolution",
    "SkillRetrieval",
    "SkillStatus",
    "SkillTrigger",
    "iter_skill_files",
    "load_all_skills",
    "match_skills_by_name",
    "match_skills_by_trigger",
    "read_skill_file",
    "resolve_skill",
    "resolve_skill_with_attribution",
]
