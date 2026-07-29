# Performance investigation — 2026-07-28

> **Production measurements arrived 2026-07-29** and are at the bottom, under
> [Measured on the live host](#measured-on-the-live-host-2026-07-29). They **correct two claims**
> made from synthetic data and kill one optimization outright. Read that section before acting on
> anything above it.

Hidden View "became noticeably slower". This is the measurement pass that answers *why*, before any
optimization. Two of the findings were implemented; the rest are ranked and left alone deliberately.

## What was measured, and what could not be

| | how |
|---|---|
| **Engine hot path** | `examples/perf_profile.py` — the REAL code (`_fetch` → `build_stories` → `clustering.cluster` → `stabilize_ids` → serialize) against a real SQLite store with the real schema, indexes and pragmas, at 5k / 10k / 20k / 40k articles |
| **Bundle** | `next build`, before and after, per route |
| **Concurrency** | five threads against one cold cache key, counting builds |
| **Config / topology** | read from `deploy/docker-compose.yml`, `Caddyfile`, the store's pragmas |
| **Production latency** | ⚠️ **not measured here** — `deploy/ops/perf-probe.sh` exists for that and must be run on the box |

**The data in the engine profile is synthetic; the code is not.** Headlines come from a Zipf
vocabulary with planted events, calibrated until the corpus reproduced the live catalog's shape —
1,477 stories vs 1,050 live-scaled, 0.295 covered share vs 0.215, largest cluster 130 vs 111. The
first two attempts were **not** calibrated and produced a single 15,940-article mega-cluster; the
timings from those runs were discarded rather than reported. Absolute milliseconds are therefore
indicative. **The scaling exponents are the finding.**

## The headline: story clustering is quadratic

```
       n  cluster ms   x prev   cold ms   cold/warm   pageKB
   5,000         399        -     1,310      2,259x     337
  10,000       1,567     3.9x     3,110      4,936x     436
  20,000       7,357     4.7x    10,148     15,613x     912
  40,000      32,016     4.4x    35,601     56,510x    2266

  stage                growth exponent (1.0 linear, 2.0 quadratic)
  SQL fetch             1.07
  clustering            2.12  <-- superlinear
  build_stories         1.70  <-- superlinear
  cold request          1.60  <-- superlinear
  warm request          0.04
  postings work         2.30  <-- superlinear
```

**Doubling the catalog quadruples clustering time.** That is the signature of "it used to be fine".
The live six-day window is ~19,846 articles, so a cold `/api/stories` costs roughly **10 seconds**,
against **0.65 ms** warm.

The cause is in `clustering.cluster`'s candidate walk. For each article it walks the postings list
of every token it carries, so the true work unit is `sum(len(postings[token])²)` — dominated by the
few highest-frequency tokens, and growing with the **square** of the catalog. `postings work` at
exponent 2.30 is that quantity measured directly.

## Top 10 bottlenecks

| # | Finding | Evidence | Impact | Effort | Status |
|---|---|---|---|---|---|
| 1 | Story cache TTL (120 s) expired **still-correct** builds 4× per 600 s poll cycle | fingerprint already invalidates on write; poller already re-warms | ~40 s of reader-visible stall per 10 min → ~0 | trivial | **done** |
| 2 | Reader path had **no single-flight** — N concurrent cold readers ran N full builds | 5 threads → 5 builds; with the lock → 1 | 5× CPU on every cold moment | small | **done** |
| 3 | Recharts in the **home page** First Load JS for one 104px sparkline | `/` 394 kB vs `/stories` 293 kB | −99 kB on `/`, −104 kB `/analytics` | small | **done** |
| 4 | Clustering is **O(n²)** in catalog size | exponent 2.12 | 7.4 s → 32 s between 20k and 40k | **large** | ranked, not done |
| 5 | `_fetch` loads **whole ORM rows** — every column, then builds a 24-key dict per article | 844 ms of 900 ms at 20k; narrowed select = 91 ms | ~750 ms per cold build | medium | ranked, not done |
| 6 | `/api/stories` payload grows with the catalog | 337 KB → 912 KB → 2,266 KB at fixed `limit=20` | home requests `limit=60` | medium | **confirm in prod** |
| 7 | Feed poller runs **in the API process** as a daemon thread | `feed_service.py:273` | ingestion CPU competes with request serving under the GIL | large | ranked, not done |
| 8 | `stabilize_ids` rewrites **every** story member on each cold build | 73 ms at 5k → 772 ms at 40k, exponent 1.19 | a write inside a GET | medium | ranked, not done |
| 9 | **No Docker CPU/memory limits** on any service | `docker-compose.yml` has no `deploy.resources` | one container can take the whole box | trivial | ranked, not done |
| 10 | 97 of 118 components are `"use client"`; **zero** dynamic imports before this pass | `grep -rl '"use client"'` | whole app ships as client JS | large | partly addressed (3) |

Minor, noted while looking: `date-fns` is a declared dependency with **no import anywhere** in the
app; `lucide-react` is imported in 63 files (tree-shakes acceptably); Caddy compression (`zstd
gzip`) and the SQLite pragmas (`WAL`, `synchronous=NORMAL`, `busy_timeout=5000`) are all already
correct, and `fetched_at` **is** indexed — three hypotheses killed by checking rather than assuming.

## What was implemented

Only #1, #2 and #3 — the intersection of high impact and low risk. Each is independently
reversible and none changes a single clustering result.

### 1 + 2 — the cold story build, paid less often and never twice at once

`warm_cache` has always been single-flight, so the poller's eight adapter threads could not
stampede each other. The **reader** path had no such guard: every request arriving during a rebuild
started its own. Five simultaneous readers cost five full builds — on a box with fewer cores than
that has readers, they do not merely wait, they compete.

The TTL was the other half. Correctness never depended on it: the catalog fingerprint is in the
cache key, so any write invalidates immediately, and the poller re-warms off the request path. At
120 s against a 600 s poll interval the TTL was expiring four correct builds per cycle and handing
each rebuild to whichever reader arrived first.

```
                                   before      after
  cold builds per poll cycle          5           1     (4 of them bought nothing)
  builds for 5 concurrent readers     5           1     (proven by test, not asserted)
  cold build cost itself           ~10 s       ~10 s    (unchanged — this is finding #4)
```

**Neither change makes a cold build faster.** They make it rare and non-duplicated. The build's own
cost is #4, and #4 is not a low-risk change.

Re-running the profile after the change says exactly that, which is the point of re-running it:

```
       n   cluster before     after    cold before     after    stories before -> after
   5,000              399       380          1,310     1,303          875 -> 875
  10,000            1,567     1,645          3,110     3,487          931 -> 931
  20,000            7,357     7,340         10,148     9,882        1,040 -> 1,040
```

Per-build cost is flat within noise — as predicted, and worth stating plainly rather than hunting
for a number that moved. **The story counts are identical at every size**, which is the check that
matters most: a performance change that quietly altered clustering would be a product regression
wearing a speed-up's clothes.

### 3 — recharts out of the initial bundle

Five chart components split into a `*-impl.tsx` and a `next/dynamic` wrapper that keeps the
original module path, export name and props, so **no call site changed**. `ssr: false` is safe:
every consumer was already a client component fetching through React Query, so these never rendered
in server HTML. The placeholder reserves the chart's own height — a lazy chart that collapses to
zero and then pushes the page down trades a bundle win for a layout shift.

```
  route            before    after    delta
  /analytics         387k     283k    -104k
  /report            389k     288k    -101k
  /                  394k     295k     -99k      <-- the landing page, -25%
  /profile           373k     274k     -99k
  /publishers/[name] 285k     286k      +1k      <-- the wrapper module itself
  /stories           293k     294k      +1k
```

## What was deliberately NOT done

**#4, the quadratic clusterer, is the real fix and the one to be most careful with.** The obvious
lever — skipping postings lists above some document-frequency cutoff — changes which articles
cluster, and this repo has measured clustering changes against the live catalog every previous
time (`docs/CLUSTER_TRUST.md`: IDF weighting cost 361 of 3,431 covered articles; global link quorum
cost 9.3% and made coherence *worse*). Shipping a recall change inside a performance fix would be
the same mistake in a new costume. The measurement plan already exists — `audit_clustering_change.py`
— and it should be run before, not after.

**#5, narrowing the fetch,** is worth ~750 ms per cold build but `search_feed_articles` is shared by
Search, Discover and export. The low-risk shape is an additive clustering-specific query, not a
change to the shared method — and the field audit has to come first. Note the measured saving is a
**lower bound**: `body` is NULL in the synthetic corpus and may be populated in production.

**#6, #7, #8, #9, #10** are real but each needs either production numbers or a design decision.

## Confirm these in production before acting further

Run `bash deploy/ops/perf-probe.sh` on the box. It is read-only. Specifically it settles:

* the **real** cold/warm split and endpoint latencies (mine are synthetic-data timings);
* the **real** `/api/stories` payload at `limit=60`, which is what the home page requests;
* whether `body` is populated, which decides how big finding #5 actually is;
* `EXPLAIN QUERY PLAN` on the hot queries — a `SCAN` of `feed_articles` would be a finding I could
  not see locally;
* container CPU/memory against limits that currently do not exist.

## Reproducing

```bash
python examples/perf_profile.py --calibrate --sizes 20000   # check the corpus shape FIRST
python examples/perf_profile.py --sizes 5000 10000 20000 40000 --json before.json
bash deploy/ops/perf-probe.sh                               # on the production host
```

`--calibrate` exists because the first two runs of this investigation were taken on a corpus that
collapsed into one cluster. A timing on the wrong corpus is worse than no timing: it is a wrong
number with a decimal point on it.


---

# Measured on the live host (2026-07-29)

`deploy/ops/perf-probe.sh` against the running deployment at `8f023b0`, catalog 25,300 articles /
83.2 MB. **These numbers supersede the synthetic ones above wherever they disagree.**

## Two corrections to what the synthetic profile said

**1. Clustering is FASTER in production than my model predicted, and `_fetch` is SLOWER.**

| stage | synthetic @20k | production @21.9k |
|---|---:|---:|
| `_fetch` | 1,008 ms | **1,714 ms** |
| `build_stories` | 8,317 ms | **3,681 ms** |
| cold `list_stories` | 10,148 ms | **5,407 ms** |
| warm | 0.65 ms | **2.0 ms** |
| cold/warm ratio | 15,613x | **2,657x** |

My generated headlines clustered harder than real ones, so I **overstated** clustering by ~2.3x.
The quadratic *shape* stands — that was the finding — but the constant was pessimistic, and the
honest read is that a cold build costs ~5.4 s today, not ~10 s.

`_fetch` went the other way and the reason is measurable: at the same row count my synthetic
database is **18.2 MB** and production is **83.2 MB**. Production rows are ~4.6x bigger because
`body` — full article text — is populated. Finding #5 is therefore **bigger** than the lower bound
I gave: `_fetch` is now **32% of the whole cold build**.

**2. The API container is NOT sustainably CPU-saturated.** The first probe caught it at 100.49%
five minutes after a deploy — that was startup work (ingest, GKG cold-start backfill). Steady state
is **0.17%**. I reported it as a sustained finding; it was a transient. Host load 1.03 on 2 cores,
memory 1,516 MB of 3,834 MB.

## The one number that matters most

```
     min    med    max   bytes  endpoint
      25     27  65548     514K  /api/stories?limit=20
```

Median **27 ms**. Max **65.5 seconds** — the first request after the container restarted, which is
12x the steady cold build. That is startup contention (ingest + GKG backfill + first build racing
on 2 cores), not the steady cold path, and neither the TTL nor the single-flight change addresses
it: **nothing warms the cache before the first reader arrives after a restart.** Every subsequent
request was 25–37 ms.

Everything else in steady state is healthy:

| endpoint | med | bytes | |
|---|---:|---:|---|
| `/api/stories?limit=20` | 27 ms | 514K | |
| `/api/stories?limit=60` | 38 ms | **822K** | what the HOME page requests |
| `/api/stories?sort=latest` | 134 ms | 371K | sorting happens outside the cache |
| `/api/discover?limit=20` | 361 ms | 108K | **slowest steady endpoint**, max 2,560 ms |
| `/api/search?q=trump` | 12 ms | 21K | |
| `/api/health` | **117 ms** | 0K | unexplained — a liveness probe should be ~1 ms |

## New findings only production could show

* **`catalog_fingerprint` costs 18.5 ms and runs on EVERY `/api/stories` request** — 43x my
  synthetic 0.43 ms, because it is a covering-index scan of 25,300 rows. It is 70% of a warm
  request's total cost.
* **`SCAN feed_articles` on the publisher filter** — confirmed by `EXPLAIN QUERY PLAN`. The filter
  is `lower(publisher) = ?` and a function on a column cannot use `ix_feed_publisher`. **Fixed**
  below.
* **WAL at 5.7 MB** — checkpoints are lagging, worth watching but not yet a problem.
* **`deploy-backup-scheduler-1` has written 5.58 GB** of block I/O, hourly, to the same disk as the
  live database. Not on the original list; disk is at 66%.
* **Still no CPU or memory limit** on any container — now confirmed on the host, not inferred.

## An optimization killed by measurement

The app leaves SQLite's `cache_size` at the 2 MB default and `mmap_size` at 0, against an 83 MB
database. That looks like an obvious win. **It is not:**

```
  current  (cache 2MB, mmap off)     _fetch    866.7 ms   +0%
  cache 64MB                         _fetch    915.2 ms   +6%
  cache 64MB + mmap 256MB            _fetch    900.6 ms   +4%
```

Both variants were *slower*. `_fetch` is not disk-bound — it is Python object construction (ORM
row materialization, `json.loads`, dict building), exactly as the earlier breakdown showed. Raising
the page cache optimizes a resource that was never the constraint. **Not shipped.**

## A cheap fix that turned out not to be cheap

Deferring the `body` column from the clustering fetch is the obvious way to reclaim that 1,714 ms —
it is the bulk of the 83 MB and clustering never looks at article text. Except `discover.
_reading_minutes` computes `readingMinutes` from `body`, falling back to `description`. Dropping the
column would silently shrink every article's reading time on every surface.

The right fix is to **precompute `readingMinutes` at ingest** into `scored`, which makes `body`
genuinely unused at read time — but that needs a backfill, so it is a change to plan, not to slip
into a performance pass.

## Shipped from this round

**Expression index on `lower(publisher)`.** Measured at 25,000 rows: **28.7 ms → 1.8 ms (16.2x)**,
plan flips `SCAN` → `SEARCH ... USING INDEX ix_feed_publisher_lower`. An index cannot change
results, so the risk is confined to write cost and disk. It matters more later than now: the
catalog is 25k and `RWE_RETENTION_MAX_COUNT` permits **150,000**.

## Ranked, after production

| | action | evidence | worth |
|---|---|---|---|
| 1 | **Warm the cache at startup**, before the readiness gate opens | the 65.5 s first request | removes the worst number in the report |
| 2 | Cache `catalog_fingerprint` for a second, or derive it from the poller's write | 18.5 ms x every request | ~70% off a warm request |
| 3 | Precompute `readingMinutes` at ingest, then narrow the fetch | 1,714 ms, 32% of the cold build | largest engine win available |
| 4 | Investigate `/api/health` at 117 ms | 5 runs, tight spread | a liveness probe should be ~1 ms |
| 5 | `/api/discover` at 361 ms median, 2,560 ms max | slowest steady endpoint | |
| 6 | Declare container CPU/memory limits | `nanocpus: 0` | protects against the startup spike |
| 7 | The quadratic clusterer | exponent 2.12 | still the long-term ceiling |

Note how the ranking changed once real numbers arrived: **the top item did not exist in the
synthetic list at all**, and the item I would have ranked first from the repo (the page cache) is
now deleted as measured-harmful.

---

# Story-cache invalidation & warming — architecture review (2026-07-29)

Commissioned on the hypothesis that **independent provider writes invalidate the story cache too
often**. Traced, measured in three regimes, and **the hypothesis is rejected**. The machinery to
coalesce is implemented, tested and shipped **OFF**, which is the finding rather than a hedge.

## The lifecycle

```
adapter thread (one per provider, MultiSourcePoller.start)
  └─ poll_adapter_once
       └─ with self._lock:                    ← GLOBAL: serializes ALL adapters
            ├─ adapter.poll_once()            → upsert × N ⇒ fetched_at moves ⇒ FINGERPRINT CHANGES
            └─ _post_cycle(agg)
                 ├─ agg["new"] == 0 → return  (no warm — already correct)
                 ├─ storage_lifecycle.run_cleanup()   ⇒ may change the fingerprint again
                 ├─ warm_cache(store)          ← 5.6 s, INSIDE the lock
                 └─ _on_cycle()                ← corpus rebuild, 1–2 s, also inside
```

`_cached_build` keys on `(topic, dates, …, catalog_fingerprint)`. **Readers are protected by that
key, not by the warm** — which is why any scheduling change here is safe by construction.

## Rebuild frequency, measured

| regime | rebuilds | build CPU | ingest blocked | reader miss |
|---|---:|---:|---:|---:|
| steady, inline (today) | 10–11 | 57–64 s | 1.8–3.4 s | 13–18% |
| steady, coalesced 5.0 s | 12 | 67.8 s | 1.5 s | 22.2% |
| steady, coalesced 0.5 s | 12 | 71.0 s | 1.6 s | 24.5% |
| **startup burst, inline** | **2** | 11.2 s | 2.0 s | 3.1% |
| **startup burst, coalesced 5 s** | **2** | 11.5 s | 2.1 s | 3.2% |

**Coalescing made things worse at every setting, in every regime.** Rebuilds went *up*, because
deferring a warm lets a reader arrive first and build it — costing a rebuild *and* a slow request.

## Why there was nothing to coalesce

Two independent reasons, and the second is the one that settles it.

**Production writes are ~60 s apart.** The live logs put `story_cache_warm` events four docker
healthchecks (15 s each) apart. No safe quiet window merges anything.

**Adapters cannot finish simultaneously.** `poll_adapter_once` holds a global lock across poll +
post-cycle, so by the time the second adapter reaches `_post_cycle` the first one's warm has already
returned. **The same lock that made `warm_cache`'s single-flight guard dead code also makes
coalescing dead code.** Startup — eight adapters polling at once, the strongest possible case — is a
dead heat for exactly this reason.

## Verdict

The invalidations are **not redundant**. Each rebuild serves a genuinely changed catalog for the
~60 s until the next write. The 5.6 s is the *clusterer's* cost, not the scheduler's, and it belongs
to the quadratic-clustering finding — not here.

Shipped: `request_warm()`, the coalescing warmer, `RWE_STORY_WARM_COALESCE` (default **0** = inline,
byte-identical to previous behaviour) and `RWE_STORY_WARM_MAX_DELAY` as the starvation cap, plus six
tests. `story_cache_warm` now logs `coalesced`, so if the poller lock is ever narrowed — a
worthwhile change in its own right, and the precondition for any of this mattering — the question
can be re-answered from production instead of from a model.

The kill switch was broken on first write: `_env_float` treats 0 as junk and returned the default,
so setting the flag to 0 left the feature on. Caught by the test written for exactly that, which is
the argument for writing kill-switch tests at all.
