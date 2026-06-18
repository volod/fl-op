"""File-based contract and schema registry.

Backed by contracts/registry.yaml. Provides lookup of contract definitions and a
metadata-integrity guard: a recomputed optimizationMetadataHash must match the
stored value unless the caller explicitly persists new fingerprints.

Fingerprint semantics:
  avroParsingFingerprint   - computed from generated Avro (structural)
  optimizationMetadataHash - computed from ODCS (semantic)
"""

import copy
import logging
import os
import pathlib
from dataclasses import dataclass
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
from fl_op.contracts.plugins import (
    DomainPackContribution,
    discover_domain_packs,
    merge_contributions,
)
from fl_op.contracts.profile import OptimizationProfile, load_profile
from fl_op.core.paths import CONTRACTS_ROOT

logger = logging.getLogger(__name__)

# Marker shared with the validation CLI so a reviewed metadata change can be
# acknowledged (--write) while any other validation error still fails the run.
METADATA_DRIFT_MARKER = "optimizationMetadataHash changed"


class MetadataLossError(RuntimeError):
    """Raised when stored and computed optimization metadata hashes diverge."""


@dataclass(frozen=True)
class RegistryArtifact:
    """Versioned artifact identity for one registered contract projection."""

    registry_id: str
    artifact_id: str
    local_id: str
    qualified_id: str
    domain: Optional[str]
    odcs_id: str
    contract_version: str
    mapping_version: str

    @property
    def version(self) -> str:
        contract_version = self.contract_version or "-"
        mapping_version = self.mapping_version or "-"
        return f"odcs:{contract_version}+mapping:{mapping_version}"

    @property
    def ref(self) -> str:
        return f"{self.artifact_id}@{self.version}"

    def model_dump(self) -> dict[str, Any]:
        return {
            "registryId": self.registry_id,
            "artifactId": self.artifact_id,
            "artifactRef": self.ref,
            "localId": self.local_id,
            "qualifiedId": self.qualified_id,
            "domain": self.domain,
            "odcsId": self.odcs_id,
            "contractVersion": self.contract_version,
            "mappingVersion": self.mapping_version,
        }


class ContractEntry:
    def __init__(self, contract_id: str, spec: dict[str, Any]) -> None:
        self.registry_id = contract_id
        self.contract_id = contract_id
        self.local_id: str = spec.get("id") or spec.get("contractId") or contract_id
        self.avro_ref: Optional[str] = spec.get("avro")
        self.odcs_ref: Optional[str] = spec.get("odcs")
        self.mapping_ref: Optional[str] = spec.get("mapping")
        self.domain: Optional[str] = spec.get("domain")
        self.source_file: Optional[str] = spec.get("sourceFile")
        self.source_format: str = spec.get("sourceFormat", "csv")
        self.artifact_spec: dict[str, Any] = dict(spec.get("artifact") or {})
        self.stored_fingerprints: dict[str, str] = dict(spec.get("fingerprints") or {})

    @property
    def qualified_id(self) -> str:
        return f"{self.domain}/{self.local_id}" if self.domain else self.local_id


