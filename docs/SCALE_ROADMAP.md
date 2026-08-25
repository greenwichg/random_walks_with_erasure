# Scaling to a 50,000-source universe — the dependency-ordered path

**Status:** design, with **M1 and M2 built and shipped off** (Parts 7 and 8) ·
**Companions:** `CRAWLER_ARCHITECTURE_AUDIT.md` (how we fetch), `SOURCE_COVERAGE_AUDIT.md` (which
publishers we carry and what they do inside), `CORPUS_ARCHITECTURE.md` (the corpus contract this
roadmap extends — now amended to four datasets), `PERFORMANCE.md` (every cost constant quoted below).

The strategic goal is explicit: ingest a Ground News–scale source universe, ~50,000 outlets. This
document works out what actually breaks on the way there, in what order the fixes depend on each
other, and which single milestone has to be built first.

Every number here is either **[M] measured on production**, **[D] derived by arithmetic over
measured values**, or **[P] projected from a measured rate with the assumption stated** — the
provenance convention `capacity_report.py` already uses, because a scale plan is only as good as its
weakest number and they all look alike once formatted.

---

## The one-paragraph answer

Three bounds break before 50k sources, and **the two that break first are silent**. Not the
quadratic clusterer — a count-based scan cap and a count-based retention cap, both of which convert
extra ingestion into *less* coverage without raising an error. The fix for all three is the same
structural move: **the clustering corpus stops being "whatever `_fetch` returned" and becomes an
explicit, semantic projection** — a bounded Tier A spine that the O(n²) builder owns, with Tier B
attaching to that spine by *assignment* rather than participating in the build. Tier B scales to
50k because assignment is linear in new arrivals and cannot alter the partition. That one change is
the first milestone and every other stage depends on it.

---

# Part 1 — What actually breaks, in the order it breaks

Scale factor `k` below is *window articles ÷ 22,493*, the corpus the live pipeline profile was taken
on. Today `k = 1`. A 50k-source universe at a conservative 3 items/source/day is `k ≈ 40`; at 10
items/source/day it is `k ≈ 133`.

## Break #1 (silent, and already armed): the scan cap turns sources into fewer stories

`story_service.max_scan_default()` is **60,000 rows [M]**, applied by `_fetch` as
`sort="newest", limit=60000`. The window is a *time* bound (6 days) with a *count* cap on top.

`_fetch`'s own docstring records what happened the last time that cap bound:

> "It used to be `max_scan=2000` rows ordered newest-first, which made story yield a function of
> ingestion RATE: every provider added shrank the hours those 2000 rows covered, so integrating more
> sources produced FEWER stories (measured: a 12.5-hour effective window against a 6-day clustering
> threshold, 89 stories from a 12,790-article catalog)."

The cap was raised 2,000 → 60,000. **The target is 40–133× bigger than the corpus that cap was sized
against.** [D]

| ingestion rate | what 60,000 newest rows covers | vs the 6-day clustering window |
|---|---:|---|
| today, ~4,650/day [M] | 12.9 days | cap never binds ✓ |
| 50k sources @ 3/day → 150k/day [P] | **9.6 hours** | 6-day window silently truncated to ⅙ of a day |
| 50k sources @ 10/day → 500k/day [P] | **2.9 hours** | window truncated to ~2% of itself |

This is the first thing that breaks, it breaks during *shadow ingest* rather than at 50k, it emits
no error, and its symptom — fewer stories — reads as a clustering regression rather than a bound
being hit. **This is the defect that decides which milestone comes first.**

## Break #2 (silent): the retention cap turns the archive into hours

`RWE_RETENTION_MAX_COUNT=150000` [M] — "~30 days of ingestion; DB steadies ~460 MB" per
`deploy/.env.production.example`. It is a **count**, and the comment's "30 days" is an artifact of
today's rate.

| ingestion rate | what 150,000 rows is |
|---|---|
| today [M] | ~32 days ✓ |
| 150k/day [P] | **1 day** |
| 500k/day [P] | **7 hours** |

`CORPUS_ARCHITECTURE.md` makes searchability a contract: *"Every ingested article remains searchable
unless removed by retention or moderation"*, and ① is responsible for being *complete and findable*.
A count cap under 100× ingestion silently reduces the Full/Searchable Corpus to a few hours deep —
so **search breaks before clustering does**, and it breaks the one dataset whose entire job is
completeness. Retention must become **age-per-tier**, not a global count.

## Break #3 (loud, eventually): the story builder is quadratic twice

From the live-corpus profile (22,493 rows → 1,074 stories, `build_stories` = 5,069 ms [M]):

| stage | prod ms | exponent | shape |
|---|---:|---|---|
| `_fetch` | 2,319 | 1.07 | linear |
| `cluster` | 1,942 | **2.05** | quadratic in articles |
| `_merge_duplicates` | 1,451 | — | **quadratic in clusters** (250,736 candidate pairs over 1,139 clusters [M]) |
| `feed_article_to_article` | 1,118 | 1.12 | linear |

