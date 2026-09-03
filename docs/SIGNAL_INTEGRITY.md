# Signal Integrity & Registry Coverage — M1 (shipped) · Coverage-gap lens — M3 (shipped)

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

## Third-party factuality verdicts — publication is a separate decision from curation

`outlet_registry.csv` carries `factuality` / `factuality_source` / `factuality_asof` for 130
outlets, all MBFC. **Holding a verdict and publishing it are different decisions**, and the split
is enforced in code: `RWE_PUBLIC_FACTUALITY` (**default OFF**) governs every surface that could
put a verdict on the wire. These ratings are a third party's commercial product and we hold no
licence to redistribute them, so publication is an explicit operator act rather than a consequence
of the data existing.

The switch is defined ONCE — `outlet_registry.factuality_published()`, beside the data it governs
— and applied at each **serializer**, never in the UI:

  * `publisher_service` — the publisher profile's own verdict block.
  * `story_service._coverage` — the per-outlet verdict on a story's coverage rows, which is what
    the story page's Factuality breakdown counts.

A client-side hide would still ship the rater's data to anyone reading the payload; gating where
the payload is built means a disabled deployment transmits no verdict at all — not in a response,
not in a cache, not in a log. There is a test that greps the whole serialized profile for the
verdict string, and one that asserts a story's rows carry nothing with the gate off.

Both carriers also expose `factualityPublished`, which says **this deployment publishes
factuality** — not **this outlet is rated**. Without it the two absences are indistinguishable, and
the badge would render "Not rated" over 130 outlets we hold verdicts for: a label that lies, which
is the one thing this document exists to prevent. With it, absence keeps its single honest meaning
— the story breakdown can say "this deployment doesn't publish ratings" and "none of these outlets
is rated" as the different facts they are, and a genuinely unrated outlet still gets its explicit
"not rated".

The verdict always travels as `{value, source, asOf, ratingUrl}`, never as a bare level, on both
carriers — one shape, one client type (`FactualityRating`), so no surface can render a rating
without saying who issued it and when. On the story breakdown that becomes a credit line under the
chart, dated to the OLDEST verdict shown: understating freshness can only send a reader to the
source, while overstating it would put words in the rater's mouth.

Curation, provenance and linting keep working while publication is off, so re-enabling needs no
re-curation. `credibility` is a different column on a different scale (the clustering vote-gate's
input) and is never exposed either way.

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

**First verified pass (2026-07-26):** 18 leans filled, each checked against the outlet's own
AllSides page (domain-restricted search — never memory; two would have been WRONG from recall:
AllSides moved Daily Mail Right→Lean Right in 9/2025 and The Telegraph Lean Right→Center in
6/2026). 8 outlets confirmed **Not Rated** by AllSides stay blank (Al Arabiya, NDTV, Times of
India, France 24, Le Monde, Der Spiegel, ABC Australia, Sydney Morning Herald). The verification
log lives as a comment block beside the rows in `outlet_registry.csv`. Rated core: 55 → 73.
Leans are stamped at INGEST, so newly rated outlets' articles join lean filters/distributions as
the catalog rolls over — existing rows keep their stored (null) lean until they age out.

## M3 — Blindspots as a discovery lens (what M1's honest distributions unlock)

`/api/stories?blindspot=any|left|center|right` filters to stories with a **detected** coverage
gap (`blindspotSide` — a side with zero rated coverage while another side is well covered).
`blindspotSide` null means balanced-OR-unknown (an all-unrated story casts no votes) and never
matches — a gap is a counted finding, not a default. `blindspotFacets` on the envelope follows
the countryFacets discipline (counted under the other filters, before its own — the picker
offers only sides returning ≥1 story, and disappears entirely when no gaps are detected).
Surface: a "Coverage gaps" FilterSelect on the Stories browser + the `?blindspot=` deep link;
the home Blind spots module's "view all" lands on the pre-filtered lens. Deliberately a
DIMENSION of Stories, not a nav destination (the consolidation doctrine). Sequenced after M1
because the lens only means something over rated-vote distributions — shipped before it, it
would mostly have surfaced fabricated-center noise.

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
