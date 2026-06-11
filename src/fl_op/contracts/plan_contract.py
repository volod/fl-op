"""Validate produced plans against the canonical plan output contract.

The canonical plan contract (contracts/canonical/odcs/plan.odcs.yaml) mirrors
the input entity contracts: it declares the bindings a published plan must
carry. This module is the production-side counterpart of the input mapping
machinery: it resolves each declared binding against the Plan artifact payload
(the ``model_dump(mode="json")`` shape written to plan.json) and reports every
required binding that does not resolve to a value.
"""

import logging
from typing import TYPE_CHECKING, Any

from fl_op.contracts.canonical_model import load_canonical_model

if TYPE_CHECKING:
    from fl_op.canonical.plan import Plan

logger = logging.getLogger(__name__)

PLAN_ENTITY = "plan"

# Contract binding path -> plan-payload path. Record-level bindings address
# every element of a list field via "<list_field>[].<record_field>". This is
# the output-side analogue of the solver-input _CANONICAL_KEY table.
_PLAN_BINDING_PATHS: dict[str, str] = {
    "plan.planId": "plan_id",
    "plan.revisionId": "revision_id",
    "plan.parentRevisionId": "parent_revision_id",
    "plan.snapshotRef": "snapshot_id",
    "plan.planningMode": "planning_mode",
    "plan.status": "status",
    "plan.generatedAt": "generated_at",
    "plan.effectiveFrom": "effective_from",
    "plan.effectiveTo": "effective_to",
    "plan.assignment.assignmentId": "assignments[].assignment_id",
    "plan.assignment.taskRef": "assignments[].task_id",
    "plan.assignment.assetRefs": "assignments[].asset_ids",
    "plan.assignment.operatorRefs": "assignments[].operator_ids",
    "plan.assignment.plannedStart": "assignments[].planned_start",
    "plan.assignment.plannedFinish": "assignments[].planned_finish",
    "plan.assignment.expectedRevenue": "assignments[].expected_revenue_eur",
    "plan.assignment.expectedCost": "assignments[].expected_cost_eur",
    "plan.assignment.expectedMargin": "assignments[].expected_margin_eur",
    "plan.unassignedTask.taskRef": "unassigned_tasks[].task_id",
    "plan.unassignedTask.reasonCode": "unassigned_tasks[].reason_code",
    "plan.materialReservation.reservationId": "material_reservations[].reservation_id",
    "plan.materialReservation.taskRef": "material_reservations[].task_id",
    "plan.materialReservation.materialType": "material_reservations[].material_type",
    "plan.materialReservation.inventoryLocationRef": (
        "material_reservations[].inventory_location_ref"
    ),
    "plan.materialReservation.quantity": "material_reservations[].quantity",
    "plan.materialReservation.status": "material_reservations[].status",
}

_RECORD_PATH_SEPARATOR = "[]."


def _is_missing(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def validate_plan_payload(payload: dict[str, Any]) -> list[str]:
    """Check a plan artifact payload against the canonical plan contract.

    Returns one error per contract binding without a payload mapping and per
    required binding that does not resolve to a value (plan-level fields and
    every record of the addressed list). An empty list means the payload
    satisfies the contract.
    """
    model = load_canonical_model()
    errors: list[str] = []
    for fld in model.fields_for(PLAN_ENTITY):
        path = _PLAN_BINDING_PATHS.get(fld.binding)
        if path is None:
            errors.append(
                f"plan binding '{fld.binding}' has no payload path mapping"
            )
            continue
        if not fld.required:
            continue
        if _RECORD_PATH_SEPARATOR in path:
            list_field, record_field = path.split(_RECORD_PATH_SEPARATOR, 1)
            for n, record in enumerate(payload.get(list_field) or []):
                if _is_missing(record.get(record_field)):
                    errors.append(
                        f"{list_field}[{n}].{record_field}: required binding "
                        f"'{fld.binding}' unresolved"
                    )
        elif _is_missing(payload.get(path)):
            errors.append(
                f"{path}: required binding '{fld.binding}' unresolved"
            )
    return errors


def assert_plan_conforms(plan: "Plan") -> None:
    """Raise ValueError when a Plan violates the canonical output contract."""
    payload = plan.model_dump(mode="json", by_alias=True)
    errors = validate_plan_payload(payload)
    if errors:
        raise ValueError(
            f"plan {payload.get('plan_id', '<unknown>')} violates the canonical "
            f"plan contract: {errors}"
        )
    logger.debug(
        "Plan %s conforms to the canonical plan output contract",
        payload.get("plan_id", "<unknown>"),
    )
