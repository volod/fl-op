"""Solver-adapter service-provider interface (spec 21).

Every adapter compiles an immutable snapshot into a solver input, solves, and
normalizes the result into a canonical Plan. Capability validation ensures a
profile only runs on an adapter that supports every enforced constraint.
"""

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from fl_op.canonical.plan import Plan
    from fl_op.canonical.snapshot import PlanningSnapshot
    from fl_op.contracts.profile import OptimizationProfile


class AdapterManifest(BaseModel):
    """Published capabilities of a solver adapter (spec 21.1)."""

    adapter_id: str
    adapter_version: str
    solver_name: str
    solver_version: str
    supported_planning_modes: list[str] = Field(default_factory=list)
    supported_rule_operators: list[str] = Field(default_factory=list)
    supported_domain_functions: list[str] = Field(default_factory=list)
    supported_features: list[str] = Field(default_factory=list)
    unsupported_features: list[str] = Field(default_factory=list)
    integer_scaling_policy_ref: str = ""


class ValidationReport(BaseModel):
    """Result of validating a profile against an adapter's capabilities (spec 21.2)."""

    ok: bool
    unsupported_constraints: list[str] = Field(default_factory=list)
    messages: list[str] = Field(default_factory=list)


class AdapterHealth(BaseModel):
    healthy: bool = True
    detail: str = ""


@runtime_checkable
class SolverAdapter(Protocol):
    """The adapter contract every solver integration implements (spec 21)."""

    @property
    def manifest(self) -> AdapterManifest: ...

    def validate_profile(self, profile: "OptimizationProfile") -> ValidationReport: ...

    def supports(self, feature: str) -> bool: ...

    def compile(
        self,
        snapshot: "PlanningSnapshot",
        profile: "OptimizationProfile",
        config: dict[str, Any],
    ) -> Any: ...

    def solve(self, solver_input: Any, config: dict[str, Any]) -> Any: ...

    def normalize(
        self,
        raw_result: Any,
        snapshot: "PlanningSnapshot",
        profile: "OptimizationProfile",
    ) -> "Plan": ...

    def health(self) -> AdapterHealth: ...
