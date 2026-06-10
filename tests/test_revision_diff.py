"""Revision comparison: every changed assignment gets an explanation."""

from typing import Any

from fl_op.planning.revision_diff import diff_revision_pair


def _assignment(task_id: str, bundle_id: str, start: str = "2026-06-05T08:00:00", **extra) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "bundle_id": bundle_id,
        "planned_start": start,
        "is_frozen": False,
        **extra,
    }


def _plan(assignments: list[dict], unassigned: list[dict] = None, corrective: list[dict] = None) -> dict[str, Any]:
    return {
        "revision_id": "rev-x",
        "assignments": assignments,
        "unassigned_tasks": unassigned or [],
        "corrective_actions": corrective or [],
    }


_TRIGGER = {
    "revision": 2,
    "trigger": "asset.unavailable",
    "trigger_entity_ref": "vehicle_1",
    "n_coalesced_events": 1,
}


def test_corrective_action_explains_reassignment() -> None:
    prev = _plan([_assignment("order_1", "bundle-a")])
    new = _plan(
        [_assignment("order_1", "bundle-b")],
        corrective=[
            {
                "action": "reassigned-after-asset-loss",
                "task_id": "order_1",
                "detail": "assignment lost assets ['vehicle_1']; task re-solved",
            }
        ],
    )
    diff = diff_revision_pair(prev, new, _TRIGGER)
    assert len(diff["changes"]) == 1
    change = diff["changes"][0]
    assert change["change"] == "reassigned"
    assert "reassigned-after-asset-loss" in change["explanation"]
    assert change["from_bundle"] == "bundle-a"
    assert change["to_bundle"] == "bundle-b"


def test_unchanged_assignments_are_counted_not_listed() -> None:
    prev = _plan([_assignment("order_1", "bundle-a"), _assignment("order_2", "bundle-b")])
    new = _plan([_assignment("order_1", "bundle-a"), _assignment("order_2", "bundle-b")])
    diff = diff_revision_pair(prev, new, _TRIGGER)
    assert diff["changes"] == []
    assert diff["n_unchanged"] == 2


def test_new_service_task_is_explained_as_monitoring_derived() -> None:
    prev = _plan([])
    new = _plan([_assignment("service-sensor_1", "bundle-s")])
    diff = diff_revision_pair(prev, new, _TRIGGER)
    assert diff["changes"][0]["change"] == "assigned"
    assert "monitoring-derived" in diff["changes"][0]["explanation"]


def test_newly_unassignable_task_quotes_reason_code() -> None:
    prev = _plan([_assignment("order_1", "bundle-a")])
    new = _plan([], unassigned=[{"task_id": "order_1", "reason_code": "NO_COMPATIBLE_BUNDLE"}])
    diff = diff_revision_pair(prev, new, _TRIGGER)
    change = diff["changes"][0]
    assert change["change"] == "unassigned"
    assert "NO_COMPATIBLE_BUNDLE" in change["explanation"]
    assert "asset.unavailable:vehicle_1" in change["explanation"]


def test_withdrawn_service_task_uses_corrective_record() -> None:
    prev = _plan([_assignment("service-sensor_1", "bundle-s")])
    new = _plan(
        [],
        corrective=[
            {
                "action": "service-withdrawn",
                "task_id": "service-sensor_1",
                "detail": "monitoring:sensor_1:battery-low:15.0pct",
            }
        ],
    )
    diff = diff_revision_pair(prev, new, _TRIGGER)
    change = diff["changes"][0]
    assert change["change"] == "removed"
    assert "service-withdrawn" in change["explanation"]


def test_plain_resolve_change_is_marked_optimization_tradeoff() -> None:
    prev = _plan([_assignment("order_1", "bundle-a")])
    new = _plan([_assignment("order_1", "bundle-b", change_penalty=1000)])
    diff = diff_revision_pair(prev, new, _TRIGGER)
    assert "optimization tradeoff" in diff["changes"][0]["explanation"]