"""Contract-validation planning command implementation."""

import logging

from fl_op.contracts.registry import FileRegistry
from fl_op.contracts.validate import validate_suite

logger = logging.getLogger(__name__)


def run_contracts_validate(persist: bool = False) -> bool:
    """Validate the contract suite; optionally persist fingerprints. Returns ok."""
    registry = FileRegistry()
    report = validate_suite(registry)

    logger.info("Contract validation: %s", "OK" if report.ok else "FAILED")
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

    if persist and report.ok:
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
    return report.ok
