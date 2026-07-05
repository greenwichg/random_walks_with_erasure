# Population / read enrichment alignment — validation

`prepare_qbias.py` now baseline-enriches every Qbias headline (the **same** `enrich.BaselineEnricher`
used on ingested reads) into register/emotion sidecars, and the engine loads them (via the
profile's `register_csv`/`emotion_csv`, opt-in) so the reference population and real reads carry
one enrichment pipeline. No research module or algorithm changed; the engine change is a 4-line
data-source selection in `api_server.py` (the serving adapter, not a protected module).

## Same pipeline (objective achieved)

On the real Qbias corpus (21,754 articles):

- **500 / 500** sampled population articles' register now equals `BaselineEnricher.register(headline)`
  — the population is enriched by the same pipeline as reads. (Synthetic default: 1/500 — the
  mismatch this eliminates.)
- **Deterministic:** the aligned report built twice is identical; the baseline is pure, and
  enrichment no longer depends on the simulator's random draws.
- **No regression:** all seven real-user metrics remain present and valid; nothing crashes.
- **Opt-in:** omit `RWE_REGISTER_CSV` / `RWE_EMOTION_CSV` to fall back to synthetic enrichment.

## Preprocessing performance

Enriching all 21,754 headlines: **6.6 s** (total `prepare_qbias` run 7.3 s). One-time, offline.

## The percentiles moved — and the honest reason

Aligning to a **like-with-like** population is correct, but it revealed that the *coarse* baseline
enricher concentrates the population distribution:

| population reader-level | synthetic (before) | aligned baseline (after) |
|---|---|---|
| Emotional Balance | mean 0.654, **std 0.047** | mean 0.953, **std 0.042** |
| Reporting Ratio | mean 0.600, **std 0.062** | mean 0.603, **std 0.008** |

- **Emotional Balance** keeps a usable spread — it just shifts high (Qbias headlines are mostly
  neutral), so a charged reader lands in the low tail (percentile 4) and a typical reader near the
  top (86). Directionally correct.
- **Reporting Ratio** collapses to **std 0.008** — headline-only register gives almost every
  article ~0.6, so the percentile becomes hyper-sensitive (a charged/opinion reader → 0, a typical
  reader → 98). The "before" values (e.g. Emotional 100) were artifacts of the mismatch; the
  "after" values are like-with-like but volatile for Reporting because the baseline is coarse.

So the alignment is semantically right, but the aligned percentiles are only as good as the
enricher — and headline-only register is too flat to spread the population meaningfully.

## Test suite

`pytest -q` → **394 passed** (was 389; +5: sidecar format + baseline match, catalog-id alignment,
determinism, no-enrich skip, and the engine consuming the aligned enrichment).

## Remaining technical debt / gaps

- **Coarse baseline register** → concentrated population → degenerate Reporting Ratio percentiles.
  This is the dominant gap; a higher-granularity enricher (the optional `LLMEnricher`, or a richer
  baseline that reads the abstract, not just the headline) is needed to make the aligned
  percentiles *usable*, not just correct.
- **Population still simulated readers** over the real articles (a real click-log population, MIND,
  is a larger future option).
- **Open-Mindedness** remains n/a for real users (needs the cross-cutting-reception loop).
