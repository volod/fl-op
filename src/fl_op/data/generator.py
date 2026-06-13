"""Synthetic fleet dataset generator entry point.

Orchestrates entity generators and writes datasets to
$DATA_DIR/generate-data/<ISO-timestamp>/.

Sampling: NumPy-vectorised. Geographic placement: scipy BallTree haversine.
"""

import logging
import pathlib
from datetime import datetime, timezone
from typing import Any

import numpy as np

from fl_op.core.constants import ARTIFACT_SCHEMA_VERSION, DEFAULT_DATA_FORMAT
from fl_op.core.paths import DATA_ROOT
from fl_op.data.field_order_entities import _generate_fields, _generate_orders_and_contracts, _generate_weather
from fl_op.data.fleet_entities import _generate_depots, _generate_implements, _generate_operators, _generate_vehicles
from fl_op.data.io import _load_csv_or_empty, _merge_real_into_synthetic, _write_json, _write_jsonl
from fl_op.data.route_price_entities import _generate_prices, _generate_routes
from fl_op.data.sensor_entities import _generate_sensor_readings, _generate_sensors
from fl_op.io import get_codec

logger = logging.getLogger(__name__)

_TABULAR_DATASETS = [
    "depots", "vehicles", "implements", "operators", "fields", "orders",
    "sensors", "routes", "prices",
]


def run_generate(
    n_vehicles: int,
    n_implements: int,
    n_orders: int,
    n_depots: int,
    seed: int | None,
    data_path: str | None,
    fmt: str = DEFAULT_DATA_FORMAT,
) -> pathlib.Path:
    """Generate synthetic (or augmented real) fleet dataset.

    Output directory: $DATA_DIR/generate-data/<ISO-timestamp>/
    Tabular datasets are written in the requested format (avro, csv, parquet).
    JSON datasets (contracts, weather) are always written as JSON.
    """
    rng = np.random.default_rng(seed)
    now = datetime.now(tz=timezone.utc)
    ts = now.strftime("%Y%m%dT%H%M%S")

    out_dir = DATA_ROOT / "generate-data" / ts
    out_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Writing dataset to %s (format: %s)", out_dir, fmt)

    codec = get_codec(fmt)

    depots = _generate_depots(rng, n_depots)
    vehicles = _generate_vehicles(rng, n_vehicles, depots)
    implements = _generate_implements(rng, n_implements, depots)
    operators = _generate_operators(rng, n_vehicles, depots)
    fields = _generate_fields(rng, n_orders, depots, now)
    orders, contracts = _generate_orders_and_contracts(rng, n_orders, fields, now)
    weather = _generate_weather(rng, depots, now)
    sensors = _generate_sensors(rng, fields, now)
    sensor_readings = _generate_sensor_readings(rng, sensors, now)
    routes = _generate_routes(rng, depots, fields)
    prices = _generate_prices(rng, now)

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

    for name, records in zip(
        _TABULAR_DATASETS,
        [depots, vehicles, implements, operators, fields, orders, sensors, routes, prices],
    ):
        codec.write(records, out_dir / f"{name}{codec.extension}")

    _write_json(contracts, out_dir / "contracts.json")
    _write_json(weather, out_dir / "weather.json")
    _write_jsonl(sensor_readings, out_dir / "sensor-readings.jsonl")

    metadata: dict[str, Any] = {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "run_metadata": {
            "timestamp": ts,
            "domain": "agricultural",
            "seed": seed,
            "data_format": fmt,
            "n_vehicles": len(vehicles),
            "n_implements": len(implements),
            "n_orders": len(orders),
            "n_depots": len(depots),
            "n_operators": len(operators),
            "n_fields": len(fields),
            "n_contracts": len(contracts),
            "n_sensors": len(sensors),
            "n_sensor_readings": len(sensor_readings),
            "n_routes": len(routes),
            "n_prices": len(prices),
            "data_path": data_path,
        },
    }
    _write_json(metadata, out_dir / "metadata.json")

    logger.info(
        "Generated: %d vehicles, %d implements, %d orders, %d depots -> %s",
        len(vehicles), len(implements), len(orders), len(depots), out_dir,
    )
    return out_dir


_CONSTRUCTION_TABULAR_DATASETS = [
    "yards", "machines", "attachments", "operators", "sites", "jobs",
]


def run_generate_construction(
    n_machines: int,
    n_attachments: int,
    n_jobs: int,
    n_yards: int,
    seed: int | None,
    fmt: str = DEFAULT_DATA_FORMAT,
) -> pathlib.Path:
    """Generate a synthetic construction-earthworks dataset.

    Output directory: $DATA_DIR/generate-data/<ISO-timestamp>/, holding the
    datasets the construction domain pack registers (machines, attachments,
    operators, yards, sites, jobs). Run planning against it with
    ACTIVE_DOMAIN=construction.
    """
    from fl_op.data.construction_entities import (
        _generate_attachments,
        _generate_construction_operators,
        _generate_jobs,
        _generate_machines,
        _generate_sites,
        _generate_yards,
    )

    rng = np.random.default_rng(seed)
    now = datetime.now(tz=timezone.utc)
    ts = now.strftime("%Y%m%dT%H%M%S")

    out_dir = DATA_ROOT / "generate-data" / ts
    out_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Writing construction dataset to %s (format: %s)", out_dir, fmt)

    codec = get_codec(fmt)

    yards = _generate_yards(rng, n_yards)
    machines = _generate_machines(rng, n_machines, yards)
    attachments = _generate_attachments(rng, n_attachments, yards)
    operators = _generate_construction_operators(rng, n_machines, yards)
    sites = _generate_sites(rng, n_jobs)
    jobs = _generate_jobs(rng, n_jobs, sites, now)

    for name, records in zip(
        _CONSTRUCTION_TABULAR_DATASETS,
        [yards, machines, attachments, operators, sites, jobs],
    ):
        codec.write(records, out_dir / f"{name}{codec.extension}")

    metadata: dict[str, Any] = {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "run_metadata": {
            "timestamp": ts,
            "domain": "construction",
            "seed": seed,
            "data_format": fmt,
            "n_machines": len(machines),
            "n_attachments": len(attachments),
            "n_operators": len(operators),
            "n_yards": len(yards),
            "n_sites": len(sites),
            "n_jobs": len(jobs),
        },
    }
    _write_json(metadata, out_dir / "metadata.json")

    logger.info(
        "Generated: %d machines, %d attachments, %d jobs, %d yards -> %s",
        len(machines), len(attachments), len(jobs), len(yards), out_dir,
    )
    return out_dir


def generate_agricultural_domain(request: Any) -> pathlib.Path:
    """Registry adapter for the agricultural generator."""
    return run_generate(
        n_vehicles=request.vehicles,
        n_implements=request.implements,
        n_orders=request.orders,
        n_depots=request.depots,
        seed=request.seed,
        data_path=request.data_path,
        fmt=request.fmt,
    )


def generate_construction_domain(request: Any) -> pathlib.Path:
    """Registry adapter for the construction generator."""
    return run_generate_construction(
        n_machines=request.vehicles,
        n_attachments=request.implements,
        n_jobs=request.orders,
        n_yards=request.depots,
        seed=request.seed,
        fmt=request.fmt,
    )
