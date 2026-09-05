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

The engine stays private. To reach `/v1` from the internet add a second Caddy site that proxies
**only** that prefix to the engine (the consumer app keeps `web:3000`):

```caddyfile
api.hidden-view.com {
	@v1 path /v1/*
	handle @v1 {
		reverse_proxy api:8000
	}
	respond 404
}
```

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

```
Authorization: Bearer hv_live_…
GET /v1/articles?q=&publisher_id=&topic=&country=&from=&to=&sort=newest&limit=30&cursor=
GET /v1/articles/{article_id}          GET /v1/articles/by-url?url=
GET /v1/stories?topic=&publisher_id=&country=&tag=&type=&from=&to=&sort=top&limit=&cursor=
GET /v1/stories/{story_id}             /similar   /intelligence   /history  (stories:history)
GET /v1/publishers?name=               GET /v1/publishers/{publisher_id}
GET /v1/usage?month=YYYY-MM            GET /v1/health
```

Every response is `{"data": …, "meta": {...}}`. `meta` carries `requestId`, `asOf`, `versions`
(`scorer`, `build`, `buildConfig`, `registry`, `publisherIdScheme`), `ratingsPublished`, and
`page` (`limit`, `cursor`, `nextCursor`, `total`) on lists. Headers: `X-RateLimit-Limit`,
`X-Usage-Month`, `X-Usage-Limit`, `Retry-After` on 429. Errors are the engine's envelope
`{"error": {"code", "message", "requestId"}}` with stable codes: `unauthenticated`,
`key_revoked`, `key_expired`, `tenant_suspended`, `forbidden_scope`, `ratings_not_published`,
`rate_limited`, `quota_exceeded`, `invalid_cursor`, `not_found`, `platform_disabled`.

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
breaking-story edge in `notification_events` is the event source). Each attaches to the tables
that now exist without changing them.
