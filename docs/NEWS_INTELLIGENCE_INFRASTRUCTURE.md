# News Intelligence Infrastructure — assessment and target architecture

**Status: DESIGN ONLY. Nothing here is implemented. No schema, route, adapter or consumer surface
has changed. Implementation begins only after this document is approved.**

**The question this answers:** how does ONE reusable News Intelligence Infrastructure — the data
Hidden View already acquires, the clusters and spectrum it already computes, the publisher facts it
already curates — get exposed through seven commercial products without duplicating ingestion,
clustering, intelligence or storage?

The seven products, as named in the brief, and the short handle used for each below:

| # | product | handle |
|---|---|---|
| 1 | Developer Web Search / News API | **API** |
| 2 | Enterprise News Intelligence | **ENT** |
| 3 | Data / Event-Graph Licensing | **LIC** |
| 4 | White-label News Intelligence | **WL** |
| 5 | Premium B2B features | **B2B** |
| 6 | Consumer monetization (later) | **CON** |
| 7 | Advertising / sponsorship, affiliate / referral (later) | **ADS** |

Provenance convention, inherited from `capacity_report.py` and every scale document: **[M]**
measured in this repository during this review · **[D]** derived by arithmetic over measured
values · **[P]** projected, assumption stated · **[A]** untested assumption. Where a production
number is quoted from an existing document it is marked **[M-doc]** with the document named.

Two rules the whole design obeys, taken verbatim from the brief:

1. **Do NOT weaken or modify existing consumer web/mobile functionality.** Every consumer route,
   every payload, every screen keeps working byte-for-byte. The infrastructure is a *second front
   door onto the same engine*, never a rewrite of the first.
2. **Do NOT replace existing working systems unless there is a clear architectural reason.** Each
   replacement below names the reason; where the reason is not yet measured, the existing system
   stays.

---

## A · Current architecture assessment

### A.1 The shape of the system, as it actually runs [M]

```
                       publishers · RSS/Atom · sitemaps · 6 keyed news APIs · Google News RSS · GDELT DOC+GKG
                       Wikipedia/Wikidata · Commons · publisher sites (logos) · SerpAPI/Brave/Google CSE (discovery)
                                                     │
   browser extension ──POST /api/me/reads──▶  ┌──────▼──────────────────────────────────────────────┐
   (metadata-only)                            │  rss_ingest.ingest_entries  — THE ONE CHOKE POINT    │
                                              │  canonical URL · score (topic/lean/political/tone)   │
                                              │  location resolver · block list · provenance columns │
                                              └──────┬──────────────────────────────────────────────┘
                                                     ▼
                                   SQLite (WAL, one file, one host) — examples/store.py — 32 tables
                                                     │
        ┌──────────────────┬─────────────────────────┼──────────────────────┬─────────────────────┐
        ▼                  ▼                         ▼                      ▼                     ▼
  ① full catalogue    ②′ Tier-A projection     ② recommendation         ③ reads             publisher facts
  (search/discover)   → story builder          corpus → RWE engine     → Information Health  registry.csv +
                      (pure, cached, ids       (per-user augmented)     report, evidence      wiki/logo caches
                       stabilised by ledger)                            resolver
        └──────────────────┴─────────────────────────┴──────────────────────┴─────────────────────┘
                                                     │
                                   FastAPI engine — examples/api_fastapi.py — 71 routes [M]
                                   (never internet-facing; X-IH-Auth shared secret + X-IH-User-Id)
                                                     │
                                   Next.js web tier — 44 route files [M] — session (Google) or
                                   per-user bearer token (extension, Expo app) → engine headers
                                                     │
                                   Caddy (the only public process) → web:3000
                                                     │
                              browser · PWA · browser extension · Expo app (Android/iOS)
```

The two design contracts every later decision rests on already exist and are guarded by tests:

- **One ingestion boundary.** Every producer — RSS, six keyed JSON providers (NewsAPI, Guardian,
  NewsData, GNews, MediaStack, Currents), Google News RSS, GDELT, the crawler and the browser
  extension — terminates in `rss_ingest.ingest_entries`. Downstream code cannot tell providers
  apart except through provenance columns (`source_type`, `source_provider`, `external_id`,
  `source_feed`). This is the property that makes "one data plane, many products" possible at all.
- **Four datasets with enforced boundaries** (`docs/CORPUS_ARCHITECTURE.md`,
  `tests/test_corpus_boundaries.py`): ① searchable ≠ ②′ clusterable ≠ ② recommendable ≠ ③ a
  reader's reads. Tier (A / B / shadow) is a property of the *outlet*, not the article.

### A.2 Component-by-component findings

