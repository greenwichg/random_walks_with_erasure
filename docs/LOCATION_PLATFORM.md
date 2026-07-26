# Location Intelligence Platform — Phase 0 + 1

The single location-metadata layer for Hidden View. Every provider normalizes into it; every
feature (Local News, Countries, Geographic Diversity, Publisher Intelligence, Recommendations)
consumes the one canonical model. No provider-specific location logic exists downstream of the
resolver — by construction.

## Architecture (as shipped)

```
Provider adapter                sources.py — RSS / NewsAPI / GDELT (and future providers)
      ↓  FeedEntry.country / FeedEntry.language     (whatever form the provider uses)
Location Resolver               examples/location.py — resolve_article_location()
      ↓  canonical: country = ISO 3166-1 alpha-2 · language = ISO 639-1 · None when unresolvable
Store                           feed_articles.country / .language (additive columns + ix_feed_country)
      ↓
Story clustering                unchanged — stories derive place facets from members when needed
      ↓
API                             Article.country/.language · search/discover ?country= ·
                                /api/places/publishers · /api/places/countries · /api/me/geography ·
                                settings.edition/.locations
      ↓
Frontend                        /countries page · SearchParams.country · PlacePublisher type ·
                                Search country filter · Settings "Places & edition" · Home place rail
```

## The canonical model

| Field | Form | Source of truth |
|---|---|---|
| Article `country` | ISO 3166-1 alpha-2, upper ("US") | Registry outlet locality **beats** provider metadata; provider fills the long tail |
| Article `language` | ISO 639-1, lower ("en") | Provider metadata, normalized |
| Publisher `country` / `region` / `city` | curated facts | `examples/data/outlet_registry.csv` |
| Publisher `scope` | `international · national · regional · local · hyperlocal` | registry (closed set: `location.SCOPES`) |

Fail-honest: anything unresolvable is `None` — never a guessed place. The registry row's
docstring rule applies platform-wide: locality is a curated fact, never inferred from articles.

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

- **Countries page (shipped — absorbed Local v1):** `/api/places/countries` (located-catalog ∪
  registry facets), `/api/places/publishers?country|region|city|scope` (registry facts, whole
  registry when unfiltered), `search?country=` (located catalog). Page: `/countries`;
  `/local` redirects there (see the reservation below).
- **Geographic Diversity:** `location.reader_geography(store, uid)` → counted facts
  (`countries`, `languages`, `scope` incl. explicit `unknown`) — surfaced on Analytics as the
  "Reading geography" card; no 0–100 score is computed yet, deliberately.
- **Editions / followed places:** `settings.edition` (ISO2 or None) + `settings.locations`
  (`{placeId, level}` ×≤10) — normalized in the engine contract; UI in Settings → Places &
  edition, consumed by the Home "From your places" rail.

## Countries vs. the future Local (the consolidation)

Local News v1 (`/local`) and the Countries page shipped as siblings and converged on the same
experience: country chips → registry publishers + latest located coverage. The pages differed
only in ornament (chip counts, an overview card, a locality line), so Local v1 was folded into
Countries — its "All"/whole-registry browse and publisher locality lines moved there — and the
`/local` route now temporary-redirects to `/countries`. Nothing engine-side changed: both pages
always read the same three endpoints, which remain the platform contract.

**The name "Local" is reserved** for a genuinely different product — the reader-first,
personalized place experience — and returns to navigation only when it ships. The
differentiation, so the two never blur again:

| | **Countries (today)** | **Future Local (reserved)** |
|---|---|---|
| Question | "What does coverage from place X look like?" | "What matters **near me / my places** right now?" |
| Subject | a place, chosen per visit | the reader's standing places |
| Inputs | located catalog + registry facts only | `settings.edition`/`.locations` (shipped), explicit **opt-in** GPS/nearby, travel context |
| Granularity | country | region → city → neighbourhood (hyperlocal) |
| Ranking | newest-first search, impersonal | personal relevance (must pass the W-series evaluation gate like any ranking change) |
| Needs built | nothing — shipped | Phase 2 event geography (`article_locations` side table), regional/local registry depth, opt-in location permission UX, travel mode |

Platform pieces the future Local already has waiting: followed locations + edition persisted and
normalized in settings; `scope` as a closed registry vocabulary down to `hyperlocal`; the
resolver's provider-extension procedure above; and the `/local` path itself. What it must never
do is inherited from the house rules: no place is ever guessed (GPS is opt-in or absent), and no
ranking change ships outside the evaluation framework.

## Explicitly out of scope until Phase 2/3 (per the phase plan)

Event locations, GKG enrichment, coordinates, maps, radius, GPS, nearby, travel mode, NLP
extraction. The schema anticipates them (an `article_locations` side table extends the platform
without touching `feed_articles`), and the registry grows local/hyperlocal outlets by curation.
