"""Schema-evolution policy: versioned ODCS baselines with compatibility checks.

Every ODCS contract (registered domain contracts plus the canonical entity and
plan contracts) has a committed baseline snapshot under
``contracts/evolution/<contract_id>.json`` recording its version and physical
field schema. New snapshots also carry semantic fingerprints and a migration
``history``. The evolution check validates every adjacent history pair plus the
current contract, so consumers more than one version behind get an upgrade path
instead of a single last-reviewed comparison. The current contract is classified
against the latest reviewed snapshot and the version-bump policy is enforced:

- identical schema: the version may stay or move forward (doc-only edits are
  patch-level by construction: descriptions are not part of the snapshot);
- backward-compatible change (only added optional fields): at least a minor
  version bump over the baseline is required;
- breaking change (removed fields, type changes, requiredness changes, added
  required fields): a major version bump over the baseline is required.

A schema change without any version bump always fails. Registered contract
metadata drift (``optimizationMetadataHash``) is checked in the same review
flow: a mapping-semantic change fails until the reviewed snapshot/fingerprints
are refreshed. A contract without a committed baseline fails too, so CI cannot
silently start covering a new contract without a reviewed baseline;
``fl-op contracts evolution-freeze`` records baselines after review.
"""

import json
import logging
import pathlib
from dataclasses import dataclass, field
from typing import Any, Optional

from fl_op.contracts.fingerprint import mapping_metadata_hash
from fl_op.contracts.mapping_loader import mapping_metadata_blocks
from fl_op.contracts.odcs_loader import OdcsContract, load_odcs_contract
from fl_op.contracts.registry import FileRegistry

logger = logging.getLogger(__name__)

# Committed baseline snapshots live here, one JSON document per contract id.
# Registered domain contract ids and canonical contract ids never collide:
# canonical contracts are 'canonical-' prefixed.
EVOLUTION_DIRNAME = "evolution"

# Change classes, ordered by the strictness of the required version bump.
CHANGE_IDENTICAL = "identical"
CHANGE_BACKWARD = "backward"
CHANGE_BREAKING = "breaking"


@dataclass
class ChangeReport:
    """Classification of a contract's schema relative to its baseline."""

    change_class: str
    details: list[str] = field(default_factory=list)


@dataclass
class ContractEvolution:
    """Evolution-check outcome for one contract."""

    contract_id: str
    baseline_version: Optional[str]
    current_version: str
    change_class: str
    details: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    history_versions: list[str] = field(default_factory=list)
    optimization_metadata_hash: str = ""

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass
class EvolutionReport:
    contracts: list[ContractEvolution] = field(default_factory=list)
    stale_baselines: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.contracts) and not self.stale_baselines


def schema_snapshot(
    odcs: OdcsContract,
    contract_id: Optional[str] = None,
    fingerprints: Optional[dict[str, str]] = None,
) -> dict[str, Any]:
    """Extract the version-relevant physical schema of an ODCS contract.

    Only field names, types, and requiredness participate: description and
    generation-hint edits are deliberately invisible to the policy (patch
    level). ``contract_id`` is the baseline key; for registered contracts it
    is the registry id, which is globally unique while ODCS document ids are
    not (the construction operator master's document id is also 'operators').
    """
    fields: dict[str, dict[str, Any]] = {}
    for schema_obj in odcs.doc.get("schema", []):
        if not isinstance(schema_obj, dict):
            continue
        for prop in schema_obj.get("properties", []):
            if not isinstance(prop, dict) or "name" not in prop:
                continue
            fields[prop["name"]] = {
                "logicalType": prop.get("logicalType", ""),
                "physicalType": prop.get("physicalType", ""),
                "required": bool(prop.get("required", False)),
            }
    snapshot = {
        "contractId": contract_id or odcs.id,
        "odcsId": odcs.id,
        "version": odcs.version,
        "fields": fields,
    }
    if fingerprints:
        snapshot["fingerprints"] = {
            k: v for k, v in sorted(fingerprints.items()) if v
        }
    return snapshot


