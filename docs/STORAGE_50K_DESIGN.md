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
their table** because the column they filter on has no index. All three are fixable inside SQLite
and none requires a substrate migration.

> **Correction, and it changes the urgency.** The first version of this document said "none of it is
> visible today because retention is switched off". **That was wrong** — I read the commented
> defaults in `deploy/.env.production.example` and the compose fallback (`:-0`) instead of the live
> value. Production has `RWE_RETENTION_MAX_COUNT=150000` and the catalogue is at **150,076 rows**:
> the cap is not just on, it has **just crossed**, which switches `run_retention` out of its cheap
> pre-gate and into the full whole-catalogue Python planner on every maintenance pass. The poller's
> own logs measure it at **11–21 s per pass** — 1.3× to 2.5× worse than I then estimated — which at a
> 600 s maintenance window is **1.9–3.5% lock occupancy**: pure waste, and not an outage. §2.10.
>
> **A second correction, this one against me.** I also predicted `article_entities` was leaking
> orphans on every prune. Production measured **97 orphans of 134,088 rows — 0.072%**. The missing
> reaper is real; the leak I sized it against is not. §2.12.
>
> **And the thing actually filling the disk is not storage at all.** The volume is at 76% with
> 6.3 GB free, and **8.0 GB of that is Docker build cache**, accumulating at ~500 MB per manual
> deploy. There is a prune for it in `cd-deploy.sh`, which the manual deploy path never reaches —
> and **when I had it run, it freed 458 kB of 8 GB**, because it filters on last-accessed and every
> build touches the cache. The policy needs to be a size bound, not an age bound. §2.13.
>
> **A third correction, and this one I authored.** My first version of D8 proposed moving that block
> into `update.sh` unchanged — a fix that would have fixed nothing, which is the exact defect class
> this document keeps finding in other people's code.
>
> **Separately, 20 orphaned backup temp files** (`17 .db.tmp`, `1 .db.gz.tmp`, `2 .db.tmp-journal`)
> that `backup_database` never cleans up on failure, `prune-backups.sh`'s glob cannot match, and
> `aws s3 sync` uploads — **22 objects in the bucket against 20 files on disk**, so the sync has
> caught at least two temp files mid-write during backups that then succeeded. Measured at **46 MB**:
> a correctness defect, not a capacity one, and no restore path can select one. §2.14, D9.
>
> **§9b names the pattern in my own three wrong sizings**, and says which two claims in this document
> are still unmeasured and should be read with the same scepticism.

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
below production's, because the synthetic rows carry no images, no body, and none of the enrichment
side tables. **The projection constant is production's own: 3,912 B/article, measured on the live
database in §2.11** — not the 3,078 B this document originally took from `CAPACITY_AND_COST.md`,
which predates the entity enricher. 2,473 B is what a bare, body-free Tier B / shadow row costs.

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

**The score cache is 30.6% of the database here and 25.5% in production** (table + its primary-key
index), and it is a *pure cache* — `ingest.score_with_cache` re-derives an entry deterministically.
Its retention default is 30 days, and §2.8 shows the column its prune filters on has no index. At
50,000 sources that makes it the largest single storage lever after the catalogue horizon itself:
**a quarter of the volume, for data the product can regenerate.**

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
| `prune_orphan_event_locations` | `canonical_url` | `SEARCH … USING COVERING INDEX` ✅ — but see §2.15: index-backed and still **906 ms** on production, because it probes once per side-table row |
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
| `article_event_locations` | 19.3 | the orphan reaper — **understated: the harness left this table empty.** §2.15 |
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

### 2.10 Production, measured — and it is already in the expensive state

`storage_lifecycle.py --stats` and the `dbstat` vtable against the live database, 2026-08-27:

| | value |
|---|---:|
| `feed_articles` | **150,076** rows |
| `RWE_RETENTION_MAX_COUNT` | **150,000** |
| `scored_articles` | 163,146 rows (1.087 per article) |
| `article_entities` | — (24.9 MB table + 17.9 MB index) |
| `article_event_locations` | 32,067 rows |
| `dbBytes` (file + WAL + shm) | **587.2 MB** |
| volume | 29 GB, **22 GB used, 6.3 GB free — 78%** |
| `/opt/ih/data` | 3.6 GB |

**The catalogue is 76 rows over its cap, and that is the worst place it can be.** `run_retention`'s
count-only fast path is `if catalog <= max_count: skip`. At 150,076 > 150,000 it does not skip — so
every maintenance pass now runs the full planner: `list_feed_articles(limit=10_000_000)` materialises
**150,076 rows** as Python dicts, sorts them, runs up to four repair passes and a `corpus_metrics`
pass over the kept set — **to delete about 76 rows** — and does it holding the global ingest lock.

**Measured on the box, and my estimate was low.** Interpolating §2.5's curve put this at ~8–9 s. The
poller's own `post_cycle` log over a 6-hour window says:

```
"cleanupMs": 11144.2   "cleanupMs": 11190.1   "cleanupMs": 11541.9   "cleanupMs": 20889.8
```

**11.1–20.9 seconds per maintenance pass — 1.3× to 2.5× my estimate.** The intervening `0.0` values
are the M6.1 coalescing working as designed: only the pass that is *due* pays.

The `feed_retention` events confirm the mechanism exactly:

```
{"event": "feed_retention", "pruned": 104, "kept": 150000, "catalog": 150000, "publishers": 11097, …}
{"event": "feed_retention", "pruned": 140, "kept": 150000, "catalog": 150000, …}
{"event": "feed_retention", "pruned":  58, "kept": 150000, "catalog": 150000, …}
```

The catalogue is held at exactly the cap; each pass loads 150,000 rows to delete **58–140 of them**.

