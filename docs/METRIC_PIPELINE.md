# Metric Validation Pipeline (developer tool)

An **independent, additive** pipeline that recomputes every Information-Health metric from a reader's
Reading History and checks it against the **unchanged** production engine, in ten explicit stages. It
is a developer/CI tool — it changes nothing in the product and is not exposed on any web or API route.

It builds on [Study Mode](STUDY_MODE.md): the independent metric engine (Stage 5) *is*
[`examples/study_metrics.py`](../examples/study_metrics.py), reused verbatim. Study Mode explains the
formulas and the Raw-vs-Displayed distinction; this doc explains the pipeline that runs them as a
check against production.

## What it validates — two layers

| Layer | Reproducible from one reader? | Validated against | How |
|---|---|---|---|
| **RAW** | Yes (deterministic) | `health_report.compute` | Build a tiny corpus from the reads, drive the unchanged engine, compare raw values to the independent engine (tol `1e-9`). |
| **DISPLAYED** (percentile) | No — needs a population | `health_report.percentiles` | Over the **pinned six-persona population**, rank the independent raw values with an independent `rankdata` and compare to the engine's percentiles. |

The displayed score is a *percentile rank of the raw value against a population*, so it only has
meaning for a population of ≥ 2. A single `--history`/`--user` run validates the RAW layer only; the
six golden personas together form the population that makes the DISPLAYED layer testable.

## The ten stages

| # | Stage | Module | Notes |
|---|---|---|---|
| 1 | Extract | `extract.py` | golden persona / JSON file / live user (`Store.list_reads`) — read-only |
| 2 | Normalize | `normalize.py` | the single canonical `{"scored": {...}}` contract; coerce types, invent nothing |
| 3 | Data Quality | `quality.py` | rule checks with severities (ERROR/WARN/INFO) |
| 4 | Feature Engineering | `engine.py` | topic/publisher shares, positions, emotion means, register counts |
| 5 | **Independent Metric Engine** | `study_metrics.py` | reused **verbatim**; no production imports |
| 6 | Production Collection | `production.py` | builds a corpus and drives **unchanged** `health_report.compute` |
| 7 | Comparison | `compare.py` | raw + displayed + supplementary helper-parity |
| 8 | Drift | `drift.py` | vs the previous recorded run of the same dataset |
| 9 | Validation Report | `report.py` | text (developer) or JSON (CI) |
| 10 | Trend History | `history.py` | **isolated**, append-only JSONL — never the product's tables |

**Isolation guarantee.** Only Stages 6 and 7 import production (`health_report`), read-only. The
independent engine never calls production. Nothing here is imported *into* Dashboard, Analytics, Coach,
Recommendations, Story Service, Story Intelligence, Search, feed ingestion, multi-source, media, or any
API. `health_report.py` / `personalize.py` / `narrate_report.py` are untouched. A test
(`tests/test_metric_pipeline.py::test_early_stages_do_not_import_production`) enforces the boundary.

## Why Stage 6 builds a corpus (rather than calling raw helpers)

Topic/Source/Viewpoint/Echo have public raw helpers in `health_report`, but **Emotional Balance** and
**Reporting Ratio** are computed *inline* inside `health_report.compute` over the click matrix — there
is no standalone helper to call. So Stage 6 constructs a small in-memory `MINDData` in which each
reader is one row and each read is one column, then drives `compute` itself. A reader who "clicked" all
their own columns once makes the engine's matrix aggregates collapse to plain per-reader means, so this
reproduces production's raw values for **all six** percentile metrics *and* (over ≥ 2 readers) its
percentile-ranked displayed scores — **with no formula re-implemented in the pipeline.** This mirrors
`augmented_corpus.augment` (which appends to a base corpus); here the readers passed in *are* the
population.

## The Echo-Chamber inversion

`health_report.compute` ranks `percentiles(-echo)` — it ranks `1 − echo`, so a **higher displayed
score means LESS echo-chambered**. The pipeline reproduces this by negating the raw echo before
ranking (`compare._INVERTED_FOR_DISPLAY`). Reproducing this asymmetry — rather than papering over it —
is exactly the kind of subtle production detail the pipeline exists to pin down.

## Fidelity notes (documented, not hidden)

A stored Reading-History read is lower-fidelity than a freshly scored article; the pipeline validates
the engine's *formula* on Reading-History-fidelity inputs and says so:

- **Reporting Ratio** — production averages a classifier's continuous `P(reporting)`; a stored read
  keeps only the discrete register **label**, mapped here to `reporting → 1.0 / opinion|mixed → 0.0`.
  What we validate is the label-share proxy, the honest reproducible quantity.
- **Emotional Balance** — the emotion vector is headline-derived (experimental, the noisiest signal).
- **Reading Time** — no article body is stored, so minutes fall back to a title-word estimate. It has
  no `health_report` counterpart (the product assembles it in `api_server`), so it is reported
  **independent-only** at the raw layer, never asserted equal.
- **Open-Mindedness** — needs feed-impression data (cross-cutting click-through) a Reading History does
  not carry, so it is out of scope and reported n/a with that reason.

## Golden personas (the pinned population)

Committed fixtures in [`examples/metric_pipeline/golden/`](../examples/metric_pipeline/golden/), each a
reader whose diet isolates one behaviour:

| Persona | Isolates |
|---|---|
| `balanced` | a healthy diet — high on every dimension |
| `echo_chamber` | one-sided political diet → echo = 1, cross-cutting = 0 |
| `opinion_heavy` | mostly opinion → low Reporting Ratio |
| `technology` | narrow, apolitical → low Topic Diversity, n/a viewpoint |
| `single_publisher` | one publisher → Source Diversity floor (1.0) |
| `global_reader` | the aspirational diet — near the top of everything |

Because the raw metrics are deterministic, each persona's values are fixed — so run-over-run **drift is
a regression signal**: a non-zero drift means production math (or a fixture) moved.

## Usage

```bash
# validate the pinned population (raw + displayed); exit 0 on pass, 1 on any mismatch/drift/quality error
python examples/validate_metrics.py --golden all

# a single persona (raw layer only — population of one), as JSON for CI
python examples/validate_metrics.py --golden echo_chamber --report json

# an arbitrary Reading-History export (a {"reads":[...]} envelope or a bare list)
python examples/validate_metrics.py --history my_reads.json

# a live user via Store() (reads RWE_DB_URL or the default sqlite file)
python examples/validate_metrics.py --user 1

# record this run to the isolated trend history so the next run can detect drift
python examples/validate_metrics.py --golden all --record            # → examples/metric_pipeline/.runs.jsonl (git-ignored)
python examples/validate_metrics.py --golden all --record --history-file /path/to/runs.jsonl
```

`python -m metric_pipeline …` works too when run from `examples/`; the launcher above works from
anywhere. The **exit code** is 0 only when the raw, displayed, and helper-parity layers all match, no
metric drifted, and no reader had a structural (ERROR) quality finding — so it can gate CI.

## Tests

`tests/test_metric_pipeline.py` covers: the population passing at every layer, each persona's raw
match, the echo inversion, the independent-percentile re-derivation vs `health_report.percentiles`, the
data-quality rules, drift detection, and the production-isolation guard.
