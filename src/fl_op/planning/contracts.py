"""Contract-validation planning command implementation."""

import logging

from fl_op.contracts.registry import FileRegistry
from fl_op.contracts.validate import (
    validate_canonical_model,
    validate_domain,
    validate_suite,
)

logger = logging.getLogger(__name__)


def run_domain_validate(domain: str) -> bool:
    """Validate a domain pack's mappings against the canonical model. Returns ok."""
    report = validate_domain(domain)
    logger.info(
        "Domain '%s': %d mappings, entities covered %s [%s]",
        report.domain,
        report.n_mappings,
        report.entities_covered or "[]",
        "OK" if report.ok else "FAILED",
    )
    logger.info("  %-18s %-10s  optimization / extra (analytical) physical fields", "contract", "entity")
    for c in report.coverage:
        logger.info(
            "  %-18s %-10s  %d optimization, %d extra%s",
            c.contract,
            c.canonical_entity,
            len(c.optimization_fields),
            len(c.extra_fields),
            f": {c.extra_fields}" if c.extra_fields else "",
        )
    for err in report.errors:
        logger.error("  %s: %s", domain, err)
    return report.ok


def run_canonical_validate() -> bool:
    """Validate only the canonical optimization-model contracts. Returns ok."""
    report = validate_canonical_model()
    logger.info(
        "Canonical model %s: %d entities, %d fields, %d semantic terms [%s]",
        report.model_ref or "n/a",
        report.n_entities,
        report.n_fields,
        report.n_terms,
        "OK" if report.ok else "FAILED",
    )
    for err in report.errors:
        logger.error("  canonical-model: %s", err)
    return report.ok


def run_evolution_check() -> bool:
    """Check all ODCS contracts against committed schema baselines. Returns ok."""
    from fl_op.contracts.evolution import check_evolution

    report = check_evolution()
    logger.info("Schema evolution: %s", "OK" if report.ok else "FAILED")
    logger.info("%-28s %10s %10s  change", "contract", "baseline", "current")
    for c in report.contracts:
        logger.info(
            "%-28s %10s %10s  %s",
            c.contract_id,
            c.baseline_version or "n/a",
            c.current_version,
            c.change_class,
        )
        for detail in c.details:
            logger.info("    %s", detail)
        for err in c.errors:
            logger.error("  %s", err)
    for err in report.stale_baselines:
        logger.error("  %s", err)
    return report.ok


def run_evolution_freeze() -> bool:
    """Record schema baselines for all ODCS contracts. Returns ok."""
    from fl_op.contracts.evolution import freeze_baselines

    written = freeze_baselines()
    for path in written:
        logger.info("  baseline %s", path.name)
    return bool(written)


def run_contracts_validate(persist: bool = False) -> bool:
    """Validate the contract suite; optionally persist fingerprints. Returns ok."""
    registry = FileRegistry()
    report = validate_suite(registry)

    logger.info("Contract validation: %s", "OK" if report.ok else "FAILED")

    cm = report.canonical_model
    logger.info(
        "canonical model %s: %d entities, %d fields, %d terms [%s]",
        cm.model_ref or "n/a",
        cm.n_entities,
        cm.n_fields,
        cm.n_terms,
        "ok" if cm.ok else "FAILED",
    )
    for err in cm.errors:
        logger.error("  canonical-model: %s", err)

    logger.info(
        "%-18s %8s  gen   parsingFP        metaHash", "contract", "bindings"
    )
    for c in report.contracts:
        logger.info(
            "%-18s %8d  %s   %s  %s",
            c.contract_id,
            c.n_bindings,
            "ok" if c.generation_ready else "NO",
            c.avro_parsing_fingerprint[:12] if c.avro_parsing_fingerprint else "n/a         ",
            c.optimization_metadata_hash[:12] if c.optimization_metadata_hash else "n/a         ",
        )
        for err in c.errors:
            logger.error("  %s: %s", c.contract_id, err)
    if report.profile_errors:
        for err in report.profile_errors:
            logger.error("  profile: %s", err)

    # A metadata-hash drift is exactly what --write exists to acknowledge: a
    # reviewed mapping change. Persist when drift is the only kind of error;
    # any other validation failure still blocks persistence.
    from fl_op.contracts.registry import METADATA_DRIFT_MARKER

    only_drift_errors = (
        report.profile_ok
        and report.canonical_model.ok
        and any(c.errors for c in report.contracts)
        and all(
            c.generation_ready
            and all(METADATA_DRIFT_MARKER in err for err in c.errors)
            for c in report.contracts
        )
    )
    if persist and (report.ok or only_drift_errors):
        fps = {
            c.contract_id: {
                k: v
                for k, v in {
                    "avroParsingFingerprint": c.avro_parsing_fingerprint,
                    "optimizationMetadataHash": c.optimization_metadata_hash,
                }.items()
                if v
            }
            for c in report.contracts
        }
        registry.persist_fingerprints(fps)
        if only_drift_errors:
            logger.info(
                "[ok] acknowledged optimization-metadata change; fingerprints persisted"
            )
            return True
    return report.ok
