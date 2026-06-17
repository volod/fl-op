# Current Implementation

How the system works today. This is the entry point to the implementation guide;
the detail lives in focused section pages under [implementation/](implementation/),
linked from the contents below.

For the contract layer see [canonical-model.md](reference/canonical-model.md) and
[domain-mapping.md](reference/domain-mapping.md); for the entity ontology, use
cases, and algorithm overview see
[optimization-ontology.md](reference/optimization-ontology.md); for why and how
the system survives the gap between its entity model and the physical world see
[model-world-divergence.md](reference/model-world-divergence.md); to author a new
domain pack end to end see
[authoring-domain-contracts.md](authoring-domain-contracts.md).

## At a glance

The engine consumes a **domain-neutral canonical model** only; every physical
domain projects onto it through a mapping pack, so one solver serves all domains.
Four packs are runnable end to end today: drone logistics (default),
agricultural custom services, construction earthworks, and roadside
infrastructure. The same canonical state runs in both **batch (periodic)** and
**stream (rolling)** mode, with reproducible snapshots, data-driven cost rates,
condition-based monitoring, and governed plan outputs.

## Contents

1. [Architecture and domain packs](implementation/01-architecture.md) - the three
   layers (canonical model / domain packs / engine), the four runnable packs, and
   single- vs shared-fleet domain selection.
2. [Data and contracts](implementation/02-data-and-contracts.md) - dataset and
   schema generation, contract validation, structural + semantic evolution
   gating, multi-domain staging, and profile/policy composition.
3. [Planning pipeline](implementation/03-planning-pipeline.md) - mapping source
   rows to canonical entities, unit normalization, observation assessment,
   monitoring-derived service tasks, snapshot build, and plan publication.
4. [Solver chain](implementation/04-solver-chain.md) - the shared OR-Tools chain:
   enforcement and pre-filters, compatibility matrix, operation filter, depot
   clustering, CP-SAT pre-allocation, greedy warm start, per-cluster routing
   (cost/time objectives, windows, loads, reloads, pickups, LNS), and aggregation.
5. [Rolling dispatch](implementation/05-rolling-dispatch.md) - binding-driven
   event application, per-pass coverage geometry, idempotency/dedup,
   freeze/carry/re-solve revisions, corrective rescheduling, and watermark-driven
   replanning.
6. [Quality and completeness artifacts](implementation/06-quality-artifacts.md) -
   bundle-feasibility summaries, dataset/observation quality findings, watermarks,
   and cross-run error-rate trends.
7. [Parameter tuning and experiment tracking](implementation/07-tuning-and-tracking.md) -
   the Optuna study, tuned-overlay promotion (shared and scoped), and optional
   MLflow logging.
8. [Schema evolution and CI](implementation/08-schema-evolution-and-ci.md) -
   reviewed contract/metadata baselines, the version-bump policy, and the CI gate.
9. [Artifact provenance and registry](implementation/09-provenance-and-registry.md) -
   the content-hash primitive, snapshot identity, unified caches, manifests, and
   the read-only artifact registry.
10. [Serving](implementation/10-serving.md) - the HTTP API, event-bus ingestion
    with exactly-once semantics, and the long-running plan watcher.

For the command-by-command CLI walkthrough with sample inputs/outputs, see
[usage.md](usage.md).
</content>
