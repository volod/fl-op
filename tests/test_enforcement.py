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
    kept, infeasible, blocked = apply_weather_filter(
        [_task("t1", "SPRAYING")], _SITES, forecasts, _WEATHER
    )
    assert kept == []
    assert infeasible[0]["reason_code"] == ReasonCode.NO_VALID_WEATHER_WINDOW.value
    assert blocked == {}


def test_weather_passes_with_one_compliant_window() -> None:
    forecasts = [_forecast("w1", wind=15.0), _forecast("w2", wind=3.0)]
    kept, infeasible, _ = apply_weather_filter(
        [_task("t1", "SPRAYING")], _SITES, forecasts, _WEATHER
    )
    assert len(kept) == 1
    assert infeasible == []


def test_weather_ignores_insensitive_operations_and_missing_data() -> None:
    forecasts = [_forecast("w1", wind=25.0)]
    kept, infeasible, blocked = apply_weather_filter(
        [_task("t1", "TILLAGE")], _SITES, forecasts, _WEATHER
    )
    assert len(kept) == 1 and infeasible == [] and blocked == {}
    kept, infeasible, blocked = apply_weather_filter(
        [_task("t2", "SPRAYING")], _SITES, [], _WEATHER
    )
    assert len(kept) == 1 and infeasible == [] and blocked == {}


def test_weather_reports_non_compliant_windows_as_blocked_intervals() -> None:
    forecasts = [
        ForecastRow.from_canonical_dict(
            {"forecast_id": "w1", "lat": 50.0, "lon": 28.0, "wind_speed": 15.0,
             "valid_from": "2027-06-01T06:00:00+00:00",
             "valid_to": "2027-06-01T12:00:00+00:00"}
        ),
        ForecastRow.from_canonical_dict(
            {"forecast_id": "w2", "lat": 50.0, "lon": 28.0, "wind_speed": 3.0,
             "valid_from": "2027-06-01T12:00:00+00:00",
             "valid_to": "2027-06-01T18:00:00+00:00"}
        ),
    ]
    kept, infeasible, blocked = apply_weather_filter(
        [_task("t1", "SPRAYING")], _SITES, forecasts, _WEATHER
    )
    assert len(kept) == 1 and infeasible == []
    from datetime import datetime

    start = int(datetime.fromisoformat("2027-06-01T06:00:00+00:00").timestamp())
    end = int(datetime.fromisoformat("2027-06-01T12:00:00+00:00").timestamp())
    assert blocked == {"t1": [(start, end)]}


def _spray_task(task_id: str, deadline: str) -> TaskRow:
    return TaskRow.from_canonical_dict(
        {
            "task_id": task_id,
            "operation_type": "SPRAYING",
            "location_ref": "field_1",
            "area": 10.0,
            "penalty_per_day": 100.0,
            "deadline": deadline,
        }
    )


def _epoch(ts: str) -> int:
    from datetime import datetime

    return int(datetime.fromisoformat(ts).timestamp())


_CONSERVATIVE = WeatherPolicySpec(
    sensitivity={"SPRAYING": ["wind", "rain"]}, requireForecastCoverage=True
)


def test_conservative_weather_blocks_uncovered_horizon_tail() -> None:
    """A compliant window covering only part of [now, deadline] keeps the task but
    blocks the uncovered tail; lenient mode would leave it unblocked."""
    from datetime import datetime

    now = datetime.fromisoformat("2027-06-01T06:00:00+00:00")
    forecasts = [
        ForecastRow.from_canonical_dict(
            {"forecast_id": "w1", "lat": 50.0, "lon": 28.0, "wind_speed": 3.0,
             "valid_from": "2027-06-01T06:00:00+00:00",
             "valid_to": "2027-06-01T12:00:00+00:00"}
        )
    ]
    task = _spray_task("t1", "2027-06-01T18:00:00+00:00")
    kept, infeasible, blocked = apply_weather_filter(
        [task], _SITES, forecasts, _CONSERVATIVE, now=now
    )
    assert len(kept) == 1 and infeasible == []
    assert blocked == {"t1": [(_epoch("2027-06-01T12:00:00+00:00") + 1,
                              _epoch("2027-06-01T18:00:00+00:00"))]}

    # Lenient policy leaves the same tail open (only non-compliant windows block).
    lenient = WeatherPolicySpec(sensitivity={"SPRAYING": ["wind", "rain"]})
    _, _, lenient_blocked = apply_weather_filter(
        [task], _SITES, forecasts, lenient, now=now
    )
    assert lenient_blocked == {}


