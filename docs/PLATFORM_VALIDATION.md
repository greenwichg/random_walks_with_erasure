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
2. **Cold-build latency on `/v1/stories` — done.** A cold cache answers from the durable record
   of the last build (`stale: true`, that build's `asOf`) and queues one background build; no
   story request clusters on the request thread. The battery reports `meta.asOf` / `stale`.
3. **Enrichment as a guarantee — done.** `/v1/health` publishes entity, span and event-geography
   coverage (catalogue and last 7 days); `platform-enable.sh` runs the GDELT entity backfill and
   the span backfill before validating and prints the coverage; spans stay opt-in (`kind=span`).
   The battery WARNs below 20% recent coverage — the production number decides.
4. **Cluster quality on the wire — done.** `min_trust=ok` is the default on every story
   listing; coverage lists at most 3 rows per outlet with `coverageOmitted` counting the rest.
5. **Developer experience — done.** Weak `ETag` + `If-None-Match` → 304 (a request, no unit) on
   story, article and publisher objects; `Retry-After` on `quota_exceeded` (seconds to the month's
   end); `GET /v1/usage/requests` per-key request log; `platform_keys.py rotate` with a grace
   period; `/v1/me` shows the key's expiry.

Remaining before an external key: the production battery itself (the bars above), and the
ratings / provider-ToS decisions that gate `RWE_PLATFORM_PUBLISH_RATINGS`.

## First production run (2026-09-05, release `dd94e0e`)

The battery ran against the live catalogue: **150,062 articles, 2,897 stories, 3,199 publishers,
68,457 articles published in the last seven days, 2,305 tags, 205 event countries.** Result:
76 PASS, 4 WARN, 3 FAIL, 2 SKIP — and four findings, every one of them in the enablement path
rather than the API's answers.

What the catalogue itself showed, all bars met: the default listing holds only `clusterTrust: ok`
stories, the largest story holds 6% of covered articles, at most 2.9 articles per publisher per
story, every first-page story has ≥12 publishers, summaries / tags / event countries on 100%;
`wielding stabbing` and `stabbing wielding` both find the same 8 articles with the Times Square
story on top and `/v1/stories?q=` lands on it; the developer key saw 62 provider-restricted rows
and received none of their delivery; 94 of 94 requests metered, a 304 logged with no unit; warm
latency p50 of 59 ms on `/v1/stories`, 36 ms on search, 19 ms on a story, 9 ms on `/v1/me`.

The findings (fixed in the follow-up release; `tests/test_platform_production_findings.py`):

| finding | cause | fix |
|---|---|---|
| `/v1/health` timed out the enable script's 10 s probe on the cold engine; 3.5 s warm | enrichment coverage counted on the request path: eight `IN (subquery)` scans of 150k rows | counted off the request path by one daemon thread with a 5-minute TTL (`null` until the first count lands); counts driven from the indexed side tables |
| identity backfill stopped at ~37k of 150k rows; the script said "re-run" and showed no error | one transaction per row beside the live poller; a lock error killed the process; the script sent stderr to `/dev/null` | a transaction per batch of 1,000, retried on lock contention, a failing batch reported and skipped, passes until nothing is missing, exit 1 if rows remain, one summary line; the script shows stderr |
| `stories` / `story_snapshots` / `story_membership` empty while `story_builds` advanced; every `/history` a 404 | the first recorded build (2,897 stories, ~100k joins) was one transaction and failed whole, so every later build saw the same deltas and failed the same way | chunked transactions (500 rows), a bulk UPDATE for the touched stamp instead of 2,897 round trips, the build's counters written last as the completion marker |
| `https://hidden-view.com/v1/*` still reached the web tier (404 / HTML) | `caddy reload` was not verified and `docker compose up -d` does not recreate a container whose bind-mounted file changed | the script routes a request through Caddy and expects the engine's 401; on a miss it restarts Caddy and probes again |

A fifth thing the run exposed, fixed alongside: with a build row present but no story rows, the
platform served an **empty page marked stale** on a cold cache. It now builds instead; the durable
record is served only when it holds stories.

Two numbers to act on, not bugs: provider entities reach 13.4% and event geography 11.9% of the
last seven days' articles (the battery's bar is 20%). The steady-state enricher fills forward;
the backfill reaches back 48 hours. Whether that is enough is the product's call before a key
that sells `/v1/entities` or `/v1/countries`.

The re-run on the follow-up release continues where this one stopped: the backfill is
resumable (111,645 rows remained), the entity backfill can be skipped with
`PLATFORM_GKG_HOURS=0` (its rows stand), and the first served build after the deploy records the
story history in full.

## Second production run (2026-09-05, release `4e27045`)

**86 PASS, 4 WARN, 1 FAIL, 0 SKIP.** Three of the four findings closed on the live catalogue:
every article and coverage row carries its ids and licence (the backfill finished), `/v1/health`
answered in 463 ms (from 3,555), and `https://hidden-view.com/v1/*` reached the engine (200 on
health, the unauthenticated envelope without a key, the consumer site untouched). The catalogue
bars held: 2,885 trusted stories, largest 6%, ≤2.9 articles per publisher, ≥12 publishers per
first-page story, 110 of 110 requests metered, 53 restricted rows seen by the developer key and
none delivered.

