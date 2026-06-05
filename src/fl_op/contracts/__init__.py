"""Declarative data-contract layer: Avro schemas, ODCS contracts, profiles.

This package loads and validates the x-optimization extension metadata that
binds governed source fields to solver-neutral canonical abstractions. It is the
governance entry point of the platform; nothing here depends on the solver.
"""

from fl_op.contracts.fingerprint import (
    avro_parsing_fingerprint,
    optimization_metadata_hash,
)
from fl_op.contracts.registry import FileRegistry, MetadataLossError
from fl_op.contracts.validate import SuiteReport, validate_suite

__all__ = [
    "FileRegistry",
    "MetadataLossError",
    "SuiteReport",
    "validate_suite",
    "avro_parsing_fingerprint",
    "optimization_metadata_hash",
]
