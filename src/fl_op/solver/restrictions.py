"""Location restriction semantics: restricted zones and time-restricted areas.

Two structural restrictions a location may declare (canonical bindings
``location.restrictedOperations``, ``location.restrictionWindows`` and
``location.polygon``):

- A restricted zone prohibits specific operation types at the location; a
  task demanding a prohibited operation there can never be served.
- A geometric restricted area is any other location with a polygon and
  prohibited operation set; tasks whose site geometry intersects the area are
  blocked even when the task's location id is different from the area id.
- A time-restricted area prohibits *starting* execution during declared
  intervals (curfew, protection period). The routing model removes those
  intervals from the task's allowed start range; this module pre-filters the
  tasks whose entire feasible range is blocked.

Like workable time windows, these are data semantics applied whenever the
projected rows carry values; ``enforcement.py`` stays profile-driven.
"""

import logging
import ast
import dataclasses
from datetime import datetime
from typing import Any, Optional

from fl_op.canonical.enums import ReasonCode
from fl_op.core.constants import (
    RESTRICTION_MIN_WORKABLE_AREA_FRACTION,
    ROUTING_HORIZON_S,
)
from fl_op.core.geometry import (
    polygon_difference_area_km2,
    polygon_rings_area_km2,
    unrestricted_area_fraction,
)
from fl_op.solver.enforcement import ops_set
from fl_op.solver.task_relations import parse_time_windows

logger = logging.getLogger(__name__)

# Closed integer interval [start, end] in epoch seconds or horizon offsets.
Interval = tuple[int, int]
Point = tuple[float, float]


def merge_intervals(intervals: list[Interval]) -> list[Interval]:
    """Merge overlapping or adjacent closed intervals into a sorted minimal set."""
    merged: list[Interval] = []
    for start, end in sorted(i for i in intervals if i[1] >= i[0]):
        if merged and start <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def subtract_intervals(allowed: list[Interval], blocked: list[Interval]) -> list[Interval]:
    """Remove every blocked interval from the allowed set (closed intervals)."""
    result = merge_intervals(allowed)
    for b_start, b_end in merge_intervals(blocked):
        next_result: list[Interval] = []
        for a_start, a_end in result:
            if b_end < a_start or b_start > a_end:
                next_result.append((a_start, a_end))
                continue
            if a_start < b_start:
                next_result.append((a_start, b_start - 1))
            if b_end < a_end:
                next_result.append((b_end + 1, a_end))
        result = next_result
    return result


def parse_polygon(raw: Any) -> list[Point]:
    """Parse a polygon represented as [[lat, lon], ...] or a stringified list."""
    if not raw:
        return []
    if isinstance(raw, str):
        try:
            raw = ast.literal_eval(raw)
        except (ValueError, SyntaxError):
            logger.warning("Skipping unparseable polygon %r", raw)
            return []
    if isinstance(raw, dict) and raw.get("type") == "Polygon":
        coords = (raw.get("coordinates") or [[]])[0]
        # GeoJSON uses [lon, lat]; normalize to the internal (x=lon, y=lat).
        return [
            (float(pair[0]), float(pair[1]))
            for pair in coords
            if isinstance(pair, (list, tuple)) and len(pair) >= 2
        ]
    points: list[Point] = []
    if not isinstance(raw, (list, tuple)):
        return []
    for pair in raw:
        if not isinstance(pair, (list, tuple)) or len(pair) < 2:
            continue
        # Canonical location polygons are [lat, lon] pairs. Geometry math uses
        # x=lon, y=lat, so the coordinate order is flipped here.
        points.append((float(pair[1]), float(pair[0])))
    if len(points) >= 2 and points[0] == points[-1]:
        points.pop()
    return points if len(points) >= 3 else []


def _bbox(poly: list[Point]) -> tuple[float, float, float, float]:
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    return min(xs), min(ys), max(xs), max(ys)


