"""Planning-snapshot builder (spec 17.2).

Loads governed source datasets, maps them into canonical objects, generates
operational bundles, projects the solver-payload bridge, and emits an immutable,
reproducibly-hashed PlanningSnapshot. No adapter ever reads raw source data; it
reads only the snapshot (spec 4.3).
"""

import csv
import json
import logging
import pathlib
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fl_op.canonical.common import (
    QualitySummary,
    TimeInterval,
    VersionDimensions,
)
from fl_op.canonical.enums import PlanningMode
from fl_op.canonical.snapshot import PlanningSnapshot
from fl_op.core.constants import (
    ADAPTER_COMPATIBILITY_VERSION,
    INTEGER_SCALING_POLICY_VERSION,
    MAPPING_VERSION,
    OPTIMIZATION_PROFILE_VERSION,
    PERIODIC_HORIZON_DAYS,
    ROLLING_HORIZON_HOURS,
)
from fl_op.contracts.registry import FileRegistry
from fl_op.mapping.engine import MappingEngine, MappingResult
from fl_op.snapshot.bundles import generate_bundles
from fl_op.snapshot.hashing import compute_snapshot_hash
from fl_op.snapshot.payload import to_solver_rows

logger = logging.getLogger(__name__)

# Order in which datasets are mapped. Source vocabulary -> canonical entities.
_MAPPED_CONTRACTS = ["vehicles", "implements", "operators", "depots", "fields", "orders", "weather"]


def _load_source(data_dir: pathlib.Path, source_file: str, fmt: str) -> list[dict[str, Any]]:
    path = data_dir / source_file
    if not path.exists():
        return []
    if fmt == "csv":
        with path.open() as fh:
            return list(csv.DictReader(fh))
    if fmt in ("json", "jsonl"):
        text = path.read_text()
        if fmt == "jsonl":
            return [json.loads(line) for line in text.splitlines() if line.strip()]
        data = json.loads(text)
        return data if isinstance(data, list) else data.get("windows", [])
    return []


class SnapshotBuilder:
    """Builds immutable planning snapshots from a source data directory."""

    def __init__(self, registry: Optional[FileRegistry] = None) -> None:
        self.registry = registry or FileRegistry()
        self.engine = MappingEngine(self.registry)

    def _version_dimensions(self) -> VersionDimensions:
        contract_versions: dict[str, str] = {}
        avro_versions: dict[str, str] = {}
        for cid in self.registry.list_contracts():
            entry = self.registry.get_entry(cid)
            if entry.stored_fingerprints:
                avro_versions[cid] = entry.stored_fingerprints.get(
                    "optimizationMetadataHash", ""
                )[:12]
            contract_versions[cid] = "1.0.0"
        return VersionDimensions(
            contract_versions=contract_versions,
            avro_schema_versions=avro_versions,
            mapping_versions={"agricultural-custom-services": MAPPING_VERSION},
            optimization_profile_version=OPTIMIZATION_PROFILE_VERSION,
            adapter_compatibility_version=ADAPTER_COMPATIBILITY_VERSION,
            integer_scaling_policy_version=INTEGER_SCALING_POLICY_VERSION,
        )

    def load_sources(self, data_dir: str | pathlib.Path) -> dict[str, list[dict[str, Any]]]:
        """Load every mapped contract's source rows from a data directory."""
        data_path = pathlib.Path(data_dir)
        sources: dict[str, list[dict[str, Any]]] = {}
        for cid in _MAPPED_CONTRACTS:
            entry = self.registry.get_entry(cid)
            sources[cid] = _load_source(data_path, entry.source_file or "", entry.source_format)
        return sources

    def build(
        self,
        data_dir: str | pathlib.Path,
        planning_mode: PlanningMode = PlanningMode.PERIODIC,
        effective_at: Optional[datetime] = None,
    ) -> PlanningSnapshot:
        sources = self.load_sources(data_dir)
        return self.build_from_sources(
            sources, planning_mode, effective_at, lineage_ref=f"source://{data_dir}"
        )

    def build_from_sources(
        self,
        sources: dict[str, list[dict[str, Any]]],
        planning_mode: PlanningMode = PlanningMode.PERIODIC,
        effective_at: Optional[datetime] = None,
        lineage_ref: str = "source://in-memory",
    ) -> PlanningSnapshot:
        """Build a snapshot from already-loaded (and possibly event-mutated) rows."""
        effective_at = effective_at or datetime.now(tz=timezone.utc)
        generated_at = datetime.now(tz=timezone.utc)

        result = MappingResult()
        for cid in _MAPPED_CONTRACTS:
            self.engine.map_dataset(cid, sources.get(cid, []), result)
        bundles = generate_bundles(result.assets, configuration_version=MAPPING_VERSION)

        if planning_mode == PlanningMode.ROLLING:
            horizon_to = effective_at + timedelta(hours=ROLLING_HORIZON_HOURS)
        else:
            horizon_to = effective_at + timedelta(days=PERIODIC_HORIZON_DAYS)

        quality_summary = QualitySummary(
            n_findings=len(result.findings),
            by_severity=_count_by_severity(result.findings),
            n_entities_excluded=sum(len(v) for v in result.excluded.values()),
        )

        base = PlanningSnapshot(
            snapshot_id="pending",
            effective_at=effective_at,
            generated_at=generated_at,
            planning_mode=planning_mode,
            planning_horizon=TimeInterval(**{"from": effective_at, "to": horizon_to}),
            version_dimensions=self._version_dimensions(),
            assets=result.assets,
            locations=result.locations,
            bundles=bundles,
            tasks=result.tasks,
            inventory=result.inventory,
            forecasts=result.forecasts,
            quality_findings=result.findings,
            quality_summary=quality_summary,
            lineage_ref=lineage_ref,
        )

        snapshot_hash = compute_snapshot_hash(base.canonical_content())
        solver_payload = to_solver_rows(base, self.registry)
        snapshot_id = f"snap-{planning_mode.value}-{generated_at.strftime('%Y%m%dT%H%M%S')}-{snapshot_hash[:8]}"

        snapshot = base.model_copy(
            update={
                "snapshot_id": snapshot_id,
                "snapshot_hash": snapshot_hash,
                "solver_payload": solver_payload,
            }
        )
        logger.info(
            "Built %s snapshot %s (hash %s): %d assets, %d tasks, %d bundles",
            planning_mode.value,
            snapshot_id,
            snapshot_hash[:12],
            len(snapshot.assets),
            len(snapshot.tasks),
            len(snapshot.bundles),
        )
        return snapshot


def _count_by_severity(findings: list) -> dict[str, int]:
    counts: dict[str, int] = {}
    for f in findings:
        counts[f.severity.value] = counts.get(f.severity.value, 0) + 1
    return counts
