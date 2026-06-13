# Model-world divergence

The optimizer never plans over the world; it plans over a model of the world
assembled from delayed, partial, sometimes wrong reports. In a distributed
deployment (many sensor gateways, regional brokers, partitioned ingestion,
machines and crews acting autonomously) the gap between the implemented entity
model and physical reality is not an edge case -- it is the steady state. This
page catalogs the effects that gap produces, why each must be covered
explicitly, and which mechanism in fl-op covers it.

## The premise

Three facts make divergence unavoidable:

1. **The world changes without telling us.** An asset breaks, a field floods,
   a crew starts early. The model learns about it only when (and if) a report
   arrives.
2. **Reports travel through an unreliable, distributed pipeline.** They arrive
   late, out of order, duplicated, with skewed clocks, or never.
3. **Reports can be wrong.** Sensors fail, flags lie, values drift; a faithful
   copy of a wrong reading is still wrong.

A plan is therefore always a bet placed on a stale, imperfect projection. The
engineering goal is not to eliminate divergence (impossible) but to make it
**visible, bounded, and survivable**: every accepted distortion leaves a
record, every decision can be traced to the evidence it had, and every wrong
bet has a repair path.

## Design principles

- **Deterministic core, quarantined non-determinism.** All non-determinism is
  handled at the ingestion boundary (events, observations); from the snapshot
  inward the system is a pure function: identical canonical content yields an
  identical snapshot hash and an equivalent plan. This is what makes replays,
  audits, and tests possible.
- **Immutable decisions, explicit corrections.** Revisions are never edited;
  the system corrects itself by emitting a new revision plus a record of why
  (quality findings, corrective actions). History shows what was believed and
  when.
- **Evidence over silence.** Dropping, imputing, downsampling, withdrawing, or
  repairing always produces an artifact (finding, watermark, corrective
  action, outcome log). A consumer can always distinguish "the world is fine"
  from "we could not see the world".

## Effect catalog

Each row: the physical effect, what goes wrong if it is ignored, and the
covering mechanism.

| Effect | Real-world cause | If ignored | Covering mechanism |
|---|---|---|---|
| Late arrival | Gateway buffering, broker partitions catching up | Plans built on stale state look authoritative | Per-source **watermarks** stamped on every snapshot (`source_watermarks`): consumers see exactly up to when each source was visible; later data enters the next revision |
| Out-of-order delivery | Independent routes per gateway, retries | Trend rules read a scrambled series; newest reading is not last | Observation series are ordered by `observed-at`, never by arrival; arrival-order **timestamp regressions are flagged** as findings |
| Duplication / replay | At-least-once brokers, redelivery after timeout | An order cancelled twice, a reading double-counted | **Idempotent event application** keyed by `event-id`: duplicates mutate nothing and produce no revision |
| Clock skew | Unsynchronized station clocks, resets to epoch/future | "Future" readings dominate latest-value rules | Readings claiming timestamps beyond the skew tolerance ahead of planning time are **excluded with findings** |
| Report bursts | Reconnecting station flushing its backlog | Snapshots bloat; every flush forces a re-solve | **Time-window aggregation** bounds each series (representative reading per window, endpoints preserved); **convergence-aware debouncing** coalesces event bursts into one revision |
| Loss / gaps | Dead battery, broken uplink, missing file | An empty source reads as "no demand / no fleet" | **Missing-dataset findings** (`dq://dataset/source-file-missing`); stationary equipment that stops reporting is surfaced through monitoring (stale series, service-overdue) |
| Wrong values | Sensor faults, stuck gauges, miscalibration | Phantom service tasks; suppressed real alarms | **Statistical assessment**: outlier exclusion, fault discrimination, drift detection, quality-flag and confidence gating ([canonical-model.md](canonical-model.md)) |
| Wrong model rows | A rejected entity was actually fine; an accepted value was wrong | The model diverges permanently; no repair path | **`entity.corrected` events** upsert corrected rows; the snapshot is rebuilt and the plan reconciles |
| Divergence after dispatch | Asset breaks mid-execution, prognosis proves wrong | Frozen assignments point at dead assets; false alarms stay scheduled | **Corrective rescheduling**: asset-loss release, service withdrawal/escalation records, prognosis accuracy feedback ([current-implementation.md](../current-implementation.md)) |
| Decision-time staleness | Solve takes seconds; the world keeps moving | The "current" plan is already historical at publish | Rolling revisions per converged event window; freeze windows protect work already underway; every plan records its snapshot hash and watermarks |

## What the system deliberately does not promise

- **No global total order.** Sources are reconciled per series by observed
  time; cross-source simultaneity is never assumed.
- **No exactly-once world.** Idempotency makes at-least-once delivery safe;
  exactly-once is not required of any transport.
- **No silent self-healing.** Threshold tuning from prognosis accuracy is
  recommended in logs, never auto-applied; an operator stays in the loop.
- **No clairvoyance.** A snapshot is honest about its horizon: what arrived
  after its watermarks belongs to the next revision.

## Pointers

- Entity ontology and use cases: [optimization-ontology.md](optimization-ontology.md)
- Statistical assessment and monitoring: [canonical-model.md](canonical-model.md)
- Corrective rescheduling and the stream layer: [current-implementation.md](../current-implementation.md)
- Open gaps: [future-improvements.md](../future-improvements.md)