The one FAIL was the same one: `history answers` — a 404, `story_builds` advancing, `stories`
empty. Chunking the write had not been the cause; the write failed on its FIRST insert. Two
served stories carried the same id. The id ledger gives a prior id to the one story holding most
of its coverage (`reassign_ids`), but a story that claims nothing keeps its DERIVED id — and a
split leaves the smaller piece holding the anchor article the bigger piece's id was derived
from. Both pieces are served under one id: a dead link for one of them on the consumer site, and
a duplicate primary key on `stories` for the recorder, on every build, forever.

| finding | cause | fix |
|---|---|---|
| history 404 on every story; `story_builds` advancing | two served stories under one id (a split's anchor piece re-deriving the ledger id given to the bigger piece); the first `stories` insert failed on the key | `story_service.unique_ids` after reassignment: the claiming story keeps the id, the other is re-anchored on its id plus a counter, deterministically, and the ledger records it; `record_build` drops a duplicate rather than failing the build |
| the recorder failed soft, and silently | the reason lived in one `story_history failed:` warning line | `/v1/health` carries `history`: row counts, the last completed build's counters, the last error since start; the battery reads it and names the error |
| the battery could not see the duplicate | it sampled one page of 50 | it walks every page of the listing once (`stories.walk`) and requires every served id unique; the walk also proves the cursor reaches the announced total |

Still WARN, still a product call: provider entities 13.2% and event geography 11.7% of the last
seven days. New WARN to act on before an external key: the publisher profile of a 6,471-article
outlet answered in 3.0 s (`/v1/publishers/{id}` computes topics and hosts over every row of the
publisher on the request path; a cached or bounded profile is the next improvement).

## Third production run (2026-09-05, release `e294fe3`) — validated

**93 PASS, 3 WARN, 0 FAIL, 0 SKIP; `platform: ENABLED and validated`.** The recorder wrote the
first build after the restart in full — 2,897 story rows, 2,900 snapshots, 11,258 membership
rows, no error, 480 ms — and the battery's walk found 2,892 served ids over 29 pages with none
duplicated and the cursor reaching the announced total. The top story's history answered with
1 snapshot and 43 membership rows. 139 of 139 requests metered; 53 provider-restricted rows seen
by the developer key, none delivered; 69 payloads swept with no body, no reader-private row, no
provisional row, no rating. The public edge held: 200 on health, the unauthenticated envelope
without a key, the consumer site untouched.

The three WARNs, none of them an exposure or a wrong answer:

| WARN | measured | reading |
|---|---|---|
| provider entities ≥20% of recent articles | 13.3% | the product's call on how much of `/v1/entities` a key is sold; the enricher fills forward |
| event geography ≥20% of recent articles | 11.7% | same call for `/v1/countries` |
| p95 under 1.5 s on every endpoint | health 6.5 s (one sample, taken while the first build after the restart was running), publisher profile 5.7 s (a 6,460-article outlet), outlet search 2.1 s | the publisher profile aggregated topics and hosts over every row of the publisher on the request path — three whole-catalogue scans per request. Fixed in the follow-up release: the counted core is cached per publisher (`RWE_PUBLISHER_PROFILE_TTL`, 600 s; `meta.asOf` says when it was counted), the catalogue's topic counts are shared, the platform skips the recent-articles query, and an expression index on `lower(publisher)` serves the filter every surface writes |

**Readiness.** Phase 1 is ready for a first private external user on the developer plan
(`metadata_public` only, ratings and Wikipedia unpublished, `stories:history` off the plan). What
that user gets is exactly what the battery verified: ids and licence on every row, term search,
the trusted default listing with capped coverage, ETags, a metered key with `Retry-After`. The
two things to say to them up front: the publisher profile can take seconds on the largest
outlets until it is cached, and entity / geography coverage is a minority of recent articles.

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
2. **Backfill complete**: `missingArticleId = missingPublisherId = missingLicence = 0`; search
   index `indexed == catalogue`.
3. **Clustering shape** on the live window: the default listing (`min_trust=ok`) holds only
   `clusterTrust: ok`; largest-story share well under 40%; no publisher above the coverage cap.
4. **Enrichment present**: `/v1/health` recent `entityCoverage` and `geoCoverage` ≥ 0.2 (the
   battery WARNs below), provider entities on the top story's articles, event countries on ≥30%
   of stories.
5. **Freshness**: `meta.stale: false` on the listing in steady state, `lastBuildAt` recent; warm
   p95 under 1.5 s on every endpoint.
6. **Metering exact**: metered = sent, `recordErrors` 0, a 304 logged with `units: 0`.
7. **Public edge**: `https://hidden-view.com/v1/health` 200 and a keyless `/v1/articles`
   answering the platform's `unauthenticated` envelope; the consumer site unchanged.

Anything short of that is a finding to fix before the key, not a caveat to send with it.
