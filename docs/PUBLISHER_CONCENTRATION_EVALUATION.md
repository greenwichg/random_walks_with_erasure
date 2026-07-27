# Publisher concentration gate — evaluated and rejected (2026-07-27)

**Verdict: do not implement.** Measured against the live catalog it eliminates **zero** false merges
at every threshold tested, while removing 5.7–8.0% of covered articles. The proposal was mine; the
evidence does not support it.

## The proposal

Reject a cluster whose articles-per-publisher ratio exceeds a threshold, on the theory that a story
is many outlets covering one event while a press-release template is one outlet covering many events.
Proposed after `audit_clustering_change.py` showed `M.D. Sass LLC Makes New Investment…` at 101
articles from 4 publishers (a/p 25.2) surviving both the tokenisation gates and rarity weighting.

## How it was tested

`geoCoherence` — the share of a cluster's located members that agree on where the event happened —
is computed from provider-extracted locations and knows nothing about publishers. That makes it an
**independent** signal, so it can answer what the heuristic cannot answer about itself:

* **precision** — of the clusters a gate would remove, how many are independently bad?
* **recall** — of the clusters independently known to be bad, how many would it catch?

## Results

807 stories, 3,602 articles in stories. Articles-per-publisher distribution:

```
p50 = 1.00   p75 = 1.00   p90 = 1.38   p95 = 1.50   p99 = 2.29   max = 25.25
```

| threshold | stories removed | articles removed | % of covered | precision | recall |
|---:|---:|---:|---:|---:|---:|
| 2.5 | 7 | 288 | 8.0% | 0% | **0%** |
| 3.0 | 5 | 205 | 5.7% | 0% | **0%** |
| 4.0 | 5 | 205 | 5.7% | 0% | **0%** |
| 5.0 | 5 | 205 | 5.7% | 0% | **0%** |
| 6.0 | 2 | 146 | 4.1% | 0% | **0%** |
| 8.0 | 1 | 101 | 2.8% | n/a | **0%** |

**Recall is 0% at every threshold, and it is structural rather than a sampling accident.** The five
independently-bad clusters are:

| coherence | a/p | articles | publishers | cluster |
|---:|---:|---:|---:|---|
| 0.62 | **2.0** | 208 | 106 | Trump defends 50% tariffs on Canada |
| 0.65 | **1.0** | 34 | 33 | Mount Olympus … World Heritage |
| 0.67 | **1.5** | 17 | 11 | Trump Signals to Iran |
| 0.67 | **1.3** | 8 | 6 | Meghan Markle / MasterChef |
| 0.67 | **1.3** | 4 | 3 | 'fire cloud' wildfires |

Every one sits at a/p ≤ 2.0 — below the catalog's 99th percentile of 2.29. To reach the worst
cluster you would need a threshold at or under 2.0, which is beneath where 99% of all stories live.
The heuristic cannot be tuned to catch these; it is looking at the wrong axis.

## The clusters it *would* remove score perfectly

```
   a/p  arts  pubs   coh  title
  25.2   101     4     -  M . D . Sass LLC Makes New Investment in Gildan Activewear
   7.5    45     6  1.00  BetMGM bonus code NYPMAX1550
   5.6    28     5     -  Adam Silver pushing LeBron James into free agency decision
   5.3    16     3  1.00  When Does 'Ransom Canyon' Season 2 Premiere On Netflix?
   5.0    15     3  1.00  3M Open predictions: PGA Tour picks, odds, best bets
   2.5    78    31  1.00  Republicans tee up government funding bill months early
```

Every high-concentration cluster carrying a coherence score is at **1.00 — perfectly consistent**.
That is the opposite of the hypothesis, and in hindsight obvious: a promo code repeated 45 times
really is about one thing. These are **correctly clustered non-news**, not false merges.

## Legitimate stories the heuristic would harm

At threshold 2.5 it removes **"Republicans tee up government funding bill months early"** — 78
articles, 31 publishers, coherence 1.00. A major, broadly-covered political story. Immediately
behind it sit "He was fired by the White House right after being sworn in" (42 articles, 19
publishers, a/p 2.2, coherence 1.00) and "Trump Administration Is Said to Reach Broad Nuclear
Deal" (61/28, a/p 2.2).

Because p99 = 2.29, any threshold low enough to matter runs directly through the tail of ordinary,
well-covered news. There is no gap in the distribution to place it in.

## The category error

The heuristic targets a real problem, but not the one it was proposed to solve. Two distinct defects
exist and only one of them is a clustering defect:

* **False merges** — unrelated events glued together by single-linkage chaining. Detected by
  `geoCoherence`. All five known cases sit at a/p ≤ 2.0.
* **Non-news** — betting promos, TV air-date posts, press-release wires. Correctly clustered; they
  simply should not be in a news product. `geoCoherence` rates them 1.00 because they *are*
  coherent.

A concentration gate is a **content filter wearing a clustering costume**. Putting it in the
clustering admission path means every future legitimate high-repetition story is permanently at risk
from a threshold that was never about clustering. If BetMGM promo codes and 3M Open betting picks
should not be in the catalog, that belongs at ingestion — an outlet or category exclusion in
`examples/data/outlet_registry.csv` — where it is explicit, auditable, and cannot misfire on a
government-funding story.

## Compared with doing nothing

Doing nothing leaves 5 template clusters (205 articles, 5.7%) in the catalog. Implementing the gate
removes those same 205 articles, gains **zero** false-merge reduction, and adds a permanent
misfire risk on stories at a/p 2.0–2.5. A curated exclusion for the two or three offending outlets
achieves the removal exactly, with no threshold and no risk.

## Why chaining is the better next target

The single worst cluster — `Trump defends 50% tariffs on Canada`, 208 articles from 106 publishers,
coherence 0.62, members located across CN, CU, DJ, GB, IL, IR, OM, PH, SA, SG, US and YE — is
**5.8% of all covered articles in one cluster**, and it is a genuine false merge. It is produced by
single-linkage chaining: union-find merges A~B and B~C even when A and C share nothing. That one
mechanism produced the largest cluster, the worst coherence score, and the coverage distortion that
prompted the rarity-weighting experiment which cost 361 articles and was reverted. Concentration
cannot touch it at any threshold.

## Statistical caveats, stated plainly

* Only **91 of 807 stories (11%)** carry a coherence score — location coverage is ~18% and three
  located members are required. Precision/recall rest on that minority.
* There are only **5** known-bad clusters. Any rate estimated from 5 positives has wide error bars,
  and 0/5 does not by itself rule out modest recall.
* What does not depend on sample size is the structural finding: every known-bad cluster sits at
  a/p ≤ 2.0 while the catalog's p99 is 2.29. That is a statement about where the two populations
  live, not an estimate from a small sample.
* `geoCoherence` detects one *kind* of bad cluster. False merges may exist among the 716 unscored
  stories that this heuristic would catch. No evidence for that exists either way — and "cannot be
  ruled out" is not a reason to ship.

## What would have changed the verdict

Precision ≥ 80% with recall ≥ 25% at some threshold. Observed: 0% and 0%.
