# Dimensional coverage — the Viewpoint pilot

**Status:** superseded by the generic Measurement model — see **[ADR-001](ADR-001-MEASUREMENT-METADATA.md)**.
**Scope (as shipped in the pilot):** the Political Lean / Viewpoint dimension **only**.

> **Update.** This was the smallest viable first implementation of "coverage as a first-class concept".
> It validated the idea, and coverage has since been **generalized** into a reusable per-metric
> Measurement envelope (`{coverage, provenance, confidence?}`) attached on `Metric.measurement` and
> computed engine-side in `examples/measurement.py` — see **[ADR-001](ADR-001-MEASUREMENT-METADATA.md)**.
> Viewpoint's coverage numbers are unchanged; only *where they live* and *how they're computed*
> changed (the report-level `viewpointCoverage` field is retired). The Emotion dimension is the second
> to carry a measurement. This document is kept as the record of the pilot's motivation and reasoning,
> which still holds.

This is the smallest viable first implementation of "coverage as a first-class concept" (the RFC).
It is deliberately limited to one dimension so we can validate the architecture before generalizing.

## What "dimensional coverage" is

Every Information Health metric is computed over *some subset* of a reader's reads — the subset that
carries the signal the metric needs. **Dimensional coverage makes that subset explicit and countable.**

For the Viewpoint mix (left / center / right), the signal is an **authoritative political lean**,
resolved from the outlet registry (AllSides) in `examples/outlet_registry.py`. A read from an outlet
the registry doesn't know has a `NaN`/absent lean, so it **cannot** be placed on the spectrum. The
Viewpoint coverage answers, over a reader's political reads:

| Field | Meaning |
|---|---|
| `eligiblePoliticalReads` | political reads — the honest **denominator** for the mix |
| `authoritativeLeanReads` | of those, how many carry a **finite** outlet-registry lean |
| `unknownLeanReads` | `eligible − authoritative` — political reads **not represented** in the mix |
| `provenance` | the lean's source of truth (`outlet_registry`) |

It is computed by the pure leaf `examples/viewpoint_coverage.py` and attached **additively** to the
Measured report as `viewpointCoverage` (`GET /api/report`). It is **read-only**: it changes no
score, no viewpoint value, and no recommendation.

## Why coverage is **not** confidence

These are orthogonal, and conflating them is the bug this pilot fixes:

- **Coverage = scope.** *How much of your political reading could we place on the spectrum at all?*
  A read with an unknown outlet contributes **zero** to the mix — it is out of scope, not uncertain.
- **Confidence = certainty.** *Given the reads we could place, how sure are we of the resulting axis?*
  (`axisConfidence` — the top-2 softmax margin.)

Before this pilot the report **down-weighted unknown-lean reads toward zero via confidence-weighting**,
which silently dropped them from the mix — using a *confidence* mechanism to handle a *coverage* gap,
and leaving the reader with no idea how much of their political reading the mix actually reflected.
Coverage names that scope explicitly; confidence is untouched and still means what it always did.

Concretely: a reader whose political reads are 20 authoritative + 8 unknown has **full confidence**
in the axis derived from those 20, while having **partial coverage** (20 of 28). Both facts are true
and independent; the report now states both.

## Why this is intentionally limited to the Viewpoint dimension

The Viewpoint dimension is the **only** one with a large, binary, structural coverage gap (known vs.
unknown outlet), so it is where the missing concept actually bites and where the transparency win is
largest. Generalizing the envelope to every metric, adding *observation* coverage, and making the
composite coverage-weighted are all **deferred** until this pilot validates the value and we have
data on the real coverage distribution across readers. See the RFC's migration + risk sections.

Explicitly **out of scope** for this pilot (and unchanged by it):

- **No observation coverage** (what fraction of a reader's real reading we even see).
- **No generalization** to other dimensions.
- **No inferred political lean** — unknown outlets stay unknown; we never guess a lean. Growing
  coverage is a *data* task (add outlets to `outlet_registry.csv`; `examples/outlet_coverage.py` ranks
  the highest-volume unknowns), never a model task.
- **No change** to recommendation eligibility/ranking or Information Health scoring.

## Contract

> **Superseded shape.** The pilot attached a report-level `viewpointCoverage` field. Under
> [ADR-001](ADR-001-MEASUREMENT-METADATA.md) this now lives on the metric itself as
> `metrics[viewpointBalance].measurement` (`{coverage, provenance}`), alongside the new Emotion
> measurement. The paragraph below describes the pilot's original shape for historical reference.

`viewpointCoverage` was **additive and optional** on the Measured report: present only for a signed-in
Measured reader who has political reads; **absent** on estimate/demo reports, older payloads, and
readers with no political reading (so `response_model_exclude_none` omits it). Existing clients that
ignore it behave exactly as before.

*Related: the RFC (design proposal), `docs/CORPUS_ARCHITECTURE.md` (searchable ≠ recommendable),
`examples/outlet_coverage.py` (catalog-side registry-coverage diagnostic).*
