[Authoring manual](../authoring-domain-contracts.md) > 7. Runtime feasibility checks

# 7. Runtime feasibility checks and verification

Two distinct meanings of "feasibility":

- **Authoring feasibility** (Phase B + the validation ladder): can the engine
  model and plan this domain? Covered above.
- **Operational feasibility**: given a planned fleet, can a *specific new
  prospective task* be served, and at what margin? This is the `query-contract`
  command / `/feasibility` endpoint.

## 7.1 query-contract

Before accepting a new order, check it without running the full solver:

```bash
fl-op query-contract --data latest --schedule latest --order prospect.json
```

`prospect.json` describes one prospective task (operation type, location, area,
deadline, penalty, revenue). The response returns `feasible` plus the top-3
prime-mover + related-equipment candidates with `estimated_margin_eur` and a
`schedule_conflict_risk` (low/medium/high). It responds in seconds at production
scale with no solver call. The same evaluation is exposed over HTTP at
`POST /feasibility` (see [../usage.md](../usage.md#serving-api)).

## 7.2 Inspecting a plan

```bash
fl-op solve --data latest            # batch dispatch + KPIs + infeasible reasons
fl-op analyse --schedule latest      # served/rejected %, usage, KPIs, ASCII charts
fl-op demo --data latest             # full contracts -> snapshot -> batch -> stream story
```

Reproducibility check: a snapshot hashes its canonical content (excluding per-run
ids and finding wall-clocks), so identical inputs yield identical plans. If two
runs of the same data diverge, suspect non-canonical data leaking into the
snapshot.

---

Previous: [6. Costing methods](06-costing.md) | Next: [8. Evolution, fingerprints, and documentation hygiene](08-evolution-and-hygiene.md)
</content>
