# Platform API (`/v1`) — validation before the first external user

How the commercial surface is validated against a real catalogue, what the rehearsal found, and
what a production run must show before a private external user gets a key. The battery itself is
`examples/platform_validate.py`; the production procedure is `deploy/ops/platform-enable.sh`.

## The battery

One run exercises every capability an external developer would rely on, then sweeps every payload
it received for what must never leave, then measures quality and latency:

| section | what is checked |
|---|---|
| health / docs | `/v1/health` without a key, versions stamped, OpenAPI schema with security declared |
| me | internal + developer keys resolve to their plans, scopes, classes, published switches |
| stories | the served build: ≥2 publishers per story, completeness (title, ids, publishers), summaries, tags, event countries, no title lost to withholding, clustering shape (largest-story share, articles per publisher) |
| story detail | coverage rows carry identity + licence, count equals `totalCoverage`, publishers agree, delivery present exactly when the class allows, freshness + lifecycle |
| similar / intelligence | related events never include the story itself; freshness, momentum, lifecycle present; no reader-relative field |
| coverage comparison | answers with `available`/`reason`; evidence rows carry identity + licence; evidence delivery follows the class; viewpoint findings withheld without ratings |
| history | snapshots + membership; distribution withheld without ratings |
| tags / entities | vocabulary → retrieval: returned stories carry or mention the tag; per-article entities with GDELT attribution; entity lookup results verified to carry the entity |
| article search | a query from the top story's own title: precision over visible text, newest-first ordering, ids on every row |
| publishers | listing, name search, host resolution to the article's publisher, profile, per-publisher articles (all belong) and stories (all carry it), `?publisher_id=` agreement, exact topic filter |
| countries / outlets | event-geography facets → exact `country=` filter; outlet index by place and by name (503 reported when the index is absent) |
| refusals | no key → 401, query-string key refused, unknown key, bad cursor, unknown story, lean filter without ratings, developer key without `stories:history` |
| exposure sweep | over every payload: no `body`, no `reader_private`, no provisional, no known-hidden reference (local mode supplies the set from the database), no rating value while unpublished, no Wikipedia text while unpublished, snippets ≤ 300 chars, the developer key never receives a restricted row's delivery |
| latency | p50 / p95 / max per endpoint over `--repeat` calls |
| metering | `/v1/usage` delta equals every request sent this run (refusals included); `X-Usage-Month` stamped; record errors (local mode) |

`PASS` / `WARN` / `FAIL` / `SKIP`; exit 1 on any FAIL. A WARN is a quality bar not met, a SKIP is
a capability the catalogue cannot exercise (no entities, no geography, no outlet index).

## Rehearsal (2026-09-05, standalone, a copy of `data/ih_beta.db`)

No production catalogue is reachable from the development sandbox (no network, no database
export in the repository), so the battery was rehearsed on the only catalogue available: the
beta-shaped local database — 121 articles from 10 real outlets (Reuters, AP, NPR, BBC, CNN, Fox
News, New York Post, The Guardian, Washington Times, The Hill), six days, RSS channel only,
**synthetic headlines** from the seed generator (every headline repeats across outlets as
"… - BBC dispatch 53"), no GDELT enrichment, no outlet index. The mechanics are fully real; the
content-quality numbers are not, and are read as such below.

**Backfill on a legacy schema:** 121 of 121 rows lacked `article_id`, `publisher_id` and
`licence_class`; the dry run counted them, the real run filled every one (1.2 s, one batch, 609
publishers catalogued from the registry), the second dry run reported zero missing.

**Result: 67 PASS, 4 WARN, 0 FAIL, 2 SKIP.**

| measurement | value | reading |
|---|---|---|
| stories served | 9 of 121 articles (108 covered) | every story ≥3 publishers |
| largest story | 54 articles, 10 publishers, 50% of covered articles, `clusterTrust: unverified` | WARN — the seed generator's repeated "dispatch N" headlines; a real window must not show this |
| articles per publisher (max, one story) | 5.4 | WARN — same artefact |
| summaries / tags / topic present | 100% / 100% / 100% | |
| event countries present | 0% | WARN — no enrichment in this copy; production runs `RWE_GDELT_ENTITIES` |
| coverage classes | `metadata_public` only | production carries provider channels too |
| coverage comparison | available, 3 evidence rows, viewpoint findings withheld | |
| history | 1 snapshot, 54 membership rows | |
| tag retrieval | `arts` → 4 stories, all carry the tag | |
| publishers | 609 catalogued, host `www.cnn.com` → CNN, per-publisher articles/stories consistent | |
| metering | 95 of 95 requests metered, 0 record errors | |
| exposure sweep | 26 payloads, 0 leaks in every class | after the fix below |
| latency (warm, in-process) | p50 9–15 ms on every endpoint | |
| latency (cold `/v1/stories`) | 2.2 s once (the first build of the window) | WARN — production warms the cache from the poller; a cold engine still pays it |

