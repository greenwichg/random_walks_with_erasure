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
                                /api/places/publishers (Local News v1) · settings.edition/.locations
      ↓
Frontend                        /local page · SearchParams.country · PlacePublisher type
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

## What each future feature reads

- **Local News v1 (shipped):** `/api/places/publishers?country|region|city|scope` (registry facts)
  + `search?country=` (located catalog). Page: `/local`.
- **Countries page:** `search/discover ?country=` + per-country facets — data now exists.
- **Geographic Diversity:** `location.reader_geography(store, uid)` → counted facts
  (`countries`, `languages`, `scope` incl. explicit `unknown`) ready for a future metric; no
  score is computed yet, deliberately.
- **Editions / followed places:** `settings.edition` (ISO2 or None) + `settings.locations`
  (`{placeId, level}` ×≤10) — normalized in the engine contract, no UI yet.

## Explicitly out of scope until Phase 2/3 (per the phase plan)

Event locations, GKG enrichment, coordinates, maps, radius, GPS, nearby, travel mode, NLP
extraction. The schema anticipates them (an `article_locations` side table extends the platform
without touching `feed_articles`), and the registry grows local/hyperlocal outlets by curation.
