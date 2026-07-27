# Storage Lifecycle

How Hidden View keeps its database, its local backups, and its off-host copies bounded — without
ever deleting something a reader owns.

**The problem this solves:** at the measured ingestion rate (~5–6k articles/day) nothing in the
system had an upper bound. The database grew forever, 48 hourly *full uncompressed copies* of it
grew with it (a 48× multiplier that exhausts the 30 GiB volume in ~3 weeks), and `aws s3 sync`
without `--delete` into a bucket with no lifecycle retained **every backup ever taken** — a measured
trajectory of ~25 TB and ~$574/month by month twelve.

---

## 1 · What is kept forever, and what is pruned

The dividing line is ownership, not size. **Data the reader created is never subject to a retention
policy.** Everything else is derived, observational, or regenerable.

### Never pruned (`retention_policy.PROTECTED_TABLES`)

| Table | Why |
|---|---|
| `reads` | the reader's own reading history *and* every report's input |
| `saved_articles` | explicit user saves |
| `users`, `identities`, `onboarding`, `user_settings` | account and preferences |
| `api_tokens` | credentials |
| `rec_feedback` | explicit like/dislike/ignore |
| `improvement_lifecycle` | audit trail of suggested improvements |
| `feed_health` | one row per feed — naturally bounded, and the ops diagnostic |

A test (`test_protected_tables_are_declared_and_never_pruned`) asserts no `prune_*` method exists
for any of these, so adding one fails the build rather than shipping quietly.

### Pruned, with configurable policy

| Table | Default | Rationale |
|---|---|---|
| `feed_articles` | **off** (`MAX_COUNT` / `MAX_AGE_DAYS`) | validation-aware: `corpus_health` floors guarantee the serving corpus survives any prune |
| `article_event_locations` | cascades | **orphan reaper** — catalog retention deletes articles only, so geography rows used to be stranded forever |
| `scored_articles` | 30 days | pure cache; `score_with_cache` re-derives deterministically |
| `analytics_events` | 180 days | funnel telemetry needs a window, not history |
| `rec_events` | 365 days | Open-Mindedness reads these — a full year keeps every live metric intact |
| `report_snapshots` | 500/user | the analytics trend series; beyond the cap draws no visible chart |
| `notifications` | 200/user | settled history only — **unseen rows are never pruned** |

Everything is `0 = keep forever`, uniformly. A junk or negative value **falls back to the default**
rather than deleting more: the failure mode of a bad config must be keeping too much.

---

## 2 · The cleanup job

`examples/storage_lifecycle.py::run_cleanup` runs one pass, post-cycle, from both pollers — the
same seam catalog retention already used.

* **Incremental** — every prune is capped at `RWE_RETENTION_BATCH_LIMIT` (default 5,000) rows per
  table per pass, so the single SQLite write lock is never held long enough to stall ingestion. A
  backlog drains over successive cycles.
* **Ordered** — catalog first (it is what creates orphans), then the event-location reaper, then the
  rest. The reverse order would strand a cycle's worth of orphans every time.
* **Fail-soft** — one table's failure is recorded in `errors` and the pass continues; a cleanup job
  must never take down ingestion.
* **Idempotent** — a pass with nothing to do costs a few indexed counts.

```bash
python examples/storage_lifecycle.py            # one pass, prints JSON
python examples/storage_lifecycle.py --stats    # sizes only, prune nothing
```

---

## 3 · Backups: tiered, not "newest N"

Every backup is a **full, uncompressed copy** of the database, so a flat `BACKUP_KEEP=48` costs 48×
the DB size. `deploy/ops/prune-backups.sh` replaces it with grandfather-father-son retention:

| Tier | Default | Buys you |
|---|---|---|
| `BACKUP_KEEP_HOURLY` | 12 | undo the last few hours |
| `BACKUP_KEEP_DAILY` | 7 | undo yesterday / last week |
| `BACKUP_KEEP_WEEKLY` | 4 | undo last month |
| `BACKUP_KEEP_MONTHLY` | 0 | *depth lives in S3, not on the volume* |

**~22 files instead of 48**, and — the real change — a count that stops growing with time. Verified
against 226 simulated backups spanning six months: 226 → 22, correct tier distribution.

