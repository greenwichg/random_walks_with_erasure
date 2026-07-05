# Enricher quality upgrade — validation

The deterministic baseline enricher was too coarse on headlines alone: register (P(reporting))
piled at the 0.60 default, collapsing the population's Reporting Ratio spread and making its
percentiles hyper-sensitive (the alignment-milestone finding). This milestone **feeds the enricher
richer text** — headline **+ subtitle + article description/abstract** — through the *same*
`enrich.combine_text` combiner for both the reference corpus (Qbias `heading` + `text`) and
ingested reads (`og:title` + `og:description`). No interface, algorithm, or protected module
changed; the enricher simply scores more text, and degrades gracefully to headline-only when
nothing else is present.

Changed (product layer only): `enrich.py` (`combine_text`; both enrichers consume it),
`ingest.py` (`RawRead.subtitle/description`), `api_fastapi.py` (`ReadInput.subtitle/description`),
`prepare_qbias.py` (enrich `heading + text`), `extension/content.js` + `background.js` (capture &
forward `og:description`). Protected modules (`health_report.py`, `rwe/`, `simulate_users.py`,
`narrate_report.py`) untouched.

## Before vs after — real Qbias corpus (21,754 articles; population n_users=1500, max_items=3000, seed=0)

"Before" = headline-only enrichment (the prior behavior); "after" = headline + abstract. Same
cleaned corpus, same population build, same seed — only the enrichment text differs.

### 1. Variance of Reporting Ratio across the reference population (per-user) — **increased**

| statistic            | before (headline) | after (headline+abstract) |
|----------------------|-------------------|---------------------------|
| std                  | 0.00870           | **0.01940** (2.23×)       |
| variance             | 0.000076          | **0.000376** (4.97×)      |
| IQR (p75−p25)        | 0.0100            | **0.0238**                |
| distinct values      | 216               | **400**                   |
| mean                 | 0.6056            | 0.6401                    |

Article-level register (the mechanism): std **0.0281 → 0.0687** (2.44×); distinct values 11 → 22;
share pinned within ±0.02 of the 0.60 default **84.4% → 41.7%**.

### 2. Reporting Ratio percentile stability — **improved (more stable)**

| statistic                                   | before | after |
|---------------------------------------------|--------|-------|
| max percentile jump per +0.01 raw ratio     | 48.7 pts | **23.1 pts** |
| population share within ±0.005 of the median | 45.6%  | **24.7%** |

Before, a user whose true ratio moved 0.01 could swing ~49 percentile points (a near-step ranking
function caused by the pile-up); after, ~23 — the ranking is roughly 2× less brittle.

### 3. Emotional Balance — **stays healthy, no degeneracy**

| statistic | before | after | Δ |
|-----------|--------|-------|---|
| mean      | 0.9668 | 0.9146 | −0.052 |
| std       | 0.0358 | 0.0542 | +0.018 |
| median    | 0.9722 | 0.9167 | −0.056 |

Emotional Balance shifts **down ~5 points** and spreads slightly: abstracts carry more emotional
vocabulary than headlines, so more fear/outrage cues surface (balance = 1 − (fear+outrage) shares).
This is small, explainable, and **not a degeneracy** — the metric stays high (~0.92) and its own
percentile resolution slightly improves (wider std). It did not collapse or explode.

### 4. Determinism — **preserved**

- register array identical on rebuild: **True**
- population Reporting Ratio identical on rebuild: **True**
- enrichment sidecar files byte-identical on re-run: **True**

The baseline is pure lexical scoring; richer text adds no randomness.

### 5. Cache behavior — **unchanged**

Two reads of the same canonical URL (with a description present) → `enrich()` called **once**; the
cached read carries the richer enrichment. The cache key (canonical URL) and storage are untouched;
enrichment still runs inside the scorer and is stored per article.

### 6. Preprocessing speed (one-time, full 21,754-article corpus)

| path                | time   | per article |
|---------------------|--------|-------------|
| headline-only       | 5.23 s | 0.24 ms     |
| headline + abstract | 19.50 s | 0.90 ms    |

~3.7× slower (abstracts are ~8× the text of a headline), but <20 s for the whole corpus, run once
at preprocessing time. No per-read latency change (a read enriches one article).

## Distribution (population Reporting Ratio)

```
BEFORE (headline-only)                       AFTER (headline+abstract)
0.58-0.60 | ###########  262                 0.58-0.60 | ##   25
0.60-0.62 | ########################  921    0.60-0.62 | ###########  132
0.62-0.64 | ##   56                          0.62-0.64 | ########################  479
0.64-0.66 |  2                               0.64-0.66 | #####################  440
0.66-0.68 |  1                               0.66-0.68 | #######  138
                                             0.68-0.70 | ##   34
(one dominant spike; ranking degenerate)     (spread across ~5 bins; ranking usable)
```

## Test results

Full suite: **400 passed** (`python3 -m pytest -q`). New tests this milestone: richer-text +
graceful-fallback for reads (`tests/test_enrich.py`) and heading+abstract population enrichment
verified through the engine (`tests/test_prepare_qbias.py`).

## Remaining technical debt

- **Register is still lexical and coarse.** 0.019 population std is a real improvement but percentiles
  remain somewhat concentrated (23 pts / 0.01). The abstract is the bigger lever than the headline;
  the optional `LLMEnricher` (already wired behind the same interface) would spread it further when a
  provider key is configured. The deterministic baseline stays the default.
- **Emotion lexicon precision.** More text raises recall but also false hits; the ~5-point Emotional
  Balance shift is acceptable but the lexicon could be tightened (stemming, negation handling).
- **Enrichment throughput.** 0.9 ms/article via per-keyword regex; a single compiled alternation per
  bucket would cut preprocessing time if the corpus grows.
- **Abstract availability at read time** depends on the page exposing `og:description`; when absent,
  reads correctly fall back to headline-only (spread reverts toward the baseline for those reads).

## Recommendation for the next milestone

Proceed to the **Open-Mindedness feedback loop** — the last unpopulated real-user metric (users are
at 7/8). It needs measured reception of recommended cross-cutting reads, which is behavioral data the
enricher does not supply. Enrichment quality is now sufficient for Reporting Ratio to rank
meaningfully; no further enrichment work is required before that milestone.
