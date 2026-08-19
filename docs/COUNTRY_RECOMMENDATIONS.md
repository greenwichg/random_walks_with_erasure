# For You country — putting one country's news first, and the measurements that shaped it

**Status: adopted and verified 2026-08-19.** Feature `705eb68`, content-level matching `104fa64`,
country-first ordering `125482a`, demonyms `7a44028`, backfill labelling `efc89ae`, display
partition `c2db66b`. The preference lives in `examples/settings_service.py`
(`recommendationCountry`, ISO alpha-2 or `None` = Global), the matching in
`examples/feed_source.py` (`article_countries`, `mentioned_countries`, `country_source`), the
ordering in `examples/api_server.py` (`country_mode`, `_country_multiplier`,
`Backend._preference_rerank`), the explain mirror in `examples/rec_explain.py`, the contract in
`examples/api_fastapi.py` (`recommendationCountry`, `countryMatch`), and the UI as the "Country"
card on Settings plus the boundary divider on Recommendations
(`web/lib/country-partition.ts`). Binding regressions:
`tests/test_api_fastapi.py::test_recommendation_country_persists_and_moves_the_feed_end_to_end`
and `tests/test_api_server.py::test_backfill_is_labelled_so_a_thin_country_cannot_look_full`.

Knobs, both compose defaults with the lost-env-file discipline:
`RWE_REC_COUNTRY_SOURCE` (`content` | event | mention | publisher | union) and
`RWE_REC_COUNTRY_MODE` (`first` | boost).

## What it does

Selecting a country puts that country's coverage at the top of For You. Global — the default —
leaves the feed byte-identical to what it was before this feature existed.

Three decisions define the behaviour, and each was made from a measurement rather than an
argument. All figures are from the production catalog (~27,000 articles, 2026-08-19).

## Decision 1 — a country match is about the ARTICLE, not the outlet

The first shipped rule matched the publisher's home country. It called a Delhi outlet's article
about Washington "India news", which is provenance, not subject. Measured coverage:

| signal | articles | share of catalog |
|---|---|---|
| event geography (`eventCountries`) | 4,782 | 17.7% |
| publisher home | 16,112 | 59.7% |
| **content** (event ∪ mention, incl. demonyms) | **9,623** | **35.6%** |
| union (content ∪ publisher) | 21,796 | 80.6% |

Publisher home carried three quarters of the labels, so "prioritize India" mostly meant
"prioritize Indian outlets". Matching is now **content**: where the event happened, or the country
named in the headline/dek. `union` restores the old behaviour without a deploy.

Countries are a **set** per article, not a label — an article about India and Pakistan belongs to
both, and the single-label form silently dropped one of them. The catalog column is
pipe-separated and matching is membership.

**Demonyms count**, from a curated table (`_DEMONYMS`) — never derived by suffix rule, since
Turkey→Turkish and Netherlands→Dutch share no pattern and a guessed demonym is a silent
mis-label. Each is suppressed inside known non-country phrases (`_DEMONYM_BLOCK`: African
American, nail polish, French fries, Indian Ocean, German shepherd, Dutch oven, Turkish delight
…) — the same known-counterexample discipline the clustering lexicons use. Bare "Korean" is
absent (it cannot choose between KR and KP; the South/North compounds are matched) and so is
"English" (the language reading dominates; Britain/British/UK/England already cover GB).

Demonyms added **+872 mentions / +605 content articles** (+6.7%): real, but modest, and smallest
in absolute terms for the countries that needed it most.

## Decision 2 — a partition, not a bigger boost

The feature first shipped as an 8× rank nudge. Asked whether selecting India should not simply
make the whole feed India, the anchor was swept before changing it — including the cost, since
the country and interest nudges multiply into one sort key and a country boost that dwarfed the
interest scale would make the eight sliders decorative:

| boost | India cards | slots moved | Business cards (slider 10) | vs interest-only |
|---|---|---|---|---|
| — | 2 | 0 | 4 | reference |
| 8× | 5 | 9 | 5 | +1 |
| 12× | 5 | 7 | 4 | +0 |
| 16× | 7 | 9 | 4 | +0 |
| 20× | 8 | 12 | 4 | +0 |

**Interest dilution was zero at every anchor** — the feared cost did not exist, because an item
that is both high-interest and in-country receives both multipliers and rises. But no anchor
reached the whole feed, so the answer was a different mechanism, not a larger number:
country-matched items sort into a **partition ahead of** everything else.

The partition is a **separate sort key**, not an enormous multiplier. Collapsing it into the
divisor — an "infinite boost" — drives every country item's key to zero and throws the reader's
interest ordering away exactly where they asked for it most. As a second key, interests still
order items *within* the country group.

## Decision 3 — backfill, so a thin country never yields a thin feed

The partition does **not** filter: nothing is removed from the pool, so once the country's
admissible articles run out the ordinary feed fills the remaining slots. That is what keeps a
low-supply country from serving a four-card feed. Content-level supply is very uneven —
US 3,897 · GB 1,148 · AU 518 · IN 374 · MY 113 · TR 74 — so this is the common case, not the edge
case.

**A 100% country feed is therefore not achievable for every country**, and no ordering rule can
conjure articles that do not exist. That is a property of the catalog; the levers are ingesting
more coverage of those regions, or accepting backfill.

Because backfill is invisible in the cards themselves, every rec carries `countryMatch` when a
country is selected (absent under Global, so that response is unchanged), and the feed draws a
boundary: *"{country} coverage ends here — the rest are your usual recommendations."* Serving
backfill unlabelled would let a country with a hundred articles look as though it filled a feed.

## The measurements