def classify_change(
    baseline_fields: dict[str, dict[str, Any]],
    current_fields: dict[str, dict[str, Any]],
) -> ChangeReport:
    """Classify the field-level delta between a baseline and the current schema.

    Requiredness changes are breaking in both directions: optional -> required
    breaks existing producers, required -> optional breaks consumers that rely
    on the field being present.
    """
    details: list[str] = []
    breaking = False
    backward = False

    for name, spec in baseline_fields.items():
        current = current_fields.get(name)
        if current is None:
            details.append(f"removed field '{name}'")
            breaking = True
            continue
        for type_key in ("logicalType", "physicalType"):
            if current.get(type_key, "") != spec.get(type_key, ""):
                details.append(
                    f"field '{name}' {type_key} changed "
                    f"'{spec.get(type_key, '')}' -> '{current.get(type_key, '')}'"
                )
                breaking = True
        if bool(current.get("required")) != bool(spec.get("required")):
            details.append(
                f"field '{name}' required changed "
                f"{bool(spec.get('required'))} -> {bool(current.get('required'))}"
            )
            breaking = True

    for name, current in current_fields.items():
        if name in baseline_fields:
            continue
        if current.get("required"):
            details.append(f"added required field '{name}'")
            breaking = True
        else:
            details.append(f"added optional field '{name}'")
            backward = True

    if breaking:
        return ChangeReport(CHANGE_BREAKING, details)
    if backward:
        return ChangeReport(CHANGE_BACKWARD, details)
    return ChangeReport(CHANGE_IDENTICAL, details)


def _parse_version(text: str) -> tuple[int, int, int]:
    """Parse a semver-ish version into (major, minor, patch); missing parts are 0."""
    nums: list[int] = []
    for part in (text or "").split(".")[:3]:
        digits = "".join(ch for ch in part if ch.isdigit())
        nums.append(int(digits) if digits else 0)
    while len(nums) < 3:
        nums.append(0)
    return (nums[0], nums[1], nums[2])


def version_policy_errors(
    contract_id: str,
    baseline_version: str,
    current_version: str,
    change: ChangeReport,
) -> list[str]:
    """Enforce the bump policy for one contract; returns human-readable errors."""
    old = _parse_version(baseline_version)
    new = _parse_version(current_version)

    if change.change_class == CHANGE_IDENTICAL:
        if new < old:
            return [
                f"{contract_id}: version went backwards "
                f"({baseline_version} -> {current_version})"
            ]
        return []

    if new <= old:
        return [
            f"{contract_id}: schema changed ({change.change_class}) but the "
            f"contract version was not bumped "
            f"({baseline_version} -> {current_version}); changes: {change.details}"
        ]
    if change.change_class == CHANGE_BREAKING and new[0] <= old[0]:
        return [
            f"{contract_id}: breaking schema change requires a major version "
            f"bump ({baseline_version} -> {current_version}); "
            f"changes: {change.details}"
        ]
    if (
        change.change_class == CHANGE_BACKWARD
        and new[0] == old[0]
        and new[1] <= old[1]
    ):
        return [
            f"{contract_id}: backward-compatible schema change requires at "
            f"least a minor version bump "
            f"({baseline_version} -> {current_version}); "
            f"changes: {change.details}"
        ]
    return []


def _iter_contracts(registry: FileRegistry) -> list[tuple[str, OdcsContract]]:
    """All (baseline id, contract) pairs under the policy.

    Registered domain contracts are keyed by registry id (globally unique);
    canonical contracts (including the plan output contract) by their
    'canonical-' prefixed document id.
    """
    contracts: list[tuple[str, OdcsContract]] = []
    for contract_id in registry.list_contracts():
        odcs = registry.get_odcs(contract_id)
        if odcs is not None:
            contracts.append((contract_id, odcs))
    canonical_dir = registry.root / "canonical" / "odcs"
    for path in sorted(canonical_dir.glob("*.odcs.yaml")):
        odcs = load_odcs_contract(path)
        contracts.append((odcs.id, odcs))
    return contracts


