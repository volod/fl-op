"""Console renderer for schedule analysis."""

from __future__ import annotations

from collections import Counter
from typing import Callable, Mapping

from fl_op.solver.analysis.artifacts import SolveArtifacts
from fl_op.solver.analysis.metrics import ScheduleStats

BAR_WIDTH = 28


def print_analysis(artifacts: SolveArtifacts, stats: ScheduleStats) -> None:
    print("")
    print("Fleet Optimization Run Analysis")
    print("=" * 40)
    _print_metric("solve directory", str(artifacts.path))
    _print_metric("run timestamp", str(artifacts.metadata.get("timestamp", "unknown")))
    _print_metric("dataset", str(artifacts.metadata.get("data_dir", "unknown")))
    print("")

    _print_schedule(stats, artifacts)
    _print_resource_usage(stats)
    _print_economics(stats)
    _print_count_chart("Dispatches By Cluster", stats.cluster_counts)
    _print_value_chart("Margin By Cluster", stats.margin_by_cluster, _fmt_money)
    _print_value_chart("Fuel By Cluster", stats.fuel_by_cluster, lambda v: _fmt_number(v, "L"))
    _print_count_chart("Starts By Day", stats.starts_by_day)
    _print_top_resources(stats)
    _print_count_chart("Infeasibility Reasons", stats.infeasible_reasons)
    _print_runtime_resources(artifacts)


def _print_schedule(stats: ScheduleStats, artifacts: SolveArtifacts) -> None:
    print("Schedule")
    _print_metric(
        "orders dispatched",
        f"{stats.n_dispatched}/{stats.n_orders} "
        f"({_pct(stats.n_dispatched, stats.n_orders):.1f}%)",
    )
    _print_metric(
        "orders infeasible",
        f"{stats.n_infeasible}/{stats.n_orders} "
        f"({_pct(stats.n_infeasible, stats.n_orders):.1f}%)",
    )
    _print_metric("clusters", str(artifacts.metadata.get("n_clusters", 0)))
    _print_metric("avg task duration", _fmt_number(stats.avg_duration_h, "h"))
    print("")
    _print_bar("served", stats.n_dispatched, max(stats.n_orders, 1), f"of {stats.n_orders}")
    _print_bar("rejected", stats.n_infeasible, max(stats.n_orders, 1), f"of {stats.n_orders}")
    print("")


def _print_resource_usage(stats: ScheduleStats) -> None:
    print("Resource Usage")
    _print_metric(
        "vehicles used",
        f"{len(stats.vehicle_counts)}/{stats.n_vehicles} "
        f"({_pct(len(stats.vehicle_counts), stats.n_vehicles):.1f}%)",
    )
    _print_metric(
        "implements used",
        f"{len(stats.implement_counts)}/{stats.n_implements} "
        f"({_pct(len(stats.implement_counts), stats.n_implements):.1f}%)",
    )
    _print_metric("operators used", str(len(stats.operator_counts)))
    print("")
    _print_bar("vehicles", len(stats.vehicle_counts), max(stats.n_vehicles, 1), f"of {stats.n_vehicles}")
    _print_bar(
        "implements",
        len(stats.implement_counts),
        max(stats.n_implements, 1),
        f"of {stats.n_implements}",
    )
    print("")


def _print_economics(stats: ScheduleStats) -> None:
    print("Economics")
    _print_metric("total margin", _fmt_money(stats.total_margin))
    _print_metric("greedy baseline", _fmt_money(stats.greedy_margin))
    _print_metric("solver improvement", _fmt_money(stats.improvement))
    _print_metric("avg margin/order", _fmt_money(stats.avg_margin))
    _print_metric("total fuel", _fmt_number(stats.total_fuel, "L"))
    _print_metric("avg fuel/order", _fmt_number(stats.avg_fuel, "L"))
    _print_metric("total fertilizer", _fmt_number(stats.total_fertilizer, "kg"))
    print("")


def _print_top_resources(stats: ScheduleStats) -> None:
    print("Top Resources")
    for label, counts in (
        ("vehicles", stats.vehicle_counts),
        ("implements", stats.implement_counts),
        ("operators", stats.operator_counts),
    ):
        top = ", ".join(f"{key} x{count}" for key, count in counts.most_common(5))
        _print_metric(label, top or "none")
    print("")


def _print_runtime_resources(artifacts: SolveArtifacts) -> None:
    telemetry = artifacts.telemetry
    print("Runtime And Compute Resources")
    if not telemetry:
        print("  no runtime telemetry recorded")
        print("")
        return

    _print_metric("wall time", _fmt_number(float(telemetry.get("wall_seconds", 0)), "s"))
    _print_metric(
        "cpu total",
        _fmt_number(float(telemetry.get("cpu_total_seconds", 0)), "s"),
    )
    _print_metric(
        "cpu user/system",
        f"{_fmt_number(float(telemetry.get('cpu_user_seconds', 0)), 's')} / "
        f"{_fmt_number(float(telemetry.get('cpu_system_seconds', 0)), 's')}",
    )
    _print_metric(
        "cpu efficiency",
        f"{float(telemetry.get('cpu_efficiency_pct', 0)):.1f}%",
    )
    _print_metric("max rss", _fmt_number(float(telemetry.get("max_rss_mb", 0)), "MB"))
    _print_metric("cpu count", str(telemetry.get("available_cpu_count", "unknown")))

    phases = telemetry.get("phase_seconds", {})
    if phases:
        print("")
        _print_value_chart("Runtime By Phase", phases, lambda v: _fmt_number(v, "s"))


def _print_count_chart(title: str, counts: Counter[str]) -> None:
    print(title)
    max_count = max(counts.values(), default=0)
    for label, count in sorted(counts.items()):
        print(f"  {label:<18} [{_bar(count, max_count)}] {count:>4}")
    if not counts:
        print("  none")
    print("")


def _print_value_chart(
    title: str,
    values: Mapping[str, float],
    formatter: Callable[[float], str],
) -> None:
    print(title)
    max_value = max(values.values(), default=0.0)
    for label, value in sorted(values.items()):
        print(f"  {label:<18} [{_bar(value, max_value)}] {formatter(value):>16}")
    if not values:
        print("  none")
    print("")


def _print_metric(label: str, value: str) -> None:
    print(f"  {label:<24} {value}")


def _print_bar(label: str, value: float, maximum: float, suffix: str = "") -> None:
    suffix_text = f" {suffix}" if suffix else ""
    print(f"  {label:<18} [{_bar(value, maximum)}] {value:>8.2f}{suffix_text}")


def _bar(value: float, maximum: float) -> str:
    filled = 0 if maximum <= 0 else round((value / maximum) * BAR_WIDTH)
    filled = max(0, min(BAR_WIDTH, filled))
    return "#" * filled + "." * (BAR_WIDTH - filled)


def _pct(part: int | float, total: int | float) -> float:
    if not total:
        return 0.0
    return (float(part) / float(total)) * 100.0


def _fmt_money(value: float) -> str:
    return f"{value:,.2f} EUR"


def _fmt_number(value: float, unit: str = "") -> str:
    suffix = f" {unit}" if unit else ""
    return f"{value:,.2f}{suffix}"
