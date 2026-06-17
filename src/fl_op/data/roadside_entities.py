"""Synthetic generators for the roadside-infrastructure domain pack."""

import logging
import pathlib
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np

from fl_op.core.constants import (
    ARTIFACT_SCHEMA_VERSION,
    DEFAULT_DATA_FORMAT,
    EQUIPMENT_SERVICE_OPERATION,
)
from fl_op.core.paths import DATA_ROOT
from fl_op.io import get_codec
from fl_op.data.ingestion import stamp_ingested
from fl_op.data.io import _write_json, _write_jsonl

logger = logging.getLogger(__name__)

_TABULAR_DATASETS = [
    "service-depots",
    "service-vehicles",
    "service-kits",
    "technicians",
    "road-segments",
    "signage",
    "maintenance-jobs",
]

_SIGN_KINDS = ("vms_sign", "speed_radar", "traffic_counter", "warning_sign")


def _point_near(rng: np.random.Generator, base_lat: float, base_lon: float) -> tuple[float, float]:
    return (
        float(base_lat + rng.normal(0.0, 0.08)),
        float(base_lon + rng.normal(0.0, 0.12)),
    )


def _generate_service_depots(
    rng: np.random.Generator,
    n_depots: int,
) -> list[dict[str, Any]]:
    depots = []
    for i in range(max(1, n_depots)):
        lat, lon = _point_near(rng, 50.45, 30.52)
        depots.append(
            {
                "depot_id": f"road_depot_{i:03d}",
                "name": f"Roadside depot {i + 1}",
                "lat": lat,
                "lon": lon,
                "fuel_litres": float(rng.uniform(8000, 25000)),
            }
        )
    return depots


def _generate_road_segments(
    rng: np.random.Generator,
    n_segments: int,
    now: datetime,
) -> list[dict[str, Any]]:
    segments = []
    for i in range(max(1, n_segments)):
        lat, lon = _point_near(rng, 50.45, 30.52)
        closure_windows: list[str] = []
        if i % 5 == 0:
            start = now.replace(hour=7, minute=0, second=0, microsecond=0)
            end = start + timedelta(hours=2)
            closure_windows = [f"{start.isoformat()}/{end.isoformat()}"]
        segments.append(
            {
                "segment_id": f"segment_{i:04d}",
                "road_ref": f"M-{i % 20:02d}",
                "midpoint_lat": lat,
                "midpoint_lon": lon,
                "surface_type": rng.choice(["asphalt", "concrete", "gravel"]),
                "closure_windows": closure_windows,
                "length_km": float(rng.uniform(0.4, 4.5)),
            }
        )
    return segments


