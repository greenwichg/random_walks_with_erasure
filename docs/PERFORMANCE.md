# Performance investigation — 2026-07-28

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
