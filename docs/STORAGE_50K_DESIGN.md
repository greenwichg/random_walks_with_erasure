# M3 — Storage scalability for the 50,000-source target

**Audit and design only.** Nothing here has been migrated, no source has been expanded, and no
production setting has been changed. The deliverable is the measurement, the ranked bottlenecks, the
smallest architecture that clears them, and seven acceptance bars written as numbers a harness can
check.

Grounded in `docs/CAPACITY_AND_COST.md` (2026-07-27), the 50k stress findings in
`docs/STRESS_50K_PLAN.md`, and a new harness — `examples/storage_bench.py` — built for this
milestone because the stress ladder said in as many words that it could not answer this question:

> *"Catalogue growth at scale. 156 rows is the sample, not 50k-scale accumulation.
> `bytes_per_article` is meaningless at that row count and should be ignored below ~10k rows."*

---

## 0 · The one-paragraph answer

**The single writer is not the problem, and Postgres is not the answer — yet.** Measured, SQLite
sustains **~330 articles/second** through the real ingest path against a 1 M-row catalogue, with
**zero** `database is locked` errors at 16 concurrent writers. The 50,000-source target needs
**2.9 articles/second**. That is 114× headroom, and it retires "break #5" as the *leading* storage
risk.

What actually breaks is three Python-side passes that are O(catalogue) or O(sources) and run while
holding the global ingest lock, plus a backup strategy whose cost is linear in database size and
whose cadence is not. In rank order: the **age-based retention pass** loads the entire catalogue
into Python (90 s and **+5.6 GB of RSS at 1 M rows** — it exhausts a 4 GiB box at roughly
730 k); **`corpus.tier_of` is linear in the number of configured sources** (4.1 ms *per article* at
50 k, i.e. ~4,000× slower than it needs to be); and **three of the five bounded prunes full-scan
their table** because the column they filter on has no index. All three are fixable inside SQLite,
none requires a substrate migration, and none is visible today because retention is switched off.

---

## 1 · The harness

`examples/storage_bench.py` — offline, synthetic, never production (there is deliberately no `--db`).
It builds a catalogue at a geometric ladder of sizes and measures growth, write throughput,
retention, the hot-path probes, the catalogue queries, and backup/restore.

Two properties make its numbers usable rather than decorative:

* **The bulk fill is calibrated against the real write path.** Filling 1 M rows through
  `rss_ingest.ingest_entries` would take ~50 minutes, so the ladder uses `executemany` — which is
  only legitimate if the rows are the same shape. `--calibrate` ingests 2,000 articles through the
  real path and compares bytes-per-article. The first version failed this at **23% drift** (a
  plausible-looking `scored` payload that was half the real size); the shipped version passes at
  **0.3%**.
* **The fill verifies its own row count.** `created_at` is `NOT NULL` with a *Python-side* default,
  so SQLite has no default to supply and `INSERT OR IGNORE` discarded every row in silence. The
  first ladder ran green against an **empty table**. `_fill` now counts before and after and raises.

Both of those were caught, not designed around, and they are recorded here because a storage
benchmark that measures the harness is worse than no benchmark.

```bash
python examples/storage_bench.py                                  # the full ladder
python examples/storage_bench.py --rungs 25000,100000 --skip-backup   # a quick pass
python examples/storage_bench.py --json out.json                  # machine-readable
```

**Host caveat, stated once and applying to every number below.** The ladder ran in a 4-vCPU /
16 GiB container. Production is a `t3.medium`: **2 vCPU, 0.40 sustainable, 4 GiB RAM, 30 GiB gp3.**
Byte counts, row counts and memory transfer directly. **Every wall-clock number below is optimistic
for production**, by roughly the core-count ratio for anything parallel and by less for the
single-threaded paths, which is most of them.

---

## 2 · Measured: the ladder

50,000 distinct publisher hosts, 45,000 of them excluded by the tier prefilter — the shape at the
target, not a single-host catalogue.

### 2.1 Growth

| rows | DB (incl. WAL) | B/article | index share |
|---:|---:|---:|---:|
| 25,000 | 63.3 MB | 2,530.7 | 19% |
| 100,000 | 249.2 MB | 2,492.0 | 18% |
| 400,000 | 996.4 MB | 2,491.1 | 18% |
| 1,000,000 | 2,473.4 MB | 2,473.4 | 19% |

**Growth is linear and the constant is stable from 100 k rows up.** The harness's 2,473 B/article is
below production's measured **3,078 B** because the synthetic rows carry no images and no body; the
gap is content, not structure, so **3,078 B stays the projection constant** and 2,473 B is what a
body-free Tier B / shadow row costs.

Nine indexes cost **18–19%** of the file, and that share is flat across the ladder, so indexes do not
become disproportionate with size. But the *attribution* is the finding — from the `dbstat` virtual
table at the 1 M rung:

| object | bytes | share |
|---|---:|---:|
| `feed_articles` (table) | 1,368.8 MB | 55.3% |
| **`scored_articles` (table)** | **642.9 MB** | **26.0%** |
| `sqlite_autoindex_feed_articles_1` (canonical-URL PK) | 121.5 MB | 4.9% |
| **`sqlite_autoindex_scored_articles_1`** | **114.0 MB** | **4.6%** |
| `ix_feed_source_feed` | 45.6 MB | 1.8% |
| `ix_feed_articles_fetched_at` | 40.2 MB | 1.6% |
| `ix_feed_published_at` | 38.1 MB | 1.5% |
| `ix_feed_publisher` / `ix_feed_publisher_lower` | 30.9 MB each | 2.5% |
| `ix_feed_category` / `ix_feed_lean` / `ix_feed_country` | 19.5 / 10.3 / 10.3 MB | 1.6% |

