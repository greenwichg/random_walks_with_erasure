# Platform API (`/v1`) — the commercial front door

The reusable access layer over the same engine that serves hidden-view.com. It owns *who* may
read (keys, tenants, scopes), *how much* (plans, per-key rate, per-tenant monthly quota, the
meter), and the *shape* of what leaves (durable ids, licence-class withholding, versions). It owns
no intelligence: every answer comes from the service the consumer route calls
(`search.search`, `story_service.list_stories` / `get_story` / `similar_stories`,
`story_intelligence`, `publisher_service.get_publisher`) or from the persisted story history.

Design and the decisions behind it: `docs/NEWS_INTELLIGENCE_INFRASTRUCTURE.md` (§D, §F, §I and the
implementation note at the end). Code: `examples/platform_api/`.

## Turning it on

```bash
# deploy/.env
RWE_PLATFORM_API=1                 # mounts /v1 into the engine (default 0: no /v1 route exists)
# RWE_PLATFORM_PUBLISH_RATINGS=1   # ONLY with a redistribution licence for AllSides / MBFC ratings
# RWE_PLATFORM_PUBLISH_WIKIPEDIA=1 # ONLY with CC BY-SA attribution in place
deploy/ops/restart.sh api
```

The engine stays private. `deploy/Caddyfile` routes **only** `https://hidden-view.com/v1/*` straight
to the engine (`api:8000`); every other path stays on the web tier. The route is inert until the
flag is on — without it the engine answers `/v1/*` with its ordinary 404 — so the same Caddyfile
serves both states and no DNS or certificate work is needed.

**One command does the whole first enablement on the production host** and validates it:

```bash
cd /opt/ih && sudo deploy/ops/platform-enable.sh            # --dry-run to preview, --validate to re-run the checks
```

It sets the flag, restarts the engine, reloads Caddy, takes a backup, runs the identity backfill
(dry run, then real, then verifies no row is missing an id), mints two **temporary** keys (internal
and developer plan — never printed, revoked at the end), runs `examples/platform_validate.py` inside
the api container against the live catalogue, and probes the public edge. The report lands at
`/opt/ih/data/platform_validation.json`. It is idempotent: re-running it re-validates.

`examples/platform_validate.py` is also a customer-side smoke test: `--base-url https://hidden-view.com`
with the key in `RWE_PLATFORM_KEY` (an `internal`-plan key; add `RWE_PLATFORM_KEY_DEV` for the
withholding checks) runs every capability, the exposure sweep, the quality measurements and a
latency table. `--db sqlite:////path/ih_beta.db --backfill` rehearses the same battery on a copy of
a database, standalone.

`platform_api.app.create_app(store)` builds the same surface as a standalone FastAPI app — the
shape a separate `platform` process would run — if isolation is ever worth a process.

## Tenants, keys, plans

```bash
cd /opt/ih
sudo docker exec -i deploy-api-1 python examples/platform_keys.py tenant create acme --name "Acme Corp"
sudo docker exec -i deploy-api-1 python examples/platform_keys.py mint --tenant acme --plan developer --label "acme ci"
#   -> prints the key ONCE (hv_live_…); the database stores only its SHA-256
sudo docker exec -i deploy-api-1 python examples/platform_keys.py list --tenant acme
sudo docker exec -i deploy-api-1 python examples/platform_keys.py usage acme --month 2026-09
sudo docker exec -i deploy-api-1 python examples/platform_keys.py revoke key_…
```

| plan | scopes | licence classes | rate / min | quota / month |
|---|---|---|---|---|
| `developer` | articles, stories, publishers, usage | `metadata_public` | 60 | 10,000 |
| `enterprise` | all (adds `stories:history`) | `metadata_public` | 300 | 250,000 |
| `internal` | all | `metadata_public`, `provider_restricted`, `unknown` | 600 | unlimited |

Any of the four can be overridden per key (`--scopes`, `--classes`, `--rate`, `--quota`,
`--expires`). Plans are data in `platform_api/plans.py`. Quota counts every key a tenant holds; a
refused request (4xx) is counted as a request, never as a unit. The engine's own per-IP limit
(`RWE_RATELIMIT_READ_PER_MIN`, 240/min in production) still applies underneath: raise it before
selling a plan above that rate to callers behind one address.

## Requests

The key travels as `Authorization: Bearer hv_live_…` or `X-API-Key: hv_live_…` (never in the
query string). Interactive reference: `GET /v1/docs` (Swagger UI) over `GET /v1/openapi.json`,
both public.

