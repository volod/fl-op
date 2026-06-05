"""File-based contract and schema registry (spec 6: schema-registry, contract-registry).

Backed by contracts/registry.yaml. Provides lookup of contract definitions and a
metadata-loss guard: registering a schema whose recomputed optimizationMetadataHash
diverges from a previously stored value raises, satisfying spec 9.4 ("schema
registration SHALL fail if metadata is lost").
"""

import logging
import pathlib
from typing import Any, Optional

import yaml

from fl_op.contracts.avro_loader import AvroContractSchema, load_avro_schema
from fl_op.contracts.odcs_loader import OdcsContract, load_odcs_contract
from fl_op.contracts.profile import OptimizationProfile, load_profile
from fl_op.core.paths import CONTRACTS_ROOT

logger = logging.getLogger(__name__)


class MetadataLossError(RuntimeError):
    """Raised when a schema re-registration would drop or alter x-opt metadata."""


class ContractEntry:
    def __init__(self, contract_id: str, spec: dict[str, Any]) -> None:
        self.contract_id = contract_id
        self.avro_ref: Optional[str] = spec.get("avro")
        self.odcs_ref: Optional[str] = spec.get("odcs")
        self.source_file: Optional[str] = spec.get("sourceFile")
        self.source_format: str = spec.get("sourceFormat", "csv")
        self.canonical_entity: Optional[str] = spec.get("canonicalEntity")
        self.asset_role: Optional[str] = spec.get("assetRole")
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

    def get_profile(self, profile_id: str) -> OptimizationProfile:
        profiles = self.index.get("profiles") or {}
        if profile_id not in profiles:
            raise KeyError(f"Unknown profile id: {profile_id}")
        return load_profile(self.root / profiles[profile_id]["path"])

    # -- metadata-loss guard ------------------------------------------------------

    def verify_no_metadata_loss(self, contract_id: str) -> dict[str, str]:
        """Recompute fingerprints and compare against stored values.

        Returns the freshly computed fingerprints. Raises MetadataLossError if a
        stored optimizationMetadataHash exists and differs (metadata was dropped
        or altered without a versioned migration).
        """
        entry = self.get_entry(contract_id)
        if not entry.avro_ref:
            return {}
        computed = self.get_avro(contract_id).fingerprints
        stored = entry.stored_fingerprints
        prior = stored.get("optimizationMetadataHash")
        if prior and prior != computed["optimizationMetadataHash"]:
            raise MetadataLossError(
                f"Contract {contract_id}: optimizationMetadataHash changed from "
                f"{prior} to {computed['optimizationMetadataHash']} without migration"
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
