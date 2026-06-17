"""Travel-network entity: lookup construction, routing consumption, mapping."""

import pathlib

from fl_op.core.constants import AIR_TRAVEL_CIRCUITY, GROUND_TRAVEL_CIRCUITY
from fl_op.solver.routing_model import build_node_table, build_time_matrix
from fl_op.solver.travel_time import (
    _haversine_s,
    build_travel_lookup,
    mode_circuity,
    travel_seconds,
)
from fl_op.solver.types import SiteRow, TaskRow, TravelLinkRow


def test_mode_circuity_air_is_direct_ground_detours():
    assert mode_circuity("air") == AIR_TRAVEL_CIRCUITY
    for ground in ("road", "any", None, "", "unknown"):
        assert mode_circuity(ground) == GROUND_TRAVEL_CIRCUITY


def _link(from_ref: str, to_ref: str, seconds: float, link_id: str = "l0") -> TravelLinkRow:
    return TravelLinkRow.from_canonical_dict({
        "link_id": link_id, "from_location_ref": from_ref,
        "to_location_ref": to_ref, "travel_time_s": seconds,
    })


def _mode_link(
    from_ref: str,
    to_ref: str,
    seconds: float,
    mode: str,
    link_id: str = "l0",
) -> TravelLinkRow:
    return TravelLinkRow.from_canonical_dict({
        "link_id": link_id,
        "from_location_ref": from_ref,
        "to_location_ref": to_ref,
        "travel_time_s": seconds,
        "network_mode": mode,
    })


class TestTravelLookup:
    def test_lookup_indexes_directed_pairs(self):
        lookup = build_travel_lookup([_link("d0", "f0", 1234.0)])
        assert lookup == {("d0", "f0"): 1234}

    def test_nonpositive_times_are_dropped(self):
        lookup = build_travel_lookup([_link("d0", "f0", 0.0), _link("d0", "f1", -5.0)])
        assert lookup == {}

    def test_link_wins_over_haversine(self):
        lookup = {("d0", "f0"): 1234}
        assert travel_seconds("d0", "f0", 48.5, 32.0, 48.9, 32.4, lookup) == 1234

    def test_reverse_direction_falls_back_to_forward_link(self):
        lookup = {("d0", "f0"): 1234}
        assert travel_seconds("f0", "d0", 48.9, 32.4, 48.5, 32.0, lookup) == 1234

    def test_unknown_pair_falls_back_to_haversine(self):
        lookup = {("d0", "f0"): 1234}
        # An unknown pair takes the geometric fallback with ground circuity (the
        # default no-mode leg follows roads/tracks, not a straight line).
        expected = _haversine_s(48.5, 32.0, 48.9, 32.4, circuity=GROUND_TRAVEL_CIRCUITY)
        assert travel_seconds("d0", "f1", 48.5, 32.0, 48.9, 32.4, lookup) == expected

    def test_no_lookup_falls_back_to_ground_circuity_haversine(self):
        expected = _haversine_s(48.5, 32.0, 48.9, 32.4, circuity=GROUND_TRAVEL_CIRCUITY)
        assert travel_seconds("d0", "f0", 48.5, 32.0, 48.9, 32.4, None) == expected

    def test_air_mode_fallback_flies_direct(self):
        # An air leg with no network link is straight-line (circuity 1.0), so a
        # drone's fallback is shorter than a ground mover's over the same span.
        ground = travel_seconds("d0", "f0", 48.5, 32.0, 48.9, 32.4, None, "any")
        air = travel_seconds("d0", "f0", 48.5, 32.0, 48.9, 32.4, None, "air")
        assert air == _haversine_s(48.5, 32.0, 48.9, 32.4)
        assert air < ground

    def test_mode_specific_links_do_not_leak_between_road_and_air(self):
        lookup = build_travel_lookup(
            [
                _mode_link("hub", "drop", 900.0, "road", "road_1"),
                _mode_link("hub", "drop", 120.0, "air", "air_1"),
            ]
        )
        assert travel_seconds("hub", "drop", 0, 0, 1, 1, lookup, "road") == 900
        assert travel_seconds("hub", "drop", 0, 0, 1, 1, lookup, "air") == 120
        assert lookup[("hub", "drop")] == 120

    def test_any_links_are_available_to_specific_modes(self):
        lookup = build_travel_lookup([_mode_link("hub", "drop", 300.0, "any")])
        assert travel_seconds("hub", "drop", 0, 0, 1, 1, lookup, "road") == 300
        assert travel_seconds("hub", "drop", 0, 0, 1, 1, lookup, "air") == 300