**The score cache is 30.6% of the database** (table + its primary-key index), and it is a *pure
cache* — `ingest.score_with_cache` re-derives an entry deterministically. Its retention default is
30 days, and §2.8 shows the column its prune filters on has no index. At 50,000 sources that makes
it the largest single storage lever after the catalogue horizon itself: **~30% of the volume, for
data the product can regenerate.**

`ix_feed_source_feed` is the largest secondary index because `source_feed` stores a full feed URL —
worth noting, not worth acting on at 1.8%.

### 2.2 Write throughput — the surprise

Real `ingest_entries`, N threads at once, against the catalogue at each rung:

| rows | 1 writer | 4 writers | 16 writers | p95 1 / 4 / 16 | `database is locked` |
|---:|---:|---:|---:|---|---:|
| 25,000 | 337.6/s | 331.6/s | 286.3/s | 3.60 / 29.05 / 157.54 ms | **0** |
| 100,000 | 336.2/s | 315.6/s | 283.8/s | 3.18 / 34.09 / 165.43 ms | **0** |
| 400,000 | 332.0/s | 327.3/s | 285.2/s | 3.23 / 28.94 / 172.91 ms | **0** |
| 1,000,000 | 335.1/s | 317.3/s | 284.8/s | 3.05 / 32.27 / 172.10 ms | **0** |

Three readings, and each one matters:

1. **Aggregate throughput is flat in the writer count** — ~330/s at one writer, ~285/s at sixteen.
   Concurrency buys nothing (one writer, as advertised) and costs ~15%. The pool's value is
   *fetch* concurrency, which is what M6.3 built it for; it was never going to be write concurrency.
2. **Throughput is flat in catalogue size** too — 335/s at one writer against 25 k rows and
   against 1 M. The write path is index-bound, not scan-bound.
3. **Zero lock errors, at every rung, at every width.** `busy_timeout=5000` absorbs the contention
   entirely; it shows up as p95 latency (3 ms → 173 ms), never as an error. A 173 ms p95 on a
   background ingest thread is invisible.

**330 articles/second is 28.5 M articles/day.** The 50,000-source target is **250,000/day** =
2.9/s. The measured headroom is **114×**, and it is the reason the rest of this document is about
Python passes and backups rather than about Postgres.

### 2.3 The hot-path probes

| rows | `count_feed_articles` | `catalog_fingerprint` | `storage_stats` |
|---:|---:|---:|---:|
| 25,000 | 0.6 ms | 0.6 ms | 7.3 ms |
| 100,000 | 1.2 ms | 1.6 ms | 23.9 ms |
| 400,000 | 5.2 ms | 5.2 ms | 79.2 ms |
| 1,000,000 | 12.0 ms | 12.4 ms | 190.2 ms |

All three are linear. `catalog_fingerprint()` is `(COUNT(*), max(fetched_at))` and runs on every
cached-build check; `storage_stats()` is **eleven full-table COUNTs** and runs at the end of *every*
`run_cleanup` pass. At the 7.5 M rows a 30-day archive implies they are ~90 ms and ~1.4 s
respectively — the fingerprint is fine, `storage_stats` is not, because it is paid for observability
on a pass that usually deletes nothing.

### 2.4 The catalogue queries

| rows | tier exclusion (45,000-term `NOT IN`) | 60,000-row scan window |
|---:|---:|---:|
| 25,000 | 290.3 ms | 938.6 ms |
| 100,000 | 369.0 ms | 2,370.2 ms |
| 400,000 | 1,041.9 ms | 3,217.9 ms |
| 1,000,000 | 2,418.2 ms | 2,152.3 ms |

The exclusion prefilter independently reproduces the stress ladder's **277 ms at 50 k** (here
290.3 ms at the same 45,000 terms) and then grows with the catalogue on top of it. The scan window
plateaus, as it must — it is capped at `RWE_STORIES_MAX_SCAN`, so past 60,000 rows the differences
between 2.2 s and 3.2 s are run-to-run variance on a fixed amount of work, not a trend.

### 2.5 Retention — the bottleneck

`storage_lifecycle.run_cleanup`, one pass, deleting **nothing** in every case. Steady state is the
case that matters: retention's cost is paid on every pass, and the overwhelming majority delete
nothing.

| rows | no policy | count policy (under cap) | **age policy** | RSS delta, age pass |
|---:|---:|---:|---:|---:|
| 25,000 | 23.6 ms | 20.7 ms | **1,271 ms** | +104.8 MB |
| 100,000 | 56.5 ms | 56.9 ms | **5,331 ms** | +527.5 MB |
| 400,000 | 185.1 ms | 198.7 ms | **35,367 ms** | +2,343.2 MB |
| 1,000,000 | 475.8 ms | 429.3 ms | **89,765 ms** | **+5,582.0 MB** |

The count-only policy is cheap because `run_retention` has a documented fast pre-gate for it
(`count_feed_articles` instead of a full plan). **An age policy deliberately skips that gate** — an
age rule can have prunable rows at any catalogue size — and falls through to:

```python
articles = store_.list_feed_articles(limit=10_000_000)     # corpus_health.py:560
```

The entire catalogue, materialised as Python dicts, before anything is decided. Two consequences:

* **Time is supra-linear.** 4× the rows costs 4.2× then 6.6×, and 2.5× more rows costs 2.5× again at
  the top — the load, a full `sorted()`, the raw-policy loop, up to four repair passes, and a
  `corpus_metrics` pass over the kept set. **90 seconds at one million rows.**
* **Memory is 5.58 KB of RSS per catalogue row**, dead linear across the ladder (+104.8 MB at 25 k →
  +5,582 MB at 1 M). **On a 4 GiB box that is an OOM at roughly 730,000 rows** — before the disk
  fills, before the CPU budget binds, and while the log still reports `pruned=0`.

