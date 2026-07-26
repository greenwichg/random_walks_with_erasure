# Location Intelligence Platform — Phase 0 + 1

The single location-metadata layer for Hidden View. Every provider normalizes into it; every
feature (Local News, Countries, Geographic Diversity, Publisher Intelligence, Recommendations)
consumes the one canonical model. No provider-specific location logic exists downstream of the
resolver — by construction.

## Architecture (as shipped)

```
Provider adapter                sources.py — RSS / NewsAPI / GDELT (+ the GKG event-geography
                                enricher, which locates articles already in the catalog)
      ↓  FeedEntry.country / .language / .event_locations   (whatever form the provider uses)
Location Resolver               examples/location.py — resolve_article_location() +
                                resolve_event_locations()
      ↓  canonical: country = ISO 3166-1 alpha-2 · language = ISO 639-1 · None when unresolvable
Store                           feed_articles.country / .language (additive columns + ix_feed_country)
                                + article_event_locations (0..n per article, provider provenance)
      ↓
Story clustering                unchanged — stories derive place facets from members when needed
      ↓
API                             Article.country/.language · search/discover ?country= ·
                                /api/places/publishers · /api/places/countries · /api/me/geography ·
                                settings.edition/.locations
      ↓
Frontend                        Stories country filter (StoryQuery.country) · SearchParams.country ·
                                Search country filter · Settings "Places & edition" · Home place rail
```

## The canonical model

| Field | Form | Source of truth |
|---|---|---|
| Article `country` (publisher dimension) | ISO 3166-1 alpha-2, upper ("US") | Registry outlet locality **beats** provider metadata; provider fills the long tail |
| Article `language` | ISO 639-1, lower ("en") | Provider metadata, normalized |
| Article **event locations** (0..n) | `article_event_locations` rows: country (ISO2, required) + optional region/city/lat/lon + provider `source` | Provider-extracted geography, normalized by `location.resolve_event_locations` — **we never extract places from article text ourselves** |
| Publisher `country` / `region` / `city` | curated facts | `examples/data/outlet_registry.csv` |
| Publisher `scope` | `international · national · regional · local · hyperlocal` | registry (closed set: `location.SCOPES`) |

Fail-honest: anything unresolvable is `None` (or, for event locations, simply absent) — never a
guessed place. The registry row's docstring rule applies platform-wide: locality is a curated
fact, never inferred from articles.

### Two dimensions, two jobs (never mixed)

- **Content location = the EVENT dimension, only.** Search (`?country=`), the Stories filter,
  and the country facets answer "what happened *in* X" from `article_event_locations` alone —
  in one place each (`store._search_conditions`, `story_service._event_consensus`,
  `store.feed_article_country_facets`), so the product never disagrees with itself. An article
  or story with no event geography matches **no** country; it still appears unfiltered ("All").
  Publisher homes are never a fallback: before event data flows, country pickers are honestly
  empty rather than wrong.
- **Provenance = the publisher dimension.** `feed_articles.country` (registry-beats-provider)
  stays a first-class stored fact for publisher intelligence and analytics: reading-geography
  (`/api/me/geography`), the registry features, and each story's `publisherCountries` fact.
- **Story aggregation** is member consensus: each event-located member votes for its (already
  dominance-filtered) event countries; the plurality leader(s) are the story's `countries`
  (ties kept — a genuinely two-country event IS in both places), and the unique leader, when
  one exists, is `primaryCountry`.

## Integrating a future provider (the whole procedure)

A provider needs exactly two things — **no downstream change**:

1. **A `SourceAdapter`** (existing pattern in `sources.py`) whose `normalize()` sets
   `FeedEntry.country` / `FeedEntry.language` with whatever the provider supplies.
2. **A resolver mapping, only if the provider emits a new form.** The resolver already accepts
   ISO2 ("us"), ISO3 ("USA"), BCP-47 ("en-US"), and English names ("United States", "English").
   A new form = new entries in `location._COUNTRY_NAMES` / `_LANGUAGE_NAMES` — one table edit.

Worked example — Guardian Open Platform (`response.results[*]`):

```python
class GuardianAdapter(SourceAdapter):
    provider, source_type = "Guardian", "guardian"
    def normalize(self, raw):
        entries = [rss_ingest.FeedEntry(
            url=item["webUrl"], title=item.get("webTitle") or "",
            published_at=item.get("webPublicationDate"),
            source_type="guardian", source_provider="Guardian",
            publisher_hint="theguardian.com",
            country="GB",          # publisher-level fact; ISO2 passes the resolver untouched
            language="en",
        ) for item in (raw.get("response", {}).get("results") or [])]
        return SourceBatch(self.provider, self.source_type, _now_iso(), entries, len(entries))
```

That is the entire location integration. `ingest_entries` runs the resolver; the store, search,
stories, APIs and frontend need nothing. (NewsAPI already ships `country`/`language` as ISO
codes — its adapter needs zero resolver work.)

## What each feature reads

- **Stories country filter (shipped — absorbed the Countries page, which absorbed Local v1):**
  `/api/stories?country=` — a story matches when its member-consensus EVENT countries include
  the selection ("stories happening in X"); "All" remains the whole feed, global and
  multi-country stories included. Each story carries derived `countries` (the consensus),
  `primaryCountry`, `eventCountries`, and `publisherCountries` facts, internal until a card
  consumes them. The picker + per-country counted-facts line read `/api/places/countries`;
  deep link `/stories?country=XX` (the home place rail uses it). `/countries` and `/local`
  redirect to `/stories`.
- **Registry publisher locality (`/api/places/publishers`):** remains a platform surface with no
  current web consumer — the dedicated browse UI retired with the Countries page; the future
  personalized Local experience reintroduces it.
