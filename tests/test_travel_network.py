"""Travel-network entity: lookup construction, routing consumption, mapping."""

import pathlib

from fl_op.solver.routing_model import _build_node_geometry
from fl_op.solver.travel_time import _haversine_s, build_travel_lookup, travel_seconds
from fl_op.solver.types import SiteRow, TaskRow, TravelLinkRow


def _link(from_ref: str, to_ref: str, seconds: float, link_id: str = "l0") -> TravelLinkRow:
    return TravelLinkRow.from_canonical_dict({
        "link_id": link_id, "from_location_ref": from_ref,
        "to_location_ref": to_ref, "travel_time_s": seconds,
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
        expected = _haversine_s(48.5, 32.0, 48.9, 32.4)
        assert travel_seconds("d0", "f1", 48.5, 32.0, 48.9, 32.4, lookup) == expected

    def test_no_lookup_is_pure_haversine(self):
        expected = _haversine_s(48.5, 32.0, 48.9, 32.4)
        assert travel_seconds("d0", "f0", 48.5, 32.0, 48.9, 32.4, None) == expected


class TestNodeGeometry:
    def _order(self, oid: str, fid: str) -> TaskRow:
        return TaskRow.from_canonical_dict({"task_id": oid, "location_ref": fid})

    def _field(self, fid: str, lat: float, lon: float) -> SiteRow:
        return SiteRow.from_canonical_dict({"location_id": fid, "lat": lat, "lon": lon})

    def test_matrix_uses_network_times_where_links_exist(self):
        orders = [self._order("o0", "f0")]
        field_map = {"f0": self._field("f0", 48.9, 32.4)}
        lookup = {("d0", "f0"): 1234, ("f0", "d0"): 4321}
        _, _, matrix = _build_node_geometry(
            orders, field_map, 48.5, 32.0, "d0", lookup
        )
        assert matrix[0][1] == 1234
        assert matrix[1][0] == 4321

    def test_matrix_falls_back_to_haversine_without_links(self):
        orders = [self._order("o0", "f0")]
        field_map = {"f0": self._field("f0", 48.9, 32.4)}
        _, _, matrix = _build_node_geometry(orders, field_map, 48.5, 32.0, "d0", {})
        assert matrix[0][1] == _haversine_s(48.5, 32.0, 48.9, 32.4)


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