class TestShortestPathComposition:
    def test_two_hop_path_is_composed(self):
        lookup = build_travel_lookup(
            [_link("a", "b", 600.0, "l0"), _link("b", "c", 900.0, "l1")]
        )
        assert lookup[("a", "c")] == 1500

    def test_composed_route_beats_longer_direct_link(self):
        lookup = build_travel_lookup(
            [
                _link("a", "c", 5000.0, "l0"),
                _link("a", "b", 600.0, "l1"),
                _link("b", "c", 900.0, "l2"),
            ]
        )
        assert lookup[("a", "c")] == 1500

    def test_composition_is_directed(self):
        lookup = build_travel_lookup(
            [_link("a", "b", 600.0, "l0"), _link("b", "c", 900.0, "l1")]
        )
        assert ("c", "a") not in lookup

    def test_oversized_network_keeps_direct_links_only(self, monkeypatch):
        from fl_op.solver import travel_time

        monkeypatch.setattr(travel_time, "TRAVEL_NETWORK_MAX_COMPOSE_NODES", 2)
        lookup = build_travel_lookup(
            [_link("a", "b", 600.0, "l0"), _link("b", "c", 900.0, "l1")]
        )
        assert ("a", "c") not in lookup
        assert lookup[("a", "b")] == 600


class TestNetworkTimesInClustering:
    @staticmethod
    def _depot(did: str, lat: float, lon: float):
        from fl_op.solver.types import DepotRow

        return DepotRow.from_canonical_dict(
            {"location_id": did, "lat": lat, "lon": lon}
        )

    def test_network_time_overrides_geographic_nearest_depot(self):
        """The road network reaches f0 cheaply from the farther depot."""
        from fl_op.solver.preprocessing import cluster_orders_by_depot

        order = TaskRow.from_canonical_dict({"task_id": "o0", "location_ref": "f0"})
        field = SiteRow.from_canonical_dict(
            {"location_id": "f0", "lat": 48.5, "lon": 32.0}
        )
        near = self._depot("d_near", 48.55, 32.0)
        far = self._depot("d_far", 49.5, 32.0)
        # Haversine: d_near ~5.6 km (~1334 s), d_far ~111 km. The link makes
        # d_far the fastest road origin.
        lookup = build_travel_lookup([_link("d_far", "f0", 300.0)])
        assignment = cluster_orders_by_depot([order], [field], [near, far], lookup)
        assert assignment["d_far"] == ["o0"]

    def test_without_lookup_geographic_nearest_wins(self):
        from fl_op.solver.preprocessing import cluster_orders_by_depot

        order = TaskRow.from_canonical_dict({"task_id": "o0", "location_ref": "f0"})
        field = SiteRow.from_canonical_dict(
            {"location_id": "f0", "lat": 48.5, "lon": 32.0}
        )
        near = self._depot("d_near", 48.55, 32.0)
        far = self._depot("d_far", 49.5, 32.0)
        assignment = cluster_orders_by_depot([order], [field], [near, far], None)
        assert assignment["d_near"] == ["o0"]


