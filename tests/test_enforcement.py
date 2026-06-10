"""Profile-constraint enforcement: weather windows, operator qualification, material."""

from fl_op.canonical.enums import ReasonCode
from fl_op.contracts.profile import (
    ConstraintSpec,
    MaterialDemandSpec,
    OptimizationProfile,
    ProfileMetadata,
    WeatherPolicySpec,
)
from fl_op.core.constants import XOPT_API_VERSION
from fl_op.solver.allocation.operators import assign_operator
from fl_op.solver.allocation.state import AllocationState
from fl_op.solver.enforcement import (
    EnforcementPolicy,
    apply_material_limits,
    apply_operator_qualification,
    apply_weather_filter,
)
from fl_op.solver.types import (
    ClusterSpec,
    DepotRow,
    ForecastRow,
    OperatorRow,
    SiteRow,
    TaskRow,
)


def _task(task_id: str, op: str, area: float = 10.0, penalty: float = 100.0) -> TaskRow:
    return TaskRow.from_canonical_dict(
        {
            "task_id": task_id,
            "operation_type": op,
            "location_ref": "field_1",
            "area": area,
            "penalty_per_day": penalty,
        }
    )


def _forecast(fid: str, wind: float, rain: float = 0.0) -> ForecastRow:
    return ForecastRow.from_canonical_dict(
        {"forecast_id": fid, "lat": 50.0, "lon": 28.0, "wind_speed": wind,
         "precipitation_rate": rain}
    )


_SITES = [SiteRow.from_canonical_dict({"location_id": "field_1", "lat": 50.0, "lon": 28.0})]
_WEATHER = WeatherPolicySpec(sensitivity={"SPRAYING": ["wind", "rain"]})


def test_weather_blocks_task_without_compliant_window() -> None:
    forecasts = [_forecast("w1", wind=15.0), _forecast("w2", wind=12.0)]
    kept, infeasible = apply_weather_filter(
        [_task("t1", "SPRAYING")], _SITES, forecasts, _WEATHER
    )
    assert kept == []
    assert infeasible[0]["reason_code"] == ReasonCode.NO_VALID_WEATHER_WINDOW.value


def test_weather_passes_with_one_compliant_window() -> None:
    forecasts = [_forecast("w1", wind=15.0), _forecast("w2", wind=3.0)]
    kept, infeasible = apply_weather_filter(
        [_task("t1", "SPRAYING")], _SITES, forecasts, _WEATHER
    )
    assert len(kept) == 1
    assert infeasible == []


def test_weather_ignores_insensitive_operations_and_missing_data() -> None:
    forecasts = [_forecast("w1", wind=25.0)]
    kept, infeasible = apply_weather_filter(
        [_task("t1", "TILLAGE")], _SITES, forecasts, _WEATHER
    )
    assert len(kept) == 1 and infeasible == []
    kept, infeasible = apply_weather_filter(
        [_task("t2", "SPRAYING")], _SITES, [], _WEATHER
    )
    assert len(kept) == 1 and infeasible == []


def _cluster(task_ids: list[str], operator_ref: str = "op_1") -> ClusterSpec:
    return {
        "cluster_id": "c1",
        "depot_ref": "depot_1",
        "task_ids": task_ids,
        "allocated_prime_related": {},
        "operator_ref": operator_ref,
        "total_penalty_per_day": 0.0,
    }


def test_operator_qualification_drops_uncertified_tasks() -> None:
    cluster = _cluster(["t1", "t2"])
    order_index = {"t1": _task("t1", "TILLAGE"), "t2": _task("t2", "SPRAYING")}
    operators = {
        "op_1": OperatorRow.from_canonical_dict(
            {"asset_id": "op_1", "certified_operations": "['TILLAGE']"}
        )
    }
    infeasible = apply_operator_qualification([cluster], order_index, operators)
    assert cluster["task_ids"] == ["t1"]
    assert infeasible[0]["task_id"] == "t2"
    assert infeasible[0]["reason_code"] == ReasonCode.NO_AVAILABLE_OPERATOR.value


def test_material_limit_serves_high_penalty_first() -> None:
    cluster = _cluster(["t_low", "t_high", "t_other"])
    order_index = {
        "t_high": _task("t_high", "FERTILIZING", area=15.0, penalty=900.0),
        "t_low": _task("t_low", "FERTILIZING", area=10.0, penalty=100.0),
        "t_other": _task("t_other", "TILLAGE", area=99.0),
    }
    depots = [
        DepotRow.from_canonical_dict({"location_id": "depot_1", "inventory_material": 1000.0})
    ]
    demand = {"FERTILIZING": MaterialDemandSpec(material="fertilizer", perAreaHa=50.0)}
    infeasible = apply_material_limits([cluster], order_index, depots, demand)
    # 15 ha * 50 = 750 kg fits; the low-penalty 500 kg task exceeds the remaining 250.
    assert [i["task_id"] for i in infeasible] == ["t_low"]
    assert infeasible[0]["reason_code"] == ReasonCode.INSUFFICIENT_MATERIAL.value
    assert set(cluster["task_ids"]) == {"t_high", "t_other"}


def test_allocator_prefers_operator_with_best_coverage() -> None:
    cluster = _cluster(["t1"], operator_ref="")
    narrow = OperatorRow.from_canonical_dict(
        {"asset_id": "op_narrow", "home_depot_ref": "depot_1",
         "certified_operations": ["TILLAGE"]}
    )
    broad = OperatorRow.from_canonical_dict(
        {"asset_id": "op_broad", "home_depot_ref": "depot_1",
         "certified_operations": ["TILLAGE", "SPRAYING", "SEEDING"]}
    )
    assign_operator(
        cluster,
        [narrow, broad],
        {"depot_1": [narrow, broad]},
        AllocationState(),
        cluster_operations={"TILLAGE", "SPRAYING"},
    )
    assert cluster["operator_ref"] == "op_broad"


def test_policy_from_profile_respects_enforced_flags() -> None:
    profile = OptimizationProfile(
        apiVersion=XOPT_API_VERSION,
        kind="OptimizationProfile",
        metadata=ProfileMetadata(id="p", version="0.1.0", semanticModelRef="urn:x"),
        constraints=[
            ConstraintSpec(id="respect-weather-window", severity="hard", enforced=True),
            ConstraintSpec(id="operator-qualified", severity="hard", enforced=False),
        ],
        materialDemand={"FERTILIZING": MaterialDemandSpec(material="fertilizer", perAreaHa=50.0)},
    )
    policy = EnforcementPolicy.from_profile(profile)
    assert policy.weather is not None
    assert policy.operator_qualification is False
    assert policy.material_demand == {}  # constraint not enforced