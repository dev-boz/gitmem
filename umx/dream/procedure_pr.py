"""Procedure revision PR body renderer for Dream pipeline."""
from __future__ import annotations

import dataclasses
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_PROC_DELTA_RE = re.compile(
    r"<!--\s*umx-procedure-delta:\s*(\{.*?\})\s*-->",
    re.DOTALL,
)


@dataclass
class ProcedureDelta:
    procedure_id: str
    title: str
    change_type: str  # "added", "modified", "deprecated", "encoding_strength_change"
    old_strength: int = 0
    new_strength: int = 0
    affected_task_classes: list[str] = field(default_factory=list)
    rationale: str = ""
    eval_trigger_ref: str = ""


def render_procedure_revision_pr_body(
    deltas: list[ProcedureDelta],
    *,
    repo_dir: Path | None = None,
) -> str:
    """Render a Markdown PR body for procedure revisions."""
    n = len(deltas)

    # Build the embedded JSON metadata comment
    delta_payload: dict[str, Any] = {
        "deltas": [dataclasses.asdict(d) for d in deltas],
        "schema_version": "0.6",
    }
    delta_json = json.dumps(delta_payload, separators=(", ", ": "))

    # Build table rows
    rows: list[str] = []
    for d in deltas:
        classes = ", ".join(d.affected_task_classes) if d.affected_task_classes else "—"
        strength = f"S:{d.old_strength}→S:{d.new_strength}"
        rationale = d.rationale or "—"
        rows.append(
            f"| `{d.procedure_id}` | {d.change_type} | {classes} | {strength} | {rationale} |"
        )
    table_rows = "\n".join(rows)

    # Determine eval trigger section text
    # Collect unique non-empty trigger refs
    refs = [d.eval_trigger_ref for d in deltas if d.eval_trigger_ref]
    if refs:
        trigger_text = "\n".join(f"- {ref}" for ref in refs)
    else:
        trigger_text = "No explicit eval trigger — routine revision."

    body = (
        f"<!-- umx-pr-type: procedure_revision -->\n"
        f"<!-- umx-procedure-delta: {delta_json} -->\n"
        f"\n"
        f"## Procedure Revisions\n"
        f"\n"
        f"This PR revises {n} procedure(s) based on eval feedback and dream pipeline analysis.\n"
        f"\n"
        f"### Changes\n"
        f"\n"
        f"| Procedure | Change | Task Classes | Strength | Rationale |\n"
        f"|-----------|--------|-------------|----------|-----------|\n"
        f"{table_rows}\n"
        f"\n"
        f"### Eval Trigger\n"
        f"\n"
        f"{trigger_text}\n"
        f"\n"
        f"### Review Checklist\n"
        f"\n"
        f"- [ ] Procedure change is backed by empirical evidence\n"
        f"- [ ] Affected task classes verified\n"
        f"- [ ] Encoding strength change is justified\n"
        f"- [ ] No contradictions with existing procedures\n"
    )
    return body


def parse_procedure_revision_pr_body(body: str) -> list[ProcedureDelta] | None:
    """Find and parse the umx-procedure-delta comment. Returns None if not found/malformed."""
    match = _PROC_DELTA_RE.search(body)
    if match is None:
        return None
    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    raw_deltas = payload.get("deltas")
    if not isinstance(raw_deltas, list):
        return None
    result: list[ProcedureDelta] = []
    for entry in raw_deltas:
        if not isinstance(entry, dict):
            return None
        try:
            result.append(ProcedureDelta(**entry))
        except TypeError:
            return None
    return result


def build_procedure_delta_from_eval_trigger(
    trigger_dict: dict[str, Any],
    *,
    procedures_dir: Path | None = None,
) -> list[ProcedureDelta]:
    """Build ProcedureDelta objects from an IMX dream trigger record.

    Only handles trigger_type in ("procedure_regression", "policy_drift").
    Returns empty list if procedures_dir is None or trigger type is unhandled.
    """
    if procedures_dir is None:
        return []

    trigger_type = trigger_dict.get("trigger_type", "")
    if trigger_type not in ("procedure_regression", "policy_drift"):
        return []

    context = trigger_dict.get("context", {})
    task_class = context.get("task_class", "") if isinstance(context, dict) else ""
    query = trigger_dict.get("query", "")
    task_id = context.get("task_id", "") if isinstance(context, dict) else ""

    deltas: list[ProcedureDelta] = []
    for proc_path in sorted(procedures_dir.glob("**/*.md")):
        try:
            content = proc_path.read_text(encoding="utf-8")
        except OSError:
            continue
        if task_class and task_class not in content:
            continue
        deltas.append(
            ProcedureDelta(
                procedure_id=proc_path.stem,
                title=proc_path.stem.replace("-", " ").strip(),
                change_type="modified",
                rationale=f"Regression detected: {query}",
                eval_trigger_ref=task_id,
            )
        )
    return deltas
