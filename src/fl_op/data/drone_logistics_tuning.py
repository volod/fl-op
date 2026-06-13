"""Checked-in tuning defaults for the drone-logistics domain."""

from __future__ import annotations

import copy
import pathlib
from typing import Any

import yaml

from fl_op.core.paths import DOMAINS_ROOT

DRONE_LOGISTICS_TUNING_FILENAME = "tuning.yaml"

_DEFAULT_TUNING: dict[str, Any] = {
    "schemaVersion": "1.0",
    "domain": "drone_logistics",
    "weather": {
        "maxWindMs": 11.0,
        "maxRainMmPerH": 2.5,
    },
    "fleet": {
        "ugvShare": 0.6,
    },
    "ugvRoadSpeedBucketsKmh": {
        "denseUrban": 18.0,
        "arterial": 26.0,
        "industrial": 34.0,
    },
    "payloadCapacityClassesKg": {
        "UGV": {
            "light": 180.0,
            "medium": 420.0,
            "heavy": 900.0,
        },
        "UAV": {
            "micro": 3.0,
            "standard": 7.5,
            "heavy": 12.0,
        },
    },
    "deadlinePenaltyEurPerDayByCustomerClass": {
        "manufacturer": 900.0,
        "restaurant": 2600.0,
        "online_store": 650.0,
    },
    "deliveryDropPenaltyMultiplierByCustomerClass": {
        "manufacturer": 1.2,
        "restaurant": 1.5,
        "online_store": 1.0,
    },
    "energyCostRates": {
        "fuelEquivalentEurPerL": 1.15,
        "electricityEurPerKwh": 0.18,
    },
    "solver": {
        "clusterTargetSize": 36,
        "clusterSolveTimeLimitS": 75,
        "lnsTimeLimitS": 1,
        "rollingInstabilityPenalty": 1400,
    },
}


def default_drone_logistics_tuning_path() -> pathlib.Path:
    return DOMAINS_ROOT / "drone_logistics" / DRONE_LOGISTICS_TUNING_FILENAME


def load_drone_logistics_tuning(
    path: pathlib.Path | None = None,
) -> dict[str, Any]:
    """Load domain tuning, layered on defaults for forward compatibility."""
    tuning = copy.deepcopy(_DEFAULT_TUNING)
    target = path or default_drone_logistics_tuning_path()
    if not target.exists():
        return tuning
    raw = yaml.safe_load(target.read_text()) or {}
    if not isinstance(raw, dict):
        return tuning
    _deep_update(tuning, raw)
    return tuning


def drone_solver_parameter_overrides(tuning: dict[str, Any]) -> dict[str, Any]:
    solver = tuning.get("solver") or {}
    return {
        key: value
        for key, value in {
            "cluster_target_size": solver.get("clusterTargetSize"),
            "cluster_solve_time_limit_s": solver.get("clusterSolveTimeLimitS"),
            "lns_time_limit_s": solver.get("lnsTimeLimitS"),
            "rolling_change_penalty": solver.get("rollingInstabilityPenalty"),
        }.items()
        if value is not None
    }


def apply_drone_profile_tuning(profile: Any, tuning: dict[str, Any] | None = None) -> Any:
    """Layer drone domain tuning onto a loaded OptimizationProfile."""
    tuning = tuning or load_drone_logistics_tuning()
    weather = tuning.get("weather") or {}
    if not weather:
        return profile
    weather_policy = profile.weatherPolicy.model_copy(
        update={
            "maxWindMs": float(
                weather.get("maxWindMs", profile.weatherPolicy.maxWindMs)
            ),
            "maxRainMmPerH": float(
                weather.get("maxRainMmPerH", profile.weatherPolicy.maxRainMmPerH)
            ),
        }
    )
    return profile.model_copy(update={"weatherPolicy": weather_policy})


def _deep_update(target: dict[str, Any], source: dict[str, Any]) -> None:
    for key, value in source.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_update(target[key], value)
        else:
            target[key] = value
