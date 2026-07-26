# Publisher Intelligence — MVP (shipped)

The trust layer between *seeing* a source and *reading* it: every publisher name in the app is a
doorway to a profile of counted facts. Deliberately **not** a nav destination — publisher pages
are contextual (click a publisher badge, a story-coverage row), following the consolidation
direction that folded Countries into Stories rather than growing the nav.

## What it is (and what it deliberately is not)

The original Template-3 concept was an encyclopedia page: multi-provider bias consensus,
factuality and transparency scores, ownership trees, publisher timelines, AI summaries,
most-read/most-shared leaderboards, interactive maps. Almost none of that survived contact with
the platform's rules and its data:

| Concept | Decision | Why |
|---|---|---|
| Curated lean + locality | **Shipped** | `outlet_registry.csv` — real curated facts |
| Counted catalog profile | **Shipped** | topics / cadence / geography / tone are countable today |
| Multi-provider bias consensus | Rejected | one curated table exists; a "consensus" would be fabricated |
| Observed vs curated lean | Rejected | feed article lean IS the registry lean by construction (`ingest`) — it would compare a number with itself |
| Factuality / transparency scores | Postponed | zero backing data; a score without methodology is what this product opposes |
| Ownership / founded / HQ / timeline | Postponed | registry has no such columns; Wikidata-as-provider is the future path |
| AI publisher summary | Postponed | counted facts over prose; if Guide ever writes one it cites these modules |
| Most read / most shared | Rejected permanently | we don't track shares; engagement leaderboards contradict the thesis |
| Similar publishers / suggestions | Postponed | ranked suggestions sit behind the W-series evaluation gate |
| Interactive world map | Postponed | coordinates surfaces are Location Phase 3 |

The page is designed for the outlet we know **least** about — the GDELT long tail is the most
common trust moment. Honest degradation is the design center: "Not rated · 12 articles indexed"
beats empty encyclopedia slots.

## Architecture (as shipped)

```
outlet_registry.resolve(name)        curated identity/lean/locality — or honest absence
store.publisher_catalog_stats(name)  counted: volume + window, topics, languages, hosts,
                                     event countries (side table), tone splits with per-signal n
publisher_service.get_publisher      composition + floors; recent articles via the SAME
                                     search path + Article serializer Discover/Search use
GET /api/publishers/{name}           typed models, exclude_none, 404 when nobody knows the name
web /publishers/[name]               header + counted modules (BarList/SectionCard idioms),
                                     deep links to /search?publisher= and /stories?publisher=
```

Entry points: `PublisherBadge` names link everywhere the badge renders (Discover, Search, Saved,
Recommendations, Analyzer), and story-detail coverage rows link each publisher. Search and the
Stories browser accept a `?publisher=` deep link so the profile's "view all" links arrive
pre-filtered (the `?country=` pattern).

## Honesty contracts (pinned by tests)

- **L2.2 end-to-end**: an unrated outlet is `rated: false` with null lean — the page shows
  "Not rated", never a fabricated Center. (The same commit made the feed serializer stop
  coercing unknown lean to 0.0 — display and the SQL lean filter now agree.)
- **Per-signal n**: tone splits count only articles that carry the signal (register uses the
  engine's own 0.6/0.4 thresholds; non-finite is excluded, never defaulted). Below
  `MIN_SIGNAL` (5) the module is omitted — omit, don't thin-render.
- **404, not synthesis**: a name neither the registry nor the catalog knows is Not Found.
  A registry outlet with zero catalog rows profiles with an honest zero.
- **Provisional rows excluded** (uncorroborated extension-created articles), matching Discover.
- **Event geography is the event dimension**: "where their stories happen" counts
  `article_event_locations` (provider-extracted), never the publisher's home; the registry
  country renders separately as the curated home.

## Fast-follows

1. **Publisher blindspot (M2 — shipped):** "What they rarely cover" — the catalog's biggest
   topics where the publisher's share is under half the catalog's (zero always qualifies),
   counted on both sides. Deterministic rule + floors (`BLINDSPOT_MIN_ARTICLES`,
   `BLINDSPOT_MIN_CATALOG`) in `publisher_service._topic_gaps`; below a floor the module is
   omitted — a thin sample "misses" everything, which asserts nothing.
2. **Co-coverage (M2 — shipped):** "Covers the same stories as" — counted story co-membership
   over the same clustering the Stories surface serves (one count per shared story, floor
   `CO_COVERAGE_MIN_STORIES`), never a similarity ranking. Names link to their profiles.
3. A publishers index page, if navigation demand appears (not started).
4. Follow-publisher — only once a surface consumes the signal (no dead controls; not started).

Data-acquisition-gated: ownership (Wikidata provider via the resolver-extension procedure in
docs/LOCATION_PLATFORM.md), factuality (licensed/curated source), maps (Phase 3), Guide-written
summaries citing the counted modules.