**And here is the honest size of it today: at a 600 s maintenance window, 11–21 s is 1.9–3.5% lock
occupancy.** That is real, it is pure waste, and it is not an outage. The reason S1 is ranked first
is not today's number — it is the shape behind it: supra-linear in catalogue size, ~5.58 KB of RSS
per row, and an OOM on this box at ~730 k rows. What production confirms is that the expensive path
is *live*, not that it is currently hurting.

*Still unconfirmed:* the ~840 MB RSS per pass. `cleanupMs` proves the time; nothing in the logs
proves the memory.

### 2.11 Production's storage constants, which are higher than the ones this document projected from

| quantity | this document assumed | production says |
|---|---:|---:|
| all-in bytes per article | 3,078 B (`CAPACITY_AND_COST`, 2026-07-27) | **3,912 B** |
| daily growth at 50 k sources | 770 MB/day | **978 MB/day** |
| OS + Docker overhead on the volume | 10 GB | **18.4 GB** (22 GB used − 3.6 GB of data) |
| score cache share of the database | 30.6% (bench) | **25.5%** |

Attribution per article, from `dbstat`:

| object | B/article | note |
|---|---:|---|
| `feed_articles` + primary key | 2,121.6 | |
| score cache + primary key | 999.4 | 919.4 B/row × 1.087 rows per article |
| **`article_entities` + its index** | **285.2** | **7.3% of the database — see §2.12** |
| visible `feed_articles` secondary indexes | 137.3 | `fetched_at`, `published_at`, `source_feed` |
| `article_event_locations` + its index | 75.3 | |

**Why the constant rose.** `CAPACITY_AND_COST` measured 3,078 B on 2026-07-27. `article_entities` is
populated by the GDELT entity enricher, whose X5b adoption is dated **2026-08-16** — after that
measurement. The old constant is not wrong; it is pre-entities. **Every projection in §5 uses
3,912 B.**

### 2.12 `article_entities` has no orphan reaper — and retention is actively creating orphans

`article_event_locations` is a side table keyed by canonical URL with no foreign key, so catalogue
retention would strand its rows forever — which is exactly why `prune_orphan_event_locations` exists
and why `run_cleanup` runs it second, right after the catalogue prune that creates the orphans.

**`article_entities` is the same shape and has no equivalent.** The only `delete(ArticleEntity)` in
the codebase is inside `replace_article_entities`, scoped to one `canonical_url` and one `source` —
a per-article replace, not a reaper. It is also absent from `retention_policy.PROTECTED_TABLES`, so
it is not deliberately protected either: it was simply never given a lifecycle.

> **I predicted this would be a live leak, and production says it is not. Measured:**
>
> ```
> entities 134088
> orphans      97
> ```
>
> **97 orphaned rows out of 134,088 — 0.072%, about 28 KB.** I sized this defect against the whole
> 42.8 MB the table occupies and called it "a monotonic leak, growing every pass". That was wrong by
> roughly three orders of magnitude, and it was wrong for a reason I should have thought through:
> retention prunes the **oldest articles by `published_at`**, while entity rows are written by the
> GDELT enricher over a much narrower and more recent slice. Most pruned articles never had an entity
> row to strand.
>
> What survives is the *class*, not the *size*: there genuinely is no reaper, rows genuinely do
> strand — 97 of them prove the path is real — and nothing bounds it if entity coverage ever widens
> or the catalogue horizon shortens. It is a cheap gap to close and a bad one to leave open. It is
> not urgent, and **it is no longer step 2 in §8.**

The fix is the one that already exists one table over: a bounded `prune_orphan_article_entities`
beside `prune_orphan_event_locations`, in the same step of the same pass.
`ix_article_entities_canonical_url` already indexes the join column, so the plan is the same
covering-index search that makes the event-location reaper cheap — and the measurement above is what
that reaper would delete on its first run.

### 2.13 The disk pressure is 8 GB of Docker build cache, and the fix already exists in the wrong file

`docker system df` on the box:

| type | total | active | size | **reclaimable** |
|---|---:|---:|---:|---:|
| Images | 6 | 5 | 2.772 GB | 12.37 kB (0%) |
| Containers | 5 | 4 | 10.04 MB | 1.495 MB (14%) |
| Local Volumes | 3 | 2 | 5.372 MB | 5.359 MB (99%) |
| **Build Cache** | **77** | **0** | **8.037 GB** | **8.037 GB (100%)** |

**Eight gigabytes of build cache, none of it active, all of it reclaimable — more than the database
(0.587 GB) and every local backup (3.0 GB) put together.** The accounting closes: 10.82 GB of Docker
plus 3.6 GB of `/opt/ih/data` is 14.4 GB of the 22 GB used.

```
today                          22.0 / 29 GB  =  76%   (6.3 GB free)   A5 FAIL
after `docker builder prune`   14.0 / 29 GB  =  48%   (15.0 GB free)  A5 PASS with margin
```

