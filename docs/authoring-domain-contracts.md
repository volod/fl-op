# Authoring a Domain Data Contract: Manual

This is the entry point to the manual for taking a real-world operations domain
(a fleet, a crew, a sensor network, a set of jobs) and turning it into a runnable
**domain pack** the fl-op engine can plan, without changing a line of engine code.

It is written so a careful first-time author can produce a complete, valid,
runnable domain pack end to end, and at every step it marks the path to the
deeper methodology for readers who want to understand *why* each artifact looks
the way it does. The chapters are sequential; each links to the next.

- **Audience:** data engineers, solution architects, and domain experts onboarding
  a new domain. No optimization background is needed to *author* a pack; the
  deep-dive chapter is optional.
- **What you produce:** a directory under `contracts/domains/<domain>/` with
  physical schemas (ODCS), canonical mappings, an optimization profile, a registry
  entry, and (optionally) a synthetic data generator.
- **What you can do afterwards:** generate or load data, build a reproducible
  planning snapshot, run both batch (`plan periodic`) and streaming
  (`plan rolling`) optimization, and answer ad-hoc feasibility queries
  (`query-contract`).

Throughout, the manual builds one running example, a hypothetical **`utilities`**
domain (power-line right-of-way vegetation management). It is not shipped in the
repo; it only gives every step a concrete artifact. The four shipped packs
(`drone_logistics`, `agricultural`, `construction`, `roadside`) are the real,
verifiable references.

## Contents

1. [The mental model: three layers](authoring/01-mental-model.md) - why the engine
   never reads your schema, and the three artifact kinds you author.
2. [Glossary and thesaurus](authoring/02-glossary-and-thesaurus.md) - core terms
   and the source-word -> canonical-concept translation table.
3. [Phase A: domain description methodology](authoring/03-domain-description.md) -
   the six elicitation questions, the worked `utilities` example, and the
   entity-mapping worksheet.
4. [Phase B: feasibility study](authoring/04-feasibility-study.md) - the
   ontology-fit checklist, required-binding coverage, use-case coverage, and the
   go/no-go gate.
5. [Phase C: step-by-step authoring](authoring/05-authoring-steps.md) - scaffold,
   ODCS schemas, mappings, profile, registry, data/generator, the validation
   ladder, smoke plan, evolution freeze, and the minimal-pack checklist.
6. [Costing methods](authoring/06-costing.md) - engine constants vs. data-driven
   cost rates, the resolution algorithm, the margin formula, and a worked example.
7. [Runtime feasibility checks and verification](authoring/07-runtime-feasibility.md) -
   `query-contract`/`/feasibility` and inspecting a plan.
8. [Evolution, fingerprints, and documentation hygiene](authoring/08-evolution-and-hygiene.md).
9. [Reference tables and command cheat-sheet](authoring/09-reference-tables.md) -
   `missingValuePolicy`, `planningUse`, quantity kinds, the semantic-term
   vocabulary, and the command cheat-sheet.
10. [Knowledge path: deep dive](authoring/10-knowledge-path.md) - internal docs,
    source modules, and external references.

## Companion documents

- Contract mechanics: [reference/canonical-model.md](reference/canonical-model.md)
- Projecting a domain: [reference/domain-mapping.md](reference/domain-mapping.md)
- The ontology and use-case coverage: [reference/optimization-ontology.md](reference/optimization-ontology.md)
- The math and the solver: [algorithms/01-problem-formulation.md](algorithms/01-problem-formulation.md),
  [algorithms/02-solver-pipeline.md](algorithms/02-solver-pipeline.md)
- Structured reading list: [algorithms/03-learning-path.md](algorithms/03-learning-path.md)
- Command-by-command CLI walkthrough: [usage.md](usage.md)

Start at [1. The mental model](authoring/01-mental-model.md).
</content>
