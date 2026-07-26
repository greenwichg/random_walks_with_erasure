# Signal Integrity & Registry Coverage — M1 (shipped)

Every badge, distribution, filter, and publisher profile reads one signal layer. This milestone
made that layer TRUE (no display defaults survive on the feed path) and widened what it covers
(the registry can now hold locality without a lean). Chosen over new surfaces deliberately:
Publisher Intelligence was the first surface honest enough to expose that the others weren't.

## The nullable signal contract (feed path)

| Signal | Was | Now | Where |
|---|---|---|---|
| `lean` / `leanBucket` / `publisherLean` | 0.0 / "center" for unrated outlets | null (L2.2) | shipped previously; the pattern this milestone extends |
| `register` | **numeric P(reporting) string-compared → every enriched value collapsed to "reporting"** (opinion pieces labelled Reporting on story coverage rows); absent → "reporting" | numeric buckets via the engine's own 0.6/0.4 thresholds; absent → null | `discover._register` → `store._register_bucket` (ONE implementation — the serializer and the publisher tone module can never disagree) |
| `emotion` / `dominantEmotion` | fabricated all-neutral vector + "neutral" | null when no real vector | `discover._emotion` |
| `confidence` | fabricated 0.7 | null when unmeasured | `discover.feed_article_to_article` |

Web side: `Article` / `StoryCoverage` carry the signals as optional-null; badges render nothing
over defaults (`ArticleRow`, `ArticleAttributes`); the History emotion filter matches nothing
for an emotion-less read (still in "All" — the lean/country rule); history-insights counts
dominant emotions only over reads that carry one.

**Known remaining, deliberately untouched:** the recommendation path's serializer
(`api_server._register_enum`) maps non-finite → "mixed" for its own surface; the corpus it
serves is enriched, so practical exposure is low, and the recommendation surface is not changed
outside the evaluation framework.

## Locality-without-lean registry rows

`outlet_registry.csv` accepts a BLANK lean: the outlet resolves (canonical name, domain aliases,
home country/scope — curated public facts) while `Outlet.lean` is NaN — the same "unknown" the
scorer already speaks, so ingestion names the outlet and fills provenance country while L2.2
keeps its displayed lean null. `lint_registry` accepts blank and still rejects NaN-spellings and
out-of-range values; `outlets()` orders locality-only rows deterministically last;
`place_publishers` omits lean for them; `place_countries.registryPublishers` counts only RATED
rows (the web renders it as "Rated publishers" — the label must not lie).

**The registry grew 55 → 138**: the major international long tail (DE FR ES IT BE IE CH NL UA GB
CA AU NZ IN PK JP KR HK SG TH ID PH IL AE SA TR KE NG ZA EG MA BR AR MX) as locality-only rows.
**Zero leans were assigned** — a lean enters the registry only when verified against a public
rating source, never guessed.

### The curation workflow (operator loop)

```
python examples/outlet_coverage.py --db <prod-url> --top 50   # unknown outlets by article volume
# add locality-only rows for the top entries (country/scope are public facts)
# add a lean ONLY with a citable public rating (AllSides et al.)
python examples/outlet_coverage.py --lint                     # well-formedness gate
```

## Hardening rider (landed with the milestone)

- The last hardcoded English strings on live surfaces are localized (Stories count + pagination —
  which simply hadn't adopted the existing `common.*` keys — Discover count, Stories empty body):
  759 keys × 5 catalogs.
- `web/.eslintrc.json` existed nowhere despite a `lint` script and inline eslint-disables; the
  config is restored (`next/core-web-vitals`, zero findings) and CI now runs lint.
- The two long-standing `test_demo_account` failures were a REAL bug, not flake: the RC2.3/2.4
  improvement lifecycle machinery (added after the exhibit feature) annotated the exhibit's
  report for authed viewers — forking the frozen showcase per viewer and writing ledger rows
  from exhibit traffic. `_report_for` now returns `(report, is_exhibit)` and annotation skips
  exhibit reports entirely. A second latent bug fixed in the same pass: `state.demo_uid` never
  reset when the flag was unset, so a previous lifespan's exhibit uid could write-lock an
  ordinary user in the next process. The engine suite is fully green (1508) for the first time.
