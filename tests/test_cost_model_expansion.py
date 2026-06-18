"""Cost model expansion: per-link tolls priced off network-link distance, fixed
per-visit service fees, and per-vehicle/per-operator operating rates."""

from datetime import datetime, timedelta, timezone

from fl_op.core.constants import RATE_TYPE_SERVICE_FEE
from fl_op.solver.cost_rates import (
    ResourcePrices,
    operator_wage_eur_per_h,
    resolve_unit_price,
    vehicle_machine_wear_eur_per_h,
    vehicle_operating_eur_per_h,
)
from fl_op.solver.routing_model import build_node_table, build_vehicle_cost_matrices
from fl_op.solver.travel_time import (
    build_travel_lookup,
    network_distance_km,
    network_toll_eur,
)
from fl_op.solver.types import (
    CostRateRow,
    DepotRow,
    OperatorRow,
    PrimeMoverRow,
    RelatedRow,
    SiteRow,
    TaskRow,
    TravelLinkRow,
)


def _link(frm, to, secs, dist, toll, mode="road", lid="l0"):
    return TravelLinkRow.from_canonical_dict({
        "link_id": lid, "from_location_ref": frm, "to_location_ref": to,
        "travel_time_s": secs, "distance_km": dist, "toll_eur": toll,
        "network_mode": mode,
    })


class TestNetworkMeasures:
    def test_lookup_carries_distance_and_toll(self):
        lookup = build_travel_lookup([_link("a", "b", 100, 5.0, 2.5)])
        assert network_distance_km(lookup, "a", "b", "road") == 5.0
        assert network_toll_eur(lookup, "a", "b", "road") == 2.5
        assert lookup.has_tolls is True

    def test_untolled_link_reports_zero_toll_not_none(self):
        # 0.0 (declared untolled link) is distinct from None (off-network leg).
        lookup = build_travel_lookup([_link("a", "b", 100, 5.0, 0.0)])
        assert network_toll_eur(lookup, "a", "b", "road") == 0.0
        assert lookup.has_tolls is False

    def test_off_network_pair_returns_none(self):
        lookup = build_travel_lookup([_link("a", "b", 100, 5.0, 2.5)])
        assert network_distance_km(lookup, "a", "z", "road") is None
        assert network_toll_eur(lookup, "a", "z", "road") is None

    def test_toll_is_mode_aware(self):
        lookup = build_travel_lookup([_link("a", "b", 100, 5.0, 3.0, mode="road")])
        # An air vehicle does not pay the road link's toll for the pair.
        assert network_toll_eur(lookup, "a", "b", "air") is None


class TestVehicleCostMatrices:
    def _nodes(self):
        order = TaskRow.from_canonical_dict({"task_id": "o0", "location_ref": "f0"})
        field_map = {
            "f0": SiteRow.from_canonical_dict(
                {"location_id": "f0", "lat": 0.0, "lon": 0.5}
            )
        }
        return build_node_table([order], field_map, 0.0, 0.0, "d0")

    def _vehicle(self):
        return {
            "prime": PrimeMoverRow.from_canonical_dict(
                {"asset_id": "v0", "compatible_operations": ["X"]}
            ),
            "related": RelatedRow.from_canonical_dict(
                {"asset_id": "i0", "compatible_operations": ["X"]}
            ),
        }

    def test_network_distance_and_per_link_toll_used(self):
        nodes = self._nodes()  # d0 (0,0) -> f0 (0,0.5)
        lookup = build_travel_lookup([_link("d0", "f0", 600, 60.0, 4.0)])
        dist_m, toll_m = build_vehicle_cost_matrices(
            nodes, [self._vehicle()], lookup, 0.5
        )
        # Distance is the declared network distance (60 km), not the geodesic
        # straight line; toll is the per-link toll, not distance x fleet rate.
        assert dist_m[0][0][1] == 60.0
        assert toll_m[0][0][1] == 4.0

    def test_off_network_leg_uses_fleet_per_km(self):
        nodes = self._nodes()
        lookup = build_travel_lookup([])  # no links: geodesic + fleet rate
        dist_m, toll_m = build_vehicle_cost_matrices(
            nodes, [self._vehicle()], lookup, 0.5
        )
        geodesic_km = dist_m[0][0][1]
        assert geodesic_km > 0
        assert abs(toll_m[0][0][1] - geodesic_km * 0.5) < 1e-9


class TestPerAssetRates:
    def test_machine_wear_prefers_vehicle_rate(self):
        vehicle = PrimeMoverRow.from_canonical_dict(
            {"asset_id": "v0", "machine_wear_eur_per_h": 8.0}
        )
        assert vehicle_machine_wear_eur_per_h(vehicle, 5.0) == 8.0

    def test_machine_wear_falls_back_to_fleet(self):
        vehicle = PrimeMoverRow.from_canonical_dict({"asset_id": "v0"})
        assert vehicle_machine_wear_eur_per_h(vehicle, 5.0) == 5.0

    def test_operator_wage_prefers_operator_rate(self):
        operator = OperatorRow.from_canonical_dict(
            {"asset_id": "op0", "wage_eur_per_h": 25.0}
        )
        assert operator_wage_eur_per_h(operator, 18.0) == 25.0

    def test_operator_wage_falls_back_to_fleet(self):
        operator = OperatorRow.from_canonical_dict({"asset_id": "op0"})
        assert operator_wage_eur_per_h(operator, 18.0) == 18.0
        assert operator_wage_eur_per_h(None, 18.0) == 18.0

    def test_operating_rate_combines_wear_and_wage(self):
        vehicle = PrimeMoverRow.from_canonical_dict(
            {"asset_id": "v0", "machine_wear_eur_per_h": 8.0}
        )
        prices = ResourcePrices(labor_eur_per_h=20.0, machine_wear_eur_per_h=5.0)
        # Operator wage 30 + vehicle wear 8.
        assert vehicle_operating_eur_per_h(vehicle, 30.0, prices) == 38.0
        # No operator wage -> fleet labour 20 + vehicle wear 8.
        assert vehicle_operating_eur_per_h(vehicle, None, prices) == 28.0