| Area | What exists [M] | Verdict for reuse |
|---|---|---|
| **Crawler** (`examples/crawler.py`, 1,283 lines) | Discovery-document crawler (robots.txt → RSS → sitemap → section), never article pages; fail-closed robots policy; per-host rate limit; `CrawlAdapter` wired into the poller behind `RWE_CRAWL_ENABLED`; per-host admission ledger (`source_admission`) with probe accounting and crawl policy columns; `verify_crawler_config.py`. ToS review outstanding (`docs/CRAWLER_PRODUCTION_READINESS.md`). | Reuse as-is. It is the acquisition channel that *scales with content, not source count* (`SCALE_ROADMAP.md`). The outstanding ToS review is the gating item for LIC/ENT, not code. |
| **Browser extension** (`extension/`, MV3, v0.2.0) | Opt-in, metadata-only capture (`og:type` / JSON-LD / `article:published_time`); posts to `/api/me/reads` with a per-user bearer token; server stores only a SHA-256 token hash. Extension-born articles enter the catalogue as `provisional` and are promoted by feed corroboration or ≥N distinct readers. | Reuse as-is for consumer. **Never a licensing source**: a provisional row's existence is a reader's browsing history. Must stay excluded from every external surface (today it is excluded from Discover only). |
| **Ingestion pipeline** (`ingest.py`, `rss_ingest.py`, `sources.py` 2,204 lines) | `SourceAdapter` chassis with quotas, retries, 429 accounting, per-feed conditional GET + adaptive interval + circuit breaker, per-source health rows, a durable daily meter for web-search spend (`web_search_spend`). Scoring is deterministic and cached first-writer-wins per canonical URL. | Reuse as-is. The `KeyedJSONAdapter` budget/rotation machinery is the template for outbound-cost metering; `web_search_spend` is the template for durable usage meters. |
| **Article / data model** (`feed_articles`) | Primary key = `canonical_url` (String 2048). Scored JSON blob (topic, lean, political, emotion, register, confidence). Provenance columns. Media columns (URL only, never bytes). `country`/`language` canonical. `article_state`. Side tables: `article_event_locations` (provider-extracted, provenance per row), `article_entities` (GDELT person/org + our `span` kind, provenance per row). `body` column stores `content:encoded` when a feed ships it. | Reuse, **but the identity is the gap** (A.4). No surrogate article id; the URL string *is* the key everywhere (`reads`, `saved_articles`, `rec_events`, `story_member`, `scored_articles`). |
| **Publisher registry** (`examples/data/outlet_registry.csv`, `outlet_registry.py`, `publisher_identity.py`) | 609 curated rows [M]: lean filled 505, country 601, scope 594, factuality 130 (all MBFC), ownership 14, kind 31, credibility 71. Aliases per row. Lean is AllSides-derived and verified against AllSides pages (`SIGNAL_INTEGRITY.md`). `publisher_identity.groups` folds name variants onto one identity. Enrichment caches: `publisher_metadata` (Wikipedia/Wikidata, per-field provenance, verified matching, 44/60 busiest resolved [M-doc]) and `publisher_logo`. `source_lifecycle` + append-only `source_lifecycle_events` ledger; `source_admission` per host. Production catalogue: 9,397 hosts, 4,854 outlet identities [M-doc `PATH_TO_50K_DECISION_REVIEW.md`]. | Reuse the *facts* and the *matching discipline*. **Missing: a durable publisher id and a publisher graph.** Identity today is a canonical name string resolved at read time; the parent/owner relation exists only as a Wikidata text field and 13 curated `ownership_owner` cells. |
| **Clustering / story system** (`clustering.py`, `story_service.py` 4,451 lines) | Deterministic token-Jaccard union-find with inverted-index candidate generation; cluster-level linkage quorum; template gate; instance-anchor veto; time decay; geo veto; entity merge/veto; Tier-B *attachment* (M4, coverage joins without moving the partition); duplicate merge; repair; trust verdict (`clusterTrust` ok/low/unverified from geo coherence); blindspot gate; id stabilisation via the `story_member` ledger (measured 5.1 %/day churn → fixed); stable-id carry-over rule (≥50 % of URLs). Build is **pure**, cached (fingerprint + TTL), warmed by the poller, optionally offloaded to a subprocess pool. Banded LLM event-identity judge (`event_identity.py`) shipped OFF, verdicts persisted in `event_verdicts`. Window 6 days; Tier A row cap 60,000. | Reuse the builder unchanged. **Stories are derived per build and never persisted** except the id ledger and tags. There is no story history, no membership history, no build version on the wire, no "what did this story look like yesterday". That is the single largest gap for ENT and LIC. |
| **Story intelligence** (`story_intelligence.py`) | Freshness band, momentum, lifecycle, coverage statistics, timeline, alerts, new-since-last-visit; computed at request time from the story's coverage list; thresholds from env. Breaking-story edge detection persisted in `notification_events` (idempotent per story). | Reuse as-is; it is a pure function of a story. Needs persisted story history to answer "over time" questions. |
| **Topics / tags / entities** | Topic: one canonical deterministic classifier at ingest (`ingest.classify_topic`). Entities: provider (GDELT GKG persons/orgs, ~24 % of articles [M-doc]) + our rule-based capitalised spans, kind-separated with provenance. Tags: story-level projection with direct / inherited / topic sources and scores, persisted in `story_tag`, exposed as `story.tags`, `?tag=`, `tagFacets`, and the Similar-News-Topics section. | Reuse. No entity *identity* (no QIDs, no cross-story entity table) — names are normalised strings. Adequate for API/ENT phase 1; LIC event-graph wants a canonical entity node later. |
| **Coverage / bias / blindspot** | Per-story L/C/R distribution over distinct rated publisher identities; `blindspotSide` gated by rated-publisher floor *and* cluster trust, with `blindspotWithheld` recorded; `lowCredibilityPublishers` named rather than dropped; Tier B coverage attached without votes; publisher-level "what they rarely cover" gaps and co-coverage; L0 coverage comparison (`coverage_comparison.py`, deterministic, versioned by `ALGO_VERSION`). | Reuse as-is. These are exactly the "spectrum intelligence" products sell. All are counted facts with a stated basis — already the honesty contract B2B buyers need. |
| **Trust / confidence signals** | The **Measurement envelope** (`ADR-001`: `{coverage{observed, eligible, basis}, provenance{kind, source}, confidence?}`) on report metrics; `clusterTrust`; `geoCoherence`; `factuality {value, source, asOf, ratingUrl}` never as a bare level; `factualityPublished` distinguishes "not rated" from "not published"; publisher `about.sources` per field. | Reuse the *shape* platform-wide: every platform response field that is derived carries the same envelope (F.4). |
| **APIs** | Engine: 71 routes [M] (public catalogue: search, discover, stories, story, similar, intelligence, publishers, places; reader: `/api/me/*`; internal; dev). Web: 44 route files [M], the auth matrix documented route by route (`API_AUTH_MATRIX.md`). **One external-key surface already exists**: `GET /api/search.json`, a SerpAPI-compatible facade over the outlet index with `RWE_SEARCH_API_KEYS`, internal-first with budgeted SerpAPI top-up and honest `ih_source` provenance. Pydantic models with `exclude_none`, additive-optional discipline. | Reuse the catalogue routes' *services* (`search.search`, `story_service.list_stories`, `publisher_service.get_publisher`, …) behind a new versioned gateway. The facade is the seed of product 1. |
| **Authentication** | Google OAuth (NextAuth JWT, 30-day, non-revocable); engine trusts the web tier only (`X-IH-Auth` = `RWE_INTERNAL_SECRET`, fail-closed boot); per-user API tokens (hash only, no expiry, no scopes) resolved via `/api/internal/resolve-token`; mobile exchange `POST /api/auth/mobile` (Google ID token → bearer); beta allowlist. Rate limiting: in-process token buckets per scope, keyed by user or IP; body limits per scope. | Reuse for consumers unchanged. **No tenant, no scoped keys, no key lifecycle, no per-key quota, no shared limiter** — everything a paying API customer needs is absent by design of a single-tenant consumer product. |
| **Web / mobile apps** | Next 14 App Router (desktop + mobile web), Expo app reproducing the mobile web screen for screen, `@ih/core` shared client (`services.ts` 1:1 with endpoints, domain types, logic leaves, i18n ×5). | Untouched by this design. They keep the internal path. |
| **Storage and retention** | SQLite WAL on one EBS volume; `create_all` + idempotent `_ensure_*_columns` (no migration tool); catalogue retention by count (150,000 ≈ 30 days [M-doc]) and by age per tier; `PROTECTED_TABLES` never pruned; score cache 30 d; analytics 180 d; backups gzipped, GFS-tiered, hourly to S3 with lifecycle (7 d → Glacier IR, 90 d → Deep Archive, 365 d expire). Measured ingest headroom 114× at 50k sources [M-doc]. | Reuse SQLite for the hot path (the capacity documents say Postgres is an *architectural* trigger — second host or zero-downtime — not a volume one). **There is no archive.** Articles older than ~30 days are deleted; stories are never stored; the only historical copies are database backups, which are not queryable. |
| **Tests and docs** | 3,876 pytest test functions across the `tests/` tree [M] plus a concurrency harness; 41 web + 23 core unit test files; 26 Playwright specs against the real stack; extension tests; CI with a deployment-rules drift guard; ~150 design documents with a measured/derived provenance convention and pre-registered adoption bars. | The guardrail-test pattern (`test_corpus_boundaries.py`, `api-auth-guard.test.ts`) is how "consumer unchanged" gets enforced rather than promised (H.0). |

### A.3 What is already, in effect, a platform

Three things are worth stating plainly because they change the size of the job:

1. **The public catalogue routes take no identity.** `search_feed`, `stories`, `story`,
   `story_similar`, `publisher_profile`, `place_countries` accept no `Request` and no user id
   (`API_AUTH_MATRIX.md`, group C). They are already tenant-neutral read services. A gateway can
   call them today.
2. **Every derived number already carries its basis.** Distributions count distinct rated
   identities; blindspots say when they were withheld; factuality names its rater and date;
   measurements name their denominator. The honesty machinery B2B customers pay for is built.
3. **Metering, budgets and keys have working prototypes.** `web_search_spend` (durable daily
   meter), `KeyedJSONAdapter` budgets, `RWE_SEARCH_API_KEYS`, the scope-classified rate limiter.
   None is multi-tenant, all are the right shape.

### A.4 The gaps, in the order they block products

| # | Gap | Why it blocks | Blocks |
|---|---|---|---|
| G1 | **No durable article id.** The canonical URL is the primary key and the foreign key everywhere. A canonicalisation rule change (there have been several: tracking params, scheme flips, aggregator unwrapping) silently re-keys an article. IDs on the wire are URLs up to 2 KB. | A licensed dataset cannot join across deliveries; an API customer cannot store a stable reference. | API · ENT · LIC · WL |
| G2 | **Stories are not persisted and have no history.** `story_member` remembers only the last id per URL; a story's membership, distribution, trust and blindspot at any past time are unrecoverable. Window is 6 days; retention deletes members after ~30 days. | "How did coverage of X evolve", event-graph deliveries, breaking-alert audit trails, SLA replay. | ENT · LIC · B2B |
| G3 | **No archive.** Rows leave the catalogue at the retention cap; backups are the only history and are not queryable. | Any historical product. | ENT · LIC |
| G4 | **No publisher id or graph.** Identity is a name string; ownership is 14 curated cells + a Wikidata text field; co-coverage is computed per request. | Publisher-graph licensing, enterprise media monitoring by owner, white-label source packs. | ENT · LIC · WL |
| G5 | **No tenant / key / quota / metering model.** One env-var key list, in-process limiter, no usage records. | Every paid product. | all |
| G6 | **No licence classification on data.** Rows do not say which acquisition channel's terms govern them. Provider-API content, AllSides-derived leans and MBFC verdicts sit beside our own derived facts with nothing telling them apart on the wire except (for MBFC) one switch. | Redistribution risk; a licensing export cannot be filtered honestly. | API · LIC · WL |
| G7 | **No versioning of derived outputs.** Only `coverage_comparison` carries `ALGO_VERSION`; story builds carry none; scoring carries none. | A customer cannot tell a data change from an algorithm change; reproducibility clauses in licence contracts. | LIC · ENT |
| G8 | **No bulk / export / webhook surface.** Everything is request-response, paged, 200 max. | Licensing deliveries; enterprise monitoring. | LIC · ENT |
| G9 | **Schema evolution is `create_all` only.** Every table above needs adding; the first non-additive change has no story. | Safe rollout of G1–G8. | all |
| G10 | **Single process serves consumers and would serve customers.** Third-party load shares the GIL, the limiter and the story cache with hidden-view.com. | Isolation. | API · WL |