And it runs **holding the global ingest lock** (`sources.py:1671`, inside `_post_cycle`), so its
duration is lock occupancy.

This is not hypothetical configuration. **M2 shipped per-tier age retention**
(`RWE_RETENTION_MAX_AGE_DAYS_TIER_B` / `_SHADOW`), and `docs/SCALE_ROADMAP.md` break #2 makes
age-shaped retention a *requirement* before source coverage grows. **The next step the roadmap asks
for is the step that breaks.**

### 2.6 Backup and restore

| rows | DB | page copy | `integrity_check` | gzip -6 | ratio | restore | WAL forced by a concurrent backup |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 25,000 | 63.3 MB | 0.18 s | 0.21 s | 1.15 s | 8.62× | 0.71 s | 15.5 MB (3,400 rows) |
| 100,000 | 249.2 MB | 0.74 s | 0.91 s | 4.58 s | 7.83× | 4.15 s | 53.2 MB (14,000 rows) |
| 400,000 | 996.4 MB | 3.17 s | 4.11 s | 18.18 s | 7.79× | 17.94 s | 353.8 MB (60,400 rows) |
| 1,000,000 | 2,473.4 MB | 6.28 s | 11.90 s | 45.09 s | 7.78× | 49.34 s | **477.9 MB** (100,800 rows) |

Normalised at the 1 M rung: **copy 2.5 s/GB, integrity 4.8 s/GB, gzip 18.2 s/GB, restore
19.9 s/GB** — within 10% of the 400 k rung, so the pipeline is linear in database size. It costs
**25.5 s/GB**, and **71% of it is gzip** — the compressor, not the database. The compression ratio
is stable at **7.8×** from 100 k rows up.

The last column is the mechanism nobody had measured. `Connection.backup()` with the default
`pages=-1` copies the whole database in one step, holding a read transaction for its entire
duration — and an open read transaction is exactly what prevents a WAL checkpoint. Every write made
during the backup accumulates in the WAL, on the same volume: **354 MB in 3.2 s at the 400 k rung,
478 MB in 6.0 s at 1 M.**

**Read that as a mechanism, not a forecast.** The harness's writer is a tight `executemany` loop
running at ~19,000 rows/second — thousands of times faster than the 2.9 writes/second 50,000 sources
actually imply. The relationship is *WAL ≈ backup duration × write rate*, so at a realistic ingest
rate the WAL a backup forces is small even for a large database. What the measurement establishes is
that the term exists and is unbounded by anything except how long the backup takes — which is
exactly why §2.9's `journal_size_limit=-1` (the WAL never shrinks back) matters, and why a shorter
or less frequent backup helps twice.

### 2.7 `corpus.tier_of` is linear in the number of sources

Not a storage structure, but it sits inside the retention path (`corpus_health._tier_age_resolver`
calls it **per article**) and inside `corpus.select`'s row loop, so it is a storage-cost finding.

| configured hosts | `tier_of` per call |
|---:|---:|
| 100 | 12.1 µs |
| 1,000 | 76.3 µs |
| 5,000 | 367.4 µs |
| 20,000 | 1,699.9 µs |
| 50,000 | **3,428.6 µs** |

Cleanly linear. The cause is `corpus._host_match`:

```python
return bool(host) and any(host == h or host.endswith("." + h) for h in hosts)
```

— a full scan of the configured host set, per article, up to four times per `tier_of` (shadow and
Tier B, each tested against URL and publisher). At 50,000 hosts that is up to 200,000 string
comparisons **per article**.

A per-tier retention pass over a 7.5 M-row catalogue would spend **8.5 hours** in this function
alone. Even at today's ~50,000-row production catalogue, turning on a per-tier age would add
**~205 seconds of held ingest lock per pass**.

The equivalent formulation is O(labels) and independent of source count: walk the article host's own
label suffixes (`news.example.com` → `news.example.com`, `example.com`, `com`) and test each against
the set. Measured at **0.84 µs** at 50,000 hosts — **~4,000× faster**, and provably the same
predicate, because `host.endswith("." + h)` is true exactly when `h` is one of those suffixes.

### 2.8 Three of the five bounded prunes full-scan their table

`EXPLAIN QUERY PLAN` against the real schema:

| prune | filter column | plan |
|---|---|---|
| `prune_orphan_event_locations` | `canonical_url` | `SEARCH … USING COVERING INDEX` ✅ |
| `prune_scored_cache` | `scored_articles.created_at` | **`SCAN scored_articles`** |
| `prune_analytics_events` | `analytics_events.created_at` | **`SCAN analytics_events`** |
| `prune_rec_events` | `rec_events.shown_at` | **`SCAN rec_events`** |
| `prune_report_snapshots` | per-user, `id`-ordered | indexed by `user_id` ✅ |

`analytics_events` has four indexes and none is on `created_at` (`ix_analytics_events_server_ts` is
a different column). `rec_events` is indexed on `user_id` only. `scored_articles` has only its
primary key.

`scored_articles` is the expensive one: it holds one row per article for 30 days by default, so at
250,000 articles/day it is a **7.5 M-row table that is fully scanned on every cleanup pass** to find
the rows to delete.

Measured per step, at 400 k catalogue rows with catalogue retention **off** and nothing to delete:

| step | ms | what it is |
|---|---:|---|
| `feed_articles` | 0.0 | disabled — no catalogue policy |
| `article_event_locations` | 19.3 | the orphan reaper, correctly index-backed |
| **`scored_articles`** | **117.6** | **full scan of the 400 k-row score cache, finding nothing** |
| `analytics_events` | 1.9 | a scan, but of an empty table here — see the caveat |
| `rec_events` | 1.5 | same |
| `report_snapshots` | 0.8 | per-user, index-backed |
| **`storage_stats`** | **94.7** | **eleven full-table COUNTs, for a log line** |
| **total** | **235.8** | |