| endpoint | scope | what it answers |
|---|---|---|
| `GET /v1/health` | — | liveness, the versions in force, enrichment coverage (entities / spans / event geography over the catalogue and the last 7 days; counted in the background, `null` until the first count after a start), `lastBuildAt`, search-index status |
| `GET /v1/me` | any key | the key's tenant, plan, scopes, licence classes, limits, month-to-date, the key's own label / prefix / expiry |
| `GET /v1/articles?q=&publisher_id=&publisher=&topic=&country=&from=&to=&sort=&limit=&cursor=` | `articles:read` | catalogue term search + filters; with `q` the default `sort` is `relevance` (else `newest`; `oldest`, `publisher`); provisional rows excluded in SQL |
| `GET /v1/articles/{article_id}` · `GET /v1/articles/by-url?url=` | `articles:read` | one article, its current `storyId`, its provenance channels |
| `GET /v1/articles/{article_id}/entities?kind=` | `articles:read` | provider-extracted `person` / `org` names (GDELT attribution), `kind=span` for our headline spans |
| `GET /v1/entities?name=&kind=&limit=&cursor=` | `articles:read` | the articles an entity was extracted on, newest first |
| `GET /v1/countries` | `articles:read` | per-country article + publisher counts over EVENT geography |
| `GET /v1/stories?q=&topic=&publisher_id=&country=&tag=&type=&lean=&blindspot=&from=&to=&min_trust=ok&sort=top&limit=&cursor=` | `stories:read` | the served story build, paged; `q` keeps the events whose member articles match the words, best-matched first under `sort=top`; `min_trust` = `ok` (default) / `unverified` / `any` (`lean`/`blindspot` only where ratings are published) |
| `GET /v1/stories/{story_id}?coverage_per_publisher=` | `stories:read` | one story with its coverage, at most N rows per outlet (deployment default 3; `0` = all), `coverageOmitted` counting the rest; `ETag` |
| `GET /v1/stories/{story_id}/similar?limit=` | `stories:read` | stories about the same / a related event |
| `GET /v1/stories/{story_id}/intelligence` | `stories:read` | freshness, momentum, lifecycle, alerts |
| `GET /v1/stories/{story_id}/coverage-comparison?article_id=` (or `url=`) | `stories:read` | one member against the rest of its coverage: other outlets, event geography, register mix, timing, uniqueness (counted facts, never text) |
| `GET /v1/stories/{story_id}/history?limit=` | `stories:history` | persisted snapshots + membership joins/leaves |
| `GET /v1/tags?q=&min_stories=&limit=&cursor=` | `stories:read` | the tag vocabulary of the live window with story counts |
| `GET /v1/tags/{tag}?limit=&cursor=` | `stories:read` | every story recorded under the tag, strongest association first |
| `GET /v1/publishers?name=` · `?q=&country=&scope=&kind=&registered=&limit=&cursor=` | `publishers:read` | resolve one by any name/host form, or list busiest-first under filters |
| `GET /v1/publishers/by-host?host=` | `publishers:read` | a hostname or URL → its publisher |
| `GET /v1/publishers/{publisher_id}` | `publishers:read` | curated facts + hosts + counted profile |
| `GET /v1/publishers/{publisher_id}/articles?q=&topic=&from=&to=&sort=&limit=&cursor=` | `articles:read` | the publisher's articles (same `q` / `sort` semantics as `/v1/articles`) |
| `GET /v1/publishers/{publisher_id}/stories?q=&topic=&country=&from=&to=&sort=&limit=&cursor=` | `stories:read` | stories with coverage from the publisher |
| `GET /v1/outlets/search?q=&count=` | `publishers:read` | the outlet index (Wikidata, Wikipedia, Common Crawl, observed feeds): outlets by place, language or name — internal index only, no paid upstream |
| `GET /v1/usage?month=YYYY-MM` | `usage:read` | the tenant's meter, per day / key / endpoint |
| `GET /v1/usage/requests?key_id=&from=&to=&status=&limit=&cursor=` | `usage:read` | the tenant's per-request log, newest first (time, key, endpoint, units, status, request id, latency; never query text) |