Nothing in G1–G10 is a defect in the consumer product. They are the specific things a single-tenant
consumer system does not need and a platform cannot do without.

---

## B · Reusable intelligence / data primitives we already have

Each row: the primitive, where it lives, how it is reused (**as-is** / **wrap** = call through a
new adapter without change / **extend** = additive columns or a sibling module), and which
products draw on it.

| Primitive | Lives in | Reuse | Products |
|---|---|---|---|
| Canonical URL + dedup key | `ingest.canonical_url`, `normalize_url` | as-is (it becomes the *alias* of the new article id, never replaced) | all |
| The ingestion choke point | `rss_ingest.ingest_entries` | extend: emit one provenance row per observation | all |
| Source adapter chassis (quota, retry, 429, health, budgets) | `sources.SourceAdapter`, `KeyedJSONAdapter`, `MultiSourcePoller` | as-is | all |
| Per-feed scheduler (conditional GET, adaptive interval, breaker) | `feed_schedule.py`, `feed_health` | as-is | all |
| Crawler + robots + admission ledger + crawl policy | `crawler.py`, `robots.py`, `source_admission`, `source_discovery`, `source_validation`, `source_web` | as-is | ENT · LIC (own-acquired content is the licensable content) |
| Extension capture + provisional lifecycle | `extension/`, `article_state`, `maybe_promote_feed_article` | as-is; **excluded** from platform surfaces | CON only |
| Deterministic scoring (topic, political, lean, tone) + score cache | `ingest.Scorer`, `enrich.BaselineEnricher`, `scored_articles` | extend: record `scorer_version` | API · ENT |
| Location resolver + event geography (provider-extracted, provenance per row) | `location.py`, `article_event_locations`, GKG enricher | as-is | API · ENT · LIC |
| Entities (provider + span, provenance per row) | `article_entities`, `entity_spans.py`, `gdelt_gkg.py` | as-is; extend later with an entity node table | API · ENT · LIC |
| Tier boundary (A / B / shadow) and selection with a stated budget | `corpus.py` | as-is | all (WL adds tenant-scoped tiering later) |
| Story builder (pure) with every adopted gate | `story_service.build_stories`, `clustering.py` | as-is; **wrap** its output into persisted, versioned story snapshots | API · ENT · LIC · B2B |
| Story id ledger + carry-over rule | `story_member`, `reassign_ids` | extend: becomes the seed of the durable `stories` table | all |
| Tier-B attachment (coverage joins, partition never moves) | `story_service` M4 path | as-is | API · ENT |
| Trust verdict + geo coherence + blindspot gate + withheld flag | `_cluster_trust`, `_blindspot`, `blindspotWithheld` | as-is | API · ENT · B2B |
| Bias / factuality / ownership distributions | `_distribution`, `@ih/core/logic/*-distribution.ts`, `factuality_record` | as-is (engine); the TS leaves stay client-side | API · ENT · WL |
| Story tags (direct / inherited / topic, scored, persisted) | `story_tags.py`, `story_tag` | as-is | API · ENT · LIC |
| Similar stories (IDF-weighted profile similarity, relative cut) | `story_service.similar_stories` | as-is; persist as `story_relations(kind=similar)` per build | API · ENT · LIC |
| Story intelligence (freshness, momentum, lifecycle, alerts) | `story_intelligence.py` | as-is; runs over persisted snapshots for history | ENT · B2B |
| Breaking-story edge detection (idempotent per story) | `story_events.py`, `notification_events` | as-is — this *is* the webhook event source | ENT · B2B |
| Coverage comparison L0 (deterministic, `ALGO_VERSION`) | `coverage_comparison.py` | as-is | ENT · B2B |
| Publisher profile composition (curated / counted / wikipedia / wikimedia, per-field provenance) | `publisher_service.get_publisher`, `publisher_metadata`, `publisher_wiki.verify` | as-is; the same composition writes the durable publisher row | API · ENT · LIC · WL |
| Publisher identity folding | `publisher_identity.groups` | as-is; its groups become `publisher_hosts` rows | all |
| Outlet index + SerpAPI-compatible facade + budgeted top-up | `outlet_search.py`, `/api/search.json`, `web_search_spend` | as-is — product 1's first endpoint, already key-gated | API |
| Measurement envelope (coverage / provenance / confidence) | `measurement.py`, `ADR-001` | as-is as the *wire contract* for every derived field on the platform | all |
| Rate limiter scopes + body limits | `ratelimit.py`, `reqlimits.py` | wrap: same classes, keyed by API key + plan | API · WL |
| Structured logging, request ids, in-process metrics, error reporter seam | `obs_metrics.py`, `error_reporting.py`, OBS1 | as-is; add per-key labels | all |
| Product analytics sink (allow-listed props, pseudonymous) | `product_analytics.py`, `analytics_events` | as-is for CON; the ADS attribution stream is a sibling table, not this one | CON · ADS |
| Retention policy with protected tables and per-tier age | `retention_policy.py`, `storage_lifecycle.py`, `corpus_health.run_retention` | extend: archive-before-delete hook | LIC · ENT |
| Backups + S3 lifecycle + Terraform | `deploy/ops/*`, `terraform/s3.tf` | as-is; the archive bucket is a sibling of the backup bucket | LIC |
| Guardrail tests (structural + behavioural) | `tests/test_corpus_boundaries.py`, `web/lib/api-auth-guard.test.ts` | pattern reused for "consumer unchanged" and "no licensed row leaks" | all |

**What is deliberately NOT reused for the platform:** the recommendation engine (RWE), the
Information Health report, the evidence resolver, the coach, notifications, push, email. These are
*reader-relative* — they need a reader's reads to mean anything — and the brief's products are
about the *world*, not a reader. They stay consumer-only (CON may later sell them; nothing changes
for that).

---

## C · Missing infrastructure

Grouped by what it unblocks. Everything here is **additive**.

**C.1 Identity (G1, G4)**
- `articles.article_id` — an opaque, immutable id assigned on first sight, with an alias table so
  every URL form ever seen resolves to it (E.1).
- `publishers.publisher_id` — a durable id materialised from the registry and the identity folds,
  with `publisher_hosts` and `publisher_relations` (E.3).
- `stories.story_id` promoted from "the id the ledger last served" to a first-class row with
  lifecycle (E.2).

**C.2 History and snapshots (G2, G3, G7)**
- Persisted **story builds**: one row per build (`story_builds`) and one snapshot row per story per
  build (`story_snapshots`) with membership deltas (`story_membership`). Written by the same
  post-cycle warm that already runs the build; the build stays pure.
- **Archive-before-delete**: retention writes the rows it is about to prune to the archive
  (S3, partitioned by day, JSONL/Parquet) before deleting. Articles, provenance, entities,
  locations, story snapshots, publisher snapshots. The hot database stays exactly as bounded as
  it is now.
- **Algorithm versions** on every derived artefact: `scorer_version` on scored rows,
  `build_version` on story snapshots, `registry_version` (a content hash of the CSV) on publisher
  snapshots.

**C.3 Provenance and licence class (G6)**
- `article_provenance`: append-only, one row per *observation* of an article from a channel
  (today the row keeps first-seen values and only `fetched_at` moves).
- `licence_class` computed per article from its provenance set, per publisher from its registry
  row, per fact from its source (I.2). Stored, not inferred at serve time, so an export filter is a
  `WHERE`, not a judgement.

**C.4 Tenancy, keys, entitlements, metering (G5)**
- `tenants`, `api_keys` (hash only, scopes, plan, expiry, revocation, last-used — the shape
  `api_tokens` already has, plus scopes/tenant/plan), `entitlements` (per tenant: products,
  quotas, rate plan, allowed licence classes, allowed source sets), `usage_events` (append-only)
  and `usage_daily` (rollup).
- A **platform gateway** process: the same FastAPI code base, a new `platform/` router package,
  run as its own uvicorn service (`deploy/docker-compose.yml: platform`), reading the same
  database, publishing `/v1/*`. Consumer traffic never shares its process, limiter or cache.

