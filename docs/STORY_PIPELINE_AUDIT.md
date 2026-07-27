# Story Pipeline — Audit and Fix (2026-07-27)

## Symptom

The catalog held 12,790 articles across 8 healthy providers, and the Stories browser showed **89
stories**. The count had *fallen* as providers were added.

## Root cause

Story clustering never saw the catalog. `story_service._fetch` took the newest **2,000 rows**, and
`/api/stories` never overrode that default. The cap was denominated in **articles, not time**:

| | Articles/day | 2,000 articles covers |
|---|---:|---:|
| Before the six providers | ~1,200 | ~40 hours |
| After | **~3,900** (measured) | **~12.5 hours** (measured) |

Against `clustering.DEFAULT_WINDOW_DAYS = 6.0` — 144 hours. The similarity threshold was being fed a
window **11.5× narrower** than it pairs over.

Stories require ≥2 articles from ≥2 distinct publishers, and cross-publisher co-coverage accumulates
over a day or more. Truncating to 12.5 hours cut most co-coverage sets before `min_publishers` could
be satisfied. **Every provider added made it worse**, because more articles per day meant fewer hours
inside a fixed row count.

The cap existed for a reason: `clustering.cluster()` was all-pairs **O(n²)**, re-run on every
request with no cache. Measured on the production host:

| max_scan | Stories | Time |
|---:|---:|---:|
| 2,000 (shipped) | 89 | 1.2 s |
| 4,000 | 181 | 4.0 s |
| 6,000 | 324 | 8.9 s |
| 12,790 (full) | ~700–800 (extrapolated) | ~40 s |

So the cap was a latency budget, and raising it alone would have traded 89 stories for a 40-second
page. The fix had to be algorithmic first.

### A second defect, found while probing

`store._search_order` sorts `published_at` — a nullable **TEXT** column — lexicographically, and
`rss_ingest._to_iso` preserved each source's UTC offset. So:

```
2026-07-27T12:00:00-04:00   (16:00Z)  sorted BELOW
2026-07-27T16:00:00+00:00   (16:00Z)
```

Identical instant, but the `-04:00` row ranked lower and was dropped from the newest-first window
first. Measured: **2,657 articles (21% of the catalog)** carried `-04:00` — in July that is New York
and Washington, i.e. most of the outlets carrying the political spectrum. A further ~690 rows were
stored naive (no offset at all). The window wasn't just too narrow; it was **biased against the
publishers the product exists to compare**.

## What stages were NOT the problem

Ruled out with live data, so they aren't re-investigated next time:

- **Feed ingestion** — 8/8 providers healthy, ~3,900 articles/day. (GDELT runs ~40% SSL timeouts;
  that costs volume, not stories.)
- **Retention / cleanup** — `pruned: 0, kept: 12237`. Retention has never deleted an article. Stories
  are derived per request and never stored, so nothing deletes them either.
- **Freshness filtering** — `/api/stories` applies no date bound. The `fresh: 7216` figure in the
  logs is `corpus_health`'s *recommendation-candidate* freshness, a different pipeline.

## The fix

**1. Exact blocking in `clustering.cluster()`.** Candidates come from an inverted token index
instead of all pairs. This is exact, not approximate: `jaccard(a,b) ≥ sim > 0` requires
`|a ∩ b| ≥ 1`, so a pair sharing no token can never match. Verified against the previous all-pairs
implementation (retained in `tests/test_clustering.py` as the oracle) over randomised corpora and the
degenerate hub-token case — **byte-identical clusters, ~17× faster**.

**2. `_fetch` bounds by time, not row count.** `date_from` defaults to `now − RWE_STORIES_SCAN_DAYS`
(default 6.0, matching the clustering window). A caller-supplied `date_from` still wins.
`RWE_STORIES_MAX_SCAN` (default 60,000) remains as a memory backstop — explicitly *not* the
relevance rule.

**3. A clustered-build cache** with two independent invalidations: a catalog fingerprint
`(row count, newest fetched_at)` in the key, plus a TTL (`RWE_STORIES_CACHE_TTL`, default 120 s).
Filters, sort and pagination stay outside the cache, so every filter combination is served from one
build. `get_story` shares it — a detail page costs no extra clustering and cannot 404 a link the list
just rendered.

> The fingerprint is a pair, not a count, deliberately. Deleting N rows and inserting N others leaves
> a count unchanged while the content differs entirely — exactly what a retention prune plus an
> ingest in the same interval does. That bug was caught by the suite and is now pinned by
> `test_cache_invalidates_when_content_changes_at_constant_row_count`.

**4. `published_at` normalised to UTC** in `rss_ingest._to_iso` (every adapter routes through it), so
lexicographic and chronological order coincide. `examples/backfill_published_at.py` converts existing
rows — idempotent, `--dry-run` first, leaves unparseable and NULL values untouched.

### Measured effect

On a synthetic 3,556-article / 900-event / 6-day corpus:

```
old 2000-row cap : 511 stories
new time window  : 898 stories      cold 0.35 s   cached 1.1 ms
```

## Deploying it

Deploy first — the script is baked into the image at build time, so it does not exist in a
container built from an earlier commit:

```bash
bash deploy/ops/cd-deploy.sh <sha>                                              # snapshots, then deploys
docker exec -i deploy-api-1 python examples/backfill_published_at.py --dry-run  # inspect
docker exec -i deploy-api-1 python examples/backfill_published_at.py            # apply
```

No env changes are required — the defaults are the intended production values.

**Backups are automatic at every layer here; none of it is an operator step.** `cd-deploy.sh`
snapshots before any code moves and aborts if that fails. The migration takes its OWN
integrity-checked snapshot through the same `store.create_backup` path before its first write, and
refuses to start if it cannot — so the pre-migration state is always recoverable, independent of
where the hourly cron happens to fall. The snapshot lands in the normal backups directory, so the
usual off-host S3 sync and tiered pruning pick it up with no special handling. Re-running an
already-migrated database takes no snapshot and writes nothing.

## Still open

- **The 94/2 cluster.** Production's largest cluster is 94 articles from **2 publishers** — a false
  merge, almost certainly one outlet emitting near-identical headlines (live blogs, market wraps).
  The stop-list already carries `live update updates`, so this isn't fully handled. It consumes ~5%
  of the window and presents as one bogus story. Diagnose against real data before choosing a
  cohesion rule; do not tune `sim` to compensate.
- **Retuning `sim = 0.28`.** Now that the window is correct the threshold may want revisiting — but
  tuning it against the old, truncated input would have been fitting to a broken signal.
