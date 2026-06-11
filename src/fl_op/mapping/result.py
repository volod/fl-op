"""Mapping result container."""

from dataclasses import dataclass, field

from fl_op.canonical.asset import Asset
from fl_op.canonical.commitment import Commitment, InventoryPosition
from fl_op.canonical.common import QualityFinding
from fl_op.canonical.cost import CostRate
from fl_op.canonical.forecast import Forecast
from fl_op.canonical.location import Location
from fl_op.canonical.observation import Observation
from fl_op.canonical.task import Task
from fl_op.canonical.travel import TravelLink


@dataclass
class MappingResult:
    """Canonical objects and quality findings produced from source datasets."""

    assets: list[Asset] = field(default_factory=list)
    locations: list[Location] = field(default_factory=list)
    tasks: list[Task] = field(default_factory=list)
    forecasts: list[Forecast] = field(default_factory=list)
    observations: list[Observation] = field(default_factory=list)
    commitments: list[Commitment] = field(default_factory=list)
    inventory: list[InventoryPosition] = field(default_factory=list)
    travel_links: list[TravelLink] = field(default_factory=list)
    cost_rates: list[CostRate] = field(default_factory=list)
    findings: list[QualityFinding] = field(default_factory=list)
    # Entity ids excluded by quality policy, keyed by contract id.
    excluded: dict[str, list[str]] = field(default_factory=dict)
