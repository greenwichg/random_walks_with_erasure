# Recommendation latency — stage-by-stage investigation

**Scope:** `POST /api/me/reads` → `GET /api/recommendations` returning, for a signed-in reader.
**Status:** investigation complete; the recommended fix is **implemented** — see
"Implemented" at the end.
**Date:** 2026-08-02. Production measurements added the same day — **they supersede the local
findings below wherever they disagree, and they disagree on the headline.** See
"Measured in production".

The question behind this: after reading a Discovery article, the new recommendation takes long
enough to appear that it reads as broken. Before changing anything, find out *which stage* the
time is actually in.

---

## What was added (and what was deliberately not)

Stage timers only. Every one is an `obs_metrics` observation wrapped in its own `try/except`, so a
metrics backend that throws cannot cost a reader their feed — asserted by
`test_a_failing_metrics_backend_never_costs_a_reader_their_feed`, which monkeypatches `observe`,
`incr` *and* the logger to raise and still requires a byte-identical feed.

| Series | Where | Answers |
|---|---|---|
| `read_score_ms`, `read_persist_ms` | `api_fastapi.add_reads` | what the read POST costs, split into scoring and the row write |
| `rec_cache_key_ms` | `personalize._model` | the two store reads that decide hit vs miss, paid on **every** request |
| `rec_model_cache_hit_total` / `_miss_total` | `personalize._model` | the cold/warm split, counted rather than inferred |
| `rec_build_*_ms` (9 sub-stages) | `personalize._build_model` | where a model rebuild actually goes |
| `rec_serve_model_ms`, `rec_serve_rank_serialize_ms`, `rec_serve_story_slot_ms` | `personalize.recommendations` | the three top-level serve stages |
| `rec_slot_index_ms`, `rec_slot_reads_ms`, `rec_slot_candidates_ms`, `rec_slot_context_ms`, `rec_slot_resolve_types_ms` | `personalize._apply_story_slot` | inside the Story-Match slot |
| `rec_story_index_hit_total` / `_miss_total`, `rec_story_index_build_ms`, `rec_story_index_view_ms` | `evidence_resolver.story_index` | index cache behaviour, and how much of a miss is the story view |
| `story_default_view_peek_hit_total` / `_inline_build_total`, `story_default_view_inline_build_ms` | `story_service.default_story_view` | **the branch that decides everything** — see below |
| `rec_handler_recommend_ms`, `rec_handler_media_ms`, `rec_handler_explanations_ms`, `rec_handler_record_shown_ms` | `api_fastapi` recommendations handler | the handler's own post-passes, which a breakdown stopping at the recommender would misattribute |

Plus one structured line per build/serve/slot (`rec_model_build`, `rec_serve`, `rec_story_slot`,
`rec_handler_stages`) so a single request can be read end to end instead of reconstructed from
percentiles.

### One instrumentation bug found before it could mislead anyone

The first version logged through a bare `logging.getLogger("ih.personalize")`. Measured under
uvicorn's default logging config: **effective level `WARNING`, zero reachable handlers** — every
stage line would have been silently dropped in production while passing every test, because pytest
configures the root logger and uvicorn does not. `ih.personalize` now owns its handler and level
exactly as `ih.api` does, and `test_stage_lines_are_reachable_without_a_configured_root_logger`
locks that in. Instrumentation whose failure mode is silence is worse than none: it gets quoted.

---

## Measurements

Local box, 2,000-article catalog, reader with 88 reads, through the **real FastAPI app**
(`TestClient`) — so handler post-passes are included, not just the recommender. Production
constants will differ (50,899 articles); the point of these runs is the *shape* and the
*controls*, and the production probe is `deploy/ops/rec-latency-probe.sh`.

### End-to-end, with the Story Slot on and off

