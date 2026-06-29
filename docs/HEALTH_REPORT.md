# The Information Health Report — metric guide

A plain-English reference for every number in the per-user **Information Health
Report** (`examples/health_report.py`): what it measures, how it's computed, how
to read it, and what it can't tell you.

This is the *reader's guide*. The **feasibility analysis** (why these metrics,
what's risky) lives in [`HEALTH_REPORT_PLAN.md`](HEALTH_REPORT_PLAN.md); the
**exact formulas** are frozen in the `health_report.py` module docstring (the
source of truth — this guide carries the intuition, the code carries the math).

---

## What the report is — and the one caveat that governs everything

It turns a user's **click history** into a profile of their reading diet:
topic and source variety, political balance, and (optionally) reporting-vs-opinion
and emotional tone. It's the *auditing* side of the project's diversification work.

> **Read this first.** The data is clicks **within MSN News only**, scored from
> **headlines** (title + abstract, not full articles), at one point in time. So
> the report describes *"your MSN-News reading,"* **not** your whole information
> diet. It is a **mirror, not a verdict** — present it as "here is your diet vs
> the catalog and vs other readers," never as a health diagnosis.

## How the scores work

- **Scores are percentiles**, 0–100, vs. the other readers in the dataset — e.g.
  *Source Diversity 41* means "more concentrated than ~59% of readers," not an
  absolute grade. (A fixed 0–100 rubric would be a value judgment; percentiles
  are defensible.)
- **Higher is always "healthier"** for every score below — more diverse, more
  cross-cutting, **less** echo-chambered, more reporting, calmer.
- **Overall Score** is an *unweighted, illustrative* average of the available
  sub-scores. Treat it as a rough roll-up, not a measurement — weighting the
  dimensions against each other is a value judgment we deliberately don't make.
- **Two tiers of reliability — know which you're reading.** The **Variety / Tone &
  substance** metrics (Topic Diversity, Source Diversity, Reporting Ratio) are
  *axis-independent* and stand on their own. The **Balance & openness** metrics
  (Viewpoint Balance, Echo Chamber Score, Open-Mindedness) all rest on the report's
  **MIND text-lean axis, which is a weak proxy** (Spearman ≈ 0.27 / ≈ 0 vs human
  labels; `RESULTS.md` Limitation 1), so they are **directional signals, not
  measurements.** (A *behavioral* left↔right axis did validate on a different dataset
  — Reddit Politosphere, `lean_corr = 0.65` — but the report can't use it: MIND
  carries no behavioral lean per article.) Emotional Balance / Attention are
  separately **experimental** (headline emotion is noisy). So a correctly-computed
  number is not the same as a validated one — the report tells you *what it measured*,
  tiered by how much to trust it.
- **Reliability floor:** users below `--min-clicks` get no scores. The political
  metrics (Viewpoint, Echo, Open-Mindedness) need `--min-political` political
  clicks and use the **political subset** only; topic and source use **all** clicks.
- **Political-engagement share** is shown as *context, not a score* — what fraction
  of your reading is political at all. It keeps the viewpoint scores honest: a
  5%-political reader and an 80%-political reader are very different people.
- **Feed it a full-catalog ingest, not the political slice.** The report profiles a
  whole *reading diet*, so it needs an `.npz` ingested over **all** topics (the
  notebook builds `mind_full.npz`). The `--political-only` `mind_text.npz` used for
  the RWE evaluation collapses every item to the single category `news`, which makes
  Topic Diversity undefined and "100% political" true by construction — the viewpoint
  metrics still work there, but the Variety section goes blank.
- **The two metric families pull in opposite directions — that's expected.** On a
  full catalog, Topic/Source/Reporting/Emotion apply to everyone, but most readers
  click *little or no* political news, so their Viewpoint / Echo / Open-Mindedness
  scores are **legitimately `n/a`** (not broken — those metrics only mean something
  for political readers). To see a reader who exercises *every* dimension, sample with
  `--require-political` (the notebook does): it draws users above the political-click
  floor, who still have diverse topics so the other metrics stay meaningful.

## Quick reference

