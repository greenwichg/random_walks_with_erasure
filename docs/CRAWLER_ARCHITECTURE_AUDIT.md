# Ingestion architecture — systemic weaknesses, and the per-feed scheduler

Audit of the path that actually runs in production, the comparison against published practice, and
the one architectural change taken from it. Same discipline as the clustering work: audit the real
code, register a candidate, measure it on the live catalog, adopt only on the numbers.

## What actually runs

The first finding is a naming trap worth stating before anything else.

> **`examples/crawler.py` is not in the production path.** Its own design doc opens with
> "read-only POC, not wired into ingestion", nothing imports it, no live crawl has ever been run,
> and its configured sitemap URLs and `article_pattern`s are, in its author's words, "unverified
> guesses". The sitemap/section discovery ladder, the robots gate and the per-host rate limiter
> all exist and none of them has ever ingested an article.

Production discovery is:

```
MultiSourcePoller (sources.py)
├── RSSAdapter          ← rss_ingest.ingest_all: sweeps ALL feeds every RWE_POLL_INTERVAL (600s)
├── KeyedJSONAdapter ×6   NewsAPI · Guardian · NewsData · GNews · MediaStack · Currents
├── GoogleNewsAdapter
├── GDELTAdapter
└── enrichers            GKG entities · publisher metadata
                          │
                          ▼  every path converges here
              rss_ingest.ingest_entries  →  ingest.canonical_url dedup  →  store
```

The chassis around it is genuinely good and is not what this audit is about: shared retry with a
total sleep budget, 429 handling that does not retry into a rate limit, per-adapter backoff on
sustained failure, per-source health, "one source's outage never affects another". The weaknesses
below are all in *scheduling and identity*, not in transport.

## The systemic weaknesses

### 1. No conditional GET — freshness is priced in bandwidth

`rss_ingest.fetch_feed` sends `User-Agent` and `Accept` and nothing else. No `If-None-Match`, no
`If-Modified-Since`; the response's `ETag` and `Last-Modified` are read by nothing. **Every poll
downloads every feed in full, whether or not it changed.**

