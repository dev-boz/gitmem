from __future__ import annotations

from dataclasses import asdict, dataclass, field
import importlib
from pathlib import Path

from umx.config import UMXConfig
from umx.memory import FACT_FILE_SCHEMA_VERSION, iter_fact_files, read_fact_file_schema_version

_MIGRATION_MODULES = ("0001_initial",)


@dataclass(slots=True, frozen=True)
class FactFileSchemaIssue:
    path: str
    found: int | None
    raw: str | None = None


@dataclass(slots=True, frozen=True)
class FactFileSchemaAudit:
    expected: int
    current_files: int = 0
    missing: list[FactFileSchemaIssue] = field(default_factory=list)
    stale: list[FactFileSchemaIssue] = field(default_factory=list)
    future: list[FactFileSchemaIssue] = field(default_factory=list)

    @property
    def status(self) -> str:
        return "ok" if not (self.missing or self.stale or self.future) else "warn"

    @property
    def state(self) -> str:
        return "current" if self.status == "ok" else "needs-migration"

    @property
    def from_version(self) -> int:
        versions = [issue.found for issue in (*self.stale, *self.future) if issue.found is not None]
        if self.missing:
            versions.append(0)
        if versions:
            return min(versions)
        return self.expected

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["status"] = self.status
        payload["state"] = self.state
        payload["from_version"] = self.from_version
        return payload


@dataclass(slots=True, frozen=True)
class MigrationRunResult:
    from_version: int
    to_version: int
    applied_migrations: list[str] = field(default_factory=list)
    applied: list[str] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def available_migrations() -> list[str]:
    return list(_MIGRATION_MODULES)


def inspect_fact_file_schema(repo_dir: Path) -> FactFileSchemaAudit:
    current_files = 0
    missing: list[FactFileSchemaIssue] = []
    stale: list[FactFileSchemaIssue] = []
    future: list[FactFileSchemaIssue] = []
    for path in iter_fact_files(repo_dir):
        found, raw = read_fact_file_schema_version(path)
        relative = path.relative_to(repo_dir).as_posix()
        if found is None:
            missing.append(FactFileSchemaIssue(path=relative, found=None, raw=raw))
        elif found < FACT_FILE_SCHEMA_VERSION:
            stale.append(FactFileSchemaIssue(path=relative, found=found, raw=raw))
        elif found > FACT_FILE_SCHEMA_VERSION:
            future.append(FactFileSchemaIssue(path=relative, found=found, raw=raw))
        else:
            current_files += 1
    return FactFileSchemaAudit(
        expected=FACT_FILE_SCHEMA_VERSION,
        current_files=current_files,
        missing=missing,
        stale=stale,
        future=future,
    )


def run_migrations(repo_dir: Path, *, config: UMXConfig | None = None) -> MigrationRunResult:
    audit = inspect_fact_file_schema(repo_dir)
    if audit.future:
        paths = ", ".join(issue.path for issue in audit.future)
        raise RuntimeError(
            f"fact-file schema_version is newer than supported schema {FACT_FILE_SCHEMA_VERSION}: {paths}"
        )
    if audit.state == "current":
        return MigrationRunResult(
            from_version=audit.from_version,
            to_version=FACT_FILE_SCHEMA_VERSION,
        )

    current_version = audit.from_version
    applied_migrations: list[str] = []
    applied: list[str] = []
    changed_files: list[str] = []
    for module_name in _MIGRATION_MODULES:
        module = importlib.import_module(f"umx.migrations.{module_name}")
        if not module.can_apply(current_version):
            continue
        result = module.apply(repo_dir, config=config)
        if result["changed_files"]:
            applied_migrations.append(module.MIGRATION_ID)
        applied.extend(result["applied"])
        changed_files.extend(result["changed_files"])
        current_version = result["to_version"]

    if current_version != FACT_FILE_SCHEMA_VERSION:
        raise RuntimeError(
            f"no migration path from fact-file schema {audit.from_version} to {FACT_FILE_SCHEMA_VERSION}"
        )
    return MigrationRunResult(
        from_version=audit.from_version,
        to_version=current_version,
        applied_migrations=applied_migrations,
        applied=applied,
        changed_files=changed_files,
    )