**C.5 Delivery surfaces (G8)**
- Bulk export jobs (`export_jobs`) producing signed S3 URLs from the archive + hot rows.
- Webhooks (`webhook_endpoints`, `webhook_deliveries`) driven by `notification_events`
  (breaking) and story-snapshot deltas (updated / merged / split). The existing
  `notification_deliveries` retry-lease design is the template.

**C.6 Operations (G9, G10)**
- Alembic migrations, seeded from the current `create_all` state; `_ensure_*_columns` retired only
  after the first Alembic revision lands (they keep working meanwhile).
- The gateway's own compose service, health endpoints, metrics labels per key/tenant, and a
  drift rule in `deploy/deployment-rules.json` so the gateway cannot be enabled without its
  secrets and its archive bucket.

**C.7 Later (CON, ADS)** — not infrastructure for the first phases, listed so the schema leaves
room: `user_entitlements` (consumer plans), `sponsorships` (labelled content units), `affiliate_links`
+ `attribution_events` (a stream separate from `analytics_events`), and the privacy-policy change
that must precede any of it (I.4).

---

## D · Proposed target architecture

### D.1 One data plane, one intelligence plane, many product facades

```
 ACQUISITION (unchanged)          feeds · APIs · Google News · GDELT · crawler · extension(consumer only)
        │
        ▼
 CANONICAL STORE  ─────────────── SQLite (hot, bounded as today)  +  ARCHIVE (S3, append-only, versioned)
   articles (+aliases, provenance, licence_class)   publishers (+hosts, relations, snapshots)
   stories (+builds, snapshots, membership, relations)   entities · locations · tags · verdicts
        │
        ▼
 INTELLIGENCE (unchanged code, persisted outputs)
   story builder → story_snapshots       spectrum / blindspot / trust → on the snapshot
   story intelligence → over snapshots   publisher profile → publisher_snapshots
   coverage comparison → on demand       breaking edge → notification_events → webhooks
        │
        ├──────────────────────────────┬─────────────────────────────────────┐
        ▼                              ▼                                     ▼
 CONSUMER PATH (unchanged)      PLATFORM GATEWAY  /v1  (new process)     DELIVERY WORKERS (new)
   engine :8000 → web → Caddy     keys · tenants · scopes · quotas         exports → S3 signed URLs
   browser · PWA · extension      metering · licence filter · envelope     webhooks → customer endpoints
   Expo app                       OpenAPI /v1 · SDK-friendly errors
                                   │
        ┌──────────────┬───────────┼─────────────┬──────────────┐
        ▼              ▼           ▼             ▼              ▼
       API            ENT         LIC            WL            B2B          (CON, ADS: consumer path + entitlements)
```

**The rule that keeps this one system:** the gateway owns *access* (who, how much, under which
licence) and *shape* (`/v1` envelopes). It owns **no intelligence**. Every answer it gives comes
from the same service functions the consumer routes call (`search.search`,
`story_service.list_stories`, `publisher_service.get_publisher`, `story_intelligence.*`,
`coverage_comparison.compare`) or from the persisted snapshots those services wrote. A product that
needs a new computation adds it to the intelligence plane, where both paths see it.

### D.2 Why a second process rather than a router in the engine

Reason (G10, measured facts): the engine runs the pollers, the story warm (5–6 s GIL-bound per
cycle [M-doc]), the per-user recommender and the consumer request path in **one Python process**.
Adding third-party traffic to it means a customer's burst degrades hidden-view.com and vice versa,
and the in-process rate limiter cannot distinguish the two populations' budgets. A second uvicorn
process over the same WAL database costs one compose service and nothing else: SQLite readers do not
block each other, and the gateway's only writes (usage, keys, jobs) are small and on their own
tables. The capacity documents' Postgres trigger — "a second host" — is *not* reached by a second
process on the same host; it is reached when the gateway needs its own host, and that is the
moment to move (H, Phase 3 decision gate).

### D.3 Why the story builder stays pure and snapshots are written beside it

The whole clustering arc (`CLUSTER_TRUST.md`, `STORY_CLUSTER_MERGES.md`, the adoption bars) rests on
`build_stories` being a pure function: same rows in, same stories out. Persisting history must not
touch that. Today the post-cycle warm builds the cache and `stabilize_ids` runs when the default
view is served (`list_stories`). The snapshot step hooks in *after* `stabilize_ids`, single-flight
and once per build fingerprint, so it records exactly the ids readers saw: *write what was just
served* as a snapshot. The build never reads the snapshot table. The id ledger keeps deciding ids
exactly as now; the `stories` row is created the first time an id is served and closed when it
stops being served. Consumer routes keep reading the cache; only the platform and the archive read
snapshots.

### D.4 Why identity is added, not swapped

Changing `feed_articles`' primary key would touch every table and every consumer path. Instead
`article_id` is a new unique column filled by a backfill and set at insert; `article_aliases` maps
every URL form to it; the consumer path keeps using canonical URLs and notices nothing. Platform
responses expose `article_id` and accept either form on lookup. The same pattern for publishers:
`publishers.publisher_id` is materialised from the registry and the identity folds; `feed_articles`
gains a nullable `publisher_id` filled at ingest and by backfill; the `publisher` name column stays.

### D.5 Multi-tenant readiness without multi-tenant data

Phase 1 tenancy is **access tenancy**: every tenant sees the same world, filtered by entitlement
and licence class. White-label (WL) adds **presentation tenancy** (branding, source packs as
`tenant_source_sets` selecting publisher ids, a tenant-scoped reader namespace via
`users.tenant_id`). **Data tenancy** — a tenant's private sources ingested for it alone — is
designed for (a `tenant_id` on `source_admission` and on provenance rows, and `corpus.select`
already selects by outlet tier, so a tenant outlet set is the same shape of input) but not built
until a customer needs it, because it is the one thing
that would make the ingestion plane tenant-aware.

### D.6 What does not change

- The engine's 71 routes, the web tier's 44 handlers, the Expo app, the extension: byte-identical.
- The engine stays private (Caddy still proxies only `web:3000`; the gateway is a *second* public
  upstream on its own host name, e.g. `api.hidden-view.com`).
- SQLite, retention bounds, backups, the four-dataset contract, every adopted clustering gate.
- The privacy policy's promises to readers — until CON/ADS are approved separately (I.4).

---

## E · Data model changes

All additive. Column types follow the existing store conventions (ISO strings for timestamps that
must sort lexically, `Text` JSON blobs where the codebase already persists dicts). Names are
proposals; the first Alembic revision fixes them.

### E.1 Article identity and provenance

| Table / column | Purpose |
|---|---|
| `feed_articles.article_id` (String 32, unique, indexed) | `ar_` + 20 hex chars, **assigned once** at insert from the canonical URL at that moment and never recomputed. Backfilled for existing rows. |
| `feed_articles.publisher_id` (FK → publishers, nullable) | resolved at ingest; backfilled; the `publisher` name column stays authoritative for display until the backfill is verified |
| `feed_articles.licence_class` (String 24, indexed) | derived from provenance at ingest and re-derived when a new observation arrives (I.2) |
| `feed_articles.scorer_version` (String 16) | version of the deterministic scorer that produced `scored`; the score cache gains the same column |
| `article_aliases(alias TEXT PK, article_id, kind, first_seen)` | every URL form ever seen for the article: raw, canonical, aggregator-wrapped, scheme-flipped, provider `external_id`. Lookup by any form resolves to one id; a future canonicalisation change adds aliases instead of re-keying |
| `article_provenance(id PK, article_id idx, channel, provider, source_ref, external_id, observed_at, published_at_seen, licence_class)` | **append-only, one row per observation**. Today `upsert_feed_article` keeps first-seen values; this keeps the fact that NewsAPI *also* delivered it, when, and under which terms. It is the evidence behind `licence_class` and the "we hold this from our own crawl" claim LIC depends on |

### E.2 Stories: durable rows, builds, snapshots, relations

| Table | Purpose |
|---|---|
| `stories(story_id PK, first_served_at, last_served_at, status{active,closed,merged_into,split_from}, successor_id, representative_article_id, topic)` | the durable event row; created when an id is first served, closed when it leaves the window; merge/split recorded via the ledger's existing exclusivity rules |
| `story_builds(build_id PK, built_at, build_version, window_start, window_end, tier_a_rows, tier_a_total, stories, config_hash)` | one row per served build: the algorithm version and the config it ran under (every `RWE_CLUSTER_*` / `RWE_STORY_*` value hashed) |
| `story_snapshots(build_id, story_id, PK both; title, summary, topic, total_coverage, publisher_count, distribution JSON, blindspot_side, blindspot_withheld, cluster_trust, geo_coherence, countries JSON, freshness_band, lifecycle, hero JSON, tags JSON)` | the story as served at that build — the wire `StoryModel` minus the coverage list |
| `story_membership(story_id, article_id, joined_build, left_build NULL, attached BOOL)` | membership history; `attached` marks Tier-B attachment (no vote) exactly as `tierB` does on the wire today |
| `story_relations(from_story, to_story, kind{similar,continuation,merged,split,shared_tag}, score, build_id)` | the event graph edges LIC sells: `similar` from the existing similar-stories scorer, `merged`/`split` from the ledger, `continuation` from the consumer continuation logic when enabled, `shared_tag` from `story_tag` |