**90% of a pass that deletes nothing is those two steps.** At the 7.5 M score-cache rows a 30-day
default implies at 50 k sources, the scan alone extrapolates to ~2.2 s per pass.

*Caveat, stated rather than glossed:* `analytics_events` and `rec_events` are near-zero here because
the harness does not populate them. Their query plans are scans, so their cost grows with their own
row counts in production; the 1.9 ms and 1.5 ms above are the shape of the plan, not a measurement of
the production tables.

### 2.9 The WAL and pragma configuration, audited

`store.SQLITE_PRAGMAS` is applied to every connection by a SQLAlchemy `connect` listener. Read back
from a live engine, together with the defaults nobody set:

| pragma | value | verdict |
|---|---|---|
| `journal_mode` | `wal` | ✅ the right mode; readers never block the writer |
| `synchronous` | `1` (NORMAL) | ✅ the documented WAL pairing |
| `busy_timeout` | `5000` | ✅ and §2.2 shows it absorbing 16-way contention with zero errors |
| `foreign_keys` | `1` | ✅ |
| `page_size` | `4096` | ✅ default, and the rows are ~2.5 KB so one row per page |
| `wal_autocheckpoint` | `1000` pages ≈ 4 MB | ✅ as a default — but see §2.6: a backup's read transaction blocks the checkpoint regardless |
| **`journal_size_limit`** | **`-1`** | ⚠ **the WAL is never truncated after a checkpoint.** It stays at its high-water mark — which §2.6 shows a backup-under-load setting at 478 MB — and that disk is held until something runs `wal_checkpoint(TRUNCATE)` |
| **`auto_vacuum`** | **`0` (NONE)** | ⚠ **freed pages are reused, never returned to the OS.** The database file never shrinks on its own |
| `cache_size` | `-2000` (2 MB/connection) | ⚠ 15 pooled connections × 2 MB against an 18.7 GB database; every lookup falls through to the OS page cache, on a 4 GiB box |
| `mmap_size` | `0` | — not set; an option, not a defect |

Connection pool: SQLAlchemy 2.0 `QueuePool`, **5 + 10 overflow = up to 15 connections**.

**What `auto_vacuum=NONE` costs, measured.** Deleting ~48% of a 400,000-row catalogue plus its
orphaned score-cache rows, in one SQL statement pair:

| step | result |
|---|---|
| the delete itself | **18.18 s** for 195,558 catalogue rows + their cache rows |
| file size afterwards, WAL checkpointed | **1,007.9 MB — unchanged** |
| freelist | 91,847 pages = **376.2 MB reusable but not returned** |
| `VACUUM` | **5.41 s**, file → **506.3 MB** |

In steady state this is fine: new rows reuse the freelist and the file plateaus. It matters exactly
once — **the first time a horizon is shortened on an already-grown catalogue** — and then it needs a
`VACUUM`, which holds an exclusive lock and requires **one whole extra file's worth of free disk**.
At 18.7 GB that is ~100 seconds of exclusive lock and 18.7 GB of headroom, and it belongs in the
volume sizing rather than in a surprise.

---

## 3 · Ranked bottlenecks

| # | Bottleneck | Binds at | Shape | Fix class |
|---|---|---|---|---|
| **S1** | Age-retention loads the whole catalogue (time **and** RSS), under the ingest lock | **~730 k rows** on a 4 GiB box | O(n) memory, supra-linear time | code — make it SQL-shaped |
| **S2** | `corpus.tier_of` is O(configured sources) per article | **any** per-tier age policy, today | O(sources × articles) | code — O(labels) suffix lookup |
| **S3** | Hourly full-file backup + `integrity_check` + gzip | ~5 GB database | 25.5 s/GB, cadence fixed | ops — cadence + compressor |
| **S4** | A backup pins the WAL for its whole duration, so WAL ∝ duration × write rate | scales with backup time, not catalogue size | mechanism measured; magnitude at production write rates is small | ops — same fix as S3, plus S9 |
| **S5** | Three prune columns unindexed (`scored_articles` worst) | ~1 M rows in the score cache | full scan per pass — **117.6 ms of a 235.8 ms pass at 400 k** | schema — three indexes |
| **S5b** | The score cache is **30.6%** of the database, for regenerable data on a 30-day default | now | linear | ops — one env var |
| **S6** | Volume capacity | depends entirely on the retention horizon | linear | ops — size the volume |
| **S7** | `storage_stats()` — 11 full COUNTs on every cleanup pass | ~5 M rows | linear | code — sample, don't count |
| **S8** | Tier lists live in environment variables | ~30 k sources (1 MB of a 2 MB `ARG_MAX`) | linear | code — move to a table |
| **S9** | `journal_size_limit=-1` — the WAL never shrinks below its high-water mark | first big backup under load | one-time step | ops — one pragma |
| **S10** | `auto_vacuum=NONE` — the file never shrinks when a horizon is first shortened | first horizon change | one-time, needs 1× file free | ops — a planned `VACUUM` |
| ~~S11~~ | ~~SQLite single-writer throughput~~ | — | **measured, cleared**: 114× headroom | — |

**S11 is the correction this milestone makes to its own premise.** `SCALE_ROADMAP.md` break #5 and
`CAPACITY_AND_COST.md` bottleneck #6 both name SQLite write concurrency as the storage risk. Against
a real 1,000,000-row catalogue with 16 concurrent real ingest threads it is not, by two orders of
magnitude, and the cost of believing otherwise would have been a substrate migration bought to solve
a problem that does not exist.