def test_conservative_weather_drops_task_without_horizon_coverage() -> None:
    """A compliant window entirely before ``now`` proves nothing about the horizon,
    so the conservative filter declares the task infeasible."""
    from datetime import datetime

    now = datetime.fromisoformat("2027-06-01T06:00:00+00:00")
    forecasts = [
        ForecastRow.from_canonical_dict(
            {"forecast_id": "w1", "lat": 50.0, "lon": 28.0, "wind_speed": 3.0,
             "valid_from": "2027-06-01T00:00:00+00:00",
             "valid_to": "2027-06-01T05:00:00+00:00"}
        )
    ]
    task = _spray_task("t1", "2027-06-01T18:00:00+00:00")
    kept, infeasible, blocked = apply_weather_filter(
        [task], _SITES, forecasts, _CONSERVATIVE, now=now
    )
    assert kept == [] and blocked == {}
    assert infeasible[0]["reason_code"] == ReasonCode.NO_VALID_WEATHER_WINDOW.value


def test_conservative_weather_full_coverage_blocks_nothing() -> None:
    """A compliant window spanning the whole horizon keeps the task with no gaps."""
    from datetime import datetime

    now = datetime.fromisoformat("2027-06-01T06:00:00+00:00")
    forecasts = [
        ForecastRow.from_canonical_dict(
            {"forecast_id": "w1", "lat": 50.0, "lon": 28.0, "wind_speed": 3.0,
             "valid_from": "2027-06-01T05:00:00+00:00",
             "valid_to": "2027-06-01T20:00:00+00:00"}
        )
    ]
    task = _spray_task("t1", "2027-06-01T18:00:00+00:00")
    kept, infeasible, blocked = apply_weather_filter(
        [task], _SITES, forecasts, _CONSERVATIVE, now=now
    )
    assert len(kept) == 1 and infeasible == [] and blocked == {}


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


def test_operator_qualification_pairs_backup_for_uncovered_task() -> None:
    cluster = _cluster(["t1", "t2"])
    order_index = {"t1": _task("t1", "TILLAGE"), "t2": _task("t2", "SPRAYING")}
    operators = {
        "op_1": OperatorRow.from_canonical_dict(
            {"asset_id": "op_1", "certified_operations": "['TILLAGE']"}
        ),
        "op_2": OperatorRow.from_canonical_dict(
            {"asset_id": "op_2", "certified_operations": "['SPRAYING']"}
        ),
    }
    infeasible = apply_operator_qualification([cluster], order_index, operators)
    assert infeasible == []
    assert cluster["task_ids"] == ["t1", "t2"]
    assert cluster["task_operators"] == {"t2": "op_2"}


def test_operator_pairing_skips_operators_claimed_by_other_clusters() -> None:
    cluster_a = _cluster(["t1"], operator_ref="op_1")
    cluster_b = _cluster([], operator_ref="op_2")
    order_index = {"t1": _task("t1", "SPRAYING")}
    operators = {
        "op_1": OperatorRow.from_canonical_dict(
            {"asset_id": "op_1", "certified_operations": "['TILLAGE']"}
        ),
        # Qualified but already staffing another cluster: not a free backup.
        "op_2": OperatorRow.from_canonical_dict(
            {"asset_id": "op_2", "certified_operations": "['SPRAYING']"}
        ),
    }
    infeasible = apply_operator_qualification(
        [cluster_a, cluster_b], order_index, operators
    )
    assert [i["task_id"] for i in infeasible] == ["t1"]
    assert cluster_a["task_ids"] == []


def test_operator_pairing_prefers_freest_backup() -> None:
    cluster = _cluster(["t1"])
    order_index = {"t1": _task("t1", "SPRAYING")}
    operators = {
        "op_1": OperatorRow.from_canonical_dict(
            {"asset_id": "op_1", "certified_operations": "['TILLAGE']"}
        ),
        "op_busy": OperatorRow.from_canonical_dict(
            {"asset_id": "op_busy", "certified_operations": "['SPRAYING']"}
        ),
        "op_free": OperatorRow.from_canonical_dict(
            {"asset_id": "op_free", "certified_operations": "['SPRAYING']"}
        ),
    }
    infeasible = apply_operator_qualification(
        [cluster], order_index, operators, {"op_busy": 0.2}
    )
    assert infeasible == []
    assert cluster["task_operators"] == {"t1": "op_free"}