| | slot ON | slot OFF (control) |
|---|---:|---:|
| `POST /api/me/reads` | 11.0 ms | 7.8 ms |
| `GET /api/recommendations` — **cold** | 816.1 ms | 678.1 ms |
| `GET /api/recommendations` — **warm** | 215.1 ms | 33.3 ms |
| read → feed visible (the measured flow) | **264.9 ms** | **88.8 ms** |

### Cold serve, slot ON (816.1 ms)

| stage | ms | share |
|---|---:|---:|
| `rec_story_index_build` (inside `slot_index`) | 577.1 | **71 %** |
| `rec_slot_candidates` | 128.1 | 16 % |
| `rec_serve_model` (the whole model rebuild) | 49.8 | 6 % |
| ↳ of which `rec_build_population` | 41.9 | 5 % |
| `rec_handler_explanations` | 16.4 | 2 % |
| `rec_handler_record_shown` | 10.2 | 1 % |
| `rec_serve_rank_serialize` | 4.5 | 0.6 % |
| `rec_build_persist_report` | 3.1 | 0.4 % |
| everything else (11 stages) | < 3 each | — |

### Warm serve, slot ON (215.1 ms)

| stage | ms | share |
|---|---:|---:|
| `rec_slot_candidates` | 142.0 | **66 %** |
| `rec_handler_explanations` | 20.9 | 10 % |
| `rec_slot_resolve_types` | 12.2 | 6 % |
| `rec_handler_record_shown` | 11.6 | 5 % |
| `rec_slot_context` | 7.2 | 3 % |
| `rec_serve_rank_serialize` | 5.3 | 2 % |
| `rec_serve_model` (cache hit) | 1.5 | 0.7 % |
| `rec_slot_index` (index cache hit) | 0.6 | 0.3 % |

### Read persistence is not the problem

`read_score_ms` 2.3 ms + `read_persist_ms` 1.6 ms; the whole POST including HTTP is 11.0 ms. In
production it was measured at 38.4 ms. Under 5 % of the wait, either way.

---

## The finding

### The dominant cold stage is a full story clustering on the request thread

`evidence_resolver.story_index()` reads `story_service.default_story_view()`, which has two
branches with a **60× cost difference**, and the code comment above it asserted the cheap one
("reads the build the poller already warmed") without measuring it:

| branch | local cost @2k articles |
|---|---:|
| peek hits the poller-warmed build | **1.0 ms** |
| peek misses → read-only **inline** `build_stories` on the request thread | **601.9 ms** |

Consequently `story_index()` on a cache miss costs **10.2 ms** with the view warm and **620.4 ms**
with it cold. That single branch is the entire cold-path story: 577 of 816 ms.

Two things make the miss common rather than rare:

1. **The index cache key is `(count_feed_articles, time // 60)`** (`evidence_resolver:141`). Under
   continuous ingestion (`RWE_FEED_POLL=1`) the row count moves whenever a single article lands,
   so the index misses far more often than its 60-second TTL suggests.
2. **Every miss re-consults the story view**, and if the view's own peek misses (expired TTL,
   fingerprint moved since the last warm), the reader pays a full clustering inline.

The production build was measured at **3,681 ms at 21.9k articles** (`docs/PERFORMANCE.md`,
2026-07-29) and the catalog is now 50,899. The production cold feed measured **3,374.8 ms**. One
inline story build accounts for essentially all of it.

### The dominant warm stage is the Story Slot's reader×coverage join

`rec_slot_candidates` — the nested loop in `_apply_story_slot` over the reader's read URLs × each
matched story's full `coverage` list, canonicalising every member URL — costs 142 ms warm and is
**66 %** of a warm serve. It is on the critical path of every served feed and the model cache does
nothing for it: the control run with `RWE_STORY_SLOT=0` serves warm in 33.3 ms.

### Eliminated hypotheses