---

## 4 · The smallest architecture that clears them

**Stay on SQLite.** Nothing measured here is a substrate problem. The design is four code changes,
one schema change, and a handful of settings — no migration, no second database, no new service on
the critical path.

### D1 — Retention becomes SQL-shaped for Tier B and shadow, and stays validation-aware for Tier A

The Python planner exists for a real reason: `corpus_health.plan_retention` guarantees the
configured floors (minimum total / publishers / per-political-bucket / fresh) survive any prune. That
guarantee is about **the serving corpus**, and the serving corpus is Tier A.

So split the pass by what the floors are actually protecting:

* **Tier A** keeps today's validation-aware planner, unchanged — but over the Tier A rows only,
  and **with an explicit ceiling**.

  **A correction to the obvious argument, before it gets made.** It is tempting to say Tier A is
  already bounded at 83,000 by `corpus.DEFAULT_TIER_A_BUDGET`. It is not. That constant's own
  docstring says *"This is a WARNING threshold, not a gate. Nothing is dropped for exceeding it"*,
  and it is about the **6-day clustering window**, not the stored row count. Nothing in the code
  bounds how many Tier A rows the catalogue holds; what bounds it in practice is the number of
  rated outlets, which is a strategic constraint rather than a mechanism.

  So the Tier A arm needs a ceiling of its own: **refuse and log above `N` Tier A rows** rather
  than load them. At the measured 5.58 KB of RSS per row, a 4 GiB box gives an honest N around
  **150,000** (≈840 MB, ≈6 s by the §2.5 curve). Above that the planner declines, says so, and the
  SQL arm handles Tier A too — losing the floor guarantee, loudly, instead of losing the process,
  silently.
* **Tier B and shadow** get a bounded SQL delete per pass:
  `DELETE FROM feed_articles WHERE <tier predicate> AND published_at < :cutoff LIMIT :batch`,
  reusing the existing `RWE_RETENTION_BATCH_LIMIT`. The tier predicate is the same publisher
  set `corpus.sql_exclusions()` already builds for the query path, and `ix_feed_published_at`
  already indexes the cutoff.

  The floors do not apply here and saying so is the argument, not a shortcut: **shadow is surfaced
  nowhere**, so a per-bucket or fresh floor over it protects nothing a reader can see; **Tier B is
  searchable but never clusters**, so the political-bucket and freshness floors — which exist to
  keep *story formation* honest — are not about it either. A minimum-total floor over Tier B, if
  wanted, is a `LIMIT`, not a planner.

This removes the O(n) memory entirely and replaces supra-linear Python with an indexed delete.
**Measured, on the same 400,000-row catalogue** (`EXPLAIN QUERY PLAN` confirms the subselect uses
`ix_feed_published_at` and the delete uses the canonical-URL primary key):

| pass | today (Python planner) | proposed (batched SQL) |
|---|---:|---:|
| **steady state — nothing to delete** | 35,367 ms, +2,343 MB RSS | **0.1 ms**, no Python load at all |
| steady state, per-tier (45,000-term `IN`) | 35,367 ms + ~205 s of `tier_of` | **5.4 ms** |
| a pass that deletes its full 5,000-row batch | — | **860–1,240 ms** |

The steady-state number is the one that matters, because §2.5's whole point is that almost every
pass deletes nothing. **35 seconds becomes 5 milliseconds**, and A3's 2,000 ms bar is met even on the
passes that do delete.

Written as a bounded subselect (`WHERE canonical_url IN (SELECT … WHERE published_at < ? LIMIT ?)`)
rather than `DELETE … LIMIT`: the `LIMIT` form needs `SQLITE_ENABLE_UPDATE_DELETE_LIMIT` at compile
time, and this repository has already been caught once assuming a SQLite build option
(`STRESS_50K_PLAN.md` §3.5 on `SQLITE_MAX_VARIABLE_NUMBER`). The subselect form works on every build.

### D2 — `corpus._host_match` becomes O(labels)

Replace the full-set scan with the label-suffix walk in §2.7. Provably the same predicate, 4,000×
faster at 50 k sources, constant in source count. **D1's per-tier arm depends on this**, and so does
`corpus.select`'s row loop — this is the change that makes tier assignment free at any N.

### D3 — Three indexes, and a shorter score-cache window

**The indexes:** `scored_articles.created_at`, `analytics_events.created_at`, `rec_events.shown_at`.
Additive, reversible, no data migration — and they turn three per-pass full scans into indexed range
lookups. `scored_articles` is the one that matters: 117.6 ms of a 235.8 ms pass at 400 k rows, and
~2.2 s per pass at the 7.5 M rows a 30-day cache implies at 50 k sources.

**The window:** the score cache is **30.6% of the database** (§2.1) and is *pure cache* —
`ingest.score_with_cache` re-derives an entry deterministically from the same scorer, so a cache miss
costs CPU and loses nothing. `RWE_RETENTION_SCORED_DAYS` already exists and already defaults to 30.
Its only consumer is a re-poll or a re-read of the same canonical URL, and both are overwhelmingly
concentrated in the first days after publication.

Shortening it is the cheapest large storage lever there is — one environment variable, no code:

| `RWE_RETENTION_SCORED_DAYS` | cache rows at 50 k sources | cache bytes | saved vs 30 d |
|---:|---:|---:|---:|
| 30 (today) | 7.5 M | ~5.7 GB | — |
| 14 | 3.5 M | ~2.7 GB | **3.0 GB** |
| 7 | 1.75 M | ~1.3 GB | **4.4 GB** |

*(at 757 B/cache row, measured: 642.9 MB of table + 114.0 MB of primary-key index over 1 M rows.)*

