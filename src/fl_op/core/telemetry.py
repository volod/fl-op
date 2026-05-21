"""Runtime and process resource telemetry."""

from __future__ import annotations

import multiprocessing
import os
import resource
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class RunTelemetry:
    """Track phase timings and process resource usage for a command run."""

    started_at: float = field(default_factory=time.perf_counter)
    phase_started_at: float = field(default_factory=time.perf_counter)
    phases: dict[str, float] = field(default_factory=dict)

    def mark_phase(self, name: str) -> None:
        now = time.perf_counter()
        self.phases[name] = round(now - self.phase_started_at, 3)
        self.phase_started_at = now

    def snapshot(self) -> dict[str, Any]:
        usage = resource.getrusage(resource.RUSAGE_SELF)
        child_usage = resource.getrusage(resource.RUSAGE_CHILDREN)
        wall_seconds = time.perf_counter() - self.started_at
        cpu_user_seconds = usage.ru_utime + child_usage.ru_utime
        cpu_system_seconds = usage.ru_stime + child_usage.ru_stime
        cpu_total_seconds = cpu_user_seconds + cpu_system_seconds

        return {
            "wall_seconds": round(wall_seconds, 3),
            "cpu_user_seconds": round(cpu_user_seconds, 3),
            "cpu_system_seconds": round(cpu_system_seconds, 3),
            "cpu_total_seconds": round(cpu_total_seconds, 3),
            "cpu_efficiency_pct": round((cpu_total_seconds / wall_seconds) * 100, 1)
            if wall_seconds
            else 0.0,
            "max_rss_mb": round(_max_rss_mb(usage, child_usage), 2),
            "available_cpu_count": multiprocessing.cpu_count(),
            "process_id": os.getpid(),
            "phase_seconds": dict(self.phases),
        }


def _max_rss_mb(
    usage: resource.struct_rusage,
    child_usage: resource.struct_rusage,
) -> float:
    # Linux reports ru_maxrss in KiB. macOS reports bytes; this project runs on Linux in CI/dev.
    return max(usage.ru_maxrss, child_usage.ru_maxrss) / 1024.0

