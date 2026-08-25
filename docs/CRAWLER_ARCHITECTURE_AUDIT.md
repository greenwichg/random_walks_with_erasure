# Ingestion architecture — systemic weaknesses, and the per-feed scheduler

Audit of the path that actually runs in production, the comparison against published practice, and
the one architectural change taken from it. Same discipline as the clustering work: audit the real
code, register a candidate, measure it on the live catalog, adopt only on the numbers.

> **Companion:** `SOURCE_COVERAGE_AUDIT.md` covers the other half — *which* publishers we carry and
> what they do once inside. It records three measured rejections: source expansion and curation (the
> untracked backlog is worth 13 blindspot claims across 1,528 stories), excluding research/forum
> outlets from clustering (removal costs 24 news articles their coverage to fix 1 false merge), and
> a URL fallback in outlet resolution (+1 blindspot claim for 473 articles losing their story).
>
> **Forward reference:** `SCALE_ROADMAP.md` takes the scheduler's equilibrium law from this document
> and derives the crawl budget for a 50,000-source universe — `2.71 × items/day`, i.e. **crawl cost
> is proportional to content, not to source count**. It also identifies the two places this design
> stops scaling: the 6-hour interval ceiling binds for any feed under 1.48 items/day (which is most
> of a 50k universe), and `poll_adapter_once`'s global lock serializes the whole fleet.

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


---

# Measured on production, 2026-08-25

19 feeds/adapters tracked, 27,873 articles in the scan window.

## The per-feed breaker — and a claim retracted

The first production read reported this:

```
  polls    ok  fail   feed
    332   193   131   MediaStack
```

and this document said it "validated the breaker, decisively". **That was wrong, in both halves,
and the error is worth keeping rather than deleting.**

`feed_health` rows are keyed two different ways: by feed URL for RSS (`https://…/rss.xml`) and by
a synthetic adapter key for everything else (`mediastack://news`, `gdelt://doc`,
`newsapi://top-headlines`). MediaStack is a `KeyedJSONAdapter`, not an RSS feed. So:

* **the new breaker does not cover it.** The scheduler lives in `RSSAdapter._ingest_scheduled`;
  adapters have their own `poll_once` and never reach it;
* **it was not "re-asked every sweep" either.** `MultiSourcePoller._effective_interval` already
  backs an adapter off on sustained failure — at 132 consecutive failures that is 4× its own
  interval, capped at `RWE_SOURCE_MAX_INTERVAL`. The rule that would fix it had already fixed it.

The audit printed "re-asked every sweep" because that phrase was an *assumption baked into the
instrument*, not an observation. `is_rss_feed()` now splits the two populations: RSS rows are
scoped to this change, adapter rows are listed for visibility with the existing rule named. The
request/body totals count RSS rows only, since the scheduler cannot move an adapter's cadence.

What remains true: **the breaker is the right mechanism for RSS feeds**, and the 19 tracked rows
are ~10 RSS feeds plus ~9 adapters, so the scheduler's real scope is half what the first read
implied.

## The dedup blind spot: REJECTED

```
articles in window : 27,873
redundant rows     : 13 (0.05% of the window) across 13 groups
  by cause         : scheme 13   amp 0   mobile 0   other 0
```

Thirteen rows, **all of them scheme-only** (`http://` vs `https://` of one article — Deadline ×4,
9to5Google ×2, El Punt Avui ×2, Euronews, Hollywood Reporter). Zero AMP, zero mobile.

0.05% does not pay for a migration that would touch story ids, the feedback ledger, the score cache
and `url_by_id`, and under which every existing `http://` row stops matching its own key. **The
candidate is closed** — measured, rejected, recorded, exactly like the clustering knobs that failed
their bars. If the number ever moves materially the audit is here to say so.

## Two instrument corrections (the reason to keep reading your own tools)

Both were found by running the audit rather than by reasoning about it, and both would have
produced a wrong recommendation.

1. **`feed_health.imported` is last-cycle, not cumulative.** `record_feed_health` *assigns* it
   (`row.imported = ...`) while `total_polls` accumulates. The first version of this audit keyed
   each feed's settled interval on `imported > 0`, so CNN read as quiet and NPR as busy purely from
   what happened in one 10-minute window. The publish rate now comes from counting each
   publisher's articles across the scan window — a signal that is actually a rate.