The re-scoring cost this trades against is small and already known: **2.91 ms/article**, and only for
articles re-encountered after the window. It should be measured on a real catalogue before the value
is chosen, but the direction is not in doubt.

**The caveat that applies to every horizon change, here and in §5.** Prunes are batch-limited at
`RWE_RETENTION_BATCH_LIMIT=5000` rows per table per pass, deliberately, so no pass holds a long write
lock. That makes a *steady state* cheap and a *transition* slow: shortening the score cache from 30
days to 7 has 5.7 M rows to drain, and at 5,000 per pass on a 600 s maintenance window that is
**~8 days**. The measured cost of a 5,000-row batched delete is 860–1,240 ms (§4 D1), so a one-off
drain wants a temporarily larger batch — 50,000 is ~9 s of lock, which is a maintenance window, not
an outage. **Raise it for the transition, put it back afterwards**, and do not discover this on day
three of an eight-day drain.

### D4 — `storage_stats()` comes off the cleanup hot path

It is observability, not policy: eleven full-table COUNTs at the end of every pass, ~1.4 s at 7.5 M
rows, to log a number nothing acts on within the pass. Keep it on the ops probe and on
`--stats`; on the poller path, log the file size (a `stat`, free) and the counts on a slow cadence.

### D5 — Backups: drop the compression level, then step out the interval

**First, the compressor, because it is 71% of the pipeline.** Measured on a real 1,007.9 MB
snapshot of the benchmark catalogue:

| gzip level | seconds | s/GB | output | ratio | vs level 6 |
|---:|---:|---:|---:|---:|---|
| 1 | 6.19 | 6.1 | 151.2 MB | 6.66× | **2.95× faster**, 17% more bytes |
| **3** | **7.79** | **7.7** | **140.3 MB** | **7.18×** | **2.34× faster, 8% more bytes** |
| 6 (today) | 18.26 | 18.1 | 129.4 MB | 7.79× | — |
| 9 | 32.97 | 32.7 | 127.1 MB | 7.93× | 1.8× slower for 1.8% fewer bytes |

**Level 3 is the right trade and it is not close.** It removes 41% of the whole backup pipeline
(25.4 → 15.0 s/GB) for 8% more backup bytes, on a volume where a gigabyte costs $0.08/month and a
CPU-second is the scarce resource. Level 9 is strictly worse than level 6 here. `store.py` already
has `RWE_BACKUP_COMPRESS` as an on/off switch; this is a level knob beside it, and every existing
`.db.gz` stays readable because gzip's level is an encoder choice, not a format.

**Then the interval.** At the corrected 15.0 s/GB, against the `t3.medium`'s **0.40 sustainable
vCPU = 1,440 CPU-seconds per hour**:

| database | pipeline per backup | hourly | 6-hourly | daily |
|---:|---:|---:|---:|---:|
| 0.5 GB (today) | 7.5 s | 0.5% | 0.1% | 0.02% |
| 5 GB | 75 s | 5.2% | 0.9% | 0.2% |
| 18.7 GB (the §5 recommendation) | 281 s | **19.5%** ❌ | **3.3%** ✅ | 0.8% ✅ |
| 23 GB (a flat 30-day archive) | 345 s | **24.0%** ❌ | **4.0%** ✅ | 1.0% ✅ |

So the smallest change that clears the bar is **level 3 + six-hourly full backups**, at a 6-hour
RPO. If a 6-hour RPO is not acceptable, the alternative is one new dependency — **continuous WAL
shipping (Litestream) with a daily full copy** — which gives a near-zero RPO *and* fixes S4, because
the long read transaction that pins the WAL then happens once a day instead of twenty-four times.

The RPO is the decision; both options clear the CPU bar.

Either way the **existing tiered local retention** (`deploy/ops/prune-backups.sh`, 12 hourly / 7
daily / 4 weekly ≈ 23 files) and the **existing S3 lifecycle** (`terraform/s3.tf`: GLACIER_IR at 7
days, DEEP_ARCHIVE at 90, expire at 365, noncurrent 30) stay as they are. Both of
`CAPACITY_AND_COST.md`'s urgent findings are **already closed** — see §6.

### D5b — Two pragmas and one planned maintenance window

Small, and named separately so they are not lost inside D5:

* **`journal_size_limit`** — set it (a few hundred MB) so a checkpoint truncates the WAL back down
  instead of leaving it at whatever high-water mark the largest backup-under-load produced. Without
  it, §2.6's 478 MB is disk the system keeps forever.
* **`cache_size`** — 2 MB per connection is a default nobody chose. Against an 18.7 GB database
  it is worth measuring a larger value (`-65536`, 64 MB) against the box's 4 GiB and the 15-connection
  pool — 15 × 64 MB is a quarter of the box, so this is a *measure-then-set*, not a raise-and-hope.
* **`VACUUM` is a planned window, not a background job.** §2.9: the first time a horizon is
  shortened, the file does not shrink until a `VACUUM` that holds an exclusive lock and needs one
  extra file's worth of disk. Schedule it beside the horizon change; do not discover it.

### D6 — Tier assignment moves out of the environment

`RWE_CORPUS_TIER_B` and `RWE_CORPUS_SHADOW` are comma-separated strings. 50,000 hosts is **999,999
bytes** — measured — against a 2,097,152-byte `ARG_MAX` that covers the whole environment *and*
argv, duplicated across the `api` and `ingest` services, in a `deploy/.env` line an operator is
expected to edit by hand. This is a config-storage problem, and it belongs in a table with the same
`(canonical, host, tier)` shape the env string is parsed into today.

**Not urgent** — it binds around 30,000 sources — but it is on the critical path to 50 k and it is
listed here so the tier lane's storage is decided deliberately rather than discovered at an
`E2BIG`.