class FileRegistry:
    """Registry over a contracts directory containing registry.yaml."""

    def __init__(self, root: pathlib.Path | None = None) -> None:
        self.root = root or CONTRACTS_ROOT
        self.index_path = self.root / "registry.yaml"
        if not self.index_path.exists():
            raise FileNotFoundError(f"Registry index not found: {self.index_path}")
        # The raw, file-backed index is the only thing ever written back to
        # registry.yaml (fingerprint persistence); the live ``index`` is that plus
        # any discovered plugin packs.
        self._file_index: dict[str, Any] = yaml.safe_load(self.index_path.read_text())
        contributions = discover_domain_packs()
        if contributions:
            self.index: dict[str, Any] = copy.deepcopy(self._file_index)
            # Domains contributed by installed plugin packs (entry-point
            # discovery), merged before entries are built so they are
            # first-class. In-repo keys always win; conflicts skip with a warning.
            self.plugin_domains: dict[str, DomainPackContribution] = merge_contributions(
                self.index, contributions
            )
        else:
            self.index = self._file_index
            self.plugin_domains = {}
        self.entries: dict[str, ContractEntry] = {
            cid: ContractEntry(cid, spec)
            for cid, spec in (self.index.get("contracts") or {}).items()
        }

    # -- contract / schema access -------------------------------------------------

    def list_contracts(self) -> list[str]:
        return list(self.entries)

    def _split_versioned_ref(self, contract_id: str) -> tuple[str, Optional[str]]:
        if "@" not in contract_id:
            return contract_id, None
        base, version = contract_id.rsplit("@", 1)
        return base, version or None

    def domain_ids(self) -> list[str]:
        return sorted((self.index.get("domains") or {}).keys())

    def get_domain_spec(self, domain: str) -> dict[str, Any]:
        domains = self.index.get("domains") or {}
        if domain not in domains:
            raise KeyError(
                f"Unknown domain '{domain}'; known: {sorted(domains)}"
            )
        return domains[domain] or {}

    def profile_domain(self, profile_id: str) -> Optional[str]:
        for domain, spec in (self.index.get("domains") or {}).items():
            if (spec or {}).get("profile") == profile_id:
                return domain
        return None

    def resolve_contract_id(
        self,
        contract_id: str,
        domain: Optional[str] = None,
    ) -> str:
        """Resolve a global, qualified, or domain-local contract id.

        Registry keys remain globally unique for compatibility. Domain profiles
        can refer to local ids such as ``operators``; when resolved in the
        construction domain that points at the existing ``construction-operators``
        registry entry.
        """
        base_id, requested_version = self._split_versioned_ref(contract_id)

        if "/" in base_id:
            domain_part, local_id = base_id.split("/", 1)
            matches = [
                key
                for key, entry in self.entries.items()
                if (
                    entry.domain == domain_part
                    and entry.local_id == local_id
                )
                or self._contract_artifact_for_resolved(key).artifact_id == base_id
            ]
            if len(matches) == 1:
                return self._check_requested_artifact_version(
                    matches[0], requested_version
                )
            raise KeyError(f"Unknown contract id: {contract_id}")

        if domain is not None:
            matches = [
                key
                for key, entry in self.entries.items()
                if entry.domain == domain and entry.local_id == base_id
            ]
            if len(matches) == 1:
                return self._check_requested_artifact_version(
                    matches[0], requested_version
                )
            if len(matches) > 1:
                raise KeyError(
                    f"Ambiguous contract id '{base_id}' in domain '{domain}'"
                )
            if base_id in self.entries:
                return self._check_requested_artifact_version(
                    base_id, requested_version
                )

        if base_id in self.entries:
            return self._check_requested_artifact_version(
                base_id, requested_version
            )

        matches = [
            key for key, entry in self.entries.items() if entry.local_id == base_id
        ]
        if len(matches) == 1:
            return self._check_requested_artifact_version(
                matches[0], requested_version
            )
        if len(matches) > 1:
            raise KeyError(
                f"Ambiguous contract id '{base_id}'; qualify it as domain/id"
            )
        raise KeyError(f"Unknown contract id: {contract_id}")

    def _check_requested_artifact_version(
        self, registry_id: str, requested_version: Optional[str]
    ) -> str:
        if requested_version is None:
            return registry_id
        artifact = self.contract_artifact(registry_id)
        if artifact.version != requested_version:
            raise KeyError(
                f"Contract artifact '{artifact.artifact_id}' has version "
                f"{artifact.version}, not {requested_version}"
            )
        return registry_id

    def get_entry(
        self,
        contract_id: str,
        domain: Optional[str] = None,
    ) -> ContractEntry:
        resolved = self.resolve_contract_id(contract_id, domain=domain)
        return self.entries[resolved]

    def contract_artifact(
        self,
        contract_id: str,
        domain: Optional[str] = None,
    ) -> RegistryArtifact:
        """Return the versioned registry artifact identity for a contract.

        The artifact id is domain-local and stable (``domain/local-id``). Its
        version carries both independently governed dimensions: physical ODCS
        version and canonical mapping version.
        """
        resolved = self.resolve_contract_id(contract_id, domain=domain)
        return self._contract_artifact_for_resolved(resolved)

    def list_contract_artifacts(self) -> list[RegistryArtifact]:
        return [
            self._contract_artifact_for_resolved(cid)
            for cid in self.list_contracts()
        ]

    def _contract_artifact_for_resolved(self, registry_id: str) -> RegistryArtifact:
        entry = self.entries[registry_id]
        odcs_id = ""
        contract_version = ""
        mapping_version = ""
        if entry.odcs_ref:
            odcs = load_odcs_contract(self.root / entry.odcs_ref)
            odcs_id = odcs.id
            contract_version = odcs.version
        if entry.mapping_ref:
            mapping = load_mapping(self.root / entry.mapping_ref)
            mapping_version = mapping.mapping_version or ""
        artifact_id = (
            entry.artifact_spec.get("id")
            or (f"{entry.domain}/{entry.local_id}" if entry.domain else entry.local_id)
        )
        return RegistryArtifact(
            registry_id=registry_id,
            artifact_id=artifact_id,
            local_id=entry.local_id,
            qualified_id=entry.qualified_id,
            domain=entry.domain,
            odcs_id=odcs_id,
            contract_version=contract_version,
            mapping_version=mapping_version,
        )

    def get_avro(self, contract_id: str) -> AvroContractSchema:
        contract_id = self.resolve_contract_id(contract_id)
        entry = self.entries[contract_id]
        if not entry.avro_ref:
            raise KeyError(f"Contract {contract_id} has no Avro schema")
        return load_avro_schema(self.root / entry.avro_ref)

    def get_odcs(self, contract_id: str) -> Optional[OdcsContract]:
        contract_id = self.resolve_contract_id(contract_id)
        entry = self.entries[contract_id]
        if not entry.odcs_ref:
            return None
        return load_odcs_contract(self.root / entry.odcs_ref)

    def get_mapping(self, contract_id: str) -> Optional[CanonicalMapping]:
        """Load the canonical mapping document for a registered contract."""
        contract_id = self.resolve_contract_id(contract_id)
        entry = self.entries[contract_id]
        if not entry.mapping_ref:
            return None
        return load_mapping(self.root / entry.mapping_ref)

    @property
    def active_domain(self) -> Optional[str]:
        """Active domain pack: ACTIVE_DOMAIN env override, else the registry index.

        The override lets one deployment switch domains per run
        (ACTIVE_DOMAIN=construction fl-op plan periodic ...) without editing
        registry.yaml.
        """
        override = os.environ.get("ACTIVE_DOMAIN")
        if override:
            known = set(self.index.get("domains") or {})
            if override not in known:
                raise KeyError(
                    f"ACTIVE_DOMAIN '{override}' is not a registered domain; "
                    f"known: {sorted(known)}"
                )
            return override
        return self.index.get("activeDomain")

    @property
    def active_domains(self) -> list[str]:
        """Active domain set for shared-fleet planning.

        ``ACTIVE_DOMAINS=agricultural,construction`` maps and projects multiple
        registered packs into one canonical snapshot. If unset, the legacy
        single ``ACTIVE_DOMAIN`` / registry ``activeDomain`` behavior is used.
        """
        override = os.environ.get("ACTIVE_DOMAINS")
        if override:
            known = set(self.index.get("domains") or {})
            requested = [
                item.strip()
                for item in override.split(",")
                if item.strip()
            ]
            if requested in (["*"], ["all"]):
                return sorted(known)
            unknown = sorted(set(requested) - known)
            if unknown:
                raise KeyError(
                    f"ACTIVE_DOMAINS contains unregistered domains {unknown}; "
                    f"known: {sorted(known)}"
                )
            return requested
        domain = self.active_domain
        return [domain] if domain else []

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
        profile = load_profile(self.root / profiles[profile_id]["path"])
        if self.profile_domain(profile_id) == "drone_logistics":
            from fl_op.data.drone_logistics_tuning import apply_drone_profile_tuning

            return apply_drone_profile_tuning(profile)
        return profile

    def domain_profile_ids(self, domains: list[str]) -> list[str]:
        """Profile ids declared by the given domains, in domain order, deduped.

        A domain without a declared profile contributes nothing; the result is
        the ordered set of profiles to compose for a (possibly multi-domain)
        shared-fleet build.
        """
        profile_ids: list[str] = []
        for domain in domains:
            spec = self.get_domain_spec(domain)
            profile_id = spec.get("profile")
            if profile_id and profile_id not in profile_ids:
                profile_ids.append(profile_id)
        return profile_ids

    def composite_profile(
        self, domains: list[str]
    ) -> Optional[OptimizationProfile]:
        """Compose the active domains' profiles into one optimization profile.

        The first domain that declares a profile is the primary (it supplies the
        base identity, scalar defaults, and objective hierarchy); each subsequent
        domain profile is layered on via ``OptimizationProfile.composed_with``.
        Returns ``None`` when no selected domain declares a profile, so callers
        fall back to engine defaults exactly as before.
        """
        profile_ids = self.domain_profile_ids(domains)
        if not profile_ids:
            return None
        composite = self.get_profile(profile_ids[0])
        for profile_id in profile_ids[1:]:
            composite = composite.composed_with(self.get_profile(profile_id))
        return composite

    # -- generator capability metadata --------------------------------------------

    def domain_entities(self, domain: str) -> list[str]:
        """Canonical entities the domain's contracts map, in declaration order.

        Drives generator capability metadata: a domain can generate exactly the
        canonical entities its registered contracts project, so the registry is
        the single source of truth rather than a hand-maintained list.
        """
        entities: list[str] = []
        for cid in self.list_contracts():
            entry = self.entries[cid]
            if entry.domain != domain or not entry.mapping_ref:
                continue
            mapping = self.get_mapping(cid)
            if mapping is not None and mapping.canonical_entity not in entities:
                entities.append(mapping.canonical_entity)
        return entities

    def generator_capabilities(self, domain: str) -> dict[str, Any]:
        """Describe what a domain's data generator can produce.

        Combines registry-derived facts (the generator callable, the canonical
        entities the domain maps, the contract ids and source formats it stages)
        with any operator-declared ``capabilities`` block on the domain spec.
        Derived fields always reflect the registry so declared metadata cannot
        silently drift from the contracts.
        """
        spec = self.get_domain_spec(domain)
        contract_ids = [
            self.entries[cid].qualified_id
            for cid in self.list_contracts()
            if self.entries[cid].domain == domain
        ]
        source_formats = sorted(
            {
                self.entries[cid].source_format
                for cid in self.list_contracts()
                if self.entries[cid].domain == domain
                and self.entries[cid].source_file
            }
        )
        declared = spec.get("capabilities")
        plugin = self.plugin_domains.get(domain)
        return {
            "domain": domain,
            "generator": spec.get("generator"),
            "profile": spec.get("profile"),
            "version": spec.get("version"),
            "source": "plugin" if plugin is not None else "builtin",
            "plugin": (
                {"entryPoint": plugin.entry_point, "distribution": plugin.distribution}
                if plugin is not None
                else None
            ),
            "canonicalEntities": self.domain_entities(domain),
            "contracts": contract_ids,
            "sourceFormats": source_formats,
            "declared": dict(declared) if isinstance(declared, dict) else {},
        }

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
                f"Contract {contract_id}: {METADATA_DRIFT_MARKER} from "
                f"{prior} to {current}; rerun validation with --write after reviewing the change"
            )
        return computed

    def persist_fingerprints(self, fingerprints_by_contract: dict[str, dict[str, str]]) -> None:
        """Write recomputed fingerprints back into registry.yaml.

        Only file-backed contracts are persisted: discovered plugin contracts
        live in their own distribution and are never written into this repo's
        registry.yaml, so the dump targets ``_file_index`` (which excludes the
        merged plugin entries).
        """
        file_contracts = self._file_index.get("contracts") or {}
        for cid, fps in fingerprints_by_contract.items():
            if cid in file_contracts:
                file_contracts[cid]["fingerprints"] = fps
                self.index["contracts"][cid]["fingerprints"] = fps
                self.entries[cid].stored_fingerprints = dict(fps)
        self.index_path.write_text(yaml.safe_dump(self._file_index, sort_keys=False))
        logger.info("Persisted fingerprints for %d contracts", len(fingerprints_by_contract))