**There is a prune for this — in a file this deployment does not run.** `cd-deploy.sh:149` carries
one, with a comment that diagnosed exactly this failure ("*It filled the disk at roughly 500 MB per
deploy, so the failure mode was 'PREFLIGHT starts refusing to deploy' some 25 deploys out*"):

```bash
PRUNE_WINDOW="${CD_BUILD_CACHE_KEEP_HOURS:-168}h"
docker builder prune -f --filter "until=${PRUNE_WINDOW}"
```

`cd-deploy.sh` **calls** `update.sh` and then prunes; the documented manual deploy runs `update.sh`
directly and never reaches it. `update.sh`'s only mention of the subject is line 133, a remediation
*message* printed after a deploy has already failed.

> **⚠ And here is where I was wrong, measured on the box.** Running that exact command freed
> **458.5 kB of 8,037 MB — 0.006%.** Three cache records were old enough; nothing else was.
>
> `--filter until=168h` filters on **last accessed**, and BuildKit touches a cache record every time
> a build reuses it. At this deploy cadence essentially the whole 8 GB is "accessed" within any
> 7-day window, so the filter can never reach it. `docker system df` reporting "100% RECLAIMABLE"
> means *not in use by a running build* — which is a different question, and I read one as the other.
>
> So the accurate statement is not "the fix exists in the wrong file". It is: **a cleanup exists in
> the wrong file, and its policy is the wrong shape.** Moving it into `update.sh` unchanged would
> have been a fix that fixes nothing — the same defect class I keep finding, produced by me, in the
> proposal meant to close it.

**An age bound cannot bound a cache that is touched on every build. A size bound can:**

```bash
docker builder prune -f --reserved-space 2GB   # evict least-recently-used down to 2 GB
docker builder prune -f --keep-storage 2GB     # the same bound on an older Docker (deprecated name)
docker builder prune -f                        # or take all 8.037 GB; next build is cold
```

**Run on the box 2026-08-27: 6.179 GB reclaimed**, leaving the cache under the 2 GB reserve — and
the run also reported *"Flag --keep-storage has been deprecated, keep-storage flag has been changed
to reserved-space"*. `prune_build_cache` therefore tries `--reserved-space` first and falls back to
`--keep-storage`, because a deploy script that only knows the retired name is a slow-motion version
of the bug it was written to fix.

Build cache is purely a build accelerator — nothing at runtime reads it, and `builder prune` never
touches images, containers or volumes. The only cost of pruning all of it is a slower next build.

### 2.14 Twenty orphaned backup temp files that nothing will ever delete — and that ship to S3

The backup directory, broken down by suffix:

```
     23 allowlist.txt          26 db.gz              23 score_reference.json
     17 db.tmp                  1 db.gz.tmp           2 db.tmp-journal
```

**Twenty files that are not backups.** `backup_database` writes to `dest_path + ".tmp"` and then
`os.replace`s it into place — correct, and the reason a partial backup is never published under a
real name. But the cleanup is missing from the failure path:

```python
src = sqlite3.connect(db_path)
tmp = dest_path + ".tmp"
dst = sqlite3.connect(tmp)
try:
    src.backup(dst)
finally:
    dst.close()                 # closes the handles …
    src.close()                 # … and never removes `tmp`
os.replace(tmp, dest_path)      # only reached on success
```

Every interrupted or failed backup leaves a `.db.tmp` behind. The two `.db.tmp-journal` files say at
least two of those were **killed** rather than raised — SQLite leaves a rollback journal when a
process dies mid-write, and a plausible mechanism is `dc up -d` recreating the `backup-scheduler`
container mid-copy during a deploy. *(Mechanism proposed, not established — the timestamps on those
files against the deploy log would settle it.)*

**Three separate things then fail to notice.** `prune-backups.sh` globs `-name '*.db' -o -name
'*.db.gz'`, and `ih_beta-…db.tmp` matches **neither** — so the tiered retention that works perfectly
on real backups (§9) cannot see these at all. `create_backup`'s gzip step does clean up on an
exception but not on a kill, which is the stray `.db.gz.tmp`. And `backup-offhost.sh` runs

```bash
aws s3 sync "$DATA_DIR/backups/" "s3://$S3/backups/"     # no --exclude
```

so **every one of those partial database copies is uploaded to S3** and then retained by the
lifecycle rules for a year.

> **Measured, and small — I over-sized this too.** The twenty files total **46 MB**, not the ~1 GB I
> expected. That is **2.3 MB each against a 554 MB database**: every one of these backups died at
> roughly **0.4% of the copy**, essentially at the moment it started, which fits a container being
> killed at recreation far better than a failure partway through.
>
> **S14 is a correctness defect, not a capacity one.** It stays on the list because files that
> nothing can delete and that get uploaded to a billed bucket are worth fixing at any size — but it
> is 46 MB, and I should not have called it "the unbounded local growth I went looking for".

**The S3 count is the more interesting number, and it is the real argument for the fix.** There are
**22 `.tmp` objects in the bucket against 20 files on disk** — *more in S3 than exist locally*, even
though nothing ever deletes a local temp file. So at least two were **transient**: the `:23`
`aws s3 sync` caught a temp file mid-write during a backup that then **succeeded**, `os.replace`
removed the local copy, and S3 — synced without `--delete` — kept the partial forever.

That means `--exclude '*.tmp*'` is not only about failed backups. **A perfectly healthy backup can
have its half-written intermediate uploaded**, purely because the sync and the backup overlapped.

**And nothing can restore one, which is the reassuring half.** Both recovery paths glob the same two
suffixes — `restore.sh:26` and `verify-restore.sh:16` use `ls -1t "$dir"/*.db "$dir"/*.db.gz` — and
neither matches `.db.tmp`. Even if one were selected, `restore_database` runs `integrity_ok` first
and refuses. The debris is inert for recovery; it is waste and noise, not a recovery risk.

**D9** is three small pieces: remove `tmp` in `backup_database`'s failure path; widen
`prune-backups.sh` to sweep `*.tmp` / `*-journal` older than a day; and add `--exclude '*.tmp*'` to
the S3 sync so a partial copy can never be shipped in the first place.

---

### 2.15 The first production pass on the new code — and a gap in this harness

`storage_cleanup`, 2026-08-27T14:01:33, six minutes after the deploy of D1/D2/D3 (`dc logs` only
retains the current container, so this is unambiguously the new code):

```json
{"total": 165, "deleted": {"feed_articles": 91, "scored_articles": 74, ...},
 "ms": {"feed_articles": 8394.0, "article_event_locations": 906.1, "scored_articles": 44.2,
        "analytics_events": 4.5, "rec_events": 4.0, "report_snapshots": 6.6,
        "storage_stats": 110.1}, "totalMs": 9469.5, "dbBytes": 591454528}
```

**D3 is confirmed live.** `EXPLAIN QUERY PLAN` on the production database now reports
`SEARCH scored_articles USING INDEX ix_scored_created_at`, `storage_diagnostics().indexErrors` is
empty, and the prune that scanned 163,146 rows costs **44.2 ms**.

**And `article_event_locations` at 906.1 ms exposed a hole in this harness.** `_fill` populated
`feed_articles` and `scored_articles` and nothing else, so the orphan reaper and the entity table —
both real per-article storage AND real per-pass cost — were measured against empty tables. §2.8
reported that step at 19.3 ms and called it "correctly index-backed"; it *is* index-backed, and on
production it is the **second most expensive step in the pass**. `_fill` now writes both side tables
at production's measured ratios (0.214 event locations and 0.893 entity rows per article).

With the side tables present, at the same 150,000 rows:

| step | this harness (4 vCPU, warm) | production (2 vCPU, cold) | ratio |
|---|---:|---:|---:|
| `feed_articles` | 2,289.4 ms | 8,394.0 ms | 3.7× |
| `article_event_locations` | 20.6 ms | 906.1 ms | **44×** |
| `scored_articles` | 0.7 ms | 44.2 ms | **63×** |
| `storage_stats` | 6.2 ms | 110.1 ms | **18×** |
| **total** | **2,316.4 ms** | **9,469.5 ms** | 4.1× |

The CPU-bound step is 3.7×, which is roughly what a `t3.medium` at 0.40 sustainable vCPU costs
against a 4-vCPU container. The *small* steps at 18–63× are not CPU — they are a **cold page cache**
six minutes after a container restart, reading index pages off EBS.

**So D1's effect on production is NOT yet established, in either direction.** The totals are
11,144–20,890 ms before and 9,469.5 ms after, but the "after" is a cold-start pass and there is no
per-step breakdown from before. A warm sample settles it; one cold sample does not, and the
temptation to read 9,469 < 11,144 as a win is exactly the reasoning §9b is about.

---

## 3 · Ranked bottlenecks

| # | Bottleneck | Binds at | Shape | Fix class |
|---|---|---|---|---|
| **S1** | Retention loads the whole catalogue (time **and** RSS), under the ingest lock | **running NOW** at **11–21 s/pass, 1.9–3.5% lock occupancy** (§2.10); OOMs a 4 GiB box at ~730 k rows | O(n) memory, supra-linear time | code — make it SQL-shaped |
| **S2** | `corpus.tier_of` is O(configured sources) per article | **any** per-tier age policy, today | O(sources × articles) | code — O(labels) suffix lookup |
| **S3** | Hourly full-file backup + `integrity_check` + gzip | ~5 GB database | 25.5 s/GB, cadence fixed | ops — cadence + compressor |
| **S4** | A backup pins the WAL for its whole duration, so WAL ∝ duration × write rate | scales with backup time, not catalogue size | mechanism measured; magnitude at production write rates is small | ops — same fix as S3, plus S9 |
| **S5** | Three prune columns unindexed (`scored_articles` worst) | ~1 M rows in the score cache | full scan per pass — **117.6 ms of a 235.8 ms pass at 400 k** | schema — three indexes |
| **S5b** | The score cache is **25.5%** of the database (production), for regenerable data on a 30-day default | now | linear | ops — one env var |
| **S12** | `article_entities` has **no orphan reaper** | not yet — **measured at 97 orphans of 134,088 (0.072%)**; the class is real, the size is not | unbounded in principle, negligible today | code — one bounded prune beside the event-location reaper |
| **S13** | **8.0 GB of Docker build cache** — `update.sh` never prunes it, and `cd-deploy.sh`'s age-based prune **freed 458 kB of 8 GB when tested**, so its policy is the wrong shape too | **now** — it is why the volume is at 76% | ~500 MB per manual deploy, unbounded | ops — a **size**-bounded prune; code — D8 |
| **S14** | **20 orphaned backup temp files** — `backup_database` never removes `tmp` on failure, `prune-backups.sh`'s glob cannot match them, and `aws s3 sync` ships them (**22 in the bucket vs 20 on disk**: the sync can capture a *healthy* backup's temp file mid-write) | now, but **46 MB** — a correctness defect, not a capacity one; no restore path can select one | one file per failed backup, forever, locally and in S3 | code — D9 |
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

