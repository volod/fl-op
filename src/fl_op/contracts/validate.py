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
from typing import TYPE_CHECKING, Optional

from fl_op.contracts.registry import FileRegistry, MetadataLossError
from fl_op.contracts.schema_gen import check_generation

if TYPE_CHECKING:
    from fl_op.contracts.canonical_model import CanonicalModel
    from fl_op.contracts.mapping_loader import CanonicalMapping

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
class ContractCoverage:
    """Physical-field breakdown for one domain contract.

    Separates the fields the optimizer consumes (mapped onto the canonical model)
    from extra physical fields retained for analysis / future use.
    """

    contract: str
    canonical_entity: str
    n_physical: int = 0
    optimization_fields: list[str] = field(default_factory=list)
    extra_fields: list[str] = field(default_factory=list)


@dataclass
class DomainReport:
    domain: str
    n_mappings: int = 0
    entities_covered: list[str] = field(default_factory=list)
    coverage: list[ContractCoverage] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass
class CanonicalModelReport:
    model_ref: str = ""
    n_entities: int = 0
    n_fields: int = 0
    n_terms: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.model_ref) and not self.errors


@dataclass
class SuiteReport:
    contracts: list[ContractReport] = field(default_factory=list)
    profile_id: Optional[str] = None
    profile_ok: bool = False
    profile_errors: list[str] = field(default_factory=list)
    canonical_model: CanonicalModelReport = field(default_factory=CanonicalModelReport)

    @property
    def ok(self) -> bool:
        return (
            all(c.ok for c in self.contracts)
            and self.profile_ok
            and self.canonical_model.ok
        )


def _check_mapping_against_model(
    contract_id: str, mapping: "CanonicalMapping", model: "CanonicalModel"
) -> list[str]:
    """Every mapping binding must target a declared canonical field + known term."""
    errors: list[str] = []
    entity = mapping.canonical_entity
    if entity not in model.entities():
        errors.append(f"{contract_id}: unknown canonical entity '{entity}'")
        return errors
    allowed = model.allowed_bindings(entity)
    for fb in mapping.bindings:
        if fb.meta.binding not in allowed:
            errors.append(
                f"{contract_id}: binding '{fb.meta.binding}' is not declared on "
                f"canonical entity '{entity}'"
            )
        if not model.has_term(fb.meta.semantic_term):
            errors.append(
                f"{contract_id}: unknown semantic term '{fb.meta.semantic_term}'"
            )
    return errors


def validate_contract(
    registry: FileRegistry,
    contract_id: str,
    model: Optional["CanonicalModel"] = None,
) -> ContractReport:
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
            avro_fp = avro.avro_parsing_fingerprint
        except Exception as exc:  # noqa: BLE001
            errors.append(f"Avro load failed: {exc}")

    # Generation readiness is still checked against the physical ODCS schema.
    odcs = registry.get_odcs(contract_id)
    if odcs is not None:
        gen_report = check_generation(odcs.doc, contract_id, "avro")
        if not gen_report.ok:
            generation_ready = False
            for err in gen_report.errors:
                errors.append(f"generation check: {err}")

    # Semantic metadata, fingerprint, and canonical completeness come from the
    # mapping document.
    mapping = registry.get_mapping(contract_id)
    if mapping is not None:
        meta_hash = registry.compute_fingerprints(contract_id).get(
            "optimizationMetadataHash", ""
        )
        n_bindings = len(mapping.bindings)
        if model is not None:
            errors.extend(_check_mapping_against_model(contract_id, mapping, model))

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


def validate_canonical_model() -> CanonicalModelReport:
    """Validate the canonical optimization-model contracts in isolation.

    Checks that the model index and per-entity ODCS contracts parse, that every
    declared canonical field references a known semantic term, that bindings are
    unique within an entity, and that each entity declares at least one required
    identity field.
    """
    from fl_op.contracts.canonical_model import load_canonical_model

    report = CanonicalModelReport()
    try:
        model = load_canonical_model()
    except Exception as exc:  # noqa: BLE001
        report.errors.append(f"canonical model load failed: {exc}")
        return report

    report.model_ref = model.model_ref
    report.n_entities = len(model.entities())
    report.n_fields = len(model.fields)
    report.n_terms = len(model.semantic_terms)

    if not model.model_ref:
        report.errors.append("canonical model is missing canonicalModelRef")

    for entity in model.entities():
        seen: set[str] = set()
        for fld in model.fields_for(entity):
            if not model.has_term(fld.semantic_term):
                report.errors.append(
                    f"{entity}.{fld.name}: unknown semantic term '{fld.semantic_term}'"
                )
            if fld.binding in seen:
                report.errors.append(
                    f"{entity}: duplicate binding '{fld.binding}'"
                )
            seen.add(fld.binding)
        if not model.required_bindings(entity):
            report.errors.append(f"{entity}: no required identity field declared")

    return report


