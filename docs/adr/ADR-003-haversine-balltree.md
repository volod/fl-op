# ADR-003: Haversine BallTree for geographic clustering

Date: 2026-05-21
Status: Accepted
Deciders: Volodymyr Lazurenko, gstack /plan-eng-review

## Context

Orders must be grouped into clusters by proximity to depots so that each cluster
can be solved independently. Two approaches were evaluated for nearest-depot
assignment:

- **Euclidean cKDTree** (`scipy.spatial.cKDTree`): treats lat/lon coordinates as
  Cartesian (x, y) and computes straight-line distance in degree-space. Fast and
  trivially available in scipy.
- **Haversine BallTree**: computes great-circle distance on the sphere surface.
  Available via `sklearn.neighbors.BallTree(metric='haversine')`.

## Decision

Use **sklearn.neighbors.BallTree with metric='haversine'** for all geographic
nearest-depot queries. Coordinates are converted to radians before insertion.

## Rationale

Euclidean distance on lat/lon coordinates is incorrect at agricultural scale.
In central Ukraine (latitude ~49 deg N), one degree of longitude is approximately
67 km while one degree of latitude is approximately 111 km. Euclidean distance
treats them as equal, producing systematic errors of up to 40% in North-South vs.
East-West distance comparisons. At a depot-to-field radius of 400 km, this error
is large enough to assign orders to the wrong depot, degrading both dispatch
efficiency and solver quality.

Haversine distance is the correct measure for great-circle routing on a spheroid.
The overhead over Euclidean distance is a few trigonometric operations per query
— negligible compared to the solver runtime.

## Consequences

- All coordinates must be converted to radians before being inserted into or
  queried against a BallTree: `np.radians(coords)`.
- scipy.spatial dropped BallTree in scipy 1.14+ (see ADR-015). scikit-learn
  is required as an explicit dependency.
- Haversine distance is a straight-line great-circle approximation; it does not
  account for road networks or field access paths. This is an accepted limitation
  for the POC phase; road-network routing is explicitly deferred to post-POC.
- Any future code that performs geographic distance comparisons must use haversine,
  not Euclidean. This is enforced by a project-wide convention documented here.