Every response is `{"data": …, "meta": {...}}`. `meta` carries `requestId`, `asOf`, `versions`
(`scorer`, `build`, `buildConfig`, `registry`, `publisherIdScheme`), `ratingsPublished`, and
`page` (`limit`, `cursor`, `nextCursor`, `total`) on lists. Headers: `X-RateLimit-Limit`,
`X-Usage-Month`, `X-Usage-Limit`, `Retry-After` on 429. Errors are the engine's envelope
`{"error": {"code", "message", "requestId"}}` with stable codes: `unauthenticated`,
`key_revoked`, `key_expired`, `tenant_suspended`, `forbidden_scope`, `ratings_not_published`,
`rate_limited`, `quota_exceeded`, `invalid_cursor`, `invalid_request`, `not_found`,
`search_unavailable`, `platform_disabled`. Parameter validation failures are FastAPI's `422`.

A keyed `/v1` request is exempt from the engine's per-IP limiter (the platform meters per key);
a keyless one is throttled at the engine's `auth` rate (30/min in production) — a request with no
key can only be guessing. `/v1/openapi.json` and `/v1/docs` are public and never metered.

### Freshness, trust, caching, keys

**`meta.asOf` and `meta.stale`.** Every story answer names the build it came from. A servable
cached build answers with `stale: false`. When the cache is cold (a restart, an expired window)
the engine answers from the durable record of the last build — `stories`, `story_snapshots`,
`story_membership`, joined back to the catalogue rows — with `stale: true` and that build's
`asOf`, and queues one background build; the next request finds the cache warm. No `/v1` story
request clusters on the request thread. Before the first build was ever recorded (a first boot)
the answer is an honest empty, stale page with `asOf: null`. `/similar` still needs the live
build and pays for it when cold.

**Trust as a filter.** `min_trust=ok` (default on `/v1/stories`, `/v1/tags/{tag}` and
`/v1/publishers/{id}/stories`) serves only stories the independent geography signal corroborates
or that are too small to chain. `unverified` also keeps the big unchecked clusters; `any` keeps
`low` (the located members disagree about where the event happened). `clusterTrust` is on every
story either way.

**Coverage per publisher.** A story lists at most `RWE_PLATFORM_COVERAGE_PER_PUBLISHER` (3)
coverage rows per outlet, newest first; `coverageOmitted` and `coveragePerPublisher` say what was
left out, so `len(coverage) + coverageOmitted == totalCoverage`. `?coverage_per_publisher=0`
lists everything.

**ETags.** `/v1/stories/{id}`, `/v1/articles/{id}`, `/v1/articles/by-url` and
`/v1/publishers/{id}` carry a weak `ETag` over the object and `Cache-Control: private,
must-revalidate`; `If-None-Match` answers `304` with no body — recorded as a request, never as a
unit.

**Retry-After.** `rate_limited` says when the per-minute bucket refills; `quota_exceeded` says
how many seconds remain in the UTC calendar month.

**Key rotation.** `platform_keys.py rotate key_… [--grace-hours 24]` mints a successor with the
same tenant, plan, scopes, classes, limits and label, prints it once, and gives the old key the
grace period (`0` revokes it now; an earlier expiry is never extended). `/v1/me` shows the key's
own `expiresAt` so a client can see the countdown.

**Entities.** `person` / `org` are provider-extracted (GDELT GKG) and are the default kinds on
`/v1/entities` and `/v1/articles/{id}/entities`; `span` — the capitalised headline spans our own
extractor finds — stays opt-in (`kind=span`): untyped and extracted by us, it is a different
promise. `/v1/health` reports both coverages separately.

### How `q` matches

`q` is words: every word must occur, in any order, in the headline, the snippet, the publisher
name or the category; words are porter-stemmed (`resign` finds `resigns` and `resigned`) and
diacritic-folded; a trailing `*` keeps a prefix (`econ*`); operators and punctuation are plain
words. `sort=relevance` (the default with `q`) ranks by bm25 with the headline weighted above the
snippet. `meta.query.terms` echoes the words that were matched. On `/v1/stories`, an event
matches when one of its member articles matches, and under `sort=top` events with more matching
members lead. The index is SQLite FTS5 (`feed_articles_fts`), maintained by triggers on every
catalogue write and checked / rebuilt with `examples/search_index.py`. The consumer Search page
keeps its substring match unless the deployment sets `RWE_SEARCH_TERMS=1`.

### What the intelligence endpoints reuse