---

## 5 · Sizing: the horizon is the design variable

Everything downstream of retention is a function of one number nobody has chosen yet: **how long the
archive keeps a Tier B / shadow article.** At 50,000 sources and 3,078 B/article:

```
250,000 articles/day × 3,078 B  =  770 MB/day
```

Volume required ≈ OS and images (10 GB) + database + 1 GB of WAL headroom + one transient
uncompressed snapshot + 23 tiered local backups at 7.8× compression. The transient column does
double duty: it is the uncompressed snapshot a backup writes before gzip *and* the free space a
`VACUUM` needs (§2.9), and the two never happen at the same moment.

| horizon | catalogue rows | database | local backups | transient | **volume needed** | gp3 $/mo |
|---:|---:|---:|---:|---:|---:|---:|
| 7 days | 1.75 M | 5.4 GB | 15.9 GB | 5.4 GB | **37.7 GB** → 50 GB | $4.00 |
| 14 days | 3.5 M | 10.8 GB | 31.8 GB | 10.8 GB | **64.4 GB** → 80 GB | $6.40 |
| 30 days | 7.5 M | 23.1 GB | 68.1 GB | 23.1 GB | **125.3 GB** → 150 GB | $12.00 |
| 90 days | 22.5 M | 69.3 GB | 204.3 GB | 69.3 GB | **353.9 GB** → 400 GB | $32.00 |

**Disk is cheap and is not the constraint** — a 30-day archive at 50,000 sources costs **$12/month**
of gp3. What the horizon really buys and spends is the two things above it: the retention pass
(D1 makes it indexed, so it stops being the limit) and the backup CPU (D5, and §4's table shows
23 GB hourly is 40% of the sustainable vCPU).

The table prices a single horizon applied to everything. Two refinements move it, both downward:

* **The score cache has its own horizon.** Of the 3,078 B/article, ~757 B is the `scored_articles`
  row and ~2,321 B is the catalogue row plus its indexes (§2.1). So
  `DB ≈ H_catalogue × 0.580 GB + H_cache × 0.189 GB` per day-of-horizon. A 30-day catalogue with a
  7-day score cache is **18.7 GB, not 23.1** — D3's env var alone.
* **Tier B and shadow can have different horizons**, which is exactly what
  `RWE_RETENTION_MAX_AGE_DAYS_TIER_B` / `_SHADOW` were built for in M2.

**Recommendation: 14 days for shadow, 30 for Tier B, 7 for the score cache, and Tier A on the
existing 83,000-row budget.** Shadow only has to outlive M8's evaluation window (~14 days); Tier B
carries the searchability contract; Tier A is already bounded by design. The resulting size depends
on how much of the 50,000 has been promoted out of shadow, which nobody knows yet — so it is a
**bracket, not a point**: from **9.4 GB** of database (everything still in shadow at 14 days) to
**18.7 GB** (everything promoted to Tier B at 30 days) — which by the same formula as the table is a
**58 GB** volume at the low end and **104 GB** at the high end. **Provision 120 GB** ($9.60/month)
and the horizon stays a product decision rather than a capacity one. It is priced here, not made
here.

---

## 6 · What has already been fixed since `CAPACITY_AND_COST.md`

That document's two 🔴 findings are closed, and its text is now stale in three places. Recorded so
the next reader does not re-solve them:

| Finding, 2026-07-27 | State now |
|---|---|
| 🔴 "EBS 30 GiB exhausts in ~25 days from 48 hourly full-copy backups" | **Closed.** `BACKUP_KEEP=60` is now a runaway ceiling only; real retention is tiered in `deploy/ops/prune-backups.sh` (12h/7d/4w ≈ 23 files) and stops growing with time. |
| 🔴 "S3 backups grow without bound — no lifecycle rules" | **Closed.** `terraform/s3.tf` now has `aws_s3_bucket_lifecycle_configuration.ih_backups`: GLACIER_IR at 7 d, DEEP_ARCHIVE at 90 d, expiry at 365 d, noncurrent expiry at 30 d, abort-incomplete-multipart at 7 d. |
| "Backups are full **uncompressed** copies" (also in `STORAGE_LIFECYCLE.md` §3) | **Stale.** `store.backup_compression()` defaults **on**; measured **7.8–8.6×**. Both documents should be corrected. |
| 🟠 "Catalog retention is OFF" | **Still true**, and now the interesting part: §2.5 says turning the *age* form on is what breaks. D1 is the prerequisite. |
| 🟢 "SQLite write concurrency — not on this trajectory" | **Confirmed, with numbers** — §2.2. |

One thing checked and found **not** to be a defect: `RWE_RETENTION_*` is declared on the `api`
service only, not on `ingest`. That is correct — `ingest` runs `rss_ingest.py run` as a one-shot and
never calls `run_cleanup`; the only two callers are `feed_service.py:234` and `sources.py:1671`,
both in the api process. Noted explicitly because this repository has shipped a
switch-that-cannot-reach-the-container four times, and the absence of one here is a fact worth
recording rather than a gap worth "fixing".

---

## 7 · Acceptance bars

Seven, one per category the milestone was asked to bound. Each is a number `storage_bench.py` (or
the named ops probe) can check, at the **design catalogue size** — the §5 recommendation, a 6 M-row
/ 18.7 GB catalogue. `Today` is what the same measurement reports now, so a bar that is already met
is visibly distinct from one that is aspirational.