`_merge_duplicates` being a *second* quadratic term is easy to miss: the synthetic profile put it at
exponent 0.60, which `PERFORMANCE.md` records as wrong — the synthetic corpus produced zero merges,
so the path never ran. The pair count is ~40% of all cluster pairs, and cluster count grows roughly
with articles.

Envelope, fitted to those constants (`3.44k + 1.94k^2.05 + 1.45k²` seconds) [D]:

| k | window articles | build time | vs the 600 s poll cycle |
|---:|---:|---:|---|
| 1 | 22.5k | 6.8 s *(measured 5.1 s — the fit is ~35% conservative)* | fine |
| 2.7 | 60k | 35 s | fine |
| 3.7 | **83k** | **60 s** | **the Tier A budget on this box** |
| 10 | 225k | 6.6 min | 66% over |
| 40 | 900k | **1.7 h** | 10× over |
| 133 | 3.0M | **~20 h** | 120× over |

**Conservative floor: 1.7 hours per build. Realistic: 8–20 hours.** And that is CPU-seconds on a
`t3.medium` whose *sustainable* budget is **0.40 vCPU [M]** — 240 vCPU-seconds per 600 s cycle, of
which the whole process currently uses 0.19 vCPU. Wall clock is worse than the table.

### The Tier A budget, stated as a number