The consequence is not the bandwidth, at nine feeds. It is that the cadence is set by what the
slowest-changing feed costs: halving the interval doubles the bytes, so freshness cannot be bought
without paying for it across the whole list. This is the single most-cited production practice for
feed consumers, and the support is nearly universal — roughly **89% of feeds carry `ETag` and 73%
`Last-Modified`** (Mozilla Observatory's published survey). A 304 costs a few hundred bytes and no
XML parse at all.

### 2. Uniform sweep — publish rate is ignored in both directions

`ingest_all` iterates the whole feed list every cycle. A wire filing 200 stories a day and a weekly
column are asked at exactly the same rate. One is under-served (news sits undiscovered for up to a
full interval) and the other is pestered for nothing.

### 3. Per-feed failure is recorded and then ignored by the scheduler

This one is a genuine wiring gap rather than a missing feature. `store.record_feed_health` has
tracked `consecutive_failures` **per feed** since it was written, and `MultiSourcePoller` even
copies it into `self._consecutive` keyed by feed URL. But `_effective_interval` looks up
`self._consecutive[adapter.health_key]` — the **adapter's** key, not the feed's. So RSS backs off
as a whole or not at all, and a permanently-404 feed is re-asked at full rate forever while its own
failure count sits in the database, read by nothing.

### 4. The dedup key is scheme-sensitive and variant-blind

`ingest.canonical_url` lowercases the host, strips `www.`, drops the query and the trailing slash —
and **keeps the scheme**. Verified:

| pair | same article? | same key? |
|---|---|---|
| `http://x.com/a` vs `https://x.com/a` | yes | **no** |
| `https://x.com/a` vs `https://x.com/a/amp` | yes | **no** |
| `https://x.com/a` vs `https://m.x.com/a` | yes | **no** |
| `https://x.com/a?utm_source=t` vs `https://x.com/a` | yes | yes ✓ |

With seven providers delivering overlapping coverage into one catalog, that is a real duplicate
surface. It is **not** fixed in this change, deliberately — see *Not done* below.

### 5. Discovery is a hand-maintained list

Nine feeds in `deploy/rss_feeds.example.txt`. No feed auto-discovery (`<link rel="alternate">` on a
publisher's homepage), no sitemap use in production, and section feeds are partial by nature —
a publisher's `/rss.xml` is usually one desk, not the newsroom. Coverage is bounded by whatever
someone last typed into a file.

## What production systems do, and how we compare

| Practice | Published evidence | Us, before | Us, after |
|---|---|---|---|
| Conditional GET (`ETag`/`Last-Modified`, 304) | universal feed-consumer advice; ~89%/73% feed support | absent | **implemented** |
| Adaptive per-feed cadence by observed publish rate | Feedly/reader-app practice; two USPTO filings on adaptive crawl rates by publication frequency | absent | **implemented** |
| Per-feed circuit breaking | standard | data present, scheduler blind to it | **implemented** |
| Push instead of poll (WebSub/PubSubHubbub) | W3C Recommendation 2018 | absent | **not taken** — see below |
| Sitemap/news-sitemap discovery | Google News ingestion norm | POC only, never run | unchanged |
| Dedup across URL variants | standard | scheme/AMP/mobile blind | **measured, not changed** |

**WebSub is deliberately not taken.** It is a real W3C Recommendation and it would beat polling on
latency, but adoption is concentrated in WordPress and IndieWeb rather than in major newsrooms, and
subscribing requires a publicly reachable callback endpoint with its own verification and renewal
lifecycle. Building that to serve a nine-feed list would be infrastructure in search of a
publisher. Worth revisiting if the feed list grows into WordPress-hosted outlets, which do carry
hubs by default.

## The change: a per-feed scheduler

One mechanism closes 1, 2 and 3, because they are the same missing thing — **per-feed scheduling
state** instead of "iterate the list every cycle":

* `examples/feed_schedule.py` — the policy, pure and I/O-free: `due()`, `validators()`,
  `advance()`. Bounded by a floor and a ceiling, the only two numbers an operator sets and the
  only two an outsider could verify from our request log.
* `rss_ingest.fetch_feed_conditional()` — sends what we hold, translates the **304 that `urllib`
  raises as an error** back into "unchanged". `fetch_feed` itself is untouched: its
  `fetch(url) -> bytes` signature is a contract the whole test suite injects fakes against.
* `feed_health` gains `etag`, `last_modified`, `content_sha`, `next_due_at`, `interval_s` through
  the existing additive `_ensure_*_columns` migration.
* `sources.RSSAdapter._ingest_scheduled` — skip / 304 / full, all three converging on the same
  `ingest_entries` choke point, so nothing downstream can tell which path ran.

**The adaptive law**, no per-site configuration and no publisher name anywhere: changed → interval
down multiplicatively toward the floor; unchanged → up toward the ceiling; failed → back off from
the current interval by the persisted consecutive-failure count. Multiplicative on both sides
because publish rates differ by orders of magnitude, and asymmetric (0.5 down, 1.5 up) because news
arrives in bursts, so reacting fast and drifting back slowly serves freshness better.

Three deliberate safety choices, each pinned by test:

* **Unknown state is always due.** A feed the scheduler has never met, or whose stored timestamp is
  unparseable, is polled. The other default lets a feed silently stop being collected, which looks
  exactly like a publisher going quiet and would be found by nobody.
* **A 304 is a success.** The classic conditional-GET bug is to let `urllib`'s raised `HTTPError`
  mark the feed unhealthy — backing off the one feed behaving perfectly.
* **Failure never discards validators.** A transient 500 must not throw away the ETag that makes
  the next successful poll cheap.

Off by default (`RWE_FEED_SCHEDULER=0`): the sweep runs exactly as before, and no scheduling state
is read or written — asserted by a test that makes `feed_schedule_state` raise.

## Measuring it

```bash
cd /opt/ih && source deploy/ops/_compose.sh
dc run --rm -T api python examples/audit_crawler_health.py --db "$RWE_DB_URL"
```

Read-only, and it answers from stored rows rather than by asking any publisher. Two sections: what
the law would settle each feed at (and which feeds are being asked far more often than they earn,
and which are failing every cycle with nothing backing them off), and the size of the dedup blind
spot from §4.

**Read the FULL BODIES line, not the request count.** The request count may legitimately *rise* —
the law polls a busy feed harder than a 600 s sweep does. What falls is bytes, because an unchanged
feed answers 304 with no body, and that is precisely what buys the faster cadence on the feeds that
deserve it.

## Not done, and why

* **The dedup key is measured, not changed.** Widening `canonical_url` is a *migration*, not an
  edit: story ids, the feedback ledger (which keys on canonical URL), the score cache and
  `url_by_id` all resolve through it, and every existing `http://` row would stop matching its own
  key. The audit sizes the problem first. If the number is material the fix is designed against it;
  if it is ~0 the migration is not worth planning.
* **No article-body fetching.** Unchanged from `CRAWLER_DESIGN.md`: licensing, volume, and we do
  not need it.
* **No site-specific rules anywhere.** The AMP/mobile detection in the audit matches URL
  *structure* (`/amp` segment, `m.` host), never a publisher name.