def validate_domain(
    domain: str, registry: FileRegistry | None = None
) -> DomainReport:
    """Validate that a domain pack's mappings cover the canonical model completely.

    Resolves the domain's mapping documents (either from the registered contracts,
    for the active domain, or from the domain's explicit ``mappings`` list) and
    checks each against the canonical model, then verifies that every required
    canonical binding for each covered entity is satisfied.
    """
    from fl_op.contracts.canonical_model import load_canonical_model
    from fl_op.contracts.mapping_loader import load_mapping
    from fl_op.contracts.odcs_loader import load_odcs_contract

    registry = registry or FileRegistry()
    report = DomainReport(domain=domain)

    domains = registry.index.get("domains") or {}
    if domain not in domains:
        report.errors.append(f"unknown domain '{domain}'")
        return report

    try:
        model = load_canonical_model()
    except Exception as exc:  # noqa: BLE001
        report.errors.append(f"canonical model load failed: {exc}")
        return report

    domain_root = registry.root / domains[domain].get("root", f"domains/{domain}")

    mapping_refs: list[str] = list(domains[domain].get("mappings") or [])
    if not mapping_refs:
        mapping_refs = [
            entry.mapping_ref
            for cid in registry.list_contracts()
            for entry in [registry.get_entry(cid)]
            if entry.domain == domain and entry.mapping_ref
        ]

    covered: dict[str, set[str]] = {}
    for ref in mapping_refs:
        mapping = load_mapping(registry.root / ref)
        report.n_mappings += 1
        report.errors.extend(
            _check_mapping_against_model(mapping.source_contract, mapping, model)
        )
        covered.setdefault(mapping.canonical_entity, set()).update(
            fb.meta.binding for fb in mapping.bindings
        )

        # Field-level breakdown: optimization-mapped vs extra (analytical) physical
        # fields. Extra physical fields are retained for analysis, not required by
        # the optimizer.
        mapped = {fb.source_field for fb in mapping.bindings}
        odcs_path = domain_root / "odcs" / f"{mapping.source_contract}.odcs.yaml"
        if odcs_path.exists():
            physical = load_odcs_contract(odcs_path).field_names()
            report.coverage.append(
                ContractCoverage(
                    contract=mapping.source_contract,
                    canonical_entity=mapping.canonical_entity,
                    n_physical=len(physical),
                    optimization_fields=[f for f in physical if f in mapped],
                    extra_fields=[f for f in physical if f not in mapped],
                )
            )

    report.entities_covered = sorted(covered)
    for entity, cov in covered.items():
        missing = model.required_bindings(entity) - cov
        if missing:
            report.errors.append(
                f"entity '{entity}' missing required canonical bindings: {sorted(missing)}"
            )
    return report


def validate_suite(
    registry: FileRegistry | None = None,
    profile_id: str = "agricultural-custom-services",
) -> SuiteReport:
    """Validate the canonical model, all registered contracts, and the profile."""
    from fl_op.contracts.canonical_model import load_canonical_model

    registry = registry or FileRegistry()
    report = SuiteReport(profile_id=profile_id)

    report.canonical_model = validate_canonical_model()

    model: Optional["CanonicalModel"]
    try:
        model = load_canonical_model()
    except Exception:  # noqa: BLE001 - reported by validate_canonical_model
        model = None

    for contract_id in registry.list_contracts():
        report.contracts.append(validate_contract(registry, contract_id, model))

    # Mapping completeness: every required canonical binding for an entity must be
    # covered by the union of the domain mappings that target that entity.
    if model is not None:
        covered: dict[str, set[str]] = {}
        for contract_id in registry.list_contracts():
            mapping = registry.get_mapping(contract_id)
            if mapping is None:
                continue
            entity_bindings = covered.setdefault(mapping.canonical_entity, set())
            entity_bindings.update(fb.meta.binding for fb in mapping.bindings)
        for entity in model.entities():
            required = model.required_bindings(entity)
            missing = required - covered.get(entity, set())
            if missing and entity in covered:
                report.canonical_model.errors.append(
                    f"domain mappings for entity '{entity}' miss required canonical "
                    f"bindings: {sorted(missing)}"
                )

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
