[Authoring manual](../authoring-domain-contracts.md) > 10. Knowledge path: deep dive

# 10. Knowledge path: deep dive

You can author a correct pack using only Sections 1-9. To understand *why* the
model is shaped this way and to extend the engine, follow this path.

## 10.1 Internal documents (in reading order)

1. [../reference/optimization-ontology.md](../reference/optimization-ontology.md) - the
   full entity ontology, the use-case coverage matrix, and ontology gaps.
2. [../reference/canonical-model.md](../reference/canonical-model.md) - contract
   mechanics: how canonical fields, bindings, and the term vocabulary are
   declared and loaded.
3. [../reference/domain-mapping.md](../reference/domain-mapping.md) - the projection
   mechanics with the four shipped packs as worked examples.
4. [../algorithms/01-problem-formulation.md](../algorithms/01-problem-formulation.md) -
   the mathematical model: sets, decision variables, compatibility and
   time-window constraints, the margin objective, and why it is NP-hard
   (HFVRPTW + multi-resource + profit selection).
5. [../algorithms/02-solver-pipeline.md](../algorithms/02-solver-pipeline.md) - the
   eight-stage solver chain: enforcement -> compatibility matrix -> operation
   filter -> geographic clustering -> resource pre-allocation -> greedy warm start
   -> OR-Tools routing per cluster -> aggregation.
6. [../algorithms/03-learning-path.md](../algorithms/03-learning-path.md) - the staged
   reading list from integer programming through OR-Tools internals (a ~40-hour
   curriculum).
7. [../current-implementation.md](../current-implementation.md) - the authoritative
   description of delivered behavior; [../future-improvements.md](../future-improvements.md)
   for open work.

## 10.2 Source modules to read

| To understand | Read |
|---|---|
| Canonical model loading | `src/fl_op/contracts/canonical_model.py` |
| Mapping loading + binding shape | `src/fl_op/contracts/mapping_loader.py`, `src/fl_op/contracts/xopt.py`, `src/fl_op/mapping/bindings.py` |
| Missing-value handling | `src/fl_op/mapping/policies.py` |
| Registry + fingerprints + evolution | `src/fl_op/contracts/registry.py`, `fingerprint.py`, `evolution.py` |
| Cost resolution | `src/fl_op/solver/cost_rates.py` |
| Solver chain | `src/fl_op/solver/` (start at `inputs.py`, `feasibility.py`, `preprocessing.py`, `cluster_solver.py`, `routing_model.py`, `aggregator.py`) |
| Monitoring policy | `src/fl_op/snapshot/monitoring.py`, `assessment.py` |

## 10.3 External references

Contracts and formats:

- Open Data Contract Standard (ODCS): https://bitol-io.github.io/open-data-contract-standard/
- Apache Avro specification: https://avro.apache.org/docs/

Routing and scheduling theory (the problem class your packs are solved as):

- Toth, P. and Vigo, D. (eds.), *Vehicle Routing: Problems, Methods, and
  Applications*, 2nd ed., SIAM, 2014. The standard VRP reference.
- Gendreau, Laporte, Musaraganyi and Taillard (1999), "A Tabu Search Heuristic
  for the Heterogeneous Fleet VRP", *Computers & Operations Research* 26(12). The
  HFVRPTW base.
- Feillet, Dejax and Gendreau (2005), "Traveling Salesman Problems with Profits",
  *Transportation Science* 39(2). The profit-selection extension.
- Pillac, V. et al. (2013), "A review of dynamic vehicle routing problems", EJOR
  225(1). Background for rolling dispatch.
- Voudouris, C. and Tsang, E. (1999), "Guided Local Search", EJOR 113(2). The
  metaheuristic OR-Tools uses after the first solution.
- OR-Tools routing documentation: https://developers.google.com/optimization/routing

Condition-based maintenance (the monitoring policy):

- Jardine, Lin and Banjevic (2006), "A review on machinery diagnostics and
  prognostics implementing condition-based maintenance", *Mechanical Systems and
  Signal Processing* 20(7).

---

Previous: [9. Reference tables and command cheat-sheet](09-reference-tables.md) | Up: [Authoring manual index](../authoring-domain-contracts.md)
</content>
