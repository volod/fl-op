[Implementation guide](../current-implementation.md) > Data and contracts

# Data and contracts

`fl-op generate-data` writes one timestamped dataset under
`$DATA_DIR/generate-data/<timestamp>/` (Avro by default; CSV/Parquet via
`--format`). `metadata.json` records the chosen format and generated domain so
downstream commands use the right codec and, when no domain override is present,
build snapshots with the matching mapping/profile.

Physical schemas (Avro/Protobuf/Elasticsearch/Parquet) are generated from the
physical ODCS contracts into `contracts/generated/` (gitignored). Generated
schemas are structural only - they carry no optimization metadata. The
canonical plan OUTPUT contract generates physical schemas too
(`contracts/plan_schema_gen.py`, Avro and Parquet): nested records named
after the plan.json payload fields, joined from the same binding table the
publication validator uses, so downstream consumers can validate received
plan artifacts without this codebase. The plan output contract governs the
common score metrics, quality-summary fields, and corrective-action records in
addition to the envelope, assignments, unassigned tasks, and material
reservations; domain-specific nested score payloads such as solver attribution
and drone KPIs remain extra artifact data.

`fl-op contracts validate` checks: generated-schema structural fingerprints, the
canonical model, and per-domain **mapping completeness** (every mapping binds only
to declared canonical fields + known terms, and covers every required canonical
binding). The registry also exposes every source projection as a versioned
artifact ref (`domain/local-id@odcs:<version>+mapping:<version>`), validates
that those refs are unique, and still resolves legacy global ids and
domain-local aliases for compatibility. `fl-op contracts validate-domain
--domain <d>` additionally reports each contract's optimization-mapped vs
extra (analytical) physical fields.

`fl-op contracts evolution-check` enforces both structural and semantic
versioning. ODCS field changes keep the existing policy: added optional fields
need at least a minor contract-version bump, while removed fields, type
changes, requiredness changes, and added required fields need a major bump.
Canonical mapping metadata is snapshotted separately in the evolution history:
unit or quantity-kind conversions and enum/list expansions require a minor
mapping-version bump; binding or semantic-term retargeting, removals, enum
contractions, and unknown semantic rewrites require a major mapping-version
bump. Reviewed baselines carry both the normalized semantic metadata and the
registry artifact ref, so metadata edits are classified before the hash gate is
accepted.

## Multi-domain staging and policy composition

A snapshot build can span several domains at once. The registry composes the
selected domains' optimization profiles into one effective profile
(`FileRegistry.composite_profile`): the first domain that declares a profile is
the primary (it supplies identity, scalar defaults, and objective hierarchy) and
each later profile is layered on via `OptimizationProfile.composed_with`. Policy
merges are conservative so adding a domain never silently relaxes another:
weather limits collapse to the stricter (lower) bound and sensitivity maps union
(primary wins on shared operation types); monitoring scalars keep the primary
value while `assetTypeOverrides`/`assetOverrides` maps union (primary wins on key
collisions); constraints union by id with an enforced constraint winning a
conflict. With no profile-bearing domain selected the build falls back to engine
defaults unchanged.

Mixed-domain packs can declare the same `sourceFile` name (for example two
domains both staging `operators.csv`). The snapshot builder stages each domain
under its own subdirectory (`data_dir/<domain>/operators.csv`); the per-domain
file wins when present, otherwise the flat layout is used so single-domain
datasets load unchanged. `SnapshotBuilder.source_collisions` reports any
contracts from different domains still resolving to one physical file (which
would double-count entities) and `missing_source_files` reports declared
datasets absent from the directory. Both surface as warning `QualityFinding`s on
the snapshot (`dq://dataset/source-file-collision`,
`dq://dataset/source-file-missing`) rather than failing silently.

Every generator-bearing domain exposes capability metadata
(`FileRegistry.generator_capabilities`, surfaced by
`data/domain_generators.py` and the `fl-op domain-capabilities` CLI command):
the generator callable, declared profile, the canonical entities the domain's
contracts project, the staged contract ids, and source formats. Derived fields
always reflect the registry, so capabilities cannot drift from the contracts.
</content>
