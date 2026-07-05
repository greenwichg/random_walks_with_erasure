# Qbias activation — end-to-end validation & benchmark

The reference corpus is now **Qbias** (real AllSides outlets + population), activated by
configuration only — no research code changed. This records the evidence.

**Config used** (`deploy/qbias.env.example`): `RWE_PROFILE=qbias`,
`RWE_QBIAS=data/qbias_clean.csv` (from `prepare_qbias.py`), `RWE_SEED=0`, `RWE_N_USERS=1500`,
`RWE_MAX_ITEMS=3000`. The default profile stays `synthetic`, so a clean checkout and the test
suite are unaffected; Qbias is opt-in via these env vars.

## Validated behaviours (all ✅, driven against the running engine)

| # | Requirement | Evidence on Qbias |
|---|---|---|
| 1 | App starts on the cleaned Qbias corpus | `/api/health` → `profile=qbias, domain=news, eligibleReaders=1251`; startup 5.0 s |
| 2 | Onboarding shows canonical publisher names | `/api/outlets` → `Fox News, CNN, Washington Post, New York Times, Wall Street Journal, Reuters, …` (was `Outlet 14, Outlet 24, …`) |
| 3 | Reads merge into Qbias outlet buckets | 7 reads → canonical outlets, **7/7 = 100% present in the Qbias population** |
| 4 | Measured report vs the Qbias population | `/api/report` → `mode=measured, overall=38, reads=7`, computed against the Qbias readers |
| 5 | Recs + coach from the augmented Qbias corpus | 6 RWE-B recs (e.g. *New York Post*); coach cites the measured **38**/100, not the demo 58 |
| 6 | Anonymous/demo behaviour unchanged | no-auth → `mode=measured, overall=58` (demo reader); `?user=0` selector still works |

### Reads merging (previously-missing outlets now contributing)

Every outlet that silently ingested with `NaN` lean before now resolves to a canonical outlet
with a lean, and is present in the Qbias population:

```
New York Times       lean=-1   in_population=True
Washington Post      lean=-1   in_population=True     ← was NaN (lstrip('www.') bug)
Wall Street Journal  lean=+1   in_population=True     ← was NaN (lstrip + domain/name split)
Fox News             lean=+2   in_population=True
The Guardian         lean=-1   in_population=True     ← was NaN (domain/name split)
Associated Press     lean=+0   in_population=True     ← was NaN (domain/name split)
CNBC                 lean=-1   in_population=True     ← was absent from the table
reads matching reference corpus: 7/7 = 100%
```

Because these leans now populate, the real user's measured report gains **Viewpoint Balance (68)
and Echo Chamber (59)** — with a real viewpoint mix `left 57% / center 14% / right 29%` — which
were absent before. Measured metrics for a real user: **3 → 5**.

## Benchmark (synthetic → Qbias)

| Metric | synthetic | Qbias | Note |
|---|---|---|---|
| Startup | 4.5 s | 5.0 s | 1500 users × 3000 items; comparable |
| Memory (RSS) | 203 MB | 216 MB | +13 MB; scales with `N_USERS × MAX_ITEMS` |
| Onboarding outlets | `Outlet 14 …` | `Fox News …` | placeholder → real |
| Corpus outlets | — | 189 total (49 registry-canonical) | long-tail Qbias sources kept as-is |
| Real-user measured metrics | 3 | 5 | +Viewpoint, +Echo (via now-resolved leans) |
| Canonical outlets (registry) | — | 51 matched / 55 in registry | 87.4% of Qbias articles canonicalized |
| Reads matching reference | — | 100% (extension outlets) | 7/7 in the population |

### Cache behaviour

The per-`(user_id, reading_version)` augmented-model cache works as designed:

```
augmented-model build (cache MISS)   : 227 ms
reuse, same reading_version (HIT)    :   4 ms      (~57× faster)
rebuild after a new read (version++) : 210 ms
```

The scored-article cache (per canonical URL) also dedups a shared read across users (covered by
`test_score_with_cache_scores_once` and the store tests).

## Test suite

`python3 -m pytest -q` → **371 passed**, unchanged from before activation (the switch is config;
no test depends on Qbias, and the default profile is still synthetic).

## Remaining gaps in report quality

- **Reporting Ratio + Emotional Balance** are still n/a for a real user's reads — they need a
  register/emotion **enricher** on ingested headlines. This is independent of the corpus and is
  the recommended next milestone.
- **Open-Mindedness** needs measured cross-cutting reception (the continuous-improvement loop),
  so it remains n/a for real users until that lands.
- **Corpus article leans are coarse** (Qbias 3-point `bias_rating`) vs the reads' 5-point registry
  leans — directionally aligned (same centre bucketing), an optional later refinement.
- **A few registry alias gaps** (`Breitbart News`, `Time Magazine`, `Fox Business`) leave ~12% of
  Qbias articles on their raw label; data-only to close.
- **Population is simulated** over the real articles; a real click-log population (MIND) remains a
  larger future option.
