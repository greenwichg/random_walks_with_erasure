# The 50,000-source pre-beta stress test

**Goal.** Experimentally validate Hidden View at ~50,000 sources *before* beta, without waiting for
public release and without crawling 50,000 real publishers to find out.

**Not in scope, deliberately.** No political ratings are required (shadow sources need none). No
source is promoted to Tier A. No public-beta change. Source *expansion* is gated separately on the
ToS review and M8's evaluation window.

---

## 1. What is already measured

Every number below came from production or from the existing measured constants, not from an
estimate. They are the inputs to every projection in §3.

| Quantity | Value | Source |
|---|---|---|
| Bytes per article | **3,078 B** | `CAPACITY_AND_COST.md`, measured through `ingest_entries` |
| CPU per article | **2.91 ms** | same, `time.process_time()` over 1,000 articles |
| Crawl poll — **locked** (`pollMs`) | **0.15 s** | production, 2026-08-26, post-M6.2 |
| Crawl poll — **unlocked** (`fetchMs`) | **2.35 s** | same |
| Post-cycle, coalesced | **~458 s/hour**, independent of source count | production, post-M6.1 |
| Tier A clustering build | **13,624 ms** at 27,764 articles → 1,489 stories | `audit_corpus_boundary.py` |
| Lock occupancy today | **12.8%** at 13 adapters | production |
| Crawl source yield | **~27–35 URLs / 7-day news sitemap** ⇒ ~4–5 articles/day | kait8, kwch |
| Row cap / Tier A budget | 60,000 / 83,000 | `story_service`, `corpus` |

---

## 2. The architecture under test

```
 M7 discovery ──► M7 validation ──► shadow ingestion ──► bounded polling ──► catalogue
   (offline +      (8 gates,          (Tier=shadow,        (M6.3 pool,        (retention,
    probe)          robots, sitemap)   never Tier A)        leases, backoff)   M3 substrate)
                                              │
                                              └──► Tier-A isolation ──► clustering (bounded)
```

The property that makes 50k tractable at all: **Tier A is bounded independently of source count.**
Shadow sources add catalogue rows and cost polling, but contribute *nothing* to the clustering
corpus. If that isolation holds, clustering time is a function of Tier A membership and is flat in
N. If it leaks, clustering goes superlinear and the whole thing collapses. **That is the single most
important measurement at every cohort.**

---

## 3. Derived limits, and the bottlenecks that remain

### 3.1 Polling is lock-bound, and 900 s intervals are impossible at 50k

Lock budget per hour, at a 50% ceiling, after the fixed post-cycle cost:

```
(3600 × 0.5 − 458) / 0.15 s  ≈  9,000 polls/hour
```

At 50,000 sources that is **one poll per source every 5.5 hours**. The current crawl interval is
900 s — four polls per source per hour, or 200,000 polls/hour, **22× over budget**.

> **Bottleneck B1 — the polling interval must scale with N.** M6's *interval ceiling* and
> *dormancy*, which I twice recorded as "not binding", become binding here. A fixed
> `RWE_CRAWL_INTERVAL` cannot serve both 2 sources and 50,000.

### 3.2 Fetch concurrency is modest

```
9,000 polls/hour × 2.35 s  =  21,150 fetch-seconds/hour  ⇒  ≥ 6 workers
```

Call it **16 workers** with headroom for slow publishers. M6.3's pool already caps this; the number
is a setting, not a build. **Not a bottleneck** — but the *politeness* ceiling (how many
simultaneous outbound connections we are willing to make, and per what unit) is still an undecided
policy question rather than a resource one.

### 3.3 Storage is the hard constraint

At ~5 articles/day/source:

```
50,000 × 5 = 250,000 articles/day × 3,078 B  =  770 MB/day  ≈  23 GB/month
```

`CAPACITY_AND_COST.md` already finds a 30 GiB EBS exhausting in ~25 days from *backups alone* at
5,000 articles/day. At 250,000/day — a **50× ingestion increase** — SQLite plus hourly full-file
backups is not a configuration that survives a week.

> **Bottleneck B2 — M3 (storage substrate) is a hard prerequisite for 50k, not a parallel track.**
> Also: backup strategy must stop being full-file copies before N grows, independently of M3.

### 3.4 CPU is not the constraint