- **Geographic Diversity:** `location.reader_geography(store, uid)` → counted facts
  (`countries`, `languages`, `scope` incl. explicit `unknown`) — surfaced on Analytics as the
  "Reading geography" card; no 0–100 score is computed yet, deliberately.
- **Editions / followed places:** `settings.edition` (ISO2 or None) + `settings.locations`
  (`{placeId, level}` ×≤10) — normalized in the engine contract; UI in Settings → Places &
  edition, consumed by the Home "From your places" rail.

## Place browsing vs. the future Local (the consolidation)

Place browsing consolidated twice: Local News v1 (`/local`) folded into the Countries page
(they had converged on the same chips → publishers + located-articles experience), and the
Countries page then folded into **Stories** as its country filter — country became a dimension
of the primary discovery surface rather than a destination of its own. `/local` and
`/countries` temporary-redirect to `/stories`. Engine-side the consolidation only ever ADDED
surface: `/api/stories?country=` (member-location post-filter) joined the platform contract;
the three places/geography endpoints are unchanged, and the registry-publishers browse UI
retired (its endpoint remains for the future Local experience).

**The name "Local" is reserved** for a genuinely different product — the reader-first,
personalized place experience — and returns to navigation only when it ships. The
differentiation, so the two never blur again:

| | **Stories country filter (today)** | **Future Local (reserved)** |
|---|---|---|
| Question | "What does coverage from place X look like?" | "What matters **near me / my places** right now?" |
| Subject | a place, chosen per visit | the reader's standing places |
| Inputs | located catalog + registry facts only | `settings.edition`/`.locations` (shipped), explicit **opt-in** GPS/nearby, travel context |
| Granularity | country | region → city → neighbourhood (hyperlocal) |
| Ranking | the Stories sorts (top/latest/publishers), impersonal | personal relevance (must pass the W-series evaluation gate like any ranking change) |
| Needs built | nothing — shipped | Phase 2 event geography (`article_locations` side table), regional/local registry depth, opt-in location permission UX, travel mode |

Platform pieces the future Local already has waiting: followed locations + edition persisted and
normalized in settings; `scope` as a closed registry vocabulary down to `hyperlocal`; the
resolver's provider-extension procedure above; and the `/local` path itself. What it must never
do is inherited from the house rules: no place is ever guessed (GPS is opt-in or absent), and no
ranking change ships outside the evaluation framework.

## Event geography (Phase 2) — architecture shipped, providers pending

The event dimension is live end-to-end: `FeedEntry.event_locations` (adapters relay whatever
places their provider extracted, in the provider's own form) → `location.resolve_event_locations`
(normalizes countries through the same tables, drops the unresolvable, dedupes) →
`store.article_event_locations` (the reserved side table — `feed_articles` untouched, 0..n rows
per article, provider `source` on every row) → best-known search/stories/facets above. A
provider that supplies no geography never wipes another's rows (per-source replace — the same
backfill discipline the dedup merge uses).

**The supply: the GDELT GKG enricher (shipped, default OFF — `RWE_GDELT_GKG=1` to enable).**
The DOC artlist we ingest carries only `sourcecountry` (publisher-level); event geography lives
in GDELT's GKG files. `examples/gdelt_gkg.py` + `sources.GDELTGKGEnricher` poll the latest
15-minute `*.gkg.csv.zip` on the standard poller/health machinery and locate articles ALREADY
in the catalog — any provider's (an RSS-ingested outlet GDELT also monitors gets located too).
Enrichment only: it never creates articles. Provider-specific mapping stays in the adapter:

- **The FIPS trap:** GKG `V1Locations` country codes are FIPS 10-4, not ISO (FIPS `AS` =
  Australia vs ISO American Samoa; FIPS `GM` = Germany vs ISO Gambia). The enricher resolves by
  each block's trailing country NAME through `normalize_country` and never reads the code — an
  unknown name is dropped, never mis-mapped (pinned by tests).
- **Salience:** a GKG record lists every place an article mentions; only the dominant
  country(-ies) by block count are kept, so one stray mention never locates an article. Story
  consensus across members narrows further.
- **Matching:** GKG URLs are canonicalized with the SAME `ingest.canonical_url` the catalog
  dedup uses (plus a scheme-flipped candidate), so matches align by construction.
- **Quality bounds:** country-level only (the coarsest, most reliable tier); provenance
  (`gdelt-gkg`) on every row; per-source replace keeps re-runs harmless; a size cap
  (`RWE_GDELT_GKG_MAX_BYTES`) guards the download.

**Coverage honesty / deploy sequence:** until `RWE_GDELT_GKG=1` runs its first cycles the side
table is empty — country pickers offer nothing and the filter matches nothing, deliberately
(empty beats wrong). Enable the flag and coverage fills within cycles for GDELT-monitored
articles; older/unmonitored articles simply stay unlocated. Live validation of the first cycles
(match rate, located counts in the `gdelt://gkg` health row) is a deploy-time step — the suite
pins the logic offline.

- **GeoRSS / Dublin Core (evaluated, deferred):** mainstream news feeds almost never carry it,
  and `georss:point` gives coordinates without a country — turning them into countries means
  reverse geocoding, a new dependency for near-zero yield. Revisit only for verticals that
  actually publish it (quakes/weather/gov).
- **NLP extraction by us: never** (house rule). Extraction belongs to providers; normalization
  to us.

## Explicitly out of scope until Phase 3 (per the phase plan)

Coordinates *surfaces* (maps, radius), GPS, nearby, travel mode, NLP extraction, and the GKG
ingestion pipeline itself. The registry grows local/hyperlocal outlets by curation.
