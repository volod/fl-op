[Implementation guide](../current-implementation.md) > Schema evolution and CI

# Schema evolution and CI

- Every ODCS contract (registered domain contracts plus the canonical entity
  and plan contracts) has a committed reviewed snapshot under
  `contracts/evolution/` (`contracts/evolution.py`). New freezes write the
  latest schema at the top level and retain a `history` array, so
  `evolution-check` validates every adjacent reviewed schema migration pair
  plus the current contract. The version-bump policy is unchanged: added optional
  fields require at least a minor bump; removals, type changes, requiredness
  changes, and added required fields require a major bump; any change without a
  bump fails. Registered domain snapshots also carry the reviewed
  `optimizationMetadataHash`; current-vs-latest metadata drift is gated in the
  same review flow as structural schema evolution, while already-reviewed
  historical metadata hashes remain audit records. Flat pre-history baseline
  files remain readable as a one-entry history.
- CI (`.github/workflows/ci.yml`, `make ci`) regenerates all physical
  schemas from ODCS before any validation, then runs the suite validation,
  domain validations, the evolution gate, and the tests.
</content>
