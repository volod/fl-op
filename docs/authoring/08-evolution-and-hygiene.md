[Authoring manual](../authoring-domain-contracts.md) > 8. Evolution, fingerprints, hygiene

# 8. Evolution, fingerprints, and documentation hygiene

## 8.1 Fingerprints

The registry stores two hashes per contract:

- `avroParsingFingerprint` (structural, from the generated Avro schema), and
- `optimizationMetadataHash` (semantic, computed from the **mapping document**).

The metadata-loss guard fails the suite if a stored `optimizationMetadataHash`
diverges from the recomputed one; re-stamp with `fl-op contracts validate
--write`.

## 8.2 Semantic versioning of contracts and mappings

The evolution gate (`fl-op contracts evolution-check`) classifies each change
against the reviewed history in `contracts/evolution/<contract>.json`:

| Change | Bump |
|---|---|
| Added optional ODCS field, unit conversion, enum/list expansion | minor |
| Breaking schema change, binding retarget | major |
| Mapping-semantic change (unit switch, binding change, planning-use change) | reviewed hash gate in the same flow |

After a reviewed change with the policy-required version bump, record the new
baseline with `fl-op contracts evolution-freeze`.

## 8.3 Documentation hygiene (project rule)

Per [AGENTS.md](../../AGENTS.md): `docs/future-improvements.md` tracks open work
only; delivered behavior lives in `docs/current-implementation.md`. When you
finish a domain or an engine capability, move the delivered detail into
`current-implementation.md`, reduce the future-work item to its residual open
work, and keep the sequence number stable as a workstream id. If you add a new
domain, document it in the ontology's domain-coverage table.

---

Previous: [7. Runtime feasibility checks and verification](07-runtime-feasibility.md) | Next: [9. Reference tables and command cheat-sheet](09-reference-tables.md)
</content>
