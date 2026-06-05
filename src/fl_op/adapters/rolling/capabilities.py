"""Supported capabilities for the OR-Tools rolling adapter."""

SUPPORTED_CONSTRAINTS = {
    "compatible-equipment",
    "sufficient-power",
    "asset-available",
    "no-double-booking",
    "respect-contract-time-window",
    "protect-frozen-tasks",
}

SUPPORTED_FEATURES = {
    "rolling-dispatch",
    "freeze-window",
    "pinned-tasks",
    "plan-instability-penalty",
    "shared-resource-exclusivity",
}
