# Headline enrichment — validation

Ingested reads are now enriched with **register** (reporting-vs-opinion) and **emotional tone**
via the `ingest.Enricher` hook, so a real user's Reporting Ratio + Emotional Balance populate. A
deterministic offline baseline (`enrich.BaselineEnricher`) is the default; an optional
`LLMEnricher` plugs in behind the same interface (`RWE_ENRICH=llm` + a provider key). No research
module changed — the enricher only fills fields `health_report` already consumes, and the scorer's
per-URL cache stores the enriched read.

## Before vs after (real user, Qbias corpus, 6 headline reads)

| | metrics | Reporting Ratio | Emotional Balance |
|---|---|---|---|
| **Before** (no enrichment) | **5** — topic, source, viewpoint, echo, confidence | n/a | n/a |
| **After** (baseline) | **7** — + reportingRatio, + emotionalBalance | **33** | **100** |

Attention profile now populates too: `fear 0.07 / outrage 0.07 / analysis 0.19 / positive 0.11 /
neutral 0.56`. Only Open-Mindedness remains n/a (needs the measured cross-cutting-reception loop).

## Determinism, cache, performance

- **Deterministic:** two independent builds of the same reads produce an identical report (minus
  the timestamp). The baseline is pure lexical — no randomness, no network.
- **Cache (not re-enriched):** a workload of 18 reads over 6 distinct URLs ran **6 enrichments,
  12 cache hits → 67% hit rate**. Enrichment happens inside the scorer, so the existing
  per-canonical-URL cache serves every repeat unchanged.
- **Performance:** baseline enrichment is **~264 µs/headline**; `score()` per read goes from
  ~16 µs (no enricher) to ~259 µs (baseline) on a **cache miss**, and **0 additional** on a hit.
  Negligible at ingestion volumes.

## Test suite

`pytest -q` → **389 passed** (was 371; +18 enrichment tests: baseline register/emotion heuristics,
determinism, empty-headline n/a, cache-not-re-enriched, LLM parse + fallback, factory, and the
payoff that enriched reads populate Reporting + Emotional while un-enriched reads do not).

## Remaining gaps in report quality

- **Open-Mindedness** still n/a for real users — it needs measured reception of recommended
  cross-cutting reads (the continuous-improvement loop), not headline text.
- **Emotion is coarse + noisy** (headline-only, lexical) — a known low-confidence signal (see
  `classify_emotion.py`). The optional `LLMEnricher` improves it when a key is configured.
- **Population enrichment is synthetic** on Qbias, while a real user's reads are baseline-enriched;
  the percentile a real user is ranked at mixes the two distributions (can inflate a value, e.g.
  Emotional Balance 100 here). Aligning population + read enrichment is a later refinement.