**Stay on SQLite.** Nothing measured here is a substrate problem. The design is seven code changes,
one schema change, and a handful of settings — no migration, no second database, no new service on
the critical path.

### D1 — Retention stops loading columns it never reads  ✅ **STAGE 1 LANDED**

> **The audit prescribed a planner rewrite. Measuring first showed most of the win was
> somewhere much cheaper.** At 150,000 rows — production's shape — the pass breaks down as:
>
> | step | cost |
> |---|---:|
> | `list_feed_articles(limit=10M)` | **7.77 s** ← 84% of the pass |
> | `plan_retention` | 0.91 s |
> | `corpus_metrics` | 0.63 s |
> | a narrow 6-column projection | **0.54 s** (14× cheaper) |
> | the floor aggregates, in SQL | 0.25 s |
>
> **84% of the cost was loading columns retention never looks at.** `list_feed_articles`
> returns ~25 fields and JSON-parses the whole `scored` payload per row; the planner and
> its metrics read six. So stage 1 is `store.list_retention_rows()` — same algorithm, same
> floors, same prune set, fewer columns — and it carries no risk of changing a deletion
> decision at all, which the planner rewrite very much does.
>
> Measured end to end, steady state (an age policy far beyond the data, so the planner runs
> in full and prunes nothing — the case almost every pass is in), alternating runs against
> an unchanged 150,000-row catalogue:
>
> | | before | after |
> |---|---:|---:|
> | cleanup pass | 7,687 ms | **2,356 ms** (3.3×) |
> | peak RSS | 879.6 MB | **179.3 MB** (4.9×) |
> | RSS per row | 6.07 KB | **1.22 KB** |
> | OOM point, 4 GiB box | ~675,000 rows | **~3.35 M rows** |
>
> That also settles the first of §9b's two unmeasured claims: I estimated ~840 MB per pass
> on production, and it measures **879.6 MB** at 150,000 rows.
>
> **It does not clear A3 at the design size** — 2,356 ms at 150 k rows extrapolates well past
> the 2,000 ms bar at 3.5 M — so stage 2 below stands. But it fixes the live box, and it
> does so without touching a single deletion decision.