```
250,000 articles/day × 2.91 ms  =  728 s/day  =  0.84% of one vCPU
```

Consistent with the existing capacity finding. Ingest CPU stays a rounding error even at 50k.

### 3.5 The tier prefilter was checked and cleared

`store.py:2075` builds the tier prefilter as `func.lower(FeedArticle.publisher).notin_(sorted(…))` —
a literal `NOT IN` over every shadow publisher, evaluated on every clustering fetch. At 50,000
entries that looked like a hard failure (older SQLite builds cap variables at 999).

**Measured instead of assumed:** `SQLITE_MAX_VARIABLE_NUMBER` is 250,000 on SQLite 3.45.1, and a
50,000-term `NOT IN` costs **57 ms** — linear in N, against a 13,624 ms clustering build. Negligible.

Two caveats the harness must carry rather than inherit: the production SQLite build may set a
different limit, and `sorted()` over 50,000 strings runs per call. Both are measured at every cohort
rather than trusted.

### 3.6 Discovery and validation have their own budget

M7 Stage 2 spends ~3 requests per host. 50,000 hosts ⇒ **150,000 requests**, and at the configured
2 s politeness that is ~5 hours at 16-way concurrency. Not a blocker, but it is a scheduled
campaign, not a single run — and it is the one phase that genuinely touches publishers.

### 3.7 Ranked bottlenecks

| # | Bottleneck | Binds at | Status |
|---|---|---|---|
| **B2** | Storage substrate + backup strategy (M3) | ~1k–5k | **audited — `docs/STORAGE_50K_DESIGN.md`.** Not a substrate problem: SQLite has 114× write headroom. It is the *age-retention pass* (O(catalogue) in time and RSS) and the *backup cadence* (25.5 s/GB, hourly) |
| **B1** | Polling interval must scale with N | ~10k | needs M6 interval ceiling + dormancy |
| B3 | Discovery/validation campaign scheduling | 25k+ | operational, not architectural |
| B4 | 50k adapter objects + crawl config in memory | unknown | **measure** — no basis to predict |
| B5 | Global politeness ceiling | any | undecided **policy**, not a resource limit |
| B6 | Cold-start stampede (every source due at t=0) | 10k+ | small fix: jitter initial due times |
| ~~B7~~ | ~~Clustering follows the shadow catalogue~~ | — | **closed — measured false**; a one-time subprocess spawn |

---

## 4. The staged test

Cohorts: **100 → 1,000 → 5,000 → 10,000 → 25,000 → 50,000**.

Each cohort runs the *real* registry, poller, store, corpus projection and clustering path. Only the
network is synthetic.

### 4.1 Why synthetic fetch is mandatory, not a shortcut

A 50,000-source stress test must not make 50,000 real requests to real publishers. That would be an
unauthorised crawl at a scale no robots.txt review has covered, and it would be indistinguishable
from an attack from the receiving end. **The harness is offline by default and refuses to be
otherwise without an explicit, cohort-limited opt-in.**

Real admitted sources enter the test the way M7 admits them: in small, authorised cohorts, once the
ToS review is done. The scale dimension and the realism dimension are tested separately and joined
only where authorised.

### 4.2 Measurements per cohort

| Metric | How | Threshold |
|---|---|---|
| Crawler throughput | polls completed / wall second | ≥ 90% of scheduled |
| Worker utilisation | busy-worker samples / pool size | 40–90% |
| Concurrency cap honoured | peak in-flight ≤ pool size | **hard** |
| DB growth | file bytes, bytes/article | within 20% of 3,078 B |
| Polling latency | p50/p95 `pollMs`, `fetchMs` | p95 `pollMs` < 1 s |
| Poll failures | failed / total | < 5% |
| Catalogue growth | rows added | linear in N |
| **Tier A rows** | `corpus.tier_of` over the catalogue | **flat in N — hard** |
| Tier A clustering time | `story_service` build ms | flat in N; < 30 s |
| Memory | peak RSS | < 1 GiB at 50k |
| CPU | process time / wall | < 1 vCPU sustained |
| Lock occupancy | Σ(`pollMs`+`postCycleMs`)/wall | < 50%; **hard fail ≥ 80%** |
| Starvation | sources unpolled after 2 intervals | **0 — hard** |
| Backoff correctness | failing sources' interval growth | monotone, capped |
| Shadow isolation | Tier A rows from shadow sources | **0 — hard** |