| # | Category | Bar | Today (at the rung measured) | Cleared by |
|---|---|---|---|---|
| **A1** | **Storage growth** | ≤ **3,300 B/article** all-in, index share ≤ **25%**, both measured over ≥ 100 k rows | 2,473–2,492 B, 18–19% ✅ | — (already met) |
| **A2** | **Write throughput** | ≥ **250 articles/s** sustained through the real `ingest_entries` at **16** concurrent writers, p95 ≤ **250 ms**, **zero** `database is locked` | 284.8/s, p95 172.1 ms, 0 errors, at 1 M rows ✅ | — (already met) |
| **A3** | **Retention / cleanup** | a steady-state pass that deletes nothing completes in ≤ **2,000 ms** with an RSS delta ≤ **150 MB**, at the design size | **89,765 ms / +5,582 MB at 1 M rows** ❌ — D1's replacement measures **5.4 ms** on the same data | D1, D2, D3, D4 |
| **A4** | **Backup / restore** | full backup + integrity + compress ≤ **15 min**, and ≤ **5%** of the 0.40 sustainable vCPU averaged over an hour; verified restore ≤ **30 min**; WAL forced by one backup ≤ **1 GB** | 15.0 s/GB → **4.7 min** at 18.7 GB ✅; **19.5% of vCPU** if hourly ❌; restore 19.9 s/GB → 6.2 min ✅; WAL 478 MB at 2.5 GB ✅, unmeasured at 18.7 GB ⚠ | D5 |
| **A5** | **Database size** | database ≤ **60%** of the volume, local backups ≤ **25%**, ≥ **15%** free at all times; alert at 70% used | 0.5 GB of 30 GB ✅ | §5 sizing (120 GB) |
| **A6** | **Memory / disk** | no single pass exceeds **50%** of box RAM; steady-state ingest RSS ≤ **1 GiB** | the age pass needs **5.58 KB per catalogue row** — 50% of a 4 GiB box at **~366 k rows** ❌ | D1 |
| **A7** | **Concurrent ingestion** | **16** pool workers with zero lock errors, and post-cycle lock occupancy ≤ **25%** of wall time | 0 lock errors at every rung ✅; occupancy 12.8% at 13 adapters ✅ — but **never measured with retention on**, and retention is what holds the lock ⚠ | D1 |

**Three of seven are already met and were never the risk.** A3 and A6 are the same defect counted
twice — the retention pass — and A7 is that defect again seen from the lock's side. **D1 alone moves
four of the seven.**

### The two things a bar cannot yet claim

* **A4's WAL clause is extrapolated.** The measured point is 478 MB of WAL forced by a backup of a
  2.5 GB database under a synthetic writer running far faster than production ingestion. The
  relationship is duration × write rate, so at 18.7 GB and a realistic 2.9 writes/second it should
  be far *smaller* — but "should be" is not a measurement, and the harness should take one at the
  design size before this is called cleared.
* **A7 has never been observed in the state that matters.** Production's 12.8% lock occupancy was
  measured with catalogue retention **off**. Turning it on is precisely what puts a multi-second
  pass inside the lock, so the number that exists is not evidence about the number that matters.

---

## 8 · Suggested order, and why it is this one

Not a commitment — the milestone is audit and design — but the dependency order is a finding in its
own right, and it is short:

| step | change | why here | risk |
|---|---|---|---|
| 1 | **D2** — `_host_match` becomes O(labels) | D1's per-tier arm is worthless behind a 4.1 ms/article tier decision, and this is a pure-function change with a provable equivalence and a test that can be written to fail before it | lowest — one function, no schema, no config |
| 2 | **D3 indexes** + `RWE_RETENTION_SCORED_DAYS` | additive, reversible, and it removes 117.6 ms of the 235.8 ms pass *before* the pass is rewritten, so the rewrite is measured against a clean baseline | low |
| 3 | **D1** — SQL-shaped retention for Tier B and shadow | the headline: 35 s → 5.4 ms steady state, and the only change that lets an age policy be switched on at all | medium — it is a deletion path, so it needs the guard-flips discipline: mutate the predicate, prove the test fails |
| 4 | **D4** — `storage_stats` off the cleanup path | trivially safe once 3 lands, and 94.7 ms of a 235.8 ms pass | lowest |
| 5 | **D5 + D5b** — gzip level 3, backup interval, `journal_size_limit` | operational, no code beyond a level knob; do it before the volume grows, not after | low |
| 6 | **§5** — choose the horizons, resize the volume, raise the batch limit for the drain, plan the `VACUUM` | needs 1–5 in place, and it is the step that actually changes production data | the only one with a maintenance window — and the drain takes days at the default batch limit (§4 D3) |
| 7 | **D6** — tier lists out of the environment | binds at ~30 k sources; nothing before it needs it | medium, and deferrable |

**Steps 1–4 are all code changes that can be validated entirely by `storage_bench.py` against a
synthetic catalogue, with no production data and no source expansion** — which is the property that
makes them safe to do next.

---

## 9 · What this milestone does not settle

* **Nothing is implemented.** This is the audit and the design. D1–D6 are proposals with measured
  justifications, not landed changes.
* **The box.** Every fix here keeps a 50k-source catalogue inside SQLite; none of them makes a
  `t3.medium` a 50k-source machine. `SCALE_ROADMAP.md` already says the hardware is not priced, and
  §4's backup table is the first place the 0.40 sustainable vCPU actually binds.
* **Postgres.** Cleared as a *storage-throughput* decision (§2.2). The architectural triggers named
  in `CAPACITY_AND_COST.md` — a second host, zero-downtime deploys — are untouched by this
  measurement and remain the real reasons to revisit it.
* **The ToS / robots review**, still outstanding, still gates real sources at any scale. No number
  in this document changes that.
* **Whether the archive should be 14, 30 or 90 days.** §5 prices all three. Choosing is a product
  decision about what "complete and findable" means, and the corpus contract is the place it belongs.