| Score | Measures | Higher means | Needs |
|---|---|---|---|
| Topic Diversity | spread across news topics | broader reading | — |
| Source Diversity | spread across publishers | more, more-even sources | — |
| Viewpoint Balance | reading across the political centre | more cross-cutting | lean axis ✦ |
| Echo Chamber Score | one-sidedness of political reading | **less** echo-chambered | lean axis ✦ |
| Reporting Ratio | reporting vs opinion/editorial | more straight reporting | classifier † |
| Emotional Balance | calm vs charged tone | calmer reading | classifier †‡ |
| Attention Profile | mix of fear/outrage/analysis/positive | *(descriptive, not scored)* | classifier †‡ |
| Open-Mindedness | clicking the other side when shown | more cross-cutting clicks | impressions ◆ ✦ |

† needs the GPU classifier run · ‡ **experimental** · ◆ needs `--behaviors` (MIND impressions)
· ✦ **rests on the report's weak MIND text-lean axis** (Spearman ≈ 0.27 / ≈ 0 vs
human labels) — directional only; `RESULTS.md` Limitation 1

---

## The metrics, one by one

### Topic Diversity
**Answers:** do you read across many topics, or a few?
**How:** normalised entropy of your category mix — `H = −Σ pₖ·ln(pₖ) / ln(C)`,
where `pₖ` is your share of category *k* and *C* is the number of categories in
the whole catalog. `0` = a single topic; `1` = spread evenly across every topic.
Reported as a percentile.
**Read it:** high = you graze widely; low = you live in a couple of topics.
**Limit:** uses MIND's category taxonomy (coarse — "news," "sports," "health," …);
computed over all your clicks.

### Source Diversity
**Answers:** how many distinct publishers do you read, and how evenly?
**How:** the **effective number of sources** `= 1 / HHI`, where the Herfindahl
index `HHI = Σ sₒ²` over your publisher shares `sₒ`. Reading 5 sources equally
gives an effective 5; reading mostly one gives ≈1. Reported as a percentile.
**Read it:** high = a balanced spread of outlets; low = a few publishers dominate.
**Limit:** articles whose publisher couldn't be parsed from the URL are excluded.
**On MIND this is always `n/a`** — MIND's URLs are *MSN* URLs, so the original
publisher isn't in the data. It needs an external source-map (e.g. EB-NeRD, which
carries publishers, or a resolved MSN-provider table); the report says so on the
bar rather than showing a bare blank.

### Viewpoint Balance
**Answers:** does your political reading cross the centre, or stay on your side?
**How:** **cross-cutting share** = the fraction of your political reading that sits
on the *opposite* side of the centre from your own average position. Reported as a
percentile.
**Read it:** high = you regularly read the other side; low = you mostly read your
own side.
**Limit (read this):** political subset only, and it rests on the report's **MIND
text-lean axis, a weak proxy** (Spearman ≈ 0.27 / ≈ 0 vs human labels — it conflates
*topic* with *stance*; `RESULTS.md` Limitation 1). A *behavioral* left↔right axis
*did* validate on other data (Reddit Politosphere, `lean_corr = 0.65`), but MIND
carries no behavioral lean per article, so the report falls back to the weak text
axis. So treat this score as a **directional, suggestive signal, not a measurement**
— the bar position is only as meaningful as the axis under it.

### Echo Chamber Score
**Answers:** how one-sided is your political reading?
**How:** `echo = 1 − 2·min(L, R)/(L + R)` over your left/right political reading —
`0` when perfectly balanced, `1` when entirely one side. The score is the
percentile of `1 − echo`.
**Read it:** **higher = LESS echo-chambered** (more balanced). A low score flags a
one-sided diet. *(The name reads backwards to some — high is good here.)*
**Limit:** same political-subset and **weak-text-lean-axis** caveat as Viewpoint
Balance — directional only.

### Open-Mindedness ◆
**Answers:** when the feed actually *shows* you the other side, do you click it?
**How:** of the **opposite-side** articles in your impressions (what the feed put
in front of you), the fraction you clicked — a cross-cutting click-through rate,
reported as a percentile. Needs `--behaviors` (the MIND impressions); `n/a` if you
were never shown opposite-side political articles.
**Read it:** high = you engage the other side when offered; low = you skip it even
when it's right in front of you.
**Why it's distinct:** this is the report's one **agency** signal — it separates
*"the algorithm didn't show me the other side"* from *"I chose not to read it,"*
the supply-vs-demand split at the heart of bridging recommenders. Every other
score describes your *diet*; this describes your *choice*.
**Limit:** needs impressions; rests on the **weak MIND text-lean axis** (see
Viewpoint Balance — directional only); only defined for users actually shown
opposite-side political articles.