### 4.3 Hard failure thresholds

A cohort **fails** — and the campaign stops there — on any of:

1. **Any** Tier A row attributable to a shadow source. Isolation is the premise of the design; a
   single leak invalidates the cohort and every larger one.
2. Lock occupancy ≥ 80%.
3. Any source unpolled after two of its own intervals (starvation).
4. Peak concurrency > the configured pool size.
5. Poll failure rate ≥ 5% for reasons other than the injected fault cohort.
6. Clustering build time growing with N rather than with Tier A membership.

### 4.4 Minimum infrastructure to sustain 50k

Derived from §3, stated as a requirement rather than a guess:

| Resource | Requirement | Driver |
|---|---|---|
| Storage engine | **Postgres, or partitioned SQLite with a WAL-safe writer** (M3) | §3.3 — 23 GB/month, single-writer contention |
| Disk | ≥ 500 GB with lifecycle-managed backups; **no hourly full-file copies** | §3.3 |
| vCPU | 4 (2 is workable for ingest; clustering and the API need headroom) | §3.4 + 13.6 s builds |
| RAM | 4–8 GiB, pending B4 measurement | unmeasured — the harness supplies it |
| Poll workers | ~16 | §3.2 |
| Poll interval at 50k | **≥ 6 hours per source** | §3.1 |
| Tier A | unchanged bound (60k scan cap / 83k budget) | isolation makes N irrelevant |

---

## 4.5 First harness results, and two findings the plan did not predict

Cohorts 100 / 1,000 / 5,000, poll rate held at 2.5/s, 16 workers, offline fetch.

```
                100        1,000      5,000
lock occupancy  16.8%      39.3%      82.6%      <- HARD FAIL at 5k
tier A rows     0          0          0          <- isolation holds
shadow leaks    0          0          0          <- isolation holds
cluster fetch   ~4 ms      15.6 ms    46.1 ms
cluster build   20 ms      24.6 ms    6,691 ms   <- over ZERO fetched rows
cluster rows in 0          0          0
peak RSS        174 MB     179 MB     189 MB
starved         0          0          0
peak in-flight  16         16         16         <- cap honoured exactly
```

**Tier A isolation holds at every cohort** — 0 Tier A rows, 0 leaks, with every synthetic source in
shadow and `corpus.tier_of` (the documented authority, not the SQL prefilter) doing the counting.
That is the premise the whole 50k design rests on, and it is the one thing that survived unqualified.

### ⚠ B6 — the cold-start stampede

Every source is due at `t=0`, matching thread-per-adapter's "poll immediately, then every interval".
At 50,000 sources that is a **50,000-poll stampede on every restart**, bounded only by the worker
pool. It is why the measured lock occupancy climbs with cohort size even at a constant demanded poll
rate: the harness window is dominated by the initial burst, not the steady state.

This is a real production property, not an artefact — a deploy would hammer every configured
publisher at once. **The fix is jittered initial due times**, which is a small change to
`MultiSourcePoller.start`. Not made here: the brief was the harness.

### B7 — CLOSED, and it was not a bottleneck. Both of my claims were wrong.

Originally recorded here as *"clustering cost does not follow Tier A membership"* and *"something on
the warm path is proportional to the shadow catalogue"*. Attributed properly, **neither is true.**

| | measured |
|---|---|
| `build_stories` over 0 Tier A rows | **0.1 ms** |
| `_fetch` with the tier prefilter | 10–28 ms, scales mildly with catalogue |
| **First** `warm_cache` in a process | 42 – 2,305 ms, **varying 50× run to run** |
| **Second** `warm_cache`, immediately after | **1.1 ms** |

The cost is a **one-time per-process startup**: the persistent build subprocess spawning and
importing `api_server`. `build_subprocess_enabled`'s own docstring says so — *"spawn cost and the
`api_server` import are paid once, not per build"*. The run-to-run variance was the giveaway; a
scaling law does not move 50× on identical inputs.

**Tier A isolation therefore holds completely — correctness AND cost.** Clustering over an empty
Tier A is free, at every cohort, with the whole catalogue in shadow. That is the premise the 50k
design rests on and it is now measured rather than assumed.

