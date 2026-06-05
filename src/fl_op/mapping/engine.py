"""Mapping engine: source records + x-optimization bindings -> canonical objects.

This is the declarative heart of the platform. The binding dotted-path on each
field determines which canonical attribute it populates; unit normalization and
missing-value policy are applied per binding. Adding or renaming a source field
is a contract edit, not a code change.
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from fl_op.canonical.asset import Asset, Capability, GeoLocation
from fl_op.canonical.commitment import InventoryPosition
from fl_op.canonical.common import QualityFinding
from fl_op.canonical.forecast import Forecast
from fl_op.canonical.location import Location
from fl_op.canonical.task import Task
from fl_op.contracts.registry import FileRegistry
from fl_op.contracts.xopt import FieldBinding
from fl_op.mapping.bindings import BindingTable, load_binding_table
from fl_op.mapping.policies import apply_missing_value_policy
from fl_op.mapping.records import coerce_value
from fl_op.mapping.units import convert_to_canonical

logger = logging.getLogger(__name__)


@dataclass
class MappingResult:
    """Canonical objects and quality findings produced from source datasets."""

    assets: list[Asset] = field(default_factory=list)
    locations: list[Location] = field(default_factory=list)
    tasks: list[Task] = field(default_factory=list)
    forecasts: list[Forecast] = field(default_factory=list)
    inventory: list[InventoryPosition] = field(default_factory=list)
    findings: list[QualityFinding] = field(default_factory=list)
    # entity ids excluded by quality policy, keyed by contract id.
    excluded: dict[str, list[str]] = field(default_factory=dict)


def _set_path(acc: dict[str, Any], tokens: list[str], value: Any) -> None:
    """Set a nested value in the accumulator dict following dotted tokens."""
    node = acc
    for tok in tokens[:-1]:
        node = node.setdefault(tok, {})
    node[tokens[-1]] = value


class MappingEngine:
    """Maps registered source datasets into canonical objects via their bindings."""

    def __init__(self, registry: Optional[FileRegistry] = None) -> None:
        self.registry = registry or FileRegistry()

    # -- per-row accumulation -----------------------------------------------------

    def _accumulate_row(
        self,
        table: BindingTable,
        row: dict[str, Any],
        result: MappingResult,
    ) -> Optional[dict[str, Any]]:
        """Resolve every binding for one row; return an accumulator or None if dropped."""
        key_field = table.entity_key_field or ""
        entity_ref = str(row.get(key_field, "<unknown>"))
        acc: dict[str, Any] = {"_capabilities": [], "_inventory": []}
        seq = 0

        for binding in table.bindings:
            raw = row.get(binding.source_field)
            seq += 1
            outcome = apply_missing_value_policy(
                raw_value=raw,
                policy=binding.meta.missing_value_policy,
                entity_ref=entity_ref,
                field_ref=binding.source_field,
                quantity_kind=binding.meta.quantity_kind,
                quality_policy_ref=binding.meta.quality_policy_ref,
                finding_seq=seq,
            )
            if outcome.finding is not None:
                result.findings.append(outcome.finding)
            if outcome.drop_entity:
                result.excluded.setdefault(table.contract_id, []).append(entity_ref)
                return None
            resolved = outcome.value
            if resolved is None:
                # Value was missing under a non-fatal policy (e.g. accept-with-warning).
                continue

            value = coerce_value(binding.meta, resolved)
            if isinstance(value, float) and binding.meta.canonical_unit:
                value = convert_to_canonical(value, binding.meta.canonical_unit)

            self._route(acc, binding, value)

        return acc

    def _route(self, acc: dict[str, Any], binding: FieldBinding, value: Any) -> None:
        """Route a coerced value into the accumulator based on its binding path."""
        tokens = binding.meta.binding.split(".")
        # Capability-like values (capabilities.* and availability.*) are stored
        # uniformly as Capability objects keyed by semantic term.
        if "capabilities" in tokens or "availability" in tokens:
            acc["_capabilities"].append(
                Capability(
                    capability_id=f"{tokens[-1]}",
                    semantic_term=binding.meta.semantic_term,
                    value=value,
                    canonical_unit=binding.meta.canonical_unit,
                )
            )
            return
        if tokens[:2] == ["location", "inventory"] or tokens[:2] == ["asset", "inventory"]:
            acc["_inventory"].append((tokens[-1], binding.meta.canonical_unit, value))
            return
        # Structural attributes: drop the leading entity token.
        _set_path(acc, tokens[1:], value)

    # -- entity construction ------------------------------------------------------

    def _build_asset(self, table: BindingTable, acc: dict[str, Any]) -> Asset:
        loc = acc.get("location")
        geoloc = GeoLocation(lat=loc["lat"], lon=loc["lon"]) if loc else None
        roles = [table.asset_role] if table.asset_role else []
        return Asset(
            asset_id=str(acc["assetId"]),
            asset_type=str(acc.get("assetType", "")),
            roles=roles,
            name=str(acc.get("name", "")),
            home_depot_ref=acc.get("homeDepotRef"),
            location=geoloc,
            capabilities=acc["_capabilities"],
            source_ref=f"{table.contract_id}:{acc['assetId']}",
        )

    def _build_location(
        self, table: BindingTable, acc: dict[str, Any], result: MappingResult
    ) -> Location:
        loc_type = "depot" if table.asset_role == "depot" else "field"
        location_id = str(acc["locationId"])
        for material, unit, qty in acc["_inventory"]:
            result.inventory.append(
                InventoryPosition(
                    inventory_location_ref=location_id,
                    material_type=material,
                    available_quantity=float(qty),
                    canonical_unit=unit or "",
                )
            )
        return Location(
            location_id=location_id,
            location_type=loc_type,
            lat=float(acc["lat"]),
            lon=float(acc["lon"]),
            name=str(acc.get("name", "")),
            area_ha=float(acc["areaHa"]) if "areaHa" in acc else None,
            soil_type=str(acc.get("soilType", "")),
            source_ref=f"{table.contract_id}:{location_id}",
        )

    def _build_task(self, table: BindingTable, acc: dict[str, Any]) -> Task:
        return Task(
            task_id=str(acc["taskId"]),
            order_id=str(acc.get("orderId", "")),
            operation_type=str(acc["operationType"]),
            location_ref=str(acc["locationRef"]),
            area_ha=float(acc["areaHa"]) if "areaHa" in acc else None,
            deadline=acc.get("deadline"),
            priority_class=int(acc.get("priorityClass", 5)),
            revenue_value_eur=float(acc.get("revenueValue", 0.0)),
            penalty_per_day_eur=float(acc.get("penaltyPerDay", 0.0)),
            status=str(acc.get("status", "pending")),
            source_ref=f"{table.contract_id}:{acc['taskId']}",
        )

    def _build_forecast(self, table: BindingTable, acc: dict[str, Any]) -> Forecast:
        from fl_op.canonical.common import TimeInterval

        ff = acc.get("forecastFor", {})
        interval = None
        if ff.get("from"):
            interval = TimeInterval(**{"from": ff["from"], "to": ff.get("to")})
        return Forecast(
            forecast_id=str(acc["forecastId"]),
            location=acc.get("location"),
            forecast_for=interval,
            value=acc.get("value", {}),
            source_ref=f"{table.contract_id}:{acc['forecastId']}",
        )

    # -- public API ---------------------------------------------------------------

    def map_dataset(
        self,
        contract_id: str,
        rows: list[dict[str, Any]],
        result: Optional[MappingResult] = None,
    ) -> MappingResult:
        """Map one source dataset's rows into the appropriate canonical objects."""
        result = result or MappingResult()
        table = load_binding_table(self.registry, contract_id)
        entity = table.canonical_entity

        for row in rows:
            acc = self._accumulate_row(table, row, result)
            if acc is None:
                continue
            if entity == "asset":
                result.assets.append(self._build_asset(table, acc))
            elif entity == "location":
                result.locations.append(self._build_location(table, acc, result))
            elif entity == "task":
                result.tasks.append(self._build_task(table, acc))
            elif entity == "forecast":
                result.forecasts.append(self._build_forecast(table, acc))
            else:
                logger.warning("Unhandled canonical entity '%s' for %s", entity, contract_id)

        logger.info(
            "Mapped %s: %d rows -> %s (excluded %d)",
            contract_id,
            len(rows),
            entity,
            len(result.excluded.get(contract_id, [])),
        )
        return result