Retention: snapshots follow the *archive-before-delete* rule (E.5); the hot database keeps the last
N builds (proposal: 7 days of builds, ~1,000 builds at a 10-minute cycle ≈ 1–1.5 M snapshot rows
[P] at ~1,300 stories per build [M-doc]; measure before fixing N).

### E.3 Publishers: id, hosts, graph, snapshots

| Table | Purpose |
|---|---|
| `publishers(publisher_id PK, canonical_name, registry_key, lean, lean_source, country, region, city, scope, kind, credibility, factuality, factuality_source, factuality_asof, ownership, ownership_source, ownership_owner, tier, lifecycle_state, registry_version, updated_at)` | materialised from `outlet_registry.csv` (which stays the curated source of truth) + `publisher_identity.groups` for unregistered identities. The CSV loader becomes idempotent "sync registry → publishers"; nothing reads the CSV at request time any more on the platform path (the consumer path may keep doing so until measured) |
| `publisher_hosts(host PK, publisher_id, source{registry,identity_fold,admission}, first_seen)` | every host that resolves to the publisher — the join key for URLs, the crawler and the outlet index |
| `publisher_relations(from_id, to_id, kind{owner,parent,syndicates_from,same_group}, source, asof)` | the graph. Seeds: curated `ownership_owner`, Wikidata `parent` (P749/P127) from `publisher_metadata`, wire-service syndication from the existing `is_wire` classification. Co-coverage is *computed* (from `story_membership`), not stored as a relation |
| `publisher_snapshots(publisher_id, day, articles, stories, co_coverage JSON, topics JSON, countries JSON, tone JSON, gaps JSON)` | the counted profile per day — what `publisher_catalog_stats` computes per request today, persisted so ENT can chart it and LIC can deliver it |

### E.4 Tenancy, keys, entitlements, metering, delivery

| Table | Purpose |
|---|---|
| `tenants(tenant_id PK, name, kind{internal,developer,enterprise,white_label}, status, created_at)` | the `hv` tenant is the consumer product itself |
| `api_keys(key_id PK, tenant_id, key_hash unique, prefix, label, scopes JSON, plan, created_at, expires_at, revoked_at, last_used_at)` | hash-only like `api_tokens`; prefix (`hv_live_…` / `hv_test_…`) shown once; scopes are the F.2 vocabulary |
| `entitlements(tenant_id, product, enabled, quota_month, rate_per_min, licence_classes JSON, source_set_id NULL, since)` | what a tenant may read and how much |
| `usage_events(id PK, ts idx, tenant_id idx, key_id, endpoint, units, status, request_id, latency_ms)` | append-only metering; written by the gateway middleware in batches |
| `usage_daily(tenant_id, key_id, day, endpoint, units, requests, PK all four)` | rollup; the quota check reads this plus the in-memory bucket |
| `tenant_source_sets(source_set_id PK, tenant_id, name)` + `tenant_source_members(source_set_id, publisher_id)` | WL / ENT source packs |
| `export_jobs(job_id PK, tenant_id, kind, params JSON, status, created_at, completed_at, s3_key, bytes, rows, licence_classes JSON)` | bulk deliveries |
| `webhook_endpoints(id PK, tenant_id, url, secret_hash, events JSON, status)` + `webhook_deliveries(id PK, endpoint_id, event_id, attempts, next_attempt_at idx, delivered_at, last_status)` | same lease/retry shape as `notification_deliveries` |

### E.5 Archive (not a table — a bucket layout)

```
s3://<archive-bucket>/v1/articles/dt=YYYY-MM-DD/part-*.jsonl.gz       article + provenance + entities + locations, licence_class on every row
s3://<archive-bucket>/v1/story_snapshots/dt=YYYY-MM-DD/part-*.jsonl.gz
s3://<archive-bucket>/v1/story_membership/dt=YYYY-MM-DD/…
s3://<archive-bucket>/v1/publishers/dt=YYYY-MM-DD/…                  full publisher table + snapshots, registry_version
s3://<archive-bucket>/v1/_manifests/dt=YYYY-MM-DD.json                 counts, versions, config hash, sha256 per part
```

Written by a nightly job (and by retention immediately before any delete). Schema-versioned by the
`v1/` prefix. Terraform adds the bucket, lifecycle and a read-only IAM role for exports. Provider-
restricted rows are archived (we hold them for our own product) but flagged; exports filter on
`licence_class`.

### E.6 Consumer tables: one nullable column

`users.tenant_id` (default `hv`) — the only change to a reader table, so WL readers can exist in
their own namespace. Nothing on the consumer path reads it until WL ships.

### E.7 Migrations

Introduce Alembic with revision 0001 = the current `create_all` schema (autogenerate, then hand-
audit against `_ensure_*_columns`). Every table above is a later revision. Tests keep `create_all`
(they are in-memory). Production runs `alembic upgrade head` from `update.sh` before the API
restarts, behind the existing pre-deploy snapshot. The `_ensure_*` methods stay until 0001 is on
production and are then deleted in one commit.

---

## F · API architecture

### F.1 Two front doors, one engine

| | Consumer path (unchanged) | Platform gateway (new) |
|---|---|---|
| Host | `hidden-view.com` → Caddy → `web:3000` → `api:8000` (private) | `api.hidden-view.com` → Caddy → `platform:8100` |
| Identity | Google session / per-user bearer | `Authorization: Bearer hv_live_…` (API key) |
| Authorisation | reader = owner of the data | tenant entitlements + key scopes + licence classes |
| Limits | per-user/IP in-process | per-key plan, shared counters in `usage_daily` + local bucket |
| Payload | today's `ArticleModel`, `StoryModel`, … | `/v1` envelopes (F.4) built *from* the same service dicts |
| Versioning | additive-optional, unversioned | `/v1` path version; additive within v1; deprecation headers |
| Docs | internal OpenAPI | published OpenAPI 3.1 for `/v1` only; SDK generation later |

The gateway imports the same modules (`search`, `story_service`, `publisher_service`,
`story_intelligence`, `coverage_comparison`, `outlet_search`) and the same `Store`. It never
imports `personalize`, `api_server` (the recommender), `coach_service`, or anything under
`/api/me` — enforced by a structural test in the spirit of `test_corpus_boundaries.py`.

### F.2 Scopes

`articles:read` · `stories:read` · `stories:history` · `spectrum:read` · `publishers:read` ·
`publishers:graph` · `entities:read` · `search:web` (the SerpAPI-shaped facade) ·
`exports:create` · `webhooks:manage` · `usage:read`. Plans are named bundles of scopes plus quotas;
scopes are what a key carries; entitlements are what a tenant may be granted.

### F.3 Endpoints (v1)