### Reporting Ratio †
**Answers:** how much of your reading is straight reporting vs opinion/editorial?
**How:** a text classifier scores each article `P(news report)`; the metric is the
mean over your clicks, as a percentile. Populated only when you run
`classify_register.py` and pass `--register-csv`.
**Read it:** high = mostly hard news; low = mostly opinion/commentary.
**Limit:** zero-shot classifier on **headlines** — approximate, not authoritative.

### Emotional Balance †‡
**Answers:** is your reading calm or emotionally charged?
**How:** `1 − (fear + outrage) share` of your reading's emotional tone, as a
percentile. Populated only with `classify_emotion.py` + `--emotion-csv`.
**Read it:** high = calmer/more analytical; low = heavy on fear/outrage.
**Limit: experimental — the single noisiest metric.** Emotion-from-headline is
unreliable and the buckets mix *emotion* (fear, outrage, positive) with *register*
(analysis). Treat as a rough signal, never a precise figure.

### Attention Profile †‡
**Answers:** what's the emotional *mix* of your reading?
**How:** the average per-bucket share across your articles —
fear / outrage / analysis / positive / neutral. **Descriptive, not scored.**
**Read it:** a breakdown like "fear 38% · outrage 19% · analysis 28% · positive 15%."
**Limit:** same experimental caveat as Emotional Balance; rendered labelled
*experimental*.

### Biggest Insight & Blind Spot *(narrative)*
- **Biggest Insight** — your sharpest concentration fact: *"X% of your reading came
  from your top N publishers"* (the top-N publisher share + distinct-source count).
  This is the **strongest, most defensible** line in the report — concrete and
  often genuinely surprising.
- **Blind Spot** — the topic you most under-read relative to the catalog: the
  category with the largest gap between its catalog share and your share.

---

## Honest framing (please keep)

The metrics labelled **experimental** (Emotional Balance, Attention Profile) and
everything resting on the **lean axis** (Viewpoint, Echo, Open-Mindedness) are
*directional signals*, not measurements. On the axis specifically: the report uses
the **MIND text-lean axis, a weak proxy** (Spearman ≈ 0.27 / ≈ 0 vs human labels;
`RESULTS.md` Limitation 1), so the political sub-scores reflect a *weak proxy*, not a
trustworthy left↔right position. (A behavioral axis *did* validate elsewhere — Reddit
Politosphere, `lean_corr = 0.65` — but it isn't available for MIND articles, so it
can't lift these scores.) Present them as such. The report is a **descriptive,
exploratory** tool — there is no ground truth for "information health," so the scores
can't be validated against an outcome. The numbers are *correctly computed from the
data* (tested end-to-end); that is not the same as being *validated measurements*.
Avoid medicalised or prescriptive language in anything user-facing.

## Map: metric → code

| Metric | Where |
|---|---|
| Topic Diversity | `examples/health_report.py` · `normalized_entropy` |
| Source Diversity | `examples/health_report.py` · `hhi` / `effective_number` / `top_n_share` |
| Viewpoint Balance | `examples/health_report.py` · `cross_cutting_share` |
| Echo Chamber Score | `examples/health_report.py` · `echo_score` |
| Open-Mindedness | `examples/health_report.py` · `selective_exposure_array` (`--behaviors`) |
| Political-engagement share | `examples/health_report.py` · `compute` (political mask) |
| Reporting Ratio | `examples/classify_register.py` → `compute(register=…)` |
| Emotional Balance / Attention | `examples/classify_emotion.py` → `compute(emotion=…)` |
| scores → percentiles | `examples/health_report.py` · `percentiles` |
| HTML rendering | `examples/health_report.py` · `render_html` |

_Last updated: 2026-06-29 (axis caveat corrected — the Viewpoint/Echo/Open-Mindedness
scores rest on the report's **weak MIND text-lean axis** (Spearman ≈ 0.27 / ≈ 0); a
behavioral axis validated elsewhere (Politosphere, `lean_corr = 0.65`) but isn't
available for MIND)._