def _baselines_dir(registry: FileRegistry) -> pathlib.Path:
    return registry.root / EVOLUTION_DIRNAME


def baseline_path(registry: FileRegistry, contract_id: str) -> pathlib.Path:
    return _baselines_dir(registry) / f"{contract_id}.json"


def load_baseline(
    registry: FileRegistry, contract_id: str
) -> Optional[dict[str, Any]]:
    path = baseline_path(registry, contract_id)
    if not path.exists():
        return None
    return json.loads(path.read_text())


def _snapshot_fingerprints(snapshot: dict[str, Any]) -> dict[str, str]:
    fps = snapshot.get("fingerprints")
    return fps if isinstance(fps, dict) else {}


def baseline_history(document: dict[str, Any]) -> list[dict[str, Any]]:
    """Return reviewed snapshots from a baseline document.

    Flat pre-history baselines are treated as a one-snapshot history; freeze
    writes the latest snapshot at the top level plus the full ``history`` list
    for compatibility with older readers.
    """
    raw = document.get("history")
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    return [document] if document else []


def _current_snapshot(
    registry: FileRegistry,
    contract_id: str,
    odcs: OdcsContract,
) -> dict[str, Any]:
    fingerprints: dict[str, str] = {}
    if contract_id in registry.entries:
        fingerprints = _evolution_fingerprints(registry, contract_id)
    return schema_snapshot(odcs, contract_id, fingerprints=fingerprints)


def _evolution_fingerprints(
    registry: FileRegistry,
    contract_id: str,
) -> dict[str, str]:
    """Fingerprints needed by evolution checks, tolerant of missing generated Avro.

    Schema evolution compares ODCS fields directly, so generated Avro artifacts
    are optional here. Temp contract-tree tests intentionally omit them.
    """
    entry = registry.get_entry(contract_id)
    fps: dict[str, str] = {}
    if entry.avro_ref and (registry.root / entry.avro_ref).exists():
        fps["avroParsingFingerprint"] = registry.get_avro(
            contract_id
        ).avro_parsing_fingerprint
    if entry.mapping_ref:
        mapping_doc = mapping_metadata_blocks(registry.root / entry.mapping_ref)
        fps["optimizationMetadataHash"] = mapping_metadata_hash(mapping_doc)
    return fps


def _metadata_drift_errors(
    contract_id: str,
    reviewed: dict[str, Any],
    current: dict[str, Any],
) -> list[str]:
    reviewed_hash = _snapshot_fingerprints(reviewed).get("optimizationMetadataHash")
    current_hash = _snapshot_fingerprints(current).get("optimizationMetadataHash")
    if reviewed_hash and current_hash and reviewed_hash != current_hash:
        return [
            f"{contract_id}: optimization metadata changed "
            f"({reviewed_hash} -> {current_hash}); review the mapping semantics "
            "and record a new evolution snapshot"
        ]
    return []


def _pairwise_history_errors(contract_id: str, history: list[dict[str, Any]]) -> list[str]:
    """Validate every adjacent reviewed migration step in history."""
    errors: list[str] = []
    for prev, nxt in zip(history, history[1:]):
        change = classify_change(prev.get("fields") or {}, nxt.get("fields") or {})
        errors.extend(
            version_policy_errors(
                contract_id,
                prev.get("version", ""),
                nxt.get("version", ""),
                change,
            )
        )
        errors.extend(_metadata_drift_errors(contract_id, prev, nxt))
    return errors


def _merge_history(
    existing: Optional[dict[str, Any]],
    snapshot: dict[str, Any],
) -> list[dict[str, Any]]:
    history = baseline_history(existing or {})
    comparable = {
        key: snapshot.get(key)
        for key in ("version", "fields", "fingerprints", "contractId", "odcsId")
    }
    if history:
        latest = history[-1]
        latest_comparable = {
            key: latest.get(key)
            for key in ("version", "fields", "fingerprints", "contractId", "odcsId")
        }
        if latest_comparable == comparable:
            return history
    return [*history, snapshot]