**The methodological error, recorded because it is the reusable part:** the first attribution timed
`warm_cache` ONCE and read a startup cost as a scaling curve. `warm_cache` also single-flights and
returns `None` in microseconds when it stands down, so a single sample can measure "did this call
win the lock" instead of "what does this cost". The harness now times `_fetch`, `build_stories` and
two consecutive `warm_cache` calls separately, which is what turned a false bottleneck into a
closed one.

### On the earlier 5 s-interval runs### On the earlier 5 s-interval runs

A first pass used a fixed 5 s interval and every cohort above ~500 failed on lock occupancy. That
was the **harness measuring its own compression**: 1,000 sources at 5 s demands 200 polls/s where
50k at the plan's derived interval demands 2.5. The interval is now derived from a constant target
rate, so cohort size is the only variable. Recording it because the first numbers looked like a
dramatic architectural finding and were nothing of the kind.

## 4.6 The full ladder to 50,000 — what it did and did not establish

Ladder run after the B6 stagger landed: 100 → 50,000, 25 s per cohort, 16 workers, 2.5 polls/s held
constant.

### Established at 50,000 sources

| | measured |
|---|---|
| Registry build, 50,000 adapters | **0.13 s** |
| Steady-state throughput | 78 polls / 25 s ≈ **3.1/s** against 2.5 demanded — keeping up |
| Lock occupancy | **13.7 – 16.6%** |
| p95 poll (locked ingest) | **60.3 ms** |
| Concurrency cap | peak in-flight **16** = exactly the pool |
| **Tier A rows / shadow leaks** | **0 / 0** |
| Peak RSS | **240 – 620 MB** across runs (**B4 answered**, with real variance) |
| Tier prefilter SQL | **277 ms** at 50k |
| Cluster fetch / build | 209.5 ms / **0.1 ms** |

**B4 is closed:** 50,000 adapter objects plus the lease table cost a few hundred MB, not gigabytes.
Reported as a range because two runs differed 2.5× — a point estimate would be a flattering fiction.

**§3.5 needs a correction.** The synthetic bench put the 50k-term `NOT IN` at 57 ms. Against a real
table with a real query planner it is **277 ms** — ~5× worse, and now the largest single cost on the
clustering fetch path. Still not a blocker against a 13.6 s build, but the bench understated it, and
"measured on a toy table" was not the same as "measured".

### ⚠ NOT established — and the harness said PASS anyway

`polls` and `catalog_rows` came back **identical at 10k, 25k and 50k**: 78 and 156. At 2.5 polls/s
for 25 s, ~62 polls is the correct steady-state sample — but it means **0.2% of sources were polled
at 50k**, and these were never measured:

* **Starvation.** The check is gated on `seconds >= 2 × interval`. At 50k the interval is 20,000 s
  and the window 25 s, so it never ran — and the field kept its `0` default, printing
  `starved_sources 0` as a PASS on an invariant it had not tested. **The same "a gate that cannot
  fire reads as a gate that passed" failure this codebase has found ten times in its own
  instruments, reproduced inside the instrument built to find it.** It now reports `None` /
  `NOT MEASURED`, which is explicitly not a pass, and a new `coverage_pct` makes the sampling
  visible.
* **Catalogue growth at scale.** 156 rows is the sample, not 50k-scale accumulation.
  `bytes_per_article` is meaningless at that row count and should be ignored below ~10k rows.

### The structural limit this exposes

**You cannot observe 50,000 sources' per-source behaviour in 25 seconds.** Steady state at 50k is
2.5 polls/s against a 5.5-hour interval, so full coverage takes **5.5 hours** by definition. That is
a property of the target, not a flaw in the harness.

So the ladder validates **rate and isolation properties** — which is a real result — and per-source
properties need a long-running soak, not a longer ladder. Anyone reading "50k PASS" should read it
as *the scheduler, the lock and Tier A isolation hold at a 50,000-source registry*, and not as
*every one of 50,000 sources was exercised*.

## 5. What this plan does not settle

* **The global politeness ceiling** (B5) — how many simultaneous outbound connections Hidden View is
  willing to make, and per host / per ASN / per publisher. A resource cap is not a policy.
* **The ToS review** — still outstanding, still the gate on real sources at any scale.
* **B4, memory at 50k adapters** — deliberately unpredicted. The harness measures it.