| Method · path | Backed by | Notes |
|---|---|---|
| `GET /v1/articles` | `search.search` | q, publisher_id, topic, lean, country, language, from/to, sort, cursor. Snippet ≤ 300 chars, never `body` |
| `GET /v1/articles/{article_id}` · `GET /v1/articles/by-url?url=` | `article_aliases` → row | any alias form resolves |
| `GET /v1/articles/{id}/story` | `story_membership` current | the story the article sits in now |
| `GET /v1/stories` | `story_service.list_stories` (cache) | every consumer filter (topic, publisher, lean, country, blindspot, tag, type, dates, sort) |
| `GET /v1/stories/{story_id}` | cache → else latest snapshot | with `coverage[]` (article ids + publisher ids + lean + attached flag) |
| `GET /v1/stories/{id}/spectrum` | snapshot | distribution, blindspot + withheld, low-credibility list, trust, factuality distribution when published, ownership distribution |
| `GET /v1/stories/{id}/intelligence` | `story_intelligence.compute_intelligence` | anonymous form only (no `newSinceLastVisit`) |
| `GET /v1/stories/{id}/history` | `story_snapshots` + `story_membership` | `stories:history`; per-build series of coverage, distribution, trust; membership joins/leaves |
| `GET /v1/stories/{id}/related` | `story_relations` | kinds filterable |
| `GET /v1/stories/{id}/coverage-comparison?article_id=` | `coverage_comparison.compare` | L0, with `algoVersion` |
| `GET /v1/publishers` · `GET /v1/publishers/{publisher_id}` | `publishers` + `publisher_service.get_publisher` | profile with per-field provenance; `publishers:read` |
| `GET /v1/publishers/{id}/graph` | `publisher_relations` + computed co-coverage | `publishers:graph` |
| `GET /v1/publishers/{id}/timeseries` | `publisher_snapshots` | ENT |
| `GET /v1/tags` · `GET /v1/tags/{tag}/stories` | `story_tag` | |
| `GET /v1/entities?name=` | `article_entities` | name-keyed until an entity node table exists |
| `GET /v1/countries` | `feed_article_country_facets` | |
| `GET /v1/search.json` | the existing SerpAPI-compatible facade, moved under `/v1` with a redirect kept at the old path | `search:web` |
| `POST /v1/exports` · `GET /v1/exports/{job_id}` | `export_jobs` + archive | signed URL, licence-class filtered by entitlement |
| `POST /v1/webhooks` · `DELETE …` · `GET …/deliveries` | `webhook_endpoints` | events: `story.breaking`, `story.updated`, `story.merged`, `story.split`, `publisher.updated` |
| `GET /v1/usage` | `usage_daily` | the tenant's own meter |
| `GET /v1/health` · `GET /v1/openapi.json` | | |

Pagination is cursor-based on the platform (`cursor`, `next_cursor`), even where the consumer route
is offset-based; the cursor encodes the consumer offset for the cached story list and the sort key
for the catalogue.

### F.4 The response envelope

Every `/v1` object carries what the consumer already carries implicitly, made explicit:

```jsonc
{
  "data": { ... },
  "meta": {
    "requestId": "…",
    "asOf": "2026-09-05T02:00:00Z",          // the build/snapshot time the answer reflects
    "versions": { "build": "2026.09.a", "scorer": "1", "registry": "sha256:…", "algo": {"coverageComparison": 1} },
    "licence": { "class": "own_derived", "attribution": ["GDELT Project", "Wikidata (CC0)"] },
    "measurement": { "coverage": {"observed": 9, "eligible": 12, "basis": "rated_publishers"},
                     "provenance": {"kind": "derived", "source": "story_builder"} }   // ADR-001 shape, per derived field group
  }
}
```

Derived fields that are withheld on the consumer path (`blindspotWithheld`, `factualityPublished`)
stay withheld here with the same flags; the platform never publishes a number the consumer product
would refuse to show.

### F.5 Metering and limits

Middleware order: authenticate key → resolve tenant + entitlements (cached 60 s, like
`corpus._admitted`) → scope check → plan rate bucket (the existing `RateLimiter`, keyed by key id)
→ monthly quota check (`usage_daily`) → handler → append `usage_events` (batched, flushed every
second or 100 rows, fail-soft like every observational write in this codebase) → response headers
`X-RateLimit-*`, `X-Usage-Month`. Units are per request in phase 1; exports are metered by rows.

### F.6 Errors

The engine's `{error: {code, message}}` envelope, with a fixed code vocabulary
(`unauthenticated`, `forbidden_scope`, `forbidden_licence`, `quota_exceeded`, `rate_limited`,
`not_found`, `invalid_cursor`, `unavailable`). `429` carries `Retry-After` exactly as the engine's
limiter does today.

---

## G · Revenue-product mapping

| Product | What it sells | Primitives (B) | Endpoints (F.3) | Missing before it can ship | Licence boundary (I) |
|---|---|---|---|---|---|
| **1 · API** | search over the catalogue; story clusters with spectrum; publisher profiles; the outlet-discovery search | scoring, catalogue search, story builder + trust, publisher profile, outlet index | articles, stories, spectrum, publishers, tags, countries, `search.json` | C.1 identity, C.4 keys/metering, gateway, licence class | serves `own_derived` + `metadata_public` fields; **provider-restricted rows excluded** from results or reduced to id + publisher + timestamp (decision I.2); AllSides-derived lean requires the rating-licence decision (I.3) |
| **2 · ENT** | monitoring of coverage over time: who covered what, from which side, when it broke, how it moved; alerts; comparisons | story snapshots + intelligence, breaking edge, publisher snapshots, coverage comparison, tags | history, intelligence, related, coverage-comparison, publishers/timeseries, webhooks, exports | C.2 history + archive, C.5 webhooks/exports, dashboards (a later web surface on the same `/v1`) | as API, plus history depth is bounded by the archive start date — ENT cannot sell history that predates the archive |
| **3 · LIC** | the event graph: stories, memberships, relations, publisher graph, spectrum per event, versioned and reproducible | stories/relations/membership/publisher tables, archive, versions | exports (bulk), `/v1/stories/{id}/history`, `/v1/publishers/{id}/graph` | everything in C.1–C.3 and C.5; contract-grade manifests (sha256, counts, versions) | **only `own_derived` and `metadata_public` classes are licensable**; provider content, third-party ratings, extension-born rows, image URLs and descriptions beyond a snippet are excluded by `WHERE`, and a test asserts an export never contains them |
| **4 · WL** | the consumer product under another brand, on chosen sources | the whole consumer web app + `@ih/core`, plus tenancy | consumer path with `users.tenant_id`, `tenant_source_sets`; theming via the existing design tokens | E.4 tenancy, E.6, a per-tenant branding config, per-tenant source packs in `corpus.select`; a second Next deployment per tenant (or host-based tenant resolution) | as the consumer product; the tenant's readers' data stays under Hidden View's privacy policy unless a DPA says otherwise |
| **5 · B2B** | premium features on top of 1–2: coverage comparison, similar-story graph, alerts with SLAs, higher quotas, seats | coverage comparison, similar, breaking, intelligence | the same `/v1` under higher plans + `webhooks` | plans + entitlements (C.4) | as API/ENT |
| **6 · CON** (later) | reader plans: history depth, alerts, export, ad-free | the consumer product; `user_entitlements` | consumer path | a plan table + a billing provider + `PROTECTED_TABLES` entry; nothing on the intelligence plane | reader data only; no change to what is collected |
| **7 · ADS** (later) | labelled sponsorship units; affiliate/referral links with attribution | the placement slots in the existing card river; a *separate* attribution stream | consumer path | `sponsorships`, `affiliate_links`, `attribution_events`, a policy revision, consent UX; **precondition: I.4** | the privacy policy currently says "no third-party advertising" and "we do not sell personal data" — ADS is only possible with a revised, consented policy, and attribution must never join `reads` |

The mapping shows the point of the brief: products 1–5 are **five plans over one gateway** once
C.1–C.5 exist; 6–7 are **consumer-path features** that touch none of the intelligence plane.

---

## H · Implementation phases

Each phase ships behind a flag, defaults off, is validated on beta and then defaults on — the
shipped pattern. Each phase names its bars first, and the guard tests it adds. No phase modifies a
consumer payload.

### Phase 0 — Foundations (identity, provenance, migrations, archive) · 2–3 weeks [P]

- Alembic 0001 from the current schema; `update.sh` runs migrations after the pre-deploy snapshot.
- `article_id` + `article_aliases` + backfill; `article_provenance` written by `ingest_entries`
  on every observation (additive: `upsert_feed_article` unchanged, one extra insert).
- `publishers` + `publisher_hosts` materialised from the registry and identity folds;
  `feed_articles.publisher_id` filled at ingest and by backfill; `registry_version`.
- `licence_class` derivation (I.2) at ingest + backfill; `scorer_version` on scored rows.
- Archive bucket (Terraform), nightly `archive_export.py`, archive-before-delete in
  `storage_lifecycle.run_cleanup` (fail-closed: if the archive write fails, the prune is skipped
  and reported — keeping too much is the safe failure).
- **Bars:** consumer e2e (26 specs) and the engine suite green unchanged; backfill covers 100 % of
  rows [M at deploy]; alias lookup resolves every URL form the catalogue has ever stored for a
  sampled 1,000 articles; archive manifest counts equal pruned counts for 7 consecutive days.
- **Guards:** a structural test that no consumer route reads `article_provenance` or the
  archive; a behavioural test that `ArticleModel` and `StoryModel` wire payloads are
  byte-identical before/after (golden fixtures).

### Phase 1 — Platform gateway, keys, tenants, metering, read endpoints · 3–4 weeks [P]

- `platform/` package; compose service `platform:8100`; Caddy host `api.hidden-view.com`;
  deployment-rules entry.
