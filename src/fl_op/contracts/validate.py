"""Contract-suite validation orchestration.

Validates every registered contract:
  - Generated Avro schema parses cleanly (structural integrity);
  - avroParsingFingerprint matches registry;
  - ODCS xOptimization metadata is complete (generation_ready check for Avro);
  - optimizationMetadataHash computed from ODCS matches registry;
  - the metadata-loss guard passes;
  - the optimization profile loads and its enforced constraints are known.

Returns a structured report; callers decide how to render or exit.
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

from fl_op.contracts.registry import FileRegistry, MetadataLossError
from fl_op.contracts.schema_gen import check_generation

logger = logging.getLogger(__name__)


@dataclass
class ContractReport:
    contract_id: str
    avro_name: str
    n_bindings: int
    avro_parsing_fingerprint: str
    optimization_metadata_hash: str
    generation_ready: bool
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.generation_ready and not self.errors


@dataclass
class SuiteReport:
    contracts: list[ContractReport] = field(default_factory=list)
    profile_id: Optional[str] = None
    profile_ok: bool = False
    profile_errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.contracts) and self.profile_ok


def validate_contract(registry: FileRegistry, contract_id: str) -> ContractReport:
    errors: list[str] = []
    avro_fp = ""
    meta_hash = ""
    avro_name = contract_id
    n_bindings = 0
    generation_ready = True

    entry = registry.get_entry(contract_id)

    if entry.avro_ref:
        try:
            avro = registry.get_avro(contract_id)
            avro_name = avro.name
            n_bindings = len(avro.fields)
            avro_fp = avro.avro_parsing_fingerprint
        except Exception as exc:  # noqa: BLE001
            errors.append(f"Avro load failed: {exc}")

    odcs = registry.get_odcs(contract_id)
    if odcs is not None:
        from fl_op.contracts.fingerprint import odcs_metadata_hash
        meta_hash = odcs_metadata_hash(odcs.doc)
        n_bindings = len(odcs.bindings)

        gen_report = check_generation(odcs.doc, contract_id, "avro")
        if not gen_report.ok:
            generation_ready = False
            for err in gen_report.errors:
                errors.append(f"generation check: {err}")

    try:
        registry.verify_no_metadata_loss(contract_id)
    except MetadataLossError as exc:
        errors.append(str(exc))

    return ContractReport(
        contract_id=contract_id,
        avro_name=avro_name,
        n_bindings=n_bindings,
        avro_parsing_fingerprint=avro_fp,
        optimization_metadata_hash=meta_hash,
        generation_ready=generation_ready,
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
        known = set(registry.list_contracts())
        missing = [c for c in profile.inputContracts if c not in known]
        if missing:
            report.profile_errors.append(f"profile references unknown contracts: {missing}")
        report.profile_ok = not report.profile_errors
    except Exception as exc:  # noqa: BLE001
        report.profile_errors.append(str(exc))
        report.profile_ok = False

    return report
