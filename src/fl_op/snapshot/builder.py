"""Planning-snapshot builder.

Loads governed source datasets, maps them into canonical objects, generates
operational bundles, projects the solver-payload bridge, and emits an immutable,
reproducibly-hashed PlanningSnapshot. Adapters consume only the snapshot, never
raw source data.
"""

import json
import logging
import pathlib
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fl_op.canonical.common import (
    QualityFinding,
    QualitySummary,
    TimeInterval,
    VersionDimensions,
)
from fl_op.canonical.enums import PlanningMode, QualitySeverity
from fl_op.canonical.snapshot import PlanningSnapshot
from fl_op.core.constants import (
    ADAPTER_COMPATIBILITY_VERSION,
    INTEGER_SCALING_POLICY_VERSION,
    MAPPING_VERSION,
    OBSERVATION_ERROR_RATE_ALERT,
    OPTIMIZATION_PROFILE_VERSION,
    PERIODIC_HORIZON_DAYS,
    ROLLING_HORIZON_HOURS,
    SNAPSHOT_INPUT_ENTITIES,
)
from fl_op.contracts.profile import MonitoringPolicySpec
from fl_op.contracts.registry import FileRegistry
from fl_op.io import detect_format, get_codec, locate_source
from fl_op.mapping.engine import MappingEngine, MappingResult
from fl_op.snapshot.assessment import assess_observations
from fl_op.snapshot.bundles import summarize_bundles
from fl_op.snapshot.hashing import compute_snapshot_hash
from fl_op.snapshot.monitoring import derive_service_tasks
from fl_op.snapshot.quality_trend import degrading_sources, record_error_rates

logger = logging.getLogger(__name__)


def mapped_contract_ids(registry: FileRegistry) -> list[str]:
    """Contracts the snapshot builder maps, derived from the registry.

    Every active-domain contract whose mapping targets a snapshot-input
    canonical entity participates, in registry declaration order. Domains add
    or drop datasets by editing the registry only; no engine change is needed.
    """
    active = registry.active_domain
    ids: list[str] = []
    for cid in registry.list_contracts():
        entry = registry.get_entry(cid)
        if active and entry.domain != active:
            continue
        if not entry.mapping_ref:
            continue
        mapping = registry.get_mapping(cid)
        if mapping is not None and mapping.canonical_entity in SNAPSHOT_INPUT_ENTITIES:
            ids.append(cid)
    return ids


def _load_source(
    data_dir: pathlib.Path,
    source_file: str,
    registry_format: str,
    codec: Any,
) -> list[dict[str, Any]]:
    if registry_format in ("json", "jsonl"):
        path = data_dir / source_file
        if not path.exists():
            return []
        text = path.read_text()
        if registry_format == "jsonl":
            return [json.loads(line) for line in text.splitlines() if line.strip()]
        data = json.loads(text)
        return data if isinstance(data, list) else data.get("windows", [])
    return codec.read(locate_source(data_dir, source_file, codec))


