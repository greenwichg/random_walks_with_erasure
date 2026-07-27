# Capacity & Cost Analysis (2026-07-27)

Measured, not estimated, where measurement was possible: bytes-per-article and CPU-per-article come
from running the real ingestion pipeline; ingestion rate comes from two production catalog counts;
infrastructure comes from `terraform/` and the live compose/ops configuration.

## Headline

**Compute is not the constraint — storage retention is, and one setting is on a ~3.5-week fuse.**

| Finding | Severity |
|---|---|
| **EBS 30 GiB exhausts in ~25 days** — not from the database (490 MB) but from **48 hourly full-copy backups** (`BACKUP_KEEP=48` × the whole DB each time) | 🔴 urgent |
| **S3 backups grow without bound** — `aws s3 sync` without `--delete` + **no lifecycle rules on the bucket** ⇒ every hourly backup ever taken is retained forever: ~$574/month by month 12 | 🔴 high |
| **Catalog retention is OFF** (`RWE_RETENTION_MAX_COUNT=0`) while GDELT + 7 providers ingest ~5k articles/day | 🟠 root cause of both |
| CPU: 2.91 ms/article ⇒ **0.02% of one vCPU** at current rates | 🟢 non-issue |
| Memory: ingest peak RSS 168 MB on a 4 GiB box | 🟢 non-issue |
| Network: ingestion is inbound (free); egress is HTML/JSON only (images hotlink to publishers) | 🟢 ~$0 |

## Measured constants

| Quantity | Value | How obtained |
|---|---|---|
| Bytes per article | **3,078 B** | ingested 2,000 realistic articles through `ingest_entries` incl. event locations, measured file delta |
| CPU per article | **2.91 ms** | `time.process_time()` over a 1,000-article batch (scoring + dedup + persist) |
| Ingestion rate | **~5,000 net-new/day** (range 3,500–7,000) | catalog 8,470 → 10,032 over 5.27 h, minus first-cycle enablement burst; cross-checked against per-provider cycle math |
| Catalog today | 10,032 articles ⇒ ~31 MB | production probe, 2026-07-27 01:05 UTC |

Per-provider daily contribution at current settings: NewsData ~960, GNews ~960, Currents ~960,
RSS+GDELT ~2,000, MediaStack ~290 (3 cycles/day), Google News ~100, Guardian/NewsAPI ~0 net (their
output is already covered — arriving as duplicates, which is dedup working, not waste).

## Projections (current configuration, no changes)

| Horizon | Articles | DB | Local backups (48×) | EBS used¹ | S3 cumulative | S3 $/mo |
|---|---:|---:|---:|---:|---:|---:|
| now | 10,032 | 0.03 GB | 1.5 GB | 11.5 GB | ~0 | $0 |
| 1 month | 160,032 | 0.49 GB | 23.6 GB | **34.1 GB ✗** | 194 GB | $4 |
| 3 months | 465,032 | 1.43 GB | 68.7 GB | 80.1 GB | 1.6 TB | $37 |
| 6 months | 920,032 | 2.83 GB | 135.9 GB | 148.8 GB | 6.3 TB | $145 |
| 12 months | 1,835,032 | 5.65 GB | 271.1 GB | 286.8 GB | **24.9 TB** | **$574** |

¹ assumes ~10 GB for OS + Docker images/layers; verify with `df -h /` and `du -sh /opt/ih/data/*`.

**The 48× multiplier is the whole story.** SQLite backups are full file copies (no compression, no
`VACUUM INTO`), taken hourly by the `backup-scheduler` container and shipped hourly to S3 by the
`:23` cron. The database itself stays modest for years; the *copies* of it do not.

### Total monthly bill

| Line item | now | 1 mo | 3 mo | 6 mo | 12 mo |
|---|---:|---:|---:|---:|---:|
| EC2 t3.medium (on-demand) | $30 | $30 | $30 | $30 | $30 |
| EBS gp3 (resized to fit) | $2 | $8 (100 GB) | $16 (200 GB) | $24 (300 GB) | $40 (500 GB) |
| Elastic IP + Route 53 | $4 | $4 | $4 | $4 | $4 |
| S3 backups | $0 | $4 | $37 | $145 | $574 |
| CloudWatch | $2 | $2 | $2 | $3 | $3 |
| Data transfer | $0 | $0 | $0 | $0 | $1 |
| **Total** | **~$38** | **~$48** | **~$89** | **~$206** | **~$652** |