**What the sweep caught (fixed in the same change):** `GET /v1/stories/{id}/history` listed a
reader-private (extension-observed, uncorroborated) member in `membership` — url withheld, but its
`articleId`, publisher and join time present. Hidden members are now absent from the history
entirely, as they are from coverage, counts and evidence. The battery's fixture keeps one such row
so the regression stays covered (`tests/test_platform_validate.py`).

**What the search check found (fixed in the follow-up change):** `GET /v1/articles?q=` matched
the query as one substring. On the rehearsal catalogue `Ontario` and `Lake Ontario` found all 54
articles about the renaming story; `trump apple` and `ontario dispatch` found none, though every
one of those headlines carries both words. `/v1` now runs term search over an FTS5 index
(`feed_articles_fts`: headline, snippet, publisher, category; porter-stemmed; bm25 with the
headline weighted): on the same catalogue `trump apple` → 54, `ontario dispatch` → 54,
`budget package` → 18, `kusama retrospective` on `/v1/stories?q=` → the one story. The battery
now requires the top story's own words to be found in either order, the top result to carry the
query, and `/v1/stories?q=` to return the story the words came from. The consumer Search page is
unchanged (substring) unless `RWE_SEARCH_TERMS=1`.

## Improvements before an external developer relies on this

1. **Term search with ranking — done.** FTS5 behind `q` on `/v1/articles`, `/v1/stories` and the
   per-publisher listings; `sort=relevance` default with a query; `meta.query.terms` echoed.
   Remaining choice: flip the consumer Search page to the same engine (`RWE_SEARCH_TERMS=1`).
2. **Cold-build latency on `/v1/stories`.** The first request after a restart or cache expiry
   builds the window on the request thread (2.2 s at 121 articles; tens of seconds at production
   size). Serve the persisted last build (`story_snapshots` is already written) while the fresh
   one builds, and expose `meta.asOf` so a customer can tell.
3. **Enrichment coverage as a product guarantee.** `/v1/countries`, `/v1/entities` and the
   coverage comparison's geography findings are only as good as GDELT enrichment coverage
   (24% provider-covered at the last audit). Publish the coverage number in `/v1/health`, run the
   entity backfill before the first key, and decide whether the headline-span extractor's `span`
   kind should count as a default entity kind for the API.
4. **Cluster quality gates on the wire.** `clusterTrust` is served but nothing stops a low-trust
   or over-merged story from being the top result. Add `min_trust=ok` as a default filter on
   `/v1/stories` (opt-out), and cap articles-per-publisher in a served story's coverage.
5. **Developer experience.** Idempotent SDK-shaped responses need: stable sort options
   documented per endpoint, `If-None-Match` / `ETag` on story and article objects, a
   `Retry-After` on quota exhaustion, per-key request logs a customer can read (`/v1/usage`
   already has the rows), and a self-service key rotation command.

## Production run

```bash
cd /opt/ih && sudo deploy/ops/update.sh <sha>            # a release carrying the /v1 Caddy route
sudo deploy/ops/platform-enable.sh --dry-run             # preview
sudo deploy/ops/platform-enable.sh                       # enable, backfill, mint temp keys, validate, revoke
```

Read the report at `/opt/ih/data/platform_validation.json`. The bars a production run must clear
before a private external key is minted:

1. **0 FAIL** in the battery, and every exposure check PASS (not SKIP) — a developer-plan key in
   the run, which the script mints.
2. **Backfill complete**: `missingArticleId = missingPublisherId = missingLicence = 0`.
3. **Clustering shape** on the live window: largest-story share well under 40%, ≤3 articles per
   publisher per story, `clusterTrust: ok` on the top stories.
4. **Enrichment present**: event countries on ≥30% of stories, provider entities on the top
   story's articles (otherwise `/v1/countries`, `/v1/entities` are empty for customers).
5. **Latency**: warm p95 under 1.5 s on every endpoint; note the cold `/v1/stories` cost.
6. **Metering exact**: metered = sent, `recordErrors` 0.
7. **Public edge**: `https://hidden-view.com/v1/health` 200 and a keyless `/v1/articles`
   answering the platform's `unauthenticated` envelope; the consumer site unchanged.

Anything short of that is a finding to fix before the key, not a caveat to send with it.