- `tenants`, `api_keys`, `entitlements`, `usage_events`, `usage_daily`; key minting CLI (hash
  only, shown once) — the `api_tokens` discipline.
- `/v1` read endpoints for articles, stories, spectrum, intelligence, publishers, tags, countries,
  `search.json`; envelope; cursor pagination; metering middleware; OpenAPI.
- **Bars:** p95 latency on cached story list ≤ consumer path + 20 ms; a key with no
  `stories:read` scope gets `403 forbidden_scope` on every stories route (matrix test, like
  `api-auth.spec.ts`); `usage_daily` equals the count of 2xx requests in a replayed log to the row;
  a provider-restricted article never appears in a `/v1/articles` result under a default plan
  (behavioural test over a seeded catalogue).
- **Guards:** structural test that `platform/` imports nothing reader-relative; the engine's
  process must not import `platform/`.
- **Ships product 1** (developer plan) to design partners.

### Phase 2 — Story persistence, history, relations, publisher graph, delivery · 4–5 weeks [P]

- `stories`, `story_builds`, `story_snapshots`, `story_membership`, `story_relations` written by
  the post-cycle warm (single-flight, after `stabilize_ids`); hot retention of N builds; archive.
- `publisher_relations` seeded from curated ownership, Wikidata parent, wire classification;
  `publisher_snapshots` nightly.
- `/v1/stories/{id}/history`, `/related`, `/publishers/{id}/graph`, `/timeseries`;
  exports (jobs → S3 signed URLs, licence-filtered, manifested); webhooks from
  `notification_events` (breaking) and snapshot deltas.
- **Bars:** snapshot of build *b* reproduces the `StoryModel` served from the cache for build *b*
  field-for-field (test over a fixture build); id merge/split events in `stories.status` agree with
  `reassign_ids` decisions on a replayed day; an export's manifest sha256 verifies; a webhook
  delivery retries with the same lease semantics `notification_deliveries` proves.
- **Guards:** the build stays pure — `build_stories` still never touches the store (existing
  test); a test that every exported row has a `licence_class` in the tenant's allowed set.
- **Ships products 2, 3 (first deliveries), 5 (plans).**

### Phase 3 — Tenancy for white-label and enterprise · 4–6 weeks [P]

- `users.tenant_id`, `tenant_source_sets`, host-based tenant resolution in the web tier, tenant
  branding config, tenant-scoped `corpus.select` for the reader-facing story list.
- Decision gate: if the gateway needs its own host, move the platform tables (and only them) to
  Postgres first; the intelligence plane's SQLite stays until the second-host trigger the
  capacity documents define is actually reached.
- **Bars:** the `hv` tenant's every consumer payload unchanged (golden fixtures again); a WL tenant
  cannot read another tenant's readers (matrix test).
- **Ships product 4.**

### Phase 4 — Consumer monetisation and advertising hooks (only if approved separately)

- `user_entitlements` + billing provider; then, gated on a revised privacy policy and consent UX
  (I.4): `sponsorships` as labelled units in the existing card river, `affiliate_links` and an
  `attribution_events` stream that is *not* `analytics_events` and never joins `reads`.
- **Ships 6, then 7.**

### H.0 The guard that applies to every phase

A new `tests/test_platform_boundaries.py`, in the mould of `test_corpus_boundaries.py`:

| Invariant | Guard |
|---|---|
| No consumer route's payload changes | golden JSON for `ArticleModel`, `StoryModel`, `PublisherProfileModel`, `RecommendationModel` over a fixed fixture catalogue |
| The intelligence plane has no tenant awareness (Phases 0–2) | structural: `story_service`, `clustering`, `ingest`, `rss_ingest`, `sources` import nothing from `platform/` and reference no `tenant` |
| Provisional (extension-born) rows never reach a platform surface | behavioural, over a seeded provisional row, every `/v1` list endpoint and every export |
| Provider-restricted and third-party-rating fields never leave under a plan that lacks the class | behavioural per licence class |
| Reader tables are never read by the gateway | structural: `platform/` references none of `reads`, `saved_articles`, `rec_*`, `report_snapshots`, `notifications`, `push_*`, `api_tokens` |

---

## I · Risks and legal / provenance boundaries

### I.1 The honest inventory of where every fact comes from

