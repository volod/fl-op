"""OR-Tools rolling-dispatch adapter (spec 19), Python-native (no Timefold/JVM).

Evolves the existing reschedule logic into a proper adapter behind the SPI. It
freezes started and imminent tasks, re-solves the remainder via the shared solver
chain, and emits an immutable plan revision with plan-instability tracking. The
freeze window is applied in the adapter layer, not in the solver internals.
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Optional

from fl_op.adapters.base import dispatch_to_assignment, infeasible_to_unassigned
from fl_op.adapters.spi import AdapterHealth, AdapterManifest, ValidationReport
from fl_op.adapters.base import validate_profile_against_features
from fl_op.canonical.common import RiskSummary
from fl_op.canonical.enums import PlanningMode, PlanStatus
from fl_op.canonical.plan import Assignment, Plan
from fl_op.core.constants import (
    ADAPTER_ORTOOLS_ROLLING_ID,
    ADAPTER_VERSION,
    DEFAULT_CHANGE_PENALTY,
    FREEZE_WINDOW_MINUTES,
    INTEGER_SCALING_POLICY_VERSION,
)
from fl_op.solver.chain import run_solver_chain

if TYPE_CHECKING:
    from fl_op.canonical.snapshot import PlanningSnapshot
    from fl_op.contracts.profile import OptimizationProfile

logger = logging.getLogger(__name__)

_SUPPORTED_CONSTRAINTS = {
    "compatible-equipment",
    "sufficient-power",
    "asset-available",
    "no-double-booking",
    "respect-contract-time-window",
    "protect-frozen-tasks",
}
_SUPPORTED_FEATURES = {
    "rolling-dispatch",
    "freeze-window",
    "pinned-tasks",
    "plan-instability-penalty",
    "shared-resource-exclusivity",
}

_STARTED_STATUS = "started"


class RollingSolveResult:
    """Compiled-and-solved rolling state passed to normalize()."""

    def __init__(
        self,
        chain_result,
        frozen_assignments: list[Assignment],
        carried_forward: list[Assignment],
        previous_by_task: dict[str, Assignment],
        now: datetime,
    ) -> None:
        self.chain_result = chain_result
        self.frozen_assignments = frozen_assignments
        self.carried_forward = carried_forward
        self.previous_by_task = previous_by_task
        self.now = now


class OrToolsRollingAdapter:
    """Rolling-dispatch adapter satisfying the SolverAdapter protocol."""

    @property
    def manifest(self) -> AdapterManifest:
        from fl_op.adapters.ortools_periodic import _ortools_version

        return AdapterManifest(
            adapter_id=ADAPTER_ORTOOLS_ROLLING_ID,
            adapter_version=ADAPTER_VERSION,
            solver_name="google-ortools",
            solver_version=_ortools_version(),
            supported_planning_modes=["rolling"],
            supported_features=sorted(_SUPPORTED_FEATURES),
            integer_scaling_policy_ref=INTEGER_SCALING_POLICY_VERSION,
        )

    def supports(self, feature: str) -> bool:
        return feature in _SUPPORTED_FEATURES or feature in _SUPPORTED_CONSTRAINTS

    def validate_profile(self, profile: "OptimizationProfile") -> ValidationReport:
        return validate_profile_against_features(profile, _SUPPORTED_CONSTRAINTS)

    def health(self) -> AdapterHealth:
        return AdapterHealth(healthy=True, detail="ortools rolling adapter ready")

    # -- freeze determination -----------------------------------------------------

    def _frozen_task_ids(
        self,
        snapshot: "PlanningSnapshot",
        previous_by_task: dict[str, Assignment],
        now: datetime,
    ) -> set[str]:
        cutoff = now + timedelta(minutes=FREEZE_WINDOW_MINUTES)
        frozen: set[str] = set()
        for task in snapshot.tasks:
            if task.status == _STARTED_STATUS:
                frozen.add(task.task_id)
        for task_id, prev in previous_by_task.items():
            if prev.planned_start <= cutoff:
                frozen.add(task_id)
        return frozen

    def compile(
        self,
        snapshot: "PlanningSnapshot",
        profile: "OptimizationProfile",
        config: dict[str, Any],
    ) -> RollingSolveResult:
        """Incremental replanning: freeze, carry forward unaffected work, re-solve the rest.

        To minimize avoidable disruption (spec 19.9), only tasks actually affected
        by the events since the last plan are re-optimized:
          - started or freeze-window tasks are frozen (preserved verbatim);
          - any non-frozen prior assignment whose task still exists and whose
            assets are all still available is carried forward unchanged;
          - the remainder (new tasks, and tasks whose asset disappeared) are the
            only orders sent to the solver.

        On a baseline build (no previous plan) every task is re-solved, exactly as
        a fresh periodic plan would be.
        """
        now = config.get("now") or snapshot.effective_at
        previous_plan: Optional[Plan] = config.get("previous_plan")
        previous_assignments = list(previous_plan.assignments) if previous_plan else []
        previous_by_task: dict[str, Assignment] = {
            a.task_id: a for a in previous_assignments
        }

        current_task_ids = {t.task_id for t in snapshot.tasks}
        available_asset_ids = {a.asset_id for a in snapshot.assets}

        frozen_ids = self._frozen_task_ids(snapshot, previous_by_task, now)
        frozen_assignments = [
            previous_by_task[tid].model_copy(update={"is_frozen": True})
            for tid in frozen_ids
            if tid in previous_by_task
        ]

        # Carry forward every non-frozen prior assignment that is unaffected by
        # the events: its task still exists and all its assets are still present.
        carried_forward: list[Assignment] = []
        for assignment in previous_assignments:
            tid = assignment.task_id
            if tid in frozen_ids or tid not in current_task_ids:
                continue
            if not set(assignment.asset_ids).issubset(available_asset_ids):
                continue  # an assigned asset became unavailable -> must re-solve
            carried_forward.append(assignment.model_copy(update={"is_frozen": False}))

        preserved = frozen_ids | {a.task_id for a in carried_forward}
        tasks_to_resolve = {t.task_id for t in snapshot.tasks if t.task_id not in preserved}

        chain_result = None
        if tasks_to_resolve:
            # Implements and operators held by preserved work are exclusive, so
            # exclude them from the re-solve to avoid double-booking. Each affected
            # task's own former implement/operator is NOT preserved, so it remains
            # available and the subset stays feasible. Vehicles are reused
            # sequentially across the horizon, so they are left available.
            held = [*frozen_assignments, *carried_forward]
            held_implements = {
                aid for a in held for aid in a.asset_ids if aid.startswith("implement")
            }
            held_operators = {op for a in held for op in a.operator_ids}

            payload = dict(snapshot.solver_payload)
            payload["orders"] = [
                o for o in payload.get("orders", []) if o["order_id"] in tasks_to_resolve
            ]
            payload["implements"] = [
                im for im in payload.get("implements", [])
                if im["implement_id"] not in held_implements
            ]
            payload["operators"] = [
                op for op in payload.get("operators", [])
                if op["operator_id"] not in held_operators
            ]
            chain_result = run_solver_chain(payload)

        logger.info(
            "Rolling replan: %d frozen, %d carried forward, %d re-solved",
            len(frozen_assignments), len(carried_forward), len(tasks_to_resolve),
        )
        return RollingSolveResult(
            chain_result, frozen_assignments, carried_forward, previous_by_task, now
        )

    def solve(self, solver_input: RollingSolveResult, config: dict[str, Any]) -> RollingSolveResult:
        # Solving already happened in compile (the chain runs on the filtered payload).
        return solver_input

    def normalize(
        self,
        raw_result: RollingSolveResult,
        snapshot: "PlanningSnapshot",
        profile: "OptimizationProfile",
    ) -> Plan:
        from fl_op.adapters.ortools_periodic import _ortools_version

        now = datetime.now(tz=timezone.utc)
        chain = raw_result.chain_result
        new_assignments: list[Assignment] = []
        unassigned = []
        kpis: dict[str, Any] = {}

        if chain is not None:
            kpis = chain.kpis
            for dp in chain.dispatch:
                assignment = dispatch_to_assignment(dp)
                prev = raw_result.previous_by_task.get(assignment.task_id)
                # A re-solved task counts as a genuine change only when its bundle
                # (vehicle + implement) differs from the previous plan. New tasks
                # have no predecessor and are additions, not changes.
                if prev is not None and prev.bundle_id != assignment.bundle_id:
                    assignment = assignment.model_copy(
                        update={
                            "previous_bundle_id": prev.bundle_id,
                            "previous_start_time": prev.planned_start,
                            "change_penalty": DEFAULT_CHANGE_PENALTY,
                        }
                    )
                new_assignments.append(assignment)
            unassigned = [infeasible_to_unassigned(inf) for inf in chain.infeasible]

        assignments = (
            raw_result.frozen_assignments
            + raw_result.carried_forward
            + new_assignments
        )

        # Parent/origin linkage is applied by plan() when a previous plan is given.
        n_changed = sum(1 for a in new_assignments if a.change_penalty > 0)
        n_new_tasks = sum(
            1 for a in new_assignments if a.task_id not in raw_result.previous_by_task
        )
        score = {
            "n_frozen": len(raw_result.frozen_assignments),
            "n_carried_forward": len(raw_result.carried_forward),
            "n_replanned": len(new_assignments),
            "n_new_tasks": n_new_tasks,
            "n_changed_after_freeze": n_changed,
            "plan_instability_penalty": n_changed * DEFAULT_CHANGE_PENALTY,
            "total_estimated_margin_eur": kpis.get("total_estimated_margin_eur", 0.0),
            "n_unassigned": len(unassigned),
        }

        revision_id = f"rev-{uuid.uuid4().hex[:8]}"
        return Plan(
            plan_id=f"plan-rolling-{snapshot.snapshot_hash[:8]}",
            revision_id=revision_id,
            parent_revision_id=None,
            origin_plan_id=f"plan-rolling-{snapshot.snapshot_hash[:8]}",
            planning_mode=PlanningMode.ROLLING,
            snapshot_id=snapshot.snapshot_id,
            snapshot_hash=snapshot.snapshot_hash,
            version_dimensions=snapshot.version_dimensions,
            adapter_id=ADAPTER_ORTOOLS_ROLLING_ID,
            adapter_version=ADAPTER_VERSION,
            solver_version=_ortools_version(),
            generated_at=now,
            effective_from=snapshot.effective_at,
            effective_to=snapshot.planning_horizon.to,
            status=PlanStatus.DRAFT,
            assignments=assignments,
            unassigned_tasks=unassigned,
            score=score,
            quality_summary=snapshot.quality_summary,
            risk_summary=RiskSummary(n_contract_deadlines_at_risk=len(unassigned)),
            lineage_ref=snapshot.lineage_ref,
        )

    # Convenience: full compile -> normalize producing a revision linked to a parent.
    def plan(
        self,
        snapshot: "PlanningSnapshot",
        profile: "OptimizationProfile",
        config: dict[str, Any] | None = None,
    ) -> Plan:
        config = config or {}
        raw = self.compile(snapshot, profile, config)
        plan = self.normalize(self.solve(raw, config), snapshot, profile)
        previous_plan: Optional[Plan] = config.get("previous_plan")
        if previous_plan is not None:
            plan = plan.model_copy(
                update={
                    "parent_revision_id": previous_plan.revision_id,
                    "origin_plan_id": previous_plan.origin_plan_id,
                }
            )
        return plan