Holding the build to ~60 s (25% of the cycle's sustainable CPU) gives **k ≈ 3.7, i.e. ~83,000
articles in the 6-day clustering window — about 3× today's corpus** [D]. That is the design budget
for Tier A. It moves with the box and with the constants; it does not move with the source count,
which is the entire point.

## Break #4: the poller cannot fan out

Two structural facts, both already documented, neither a scale problem at nine feeds:

* **The poller runs inside the API process** as a daemon thread (`feed_service.py:300`) — ingestion
  CPU competes with request serving under the GIL (`PERFORMANCE.md` finding #7).
* **`poll_adapter_once` holds one global lock across poll *and* post-cycle** (`sources.py:1534`).
  `PERFORMANCE.md` notes this is why adapters "cannot finish simultaneously" and why the coalescing
  warmer was dead code. At 50k feeds a global serialization point is fatal.

## Break #5: SQLite's single writer

`concurrency_report.py`'s premise: *"SQLite permits one writer at a time, so this is a hard ceiling
on write-bearing requests however much CPU is spare."* At 500k articles/day that is ~5.8 sustained
writes/second, bursty, contending with retention, `corpus_refresh` and the story warm on the same
writer. Storage at 3.3 KB/article all-in [M: 25,300 articles = 83.2 MB] projects to **181 GB/year at
150k/day, 602 GB/year at 500k/day** [P].

---

# Part 2 — What does *not* break, which is the useful surprise

## The crawler already has the right scaling property

The adaptive law's equilibrium, derived in `CRAWLER_ARCHITECTURE_AUDIT.md` and pinned by a
simulation test:

```
p* = ln(slowdown) / (ln(slowdown) − ln(speedup))  = 0.369   at the shipped 0.5 / 1.5
T* = p* · 86400 / N                               for a feed publishing N items/day
```

Polls per day for one feed is `86400/T* = N/p* = 2.71·N`. Summing over the universe:

> **Total polls/day = 2.71 × total items/day. Crawl cost is proportional to CONTENT, not to source
> count.** [D]

| universe | polls/day | sustained request rate |
|---|---:|---:|
| 150k items/day | 407k | **4.7 req/s** |
| 500k items/day | 1.36M | **15.7 req/s** |

At ~2 s latency that is ~32 concurrent connections. **The fetch fleet is not the bottleneck at 50k
sources** — a genuinely load-bearing finding, and it falls out of a mechanism built for nine feeds.

### Except: the ceiling binds for the tail, and the tail is the whole universe

`DEFAULT_MAX_INTERVAL = 6 h`. A feed publishing 1 item/day wants `T* = 0.369 × 86400 = 8.9 h`, which
is *above* the ceiling. The ceiling binds for anything under **1.48 items/day** [D] — and the
validation prefilter measured the real distribution: **3,442 of 4,083 outlet identities sit below a
10-article volume floor, median 1 article in the window** [M], i.e. ~0.17/day.

So 50k mostly-quiet feeds poll at the ceiling: **50,000 × 4 = 200,000 polls/day = 2.3 req/s of pure
ceiling-bound waste** that does not shrink as sources get quieter [D]. Fix: raise the ceiling to
~24 h and add a **dormant** state (no change in N days → daily, then weekly, probe). Small, bounded,
entirely inside `feed_schedule.advance`.

## Search does not need the story builder

`/api/search` is **12 ms median [M]**, index-backed through `store.search_feed_articles`. It scales
with the index, not with clustering. `CORPUS_ARCHITECTURE.md` principle 2 already says
*"Recommendation eligibility is independent of searchability"*; this roadmap generalizes it to
**clusterability**, which is the same shape of separation with a new consumer.

## The persisted due-queue already exists

The per-feed scheduler added `etag`, `last_modified`, `content_sha`, **`next_due_at`** and
**`interval_s`** to `feed_health`. A worker pool claiming leases off an index on `next_due_at` is a
small step from where the schema already is — not a redesign.

---

# Part 3 — The load-bearing design idea

> **Cluster once over a bounded Tier A spine. ATTACH Tier B to the result. Never let Tier B into the
> build.**

| | Tier A | Tier B |
|---|---|---|
| **what it is** | the clustering corpus — outlets that form and vote in stories | everything else we ingest |
| **size** | bounded at ~83k articles / 6-day window [D] | unbounded; scales to 50k sources |
| **cost shape** | O(n²) build, paid once per poll cycle | O(1) candidate lookup + O(k) scoring **per new article** |
| **admission** | gated: needs a lean rating **and** must pass the counterfactual bars | automatic on validation |
| **can it change the partition?** | yes, that is its job | **no — by construction** |
| **surfaces** | Stories, Discover, coverage counts, blindspot arithmetic | Search, attribution, story *attachment* |

## Why assignment is cheap and build is not

The build is quadratic because it is **stateless** — it recomputes the whole window every cycle, so
cost tracks *window size*. Assignment tracks *arrival rate*, and arrival rate per cycle is
`daily volume ÷ 144`. At 500k/day that is **3,472 new Tier B articles per 600 s cycle** [D] — each
one a postings lookup on its rarest tokens plus ~10 profile scores against candidate cluster
profiles. `_merge_duplicates` already builds exactly those cluster profiles (frozensets plus IDF
weights, 166 ms for 1,139 clusters [M]). The machinery exists; it is being pointed at a new input.

## Why the containment is provable rather than argued

The invariant that makes this safe is one sentence and one test:

> **For any Tier B input whatsoever, the Tier A story set is byte-identical** — every id, title,
> coverage count, publisher count, blindspot side, trust verdict and ordered member-URL list.

That is not a new bar. It is the exact bar `PERFORMANCE.md` records for the candidate-walk rewrite
(four corpora, 3,499 stories) and for the `_merge_duplicates` size bound (four corpora plus a
production witness). A Tier B article that cannot merge two Tier A clusters, cannot split one,
cannot change a story id, and does not vote in `min_publishers` cannot move a blindspot claim
either — which is the property the product actually needs.

## Why this is not a new architecture

`CORPUS_ARCHITECTURE.md` already defines ① Full/Searchable, ② Recommendation (a quality-filtered
projection of ①), ③ Reads — with boundaries enforced by `tests/test_corpus_boundaries.py`. Its
invariant table currently reads *"Search / Discover / Stories / Story-Intelligence endpoints never
read ②"*, i.e. **Stories reads ① directly**.

The change is one row: **Stories reads a new projection ②′, the clustering corpus.** Same contract,
same guardrail test file, one more boundary. The system already believes that searchable ≠
recommendable; it needs to also believe searchable ≠ clusterable.

---

# Part 4 — Source Discovery → Shadow Ingest → Evaluation → Promotion → Retirement

## Stage 1 — Discovery

Cheapest first, and the first source needs **no crawling at all**: the catalog is already full of
outlets we ingest and cannot identify. **4,083 outlet identities in the window, 3,729 unrated
[M].** GDELT alone delivers arbitrary-domain URLs.

| discovery channel | cost | measured yield |
|---|---|---|
| crawl exhaust — unresolved publisher identities already in the catalog | zero, offline | **142 candidates on 151 hosts** clear a ≥10-article floor [M] |
| feed autodiscovery (`<link rel="alternate">`) on candidate hosts | 2 requests/host | unmeasured |
| outbound links from ingested articles | needs body retention | unmeasured |
| registry gap-filling by country/language | manual | 151 rated registry outlets published nothing in the window [M] |

**Gate:** a candidate is a `(host, evidence)` pair with ≥ 10 articles observed. The 3,442 identities
below that floor (median 1 article) are not candidates — they are noise, and spending a network
request on each is how a discovery pipeline becomes a crawl of the whole internet.

## Stage 2 — Validation (the only network-bound stage, and it is small)

151 hosts × 2 requests × 2 s politeness ≈ **10 minutes of crawling** [D]. Gates:

1. `robots.txt` permits our agent
2. a feed is discoverable and parses
3. ≥ 10 items in the feed
4. ≥ 80% of items carry a publication date *(this gate is unanswerable offline — `_fetch` is
   time-windowed, so every catalog row has a date by construction and an offline probe would report
   0 rejections whatever the feeds serve)*
5. article URL pattern is stable and on the declared host
6. language identified
7. host is not already a tracked outlet's
8. **host is not an aggregator or proxy** ← the `news.google.com` gate

Gate 8 exists because of a measured failure. The outlet-resolution counterfactual found **996 of
1,246 newly-attributed articles landing on "Google News"** from `10tv.com @ news.google.com`,
`12news.com @ news.google.com` — real local broadcasters proxied through one host, which would have
been attributed to a registry `kind=aggregator` instead of their actual publishers. A discovery
pipeline without gate 8 discovers aggregators and calls them publishers.

**Blocked on:** a ToS / robots review that has never been done. `CRAWLER_DESIGN.md` records that no
live crawl has ever run and that `crawler.py`'s configured patterns are "unverified guesses". This
stage is the first thing in the entire roadmap that touches a publisher, and it does not start
without an explicit go-ahead.

## Stage 3 — Shadow ingest

`tier = 'shadow'`. Stored, deduped, attributed, **not surfaced anywhere** — not Search, not
Discover, not Stories — for a minimum of **14 days**, long enough to measure a publish rate rather
than a burst.

> **This amends the `CORPUS_ARCHITECTURE.md` contract and must be recorded as an amendment, not
> slipped in.** Principle 1 currently reads *"Every ingested article remains searchable unless
> removed by retention or moderation."* Shadow is a third exception. Naming it in the contract and
> extending `tests/test_corpus_boundaries.py` is the difference between an architectural decision
> and a silent violation.

## Stage 4 — Evaluation

Pre-registered before the shadow window opens — the discipline that produced three rejections in a
row in `SOURCE_COVERAGE_AUDIT.md`, each of which overturned a recommendation already made in
writing. Metrics, and what each one is for:

| metric | what it catches |
|---|---|
| items/day and its variance | dead and burst-only feeds |
| median `fetched_at − published_at` | a feed that only carries stale items |
| **duplication rate** — items whose canonical URL or title already exists from another source | **syndicators and aggregators wearing a publisher's clothes** |
| host stability — share of items on the declared host | proxies, redirect farms |
| registry resolution — does it resolve? does it carry a lean? | decides *which tier* it can be promoted to |
| **assignment hit rate** — share of its items that attach to an existing Tier A cluster | whether it covers the same events as the spine; this is the Tier A promotion worklist and it costs nothing extra because assignment already runs |
| **clustering counterfactual** — `build_stories` with and without its items in Tier A | Δstories, Δcovered, Δlargest cluster, **articles that LOST their story**, exhibits, `_coherence_stats` — `audit_clustering_change.py` unchanged |
| **Δ blindspot claims** | the metric that decided the last three candidates |

## Stage 5 — Promotion, and the asymmetry that makes 50k possible

**→ Tier B: automatic.** Passes validation, duplication below threshold, not aggregator/wire,
language identified. **No clustering bar is required because Tier B cannot alter the partition** —
the containment invariant does the work a review board would otherwise have to do. This is the gate
that scales to 50,000.

**→ Tier A: gated, manual, and permanently narrow.** Requires:

* a **lean rating** — without one the outlet inflates story size while contributing nothing to the
  blindspot claim, which is `SOURCE_COVERAGE_AUDIT.md`'s central finding and moves Information Health
  the wrong way; and
* the counterfactual bars: story count not down, `articles that LOST their story` ≈ 0, largest
  cluster not materially up, coherence not degraded, exhibits unmoved.

**The honest consequence, stated plainly:** every comparable system pays humans for the rating.
Ground News — our closest analogue at ~50k sources — *licenses* AllSides / MBFC / Ad Fontes rather
than generating ratings, and NewsGuard's is entirely human. So Tier A is bounded by human or
licensing throughput, which is a budget, not an algorithm. **50k sources buy coverage, search and
attribution. They do not buy blindspot claims.** Anyone who wants 50k *rated* sources is asking for
a licensing deal, not an architecture.

## Stage 6 — Retirement (reversible by default)

| trigger | action |
|---|---|
| breaker at ceiling for N cycles, or no items in 30 days | → dormant (daily probe), then retired |
| duplication rate spikes | A → B — it became a syndicator |
| host drift, language change, parked domain | → dormant, re-validate |
| periodic re-audit fails the clustering bars | A → B |

Retirement keeps the registry row and re-probes at the ceiling. A retirement that deletes evidence
cannot be audited later, and the recurring lesson in `PERFORMANCE.md` is that the expensive failures
are the ones where the evidence was gone.

---

# Part 5 — The dependency-ordered roadmap

```
M1  corpus boundary ──┬── M2  bound Tier A + fix the count caps ──┬── M4  Tier B assignment ──┐
                      │                                          │                            │
                      ├── M3  storage substrate ─────────────────┤                            │
                      │                                          │                            │
                      └── (M5 shadow lane) ◄─────────────────────┴── M6 crawler fan-out       │
                                   │                                        │                 │
                                   └── M7 discovery + validation ───────────┴─── M8 evaluation ┘
                                                                                     │
                                                                          M9 promotion / retirement

                              M10  incremental clustering — deliberately last, and probably never
```

| # | milestone | depends on | why here |
|---|---|---|---|
| **M1** | **Corpus boundary — the clustering corpus becomes an explicit projection** | nothing | everything else depends on it; provable today with zero new publishers |
| M2 | Bound Tier A; replace count caps with tier-aware, age-based bounds; precompute `readingMinutes` at ingest so `_fetch` can narrow (31% of the build) | M1 | closes breaks #1 and #2 |
| M3 | Storage substrate: Postgres or partitioned SQLite, Tier B without `body`, real search index, retention by age-per-tier | M1 (the tier column must exist before the migration, or you migrate twice) | closes break #5; long pole; parallelizable with M6 |
| M4 | Tier B story attachment by assignment + the byte-identical containment test | M1, M2 | this is what makes Tier B visible in stories |
| M5 | Shadow ingest lane; amend the corpus contract; extend the guardrail tests | M1, M3, M6 | nowhere safe to put candidates before this |
| M6 | Crawler fan-out: poller out of the API process, narrow the global lock, worker leases off `next_due_at`, raise the interval ceiling, add dormancy, per-host politeness + robots cache | M1 (tier marker on ingested rows) | closes break #4; the `crawler.py` POC already has the robots gate and rate limiter, never run |
| M7 | Discovery + network validation | M5, **plus an explicit go-ahead and a ToS review** | first thing that touches a publisher |
| M8 | Evaluation harness on shadow data | M5, M7, M4 | assignment hit rate is one of its inputs |
| M9 | Promotion / retirement automation | M8 | the gates are only as good as the metrics |
| M10 | Incremental clustering | M2 | **only if Tier A must exceed its budget** |

## Why incremental clustering is last, and why it may never be needed

The user's brief listed it first, so the conclusion is worth stating directly: **tiering makes
incremental clustering unnecessary on the path to 50k sources.** `PERFORMANCE.md` already explains
why it is a redesign rather than an optimization —

> "Single-linkage DSU is *almost* incremental: adding an article can only merge existing clusters,
> never split them. But `_repair` splits, `_merge_duplicates` joins across the whole set, and the
> rolling 6-day window *removes* articles at the tail — and removal is what single linkage cannot do
> incrementally, because you cannot tell which merges depended on the departed article without
> recomputing."

It also costs determinism. Today's builder is stateless and reproducible: same rows → same stories,
same ids, same order. That reproducibility is what every counterfactual in this repo's audit history
depends on — `audit_clustering_change.py` builds twice and diffs. An incremental clusterer is
path-dependent, so the harness that adjudicates every clustering decision would need rebuilding
first. **Spend that only if Tier A genuinely needs to exceed ~83k articles per window. If Tier A is
bounded by the rated registry, it never will.**

---

# Part 6 — The first technical milestone

## M1 — The clustering corpus becomes an explicit projection

**Deliverable**

1. `tier` on the article row, defaulting to `A` for everything currently ingested — grandfathered,
   so day one is a no-op in production.
2. One corpus selector between the store and `build_stories`. The clustering corpus is **selected**,
   not "whatever `_fetch` happened to return".
3. Replace the positional `RWE_STORIES_MAX_SCAN=60000` truncation with the semantic Tier A filter
   plus a **loud** budget breach — a log line and an `obs_metrics` counter — because silent
   truncation is the documented failure mode and a bound that fails quietly has already cost this
   system a coverage regression once.
4. Extend `tests/test_corpus_boundaries.py` with the new invariant, alongside the three it already
   enforces.

**Acceptance bars — all falsifiable, all on instruments that already exist**

| bar | instrument |
|---|---|
| with everything in Tier A, `build_stories` output is **byte-identical** to today — ids, titles, coverage counts, publisher counts, blindspot sides, trust verdicts, ordered member URLs | the bar used for the candidate-walk rewrite and the `_merge_duplicates` bound |
| 0 stories lost, 0 `articles that LOST their story`, exhibits unmoved | `audit_clustering_change.py` |
| inject 100k synthetic Tier B rows → story set still byte-identical, build wall time unchanged within the harness error bar | `profile_merge.py`'s round-robin bench discipline (a control arm, not a before/after) |
| every new env var reaches the container | `tests/test_env_hygiene.py` — compose `environment:` is an allowlist and it already swallowed `RWE_FEED_MIN_INTERVAL` silently this month |

**Why this one first, in four sentences**

Every other milestone depends on it and it depends on nothing. It is provable **today, on the
current corpus, with zero new publishers** — no network, no ToS question, no crawl, so it cannot be
blocked by the one thing in this roadmap that needs external permission. It converts "50k sources"
from a performance problem into a *membership* problem, which is the only form of the question this
system's measurement discipline can actually adjudicate. And it closes a live latent defect rather
than building speculative infrastructure: `max_scan=60000` will re-create the documented
"more sources → fewer stories" regression the moment ingestion rate rises, which is to say during
shadow ingest, before anyone is watching for it.

---

# Part 7 — M1 as built

Shipped **off**, byte-identical, on `claude/sleepy-gates-oecof1`. 3,494 engine tests pass.

## What landed

| | |
|---|---|
| `examples/corpus.py` | the boundary: the tier policy, the selector, and the budget report |
| `story_service._fetch` | routes its rows through `corpus.select` and **keeps the `total` it used to discard** |
| `story_service.max_scan_default` | docstring corrected — see the retraction below |
| `examples/audit_corpus_boundary.py` | the production instrument: what binds today, plus the containment bars **and their control arm** |
| `tests/test_corpus_tiers.py` | 17 tests on the policy and the report |
| `tests/test_corpus_boundaries.py` | the ①/②′ invariant, its control arm, and the structural seam check |
| `tests/test_env_hygiene.py` | the compose allowlist pin |
| `docs/CORPUS_ARCHITECTURE.md` | amended: three datasets → four |

## One design decision changed during implementation

**The roadmap called for "a `tier` column on the article row". It was built as a property of the
OUTLET instead, derived at selection time, and that is the better design.**

"Does this publisher form stories" is a fact about a publisher, not about one of its articles.
Deriving the tier means no migration and no backfill; two articles from one outlet cannot disagree;
and a demotion (A→B when an outlet turns out to be a syndicator) takes effect over that outlet's
whole history on the next build, which is what a demotion should mean. A stored column would have
frozen the answer at ingest and required a rewrite to change it.

The cost was real and is recorded rather than hidden: **the tier predicate could not be pushed into
SQL**, so once Tier B had members their rows would still count against the row cap before `select`
ever saw them. `report["capBoundBeforeTier"]` stated this at runtime rather than leaving it to be
inferred. **M2 closed it** — see Part 8. The seam (`corpus.tier_of`) means moving the source of
truth to a registry column or its own table still changes no caller.

## A correction to what `max_scan_default` claimed

Its docstring said the 60,000-row cap "sits far above a normal window so it only ever engages if
ingestion volume spikes far beyond projections." Two things are wrong with that and both are now in
the docstring:

* "far above" is a statement about *today's ingestion rate*, not about the cap — the same 60,000
  covers 12.9 days now, 9.6 hours at 150k/day and 2.9 hours at 500k/day;
* it sits **below** the Tier A CPU budget (83,000), so the "memory backstop" is in fact the binding
  constraint rather than the safety net it is described as. `report["binding"]` prints which one
  binds so nobody has to work it out from two docstrings.

## The bars, and which of them production still owes

| bar | where | status |
|---|---|---|
| off returns the **same list object**, and resolves no outlet at all (proven by making `default_registry` raise) | unit | ✅ |
| a Tier B article is searchable (①) but absent from ②′, story set byte-identical | unit | ✅ |
| **control arm** — that same article DOES move the story set with tiering off | unit | ✅ |
| `_fetch` routes through `corpus.select` and keeps `total` | structural | ✅ |
| `total == cap` is **not** a breach (the off-by-one that would cry wolf on a full window) | unit | ✅ |
| compose ships all three vars, all empty | env hygiene | ✅ |
| the same three bars **on the live catalog**, with 40k injected Tier B rows | `audit_corpus_boundary.py` | ✅ |
| what actually binds the live clustering corpus | `audit_corpus_boundary.py` | ✅ |

```bash
cd /opt/ih && source deploy/ops/_compose.sh
dc run --rm -T api python examples/audit_corpus_boundary.py --db "$RWE_DB_URL"
```

## Measured on production, 2026-08-25 (`f272901`, then `b4be703`)

```
window matched      : 27,809 articles     row cap: 60,000   Tier A budget: 83,000
requested window    : 143.96 h            binding constraint: cap
effective window    : 143.96 h   <- the cap is NOT binding; the window is intact
headroom to budget  : 55,191 articles (33.5% used)

BAR 1  rows in 27,809 -> out 27,809, SAME LIST OBJECT, 1,503 stories       PASS
BAR 2  40,000 synthetic Tier B rows -> corpus 27,808, story set BYTE-IDENTICAL   PASS
BAR 3  same 40,000 admitted -> 18,661 stories, all 1,503 baseline entries differ  PASS

build_stories, best of 5 at 27,823 articles (b4be703): 8,494 ms
  samples: 11,587 · 11,972 · 9,178 · 8,494 · 11,451     spread 41% of min
```

**The boundary holds on the live catalog**, and the control arm is emphatic: admitting those rows
takes the catalog from 1,503 stories to 18,661 and changes every single baseline entry. Containment
is measuring something real.

**The cap is armed but has not fired.** Requested and effective windows are equal to two decimal
places, so the six days we ask for are the six days we cluster. Break #1 is a future event, not a
present one — which is exactly the state in which it is worth having an alarm.

### A budget concern, raised and then withdrawn by the follow-up measurement

The first run reported `build_stories` at **12,607 ms** on 27,809 articles, against
`PERFORMANCE.md`'s **5,069 ms on 22,493** — 2.5× the time for 1.24× the rows. Since the 83,000 Tier
A budget was derived from the 5,069 ms anchor, that looked like it moved the ceiling to ~60,000,
i.e. onto the row cap. **The constant was not changed on the strength of it, and the follow-up says
that call was right.**

Best-of-5 on the same box, 27,823 articles (`--repeat 5`):

```
best of 5: 11,587, 11,972, 9,178, 8,494, 11,451   ->  8,494 ms
spread 41% of the minimum
```

Re-anchoring the roadmap's own envelope (`3.44k + 1.94k^2.05 + 1.45k²`, `k = n/22,493`) on 8,494 ms:
it predicts 9.47 s at that point, so it runs **11.5% conservative** — and the 60 s target still
lands at `k ≈ 3.7`, i.e. **~83,000 articles**. The budget stands where it was. At 8.5 s per build
that is 1.4% of a 600 s poll cycle and 3.5% of the cycle's 240 sustainable vCPU-seconds.

### The mechanism I predicted was wrong, while the caution was right

I expected the five samples to show a cold-start signature — sample 1 slowest, the rest warm, on the
strength of a local `--repeat 3` that printed `25, 5, 5`. **That is not the shape.** The first
sample is *mid-range* and the second is the slowest; there is no monotone warm-up anywhere in the
series.

The local pattern did not reproduce because it was an artifact of a 60-row corpus, where the
registry memo dominated the first build. At 27,823 rows, `_fetch` and `_entities_for` run **before**
the first timed build and warm everything — so by the time build #1 starts there is no cold cost
left to pay. What the spread actually shows is **contention**: the box is serving traffic, and the
load moves between rounds.

So 12,607 ms was never a cold-start artifact. It was an ordinary draw from a distribution whose
spread is 41% — which is why one sample could not settle the question, and why the answer to "is
this number real?" was best-of-N rather than a story about why it might be inflated.

**The 41% spread is itself the finding worth keeping:** any single build timing on this box carries
roughly ±20%, so a delta smaller than that is weather. The script now prints the spread beside the
best, so the error bar arrives with the number instead of having to be remembered.

### Instrument note

`--no-control` used to print `VERDICT: FAIL`, which is wrong: a bar deliberately skipped is not a
bar that failed, and conflating the two is how a FAIL line becomes something an operator learns to
ignore — the same cries-wolf argument that keeps `total == cap` out of the truncation detector. A
timing-only run now reports **`VERDICT: INCOMPLETE`** and names the bar that did not run.

### Two instrument corrections from the same run

**It queried the catalog twice.** Section 1 reported 27,809 rows and section 2 reported 27,808 — the
6-day window start is recomputed per call and the catalog is written continuously, so two fetches
seconds apart legitimately disagree. Harmless to the bars (every arm used the same rows), but a
reader is entitled to read a one-row discrepancy as a bug in the boundary. `_fetch` now takes an
optional `report_out`, so one fetch serves every section.

**The control arm's timing invites exactly the wrong inference.** Its two points (27,809 → 12.6 s;
67,808 → 169.8 s) imply an exponent of 2.92, and that number is worthless: the injected rows are
*copies of real headlines*, so the corpus has a far more concentrated token distribution than the
real one and its quadratic term is inflated. This is the calibration mistake `PERFORMANCE.md`
records twice — a synthetic corpus put the top-10 token share at 86.4% against production's 25.8%.
The arm is built to cluster hard **on purpose**; that is what makes it a control and what
disqualifies it as a benchmark. The script now says so in its own output, next to the number.

---

# Part 8 — M2 as built

Shipped **off**, byte-identical, on `claude/sleepy-gates-oecof1`. M2 closes breaks #1 and #2 — the
two *silent* ones.

## A — the row cap now bounds Tier A, not the mixture

`corpus.sql_exclusions()` feeds `store.search_feed_articles(exclude_publishers=...)`, so an excluded
row never consumes cap. The behaviour, as starkly as the data allows (`test_corpus_tiers.py`):

> Fifty Tier B articles newer than five Tier A ones, under a cap of ten. **Without** the prefilter
> the cap fills entirely with Tier B and the clustering corpus is **empty** — the tier filter
> removes them all and reports that it did, having already lost the window. **With** it, all five
> Tier A articles survive.

At 50,000 sources, where Tier B is most of the corpus, that difference is the whole milestone.

**The invariant that keeps SQL an optimization rather than a second policy:**

> Every row the prefilter excludes is a row `corpus.select` would have dropped anyway.

One-directional on purpose. The prefilter may *miss* rows — they fall through to the Python pass,
which is the contract — and must never remove one that pass keeps. It holds by construction: a
canonical name resolves to itself, and `_matches` now tests the host set against the **publisher
string** as well as the URL. That last part is not a convenience — `ingest.Scorer._resolve_outlet`
falls back to `raw.outlet or _domain_of(raw.url)`, so an outlet the registry does not know is
routinely *stored under its bare domain*, and a host-configured tier would otherwise miss exactly
the rows most likely to carry it.

What SQL cannot express is the **residue**: an alias the registry learned after ingest, or a Tier B
host appearing only in the URL. Those still consume cap, `select` still drops them, and
`report["tierResidue"]` counts them — so the fix (one registry alias row) is discoverable rather
than silent. `capBoundBeforeTier` is now precise (`capBound and residue > 0`) instead of
pessimistic.

### One SQL trap worth naming

`lower(NULL) NOT IN (...)` evaluates to **NULL, not TRUE**, so a bare `NOT IN` silently drops every
row with no publisher — a filter removing rows it was never asked about. The explicit `IS NULL` arm
keeps them, and `test_a_null_publisher_survives_the_exclusion` fails without it.

## B — retention becomes age-shaped, and per tier

`RWE_RETENTION_MAX_AGE_DAYS_TIER_B` / `_SHADOW`, both **0 = off**, both in the compose allowlist.
`plan_retention` takes an optional `age_days_for` **callable** rather than a tier map, so a
deletion policy stays unit-testable without the registry, the environment, or a store.

**The floors are unchanged and still outrank everything.** The repair pass runs over the same flags
whatever produced them, so a per-tier age inherits the guarantee rather than needing its own:
retention cannot breach a floor, whatever shape the policy is.

### The bug this could have shipped

`run_retention` skips the whole planner when a **count-only** policy is under its cap — worth a
measured 3,433–4,543 ms per run. That gate keyed on `not max_age_days`, and **a per-tier age is an
age policy**: it can have prunable rows at any catalog size. Left alone, configuring a Tier B age
under the count cap would have pruned nothing, silently, forever.

The comment already at that gate calls it *"the whole forward-compatibility contract"*. This is the
first time the contract was cashed in. `test_a_tier_age_is_not_swallowed_by_the_count_only_fast_path`
fails against the old guard with `skipped: under_count_cap, pruned: 0` — verified by reverting the
guard and re-running, not by reasoning about it.

### `audit_retention_horizon.py`

Read-only, deletes nothing, recommends nothing automatically. It answers the question the count cap
hides: **a count cap is an age cap whose length nobody chose.** It prints the horizon at the
measured ingestion rate (`capacity_report.ingestion_rate`, from `created_at` — never
`published_at`) and at 2×/5×/10×/50×/100×, flagging the point where the archive becomes shallower
than the 6-day clustering window, plus the age policy equivalent to today's cap.

```bash
dc run --rm -T api python examples/audit_retention_horizon.py --db "$RWE_DB_URL"
```

## C — the `readingMinutes` precompute: assessed, NOT built

The roadmap bundled this into M2 as "precompute `readingMinutes` at ingest so `_fetch` can narrow
(31% of the build)". Two findings from actually looking:

**It is smaller than it sounds.** Exactly one serving-path call site reads `body`:
`discover._reading_minutes` (`body or description`). Nothing else in the request path touches the
column.

**And more dangerous than it sounds.** `readingMinutes` does not only render a label — it feeds
`study_metrics.reading_time`, an Information Health metric. Between precomputing and backfilling,
any row without the stored value computes from `description` alone and silently shrinks, so the
failure mode is a *quietly wrong metric*, not a cosmetic one.

**Deferred, and the reason is scope discipline rather than difficulty.** It needs a stateful
backfill on the live database, and it does not close either break. Every other thing in M1 and M2 is
a no-op on deploy; bundling a production data migration into that would spend the property that
makes these milestones safe to ship. The build is **8.5 s against a 600 s poll cycle — 1.4%**, so
there is no pressure, and `PERFORMANCE.md` already says of this exact change: *"that needs a
backfill, so it is a change to plan, not to slip into a performance pass."*

## Bars

| bar | where | status |
|---|---|---|
| the cap bounds Tier A — 5 of 5 survive a cap Tier B would have eaten, 0 without the prefilter | unit (own control arm) | ✅ |
| the prefilter is a **subset** of what `select` drops (same kept set both ways) | unit | ✅ |
| a NULL publisher survives the exclusion | unit | ✅ |
| off adds no SQL term and `sql_exclusions()` is empty | unit | ✅ |
| no per-tier age ⇒ resolver is `None` ⇒ the planner's scalar path, unchanged | unit | ✅ |
| a Tier B age prunes only Tier B; a Tier A article is untouched | unit | ✅ |
| a per-tier age is **not** swallowed by the count-only fast path | unit (fails on the old guard) | ✅ |
| a count-only policy still takes the fast path | unit | ✅ |
| the floors outrank a per-tier age | unit | ✅ |
| compose ships all five vars, tiers empty and ages `0` | env hygiene | ✅ |
| the live retention horizon | `audit_retention_horizon.py` | **owed** |

```bash
cd /opt/ih && source deploy/ops/_compose.sh
sudo bash deploy/ops/update.sh claude/sleepy-gates-oecof1
dc run --rm -T api python examples/audit_retention_horizon.py --db "$RWE_DB_URL"
```

Nothing else is owed: M2's boundary work is exercised entirely by unit tests with their own control
arms, and the corpus-boundary bars from M1 still pass unchanged.

---

# What this roadmap does not price

* **The box.** `t3.medium`, 2 vCPU, 0.40 sustainable [M]. Nothing here is achievable on it at 50k
  sources; M3 and M6 both imply moving off it. The roadmap is about which *architecture* survives
  the move, not about pretending the hardware does.
* **Ratings.** Repeated because it is the strategic crux: Tier A is bounded by human or licensed
  rating throughput. No architecture in this document changes that, and the comparables all pay for
  it.
* **Whether 50k sources are worth it.** `SOURCE_COVERAGE_AUDIT.md`'s verdict on *today's* corpus was
  "neither expand nor curate", on the grounds that curating the entire untracked backlog buys 13
  blindspot claims across 1,528 stories. That verdict priced **blindspot claims only**, and said so:
  *"If source diversity has value for its own sake… that is a product value this measurement is
  blind to."* This roadmap is the architecture for a strategic goal that has been set independently
  of that metric. It should not be read as the earlier measurement being overturned — it was not
  re-run — but as the case where its stated blind spot is the whole point.
