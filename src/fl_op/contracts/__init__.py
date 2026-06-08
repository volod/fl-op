"""Declarative data-contract layer: ODCS contracts, generated schemas, profiles.

ODCS is the single source of truth for all semantic metadata. Physical schemas
(Avro, Protobuf, Elasticsearch) are generated from ODCS and carry no embedded
semantic blocks.
"""

from fl_op.contracts.fingerprint import avro_parsing_fingerprint, odcs_metadata_hash
from fl_op.contracts.registry import FileRegistry, MetadataLossError
from fl_op.contracts.validate import SuiteReport, validate_suite

__all__ = [
    "FileRegistry",
    "MetadataLossError",
    "SuiteReport",
    "validate_suite",
    "avro_parsing_fingerprint",
    "odcs_metadata_hash",
]
