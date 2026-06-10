"""Revision comparison: explain why every changed assignment moved.

Compares consecutive revisions of a rolling-plan run and produces, per
revision, one explained change record per task whose assignment differs from
the parent revision. Explanations come from the revision's own artifacts: the
triggering event, corrective actions (asset loss, service withdrawal or
escalation), freeze markers, and plan-instability markers
(previous bundle / change penalty).
"""

import json
import logging
import pathlib
from typing import Any, Optional

from fl_op.core.constants import ARTIFACT_SCHEMA_VERSION
from fl_op.core.paths import DATA_ROOT
from fl_op.planning.artifacts import run_timestamp, write_json

logger = logging.getLogger(__name__)

_PLAN_ROLLING_DIRNAME = "plan-rolling"
_REVISION_DIFF_DIRNAME = "revision-diff"


def resolve_plan_dir(plan: str) -> pathlib.Path:
    """Resolve 'latest' (or an explicit path) to a rolling-plan run directory."""
    if plan == "latest":
        base = DATA_ROOT / _PLAN_ROLLING_DIRNAME
        runs = sorted(d for d in base.iterdir() if d.is_dir()) if base.exists() else []
        if not runs:
            raise FileNotFoundError(f"No rolling-plan runs under {base}")
        return runs[-1]
    path = pathlib.Path(plan)
    if not (path / "revisions_summary.json").exists():
        raise FileNotFoundError(f"{path} is not a rolling-plan run directory")
    return path


def _load_run(plan_dir: pathlib.Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    summary = json.loads((plan_dir / "revisions_summary.json").read_text())["revisions"]
    revisions = []
    for entry in summary:
        rev_path = plan_dir / "revisions" / f"{entry['revision']:03d}" / "plan.json"
        revisions.append(json.loads(rev_path.read_text()))
    return revisions, summary


def _task_universe(plan: dict[str, Any]) -> set[str]:
    return {a["task_id"] for a in plan.get("assignments", [])} | {
        u["task_id"] for u in plan.get("unassigned_tasks", [])
    }


def _trigger_text(trigger: dict[str, Any]) -> str:
    label = trigger.get("trigger", "event")
    entity = trigger.get("trigger_entity_ref", "")
    return f"{label}:{entity}" if entity else label


def diff_revision_pair(
    prev: dict[str, Any],
    new: dict[str, Any],
    trigger: dict[str, Any],
) -> dict[str, Any]:
    """Explain every task whose assignment differs between two revisions."""
    prev_by_task = {a["task_id"]: a for a in prev.get("assignments", [])}
    new_by_task = {a["task_id"]: a for a in new.get("assignments", [])}
    new_unassigned = {u["task_id"]: u for u in new.get("unassigned_tasks", [])}
    prev_unassigned = {u["task_id"]: u for u in prev.get("unassigned_tasks", [])}
    corrective = {ca["task_id"]: ca for ca in new.get("corrective_actions", [])}
    cause = _trigger_text(trigger)

    changes: list[dict[str, Any]] = []

    def record(task_id: str, change: str, explanation: str,
               from_a: Optional[dict[str, Any]], to_a: Optional[dict[str, Any]]) -> None:
        changes.append(
            {
                "task_id": task_id,
                "change": change,
                "explanation": explanation,
                "from_bundle": (from_a or {}).get("bundle_id"),
                "to_bundle": (to_a or {}).get("bundle_id"),
            }
        )

    for task_id in sorted(set(prev_by_task) | set(new_by_task) | set(new_unassigned)):
        old = prev_by_task.get(task_id)
        cur = new_by_task.get(task_id)
        action = corrective.get(task_id)

        if old is not None and cur is not None:
            if cur.get("bundle_id") == old.get("bundle_id") and cur.get(
                "planned_start"
            ) == old.get("planned_start"):
                continue  # carried forward or frozen verbatim
            if action is not None:
                explanation = f"{action['action']}: {action['detail']}"
            elif cur.get("is_frozen"):
                explanation = "frozen (started or inside freeze window); start shifted only"
            else:
                explanation = (
                    f"re-solved after {cause}; resources were reallocated "
                    "(optimization tradeoff, change penalty applied)"
                )
            record(task_id, "reassigned", explanation, old, cur)
        elif old is None and cur is not None:
            if task_id in prev_unassigned:
                explanation = f"previously unassigned; became feasible after {cause}"
            elif task_id not in _task_universe(prev):
                origin = (
                    "monitoring-derived service task"
                    if task_id.startswith("service-")
                    else f"entered planning via {cause}"
                )
                explanation = f"new task: {origin}"
            else:
                explanation = f"assigned after {cause}"
            record(task_id, "assigned", explanation, None, cur)
        elif old is not None and cur is None:
            if task_id in new_unassigned:
                reason = new_unassigned[task_id].get("reason_code", "UNKNOWN")
                explanation = f"became unassignable after {cause}: {reason}"
                record(task_id, "unassigned", explanation, old, None)
            elif action is not None:
                record(task_id, "removed", f"{action['action']}: {action['detail']}", old, None)
            else:
                explanation = f"left planning after {cause} (cancelled or completed)"
                record(task_id, "removed", explanation, old, None)

    n_unchanged = sum(
        1
        for task_id, cur in new_by_task.items()
        if task_id in prev_by_task
        and cur.get("bundle_id") == prev_by_task[task_id].get("bundle_id")
        and cur.get("planned_start") == prev_by_task[task_id].get("planned_start")
    )
    return {
        "revision": trigger.get("revision"),
        "revision_id": new.get("revision_id"),
        "trigger": _trigger_text(trigger),
        "n_coalesced_events": trigger.get("n_coalesced_events", 1),
        "n_unchanged": n_unchanged,
        "changes": changes,
    }


def _write_text_report(diffs: list[dict[str, Any]], path: pathlib.Path) -> None:
    lines = ["Revision Diff Report", "=" * 40]
    for diff in diffs:
        lines.append("")
        lines.append(
            f"revision {diff['revision']} ({diff['trigger']}): "
            f"{len(diff['changes'])} changed, {diff['n_unchanged']} unchanged"
        )
        for change in diff["changes"]:
            bundle = ""
            if change["from_bundle"] or change["to_bundle"]:
                bundle = f" [{change['from_bundle'] or '-'} -> {change['to_bundle'] or '-'}]"
            lines.append(
                f"  {change['change']:<10} {change['task_id']}{bundle}: {change['explanation']}"
            )
    path.write_text("\n".join(lines) + "\n")


def run_revision_diff(plan: str = "latest") -> pathlib.Path:
    """Compare consecutive revisions of a rolling run; write explained diffs."""
    plan_dir = resolve_plan_dir(plan)
    revisions, summary = _load_run(plan_dir)
    diffs = [
        diff_revision_pair(revisions[n - 1], revisions[n], summary[n])
        for n in range(1, len(revisions))
    ]

    out_dir = DATA_ROOT / _REVISION_DIFF_DIRNAME / run_timestamp()
    write_json(
        {
            "schema_version": ARTIFACT_SCHEMA_VERSION,
            "plan_run": str(plan_dir),
            "revision_diffs": diffs,
        },
        out_dir / "revision_diff.json",
    )
    _write_text_report(diffs, out_dir / "revision_diff.txt")

    n_changes = sum(len(d["changes"]) for d in diffs)
    logger.info(
        "Revision diff for %s: %d revisions compared, %d explained changes -> %s",
        plan_dir,
        len(diffs),
        n_changes,
        out_dir,
    )
    return out_dir
