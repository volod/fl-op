"""OR-Tools rolling-dispatch adapter.

The adapter owns the SPI surface. Rolling-specific compile and normalization
logic lives in ``fl_op.adapters.rolling`` helper modules.
"""

import dataclasses
from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional

from fl_op.adapters.base import validate_profile_against_features
from fl_op.adapters.rolling.capabilities import (
    SUPPORTED_CONSTRAINTS,
    SUPPORTED_FEATURES,
)
from fl_op.adapters.rolling.compiler import compile_rolling_state, frozen_task_ids
from fl_op.adapters.rolling.normalizer import normalize_rolling_result
from fl_op.adapters.rolling.state import RollingSolveResult
from fl_op.adapters.spi import AdapterHealth, AdapterManifest, ValidationReport
from fl_op.canonical.plan import Assignment, Plan
from fl_op.core.constants import (
    ADAPTER_ORTOOLS_ROLLING_ID,
    ADAPTER_VERSION,
    INTEGER_SCALING_POLICY_VERSION,
)

if TYPE_CHECKING:
    from fl_op.canonical.snapshot import PlanningSnapshot
    from fl_op.contracts.profile import OptimizationProfile


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
            supported_features=sorted(SUPPORTED_FEATURES),
            integer_scaling_policy_ref=INTEGER_SCALING_POLICY_VERSION,
        )

    def supports(self, feature: str) -> bool:
        return feature in SUPPORTED_FEATURES or feature in SUPPORTED_CONSTRAINTS

    def validate_profile(self, profile: "OptimizationProfile") -> ValidationReport:
        return validate_profile_against_features(profile, SUPPORTED_CONSTRAINTS)

    def health(self) -> AdapterHealth:
        return AdapterHealth(healthy=True, detail="ortools rolling adapter ready")

    def _frozen_task_ids(
        self,
        snapshot: "PlanningSnapshot",
        previous_by_task: dict[str, Assignment],
        now: datetime,
    ) -> set[str]:
        return frozen_task_ids(snapshot, previous_by_task, now)

    def compile(
        self,
        snapshot: "PlanningSnapshot",
        profile: "OptimizationProfile",
        config: dict[str, Any],
    ) -> RollingSolveResult:
        return compile_rolling_state(snapshot, config)

    def solve(
        self,
        solver_input: RollingSolveResult,
        config: dict[str, Any],
    ) -> RollingSolveResult:
        # Solving already happened in compile (the chain runs on the filtered payload).
        return solver_input

    def normalize(
        self,
        raw_result: RollingSolveResult,
        snapshot: "PlanningSnapshot",
        profile: "OptimizationProfile",
    ) -> Plan:
        return normalize_rolling_result(raw_result, snapshot, profile)

    def plan(
        self,
        snapshot: "PlanningSnapshot",
        profile: "OptimizationProfile",
        config: dict[str, Any] | None = None,
    ) -> Plan:
        """Full compile -> normalize producing a revision linked to a parent."""
        config = config or {}
        from fl_op.solver.enforcement import EnforcementPolicy
        from fl_op.tuning.solver_profile import solver_parameters_for_profile

        config.setdefault("enforcement", EnforcementPolicy.from_profile(profile))
        # Profile allocation defaults plus optional reviewed tuned overlay.
        parameters = solver_parameters_for_profile(
            profile, explicit=config.get("parameters")
        )
        if config.get("objective"):
            parameters = dataclasses.replace(
                parameters,
                optimization_objective=str(config["objective"]),
            )
        config["parameters"] = parameters
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


__all__ = ["OrToolsRollingAdapter", "RollingSolveResult"]