class TestNetworkTimesInGreedy:
    def test_network_reposition_flips_pair_ordering(self):
        """A road link from the far vehicle's depot makes it the cheaper pick."""
        from fl_op.solver.greedy import vectorized_score
        from fl_op.solver.types import PrimeMoverRow, RelatedRow

        def vehicle(vid: str, lat: float, depot: str) -> PrimeMoverRow:
            return PrimeMoverRow.from_canonical_dict({
                "asset_id": vid, "rated_power": "150", "lat": lat, "lon": 32.0,
                "home_depot_ref": depot, "travel_speed": "15",
                "fuel_consumption_rate": "18",
            })

        v_near = vehicle("v_near", 48.55, "d_near")
        v_far = vehicle("v_far", 49.5, "d_far")
        implement = RelatedRow.from_canonical_dict({
            "asset_id": "i0", "compatible_operations": "['SPRAYING']",
            "required_power": "100",
        })
        field = SiteRow.from_canonical_dict(
            {"location_id": "f0", "lat": 48.5, "lon": 32.0}
        )
        order = TaskRow.from_canonical_dict({
            "task_id": "o0", "location_ref": "f0", "operation_type": "SPRAYING",
            "area": "10", "revenue": "2000",
        })
        feasible = {"o0": [(0, 0), (1, 0)]}
        v_index = {"v_near": 0, "v_far": 1}
        i_index = {"i0": 0}

        baseline = vectorized_score(
            [order], [v_near, v_far], [implement], [field],
            feasible, v_index, i_index,
        )
        assert baseline["o0"][0][1] == 0  # nearest vehicle wins on haversine

        lookup = build_travel_lookup([_link("d_far", "f0", 60.0)])
        networked = vectorized_score(
            [order], [v_near, v_far], [implement], [field],
            feasible, v_index, i_index, travel_lookup=lookup,
        )
        assert networked["o0"][0][1] == 1  # road access flips the ordering

    def test_nearest_network_node_beats_home_depot_access(self):
        """A vehicle far from its depot joins the network at a nearby node.

        Both vehicles' home depots are off-network. Without node mapping they
        fall back to the straight-line estimate, so the geographically closer
        vehicle wins. With ``location_coords`` the farther vehicle maps onto a
        local node that has a cheap link to the field, flipping the choice.
        """
        from fl_op.solver.greedy import vectorized_score
        from fl_op.solver.types import PrimeMoverRow, RelatedRow

        def vehicle(vid: str, lon: float) -> PrimeMoverRow:
            return PrimeMoverRow.from_canonical_dict({
                "asset_id": vid, "rated_power": "150", "lat": 48.5, "lon": lon,
                "home_depot_ref": "d_off_network", "travel_speed": "15",
                "fuel_consumption_rate": "18",
            })

        # v_a sits just east of the on-network node; v_b is geographically
        # closer to the field but has no useful node link.
        v_a = vehicle("v_a", 32.06)
        v_b = vehicle("v_b", 32.03)
        implement = RelatedRow.from_canonical_dict({
            "asset_id": "i0", "compatible_operations": "['SPRAYING']",
            "required_power": "100",
        })
        field = SiteRow.from_canonical_dict(
            {"location_id": "f0", "lat": 48.5, "lon": 32.0}
        )
        order = TaskRow.from_canonical_dict({
            "task_id": "o0", "location_ref": "f0", "operation_type": "SPRAYING",
            "area": "10", "revenue": "2000",
        })
        feasible = {"o0": [(0, 0), (1, 0)]}
        v_index = {"v_a": 0, "v_b": 1}
        i_index = {"i0": 0}
        lookup = build_travel_lookup([_link("n_local", "f0", 30.0)])
        coords = {"f0": (48.5, 32.0), "n_local": (48.5, 32.05)}

        without_coords = vectorized_score(
            [order], [v_a, v_b], [implement], [field],
            feasible, v_index, i_index, travel_lookup=lookup,
        )
        assert without_coords["o0"][0][1] == 1  # closer v_b wins on straight line

        with_coords = vectorized_score(
            [order], [v_a, v_b], [implement], [field],
            feasible, v_index, i_index, travel_lookup=lookup,
            location_coords=coords,
        )
        assert with_coords["o0"][0][1] == 0  # v_a's local node access wins


