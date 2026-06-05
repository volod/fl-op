"""Contract-suite validation orchestration (spec 29.1).

Validates every registered contract:
  - Avro schema parses and round-trips with x-optimization metadata preserved;
  - dual fingerprints are computed;
  - ODCS bindings match the Avro bindings field-for-field;
  - the metadata-loss guard passes;
  - the optimization profile loads and its enforced constraints are known.

Returns a structured report; callers decide how to render or exit.
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

from fl_op.contracts.registry import FileRegistry, MetadataLossError

logger = logging.getLogger(__name__)


@dataclass
class ContractReport:
    contract_id: str
    avro_name: str
    n_bindings: int
    avro_parsing_fingerprint: str
    optimization_metadata_hash: str
    roundtrip_preserved: bool
    odcs_matches_avro: bool
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.roundtrip_preserved and self.odcs_matches_avro and not self.errors


@dataclass
class SuiteReport:
    contracts: list[ContractReport] = field(default_factory=list)
    profile_id: Optional[str] = None
    profile_ok: bool = False
    profile_errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.contracts) and self.profile_ok


def _roundtrip_preserved(registry: FileRegistry, contract_id: str) -> bool:
    """True if every x-optimization block survives a fastavro parse round-trip."""
    from fl_op.contracts.fingerprint import collect_xopt_blocks

    avro = registry.get_avro(contract_id)
    before = collect_xopt_blocks(avro.schema_json)
    after = collect_xopt_blocks(avro.roundtrip_metadata())
    return before == after


def validate_contract(registry: FileRegistry, contract_id: str) -> ContractReport:
    avro = registry.get_avro(contract_id)
    fps = avro.fingerprints
    errors: list[str] = []

    try:
        roundtrip_ok = _roundtrip_preserved(registry, contract_id)
    except Exception as exc:  # noqa: BLE001 - surface any parse failure as an error
        roundtrip_ok = False
        errors.append(f"roundtrip failed: {exc}")

    odcs = registry.get_odcs(contract_id)
    if odcs is None:
        odcs_ok = True
    else:
        avro_map = {b.source_field: b.binding for b in avro.bindings}
        odcs_map = odcs.binding_map()
        odcs_ok = avro_map == odcs_map
        if not odcs_ok:
            mismatched = {
                k for k in set(avro_map) | set(odcs_map)
                if avro_map.get(k) != odcs_map.get(k)
            }
            errors.append(f"ODCS/Avro binding mismatch on: {sorted(mismatched)}")

    try:
        registry.verify_no_metadata_loss(contract_id)
    except MetadataLossError as exc:
        errors.append(str(exc))

    return ContractReport(
        contract_id=contract_id,
        avro_name=avro.name,
        n_bindings=len(avro.bindings),
        avro_parsing_fingerprint=fps["avroParsingFingerprint"],
        optimization_metadata_hash=fps["optimizationMetadataHash"],
        roundtrip_preserved=roundtrip_ok,
        odcs_matches_avro=odcs_ok,
        errors=errors,
    )


def validate_suite(
    registry: FileRegistry | None = None,
    profile_id: str = "agricultural-custom-services",
) -> SuiteReport:
    """Validate all registered contracts and the named optimization profile."""
    registry = registry or FileRegistry()
    report = SuiteReport(profile_id=profile_id)

    for contract_id in registry.list_contracts():
        report.contracts.append(validate_contract(registry, contract_id))

    try:
        profile = registry.get_profile(profile_id)
        # Every input contract referenced by the profile must be registered.
        known = set(registry.list_contracts())
        missing = [c for c in profile.inputContracts if c not in known]
        if missing:
            report.profile_errors.append(f"profile references unknown contracts: {missing}")
        report.profile_ok = not report.profile_errors
    except Exception as exc:  # noqa: BLE001
        report.profile_errors.append(str(exc))
        report.profile_ok = False

    return report