2. **The audit's model was not the law.** It used `settled = 86400/N`, the interval at which every
   poll finds exactly one article. That is not where `feed_schedule.advance` settles. The law
   multiplies by `speedup` on change and `slowdown` otherwise, so it stops where the expected
   log-step is zero:

   ```
   p* = ln(slowdown) / (ln(slowdown) − ln(speedup))    = 0.369 at the shipped 0.5/1.5
   T* = p* · 86400 / N
   ```

   The old model was **2.7× too slow**, which made a 20-article/day feed read as "poll every 1.2 h"
   — a freshness regression the change does not cause; the true settle is ~27 min. `T*` is now
   derived from the same env knobs the law reads, and a parametrised test simulates `advance`
   against steady publish rates and asserts the two agree, so tuning one without the other fails
   in CI rather than in a recommendation.

## Still unmeasured

The cadence half. The corrected instrument gives a defensible estimate, but the request/body
figures it prints are a model of the law's steady state, not an observation of it. The only honest
way to get that is to run the scheduler on and read the `notModified` counter — which is why
`RWE_FEED_SCHEDULER` stays off pending a shadow window, and why the breaker (which needs no such
evidence) is the part worth enabling first.


---

# Second production read, 2026-08-25 (corrected instrument, scheduler ON at a 300 s floor)

```
RSS feeds in scope : 9   (+10 API adapters the scheduler does NOT touch)
requests/day        now    1,296   ->  after    1,354
FULL BODIES/day     now    1,296   ->  after    1,066

   polls    ok  arts/d fail   settled  vs sweep  feed
    4780  4780    17.3    0       31m      3.1x  Washington Times
    4781  4781    18.8    0       28m      2.8x  CNN
    4782  4782    19.5    0       27m      2.7x  NPR
    4780  4780    43.7    0       12m      1.2x  Fox News
    4781  4780       ?    0       10m      1.0x  BBC News
    4781  4778       ?    0       10m      1.0x  The New York Times
    4780  4780    81.5    0        7m      0.7x  The Hill
    4780  4779   274.7    0        5m      0.5x  New York Post
    4782  4782   195.0    0        5m      0.5x  The Guardian
```

A coherent picture at last, and a different one from either earlier read: requests +4.5%, modelled
bodies −17.7%, the two busiest feeds (NY Post 275/day, Guardian 195/day) moving from a 10-minute
sweep to a 5-minute cadence, and the three quietest drifting out to ~30 minutes. **Zero RSS feeds
failing** — so the per-feed breaker, the part with no downside, currently has nothing to act on.

## Two things this read does not settle

**The 304 rate is still unmeasured, and the bodies line depends entirely on it.** If a feed serves
no `ETag`/`Last-Modified`, its unchanged polls download a full body regardless of cadence — and
then the real figure is 1,354 bodies/day, a 4.5% *increase*, not a 17.7% saving. The estimate and
its opposite differ only by a fact nobody had checked.

The obvious way to check it does not work: the cycle aggregate carries a `notModified` counter and
**nothing ever logs it**, so `grep notModified` over the poller log returns empty however long you
wait. `observed_state()` reads the persisted `etag`/`last_modified`/`interval_s` columns instead —
an observation, needing no new logging, true of the whole history rather than of whatever is still
in the log buffer. It prints an explicit warning when no feed carries a validator, because that is
the state in which the cadence half should be turned off rather than tuned.

**Two of nine feeds contribute no attributable articles.** BBC News and The New York Times both
show 4,78x successful polls and `arts/d ?` — no catalog article in the scan window carries that
publisher name. Either a naming mismatch between `feed_health.name` and the registry-resolved
`publisher` (likely: "BBC News" vs "BBC"), or those feeds genuinely add nothing because every
article arrives first from another provider. The first is an instrument artifact; the second is
~288 requests a day for zero coverage. Worth one query to tell them apart before assuming either.

## Shipping bug found in the same session

`RWE_FEED_MIN_INTERVAL=300` was set in `deploy/.env` and never reached the container: compose
`environment:` is an explicit allowlist and only `RWE_FEED_SCHEDULER` had been listed. The
scheduler could be turned on but not slowed down or tuned. `printenv RWE_FEED_SCHEDULER
RWE_FEED_MIN_INTERVAL` printing ONE line is the check that caught it — for an allowlisted stack,
verifying an env change means verifying every variable, not the one that happened to work.