| hypothesis | verdict | evidence |
|---|---|---|
| **The model rebuild dominates** | **refuted** | `rec_serve_model` is 49.8 ms of a 816.1 ms cold serve (6 %). Its own breakdown is flat — `build_population` 41.9 ms, everything else under 5 ms. |
| Read persistence is slow | refuted | 3.9 ms of engine work; 11.0 ms including HTTP |
| Report persistence is the hidden cost | refuted | `rec_build_persist_report` 3.1 ms — the "cheap next to the compute above" comment was unmeasured but correct |
| Ranking / serialisation is slow | refuted | `rec_serve_rank_serialize` 4.5 ms cold, 5.3 ms warm |
| The cache key check is expensive | refuted | `rec_cache_key` ≈ 2 ms across both store reads |
| `record_recommendations_shown` (a SELECT+upsert per card) dominates | refuted as *dominant*, real as a cost | 10–12 ms, 5 % of a warm serve |
| Media/logo enrichment | refuted | `rec_handler_media` 1.5–2.9 ms |
| The model cache thrashes because surfacing recs bumps the reception key | **not observed** | `record_recommendations_shown` is idempotent per `(user, article)` and `_reception_key` returns `(0,0)` until Open-Mindedness is active. Counters `rec_model_cache_hit_total` / `_miss_total` now measure this directly in production. |

### A correction to my own earlier inference

After the P1–P3 verification I reported production FEED cold 3,374.8 ms / warm 758.1 ms and
inferred "⇒ model rebuild ≈ 2,617 ms" by subtraction. **That attribution was wrong.** The same
subtraction locally gives 601 ms, of which the model rebuild is 49.8 ms and the story-view inline
build is 577 ms. Cold − warm measures *everything* that only happens on a miss, and the model
rebuild is the small part of it. This is precisely the class of code-derived claim the
instrumentation exists to replace.

---

## Recommended smallest, highest-confidence optimisation

**Do not let a request thread build the story view.** Concretely: make
`story_service.default_story_view` return `[]`/last-known rather than clustering inline when the
peek misses on a request path, and let the existing background refresh (already built, already
single-flighted via `_REFRESH_PENDING`) produce the next one — the same serve-stale discipline
`list_stories` already has.

Why this one:

* it targets **71 %** of the cold path with a single, already-existing mechanism;
* it is a *removal* of work on the request thread, not a new cache to invalidate;
* the degradation is graceful and already specified — a serve with no story index simply produces
  no `story_match` explanations and no slot card, which is exactly what happens today whenever the
  index is empty;
* the one contract it must not break is `/api/analyze`'s "writes nothing anywhere"
  (`test_analysis_writes_nothing_anywhere`), which the inline branch exists to satisfy — so the
  change must keep a *read-only* path for that caller rather than removing the branch outright.

Second, if the warm path still reads slow after that: **hoist `rec_slot_candidates` out of the
per-request path** (66 % of a warm serve) — the reader's read-URL × coverage join changes only
when their reads or the story index change, both of which are already versioned.

Both are follow-on work. **Neither is implemented.**

---

## Confirming this in production

```bash
sudo bash /opt/ih/deploy/ops/rec-latency-probe.sh --email you@example.com   # full flow
sudo bash /opt/ih/deploy/ops/rec-latency-probe.sh --warm-only               # no write
```

Section `[5]` is the one that settles it: if `story_default_view_inline_build_total` is non-zero,
readers are paying full clusterings on request threads and the recommendation above applies
directly. If it is zero, the cold cost is somewhere else and this report's ordering must be
re-derived from the probe's own table rather than from these local constants.

The probe records one read for the chosen reader (the flow under measurement begins with a read,
and the model cache key *is* the read count — a rebuild cannot be forced any other way). It says
so before it does it. Everything else is read-only.

---

# Measured in production (2026-08-02, `c7034d3`)

Probe run on the live box: catalog **51,733** articles, reader uid=1 with **93 reads**,
`RWE_STORY_SLOT=1`, Open-Mindedness **active** for this reader (that last fact turns out to be
the story). These numbers supersede the local ones above wherever they disagree.

