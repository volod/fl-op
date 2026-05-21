# ADR-015: sklearn.neighbors.BallTree instead of scipy.spatial.BallTree

Date: 2026-05-21
Status: Accepted
Deciders: Implementation team (discovered during development)

## Context

The design doc (ADR-003) specifies `scipy.spatial.BallTree` with
`metric='haversine'` for geographic nearest-depot queries. The haversine metric
is the key requirement; the library providing the BallTree is a means to that end.

During implementation, the installed scipy version was 1.17.1. Attempting to
import `BallTree` from `scipy.spatial` raised `ImportError: cannot import name
'BallTree'`. Inspection of `dir(scipy.spatial)` confirmed BallTree is absent.

scipy removed `BallTree` from the public API in version 1.14 (it was deprecated
since 1.10). The class was moved to `scipy.spatial._ball_tree` (private) and
later removed entirely.

## Investigation

Three options were evaluated:

1. **Pin scipy < 1.14**: downgrades the entire scipy version to preserve the API.
   Risks incompatibility with other scipy features used in the generator.
2. **Implement haversine nearest-neighbour with vectorised numpy**: for the
   specific use case (N fields, K depots, K <= 50), a fully vectorised numpy
   haversine matrix (N x K) is O(N*K) and sub-second. Avoids any library
   dependency for this operation.
3. **Use sklearn.neighbors.BallTree with metric='haversine'**: scikit-learn
   preserves `BallTree` with the haversine metric; it is actively maintained.
   Adds scikit-learn as an explicit dependency.

## Decision

Add **scikit-learn >= 1.4** as an explicit project dependency. Replace the
`scipy.spatial.BallTree` import with `sklearn.neighbors.BallTree`.

The haversine metric, radian coordinate convention, and all correctness
properties from ADR-003 are preserved unchanged.

## Rationale

Option 2 (numpy haversine matrix) would work for N=20000 fields and K=50 depots
(1M haversine evaluations, ~100ms in numpy). However, it does not generalise
to the full BallTree use case in preprocessing — specifically, the query pattern
in `cluster_orders_by_depot()` where arbitrary query points are matched against
a fixed depot set benefits from BallTree's O(log K) query time.

Option 3 preserves the BallTree abstraction, supports the haversine metric
natively with the same `np.radians(coords)` convention, and is the standard
replacement recommended by scipy's own deprecation notice.

scikit-learn is a well-established numerical computing dependency (same ecosystem
as numpy/scipy). Its addition is appropriate for a project of this class.

## Consequences

- `scikit-learn >= 1.4` is now a runtime dependency in `pyproject.toml`.
- All BallTree usage in the codebase uses `sklearn.neighbors.BallTree`. Any
  future addition of geographic queries must use this import, not scipy.
- If scikit-learn itself deprecates or changes the BallTree haversine API, this
  ADR should be revisited. As of sklearn 1.4+ the API is stable.
- The version pin `>= 1.4` is conservative; sklearn 1.4 is the first version
  with the stable BallTree haversine interface. Pin tightly if a regression
  appears in a future minor version.
