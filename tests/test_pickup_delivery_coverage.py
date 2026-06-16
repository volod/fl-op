"""Pickup-and-delivery coverage across the agricultural, construction, and
roadside domains.

The drone-logistics pack already exercised paired pickup-and-delivery. These
tests cover the extension to the other three domains: each task contract now
carries a `pickup_location_ref` mapped to `task.pickupLocationRef`, the
agricultural and construction generators emit a share of pickup-and-delivery
tasks, and the roadside maintenance-jobs projection supports it for
externally-created tasks (the default roadside scenario stays monitoring-driven).
"""

from datetime import datetime, timezone

import numpy as np

from fl_op.data.construction_entities import (
    _generate_sites,
    _generate_jobs,
    _generate_yards,
)
from fl_op.data.field_order_entities import (
    _LOAD_DEMANDING_OPERATIONS,
    _generate_fields,
    _generate_orders_and_contracts,
)
from fl_op.mapping.engine import MappingEngine

_NOW = datetime(2026, 6, 16, tzinfo=timezone.utc)


class TestAgriculturalPickupGeneration:
    def test_orders_carry_pickup_ref_and_share_pick_up_material(self):
        depots = [
            {"depot_id": f"yard_{i:04d}", "lat": 48.5 + 0.1 * i, "lon": 32.0 + 0.1 * i}
            for i in range(3)
        ]
        fields = _generate_fields(np.random.default_rng(0), 60, depots, _NOW)
        orders, _ = _generate_orders_and_contracts(
            np.random.default_rng(0), 60, fields, _NOW
        )
        field_yard = {f["field_id"]: f["nearest_depot_id"] for f in fields}

        # Every order carries the column (empty or a yard ref).
        assert all("pickup_location_ref" in o for o in orders)
        with_pickup = [o for o in orders if o["pickup_location_ref"]]
        assert with_pickup, "expected a share of orders with a pickup location"
        for order in with_pickup:
            # The pickup is the field's nearest yard, and the order carries the
            # material it goes there to collect.
            assert order["pickup_location_ref"] == field_yard[order["field_id"]]
            assert order["operation_type"] in _LOAD_DEMANDING_OPERATIONS
            assert order["material_load_kg"] > 0


class TestConstructionPickupGeneration:
    def test_jobs_carry_pickup_ref_to_a_real_yard(self):
        yards = _generate_yards(np.random.default_rng(1), 3)
        sites = _generate_sites(np.random.default_rng(1), 30)
        jobs = _generate_jobs(np.random.default_rng(1), 30, sites, yards, _NOW)
        yard_ids = {y["yard_id"] for y in yards}

        assert all("pickup_location_ref" in j for j in jobs)
        with_pickup = [j for j in jobs if j["pickup_location_ref"]]
        assert with_pickup, "expected a share of jobs with a pickup location"
        assert all(j["pickup_location_ref"] in yard_ids for j in with_pickup)


class TestPickupMapping:
    """Every domain's task projection carries the pickup through to canonical."""

    def _agri_order(self):
        return {
            "order_id": "o1", "contract_id": "c1", "field_id": "field_1",
            "operation_type": "SPRAYING", "area_ha": 10.0,
            "deadline": "2026-07-01T00:00:00+00:00", "penalty_per_day_eur": 100.0,
            "priority": 5, "status": "pending", "estimated_revenue_eur": 2000.0,
            "depends_on_order_id": "", "workable_windows": "[]",
            "service_duration_minutes": 0.0, "material_load_kg": 120.0,
            "pickup_location_ref": "yard_0001",
        }

    def _construction_job(self):
        return {
            "job_id": "j1", "contract_id": "p1", "site_id": "site_1",
            "work_type": "EXCAVATION", "plot_ha": 2.0,
            "deadline": "2026-07-01T00:00:00+00:00", "penalty_per_day_eur": 100.0,
            "priority": 5, "status": "pending", "revenue_eur": 3000.0,
            "quantity_value": 500.0, "quantity_unit": "m3",
            "pickup_location_ref": "yard_0002",
        }

    def _roadside_job(self):
        return {
            "job_id": "mj1", "work_order_id": "wo1", "road_segment_id": "seg_1",
            "operation_type": "EQUIPMENT_SERVICE", "service_area_ha": 0.1,
            "service_duration_minutes": 30.0, "deadline": "2026-07-01T00:00:00+00:00",
            "penalty_per_day_eur": 50.0, "priority": 5, "status": "pending",
            "estimated_revenue_eur": 100.0, "pickup_location_ref": "road_depot_0001",
        }

    def _map_pickup(self, contract: str, row: dict) -> str:
        res = MappingEngine().map_dataset(contract, [row])
        assert not res.excluded.get(contract), res.findings
        return res.tasks[0].pickup_location_ref

    def test_agricultural_pickup_projects_to_task(self):
        assert self._map_pickup("orders", self._agri_order()) == "yard_0001"

    def test_construction_pickup_projects_to_task(self):
        assert self._map_pickup("jobs", self._construction_job()) == "yard_0002"

    def test_roadside_pickup_projects_to_task(self):
        assert self._map_pickup("maintenance-jobs", self._roadside_job()) == (
            "road_depot_0001"
        )
