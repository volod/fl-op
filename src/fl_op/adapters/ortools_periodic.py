"""OR-Tools periodic-planning adapter.

Wraps the existing solve chain unchanged: it consumes a snapshot's solver payload
and emits a canonical periodic Plan with assignments and normalized unassigned
reason codes. The solver internals are never modified.
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from fl_op.adapters.base import (
    dispatch_to_assignment,
    infeasible_to_unassigned,
    validate_profile_against_features,
)
from fl_op.adapters.spi import AdapterHealth, AdapterManifest, ValidationReport
from fl_op.canonical.common import RiskSummary
from fl_op.canonical.enums import PlanningMode, PlanStatus
from fl_op.canonical.plan import Plan
from fl_op.core.constants import (
    ADAPTER_ORTOOLS_PERIODIC_ID,
    ADAPTER_VERSION,
    INTEGER_SCALING_POLICY_VERSION,
)
from fl_op.solver.chain import SolverChainResult, run_solver_chain

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
    "operator-qualified",
    "required-material-available",
    "respect-weather-window",
}
_SUPPORTED_FEATURES = {
    "periodic-planning",
    "shared-resource-exclusivity",
    "prize-collecting",
    "route-refinement",
}


class OrToolsPeriodicAdapter:
    """Periodic CP-SAT/routing adapter satisfying the SolverAdapter protocol."""

    @property
    def manifest(self) -> AdapterManifest:
        return AdapterManifest(
            adapter_id=ADAPTER_ORTOOLS_PERIODIC_ID,
            adapter_version=ADAPTER_VERSION,
            solver_name="google-ortools",
            solver_version=_ortools_version(),
            supported_planning_modes=["periodic"],
            supported_features=sorted(_SUPPORTED_FEATURES),
            integer_scaling_policy_ref=INTEGER_SCALING_POLICY_VERSION,
        )

    def supports(self, feature: str) -> bool:
        return feature in _SUPPORTED_FEATURES or feature in _SUPPORTED_CONSTRAINTS

    def validate_profile(self, profile: "OptimizationProfile") -> ValidationReport:
        return validate_profile_against_features(profile, _SUPPORTED_CONSTRAINTS)

    def compile(
        self,
        snapshot: "PlanningSnapshot",
        profile: "OptimizationProfile",
        config: dict[str, Any],
    ) -> dict[str, Any]:
        # Project the canonical snapshot into the solver's working rows.
        from fl_op.solver.inputs import build_solver_inputs

        return build_solver_inputs(snapshot)

    def solve(self, solver_input: dict[str, Any], config: dict[str, Any]) -> SolverChainResult:
        return run_solver_chain(solver_input, enforcement=config.get("enforcement"))

    def normalize(
        self,
        raw_result: SolverChainResult,
        snapshot: "PlanningSnapshot",
        profile: "OptimizationProfile",
    ) -> Plan:
        return _build_plan(
            raw_result,
            snapshot,
            adapter_id=ADAPTER_ORTOOLS_PERIODIC_ID,
            planning_mode=PlanningMode.PERIODIC,
        )

    def health(self) -> AdapterHealth:
        return AdapterHealth(healthy=True, detail="ortools periodic adapter ready")

    # Convenience: full compile -> solve -> normalize.
    def plan(
        self,
        snapshot: "PlanningSnapshot",
        profile: "OptimizationProfile",
        config: dict[str, Any] | None = None,
    ) -> Plan:
        config = config or {}
        report = self.validate_profile(profile)
        if not report.ok:
            raise ValueError(f"profile incompatible with adapter: {report.messages}")
        from fl_op.solver.enforcement import EnforcementPolicy

        config.setdefault("enforcement", EnforcementPolicy.from_profile(profile))
        compiled = self.compile(snapshot, profile, config)
        raw = self.solve(compiled, config)
        return self.normalize(raw, snapshot, profile)


def _ortools_version() -> str:
    try:
        from ortools.init.python import init  # type: ignore

        return init.OrToolsVersion.version_string()
    except Exception:  # noqa: BLE001
        return "unknown"


def _build_plan(
    raw: SolverChainResult,
    snapshot: "PlanningSnapshot",
    adapter_id: str,
    planning_mode: PlanningMode,
    parent_revision_id: str | None = None,
) -> Plan:
    """Shared plan construction used by periodic and rolling adapters."""
    now = datetime.now(tz=timezone.utc)
    plan_id = f"plan-{planning_mode.value}-{snapshot.snapshot_hash[:8]}"
    revision_id = f"rev-{uuid.uuid4().hex[:8]}"

    assignments = [dispatch_to_assignment(dp) for dp in raw.dispatch]
    unassigned = [infeasible_to_unassigned(inf) for inf in raw.infeasible]

    kpis = raw.kpis
    score = {
        "total_estimated_margin_eur": kpis.get("total_estimated_margin_eur", 0.0),
        "greedy_baseline_margin_eur": kpis.get("greedy_baseline_margin_eur", 0.0),
        "solver_improvement_eur": kpis.get("solver_improvement_eur", 0.0),
        "total_fuel_l": kpis.get("total_fuel_l", 0.0),
        "total_fertilizer_kg": kpis.get("total_fertilizer_kg", 0.0),
        "n_dispatched": kpis.get("n_dispatched", len(assignments)),
        "n_unassigned": kpis.get("n_infeasible", len(unassigned)),
        "n_clusters": raw.n_clusters,
        "n_greedy_warm_start_assignments": len(raw.greedy_assignment),
    }
    risk = RiskSummary(
        n_contract_deadlines_at_risk=len(unassigned),
        total_penalty_exposure_eur=sum(
            t.penalty_per_day_eur
            for t in snapshot.tasks
            if t.task_id in {u.task_id for u in unassigned}
        ),
    )

    return Plan(
        plan_id=plan_id,
        revision_id=revision_id,
        parent_revision_id=parent_revision_id,
        origin_plan_id=plan_id,
        planning_mode=planning_mode,
        snapshot_id=snapshot.snapshot_id,
        snapshot_hash=snapshot.snapshot_hash,
        version_dimensions=snapshot.version_dimensions,
        adapter_id=adapter_id,
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
        risk_summary=risk,
        lineage_ref=snapshot.lineage_ref,
    )
