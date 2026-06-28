# Information Health Report — feasibility & scope plan

> Companion to [`PAPER_PLAN.md`](PAPER_PLAN.md) and [`NOVELTY_CHECK.md`](NOVELTY_CHECK.md).
> This is the **feasibility / scope** doc (why these metrics, what's risky). For the
> **reader's guide to each implemented score**, see
> [`HEALTH_REPORT.md`](HEALTH_REPORT.md). v1 + v2 are now implemented (see Status
> at the end).

## TL;DR verdict

**Feasible for the descriptive core, cheaply** — an Information Health Report is
fundamentally an *analysis of consumption* (each user's click history), and the
pipeline already ingests exactly the per-article metadata you would aggregate. It
is the same pattern as the existing `user_positions_from_clicks` /
`_alignment_report` code: take a user's clicked items, aggregate item attributes.
**Two of the nine requested items (emotional exposure, reporting-vs-opinion) need
new classifiers** and carry the most risk — defer them. **The 0–100 scoring is a
presentation choice, not a data problem**, and is the least defensible part unless
expressed as population percentiles.

## What it actually is (framing)

- It analyses **consumption** (the `history` / click matrix), *not*
  recommendations. Everything else in the repo scores a recommender; this scores a
  *reader's diet*. Same ingested data, different aggregation.
- **Governing caveat:** MIND `history` is clicks **within MSN News only**,
  **headlines only** (title + abstract, not full text), one snapshot. So honestly
  this is *"your MSN-News reading profile,"* **not** *"your information health."*
  Overclaiming the latter is the biggest defensibility/ethics risk.

## What the codebase already gives us (so the core is cheap)

Per article, aligned to the click-matrix columns (`rwe/mind.py::MINDData`):
`categories`, `subcategories`, `titles`, `outlets` (publisher via
`_outlet_from_url`), `political` (mask), `item_positions` (L/C/R lean from
`examples/classify_lean.py` / politicalBiasBERT). Per user: `dataset.matrix` (the
full consumption record). Reusable maths: `rwe/metrics.py` (Gini, coverage,
surprisal, the RQ3 UW measures), and the aggregate-over-a-user's-clicks pattern in
`user_positions_from_clicks`.

## Feasibility per requested item

| Item | Data source | From existing data? | Reliability | Verdict |
|---|---|---|---|---|
| What you consumed | history + metadata | ✅ direct | high | **v1** |
| Topic diversity | `categories` / `subcategories` (curated) | ✅ | high | **v1** † |
| Source diversity / concentration | `outlets` | ✅ | high | **v1 (strongest in general)** ‡ |
| Viewpoint L/C/R exposure | `item_positions` | ✅ | medium (weak axis, Spearman 0.27) | **v1, political subset** |
| Echo-chamber risk | consumption analogue of RQ3 UW metrics | ✅ (reuse `metrics.py`) | medium (same axis caveat) | **v1, caveated** |
| Blind spots / "missed" (descriptive) | under-represented categories/sources vs catalog | ✅ | high *as description* | **v1** |
| Reporting vs opinion | news/opinion classifier | ❌ enrichment | medium | **v2** |
| Emotional exposure (fear/outrage/…) | emotion classifier | ❌ enrichment | **low** | **v2** |
| Overall + sub-scores (0–100) | normalisation of the above | ✅ compute / ❌ defensible scale | low unless percentile | **reframe** |

> † **On MIND, run the report over a *full-catalog* ingest** (`mind_full.npz`, all
> topics). The `--political-only` `mind_text.npz` used for the RWE evaluation has a
> single category (`news`), so topic entropy is undefined and the Variety section
> blanks. ‡ **Source diversity is structurally `n/a` on MIND** — its URLs are MSN
> URLs, so the publisher isn't in the data; it's the strongest dimension only on a
> dataset that *carries* publishers (EB-NeRD, or an external source-map). The two
> "Variety" metrics, strongest in principle, are the weakest *on this dataset*; the
> political viewpoint metrics are what actually populate on the lean slice.

## Metrics triage

**Realistic & reliable now (v1):** topic diversity, source concentration,
viewpoint breakdown, echo-chamber proxy, descriptive blind-spots. All computable
from data already ingested with entropy / Gini / Herfindahl maths.

**Needs enrichment (v2):** emotional exposure (emotion classifier + a defined
rubric), reporting-vs-opinion (news/opinion classifier — URL `/opinion/`
heuristics are too sparse/unreliable on MIND), and *optionally* a stronger lean
axis (true outlet-lean labels from AllSides / Media Bias-Fact-Check) to firm up
the viewpoint metrics.

**Noisy / hard to defend:**
- **Emotional exposure** — emotion-from-headline is unreliable, classifiers
  disagree, and the taxonomy mixes *emotion* (fear, outrage, positive) with
  *register* (analysis). Easy to produce a number, hard to defend "Fear 38%."
- **Composite "Overall Score" (68/100)** — the single least defensible number; the
  cross-dimension weighting is a pure value judgment. Sub-scores are defensible
  only as **percentiles** ("more concentrated than 80% of readers").
- Reporting-ratio is only as good as its classifier; all lean-based numbers
  inherit the 0.27 axis noise.

## Proposed v1 metrics (planning-level definitions, not code)

Computed over user *u*'s clicked items (their row of `dataset.matrix`):

- **Topic diversity** — normalised Shannon entropy of the category distribution
  `p_c`: `H = −Σ_c p_c·log p_c / log(C)`; report top categories and the largest
  **gaps** `q_c − p_c` (catalog share `q_c` vs user share `p_c`, with `p_c ≈ 0`) as
  blind spots. Computed over *all* clicks.
- **Source concentration** — Herfindahl `HHI = Σ_o s_o²` over publisher shares
  `s_o`; plus **top-N share** (the example "82% from 4 publishers" = top-4 share)
  and distinct-outlet count.
- **Viewpoint balance** (political subset only) — L/C/R shares from
  `item_positions`; **cross-cutting share** = fraction of political clicks on the
  opposite side of the user's own mean; **echo-chamber proxy** = the consumption
  analogue of `uw_recs` (distance of the mean consumed position from the centre).
- **Scores → percentiles** against the evaluated user population, *not* absolute
  0–100; **omit (or clearly label as illustrative) the composite Overall Score.**
- **Reliability floor** — a `--min-user-clicks` threshold; users below it get a
  "not enough history" report, not a noisy score.

## Assumptions & risks (state plainly)

1. **Partial diet** (MSN-only, headlines-only, one snapshot) — describe, don't overclaim.
2. **No ground truth for "health"** — scores can't be validated against an outcome;
   this is a *descriptive/exploratory* tool, not a validated instrument.
3. **Value-laden framing** ("health", "risk", "blind spot") — defensible only as
   *descriptive + relative*, never prescriptive; "your reading is unhealthy" is a
   sensitive product claim.
4. **Sparse users** → unreliable reports → min-clicks floor required.
5. **Scope split:** topic/source diversity over *all* clicks; viewpoint/echo over
   the *political* subset only (lean is computed there).
6. **False precision** — single point scores invite over-reading; prefer
   percentiles + ranges.

## Honest framing / ethics note

Present as a **mirror, not a verdict**: "here is your reading diet vs the catalog
and vs other readers," with the partial-diet caveat visible. Avoid medicalised
language in any user-facing artifact, and avoid implying causation
("echo chamber" → harm). This mirrors the rest of the repo's honesty bar.

## Novelty note

Not novel as a concept — "news nutrition labels" / media-diet dashboards exist
(AllSides, Read Across the Aisle, various diversity tools). The contribution here
would be a *working, reproducible PoC on a public dataset, tied to the
diversification framework* — an auditing companion to the recommender side — not
the idea itself. (See `NOVELTY_CHECK.md` for the framing discipline.)

## Status & next step

**v1 spec'd and implemented** in `examples/health_report.py` — the exact formulas
(entropy/HHI normalisation, percentile reference set, min-clicks floor,
political-subset boundary) are frozen in its module docstring and covered by
`tests/test_health_report.py`. It computes the topic / source / viewpoint / echo
profile + percentile scores + insight & blind-spot lines from an ingested `.npz`,
with **no new models**:

```
python examples/health_report.py --npz mind_text.npz --sample 3
```

**v2 in progress.** Added: a **user-facing HTML report** (`health_report.py
--html`); a **reporting-vs-opinion** classifier (`examples/classify_register.py`,
zero-shot → `--register-csv` → Reporting Ratio); and an **experimental emotional
tone** classifier (`examples/classify_emotion.py`, zero-shot → `--emotion-csv` →
Emotional Balance + Attention profile, rendered labelled *experimental*). The
classifiers are Colab/GPU scripts (the `classify_lean.py` pattern); the wiring is
unit-tested on the fixture. **Still open:** a stronger lean axis (a *data-sourcing*
task — the ingest already supports `--lean-csv` / `--positions-csv`; the missing
piece is a license-clean outlet-lean table, not code). The composite "Overall" is
shown only as an explicitly *illustrative* unweighted average, per the caution above.

_Last updated: 2026-06-27._