#### Stage 2 (NOT built) — SQL-shaped for Tier B and shadow, validation-aware for Tier A

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

### D2 — `corpus._host_match` becomes O(labels)  ✅ **LANDED**

Replace the full-set scan with the label-suffix walk in §2.7. Provably the same predicate, constant
in source count.

**Landed — and implementing it found a second linear term I had missed.** D2 alone did *not* make
`tier_of` flat:

| configured hosts | `tier_of` | resolver call (`_tier_with` on a held index) |
|---:|---:|---:|
| 100 | 7.86 µs | **3.35 µs** |
| 1,000 | 17.08 µs | **3.31 µs** |
| 20,000 | 198.59 µs | **3.33 µs** |
| 50,000 | **507.50 µs** | **3.85 µs** |

The matching is flat, as designed. What is still linear is *asking the environment again*:
`os.environ` decodes a fresh string on every read and `_index`'s `lru_cache` must hash the whole
999,999-byte value to find its memo — 59 µs to decode plus 380 µs to hash, per call.

So D2 shipped in two parts: the predicate, **and a `corpus.tier_resolver()` that reads the settings
once**. `corpus.select` and `audit_source_lifecycle` already hoisted `tier_index()` out of their row
loops; `corpus_health._tier_age_resolver` — the retention path — did not, and was paying the full
507 µs per article. Over a 7.5 M-row catalogue that is **~7 hours versus ~27 seconds**, inside the
global ingest lock.

Tests: `tests/test_corpus_host_match.py` (differential against the original expression, plus a guard
that the function never *iterates* the host set) and `tests/test_retention_tier_resolver_hoist.py`
(the settings are read once per pass, however many articles it resolves). Both guards were verified
by reverting the product and watching them fail.

### D3 — Three indexes, and a shorter score-cache window  ✅ **indexes LANDED**

**The indexes — landed.** `ix_scored_created_at`, `ix_analytics_created_at`, `ix_rec_events_shown_at`,
created by `Store._ensure_retention_indexes` beside the existing `_ensure_search_indexes`, sharing
its one-transaction-per-statement rule. Additive, reversible, no data migration — and, importantly,
they upgrade a **pre-existing** database in place, which is the only thing that reaches production.

Measured A/B in one process, alternating index-present and index-absent passes to control for page
cache, at 400 k catalogue rows / 400 k score-cache rows / 200 k analytics / 200 k rec-events:

| step | without | with |
|---|---:|---:|
| `scored_articles` | 102.8 ms | **0.7 ms** |
| `analytics_events` | 21.3 ms | **0.5 ms** |
| `rec_events` | 15.2 ms | **0.5 ms** |
| `storage_stats` | 72.3 ms | **17.3 ms** |
| **pass total** | **216.1 ms** | **21.4 ms** |

**10.1× on the whole pass**, and the `storage_stats` line is a genuine side effect rather than noise:
`COUNT(*)` can walk a narrow index b-tree instead of the table, so the eleven counts get cheaper too.

**The write cost, checked rather than assumed** — an index is paid for on every insert. Ingest
through the real `ingest_entries` at 4 concurrent writers, three runs each: **260–287 articles/s
without, 276–291 with**. The ranges overlap; there is **no measurable regression**, and the small
apparent gain is noise, not a speedup.

Tests: `tests/test_retention_prune_indexes.py` asserts the **query plan** (`SEARCH`, never `SCAN`)
rather than a time — a timing test would be flaky and would also pass against any table small enough
that a scan is fast, which is every test database and precisely why this went unnoticed. It also
pins the in-place upgrade of an existing database, and that a failing index neither blocks its
neighbours nor disappears silently.

**The window — NOT changed, deliberately.** `RWE_RETENTION_SCORED_DAYS` already exists, already reaches the container, and its value is a product decision about how much re-scoring CPU to trade for disk. The measurement below prices it; choosing is not mine to do. The score cache is **30.6% of the database** in the harness and 25.5% in production (§2.1) and is *pure cache* —
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

### D7 — An orphan reaper for `article_entities`

The one change on this list that is not about 50,000 sources: it is about the box today. §2.12 —
catalogue retention deletes `feed_articles` rows and nothing ever deletes the `article_entities`
rows that pointed at them, and the count cap has been binding, so the leak is running.

`prune_orphan_event_locations` is the template, one table over and in the same step of the same
pass: a bounded `SELECT id … WHERE NOT EXISTS (…) LIMIT :batch` followed by a `DELETE … WHERE id IN
(…)`, with `ix_article_entities_canonical_url` giving the same covering-index plan.

Two properties to keep from the original: it must be **harmless when retention is off** (no pruned
articles means no orphans, and the query returns nothing), and it must run **after** the catalogue
prune, which is the thing that creates the orphans.

### D8 — `update.sh` prunes the build cache, with a policy that can actually reach it  ✅ **LANDED**

§2.13: `cd-deploy.sh` prunes after a successful deploy; `update.sh` does not, and `update.sh` is what
a manual deploy invokes. Put a prune in `update.sh`'s success path — **but not the one that is
there**. The measured result is the whole point: `--filter until=168h` freed 458 kB of 8 GB, because
it filters on last-accessed and BuildKit touches every record a build reuses.