class SnapshotBuilder:
    """Builds immutable planning snapshots from a source data directory."""

    def __init__(self, registry: Optional[FileRegistry] = None) -> None:
        self.registry = registry or FileRegistry()
        self.engine = MappingEngine(self.registry)
        self.mapped_contracts = mapped_contract_ids(self.registry)
        profile_id = self.registry.active_profile_id
        profile_policy = (
            self.registry.get_profile(profile_id).monitoring
            if profile_id
            else MonitoringPolicySpec()
        )
        # The guarded auto-tuning overlay layers on the reviewed profile
        # policy; deleting the overlay file reverts to the profile as is.
        from fl_op.snapshot.policy_tuning import (
            apply_tuned_overrides,
            load_tuned_overrides,
        )

        self.monitoring_policy = apply_tuned_overrides(
            profile_policy, load_tuned_overrides()
        )

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
        profile_id = self.registry.active_profile_id or ""
        return VersionDimensions(
            contract_versions=contract_versions,
            avro_schema_versions=avro_versions,
            mapping_versions={profile_id: MAPPING_VERSION},
            optimization_profile_version=OPTIMIZATION_PROFILE_VERSION,
            adapter_compatibility_version=ADAPTER_COMPATIBILITY_VERSION,
            integer_scaling_policy_version=INTEGER_SCALING_POLICY_VERSION,
        )

    def load_sources(self, data_dir: str | pathlib.Path) -> dict[str, list[dict[str, Any]]]:
        """Load every mapped contract's source rows from a data directory."""
        data_path = pathlib.Path(data_dir)
        codec = get_codec(detect_format(data_path))
        sources: dict[str, list[dict[str, Any]]] = {}
        for cid in self.mapped_contracts:
            entry = self.registry.get_entry(cid)
            sources[cid] = _load_source(data_path, entry.source_file or "", entry.source_format, codec)
        return sources

    def missing_source_files(self, data_dir: str | pathlib.Path) -> list[tuple[str, str]]:
        """Mapped contracts whose declared source file is absent from data_dir.

        Returns (contract_id, expected_path_name) pairs; the snapshot records a
        warning finding per missing dataset so an incomplete entity set is
        visible instead of silent.
        """
        data_path = pathlib.Path(data_dir)
        codec = get_codec(detect_format(data_path))
        missing: list[tuple[str, str]] = []
        for cid in self.mapped_contracts:
            entry = self.registry.get_entry(cid)
            source_file = entry.source_file or ""
            if entry.source_format in ("json", "jsonl"):
                path = data_path / source_file
            else:
                path = locate_source(data_path, source_file, codec)
            if not path.exists():
                missing.append((cid, path.name))
        return missing

    def build(
        self,
        data_dir: str | pathlib.Path,
        planning_mode: PlanningMode = PlanningMode.PERIODIC,
        effective_at: Optional[datetime] = None,
    ) -> PlanningSnapshot:
        sources = self.load_sources(data_dir)
        snapshot = self.build_from_sources(
            sources,
            planning_mode,
            effective_at,
            lineage_ref=f"source://{data_dir}",
            missing_sources=self.missing_source_files(data_dir),
        )
        # Cross-run quality trending: dataset builds (not per-event rolling
        # rebuilds) append their error rates and report degrading sources.
        record_error_rates(snapshot)
        for contract_id, rates in degrading_sources().items():
            logger.warning(
                "Observation source %s degrading across runs: %s",
                contract_id,
                [f"{rate:.3f}" for rate in rates],
            )
        return snapshot

    def build_from_sources(
        self,
        sources: dict[str, list[dict[str, Any]]],
        planning_mode: PlanningMode = PlanningMode.PERIODIC,
        effective_at: Optional[datetime] = None,
        lineage_ref: str = "source://in-memory",
        missing_sources: Optional[list[tuple[str, str]]] = None,
        source_watermarks: Optional[dict[str, datetime]] = None,
    ) -> PlanningSnapshot:
        """Build a snapshot from already-loaded (and possibly event-mutated) rows.

        ``source_watermarks`` carries the caller's visibility horizons for
        event-mutated sources (task/asset/location/forecast contracts); they
        merge with the observation watermarks derived from the readings, the
        newest time winning per contract.
        """
        effective_at = effective_at or datetime.now(tz=timezone.utc)
        generated_at = datetime.now(tz=timezone.utc)

        result = MappingResult()
        for cid in self.mapped_contracts:
            self.engine.map_dataset(cid, sources.get(cid, []), result)
        result.findings.extend(
            _missing_source_findings(missing_sources or [], generated_at)
        )
        # Statistical assessment: bound series by the retention window, exclude
        # outlier and source-flagged readings, floor the confidence of
        # fault-suspected series, and detect metric drift before any monitoring
        # decision is taken.
        assessment = assess_observations(
            result.observations, generated_at, as_of=effective_at
        )
        result.findings.extend(assessment.findings)
        for contract_id, rate in assessment.error_rates.items():
            if rate > OBSERVATION_ERROR_RATE_ALERT:
                logger.warning(
                    "Observation source %s degraded: %.0f%% bad readings",
                    contract_id,
                    rate * 100.0,
                )

        # Monitoring policy: stationary equipment with low or soon-depleted
        # battery, degraded health, an overdue service interval, or a drifting
        # metric yields canonical service tasks scheduled alongside ordered work.
        service_tasks = derive_service_tasks(
            result.assets,
            assessment.observations,
            effective_at,
            self.monitoring_policy,
            calibration_needs=assessment.drifting_metrics,
        )
        tasks = result.tasks + service_tasks

        # Summarized after monitoring so the demand side reflects the actual
        # order book, including derived service tasks.
        bundle_summary = summarize_bundles(result.assets, tasks)

        if planning_mode == PlanningMode.ROLLING:
            horizon_to = effective_at + timedelta(hours=ROLLING_HORIZON_HOURS)
        else:
            horizon_to = effective_at + timedelta(days=PERIODIC_HORIZON_DAYS)

        quality_summary = QualitySummary(
            n_findings=len(result.findings),
            by_severity=_count_by_severity(result.findings),
            n_entities_excluded=sum(len(v) for v in result.excluded.values()),
            observation_error_rates=assessment.error_rates,
        )

        watermarks = dict(source_watermarks or {})
        for contract_id, observed in assessment.source_watermarks.items():
            current = watermarks.get(contract_id)
            if current is None or observed > current:
                watermarks[contract_id] = observed

        base = PlanningSnapshot(
            snapshot_id="pending",
            effective_at=effective_at,
            generated_at=generated_at,
            planning_mode=planning_mode,
            planning_horizon=TimeInterval(**{"from": effective_at, "to": horizon_to}),
            version_dimensions=self._version_dimensions(),
            assets=result.assets,
            locations=result.locations,
            bundle_summary=bundle_summary,
            tasks=tasks,
            inventory=result.inventory,
            forecasts=result.forecasts,
            observations=assessment.observations,
            commitments=result.commitments,
            travel_links=result.travel_links,
            cost_rates=result.cost_rates,
            source_watermarks=watermarks,
            quality_findings=result.findings,
            quality_summary=quality_summary,
            lineage_ref=lineage_ref,
        )

        snapshot_hash = compute_snapshot_hash(base.canonical_content())
        snapshot_id = f"snap-{planning_mode.value}-{generated_at.strftime('%Y%m%dT%H%M%S')}-{snapshot_hash[:8]}"

        snapshot = base.model_copy(
            update={
                "snapshot_id": snapshot_id,
                "snapshot_hash": snapshot_hash,
            }
        )
        logger.info(
            "Built %s snapshot %s (hash %s): %d assets, %d tasks, %d feasible bundle pairs",
            planning_mode.value,
            snapshot_id,
            snapshot_hash[:12],
            len(snapshot.assets),
            len(snapshot.tasks),
            snapshot.bundle_summary.n_feasible_pairs,
        )
        return snapshot


def _count_by_severity(findings: list) -> dict[str, int]:
    counts: dict[str, int] = {}
    for f in findings:
        counts[f.severity.value] = counts.get(f.severity.value, 0) + 1
    return counts


def _missing_source_findings(
    missing_sources: list[tuple[str, str]], detected_at: datetime
) -> list[QualityFinding]:
    """One warning finding per mapped contract whose source dataset is absent."""
    return [
        QualityFinding(
            quality_finding_id=f"qf-dataset-{contract_id}",
            rule_id="dq://dataset/source-file-missing",
            severity=QualitySeverity.WARNING,
            entity_ref=contract_id,
            field_ref=file_name,
            detected_at=detected_at,
            action_applied="dataset-missing",
            planning_impact="contract dataset absent; its entity set is empty",
            source_ref=contract_id,
        )
        for contract_id, file_name in missing_sources
    ]