Nothing on `/v1` computes intelligence of its own. `articles` → `search.search`; `stories`,
`tags/{tag}`, `publishers/{id}/stories` → `story_service.list_stories` / `get_story`;
`similar` → `story_service.similar_stories`; `intelligence` → `story_intelligence`;
`coverage-comparison` → `coverage_comparison.compare` with exactly the analyzer's inputs (the
member's own facts, the article's provider-extracted event countries); `publishers/{id}` →
`publisher_service.get_publisher`; `outlets/search` → `outlet_search` (the index the discovery
pipeline builds and the SerpAPI-compatible facade reads — without the facade's paid top-up);
`entities`, `countries`, `tags` → the projections the ingest and build paths already write.

Counts on a story (`totalCoverage`, `publisherCount`, `publishers`, `attachedCoverage`) are over
the members the platform can serve: a reader-private or provisional member is not a smaller row,
it is no row. The coverage comparison runs over the same set; its evidence links follow each
member's licence class; its viewpoint findings and `missingViewpoints` derive from the
AllSides-based lean and leave only where ratings are published (`withheld` names them otherwise).

## Identity

| object | id | source |
|---|---|---|
| article | `ar_` + 20 hex, minted once on first sight; every URL form ever observed resolves to it | `feed_articles.article_id`, `article_aliases` |
| publisher | `pub_` + 20 hex, a pure function of the outlet's identity key (registry canonical › brand domain › folded name) | `publishers`, `publisher_hosts` |
| story | `st_` + 16 hex, the id the ledger serves, now with a lifecycle row | `stories`, `story_builds`, `story_snapshots`, `story_membership` |

`python examples/identity_backfill.py --db "$RWE_DB_URL" --dry-run` reports what an existing
catalogue is missing; without `--dry-run` it fills ids, aliases, one provenance row per row and
the publishers table, batched and idempotent. A legacy row a feed still lists also heals on its
next re-poll.

## Licence classes — what a plan receives

Every catalogue row carries the most permissive class among the channels it was observed through
(`article_provenance` is the evidence; `licence.py` the rule):

| class | held via | on `/v1` for a plan without the class |
|---|---|---|
| `metadata_public` | the publisher's own RSS / sitemap crawl; GDELT (attribution in `licence.attribution`) | full |
| `provider_restricted` | NewsAPI, Guardian, NewsData, GNews, MediaStack, Currents, Google News RSS | identity, publisher, time, topic, story membership; `headline` / `description` / `url` / image **withheld** and listed under `withheld` |
| `unknown` | no channel recorded (legacy rows) | as restricted, until re-observed |
| `reader_private` | a reader's browser extension, uncorroborated | never served, on any plan |

A story's title and summary are a member's words, so they follow that member's class: when the
representative is outside the plan, the title comes from the earliest member inside it and the
summary is withheld. Third-party ratings (the AllSides-derived lean and everything derived from
it — story distribution, blindspot side, low-credibility list — and MBFC verdicts) and Wikipedia
text are deployment switches, not plan properties. Bodies are never on the wire; snippets are
clamped to 300 characters.

## History and the archive

Every served unfiltered build is recorded (`RWE_STORY_HISTORY`, default on): one `story_builds`
row with the algorithm version + config hash + registry snapshot, a `story_snapshots` row per story
**that changed**, and `story_membership` joins/leaves. `GET /v1/stories/{id}/history` reads it.
The hot window is `RWE_RETENTION_STORY_HISTORY_DAYS` (30); with `RWE_ARCHIVE_ON_PRUNE=1` retention
writes what it is about to delete — catalogue rows, story history — to `RWE_ARCHIVE_DIR` first as
gzipped JSONL partitions with manifests (rows, bytes, sha256, versions) and **keeps the rows if the
write fails**. `backup-offhost.sh` ships the directory to `s3://<bucket>/archive/`, outside the
backup lifecycle prefix. `examples/archive_export.py --stats | --verify | --publishers` reads it
back and snapshots the publisher table on demand.

## What the platform does not do yet

No billing (the meter is the invoice's input), no SSO, no data tenancy (every tenant sees the
same world), no bulk exports through the API (the archive is the substrate), no webhooks (the
breaking-story edge in `notification_events` is the event source), no self-service key
management (`platform_keys.py` is the operator's tool). Each attaches to the tables that now
exist without changing them.

## Verifying a deployment

```bash
curl -s https://api.hidden-view.com/v1/health | jq .meta.versions
curl -s -H "Authorization: Bearer $KEY" https://api.hidden-view.com/v1/me | jq .data
curl -s -H "X-API-Key: $KEY" "https://api.hidden-view.com/v1/stories?limit=1" | jq '.meta.page, .data[0].storyId'
curl -s -H "X-API-Key: $KEY" "https://api.hidden-view.com/v1/outlets/search?q=local+news+websites+in+Kenya" | jq .meta.query
```

Read `$KEY` from the operator's secret store; never paste a key into a shell history or a chat.
