"""Per-pass coverage geometry: parse passes, accumulate, log the trail.

A ``task.progress`` event (or work-progress telemetry observation) may report
the geometry covered in that pass instead of a bare completed fraction: either
an explicit covered polygon or a path swept by an implement width. The stream
applier accumulates these passes per task (``stream/apply.py``); this module
turns one pass payload into geometry rings and reports the overlap-corrected
covered share against the task's original work area, so remaining work is
refined spatially rather than from a self-reported scalar.

Coverage is tracked as geometry, not a running sum of per-pass areas, so two
passes over the same strip are not double-counted -- the spatially-explicit
signal the future-work item asked for. The trail logger appends one record per
pass for rolling progress explanations.
"""

import ast
import json
import logging
import pathlib
from typing import Any, Optional

from fl_op.core.constants import (
    COVERAGE_TRAIL_FILENAME,
    HECTARES_PER_SQUARE_KM,
    QUALITY_TREND_DIRNAME,
)
from fl_op.core.geometry import polygon_rings_area_km2, swept_polygon
from fl_op.core.paths import DATA_ROOT
from fl_op.solver.restrictions import parse_polygon

logger = logging.getLogger(__name__)

# task.progress / observation payload fields carrying coverage geometry.
# An explicit covered polygon ([lat, lon] vertices) wins; otherwise a covered
# path ([lat, lon] points) swept by the implement swath width in meters.
PAYLOAD_COVERED_POLYGON = "covered_polygon"
PAYLOAD_COVERED_PATH = "covered_path"
PAYLOAD_SWATH_WIDTH_M = "swath_width_m"

# A (x=lon, y=lat) polygon ring, matching parse_polygon / swept_polygon output.
Ring = list[tuple[float, float]]


def has_coverage_payload(payload: dict[str, Any]) -> bool:
    """True when the payload carries per-pass coverage geometry."""
    return bool(payload.get(PAYLOAD_COVERED_POLYGON) or payload.get(PAYLOAD_COVERED_PATH))


def pass_ring_from_payload(payload: dict[str, Any]) -> Optional[Ring]:
    """The covered polygon ring of one pass, or None when none is usable.

    An explicit ``covered_polygon`` ([lat, lon] vertices) is taken as-is; a
    ``covered_path`` ([lat, lon] points) is swept by ``swath_width_m`` into a
    swath polygon.
    """
    polygon = payload.get(PAYLOAD_COVERED_POLYGON)
    if polygon:
        ring = parse_polygon(polygon)
        return ring or None
    path = payload.get(PAYLOAD_COVERED_PATH)
    if not path:
        return None
    width = payload.get(PAYLOAD_SWATH_WIDTH_M)
    try:
        width_m = float(width)
    except (TypeError, ValueError):
        logger.warning("covered_path pass has no usable %s: %r", PAYLOAD_SWATH_WIDTH_M, width)
        return None
    latlon = _path_latlon(path)
    if not latlon:
        return None
    ring = swept_polygon(latlon, width_m)
    return ring or None


def work_area_area_ha(work_area_geometry: Any) -> float:
    """Geodesic area (ha) of a task work-area polygon, 0.0 when unusable.

    When a task carries an explicit work-area polygon, coverage is measured
    against its true geodesic area (the workable reference) rather than the gross
    scalar area column, so the covered share reflects the actual ground worked.
    """
    ring = parse_polygon(work_area_geometry)
    if len(ring) < 3:
        return 0.0
    return polygon_rings_area_km2([ring]) * HECTARES_PER_SQUARE_KM


def coverage_state(accumulated_rings: list[Ring], original_area_ha: float) -> dict[str, float]:
    """Overlap-corrected covered share of the original work area.

    ``covered_fraction`` is the geodesic union area of all passes (in hectares)
    over the task's original area, clamped to [0, 1]. With no positive original
    area the fraction is 0.0 (a point task has no measurable coverage).
    """
    covered_ha = polygon_rings_area_km2(accumulated_rings) * HECTARES_PER_SQUARE_KM
    if original_area_ha > 0:
        fraction = min(1.0, covered_ha / original_area_ha)
        remaining_ha = max(0.0, original_area_ha - covered_ha)
    else:
        fraction = 0.0
        remaining_ha = 0.0
    return {
        "covered_fraction": round(fraction, 6),
        "covered_area_ha": round(covered_ha, 4),
        "remaining_area_ha": round(remaining_ha, 4),
    }


def _path_latlon(raw: Any) -> list[tuple[float, float]]:
    """Parse a pass centreline of [lat, lon] points (list or stringified list)."""
    if isinstance(raw, str):
        try:
            raw = ast.literal_eval(raw)
        except (ValueError, SyntaxError):
            logger.warning("Skipping unparseable covered_path %r", raw)
            return []
    if not isinstance(raw, (list, tuple)):
        return []
    points: list[tuple[float, float]] = []
    for pair in raw:
        if not isinstance(pair, (list, tuple)) or len(pair) < 2:
            continue
        try:
            points.append((float(pair[0]), float(pair[1])))
        except (TypeError, ValueError):
            continue
    return points


def _trail_path(path: Optional[pathlib.Path]) -> pathlib.Path:
    return path or (DATA_ROOT / QUALITY_TREND_DIRNAME / COVERAGE_TRAIL_FILENAME)


def record_coverage(
    reports: list[dict[str, Any]],
    path: Optional[pathlib.Path] = None,
) -> list[dict[str, Any]]:
    """Append one coverage-trail record per pass; return the records."""
    if not reports:
        return reports
    target = _trail_path(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a") as fh:
            for record in reports:
                fh.write(json.dumps(record) + "\n")
    except OSError as exc:
        logger.warning("Could not append coverage records to %s: %s", target, exc)
    return reports


def coverage_stats(path: Optional[pathlib.Path] = None) -> dict[str, Any]:
    """Aggregate the per-pass coverage trail into a rolling progress summary.

    Reduces to the latest record per task (the most passes), then reports the
    pass count, how many tasks have spatial coverage, the total covered area,
    and the mean covered share -- the spatially-explicit counterpart to the
    completion lead-time distribution.
    """
    target = _trail_path(path)
    if not target.exists():
        return {}
    latest: dict[str, dict[str, Any]] = {}
    n_passes = 0
    for line in target.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        n_passes += 1
        task_id = str(record.get("task_id", ""))
        prev = latest.get(task_id)
        if prev is None or record.get("n_passes", 0) >= prev.get("n_passes", 0):
            latest[task_id] = record
    if not latest:
        return {}
    fractions = [float(r.get("covered_fraction", 0.0)) for r in latest.values()]
    covered = [float(r.get("covered_area_ha", 0.0)) for r in latest.values()]
    return {
        "n_passes": n_passes,
        "n_tasks_with_coverage": len(latest),
        "total_covered_area_ha": round(sum(covered), 4),
        "mean_covered_fraction": round(sum(fractions) / len(fractions), 4),
    }
