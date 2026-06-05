"""Resource reservation limits for cluster pre-allocation."""

import math
from typing import Any

from fl_op.core.constants import (
    MAX_PAIRS_PER_ORDER,
    PREALLOC_MIN_RESOURCES_PER_MULTI_ORDER_CLUSTER,
    PREALLOC_ORDERS_PER_RESOURCE,
)


def cluster_resource_limit(cluster_orders: list[dict[str, Any]]) -> int:
    """Return the maximum V-I bundles to reserve for one cluster."""
    desired_resources = math.ceil(len(cluster_orders) / PREALLOC_ORDERS_PER_RESOURCE)
    if len(cluster_orders) > 1:
        desired_resources = max(
            desired_resources,
            PREALLOC_MIN_RESOURCES_PER_MULTI_ORDER_CLUSTER,
        )
    return min(len(cluster_orders), MAX_PAIRS_PER_ORDER, desired_resources)
