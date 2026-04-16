from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

from umx.config import UMXConfig

CURRENT_SCHEMA_VERSION = 2


@dataclass(slots=True, frozen=True)
class SchemaState:
    found: int | None
    expected: int
    state: Literal["missing", "current", "stale", "future-unsupported"]
    status: Literal["ok", "warn", "error"]
    fixable: bool
    pending: list[str] = field(default_factory=list)
    message: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(slots=True, frozen=True)
class SchemaRepairResult:
    from_version: int | None
    to_version: int
    applied: list[str] = field(default_factory=list)
    rebuilt_index: bool = False

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def schema_version_path(repo_dir: Path) -> Path:
    return repo_dir / "meta" / "schema_version"


def read_schema_version(repo_dir: Path) -> tuple[int | None, str | None]:
    path = schema_version_path(repo_dir)
    if not path.exists():
        return None, None
    raw = path.read_text().strip()
    if not raw:
        return None, ""
    try:
        return int(raw), raw
    except ValueError:
        return None, raw


def detect_schema_state(repo_dir: Path) -> SchemaState:
    found, raw = read_schema_version(repo_dir)
    if raw is None:
        return SchemaState(
            found=None,
            expected=CURRENT_SCHEMA_VERSION,
            state="missing",
            status="warn",
            fixable=True,
            pending=[
                f"write meta/schema_version={CURRENT_SCHEMA_VERSION}",
                f"update meta/MEMORY.md schema_version to {CURRENT_SCHEMA_VERSION}",
                "rebuild local search indexes",
            ],
            message="repository schema_version is missing",
        )
    if found is None:
        return SchemaState(
            found=None,
            expected=CURRENT_SCHEMA_VERSION,
            state="stale",
            status="warn",
            fixable=True,
            pending=[
                f"rewrite invalid schema_version as {CURRENT_SCHEMA_VERSION}",
                f"update meta/MEMORY.md schema_version to {CURRENT_SCHEMA_VERSION}",
                "rebuild local search indexes",
            ],
            message=f"repository schema_version is invalid: {raw!r}",
        )
    if found == CURRENT_SCHEMA_VERSION:
        return SchemaState(
            found=found,
            expected=CURRENT_SCHEMA_VERSION,
            state="current",
            status="ok",
            fixable=False,
            message="repository schema is current",
        )
    if found < CURRENT_SCHEMA_VERSION:
        return SchemaState(
            found=found,
            expected=CURRENT_SCHEMA_VERSION,
            state="stale",
            status="warn",
            fixable=True,
            pending=[
                f"upgrade schema_version from {found} to {CURRENT_SCHEMA_VERSION}",
                f"update meta/MEMORY.md schema_version to {CURRENT_SCHEMA_VERSION}",
                "rebuild local search indexes",
            ],
            message=f"repository schema {found} is older than supported schema {CURRENT_SCHEMA_VERSION}",
        )
    return SchemaState(
        found=found,
        expected=CURRENT_SCHEMA_VERSION,
        state="future-unsupported",
        status="error",
        fixable=False,
        pending=[],
        message=f"repository schema {found} is newer than supported schema {CURRENT_SCHEMA_VERSION}",
    )


def write_schema_version(repo_dir: Path) -> bool:
    path = schema_version_path(repo_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    content = f"{CURRENT_SCHEMA_VERSION}\n"
    if path.exists() and path.read_text() == content:
        return False
    path.write_text(content)
    return True


def ensure_memory_schema_header(repo_dir: Path) -> bool:
    path = repo_dir / "meta" / "MEMORY.md"
    header = "# umx memory index"
    schema_line = f"schema_version: {CURRENT_SCHEMA_VERSION}"
    if not path.exists():
        path.write_text(f"{header}\n{schema_line}\n")
        return True

    lines = path.read_text().splitlines()
    if not lines:
        path.write_text(f"{header}\n{schema_line}\n")
        return True

    changed = False
    if lines[0] != header:
        lines.insert(0, header)
        changed = True
    if len(lines) < 2:
        lines.insert(1, schema_line)
        changed = True
    elif lines[1].startswith("schema_version:"):
        if lines[1] != schema_line:
            lines[1] = schema_line
            changed = True
    else:
        lines.insert(1, schema_line)
        changed = True
    if changed:
        path.write_text("\n".join(lines) + "\n")
    return changed


def repair_schema(repo_dir: Path, *, config: UMXConfig | None = None) -> SchemaRepairResult:
    from umx.scope import ensure_repo_structure
    from umx.search import rebuild_index

    state = detect_schema_state(repo_dir)
    if state.state == "current":
        return SchemaRepairResult(
            from_version=state.found,
            to_version=CURRENT_SCHEMA_VERSION,
        )
    if not state.fixable:
        raise RuntimeError(state.message)

    ensure_repo_structure(repo_dir, ensure_schema=False)
    applied: list[str] = []
    if write_schema_version(repo_dir):
        applied.append(f"set meta/schema_version to {CURRENT_SCHEMA_VERSION}")

    if ensure_memory_schema_header(repo_dir):
        applied.append(f"updated meta/MEMORY.md schema_version to {CURRENT_SCHEMA_VERSION}")

    rebuild_index(repo_dir, config=config)
    applied.append("rebuilt local search indexes")
    return SchemaRepairResult(
        from_version=state.found,
        to_version=CURRENT_SCHEMA_VERSION,
        applied=applied,
        rebuilt_index=True,
    )