| measured | value |
|---|---:|
| `POST /api/me/reads` | 379.1 ms (persist 233.1, score 22.7) |
| first `GET /api/recommendations` after the read | **6,685.0 ms** |
| next `GET` (expected warm) | 725.4 ms |
| read → recommendations visible | **7,064.0 ms** |
| three warm GETs, no read in between | 730.5 / **1,376.0** / 554.3 ms |

## The local headline hypothesis is refuted in production

`[5]`: `peek hits: 1, inline builds: 0`, one stale serve with a background refresh spawned,
story-index build 103.7 ms once, hits ~2 ms. **The serve-stale machinery works; nobody paid an
inline clustering.** The story view is not production's problem, and the "smallest fix" proposed
from the local bench does not apply. The slot's candidate join, 66 % of a local warm serve, is
**4.1 ms** in production. Local constants pointed at the wrong stage twice; the probe existing is
what caught it.

## What production actually shows — three compounding causes

### 1. Serving recommendations invalidates the model cache (the thrash is real here)

`_reception_key` returns `(shownCross, openedCross)` once Open-Mindedness is active, and
`record_recommendations_shown` — which runs **after every serve** — creates a RecEvent row for
each newly surfaced rec, moving `shownCross`. So the serve itself moves the next request's cache
key. Proof, not inference:

* two `rec_model_build` events with the **same** `readingVersion=94` in one probe window — the
  second rebuild (407.4 ms) was triggered by reception, not by a read;
* `+2 rec_model_cache_miss_total` during the warm section, where **no read happened**;
* the 1,376.0 ms "warm" wall is exactly the serve that ate one of those rebuilds.

The local report called this "not observed" — correct locally, because the bench reader was not
Open-Mindedness-active. The docstring even announces the behaviour ("once active it tracks
(shown, opened) so opening more rebuilds") — but it is not just *opening*: **showing** moves it,
and showing is something the serve does to itself.

### 2. The cache key costs 40–170 ms per lookup, and every request pays it three times

`rec_cache_key_ms`: mean 127 ms across 9 lookups warm (sum 1,143 ms); around the write, the two
miss-path samples sum to ~3.3 s. The key is `count_reads` + `recommendation_reception` — and
`recommendation_reception` **loads every cross-cutting RecEvent row as a full ORM object to count
it** (`store.py:2304-2308`), a per-serve-growing table for an active reader.

Three `_model` calls per request pay it: the serve itself, the slot's `explanation_context`, and
the handler's `_attach_explanations` → `_resolver_ctx` → `explanation_context`. 9 lookups / 3
requests, measured.

The same query then runs **twice more inside every rebuild** — `build_selective` (398.9 ms mean
cold) and `_reader_exposure` (unstaged in the probe run; staged as `build_reader_exposure` since,
which is why the 6,151.9 ms `serve_model` had ~2 s no stage claimed).

### 3. Everything store-touching was 5–10× slower in the post-write window

`read_persist` 233.1 ms (1.2 ms locally); cache-key hits 124–166 ms immediately after the write
vs 37.9–38.3 ms later in the same run. Consistent with WAL checkpointing after the write plus the
background poller on the same SQLite file — the 6.7 s first GET landed inside that window. This
multiplies causes 1 and 2; it is not independent of them.

### Where a warm serve's floor comes from (554.3 ms, no rebuild)

cache_key ×3 (~120–400 ms) + `slot_context` (~110 ms — `user_report` + `get_reads` +
familiarity) + `handler_explanations` (~45–165 ms, a second `explanation_context`) +
`record_shown` (~11 ms) + serialisation (~6 ms). The earlier 758.1 ms production "warm"
measurement matches this floor plus jitter.

## Revised smallest, highest-confidence optimisation

**Stop the serve from invalidating its own cache.** `_reception_key`'s active branch should not
move on `shownCross` at serve granularity — key on `openedCross` (a real reader action) with
`shownCross` bucketed, or simply on `openedCross` alone. Rebuilds observed in the probe would
drop from 3 to 1 (the legitimate post-read one); the 1,376 ms warm outlier and the 725 ms
"should be warm" serve are exactly the rebuilds this removes. Freshness is preserved where it
matters: a new read still invalidates (reading version), an open still invalidates, and the
Measured report's Open-Mindedness *values* still come from the live query at build time — only
the rebuild *trigger* coarsens.

Close second (compounds with the first): **make `recommendation_reception` a COUNT query** instead
of materialising every row (two `SELECT count(*)` with the existing `user_id` index), and **reuse
one `_model` result per request** instead of three lookups. Together they attack the ~350–550 ms
warm floor.

The report's earlier story-view recommendation stands *only* as a latent risk (`[5]` proves the
machinery works today); it is no longer the recommended fix.

