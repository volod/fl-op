"""Cost rates as data entities: resolution, validity windows, mapping."""

from datetime import datetime, timedelta, timezone

from fl_op.core.constants import (
    ELECTRICITY_COST_EUR_PER_KWH,
    RATE_TYPE_ELECTRICITY,
    RATE_TYPE_FUEL,
    RATE_TYPE_LABOR,
    RATE_TYPE_MACHINE_WEAR,
    RATE_TYPE_MATERIAL,
    RATE_TYPE_TOLL,
)
from fl_op.solver.cost_rates import resolve_unit_price
from fl_op.solver.types import CostRateRow

_DEFAULT = 1.45


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _rate(rate_id: str, rate_type: str, price: float,
          valid_from: str = "", valid_to: str = "") -> CostRateRow:
    return CostRateRow.from_canonical_dict({
        "rate_id": rate_id, "rate_type": rate_type, "unit_price": price,
        "per_unit": "L", "valid_from": valid_from or None, "valid_to": valid_to or None,
    })


class TestResolveUnitPrice:
    def test_no_rates_falls_back_to_default(self):
        assert resolve_unit_price([], RATE_TYPE_FUEL, _now(), _DEFAULT) == _DEFAULT

    def test_other_rate_types_ignored(self):
        rates = [_rate("r0", RATE_TYPE_MATERIAL, 0.58)]
        assert resolve_unit_price(rates, RATE_TYPE_FUEL, _now(), _DEFAULT) == _DEFAULT

    def test_open_ended_rate_applies(self):
        rates = [_rate("r0", RATE_TYPE_FUEL, 1.62)]
        assert resolve_unit_price(rates, RATE_TYPE_FUEL, _now(), _DEFAULT) == 1.62

    def test_expired_rate_skipped_in_favor_of_current(self):
        now = _now()
        rates = [
            _rate("r_old", RATE_TYPE_FUEL, 0.99,
                  valid_from=(now - timedelta(days=30)).isoformat(),
                  valid_to=(now - timedelta(days=1)).isoformat()),
            _rate("r_new", RATE_TYPE_FUEL, 1.71,
                  valid_from=(now - timedelta(days=1)).isoformat(),
                  valid_to=(now + timedelta(days=365)).isoformat()),
        ]
        assert resolve_unit_price(rates, RATE_TYPE_FUEL, now, _DEFAULT) == 1.71

    def test_future_rate_not_yet_applicable(self):
        now = _now()
        rates = [
            _rate("r_future", RATE_TYPE_FUEL, 2.50,
                  valid_from=(now + timedelta(days=10)).isoformat()),
        ]
        assert resolve_unit_price(rates, RATE_TYPE_FUEL, now, _DEFAULT) == _DEFAULT

    def test_latest_valid_from_wins_among_applicable(self):
        now = _now()
        rates = [
            _rate("r_a", RATE_TYPE_FUEL, 1.50,
                  valid_from=(now - timedelta(days=20)).isoformat()),
            _rate("r_b", RATE_TYPE_FUEL, 1.60,
                  valid_from=(now - timedelta(days=2)).isoformat()),
        ]
        assert resolve_unit_price(rates, RATE_TYPE_FUEL, now, _DEFAULT) == 1.60

    def test_electricity_rate_resolves_by_resource_type(self):
        rates = [_rate("r_e", RATE_TYPE_ELECTRICITY, 0.21)]
        assert (
            resolve_unit_price(
                rates, RATE_TYPE_ELECTRICITY, _now(), ELECTRICITY_COST_EUR_PER_KWH
            )
            == 0.21
        )

    def test_operating_rate_types_resolve_by_code(self):
        now = _now()
        rates = [
            _rate("r_labor", RATE_TYPE_LABOR, 22.0),
            _rate("r_wear", RATE_TYPE_MACHINE_WEAR, 6.0),
            _rate("r_toll", RATE_TYPE_TOLL, 0.05),
        ]
        assert resolve_unit_price(rates, RATE_TYPE_LABOR, now, 0.0) == 22.0
        assert resolve_unit_price(rates, RATE_TYPE_MACHINE_WEAR, now, 0.0) == 6.0
        assert resolve_unit_price(rates, RATE_TYPE_TOLL, now, 0.0) == 0.05


class TestResourcePrices:
    def test_operating_rate_sums_labor_and_wear(self):
        from fl_op.solver.cost_rates import ResourcePrices

        prices = ResourcePrices(labor_eur_per_h=20.0, machine_wear_eur_per_h=5.0)
        assert prices.operating_eur_per_h == 25.0

    def test_operating_rates_default_to_zero(self):
        from fl_op.solver.cost_rates import ResourcePrices

        prices = ResourcePrices()
        assert prices.operating_eur_per_h == 0.0
        assert prices.toll_eur_per_km == 0.0


