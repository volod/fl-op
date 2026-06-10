"""File-based contract and schema registry.

Backed by contracts/registry.yaml. Provides lookup of contract definitions and a
metadata-integrity guard: a recomputed optimizationMetadataHash must match the
stored value unless the caller explicitly persists new fingerprints.

Fingerprint semantics:
  avroParsingFingerprint   - computed from generated Avro (structural)
  optimizationMetadataHash - computed from ODCS (semantic)
"""

import logging
import pathlib
from typing import Any, Optional

import yaml

from fl_op.contracts.avro_loader import AvroContractSchema, load_avro_schema
from fl_op.contracts.fingerprint import mapping_metadata_hash
from fl_op.contracts.mapping_loader import (
    CanonicalMapping,
    load_mapping,
    mapping_metadata_blocks,
)
from fl_op.contracts.odcs_loader import OdcsContract, load_odcs_contract
from fl_op.contracts.profile import OptimizationProfile, load_profile
from fl_op.core.paths import CONTRACTS_ROOT

logger = logging.getLogger(__name__)


class MetadataLossError(RuntimeError):
    """Raised when stored and computed optimization metadata hashes diverge."""


class ContractEntry:
    def __init__(self, contract_id: str, spec: dict[str, Any]) -> None:
        self.contract_id = contract_id
        self.avro_ref: Optional[str] = spec.get("avro")
        self.odcs_ref: Optional[str] = spec.get("odcs")
        self.mapping_ref: Optional[str] = spec.get("mapping")
        self.domain: Optional[str] = spec.get("domain")
        self.source_file: Optional[str] = spec.get("sourceFile")
        self.source_format: str = spec.get("sourceFormat", "csv")
        self.stored_fingerprints: dict[str, str] = dict(spec.get("fingerprints") or {})


class FileRegistry:
    """Registry over a contracts directory containing registry.yaml."""

    def __init__(self, root: pathlib.Path | None = None) -> None:
        self.root = root or CONTRACTS_ROOT
        self.index_path = self.root / "registry.yaml"
        if not self.index_path.exists():
            raise FileNotFoundError(f"Registry index not found: {self.index_path}")
        self.index: dict[str, Any] = yaml.safe_load(self.index_path.read_text())
        self.entries: dict[str, ContractEntry] = {
            cid: ContractEntry(cid, spec)
            for cid, spec in (self.index.get("contracts") or {}).items()
        }

    # -- contract / schema access -------------------------------------------------

    def list_contracts(self) -> list[str]:
        return list(self.entries)

    def get_entry(self, contract_id: str) -> ContractEntry:
        if contract_id not in self.entries:
            raise KeyError(f"Unknown contract id: {contract_id}")
        return self.entries[contract_id]

    def get_avro(self, contract_id: str) -> AvroContractSchema:
        entry = self.get_entry(contract_id)
        if not entry.avro_ref:
            raise KeyError(f"Contract {contract_id} has no Avro schema")
        return load_avro_schema(self.root / entry.avro_ref)

    def get_odcs(self, contract_id: str) -> Optional[OdcsContract]:
        entry = self.get_entry(contract_id)
        if not entry.odcs_ref:
            return None
        return load_odcs_contract(self.root / entry.odcs_ref)

    def get_mapping(self, contract_id: str) -> Optional[CanonicalMapping]:
        """Load the canonical mapping document for a registered contract."""
        entry = self.get_entry(contract_id)
        if not entry.mapping_ref:
            return None
        return load_mapping(self.root / entry.mapping_ref)

    @property
    def active_domain(self) -> Optional[str]:
        return self.index.get("activeDomain")

    @property
    def active_profile_id(self) -> Optional[str]:
        """Profile id declared by the active domain, if any."""
        domain = self.active_domain
        if not domain:
            return None
        spec = (self.index.get("domains") or {}).get(domain) or {}
        return spec.get("profile")

    @property
    def canonical_model_ref(self) -> Optional[str]:
        return self.index.get("canonicalModelRef")

    def get_profile(self, profile_id: str) -> OptimizationProfile:
        profiles = self.index.get("profiles") or {}
        if profile_id not in profiles:
            raise KeyError(f"Unknown profile id: {profile_id}")
        return load_profile(self.root / profiles[profile_id]["path"])

    # -- fingerprint computation --------------------------------------------------

    def compute_fingerprints(self, contract_id: str) -> dict[str, str]:
        """Compute both fingerprints for a contract.

        avroParsingFingerprint   - from generated Avro schema (structural)
        optimizationMetadataHash - from the canonical mapping document (semantic)
        """
        fps: dict[str, str] = {}
        entry = self.get_entry(contract_id)

        if entry.avro_ref:
            avro = self.get_avro(contract_id)
            fps["avroParsingFingerprint"] = avro.avro_parsing_fingerprint

        if entry.mapping_ref:
            mapping_doc = mapping_metadata_blocks(self.root / entry.mapping_ref)
            fps["optimizationMetadataHash"] = mapping_metadata_hash(mapping_doc)

        return fps

    # -- metadata-loss guard ------------------------------------------------------

    def verify_no_metadata_loss(self, contract_id: str) -> dict[str, str]:
        """Recompute fingerprints and compare against stored values.

        Returns the freshly computed fingerprints. Raises MetadataLossError if a
        stored optimizationMetadataHash exists and differs.
        """
        computed = self.compute_fingerprints(contract_id)
        stored = self.get_entry(contract_id).stored_fingerprints
        prior = stored.get("optimizationMetadataHash")
        current = computed.get("optimizationMetadataHash")
        if prior and current and prior != current:
            raise MetadataLossError(
                f"Contract {contract_id}: optimizationMetadataHash changed from "
                f"{prior} to {current}; rerun validation with --write after reviewing the change"
            )
        return computed

    def persist_fingerprints(self, fingerprints_by_contract: dict[str, dict[str, str]]) -> None:
        """Write recomputed fingerprints back into registry.yaml."""
        for cid, fps in fingerprints_by_contract.items():
            if cid in self.index["contracts"]:
                self.index["contracts"][cid]["fingerprints"] = fps
                self.entries[cid].stored_fingerprints = dict(fps)
        self.index_path.write_text(yaml.safe_dump(self.index, sort_keys=False))
        logger.info("Persisted fingerprints for %d contracts", len(fingerprints_by_contract))