class TestServiceFeeResolution:
    def test_service_fee_resolves_and_defaults_zero(self):
        now = datetime.now(tz=timezone.utc)
        assert ResourcePrices().service_fee_eur_per_visit == 0.0
        rates = [
            CostRateRow.from_canonical_dict({
                "rate_id": "sf", "rate_type": RATE_TYPE_SERVICE_FEE,
                "unit_price": 12.0, "per_unit": "visit",
            })
        ]
        assert resolve_unit_price(rates, RATE_TYPE_SERVICE_FEE, now, 0.0) == 12.0


class TestEndToEndPricing:
    """One depot, one field, one bundle; assert priced terms reach the dispatch."""

    @staticmethod
    def _solve(resource_prices=None, travel_lookup=None, vehicle_extra=None,
               operator=None, cluster_extra=None):
        from fl_op.solver.cluster_solver import solve_cluster

        now = datetime(2026, 6, 18, 8, 0, tzinfo=timezone.utc)
        now_epoch = int(now.timestamp())
        cluster = {
            "cluster_id": "cl",
            "depot_ref": "d0",
            "task_ids": ["o0"],
            "allocated_prime_related": {"v0": ["i0"]},
            "total_penalty_per_day": 100.0,
        }
        if cluster_extra:
            cluster.update(cluster_extra)
        order = TaskRow.from_canonical_dict({
            "task_id": "o0", "location_ref": "f0", "operation_type": "SPRAYING",
            "deadline": (now + timedelta(days=1)).isoformat(), "revenue": 5000.0,
        })
        vehicle = PrimeMoverRow.from_canonical_dict({
            "asset_id": "v0", "rated_power": 150.0, "travel_speed": 60.0,
            "home_depot_ref": "d0", "fuel_consumption_rate": 10.0,
            **(vehicle_extra or {}),
        })
        implement = RelatedRow.from_canonical_dict({
            "asset_id": "i0", "required_power": 100.0,
            "compatible_operations": ["SPRAYING"],
        })
        field = SiteRow.from_canonical_dict(
            {"location_id": "f0", "lat": 0.0, "lon": 0.3})
        depot = DepotRow.from_canonical_dict(
            {"location_id": "d0", "lat": 0.0, "lon": 0.0})
        dispatch, infeasible = solve_cluster(
            cluster, [order], [vehicle], [implement], [field], [depot], {},
            {"v0": 0}, {"i0": 0},
            travel_lookup=travel_lookup,
            resource_prices=resource_prices,
            now_epoch=now_epoch,
        )
        assert not infeasible
        return dispatch[0]

    def test_per_link_toll_is_charged_to_the_visit(self):
        lookup = build_travel_lookup([
            _link("d0", "f0", 1200, 40.0, 6.0, lid="out"),
            _link("f0", "d0", 1200, 40.0, 6.0, lid="back"),
        ])
        dispatch = self._solve(travel_lookup=lookup)
        # Inbound depot->field leg carries the declared per-link toll.
        assert dispatch["estimated_toll_cost_eur"] == 6.0

    def test_service_fee_reduces_margin_and_is_reported(self):
        base = self._solve(resource_prices=ResourcePrices(fuel_eur_per_l=1.0))
        with_fee = self._solve(
            resource_prices=ResourcePrices(
                fuel_eur_per_l=1.0, service_fee_eur_per_visit=25.0
            )
        )
        assert with_fee["estimated_service_fee_eur"] == 25.0
        assert (
            abs(
                (base["estimated_margin_eur"] - with_fee["estimated_margin_eur"])
                - 25.0
            )
            < 1e-6
        )

    def test_per_vehicle_wear_rate_drives_wear_cost(self):
        # Fleet wear is zero; only the vehicle's declared rate prices the wear.
        dispatch = self._solve(
            resource_prices=ResourcePrices(fuel_eur_per_l=1.0),
            vehicle_extra={"machine_wear_eur_per_h": 30.0},
        )
        assert dispatch["estimated_machine_wear_cost_eur"] > 0

    def test_per_operator_wage_drives_labor_cost(self):
        # Fleet labour is zero; the cluster operator's wage prices the labour.
        dispatch = self._solve(
            resource_prices=ResourcePrices(fuel_eur_per_l=1.0),
            cluster_extra={
                "operator_ref": "op0",
                "operator_wages": {"op0": 40.0},
            },
        )
        assert dispatch["operator_asset_id"] == "op0"
        assert dispatch["estimated_labor_cost_eur"] > 0
