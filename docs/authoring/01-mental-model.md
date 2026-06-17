[Authoring manual](../authoring-domain-contracts.md) > 1. The mental model

# 1. The mental model: three layers

The single most important idea: **the engine never reads your domain schema.**
It reads a domain-neutral canonical model. Your job as an author is to write the
*projection* from your physical data onto that canonical model. Everything else
(routing, scheduling, costing, monitoring) is shared engine machinery.

```
  Canonical optimization model         contracts/canonical/
    (the minimal contract the engine consumes; domain-neutral)
            ^
            | projected onto by  (mapping documents)
            |
  Domain mapping pack                  contracts/domains/<domain>/
    (physical ODCS schema + *.mapping.yaml + profile.yaml)
            ^
            | reads / generated from
            |
  Physical source data                 service-trucks.csv, clearing-jobs.csv, ...
```

| Layer | Owns | Source of truth for | You edit it? |
|---|---|---|---|
| Canonical model (`contracts/canonical/`) | Entities, required fields, semantic-term vocabulary | What the engine can reason about | Rarely (only to add a genuinely new meaning) |
| Domain pack (`contracts/domains/<domain>/`) | Physical schema, mappings, profile | Your domain's structure and semantics | **Yes, this is your work** |
| Physical data | Rows | Reality | Generated or loaded |

The contract artifacts inside a pack split into three kinds:

- **Physical ODCS contract** (`odcs/<name>.odcs.yaml`): a pure
  [Open Data Contract Standard](https://bitol-io.github.io/open-data-contract-standard/)
  schema. Field names, types, and schema-generation hints only. **No optimization
  semantics.** This keeps your physical schema and its generated Avro/Proto/ES/
  Parquet representations independent of the optimizer.
- **Canonical mapping** (`mappings/<name>.mapping.yaml`, `kind: CanonicalMapping`):
  the authority that projects each physical field onto a canonical binding plus a
  semantic term. This is where the meaning lives.
- **Optimization profile** (`profile.yaml`, `kind: OptimizationProfile`): the
  per-domain policy: which contracts feed planning, role definitions, constraints,
  weather/material/monitoring policies, objective priorities, and planning
  horizons.

Why the split matters: the source word is irrelevant. A `tractor`, an
`excavator`, a `service-truck`, and a `UGV` are all just `asset` with role
`mobile-prime-mover`. The engine compares capabilities and roles, so one solver
serves every domain.

---

Next: [2. Glossary and thesaurus](02-glossary-and-thesaurus.md)
</content>