---

# Implemented (2026-08-02)

Three changes, matching the revised recommendation exactly:

1. **`personalize._reception_key`** — the active key is now `(1, openedCross)`; `shownCross` is
   out of the key. A serve recording what it showed no longer invalidates the model it just used;
   the activation edge and every open (and, as always, every read) still rebuild. `min_opened` is
   clamped ≥ 1, so an active key can never collide with the inactive `(0, 0)`. The reception
   VALUES are re-read live at every rebuild — only the rebuild *trigger* coarsened.
2. **`store.recommendation_reception`** — two indexed `COUNT`s instead of materialising every
   cross-cutting `RecEvent` row as an ORM object to `len()` it. Same contract, bit for bit
   (cross-only in both counts, opened ⊆ shown, `rate: None` at zero shown) — the pre-existing
   equivalence tests in `tests/test_store.py` run unchanged against the new query.
3. **`personalize.explanation_context(user_id, model=None)`** — the story slot passes the model
   it already holds, removing one of the three per-request cache-key lookups. The handler's
   explanations pass keeps its own lookup: it sits on the other side of a module boundary whose
   shared Backend/Personalizer signature is not worth changing for a lookup that items 1–2 made
   cheap.

**Verification.**
`test_surfacing_more_recs_does_not_rebuild_the_active_model` reproduces the production loop
(active reader; shown-write after the serve) and fails against the reverted key — checked by
mutation, not asserted. The production loop replayed locally: five serves with a shown-write
after each = **1 miss + 4 hits** (pre-fix: a miss per serve), walls 15.8 → ~3 ms.

**To confirm on the box** (the same probe, same sections):

```bash
sudo bash /opt/ih/deploy/ops/rec-latency-probe.sh --email you@example.com
```

Expected against the pre-fix run: `[2]`'s cache decisions show `+0 rec_model_cache_miss_total`
(was `+2`) and no `rec_build_*` rows in the warm delta; `rec_cache_key_ms` collapses from
~127 ms mean toward single digits; the warm floor drops from ~554 ms toward the
explanations + slot-context + record-shown residue. The cold path after a read keeps its one
legitimate rebuild.

## Post-deploy verification (2026-08-02, `f8dbf57`, probe at 15:16 — ~90 s after the restart)

**The fix verified, by the counters it was specified in:**

* `[2]` warm window: **`+0 rec_model_cache_miss_total`** across 4 serves (`+8` hits) and zero
  `rec_build_*` rows in the delta — pre-fix the same window showed `+2` misses with two full
  rebuilds. The serve no longer invalidates its own model.
* `rec_cache_key_ms` per lookup: **8.9–23.7 ms** in the stage lines — measured *under heavy
  load* (below) — versus 36–167 ms on a *quiet* box pre-fix. The COUNT rewrite works.
* Lookups per serve: `cache_key` fired **8× for 4 serves** (2 each: the serve + the handler's
  explanations pass) — was 3 each; the slot now reuses the request's model.
* `[3]`: exactly **`+1` miss** — the legitimate post-read rebuild — and the build line now
  carries `build_reader_exposure`, confirming the running build.

