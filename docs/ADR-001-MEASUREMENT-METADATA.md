# ADR-001 — Measurement Metadata Architecture

**Status:** Accepted · Phase 1 (Viewpoint coverage pilot) and Phase 2 (generic model) implemented.
**Scope:** how every Information Health metric reports the *scope* and *provenance* of its value.
**Supersedes:** the report-level `viewpointCoverage` field of the coverage pilot (folded into this model).

---

## Context

Every Information Health metric is computed over *some subset* of a reader's reads — the subset that
carries the signal the metric needs. That subset was invisible. The Viewpoint mix, in particular,
silently down-weighted reads from outlets without an authoritative lean rating toward zero (via
confidence-weighting), so a reader had no idea how much of their political reading the mix actually
reflected. Worse, the mechanism *conflated two different concepts*:

- **Coverage = scope.** *How much of your reading could this metric place at all?* A read whose outlet
  has no authoritative lean contributes **zero** to the Viewpoint mix — it is **out of scope**, not
  uncertain.
- **Confidence = certainty.** *Given the reads we could place, how sure are we of the result?*
  (Viewpoint's `axisConfidence` — the top-2 softmax margin.)

Using a *confidence* mechanism to handle a *coverage* gap is the bug. The
[coverage pilot](DIMENSIONAL_COVERAGE.md) fixed this for the Viewpoint dimension only, as a
deliberately scoped first step, and asked whether coverage should become a first-class concept across
the whole system. This ADR is the answer: **yes, via a generic, reusable Measurement envelope.**

## Decision

Introduce a single **Measurement metadata envelope** that wraps any metric's value:

```jsonc
{
  "dimension": "viewpoint",
  "coverage":   { "observed": 24, "eligible": 28, "basis": "political_reads" },
  "provenance": { "kind": "authoritative", "source": "outlet_registry" },
  "confidence": 0.74            // optional — omitted unless it genuinely represents uncertainty
}
```

- **coverage** — *scope*. `eligible` is the honest denominator (the reads the metric is *about*);
  `observed` is how many of those carried the signal; `basis` names the eligibility population.
  `observed ≤ eligible` always; `eligible − observed` are the reads **not represented** in the metric.
- **provenance** — *where the value comes from*. `kind` is `authoritative` (looked up from a source of
  truth) or `derived` (inferred by a model); `source` names that source of truth / model.
- **confidence** — *certainty* (optional). Orthogonal to coverage. **Absent** unless a value genuinely
  represents uncertainty *about the prediction* (see the implementation note below).

**Where it lives.** The envelope is attached **per metric** on `MetricModel.measurement`
(`web/types/domain.ts` `Metric.measurement`), not as a report-level field. This is what makes it
reusable: any dimension can carry one, keyed to its own metric card.

**Where it is computed.** In the **engine**, alongside the metric values, from the **same read
projection** — never a second read load. The pure leaf `examples/measurement.py` counts over the
reader's already-scored reads; `personalize._build_model` calls it on the exact `reads` list that
builds the augmented corpus and stores the result on the cached `PersonalModel`; the serialiser
`Backend._serialize_report` attaches each envelope onto its metric as the metric dict is built. The
retired pilot's separate `api_fastapi` annotation (which re-loaded the reads via `list_reads`) is
gone.

**It is additive and read-only.** A measurement changes no score, no metric value, no viewpoint
distribution, and no recommendation. A client that ignores `measurement` behaves exactly as before.
It is present only on the metrics that carry one, on a Measured report; absent on estimate/demo
reports, other metrics, and older payloads.

### Dimensions in Phase 2

| Metric | `basis` | `observed` | provenance | confidence |
|---|---|---|---|---|
| `viewpointBalance` | `political_reads` | political reads with a **finite** outlet-registry lean | `authoritative` / `outlet_registry` | omitted (certainty stays as `axisConfidence`) |
| `emotionalBalance` | `all_reads` | reads carrying an emotion vector | `derived` / current emotion model | **omitted** (see note) |

Viewpoint's coverage numbers are identical to the pilot's — this is a refactor of *where* the numbers
live and *how* they're computed, not a change to *what* they are. Emotion is the second dimension,
added to validate the model generalises.

## Consequences

- **Positive.** Coverage and confidence are finally distinct and both stated. Adding a dimension is
  now a small, uniform change (one `measurement.py` function + one row in the table above), not a
  bespoke field. One computation path, one read load, no `api_fastapi` annotation hook.
- **Negative / cost.** The report payload grows by a small envelope on up to two metrics. The
  frontend must map two dimensions through one render path (done: the report page reads
  `metric.measurement` for both Viewpoint and Emotion).
- **Neutral.** The report-level `coverage` (reads-vs-threshold volume) and `axisConfidence`
  (Viewpoint certainty) are unchanged and keep their existing meanings.

## Implementation notes

### Emotion confidence is intentionally omitted

The architecture **supports** `confidence`, but the current stored emotion outputs do not preserve
sufficient inference metadata to compute a defensible confidence value. The stored emotion vector is a
*distribution over labels*, not an *uncertainty estimate*. Deriving a "confidence" from the output
distribution's concentration (e.g. 1 − normalised entropy) would equate signal concentration with
model confidence — **different concepts**. Hidden View does not expose a value as "confidence" unless
it genuinely represents uncertainty about the prediction. **Therefore the field remains absent rather
than populated with a heuristic.** If a future emotion model preserves calibrated uncertainty, the
envelope already has the slot to carry it.

### Coverage uses the value's own read projection

`store.get_reads` returns the reader's scored payloads verbatim (oldest-first) — the exact input the
augmented corpus (and therefore the metric values) is built from. Coverage is computed from that same
projection, so scope and value never disagree about *which* reads are in play. One subtlety: a read
whose canonical URL matches a reference-corpus article reuses that catalog column, so its *value*
contribution inherits the catalog column's enrichment, while its *coverage* contribution counts the
read's **own** stored authoritative signal (finite lean / emotion vector). In production these align —
the scorer resolves the same outlet-registry lean at ingestion that the reference corpus carries — so
coverage honestly reflects what each read's stored scoring actually contained.

### Explicitly out of scope (unchanged by this ADR)

- **No observation coverage** — what fraction of a reader's *real* reading we even see. This model
  describes coverage *within the reads we have*, not sampling of reading we don't.
- **No inferred political lean** — unknown outlets stay unknown; coverage names the gap, never guesses
  a value. Growing Viewpoint coverage is a *data* task (add outlets to the registry), never a model
  task.
- **No coverage-weighted composite** — the overall Information Health score and recommendation
  eligibility/ranking are untouched.

## References

- `examples/measurement.py` — the generic Measurement leaf (pure).
- `examples/personalize.py` / `examples/api_server.py` — engine computation + attachment.
- `examples/api_fastapi.py` — `MeasurementModel` / `MetricModel.measurement` API contract.
- `docs/DIMENSIONAL_COVERAGE.md` — the Viewpoint pilot (Phase 1) that motivated this.
- `tests/test_measurement.py`, `tests/test_personalize.py` — the pinned behaviour.