def _generate_service_vehicles(
    rng: np.random.Generator,
    n_vehicles: int,
    depots: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    vehicles = []
    for i in range(max(1, n_vehicles)):
        depot = depots[i % len(depots)]
        vehicles.append(
            {
                "vehicle_id": f"road_truck_{i:04d}",
                "vehicle_class": rng.choice(["bucket_truck", "service_van"]),
                "engine_power_kw": float(rng.uniform(85, 180)),
                "tank_litres": float(rng.uniform(70, 160)),
                "burn_l_per_h": float(rng.uniform(5.0, 12.0)),
                "current_lat": float(depot["lat"]) + float(rng.normal(0.0, 0.01)),
                "current_lon": float(depot["lon"]) + float(rng.normal(0.0, 0.01)),
                "depot_id": depot["depot_id"],
                "transit_speed_kmh": float(rng.uniform(45, 70)),
            }
        )
    return vehicles


def _generate_service_kits(
    rng: np.random.Generator,
    n_kits: int,
    depots: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    kits = []
    for i in range(max(1, n_kits)):
        depot = depots[i % len(depots)]
        kits.append(
            {
                "kit_id": f"road_kit_{i:04d}",
                "kit_class": rng.choice(["electrical_kit", "signage_kit"]),
                "supported_operations": [EQUIPMENT_SERVICE_OPERATION],
                "draw_power_kw": float(rng.uniform(15, 45)),
                "working_width_m": 1.0,
                "min_speed_kmh": 1.0,
                "max_speed_kmh": 8.0,
                "depot_id": depot["depot_id"],
                "work_rates": {"ha": "1.0"},
            }
        )
    return kits


def _generate_technicians(
    n_technicians: int,
    depots: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    techs = []
    for i in range(max(1, n_technicians)):
        depot = depots[i % len(depots)]
        techs.append(
            {
                "technician_id": f"road_tech_{i:04d}",
                "full_name": f"Roadside Technician {i + 1}",
                "shift_start_s": 6 * 3600,
                "shift_end_s": 18 * 3600,
                "certified_operations": [EQUIPMENT_SERVICE_OPERATION],
                "depot_id": depot["depot_id"],
            }
        )
    return techs


def _generate_signage(
    rng: np.random.Generator,
    n_signs: int,
    segments: list[dict[str, Any]],
    now: datetime,
) -> list[dict[str, Any]]:
    signs = []
    for i in range(max(1, n_signs)):
        segment = segments[i % len(segments)]
        signs.append(
            {
                "sign_id": f"sign_{i:05d}",
                "sign_kind": _SIGN_KINDS[i % len(_SIGN_KINDS)],
                "road_segment_id": segment["segment_id"],
                "lat": float(segment["midpoint_lat"]) + float(rng.normal(0.0, 0.002)),
                "lon": float(segment["midpoint_lon"]) + float(rng.normal(0.0, 0.002)),
                "mobility": "stationary",
                "last_service_at": (now - timedelta(days=120 + i % 40)).isoformat(),
                "service_interval_days": 90.0,
                "installed_at": (now - timedelta(days=1000 + i)).date().isoformat(),
            }
        )
    return signs


def _generate_inspection_rounds(
    rng: np.random.Generator,
    signs: list[dict[str, Any]],
    now: datetime,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for i, sign in enumerate(signs):
        inspected_at = now - timedelta(hours=i % 12)
        # A finding recorded in the field arrives at the platform after a delivery
        # delay; stamping it as ingested_at makes arrival order explicit across
        # restarts instead of approximating it by source row order. Both findings
        # of a sign share the one arrival time.
        ingested_at = stamp_ingested(inspected_at, rng)
        battery = 18.0 if i % 4 == 0 else float(rng.uniform(42, 95))
        health = "degraded" if i % 5 == 0 else "healthy"
        findings.extend(
            [
                {
                    "finding_id": f"finding_{i:05d}_battery",
                    "round_id": f"round_{now.strftime('%Y%m%d')}",
                    "sign_id": sign["sign_id"],
                    "metric": "battery_pct",
                    "value": battery,
                    "state_value": "",
                    "unit": "%",
                    "inspected_at": inspected_at.isoformat(),
                    "ingested_at": ingested_at,
                    "quality_flag": "ok",
                },
                {
                    "finding_id": f"finding_{i:05d}_condition",
                    "round_id": f"round_{now.strftime('%Y%m%d')}",
                    "sign_id": sign["sign_id"],
                    "metric": "sign_condition",
                    "value": None,
                    "state_value": health,
                    "unit": "",
                    "inspected_at": inspected_at.isoformat(),
                    "ingested_at": ingested_at,
                    "quality_flag": "ok",
                },
            ]
        )
    return findings


def run_generate_roadside(
    n_service_vehicles: int,
    n_service_kits: int,
    n_signs: int,
    n_depots: int,
    seed: int | None,
    fmt: str = DEFAULT_DATA_FORMAT,
) -> pathlib.Path:
    """Generate a runnable roadside-infrastructure dataset."""
    rng = np.random.default_rng(seed)
    now = datetime.now(tz=timezone.utc)
    ts = now.strftime("%Y%m%dT%H%M%S")
    out_dir = DATA_ROOT / "generate-data" / ts
    out_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Writing roadside dataset to %s (format: %s)", out_dir, fmt)

    codec = get_codec(fmt)
    depots = _generate_service_depots(rng, n_depots)
    segments = _generate_road_segments(
        rng, max(n_depots, max(1, n_signs) // 3), now
    )
    vehicles = _generate_service_vehicles(rng, n_service_vehicles, depots)
    kits = _generate_service_kits(rng, n_service_kits, depots)
    technicians = _generate_technicians(n_service_vehicles, depots)
    signage = _generate_signage(rng, n_signs, segments, now)
    inspections = _generate_inspection_rounds(rng, signage, now)
    maintenance_jobs: list[dict[str, Any]] = []

    for name, records in zip(
        _TABULAR_DATASETS,
        [depots, vehicles, kits, technicians, segments, signage, maintenance_jobs],
    ):
        codec.write(records, out_dir / f"{name}{codec.extension}")
    _write_jsonl(inspections, out_dir / "inspection-rounds.jsonl")

    metadata: dict[str, Any] = {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "run_metadata": {
            "timestamp": ts,
            "domain": "roadside",
            "seed": seed,
            "data_format": fmt,
            "n_service_vehicles": len(vehicles),
            "n_service_kits": len(kits),
            "n_technicians": len(technicians),
            "n_service_depots": len(depots),
            "n_road_segments": len(segments),
            "n_signage": len(signage),
            "n_inspection_findings": len(inspections),
            "n_maintenance_jobs": len(maintenance_jobs),
        },
    }
    _write_json(metadata, out_dir / "metadata.json")
    logger.info(
        "Generated roadside: %d vehicles, %d kits, %d signs, %d depots -> %s",
        len(vehicles),
        len(kits),
        len(signage),
        len(depots),
        out_dir,
    )
    return out_dir


def generate_roadside_domain(request: Any) -> pathlib.Path:
    """Registry adapter for the roadside generator."""
    return run_generate_roadside(
        n_service_vehicles=request.vehicles,
        n_service_kits=request.implements,
        n_signs=request.orders,
        n_depots=request.depots,
        seed=request.seed,
        fmt=request.fmt,
    )
