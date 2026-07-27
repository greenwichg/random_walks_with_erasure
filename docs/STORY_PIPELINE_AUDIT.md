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

- **Feed ingestion** — 8/8 providers healthy, ~3,900 articles/day. (GDELT fails ~40% of cycles —
  later measured as HTTP 429 rate limiting, not the SSL timeouts claimed here; see
  docs/GDELT_RATE_LIMIT_INVESTIGATION.md. Either way it costs volume, not stories.)
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

**5. The poller warms the cache after each ingest** (`sources.MultiSourcePoller._post_cycle`, after
retention so the fingerprint is final).

> Note for anyone touching the poll cycle: the API starts **`sources.MultiSourcePoller`**
> (`api_fastapi.py:299`), *not* `feed_service.FeedPoller` — that one is the standalone CLI path.
> They have separate `_post_cycle` implementations and log different events (`source_poll` vs
> `feed_poll`). The warm was first added to the wrong one and silently never ran in production;
> `test_post_cycle_warms_the_story_cache` now pins it to the poller the API actually starts.
> `warm_cache` is single-flight because `MultiSourcePoller` runs one thread per adapter, and eight
> finishing together would otherwise launch eight concurrent multi-second clustering runs. The ingest invalidates the cache by definition, so without this the first
reader after every poll paid the full rebuild — 5.4 s measured, once per `RWE_POLL_INTERVAL`
(600 s), which on low traffic is a large share of requests. The work is unavoidable; paying it on
the thread that changed the catalog instead of on a reader's request is the point. Fail-soft: a warm
that cannot be built is a slow next request, never a broken poll loop.

### Measured effect

Production, after deploy:

| | Before | After |
|---|---:|---:|
| Stories | **89** | **750** |
| Cached read | — | 1.7 ms |
| Effective window | 12.5 h | 6.0 days |
| Non-UTC rows | 3,349 | 0 |

The clustering cost itself is unchanged (~5.4 s for the full 6-day window on this host); it now runs
on the poller's thread rather than a reader's request.

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

## Side effect worth knowing

Clustering the full window takes ~6 s and runs in the same Python process as the API, so during the
poller's first cycle after a restart the process is briefly GIL-bound. The post-deploy smoke test's
internal probes used a 5-second timeout and reported the resulting miss as
`analytics not 200 with the secret (no data yet is OK pre-traffic)` — misleading twice over: an empty
analytics table answers 200 (`product_analytics.funnel([])` is a valid result), and the endpoint
measured 0.09 s once idle. The timeout is now 20 s (`SMOKE_TIMEOUT`), and a probe that never
completes reports `EXC:<Type>` instead of collapsing to `000`, so a timeout and a refused connection
are distinguishable.

## Tokenisation gates (2026-07-27, after the geography audit)

The false merges the coherence audit found were a TOKENISATION failure, not a geography one.
Measured on the real pairs:

| pair | jaccard | shared | truth |
|---|---:|---:|---|
| "Local news in brief, July 21" / "…July 22" | **1.00** | 4 (all filler) | different |
| "This Day in Country History: July 22" / "…23" | **1.00** | 4 | different |
| "Trump wins Ohio" / "Trump wins Iowa" | 0.50 | **2** | different |
| "Berlin pride event canceled…" / "Vehicle drives into crowd at Berlin pride event" | 0.86 | **6** | same |

The ratio cannot separate rows 3 and 4 — 0.50 and 0.86 both clear a 0.28 threshold. **Shared-token
count can.** Two gates now apply before similarity is considered:

* **Stop-list** extended with months, weekdays and editorial filler (`news brief roundup recap
  today weekly best top …`), and bare numbers dropped. "Local news in brief, July 21" now reduces
  to `{local}` and cannot cluster at all — it previously merged 65 articles from 42 publishers.
* **`MIN_SHARED_TOKENS` (3)** — distinctive tokens two headlines must share, and
  **`MIN_TITLE_TOKENS` (3)** — below which a headline does not cluster. Both tunable without a
  deploy via `RWE_CLUSTER_MIN_SHARED` / `RWE_CLUSTER_MIN_TOKENS`, because the right value is an
  empirical question about the live headline mix, not one to settle on hand-picked examples.

`examples/audit_clustering_change.py` measures a candidate against the real catalog — story count,
largest cluster, and which clusters split — so thresholds are chosen from data. Recurring columns
with genuinely identical titles ("This Day in Country History") are NOT solved by tokenisation and
remain open: the words really are the same every day.

## Still open

- **Cluster geography coherence** (`examples/audit_story_geography.py`, added 2026-07-27) is now
  the sharpest false-merge detector we have, and it retired an earlier wrong call: the `(198, 105)`
  cluster described below as "almost certainly real" is *"Thune on Trump's Canada tariffs"*, whose
  members are located across CN CU DJ GB IL IR OM PH SA SG US YE. `publisherDiversity` scored it
  0.53 and called it healthy. Coherence does not. Run the audit before trusting a size-based
  judgement about any cluster.
- **The 94/2 cluster.** Production's largest cluster is 94 articles from **2 publishers** — a false
  merge, almost certainly one outlet emitting near-identical headlines (live blogs, market wraps).
  The stop-list already carries `live update updates`, so this isn't fully handled. It consumes ~5%
  of the window and presents as one bogus story. Diagnose against real data before choosing a
  cohesion rule; do not tune `sim` to compensate.
- **Retuning `sim = 0.28`.** Now that the window is correct the threshold may want revisiting — but
  tuning it against the old, truncated input would have been fitting to a broken signal.