class TestNodeGeometry:
    def _order(self, oid: str, fid: str) -> TaskRow:
        return TaskRow.from_canonical_dict({"task_id": oid, "location_ref": fid})

    def _field(self, fid: str, lat: float, lon: float) -> SiteRow:
        return SiteRow.from_canonical_dict({"location_id": fid, "lat": lat, "lon": lon})

    def test_matrix_uses_network_times_where_links_exist(self):
        orders = [self._order("o0", "f0")]
        field_map = {"f0": self._field("f0", 48.9, 32.4)}
        lookup = {("d0", "f0"): 1234, ("f0", "d0"): 4321}
        nodes = build_node_table(orders, field_map, 48.5, 32.0, "d0")
        matrix = build_time_matrix(nodes, lookup)
        assert matrix[0][1] == 1234
        assert matrix[1][0] == 4321

    def test_matrix_falls_back_to_haversine_without_links(self):
        orders = [self._order("o0", "f0")]
        field_map = {"f0": self._field("f0", 48.9, 32.4)}
        nodes = build_node_table(orders, field_map, 48.5, 32.0, "d0")
        matrix = build_time_matrix(nodes, {})
        # No mode given -> ground circuity on the geometric fallback leg.
        assert matrix[0][1] == _haversine_s(
            48.5, 32.0, 48.9, 32.4, circuity=GROUND_TRAVEL_CIRCUITY
        )

    def test_pickup_resolves_against_location_outside_site_table(self):
        """A pickup at a hub/depot resolves to that hub's coordinates, not the
        cluster depot, when supplied via ``pickup_map``."""
        order = TaskRow.from_canonical_dict({
            "task_id": "o0", "location_ref": "f0", "pickup_location_ref": "hub1",
        })
        field_map = {"f0": self._field("f0", 48.9, 32.4)}
        pickup_map = {**field_map, "hub1": self._field("hub1", 50.0, 33.0)}
        nodes = build_node_table(
            [order], field_map, 48.5, 32.0, "d0", pickup_map=pickup_map
        )
        pickup_node = next(n for n in nodes if n.kind == "pickup")
        assert (pickup_node.lat, pickup_node.lon) == (50.0, 33.0)

    def test_unresolved_pickup_falls_back_to_depot(self):
        """A supplier ref absent from every table falls back to depot coords."""
        order = TaskRow.from_canonical_dict({
            "task_id": "o0", "location_ref": "f0",
            "pickup_location_ref": "ghost_supplier",
        })
        field_map = {"f0": self._field("f0", 48.9, 32.4)}
        nodes = build_node_table(
            [order], field_map, 48.5, 32.0, "d0", pickup_map=field_map
        )
        pickup_node = next(n for n in nodes if n.kind == "pickup")
        assert (pickup_node.lat, pickup_node.lon) == (48.5, 32.0)


class TestRoutesMapping:
    def test_routes_rows_map_to_travel_links(self):
        from fl_op.mapping.engine import MappingEngine

        result = MappingEngine().map_dataset(
            "routes",
            [
                {
                    "route_id": "route_0000001",
                    "from_id": "depot_0001",
                    "to_id": "field_000001",
                    "travel_time_s": 1800.0,
                    "distance_km": 10.0,
                    "road_class": "paved",
                }
            ],
        )
        assert len(result.travel_links) == 1
        link = result.travel_links[0]
        assert link.from_location_ref == "depot_0001"
        assert link.to_location_ref == "field_000001"
        assert link.travel_time_s == 1800.0
        assert link.distance_km == 10.0


class TestSnapshotIntegration:
    def test_snapshot_carries_travel_links_and_solver_rows(self, dataset_dir: pathlib.Path):
        from fl_op.snapshot.builder import SnapshotBuilder
        from fl_op.solver.inputs import SECTION_TRAVEL_LINKS, build_solver_inputs

        snapshot = SnapshotBuilder().build(dataset_dir)
        assert snapshot.travel_links, "generated dataset should map travel links"
        rows = build_solver_inputs(snapshot)
        assert len(rows[SECTION_TRAVEL_LINKS]) == len(snapshot.travel_links)
        lookup = build_travel_lookup(rows[SECTION_TRAVEL_LINKS])
        assert lookup, "projected travel links should index by location pair"
