"""Compute schedule analysis metrics from solve artifacts."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime

from fl_op.solver.analysis.artifacts import SolveArtifacts


@dataclass(frozen=True)
class ScheduleStats:
    n_orders: int
    n_vehicles: int
    n_implements: int
    n_dispatched: int
    n_infeasible: int
    total_margin: float
    greedy_margin: float
    improvement: float
    total_fuel: float
    total_fertilizer: float
    avg_duration_h: float
    avg_margin: float
    avg_fuel: float
    vehicle_counts: Counter[str]
    implement_counts: Counter[str]
    operator_counts: Counter[str]
    cluster_counts: Counter[str]
    infeasible_reasons: Counter[str]
    margin_by_cluster: dict[str, float]
    fuel_by_cluster: dict[str, float]
    starts_by_day: Counter[str]


def build_schedule_stats(artifacts: SolveArtifacts) -> ScheduleStats:
    metadata = artifacts.metadata
    kpis = artifacts.kpis
    schedule = artifacts.schedule
    infeasible = artifacts.infeasible

    n_orders = int(metadata.get("n_orders", 0) or 0)
    n_vehicles = int(metadata.get("n_vehicles", 0) or 0)
    n_implements = int(metadata.get("n_implements", 0) or 0)
    n_dispatched = int(kpis.get("n_dispatched", len(schedule)) or 0)
    n_infeasible = int(kpis.get("n_infeasible", len(infeasible)) or 0)

    vehicle_counts = Counter(d.get("vehicle_id", "unknown") for d in schedule)
    implement_counts = Counter(d.get("implement_id", "unknown") for d in schedule)
    operator_counts = Counter(d.get("operator_id", "unknown") for d in schedule)
    cluster_counts = Counter(d.get("cluster_id", "unknown") for d in schedule)
    infeasible_reasons = Counter(
        item.get("reason", "unknown") for item in infeasible
    )

    margin_by_cluster: dict[str, float] = defaultdict(float)
    fuel_by_cluster: dict[str, float] = defaultdict(float)
    starts_by_day: Counter[str] = Counter()
    durations_h: list[float] = []

    for dispatch in schedule:
        cluster_id = dispatch.get("cluster_id", "unknown")
        margin_by_cluster[cluster_id] += float(
            dispatch.get("estimated_margin_eur", 0) or 0
        )
        fuel_by_cluster[cluster_id] += float(dispatch.get("estimated_fuel_l", 0) or 0)

        start = _parse_dt(dispatch.get("scheduled_start"))
        end = _parse_dt(dispatch.get("scheduled_end"))
        if start:
            starts_by_day[start.date().isoformat()] += 1
        if start and end:
            durations_h.append(max(0.0, (end - start).total_seconds() / 3600.0))

    total_margin = float(kpis.get("total_estimated_margin_eur", 0) or 0)
    total_fuel = float(kpis.get("total_fuel_l", 0) or 0)

    return ScheduleStats(
        n_orders=n_orders,
        n_vehicles=n_vehicles,
        n_implements=n_implements,
        n_dispatched=n_dispatched,
        n_infeasible=n_infeasible,
        total_margin=total_margin,
        greedy_margin=float(kpis.get("greedy_baseline_margin_eur", 0) or 0),
        improvement=float(kpis.get("solver_improvement_eur", 0) or 0),
        total_fuel=total_fuel,
        total_fertilizer=float(kpis.get("total_fertilizer_kg", 0) or 0),
        avg_duration_h=_average(durations_h),
        avg_margin=total_margin / n_dispatched if n_dispatched else 0.0,
        avg_fuel=total_fuel / n_dispatched if n_dispatched else 0.0,
        vehicle_counts=vehicle_counts,
        implement_counts=implement_counts,
        operator_counts=operator_counts,
        cluster_counts=cluster_counts,
        infeasible_reasons=infeasible_reasons,
        margin_by_cluster=dict(margin_by_cluster),
        fuel_by_cluster=dict(fuel_by_cluster),
        starts_by_day=starts_by_day,
    )


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _average(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0