Use a **size** bound instead — `--keep-storage ${CD_BUILD_CACHE_KEEP:-2GB}` — which evicts
least-recently-used records until the cache is under the limit, and therefore bounds it no matter how
often builds run. `cd-deploy.sh`'s own filter should change to match; leaving two different policies
in two deploy paths is how this was missed in the first place.

**Landed** as `prune_build_cache` in `deploy/ops/_compose.sh`, called from `update.sh`'s SUCCESS
path — inside the stage rather than as a new one, because `DEPLOY_STAGES` is a state machine about
whether the site is serving and pruning a cache is not part of that. The knob is
`DEPLOY_BUILD_CACHE_KEEP` (default `2GB`); `CD_BUILD_CACHE_KEEP_HOURS` is retired.

**Moved, not duplicated.** `cd-deploy.sh`'s own prune is gone: two policies in two deploy paths is
precisely how the manual path came to have none. Since cd-deploy calls update.sh, both paths now get
the same one.

Properties kept from the original: **non-fatal** (every branch returns 0 — housekeeping must not turn
a green deploy red) and **after** the smoke test, never before. One property added: **no silent
fallback**. If `--keep-storage` is unsupported the function says the cache is unbounded and stops,
rather than quietly running an unbounded `prune -f` and leaving the next build fully cold.

`tests/test_build_cache_prune.sh` drives it with a stubbed `docker`, asserting the flag *shape* —
a test that only checked "a prune runs" would have passed against the version that reclaimed 458 kB.
It is wired into pytest by `tests/test_ops_shell_suites.py`, which also picks up the pre-existing
`test_backup_formats.sh` that nothing had been running.

Immediate relief still needs no deploy — the same command by hand — but the code change is what
stops it coming back at ~500 MB per deploy.

### D9 — Backups stop leaving debris, and stop shipping it

§2.14. Three pieces, none of which changes a successful backup:

* **`backup_database`** — remove `tmp` when the copy does not complete. The `finally` block already
  closes both handles; it should unlink the temp file too, on any path that does not reach
  `os.replace`. This is the root fix: everything else below is cleaning up after it.
* **`prune-backups.sh`** — its glob is `-name '*.db' -o -name '*.db.gz'`, which matches neither
  `.db.tmp` nor `.db.gz.tmp` nor `.db.tmp-journal`. Add a sweep for `*.tmp*` older than a day, kept
  separate from the tiered policy so a temp file can never occupy a retention slot.
* **`backup-offhost.sh`** — `aws s3 sync` needs `--exclude '*.tmp*'`. A partial database copy should
  not reach the bucket even if the two fixes above regress.

The ordering matters and mirrors the ordering already inside `run_cleanup`: fix the producer first,
then the reaper, then the shipper — otherwise each fix is tested against debris the previous one was
still creating.

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
archive keeps a Tier B / shadow article.** At 50,000 sources and production's measured
**3,912 B/article** (§2.11):

```
250,000 articles/day × 3,912 B  =  978 MB/day
```

Volume required ≈ OS and images + database + 1 GB of WAL headroom + one transient uncompressed
snapshot + 23 tiered local backups at 7.8× compression. The transient column does double duty: it is
the uncompressed snapshot a backup writes before gzip *and* the free space a `VACUUM` needs (§2.9),
and the two never happen at the same moment.

**The fixed term is production's, not an estimate.** The box reports 22 GB used with 3.6 GB in
`/opt/ih/data` — so OS, Docker images and layers are **18.4 GB**, not the 10 GB this table first
assumed.

| horizon | catalogue rows | database | local backups | transient | **volume needed** | gp3 $/mo |
|---:|---:|---:|---:|---:|---:|---:|
| 7 days | 1.75 M | 6.8 GB | 20.2 GB | 6.8 GB | **53.2 GB** → 64 GB | $5.12 |
| 14 days | 3.5 M | 13.7 GB | 40.4 GB | 13.7 GB | **87.2 GB** → 100 GB | $8.00 |
| 30 days | 7.5 M | 29.3 GB | 86.4 GB | 29.3 GB | **164.4 GB** → 200 GB | $16.00 |
| 90 days | 22.5 M | 88.0 GB | 259.5 GB | 88.0 GB | **454.9 GB** → 500 GB | $40.00 |

**Disk is still cheap and still not the constraint** — a 30-day archive at 50,000 sources costs
**$16/month** of gp3 — but the correction is worth naming: at production's real constant and real OS
overhead, every row above is **30–40% larger** than the first version of this table said. What the
horizon really buys and spends is the two things above it: the retention pass (D1 makes it indexed,
so it stops being the limit) and the backup CPU (D5).

**And the box has no room to wait.** The volume is at **78% used with 6.3 GB free** today, at a
150,000-row catalogue. That is already past the 70% alert line A5 proposes, and every horizon in the
table needs a bigger volume before it can be chosen.

The table prices a single horizon applied to everything. Two refinements move it, both downward:

* **The score cache has its own horizon.** Of the 3,912 B/article, ~999 B is the `scored_articles`
  row (919.4 B × 1.087 rows per article) and ~2,913 B is everything else (§2.11). So
  `DB ≈ H_catalogue × 0.728 GB + H_cache × 0.250 GB` per day-of-horizon. A 30-day catalogue with a
  7-day score cache is **23.6 GB, not 29.3** — D3's env var alone.
* **Tier B and shadow can have different horizons**, which is exactly what
  `RWE_RETENTION_MAX_AGE_DAYS_TIER_B` / `_SHADOW` were built for in M2.