**With the three fixes below: ~$45–50/month, flat, indefinitely.** A 13× difference at 12 months,
bought with configuration changes only — no new infrastructure.

## Recommended fixes (config-only, in priority order)

1. **Cap the catalog** — in `deploy/.env`: `RWE_RETENTION_MAX_COUNT=150000` (≈ 30 days of
   ingestion, ~460 MB). The `gdelt-bounded-catalog` deployment rule already *requires this knob to
   be wired* precisely because GDELT is unbounded; it is wired but set to 0 (off). Retention runs
   post-cycle via `corpus_health`, validation-aware.
2. **Stop hoarding full copies locally** — `BACKUP_KEEP=12` (12 hourly ⇒ ~5.5 GB at the capped DB
   size) and consider `BACKUP_INTERVAL=14400` (4-hourly) — for a beta with a handful of users,
   hourly full copies of a 460 MB file buy little and cost 11 GB/day of disk writes.
3. **Add S3 lifecycle rules** (the bucket currently has none — `terraform/s3.tf` documents their
   absence): transition to Glacier Instant Retrieval after 7 days, expire after 30–90 days, and add
   `NoncurrentVersionExpiration` since versioning is enabled. Steady state then: ~$3–8/month
   forever instead of unbounded growth.

Post-fix steady state: DB ~0.46 GB, local backups ~5.5 GB, **EBS ~16 GB of 30 GB (53%)**, S3 ~330 GB
under a 30-day window. All flat — the system stops accumulating.

Optional, cheap, later: compress backups (`gzip` typically 4–6× on SQLite text-heavy data), and
switch the instance to `t4g.medium` (Graviton, ~$24/mo, ~20% cheaper) at the next rebuild.

## Bottlenecks, in the order they will actually bite

| # | Bottleneck | When | Signal to watch | Action |
|---|---|---|---|---|
| 1 | **EBS capacity** (backup copies) | **~3.5 weeks** without fixes | `df -h /` > 70% | fixes 1–3; gp3 also grows online without downtime |
| 2 | **S3 spend** | month 3+ | monthly bill line | lifecycle rules |
| 3 | Corpus-rebuild CPU/latency | 6–12 months at 1M+ rows | poll-cycle wall time in api logs | retention cap makes this moot |
| 4 | t3 CPU-credit surcharge (`cpu_credits = "unlimited"` — burst is *billed*, not throttled) | 12+ months or a user-traffic step change | CloudWatch `CPUUtilization` sustained > 40% | resize to t3.large / t4g.large |
| 5 | Memory (4 GiB) | user growth, not ingestion | persistent swap use | resize |
| 6 | SQLite write concurrency | not on this trajectory | p95 write latency | see below |

## When to upgrade what

**Larger EC2 instance — not needed for 12+ months on ingestion alone.** Ingestion consumes 0.02% of
a vCPU and 168 MB. Resize when *user* traffic (SSR + recommendation serving) pushes sustained CPU
past 40% or memory into swap — both visible in CloudWatch before they hurt.

**Postgres — not justified by data volume, now or at 12 months.** Even unbounded, the DB reaches
5.6 GB in a year, well inside SQLite's comfortable range, and the write path is a single-writer
ingest of ~5k rows/day (trivial). The real triggers for Postgres are *architectural*: (a) you want a
second host (SQLite on a bind-mount can't be shared), (b) you want zero-downtime deploys, or (c)
backup-by-file-copy becomes operationally awkward — which the retention cap removes. Revisit if you
adopt the multi-host path, not before.

**Multi-host — a product decision, not a capacity one.** Today a deploy is a converge-in-place with
a brief interruption, and a host failure is a restore-from-backup (RTO minutes, RPO ≤ 1 hour). If
the beta graduates to something where minutes of downtime matter, that's the moment for
host #2 + Postgres (or Litestream) + a load balancer — roughly +$50–70/month, and it is the only
change on this list that requires real engineering rather than configuration.

## Verification commands (host)

```bash
df -h /                                    # EBS headroom — the number to watch weekly
du -sh /opt/ih/data /opt/ih/data/backups   # DB vs backup-copy split
ls -1 /opt/ih/data/backups/*.db | wc -l    # should be BACKUP_KEEP
aws s3 ls s3://hidden-view-ih-backups-652615011843/backups/ --recursive --summarize | tail -3
```
