"""Synthetic fleet dataset generator entry point.

Orchestrates entity generators and writes CSVs + JSONs to
$DATA_DIR/generate-data/<ISO-timestamp>/.

Sampling: NumPy-vectorised. Geographic placement: scipy BallTree haversine.
"""

import logging
import pathlib
from datetime import datetime, timezone
from typing import Any

import numpy as np

from fl_op.core.constants import ARTIFACT_SCHEMA_VERSION
from fl_op.core.paths import DATA_ROOT
from fl_op.data.field_order_entities import _generate_fields, _generate_orders_and_contracts, _generate_weather
from fl_op.data.fleet_entities import _generate_depots, _generate_implements, _generate_operators, _generate_vehicles
from fl_op.data.io import _load_csv_or_empty, _merge_real_into_synthetic, _write_csv, _write_json

logger = logging.getLogger(__name__)


def run_generate(
    n_vehicles: int,
    n_implements: int,
    n_orders: int,
    n_depots: int,
    seed: int | None,
    data_path: str | None,
) -> None:
    """Generate synthetic (or augmented real) fleet dataset.

    Output directory: $DATA_DIR/generate-data/<ISO-timestamp>/
    """
    rng = np.random.default_rng(seed)
    now = datetime.now(tz=timezone.utc)
    ts = now.strftime("%Y%m%dT%H%M%S")

    out_dir = DATA_ROOT / "generate-data" / ts
    out_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Writing dataset to %s", out_dir)

    depots = _generate_depots(rng, n_depots)
    vehicles = _generate_vehicles(rng, n_vehicles, depots)
    implements = _generate_implements(rng, n_implements, depots)
    operators = _generate_operators(rng, n_vehicles, depots)
    fields = _generate_fields(rng, n_orders, depots)
    orders, contracts = _generate_orders_and_contracts(rng, n_orders, fields, now)
    weather = _generate_weather(rng, depots, now)

    if data_path is not None:
        real_dir = pathlib.Path(data_path)
        logger.info("Merging real fleet data from %s", real_dir)
        real_depots = _load_csv_or_empty(real_dir / "depots.csv")
        real_vehicles = _load_csv_or_empty(real_dir / "vehicles.csv")
        real_implements = _load_csv_or_empty(real_dir / "implements.csv")
        real_orders = _load_csv_or_empty(real_dir / "orders.csv")

        if real_depots:
            depots = _merge_real_into_synthetic(real_depots, depots, "depot_id")
        if real_vehicles:
            vehicles = _merge_real_into_synthetic(real_vehicles, vehicles, "vehicle_id")
        if real_implements:
            implements = _merge_real_into_synthetic(real_implements, implements, "implement_id")
        if real_orders:
            orders = _merge_real_into_synthetic(real_orders, orders, "order_id")

    _write_csv(depots, out_dir / "depots.csv")
    _write_csv(vehicles, out_dir / "vehicles.csv")
    _write_csv(implements, out_dir / "implements.csv")
    _write_csv(operators, out_dir / "operators.csv")
    _write_csv(fields, out_dir / "fields.csv")
    _write_csv(orders, out_dir / "orders.csv")
    _write_json(contracts, out_dir / "contracts.json")
    _write_json(weather, out_dir / "weather.json")

    metadata: dict[str, Any] = {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "run_metadata": {
            "timestamp": ts,
            "seed": seed,
            "n_vehicles": len(vehicles),
            "n_implements": len(implements),
            "n_orders": len(orders),
            "n_depots": len(depots),
            "n_operators": len(operators),
            "n_fields": len(fields),
            "n_contracts": len(contracts),
            "data_path": data_path,
        },
    }
    _write_json(metadata, out_dir / "metadata.json")

    logger.info(
        "Generated: %d vehicles, %d implements, %d orders, %d depots -> %s",
        len(vehicles), len(implements), len(orders), len(depots), out_dir,
    )