def _task_tw(task_id: str, op: str, window: str) -> TaskRow:
    return TaskRow.from_canonical_dict(
        {
            "task_id": task_id,
            "operation_type": op,
            "location_ref": "field_1",
            "area": 10.0,
            "penalty_per_day": 100.0,
            "time_windows": [window],
        }
    )


def test_backup_shared_across_clusters_with_disjoint_windows() -> None:
    from datetime import datetime, timezone

    now = datetime(2026, 6, 14, 0, 0, tzinfo=timezone.utc)
    cluster_a = _cluster(["t1"], operator_ref="op_1")
    cluster_b = _cluster(["t2"], operator_ref="op_3")
    cluster_b["cluster_id"] = "c2"
    order_index = {
        "t1": _task_tw(
            "t1", "SPRAYING", "2026-06-14T01:00:00Z/2026-06-14T03:00:00Z"
        ),
        "t2": _task_tw(
            "t2", "SPRAYING", "2026-06-14T05:00:00Z/2026-06-14T07:00:00Z"
        ),
    }
    operators = {
        "op_1": OperatorRow.from_canonical_dict(
            {"asset_id": "op_1", "certified_operations": "['TILLAGE']"}
        ),
        "op_3": OperatorRow.from_canonical_dict(
            {"asset_id": "op_3", "certified_operations": "['TILLAGE']"}
        ),
        "op_backup": OperatorRow.from_canonical_dict(
            {"asset_id": "op_backup", "certified_operations": "['SPRAYING']"}
        ),
    }
    infeasible = apply_operator_qualification(
        [cluster_a, cluster_b], order_index, operators, None, now
    )
    assert infeasible == []
    # One backup covers both clusters because their windows do not overlap.
    assert cluster_a["task_operators"] == {"t1": "op_backup"}
    assert cluster_b["task_operators"] == {"t2": "op_backup"}


def test_backup_not_shared_when_windows_overlap() -> None:
    from datetime import datetime, timezone

    now = datetime(2026, 6, 14, 0, 0, tzinfo=timezone.utc)
    cluster_a = _cluster(["t1"], operator_ref="op_1")
    cluster_b = _cluster(["t2"], operator_ref="op_3")
    cluster_b["cluster_id"] = "c2"
    order_index = {
        "t1": _task_tw(
            "t1", "SPRAYING", "2026-06-14T01:00:00Z/2026-06-14T04:00:00Z"
        ),
        "t2": _task_tw(
            "t2", "SPRAYING", "2026-06-14T03:00:00Z/2026-06-14T06:00:00Z"
        ),
    }
    operators = {
        "op_1": OperatorRow.from_canonical_dict(
            {"asset_id": "op_1", "certified_operations": "['TILLAGE']"}
        ),
        "op_3": OperatorRow.from_canonical_dict(
            {"asset_id": "op_3", "certified_operations": "['TILLAGE']"}
        ),
        "op_backup": OperatorRow.from_canonical_dict(
            {"asset_id": "op_backup", "certified_operations": "['SPRAYING']"}
        ),
    }
    infeasible = apply_operator_qualification(
        [cluster_a, cluster_b], order_index, operators, None, now
    )
    # First cluster claims the only backup over its window; the overlapping
    # second cluster finds no free backup and its task is dropped.
    assert cluster_a["task_operators"] == {"t1": "op_backup"}
    assert [i["task_id"] for i in infeasible] == ["t2"]
    assert cluster_b["task_ids"] == []