| Fact on the wire | Origin | Terms (to confirm with counsel — stated as the working assumption) |
|---|---|---|
| URL, headline, publication time, publisher name | publisher feeds, sitemaps, provider APIs, Google News RSS, GDELT | headline + link + timestamp is the industry-standard redistributable unit **when acquired from the publisher's own machine-readable offer**; when acquired from a provider API it is governed by that provider's terms (next row) |
| Descriptions / deks | as above | a *snippet* (short, attributed, linked) is the defensible form; the consumer path already clamps summaries to 320 chars; the platform clamps to 300 and never serves `body` |
| `body` (`content:encoded`) | RSS feeds that ship full text | held for our own scoring only; **never served, never exported**; a test asserts it |
| Provider-API items (NewsAPI, GNews, NewsData, MediaStack, Currents, Guardian Open Platform) | six keyed adapters | provider terms typically **prohibit redistribution / resale / caching beyond a window** and may require attribution; each provider's terms must be read and recorded in `licence_class` rules. Working assumption: **`provider_restricted` → excluded from LIC exports and reduced on the API** to fields we would hold anyway once our own crawl re-observes the article (`article_provenance` shows when that happens — an article seen from both NewsAPI *and* the publisher's own feed carries `metadata_public`) |
| GDELT DOC + GKG (entities, locations, sharing image) | GDELT | open with attribution (GDELT's stated terms); attribution string travels in `meta.licence.attribution`; confirm the commercial-redistribution reading |
| Google News RSS items | aggregator | unwrapped to the publisher URL where possible; the aggregator is a *delivery* not a *source*; treat as `metadata_public` only after the publisher URL is resolved |
| AllSides-derived lean (`outlet_registry.csv`) | curated from AllSides pages | **AllSides' ratings are its own published work and its terms for commercial reuse must be confirmed** — the consumer product's in-app display with attribution is one question, redistribution through an API or a dataset is another. Working assumption: **the lean requires a licence or a replacement before it ships on `/v1` or in exports**; the L/C/R *distribution of a story* is a derived fact but it is derived from the licensed labels, so it inherits the restriction (I.3) |
| MBFC factuality | curated, gated by `RWE_PUBLIC_FACTUALITY` (off) | already treated as **unlicensed for redistribution**; stays off on the platform until licensed |
| Ownership (14 curated, Wikidata-derived parent) | public record / Wikidata CC0 | public record is fine; Wikidata is CC0 |
| Wikipedia descriptions | Wikipedia | **CC BY-SA** — attribution and share-alike; a description served on `/v1` must carry attribution and the licence, or be replaced by a counted-facts sentence. Working assumption: **exclude `description` from `/v1` and exports** and keep it consumer-only until reviewed |
| Publisher logos (Commons / site favicons) | Commons (per-file licences) / publisher sites | trademarks — nominative use in the consumer product; **not part of any export**; on `/v1` served as a URL with `logo_source` only |
| Images (article hero, story hero) | publisher feeds (URL only, hot-linked) | URLs only; **exports exclude them**; API returns the URL with `imageSource` and the publisher attribution |
| Our clusters, distributions, trust verdicts, blindspots, tags, coverage stats, timelines, publisher counted profiles | derived here | **`own_derived` — the licensable asset.** Where a derived fact is computed *from* a restricted input (lean distribution from AllSides labels) it carries the input's class, not ours |
| Reader reads, reports, recommendations, feedback, saved, notifications, extension-born provisional articles | readers | **never** on the platform, never in an archive export, never in an aggregate that could be re-identified; `PROTECTED_TABLES` stays the floor |

### I.2 The `licence_class` vocabulary (stored per article, per publisher fact, per export row)

| class | meaning | API default plan | LIC export |
|---|---|---|---|
| `own_derived` | computed by Hidden View | full | yes |
| `metadata_public` | URL/headline/time/publisher observed from the publisher's own offer (RSS, sitemap, crawl) or GDELT | full | yes |
| `provider_restricted` | held only via a keyed provider API | id, publisher, time, story membership; no headline/snippet unless the plan carries the class | no |
| `third_party_rating` | AllSides lean, MBFC factuality | withheld until licensed (I.3) | no |
| `cc_by_sa` | Wikipedia text | withheld pending review | no |
| `reader_private` | provisional / extension-born | never | never |

An article's class is the *most restrictive* class among the fields being served, computed from
`article_provenance`; a row re-observed from a public channel upgrades. Classes are data, so a
licence decision changes a rule and a backfill, not code paths.

### I.3 The AllSides question is the first decision, not the last

Every spectrum feature — the product's thesis — derives from `outlet_registry.csv`'s lean, and the
lean is transcribed from AllSides. For the consumer product this has been treated as fair display
of a public rating with the rater named. For an API or a dataset it is redistribution of a rated
third party's work. Options, to be decided before Phase 1 ships spectrum on `/v1`:

1. **License AllSides (and MBFC) for redistribution** — the cleanest; cost unknown.
2. **Own rating layer** — a Hidden View lean derived from *behaviour* (the validated Politosphere
   ideal-point method, `lean_corr 0.57 ± 0.19` [M-doc], or reader-diet co-consumption once
   `rec_events`/`reads` volume permits) — a research programme, not a phase; would make `own_derived`
   true for the spectrum.
3. **Serve the spectrum as counted facts over *named* rated publishers with rater attribution and
   no numeric lean** — defensible for coverage-gap claims ("no outlet AllSides rates Left covered
   this"), weakest commercially.

The design keeps all three open: lean fields carry `lean_source`, distributions carry
`provenance.source`, and the class gate withholds rather than fabricates.

### I.4 Privacy boundaries the products must not cross

- The privacy policy (`docs/PRIVACY_POLICY.md`) promises no sale of personal data, no third-party
  advertising, single-purpose use of reads. Products 1–5 comply by construction (H.0 guards).
  Products 6–7 require a **revised policy, a dated change notice, and consent** before the first
  line of ADS code; affiliate attribution must be its own stream, never joined to `reads`.
- White-label readers: a WL tenant's readers are Hidden View's data subjects unless a data-
  processing agreement says otherwise; `users.tenant_id` is a namespace, not a transfer.
- `analytics_events` are pseudonymous and allow-listed; per-key `usage_events` must stay free of
  query text (endpoint + units only) so a customer's searches are not a retained log.
- Publisher goodwill: the honest User-Agent, robots enforcement, per-host intervals and the
  outstanding ToS review (`CRAWLER_PRODUCTION_READINESS.md`) are *preconditions* for LIC, because
  the licensable content is exactly the content we acquire ourselves.

### I.5 Technical risks and their mitigations

| Risk | Mitigation |
|---|---|
| Identity backfill re-keys or orphans rows | `article_id` assigned from the stored canonical URL, aliases cover every stored form, foreign keys stay on the URL; the backfill is idempotent and dry-run first (`backfill_published_at.py` pattern) |
| Snapshot writes slow the poll cycle | written on the warm thread after `stabilize_ids`, batched, fail-soft; measured against the 600 s cycle before default-on (bar: < 5 % of cycle) |
| Hot database growth from snapshots | N-build hot retention + archive; sized in Phase 2 against the storage harness (`storage_bench.py`) before the value of N is fixed |
| Two processes on one SQLite file | WAL readers do not block; gateway writes are small and on their own tables; `busy_timeout` already 5 s; the concurrency harness (`tests/concurrency`) gains a two-process case |
| Customer load degrades consumers | separate process and limiter; separate host name; the consumer story cache is not shared |
| Algorithm change invalidates a customer's stored data | `build_version` + `config_hash` on every snapshot; adoption bars already require counterfactual measurement; a version bump is a documented event with a changelog on `/v1` |
| A licence class is wrong | classes are data; a rule change + backfill fixes every row; export manifests record the class rules' version |
| Key leakage | hash-only storage, prefixes, expiry, revocation, `last_used_at`, and the OBS1 log line per refusal |
| Schema evolution without a migration tool | Alembic in Phase 0, before any platform table exists |

### I.6 What this design deliberately does not do

- It does not move the intelligence plane off SQLite, add a queue, a cache server or a second host.
  Every one of those has a measured trigger in the capacity documents and none is reached by
  adding a gateway process.
- It does not build an entity-resolution layer (QIDs), a full-text article store, embeddings, or an
  LLM summariser. Each is a separate research decision with its own bars; the schema leaves room
  (`article_entities` already carries `source`; the archive is versioned).
- It does not decide the AllSides/MBFC licensing question — it makes the platform *safe under
  either answer*.
- It does not touch the recommender, the report, the coach, notifications, push or email.

---

## Implementation note — what shipped as the minimum foundation

**One decision revised against §D.2 before building: no separate gateway process.** The engine
is not internet-facing, carries zero third-party load, and a second uvicorn service would be a
second thing to operate, monitor and deploy for a benefit nobody has measured. The platform is a
self-contained package (`examples/platform_api/`) **mounted into the engine behind
`RWE_PLATFORM_API=1`** (default off — a deployment that has not opted in has no `/v1` route), with
a standalone factory (`platform_api.app.create_app`) that runs it as its own process the day
isolation is worth one. The package boundary is what preserves that option, and
`tests/test_platform_boundaries.py` pins it.

Shipped (all additive; consumer routes, payloads and apps byte-identical):

| item | where |
|---|---|
| `article_id` (minted once) + `article_aliases`; `publisher_id` (pure function of the identity key) + `publishers` / `publisher_hosts`; `licence_class`; `scorer_version` — stamped at the one ingest choke point, self-healing on re-poll, backfilled by `identity_backfill.py` | `store.py`, `identity.py`, `licence.py`, `rss_ingest.py` |
| `article_provenance` — one row per (article, channel, source) with first/last observation and count | `store.py` |
| Story history: `stories` (lifecycle: active / closed / merged + successor / split origin), `story_builds` (version + config hash + registry snapshot), `story_snapshots` (on change only), `story_membership` (joins/leaves); recorded after the served unfiltered build, fail-soft | `story_history.py`, `story_service.py` |
| Versions: `ingest.SCORER_VERSION`, `story_service.BUILD_VERSION` + `build_config_hash()`, `identity.registry_version()`; every `/v1` response carries them | |
| Archive: gzipped JSONL partitions + manifests; archive-before-delete in retention (fails closed); story-history hot window; `archive_export.py`; off-host sync under `archive/` | `archive.py`, `corpus_health.py`, `storage_lifecycle.py`, `retention_policy.py`, `deploy/ops/backup-offhost.sh` |
| `/v1`: tenants, hashed keys with scopes / plans / per-key limits / expiry / revocation, per-key rate, per-tenant monthly quota, durable meter, licence-class withholding, ratings as a deployment switch, cursor paging, stable error codes | `platform_api/`, `platform_keys.py`, `docs/PLATFORM_API.md` |

Not built, on purpose: billing, SSO, data tenancy, bulk exports through the API, webhooks, an
entity node table, Alembic (every change here is additive and uses the store's existing
`_ensure_*` discipline; the first non-additive change still needs a migration tool).

**Phase 1 (the minimum commercial access layer) shipped on that foundation** — 22 authenticated
endpoints plus public `/v1/health`, `/v1/openapi.json` and `/v1/docs`; `X-API-Key` beside the
bearer; `/v1/me`; coverage comparison, tags, entities, countries, publisher discovery (filters,
by-host, per-publisher articles and stories) and outlet search, each a wrapper over the service the
consumer route already calls; keyed `/v1` requests exempt from the engine's per-IP limiter, keyless
ones throttled at its `auth` rate. Story counts on `/v1` are over the members the platform can
serve. Reference: `docs/PLATFORM_API.md`. Phase 2 (`/v1/graph`, event edges) and Phase 3
(presentation tenancy) remain as designed in §H.

## Approval checklist

Approve, amend or reject each line; implementation starts only on the approved subset.

1. Second process (`platform:8100`, `api.hidden-view.com`) rather than a router in the engine (D.2).
2. Additive identity: `article_id` + aliases, `publisher_id` + hosts, durable `stories` (D.4, E.1–E.3).
3. Story history via post-warm snapshots; the builder stays pure (D.3, E.2).
4. Archive-before-delete to S3; hot database bounds unchanged (E.5).
5. `licence_class` as stored data with the I.2 vocabulary; provider-restricted and third-party-
   rating classes withheld by default (I.2).
6. The AllSides decision path (I.3) opened now, decided before spectrum ships on `/v1`.
7. Access tenancy in Phase 1; presentation tenancy in Phase 3; data tenancy designed, not built (D.5).
8. Alembic before any platform table (E.7).
9. Phases 0 → 1 → 2 → 3 in that order; 4 only after a separate privacy decision (H, I.4).
10. The boundary guard suite in H.0 lands with Phase 0 and gates every later phase.