**Recommendation: 14 days for shadow, 30 for Tier B, 7 for the score cache, and Tier A on the
existing 83,000-row budget.** Shadow only has to outlive M8's evaluation window (~14 days); Tier B
carries the searchability contract; Tier A is already bounded by design. The resulting size depends
on how much of the 50,000 has been promoted out of shadow, which nobody knows yet — so it is a
**bracket, not a point**: from **11.9 GB** of database (everything still in shadow at 14 days) to
**23.6 GB** (everything promoted to Tier B at 30 days) — which by the same formula as the table is a
**78 GB** volume at the low end and **136 GB** at the high end. **Provision 150 GB** ($12/month) and
the horizon stays a product decision rather than a capacity one. It is priced here, not made here.

---

## 6 · What has already been fixed since `CAPACITY_AND_COST.md`

That document's two 🔴 findings are closed, and its text is now stale in three places. Recorded so
the next reader does not re-solve them:

| Finding, 2026-07-27 | State now |
|---|---|
| 🔴 "EBS 30 GiB exhausts in ~25 days from 48 hourly full-copy backups" | **Closed.** `BACKUP_KEEP=60` is now a runaway ceiling only; real retention is tiered in `deploy/ops/prune-backups.sh` (12h/7d/4w ≈ 23 files) and stops growing with time. |
| 🔴 "S3 backups grow without bound — no lifecycle rules" | **Closed.** `terraform/s3.tf` now has `aws_s3_bucket_lifecycle_configuration.ih_backups`: GLACIER_IR at 7 d, DEEP_ARCHIVE at 90 d, expiry at 365 d, noncurrent expiry at 30 d, abort-incomplete-multipart at 7 d. |
| "Backups are full **uncompressed** copies" (also in `STORAGE_LIFECYCLE.md` §3) | **Stale.** `store.backup_compression()` defaults **on**; measured **7.8–8.6×**. Both documents should be corrected. |
| 🟠 "Catalog retention is OFF" | **No longer true, and I got this wrong in the first draft.** Production runs `RWE_RETENTION_MAX_COUNT=150000` — recommendation #1 of that document was applied — and the catalogue is at **150,076 rows**, i.e. *just over*, which is the one place the cheap pre-gate stops applying. §2.10. |
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
| **A3** | **Retention / cleanup** | a steady-state pass that deletes nothing completes in ≤ **2,000 ms** with an RSS delta ≤ **150 MB**, at the design size | **89,765 ms / +5,582 MB at 1 M rows** ❌; production is estimated at **~8–9 s / ~840 MB per pass right now** (§2.10, unconfirmed on the box) — D1's replacement measures **5.4 ms** on the same data | D1, D2, D3, D4 |
| **A4** | **Backup / restore** | full backup + integrity + compress ≤ **15 min**, and ≤ **5%** of the 0.40 sustainable vCPU averaged over an hour; verified restore ≤ **30 min**; WAL forced by one backup ≤ **1 GB** | 15.0 s/GB → **4.7 min** at 18.7 GB ✅; **19.5% of vCPU** if hourly ❌; restore 19.9 s/GB → 6.2 min ✅; WAL 478 MB at 2.5 GB ✅, unmeasured at 18.7 GB ⚠ | D5 |
| **A5** | **Database size** | database ≤ **60%** of the volume, local backups ≤ **25%**, ≥ **15%** free at all times; alert at 70% used | **76% used, 6.3 GB free** ❌ — but the database is 2% and the backups 10%; **8.0 GB of it is reclaimable Docker build cache** (§2.13). One `docker builder prune` → 48% used, PASS | D8, then §5 sizing (150 GB) |
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
| 0 ✅ | **a size-bounded `docker builder prune`** (+ **D8**, landed) | frees up to **8.0 GB** and takes the volume from 76% to 48%; nothing else on this list matters if the disk fills first, and it is the only step with an effect today. Note §2.13: the *age*-bounded form freed 458 kB | lowest — build cache only, no images, no volumes |
| 1 ✅ | **D2** — `_host_match` becomes O(labels), **plus `tier_resolver()`** | D1's per-tier arm is worthless behind a 4.1 ms/article tier decision, and this is a pure-function change with a provable equivalence and a test that can be written to fail before it | lowest — one function, no schema, no config |
| 2 ✅ | **D3 indexes** (+ `RWE_RETENTION_SCORED_DAYS`, an operator decision) | additive, reversible, and it removes 117.6 ms of the 235.8 ms pass *before* the pass is rewritten, so the rewrite is measured against a clean baseline | low |
| 3 ✅ | **D1 stage 1** — the narrow projection | measured first: 84% of the pass was loading columns retention never reads. 7,687 → 2,356 ms and 880 → 179 MB, with **no change to any deletion decision** | low — proven by a differential over randomised catalogues, mutation-tested three ways |
| 3b | **D1 stage 2** — SQL-shaped retention for Tier B and shadow | what actually clears A3 at the design size; stage 1 does not | medium — a deletion path, so it needs the guard-flips discipline: mutate the predicate, prove the test fails |
| 4 | **D4** — `storage_stats` off the cleanup path | trivially safe once D1 lands, and 94.7 ms of a 235.8 ms pass | lowest |
| 5 | **D5 + D5b** — gzip level 3, backup interval, `journal_size_limit` | operational, no code beyond a level knob; do it before the volume grows, not after | low |
| 6 | **D7** (`article_entities` reaper) and **D9** (backup temp-file cleanup) | both demoted after measurement — 97 orphans and 46 MB, not the leaks I sized them as. Real defects, cheap fixes, no urgency | lowest — bounded deletes and three failure-path edits |
| 7 | **§5** — choose the horizons, resize the volume, raise the batch limit for the drain, plan the `VACUUM` | needs 1–5 in place, and it is the step that actually changes production data | the only one with a maintenance window — and the drain takes days at the default batch limit (§4 D3) |
| 8 | **D6** — tier lists out of the environment | binds at ~30 k sources; nothing before it needs it | medium, and deferrable |

**Steps 1–3 are all code changes that can be validated entirely by `storage_bench.py` against a
synthetic catalogue, with no production data and no source expansion** — which is the property that
makes them safe to do next.