def test_backup_shared_over_overlapping_windows_when_sequential_enabled(
    monkeypatch,
) -> None:
    from datetime import datetime, timezone

    from fl_op.solver import enforcement

    monkeypatch.setattr(enforcement, "OPERATOR_SHARING_SEQUENTIAL", True)
    now = datetime(2026, 6, 14, 0, 0, tzinfo=timezone.utc)
    cluster_a = _cluster(["t1"], operator_ref="op_1")
    cluster_b = _cluster(["t2"], operator_ref="op_3")
    cluster_b["cluster_id"] = "c2"
    order_index = {
        "t1": _task_tw(
            "t1", "SPRAYING", "2026-06-14T01:00:00Z/2026-06-14T04:00:00Z"
        ),
        "t2": _task_tw(
            "t2", "SPRAYING", "2026-06-14T03:00:00Z/2026-06-14T06:00:00Z"
        ),
    }
    operators = {
        "op_1": OperatorRow.from_canonical_dict(
            {"asset_id": "op_1", "certified_operations": "['TILLAGE']"}
        ),
        "op_3": OperatorRow.from_canonical_dict(
            {"asset_id": "op_3", "certified_operations": "['TILLAGE']"}
        ),
        "op_backup": OperatorRow.from_canonical_dict(
            {"asset_id": "op_backup", "certified_operations": "['SPRAYING']"}
        ),
    }
    infeasible = apply_operator_qualification(
        [cluster_a, cluster_b], order_index, operators, None, now
    )
    # The scarce backup is shared across the overlapping windows; neither task is
    # dropped, and both clusters are stamped so the pool serializes them.
    assert infeasible == []
    assert cluster_a["task_operators"] == {"t1": "op_backup"}
    assert cluster_b["task_operators"] == {"t2": "op_backup"}
    assert cluster_a["shared_backup_operators"] == ["op_backup"]
    assert cluster_b["shared_backup_operators"] == ["op_backup"]


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
    infeasible, reservations = apply_material_limits(
        [cluster], order_index, depots, demand
    )
    # 15 ha * 50 = 750 kg fits; the low-penalty 500 kg task exceeds the remaining 250.
    assert [i["task_id"] for i in infeasible] == ["t_low"]
    assert infeasible[0]["reason_code"] == ReasonCode.INSUFFICIENT_MATERIAL.value
    assert set(cluster["task_ids"]) == {"t_high", "t_other"}
    # The admitted charge is the reservation: one mechanism for feasibility
    # and the plan's MaterialReservation outputs.
    assert len(reservations) == 1
    reservation = reservations[0]
    assert reservation["task_id"] == "t_high"
    assert reservation["reservation_id"] == "res-t_high"
    assert reservation["material_type"] == "fertilizer"
    assert reservation["inventory_location_ref"] == "depot_1"
    assert reservation["quantity"] == 750.0
    assert reservation["status"] == "provisional"


def test_reservation_canonical_conversion_and_assignment_linking() -> None:
    from datetime import datetime, timezone

    from fl_op.adapters.base import link_reservation_refs, reservation_to_canonical
    from fl_op.canonical.enums import ReservationStatus
    from fl_op.canonical.plan import Assignment

    reservation = reservation_to_canonical(
        {
            "reservation_id": "res-t1",
            "task_id": "t1",
            "material_type": "fertilizer",
            "inventory_location_ref": "depot_1",
            "quantity": 750.0,
            "canonical_unit": "kg",
            "status": "confirmed",
            "reserved_from": "2027-06-01T08:00:00+00:00",
            "reserved_to": "2027-06-01T10:00:00+00:00",
        }
    )
    assert reservation.status == ReservationStatus.CONFIRMED
    assert reservation.reserved_from is not None
    assert reservation.quantity == 750.0

    def _assignment(task_id: str) -> Assignment:
        when = datetime(2027, 6, 1, 8, tzinfo=timezone.utc)
        return Assignment(
            assignment_id=f"a-{task_id}", task_id=task_id, bundle_id="b0",
            planned_start=when, planned_finish=when,
        )

    linked = link_reservation_refs(
        [_assignment("t1"), _assignment("t2")], [reservation]
    )
    assert linked[0].material_reservation_refs == ["res-t1"]
    assert linked[1].material_reservation_refs == []


def test_material_reservations_settle_against_final_dispatch() -> None:
    from fl_op.solver.enforcement import finalize_material_reservations

    reservations = [
        {"reservation_id": "res-t1", "task_id": "t1", "status": "provisional"},
        {"reservation_id": "res-t2", "task_id": "t2", "status": "provisional"},
    ]
    dispatch = [
        {"task_id": "t1", "scheduled_start": "2027-06-01T08:00:00+00:00",
         "scheduled_end": "2027-06-01T10:00:00+00:00"},
    ]
    settled = finalize_material_reservations(reservations, dispatch)
    by_id = {r["reservation_id"]: r for r in settled}
    assert by_id["res-t1"]["status"] == "confirmed"
    assert by_id["res-t1"]["reserved_from"] == "2027-06-01T08:00:00+00:00"
    assert by_id["res-t1"]["reserved_to"] == "2027-06-01T10:00:00+00:00"
    assert by_id["res-t2"]["status"] == "released"


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