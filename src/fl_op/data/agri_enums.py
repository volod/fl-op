"""Agricultural domain enumerations used by the synthetic data generator.

These are physical-domain vocabularies (operation, implement, and vehicle types)
for the agricultural sample dataset. They are intentionally confined to the data
generator: the optimization engine treats operation types as opaque strings and
never imports a domain-specific enum.
"""

from enum import Enum


class OperationType(str, Enum):
    SPRAYING = "SPRAYING"
    TILLAGE = "TILLAGE"
    SEEDING = "SEEDING"
    HARVESTING = "HARVESTING"
    FERTILIZING = "FERTILIZING"


class ImplementType(str, Enum):
    SPRAYER = "SPRAYER"
    PLOW = "PLOW"
    DISK_HARROW = "DISK_HARROW"
    SEEDER = "SEEDER"
    COMBINE_HEADER = "COMBINE_HEADER"
    FERTILIZER_SPREADER = "FERTILIZER_SPREADER"


class VehicleType(str, Enum):
    TRACTOR = "TRACTOR"
    COMBINE = "COMBINE"
    SPRAYER_SELF_PROPELLED = "SPRAYER_SELF_PROPELLED"
