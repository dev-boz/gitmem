from __future__ import annotations

import fnmatch
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from umx.identity import generate_fact_id
from umx.models import Scope


META_RE = re.compile(r"<!--\s*(.*?)\s*-->", re.DOTALL)
TITLE_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)
SECTION_RE = re.compile(r"^##\s+(.+)$", re.MULTILINE)


@dataclass(slots=True)
class ProcedureTrigger:
    kind: str
    pattern: str


@dataclass(slots=True)
class Procedure:
    procedure_id: str
    title: str
    triggers: list[ProcedureTrigger] = field(default_factory=list)
    steps_markdown: str = ""
    confidence: float = 1.0
    source_label: str = "human_authored"
    scope: Scope = Scope.PROJECT
    topic: str = "procedures"
    task_classes: list[str] = field(default_factory=list)
    file_path: Path | None = None

    @property
    def text(self) -> str:
        return "\n".join(part for part in [self.title, self.steps_markdown.strip()] if part).strip()


def _repo_default_scope(repo_dir: Path) -> Scope:
    return Scope.USER if repo_dir.parent.name != "projects" else Scope.PROJECT


def _extract_section(text: str, section_name: str) -> str:
    matches = list(SECTION_RE.finditer(text))
    for index, match in enumerate(matches):
        if match.group(1).strip().lower() != section_name.lower():
            continue
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        return text[start:end].strip()
    return ""


def _parse_metadata(text: str) -> dict[str, str]:
    match = META_RE.search(text)
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


def _parse_triggers(text: str) -> list[ProcedureTrigger]:
    triggers: list[ProcedureTrigger] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line.startswith("- "):
            continue
        body = line[2:].strip()
        if ":" not in body:
            continue
        kind, value = body.split(":", 1)
        triggers.append(ProcedureTrigger(kind=kind.strip().lower(), pattern=value.strip().strip("`")))
    return triggers


def _parse_task_classes(metadata: dict[str, str]) -> list[str]:
    raw = metadata.get("task_classes") or metadata.get("task_class", "")
    return [tc.strip() for tc in raw.split(",") if tc.strip()] if raw else []


def read_procedure_file(path: Path, repo_dir: Path) -> list[Procedure]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    title_match = TITLE_RE.search(text)
    title = title_match.group(1).strip() if title_match else path.stem.replace("-", " ").strip()
    metadata = _parse_metadata(text)
    triggers = _parse_triggers(_extract_section(text, "Triggers"))
    steps = _extract_section(text, "Steps")
    procedure = Procedure(
        procedure_id=metadata.get("id", generate_fact_id()),
        title=title,
        triggers=triggers,
        steps_markdown=steps,
        confidence=float(metadata.get("conf", 1.0)),
        source_label=metadata.get("src", "human_authored"),
        scope=_repo_default_scope(repo_dir),
        topic=path.stem,
        task_classes=_parse_task_classes(metadata),
        file_path=path,
    )
    return [procedure]


def iter_procedure_files(repo_dir: Path) -> list[Path]:
    return sorted(repo_dir.glob("procedures/*.md"))


def load_all_procedures(repo_dir: Path) -> list[Procedure]:
    procedures: list[Procedure] = []
    for path in iter_procedure_files(repo_dir):
        procedures.extend(read_procedure_file(path, repo_dir=repo_dir))
    return procedures


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


def _matches_task_class(procedure_classes: list[str], query_class: str) -> bool:
    """Dotted-prefix match: query 'implementation.bugfix' matches procedure with 'implementation'."""
    q = query_class.lower()
    return any(
        q == c.lower() or q.startswith(c.lower() + ".") or c.lower().startswith(q + ".")
        for c in procedure_classes
    )


def match_procedures(
    procedures: list[Procedure],
    *,
    tool: str | None = None,
    prompt: str | None = None,
    file_paths: list[str] | None = None,
    command_text: str | None = None,
    task_class: str | None = None,
) -> list[Procedure]:
    command_signature = " ".join(part for part in [tool or "", command_text or ""] if part).strip()
    prompt_text = prompt or ""
    matched: list[Procedure] = []
    for procedure in procedures:
        # task_class filter: if procedure declares task_classes, check compatibility
        if task_class and procedure.task_classes:
            if not _matches_task_class(procedure.task_classes, task_class):
                continue
        for trigger in procedure.triggers:
            if trigger.kind == "command" and _matches_command(trigger.pattern, command_signature):
                matched.append(procedure)
                break
            if trigger.kind == "file" and _matches_file(trigger.pattern, file_paths or []):
                matched.append(procedure)
                break
            if trigger.kind == "pattern" and _matches_prompt(trigger.pattern, prompt_text):
                matched.append(procedure)
                break
            if trigger.kind == "task_class" and task_class and _matches_task_class([trigger.pattern], task_class):
                matched.append(procedure)
                break
    matched.sort(key=lambda procedure: procedure.title.lower())
    return matched