def check_evolution(registry: Optional[FileRegistry] = None) -> EvolutionReport:
    """Check every contract against its committed baseline snapshot."""
    registry = registry or FileRegistry()
    report = EvolutionReport()
    seen: set[str] = set()

    for contract_id, odcs in _iter_contracts(registry):
        current = _current_snapshot(registry, contract_id, odcs)
        seen.add(contract_id)
        baseline = load_baseline(registry, contract_id)
        if baseline is None:
            report.contracts.append(
                ContractEvolution(
                    contract_id=contract_id,
                    baseline_version=None,
                    current_version=current["version"],
                    change_class=CHANGE_BREAKING,
                    errors=[
                        f"{contract_id}: no committed schema baseline; review "
                        "the contract and record one with "
                        "'fl-op contracts evolution-freeze'"
                    ],
                )
            )
            continue
        history = baseline_history(baseline)
        if not history:
            report.contracts.append(
                ContractEvolution(
                    contract_id=contract_id,
                    baseline_version=None,
                    current_version=current["version"],
                    change_class=CHANGE_BREAKING,
                    errors=[
                        f"{contract_id}: committed schema baseline has no history "
                        "entries; rerun 'fl-op contracts evolution-freeze'"
                    ],
                )
            )
            continue
        latest = history[-1]
        change = classify_change(latest.get("fields") or {}, current["fields"])
        errors = version_policy_errors(
            contract_id, latest.get("version", ""), current["version"], change
        )
        errors.extend(_metadata_drift_errors(contract_id, latest, current))
        errors.extend(_pairwise_history_errors(contract_id, history))
        if contract_id in registry.entries:
            stored_hash = registry.get_entry(contract_id).stored_fingerprints.get(
                "optimizationMetadataHash"
            )
            current_hash = _snapshot_fingerprints(current).get(
                "optimizationMetadataHash"
            )
            if stored_hash and current_hash and stored_hash != current_hash:
                errors.append(
                    f"{contract_id}: optimizationMetadataHash changed from "
                    f"{stored_hash} to {current_hash}; rerun validation with "
                    "--write after reviewing the change"
                )
        report.contracts.append(
            ContractEvolution(
                contract_id=contract_id,
                baseline_version=latest.get("version", ""),
                current_version=current["version"],
                change_class=change.change_class,
                details=change.details,
                errors=errors,
                history_versions=[str(item.get("version", "")) for item in history],
                optimization_metadata_hash=_snapshot_fingerprints(current).get(
                    "optimizationMetadataHash", ""
                ),
            )
        )

    baselines = _baselines_dir(registry)
    if baselines.exists():
        for path in sorted(baselines.glob("*.json")):
            if path.stem not in seen:
                report.stale_baselines.append(
                    f"baseline {path.name} has no matching contract; remove it "
                    "after review (or rerun 'fl-op contracts evolution-freeze')"
                )
    return report


def freeze_baselines(registry: Optional[FileRegistry] = None) -> list[pathlib.Path]:
    """Record/refresh baseline snapshots for all contracts; prune stale ones.

    This is the explicit review acknowledgment: run it after a contract change
    has been reviewed and its version bumped according to the policy.
    """
    registry = registry or FileRegistry()
    baselines = _baselines_dir(registry)
    baselines.mkdir(parents=True, exist_ok=True)

    written: list[pathlib.Path] = []
    seen: set[str] = set()
    for contract_id, odcs in _iter_contracts(registry):
        snapshot = _current_snapshot(registry, contract_id, odcs)
        seen.add(contract_id)
        path = baseline_path(registry, contract_id)
        existing = load_baseline(registry, contract_id)
        history = _merge_history(existing, snapshot)
        payload = {**snapshot, "history": history}
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        written.append(path)

    for path in sorted(baselines.glob("*.json")):
        if path.stem not in seen:
            path.unlink()
            logger.info("Removed stale baseline %s", path.name)

    logger.info("Recorded %d schema baselines under %s", len(written), baselines)
    return written