Safety: the newest backup is **always** kept regardless of policy; a file is deleted only after a
keeper exists for its bucket; an unparseable filename is kept, never deleted; `--dry-run` prints
decisions without touching anything. Installed by `bootstrap-ec2.sh` as an hourly cron at **:33** —
ten minutes after the **:23** off-host sync, so a copy is safely in S3 before anything leaves disk.

The compose `backup-scheduler` keeps `BACKUP_KEEP=60` purely as a **runaway ceiling** (it must stay
above the tiered footprint, or a flat prune would delete the weekly slots) — it fires only if the
host cron stops running.

---

## 4 · S3 lifecycle

`terraform/s3.tf` now carries `aws_s3_bucket_lifecycle_configuration`:

| Age | Action |
|---|---|
| 7 days | → Glacier Instant Retrieval (millisecond restores, ~⅕ the price) |
| 90 days | → Deep Archive |
| 365 days | expire |
| noncurrent 30 days | expire (versioning is on; otherwise overwrites accumulate invisibly) |
| incomplete multipart 7 days | abort |

Apply with the MFA operator flow: `cd terraform && source ./assume.sh && terraform apply -target=aws_s3_bucket_lifecycle_configuration.ih_backups`.

---

## 5 · Monitoring

`monitor.sh` (the existing 5-minute cron) now also alerts via `ALERT_WEBHOOK` on:

| Check | Default threshold | Env |
|---|---|---|
| Disk used on the data volume | 75% | `DISK_WARN_PCT` |
| Database size | 2,000 MB | `DB_WARN_MB` |
| Backup directory size | 10,000 MB | `BACKUPS_WARN_MB` |

Every run prints `monitor: storage disk=…% db=…MB backups=…MB/N files`, so the trend is visible in
`/var/log/ih-monitor.log` even when nothing is wrong.

---

## 6 · Expected growth

Measured constants: **3,078 bytes/article**, **~5–6k articles/day**.

### Without a catalog cap (`RWE_RETENTION_MAX_COUNT=0`, today's setting)

| Horizon | Articles | DB | Local backups (22×) | S3 (with lifecycle) |
|---|---:|---:|---:|---:|
| 1 month | ~160k | 0.5 GB | 11 GB | ~30 GB |
| 3 months | ~465k | 1.4 GB | 31 GB ⚠️ | ~90 GB |
| 12 months | ~1.8M | 5.6 GB | 123 GB ✗ | ~200 GB |

### With the recommended cap (`RWE_RETENTION_MAX_COUNT=150000`)

| Horizon | Articles | DB | Local backups | S3 | Monthly cost |
|---|---:|---:|---:|---:|---:|
| any | 150k (steady) | **0.46 GB** | **~10 GB** | **~40 GB** | **~$1–3** |

Everything flattens. The recommended production settings:

```bash
RWE_RETENTION_MAX_COUNT=150000     # ≈ 30 days of ingestion; DB caps at ~460 MB
# defaults are fine for the rest (scored 30d, analytics 180d, rec-events 365d,
# snapshots 500/user, notifications 200/user, batch 5000)
```

**Note on the local figure:** 22 uncompressed copies of a 460 MB database is still ~10 GB — the
dominant consumer on a 30 GiB volume. The next lever, if that becomes tight, is compressing backups
(SQLite text data typically gzips 4–6×, taking ~10 GB to ~2 GB). That changes the restore path, so
it is deliberately *not* bundled here — it is the documented next step, not a silent default.

---

## 7 · What this deliberately does **not** do

* **No user data is ever deleted.** Account deletion remains a separate, explicit user action.
* **No compression yet** (see above).
* **No `VACUUM`.** SQLite does not return freed pages to the filesystem without one; after a large
  first prune the file stays its high-water size until a manual `VACUUM`. Deliberate: `VACUUM`
  rewrites the whole database and takes an exclusive lock, which is not something a background job
  should do unattended. Run it during a maintenance window if the file size matters after a big
  prune.
* **Catalog retention stays OFF by default.** Turning it on is an operator decision with a real
  product consequence (older articles leave Search and Stories), so it ships opt-in.