class TestPricesMapping:
    def test_prices_rows_map_to_cost_rates(self):
        from fl_op.mapping.engine import MappingEngine

        now = _now()
        result = MappingEngine().map_dataset(
            "prices",
            [
                {
                    "price_id": "price_fuel_current",
                    "resource_type": RATE_TYPE_FUEL,
                    "price_eur": 1.52,
                    "per_unit": "L",
                    "valid_from": now.isoformat(),
                    "valid_to": (now + timedelta(days=365)).isoformat(),
                }
            ],
        )
        assert len(result.cost_rates) == 1
        rate = result.cost_rates[0]
        assert rate.rate_type == RATE_TYPE_FUEL
        assert rate.unit_price_eur == 1.52
        assert rate.valid_from is not None

    def test_empty_validity_is_open_ended(self):
        from fl_op.mapping.engine import MappingEngine

        result = MappingEngine().map_dataset(
            "prices",
            [
                {
                    "price_id": "p0",
                    "resource_type": RATE_TYPE_MATERIAL,
                    "price_eur": 0.58,
                    "per_unit": "kg",
                    "valid_from": "",
                    "valid_to": "",
                }
            ],
        )
        assert result.cost_rates[0].valid_from is None
        assert result.cost_rates[0].valid_to is None

    def test_operating_rate_types_map_through(self):
        from fl_op.mapping.engine import MappingEngine

        result = MappingEngine().map_dataset(
            "prices",
            [
                {
                    "price_id": "p_labor",
                    "resource_type": RATE_TYPE_LABOR,
                    "price_eur": 24.0,
                    "per_unit": "h",
                    "valid_from": "",
                    "valid_to": "",
                },
                {
                    "price_id": "p_toll",
                    "resource_type": RATE_TYPE_TOLL,
                    "price_eur": 0.04,
                    "per_unit": "km",
                    "valid_from": "",
                    "valid_to": "",
                },
            ],
        )
        by_type = {r.rate_type: r for r in result.cost_rates}
        assert by_type[RATE_TYPE_LABOR].unit_price_eur == 24.0
        assert by_type[RATE_TYPE_TOLL].per_unit == "km"


class TestGreedyPriceConsumption:
    def test_fuel_price_scales_repositioning_cost(self):
        from fl_op.solver.greedy import _estimate_repositioning_cost
        from fl_op.solver.types import PrimeMoverRow, SiteRow

        vehicle = PrimeMoverRow.from_canonical_dict({
            "asset_id": "v0", "lat": "48.5", "lon": "32.0",
            "travel_speed": "20", "fuel_consumption_rate": "10",
        })
        field = SiteRow.from_canonical_dict(
            {"location_id": "f0", "lat": "48.9", "lon": "32.4"})
        cheap = _estimate_repositioning_cost(vehicle, field, fuel_price_eur_per_l=1.0)
        expensive = _estimate_repositioning_cost(vehicle, field, fuel_price_eur_per_l=2.0)
        assert expensive == 2 * cheap > 0

    def test_electricity_price_scales_repositioning_cost(self):
        from fl_op.solver.cost_rates import ResourcePrices
        from fl_op.solver.greedy import _estimate_repositioning_cost
        from fl_op.solver.types import PrimeMoverRow, SiteRow

        vehicle = PrimeMoverRow.from_canonical_dict({
            "asset_id": "ev0",
            "lat": "48.5",
            "lon": "32.0",
            "travel_speed": "20",
            "energy_resource_type": RATE_TYPE_ELECTRICITY,
            "energy_unit": "kWh",
            "energy_consumption_rate": "20",
        })
        field = SiteRow.from_canonical_dict(
            {"location_id": "f0", "lat": "48.9", "lon": "32.4"})
        cheap = _estimate_repositioning_cost(
            vehicle,
            field,
            resource_prices=ResourcePrices(electricity_eur_per_kwh=0.1),
        )
        expensive = _estimate_repositioning_cost(
            vehicle,
            field,
            resource_prices=ResourcePrices(electricity_eur_per_kwh=0.2),
        )
        assert expensive == 2 * cheap > 0

    def test_labor_and_wear_add_to_repositioning_cost(self):
        from fl_op.solver.cost_rates import ResourcePrices
        from fl_op.solver.greedy import _estimate_repositioning_cost
        from fl_op.solver.types import PrimeMoverRow, SiteRow

        vehicle = PrimeMoverRow.from_canonical_dict({
            "asset_id": "v0", "lat": "48.5", "lon": "32.0",
            "travel_speed": "20", "fuel_consumption_rate": "10",
        })
        field = SiteRow.from_canonical_dict(
            {"location_id": "f0", "lat": "48.9", "lon": "32.4"})
        energy_only = _estimate_repositioning_cost(
            vehicle, field, resource_prices=ResourcePrices(fuel_eur_per_l=1.0)
        )
        with_operating = _estimate_repositioning_cost(
            vehicle,
            field,
            resource_prices=ResourcePrices(
                fuel_eur_per_l=1.0, labor_eur_per_h=30.0, machine_wear_eur_per_h=10.0
            ),
        )
        assert with_operating > energy_only > 0

    def test_toll_adds_distance_cost_to_repositioning(self):
        from fl_op.solver.cost_rates import ResourcePrices
        from fl_op.solver.greedy import _estimate_repositioning_cost
        from fl_op.solver.types import PrimeMoverRow, SiteRow

        vehicle = PrimeMoverRow.from_canonical_dict({
            "asset_id": "v0", "lat": "48.5", "lon": "32.0",
            "travel_speed": "20", "fuel_consumption_rate": "10",
        })
        field = SiteRow.from_canonical_dict(
            {"location_id": "f0", "lat": "48.9", "lon": "32.4"})
        no_toll = _estimate_repositioning_cost(
            vehicle, field, resource_prices=ResourcePrices(fuel_eur_per_l=1.0)
        )
        with_toll = _estimate_repositioning_cost(
            vehicle,
            field,
            resource_prices=ResourcePrices(fuel_eur_per_l=1.0, toll_eur_per_km=0.5),
        )
        assert with_toll > no_toll > 0