**And the run caught the "latent risk" live — it is now the measured #1 problem.** The deploy
restarted the process with an empty story cache, the probe (and the reader's open browser) hit it
before the poller's first warm, and `[5]` recorded:

```
peek hits    : 1
inline builds: 4        <- full clusterings on REQUEST threads
story_default_view_inline_build_ms  count=2  avg=23,840.7  max=24,175.1
```

Four inline story builds started in the boot window; the two that had finished by the snapshot
cost **~24 s each** at 51,829 articles. The inline branch is deliberately **uncached** (the
`/api/analyze` read-only contract), so until the poller's first warm every story-consuming
request repeats it — and on a 2-vCPU box, two concurrent 24 s clusterings starve everything
else. That contention, not the recommendation pipeline, is why this run's walls are *worse* than
the quiet pre-fix run: the `[3]` model rebuild cost 1,996 ms against 93.9 ms for the identical
build on the same code base quiet — a 21× inflation with `build_population` at 1,360 ms, and
`slot_reads` (a per-user reads fetch measured at 3–5 ms quiet) at 340 ms. The probe effectively
measured the box mid-clustering-storm, which is exactly what a reader hitting the site in the
first minute after any deploy experiences.

Two consequences:

1. **Steady-state verification needs a re-run** on the warmed process — the fix's wall-clock
   effect (warm floor toward ~100–250 ms) is masked here by the boot storm.
2. **The story-view boot window is promoted from latent risk to the recommended next fix**, in
   its original form: a request-thread peek miss must not cluster inline — serve last-known (or
   empty, which every consumer already tolerates) and kick the existing single-flighted
   background refresh, while keeping a genuinely read-only inline path for `/api/analyze`'s
   documented zero-write contract. At 24 s × N concurrent consumers × every restart, this is
   now the largest single latency source in the system.

## Boot-P0 (2026-08-02): request threads never cluster

The fix the 15:16 probe demanded, in the form the original report specified:

* **`story_service.default_story_view`** — a request-path peek miss now serves `[]` (a shape
  every consumer already tolerates — it is what an empty catalog produces) and kicks the
  EXISTING single-flighted background refresh, so the first consumer heals the boot window for
  everyone at one build's cost, off the request threads. The expired-entry and
  serve-stale-off misses take the same branch: the TTL's staleness bound is still honoured —
  nothing past it is ever served — it is just no longer honoured *at the reader's expense*.
* **`build_inline=True`** preserves the read-only inline build for the two `/api/analyze` routes
  (`article_analyzer._story_block`, `analysis_enrichment.enrich_for_reader` via
  `story_index`) — their endpoint contracts a request that writes nothing anywhere, which a
  refresh kick would violate asynchronously — and for the offline audit CLI, where no poller
  ever warms the cache. With `RWE_STORIES_CACHE_TTL=0` everyone builds inline: an explicit
  opt-out of the caching layer is an opt-in to uncached costs.
* **`evidence_resolver.story_index`** forwards the flag, and no longer pins an *empty* index:
  during the boot window the view serves `[]`, and caching `{}` would have kept "no story
  evidence" alive for up to a full TTL after the refresh landed. An empty index now re-derives
  off the ~1 ms peek per call, so healing is immediate.

Degradation during the (now single-build-length) boot window: recommendations carry no
story-slot card and no `story_match` explanations, publisher profiles show no co-coverage —
each exactly the documented empty-catalog behaviour, healing without intervention.

Verification: `test_request_path_peek_miss_serves_empty_and_kicks_one_refresh` (cold: `[]`, no
synchronous `build_stories`, exactly one kick, healed after the refresh runs),
`test_the_analyzer_inline_path_still_builds_read_only` (data on a cold cache, no spawn, no cache
entry), `test_cache_disabled_keeps_the_inline_build_for_everyone`, and the two updated
expired/kill-switch contract tests. The probe's `[5]` now separates `async kicks` (healthy) from
`inline builds` (analyze/cache-off only).
