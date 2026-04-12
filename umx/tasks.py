from __future__ import annotations

from datetime import datetime

from umx.models import Fact, TaskStatus


def open_tasks(facts: list[Fact], include_abandoned: bool = False) -> list[Fact]:
    statuses = {TaskStatus.OPEN, TaskStatus.BLOCKED}
    if include_abandoned:
        statuses.add(TaskStatus.ABANDONED)
    return [fact for fact in facts if fact.task_status in statuses]


def auto_abandon_tasks(
    facts: list[Fact],
    now: datetime,
    abandon_days: int,
    usage_last_referenced: dict[str, datetime | None] | None = None,
) -> list[Fact]:
    usage_last_referenced = usage_last_referenced or {}
    updated: list[Fact] = []
    for fact in facts:
        if fact.task_status not in {TaskStatus.OPEN, TaskStatus.BLOCKED}:
            updated.append(fact)
            continue
        reference = usage_last_referenced.get(fact.fact_id) or fact.created
        age_days = max(0.0, (now - reference).total_seconds() / 86400)
        if age_days >= abandon_days:
            updated.append(fact.clone(task_status=TaskStatus.ABANDONED))
        else:
            updated.append(fact)
    return updated