def _bbox_intersects(a: list[Point], b: list[Point]) -> bool:
    amin_x, amin_y, amax_x, amax_y = _bbox(a)
    bmin_x, bmin_y, bmax_x, bmax_y = _bbox(b)
    return not (
        amax_x < bmin_x
        or bmax_x < amin_x
        or amax_y < bmin_y
        or bmax_y < amin_y
    )


def point_in_polygon(point: Point, polygon: list[Point]) -> bool:
    """Ray-casting point-in-polygon check, boundary inclusive."""
    if len(polygon) < 3:
        return False
    x, y = point
    inside = False
    prev = polygon[-1]
    for cur in polygon:
        if _point_on_segment(point, prev, cur):
            return True
        xi, yi = cur
        xj, yj = prev
        if (yi > y) != (yj > y):
            x_at_y = (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi
            if x <= x_at_y:
                inside = not inside
        prev = cur
    return inside


def polygons_intersect(a: list[Point], b: list[Point]) -> bool:
    """Return True when two polygons overlap, touch, or one contains the other."""
    if len(a) < 3 or len(b) < 3:
        return False
    if not _bbox_intersects(a, b):
        return False
    for pa1, pa2 in _edges(a):
        for pb1, pb2 in _edges(b):
            if _segments_intersect(pa1, pa2, pb1, pb2):
                return True
    return point_in_polygon(a[0], b) or point_in_polygon(b[0], a)


def _edges(poly: list[Point]) -> list[tuple[Point, Point]]:
    return list(zip(poly, [*poly[1:], poly[0]]))


def _orientation(a: Point, b: Point, c: Point) -> float:
    return (b[1] - a[1]) * (c[0] - b[0]) - (b[0] - a[0]) * (c[1] - b[1])


def _point_on_segment(p: Point, a: Point, b: Point) -> bool:
    eps = 1e-12
    cross = (p[1] - a[1]) * (b[0] - a[0]) - (p[0] - a[0]) * (b[1] - a[1])
    if abs(cross) > eps:
        return False
    return (
        min(a[0], b[0]) - eps <= p[0] <= max(a[0], b[0]) + eps
        and min(a[1], b[1]) - eps <= p[1] <= max(a[1], b[1]) + eps
    )


def _segments_intersect(p1: Point, q1: Point, p2: Point, q2: Point) -> bool:
    o1 = _orientation(p1, q1, p2)
    o2 = _orientation(p1, q1, q2)
    o3 = _orientation(p2, q2, p1)
    o4 = _orientation(p2, q2, q1)
    if (o1 > 0) != (o2 > 0) and (o3 > 0) != (o4 > 0):
        return True
    return (
        _point_on_segment(p2, p1, q1)
        or _point_on_segment(q2, p1, q1)
        or _point_on_segment(p1, p2, q2)
        or _point_on_segment(q1, p2, q2)
    )


def _epoch_intervals(
    raw_windows: Any,
    clamp_start: int,
    clamp_end: int,
) -> list[Interval]:
    """Parse ISO "from/to" windows into clamped closed epoch-second intervals."""
    intervals: list[Interval] = []
    for start, end in parse_time_windows(raw_windows):
        start_s = max(clamp_start, int(start.timestamp()))
        end_s = clamp_end if end is None else min(clamp_end, int(end.timestamp()))
        if end_s >= start_s:
            intervals.append((start_s, end_s))
    return intervals


def allowed_start_intervals(
    order: Any,
    site: Optional[Any],
    now_epoch: int,
    deadline_epoch: int,
) -> list[Interval]:
    """Closed epoch-second intervals in which the task execution may start.

    The base range is [now, deadline], narrowed to the task's workable windows
    when declared, minus the site's restriction windows. An empty result means
    no admissible start exists.
    """
    base: list[Interval] = [(now_epoch, deadline_epoch)]
    workable = _epoch_intervals(order.time_windows, now_epoch, deadline_epoch)
    if parse_time_windows(order.time_windows):
        base = workable
    blocked = (
        _epoch_intervals(site.restriction_windows, now_epoch, deadline_epoch)
        if site is not None
        else []
    )
    return subtract_intervals(base, blocked)


def _deadline_epoch(order: Any, now_epoch: int) -> int:
    try:
        return int(datetime.fromisoformat(str(order.deadline)).timestamp())
    except (ValueError, TypeError):
        return now_epoch + ROUTING_HORIZON_S


def _restricted_overlap(
    order: Any,
    site: Any,
    sites: list[Any],
) -> tuple[list[list[Point]], Optional[str]]:
    """Restricted areas overlapping ``order``'s site for its operation.

    Returns ``(overlapping_polygons, point_block_id)``. ``overlapping_polygons``
    are the restricted-area rings intersecting the site polygon (used to compute
    partial-overlap severity). ``point_block_id`` is set when the site has no
    geometry and its centroid lies inside a restricted area -- a point cannot be
    partially restricted, so that case stays a hard block.
    """
    overlapping: list[list[Point]] = []
    point_block_id: Optional[str] = None
    if site is None:
        return overlapping, point_block_id
    site_polygon = parse_polygon(site.polygon)
    site_point = (float(site.lon), float(site.lat))
    for area in sites:
        if area.location_id == site.location_id:
            continue
        if order.operation_type not in ops_set(area.restricted_operations):
            continue
        area_polygon = parse_polygon(area.polygon)
        if not area_polygon:
            continue
        if site_polygon:
            if polygons_intersect(site_polygon, area_polygon):
                overlapping.append(area_polygon)
        elif point_in_polygon(site_point, area_polygon):
            point_block_id = area.location_id
            break
    return overlapping, point_block_id


def _residual_clip_fractions(
    order: Any, site: Any, overlapping: list[list[tuple[float, float]]]
) -> tuple[float, float]:
    """Restriction severity on the uncovered remainder, and the residual fraction.

    With a task work-area polygon (falling back to the site polygon), restriction
    severity is the unrestricted share of the *uncovered* remainder
    (work minus already-covered passes), so a partly-completed task is judged on
    the work that is actually left; the second value sizes the task to the
    residual area (work minus covered minus restricted) over the full work area.
    Without usable work-area geometry it falls back to the planar site-polygon
    restriction fraction for both, matching the legacy scalar clip.
    """
    task_work_ring = parse_polygon(getattr(order, "work_area_geometry", None))
    covered_ring = parse_polygon(getattr(order, "covered_geometry", None))
    # Only tasks that carry their own work-area or covered geometry opt into the
    # geodesic residual clip; everything else keeps the legacy planar site-polygon
    # fraction so existing scalar-area behaviour is unchanged.
    if len(task_work_ring) < 3 and len(covered_ring) < 3:
        fraction = unrestricted_area_fraction(
            parse_polygon(getattr(site, "polygon", None)), overlapping
        )
        return fraction, fraction
    work_ring = (
        task_work_ring
        if len(task_work_ring) >= 3
        else parse_polygon(getattr(site, "polygon", None))
    )
    if len(work_ring) < 3:
        return 1.0, 1.0
    work_area = polygon_rings_area_km2([work_ring])
    if work_area <= 0:
        return 1.0, 1.0
    covered_rings = [covered_ring] if len(covered_ring) >= 3 else []
    uncovered_area = polygon_difference_area_km2(work_ring, covered_rings)
    residual_area = polygon_difference_area_km2(work_ring, [*overlapping, *covered_rings])
    restriction_fraction = (
        residual_area / uncovered_area if uncovered_area > 0 else 0.0
    )
    area_fraction = residual_area / work_area
    return min(1.0, restriction_fraction), max(0.0, min(1.0, area_fraction))


def _clip_order_to_area_fraction(order: Any, fraction: float) -> Any:
    """Scale an order's effective work down to the unrestricted area fraction.

    Area always scales; the generic work quantity scales only when it is
    area-like ("" / "ha"), since a volume or item count is not reduced by part
    of the field being off-limits. Revenue scales with the area too: only the
    unrestricted fraction of the field can be served, so the order earns that
    fraction of its value and the objective no longer over-credits a clipped
    task. An explicit service-duration override is left untouched (it already
    wins over any quantity estimate).
    """
    new_area = round(float(order.area or 0.0) * fraction, 6)
    new_quantity = order.work_quantity
    unit = str(order.work_quantity_unit or "").strip().lower()
    if float(order.work_quantity or 0.0) > 0 and unit in ("", "ha"):
        new_quantity = round(float(order.work_quantity) * fraction, 6)
    new_revenue = round(float(order.revenue or 0.0) * fraction, 6)
    return dataclasses.replace(
        order, area=new_area, work_quantity=new_quantity, revenue=new_revenue
    )


def apply_location_restrictions(
    orders: list[Any],
    sites: list[Any],
    now: datetime,
) -> tuple[list[Any], list[dict[str, Any]]]:
    """Split off tasks blocked by their location's declared restrictions.

    A task is excluded when its operation type is prohibited at its location
    (restricted zone), when a restricted polygon covers effectively all of its
    work area, or when the location's restriction windows block every admissible
    start in [now, deadline] (time-restricted area). When a restricted area
    covers only part of the site, the task is kept with its work area clipped to
    the unrestricted fraction (partial-overlap severity) rather than dropped.
    """
    site_map = {s.location_id: s for s in sites}
    now_epoch = int(now.timestamp())
    kept: list[Any] = []
    infeasible: list[dict[str, Any]] = []
    clipped = 0
    for order in orders:
        site = site_map.get(order.location_ref)
        if site is None:
            kept.append(order)
            continue
        prohibited = ops_set(site.restricted_operations)
        if order.operation_type in prohibited:
            infeasible.append(
                {
                    "task_id": order.task_id,
                    "cluster_id": "",
                    "reason_code": ReasonCode.RESTRICTED_ZONE.value,
                    "detail": (
                        f"operation {order.operation_type} prohibited at "
                        f"{order.location_ref}"
                    ),
                }
            )
            continue
        overlapping, point_block_id = _restricted_overlap(order, site, sites)
        if point_block_id is not None:
            infeasible.append(
                {
                    "task_id": order.task_id,
                    "cluster_id": "",
                    "reason_code": ReasonCode.RESTRICTED_ZONE.value,
                    "detail": (
                        f"operation {order.operation_type} at {order.location_ref} "
                        f"intersects restricted area {point_block_id}"
                    ),
                }
            )
            continue
        has_covered = len(parse_polygon(getattr(order, "covered_geometry", None))) >= 3
        if overlapping or has_covered:
            restriction_fraction, area_fraction = _residual_clip_fractions(
                order, site, overlapping
            )
            if restriction_fraction <= RESTRICTION_MIN_WORKABLE_AREA_FRACTION:
                infeasible.append(
                    {
                        "task_id": order.task_id,
                        "cluster_id": "",
                        "reason_code": ReasonCode.RESTRICTED_ZONE.value,
                        "detail": (
                            f"operation {order.operation_type} at "
                            f"{order.location_ref} restricted over "
                            f"{(1 - restriction_fraction) * 100:.0f}% of the "
                            "uncovered work area"
                        ),
                    }
                )
                continue
            if area_fraction < 1.0:
                order = _clip_order_to_area_fraction(order, area_fraction)
                clipped += 1
        if not allowed_start_intervals(
            order, site, now_epoch, _deadline_epoch(order, now_epoch)
        ):
            infeasible.append(
                {
                    "task_id": order.task_id,
                    "cluster_id": "",
                    "reason_code": ReasonCode.RESTRICTED_ZONE.value,
                    "detail": (
                        f"restriction windows at {order.location_ref} block "
                        "every admissible start before the deadline"
                    ),
                }
            )
            continue
        kept.append(order)
    if infeasible or clipped:
        logger.info(
            "Location restrictions excluded %d tasks, clipped %d work areas",
            len(infeasible),
            clipped,
        )
    return kept, infeasible