---

## 9 · What to check on the box next

Three read-only checks. The first two turn §2.10 and §2.12 from estimates into measurements; the
third settles a question this audit could not.

**1 — Is the retention pass actually costing what §2.10 estimates?** The poller already logs it.

```bash
cd /opt/ih && source deploy/ops/_compose.sh
dc logs --since 6h api 2>/dev/null | grep -o '"cleanupMs": [0-9.]*' | tail -20
dc logs --since 6h api 2>/dev/null | grep '"event": "feed_retention"' | tail -5
```

`cleanupMs` is the whole `run_cleanup` pass, inside the lock. `feed_retention` carries `pruned`,
`kept` and `catalog`. If `pruned` is a double-digit number and `cleanupMs` is in the thousands, that
is the state §2.10 describes, measured rather than interpolated.

**2 — How many `article_entities` rows are already orphaned?**

```bash
dc exec -T api python -c "import sqlite3;c=sqlite3.connect('/app/data/ih_beta.db');\
print('entities', c.execute('select count(*) from article_entities').fetchone()[0]);\
print('orphans ', c.execute('select count(*) from article_entities e where not exists \
(select 1 from feed_articles f where f.canonical_url=e.canonical_url)').fetchone()[0])"
```

Read-only, and index-backed on both sides. A large orphan count is the direct evidence for §2.12.

**3 — What is in the backups directory?** *Answered: healthy, and the tiered retention is working.*

```
prune-backups: policy hourly=12 daily=7 weekly=4 monthly=0
prune-backups: 26 backup(s) -> kept 26, deleted 0, freed 0 MB
```

**26 `.db.gz`, zero uncompressed leftovers**, and the KEEP list reads exactly as the policy
intends — 1 newest + 12 hourly + 8 daily + 5 weekly. (Daily and weekly carry one slot more than
their `N` because an inclusive "last N days / weeks" window includes the current partial period.
Expected, not drift.) The `:23` off-host sync is shipping to S3 in the same log. **Nothing to fix
here** — and my earlier "92 entries could mean neither mechanism is running" was the right caution
and the wrong worry.

*One loose end, not worth a conclusion:* 92 directory entries − 26 databases = 66 non-database
files, where 26 backups × 2 sidecars would be 52. `prune-backups.sh` **does** delete sidecars with
their database (lines 85–93), so the 14 extra are unexplained rather than orphaned. One line says
what they are, and I would rather ask than guess a third time:

```bash
sudo sh -c "ls -1 /opt/ih/data/backups | sed 's/^ih_beta-[0-9TZ]*\\.//' | sort | uniq -c"
```

*Superseded detail, kept because it was the reason for the check:* the missing `du` line was the
root-owned-`0600` permissions trap `db_backup.py` documents.

```
4.0K  allowlist.txt        44K  score_reference.json     16M  feed_corpus.csv
6.7M  ih_beta.db-wal      554M  ih_beta.db             3.0G  backups
```

Backups are being taken (newest `ih_beta-20260827T114616Z.db.gz`) and the whole `/opt/ih/data`
tree accounts for itself: 3.0 + 0.581 + 0.016 + 0.007 = **3.60 GB**, exactly what `du` reported.

**The one number that is not yet answerable is the backup count**, and it is worth being careful
with. `ls -1 … | wc -l` returned **92 entries** — but a backup *set* is up to three files
(`.db.gz` plus the `allowlist.txt` and `score_reference.json` sidecars `db_backup.py` writes beside
it). So 92 entries is anywhere from **31 backups** (three files each) to **92** (one file each),
against a tiered target of ~23 and the AWS ceiling of `BACKUP_KEEP=48`. **31 is mildly over the
tiered target; 92 would mean neither retention mechanism is running.** Those are different
situations and the entry count cannot tell them apart:

```bash
sudo sh -c 'ls -1 /opt/ih/data/backups/*.db.gz | wc -l'      # the real backup count
sudo sh -c 'ls -1 /opt/ih/data/backups/*.db 2>/dev/null | wc -l'   # uncompressed leftovers
sudo tail -30 /var/log/ih-backup.log                          # is the :33 prune cron running?
```

**4 — Where is the other 18.4 GB?** *Answered, and it is the most actionable result in this
document — see §2.13.*

---

## 9b · A pattern in this document's own errors, worth naming once

Three times here I sized a defect by its **class** and the measurement came back an order of
magnitude smaller:

| claim | I implied | measured | over by |
|---|---:|---:|---:|
| `article_entities` orphan leak (§2.12) | the table's 42.8 MB | **97 rows, ~28 KB** | ~1,500× |
| the build-cache prune (§2.13) | frees 8.0 GB | **458.5 kB** | ~17,500× |
| backup temp debris (§2.14) | ~1 GB | **46 MB** | ~22× |

Each was a real defect and each remains on the list. But "there is no reaper, therefore rows leak
without bound" and "the cache is 100% reclaimable, therefore the prune reclaims it" are both
*mechanism* arguments standing in for *magnitude* arguments, and they were wrong three times running
in the same direction — always too large, always in a way that made the finding sound more urgent.

**This matters for how to read the claims here that are still unmeasured**, which are exactly two:

* the **~840 MB of RSS per retention pass** (§2.10). `cleanupMs` measured the time and beat my
  estimate; nothing has measured the memory, and by the pattern above it deserves scepticism in the
  other direction too — the OOM-at-730k projection rests on it.
* the **WAL forced by a backup at the design size** (§2.6, A4). Already flagged as extrapolated from
  a synthetic writer running thousands of times faster than production.

Everything else in §2 is a direct reading from the harness or from the box.

---

## 10 · What this milestone does not settle

* **Nothing is implemented.** This is the audit and the design. D1–D9 are proposals with measured
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