**Served feed** (engine user 1, personalized, 14 cards, country-first, content matching):

| country | Global | selected | Bridging | cross-cutting |
|---|---|---|---|---|
| IN | 1 | 11–13 | 6 | 6 |
| GB | 0 | 9 | 6 | 6 |
| MY | 0 | 6 | 6 | 6 |
| TR | 0 | 4 | 6 | 6 |

**Backfill quality** — for every country probed: the feed is never short (14 cards), the blend
plan is unchanged (`{rwe-b: 6, rwe-d: 4, adaptive: 4}`), and **100% of backfill cards (8/8, 10/10,
1/1) also appear in the reader's Global feed** — the backfill is their ordinary recommendations,
not the bottom of the ranking.

**Non-interference audit** (`audit_country_interaction.py`, IN+business and GB+technology):
selecting a country changes exactly one stored field; `rec_params_from_settings` **adds** `country`
beside `openness`/`beta`/`interests` rather than replacing any; the RWE-B bridge budget is
byte-identical at openness 0/20/50/80/100; the RWE-D beta is identical at strength 0/25/50/75/100;
Interest Intensity stays monotonic under a country (IN: exposure 0.000 / 0.183 / 0.427 at slider
1 / 5 / 10); Bridging's own cross-cutting count is 6 in every scenario; and restoring Global
returns the baseline feed **byte for byte**.

## Known limits

* **Small countries cannot fill a feed.** Malaysia (113 content articles) served 6 of 14, Turkey
  4 of 14. The rest is labelled backfill. Catalog supply, not a ranking defect.
* **Publisher diversity narrows in proportion to match rate** — India at 13/14 matched dropped to
  10–11 distinct publishers; Britain at 9/14 stayed at 14. Inherent to concentrating on one
  country; the publisher cap still applies.
* **A mention is not a subject.** "unlike India, China…" names India while reporting on China —
  the comparative-mention failure `docs/EVENT_IDENTITY_RUBRIC.md` rule 1 records at story level,
  reappearing at article level.
* **The picker's supply signal is not the matching rule.** `/api/places/countries` counts
  event-located articles only, so a country can have real content supply and show zero there. The
  expanded picker deliberately offers those countries anyway; a content-level supply count on that
  endpoint is the proper fix and is not built.

## Instrument lessons (recorded so the traps keep compounding)

* **A ~200-article "cannot fill a feed" threshold was stated and then refuted by measurement** —
  MY with 113 filled 6 slots while GB with 1,148 filled 9. Supply is an upper bound; the reader's
  own ranking decides. The line was removed from the instrument and replaced with the three
  measurements that killed it.
* **A weak GB result was read as a structural publisher-cap ceiling from one reader**, and the
  next reader disproved it immediately. Per-reader variation on a rank nudge is large.
* **The share metric misleads where the count does not.** `45% (5/11)` → `42% (5/12)` is the same
  five cards with a moving denominator. Card counts are the series to read.
* **The feed is partitioned per STRATEGY, not globally** — the blend allocates slots per strategy
  and orders each group country-first inside its own budget. A single "coverage ends here"
  boundary drawn over the raw order landed inside the first group and stranded later groups'
  country cards below it. The display partitions the whole list before drawing the boundary
  (`web/lib/country-partition.ts`, tested on exactly that interleave).
* **Judge the contract, not the total.** Both audit instruments first compared the feed's *overall*
  cross-cutting count and flagged "DIVERSITY LOST" on a healthy run: only RWE-B owes opposing
  perspectives, and a cross-cutting card that happens to land in another slice is incidental and
  free to be replaced — reordering those slices is what a country preference is *for*. The same
  conflation had to be fixed twice (`3c099ce`, then `68ae021` after it fired on the post-deploy
  run). Judging the slice that owns the contract is also the **sharper** bar: a real fall inside
  Bridging can no longer be masked by an incidental gain elsewhere.
* **`dc exec` starts a NEW process** and shares no memory with the uvicorn worker, so an
  instrument cannot borrow the serving Backend; it must build one. And `--user` is a corpus row
  index — probing a real reader needs `--engine-user` through `personalize.Personalizer`.
* **`docker compose run` accepts neither `--cpus` nor `--memory`**; and without `--no-deps` it
  starts the `api` service's `depends_on` ingest container.

## Rollback

`RWE_REC_COUNTRY_MODE=boost` in `deploy/.env` restores the 8× nudge; `RWE_REC_COUNTRY_SOURCE=union`
restores publisher-inclusive matching. Both take effect on `dc up -d api` with no rebuild. A
reader who has never selected a country is unaffected either way: Global emits no parameter and
the feed is byte-identical to the pre-feature behaviour.

## Verification runbook (post-deploy)

```bash
cd /opt/ih && source deploy/ops/_compose.sh

# coverage + per-country supply by definition (store read; seconds)
dc exec -T api python examples/audit_country_rerank.py \
  --sources event,mention,content,union --countries IN,GB,US,MY,TR

# what one real reader is served, plus backfill quality (builds a corpus)
dc exec -T api python examples/audit_country_rerank.py \
  --serve-diff --engine-user 1 --countries IN,MY --modes first --backfill-check

# non-interference with the older tuning controls
dc exec -T api python examples/audit_country_interaction.py \
  --engine-user 1 --country IN --interest business
```

Expected: content coverage ≈ 35% of the catalog; a selected country leads the feed and backfills
the remainder; the blend plan stays `{rwe-b: 6, rwe-d: 4, adaptive: 4}` with **6 cross-cutting
cards inside Bridging** (the all-slices total may move by one — incidental, outside the contract);
and Global restores the baseline feed byte for byte.
