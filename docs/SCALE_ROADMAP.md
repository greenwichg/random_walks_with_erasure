# Scaling to a 50,000-source universe — the dependency-ordered path

**Status:** design, with **M1, M2, M5, M7, M8 and M9 built and shipped off** (Parts 7–12) ·
M7's network half is built but **not authorised to run** — the ToS/robots review is outstanding ·
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
| M5 | Shadow ingest lane; amend the corpus contract; extend the guardrail tests | M1 (**M3/M6 did not bind — see Part 9**) | nowhere safe to put candidates before this |
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

# Part 9 — M5 as built: the shadow lane

Shipped **off**, byte-identical, on `claude/sleepy-gates-oecof1`.

## Why M5 came before M3 and M6, which the graph lists as its dependencies

Stated as a deviation rather than taken silently. M3 (storage substrate) and M6 (crawler fan-out)
were listed because a shadow lane needs somewhere to put volume and something to fetch it. **Neither
binds for the first cohort**, because the first cohort is not outside: `ingest_entries` has no
admission gate, so the catalog already carries **3,639 untracked outlet identities** ingested through
GDELT and the adapters. They need no new fetch and no new storage — only somewhere safe to sit while
they are measured, which is exactly what M5 is.

Everything else on the discovery path is downstream of it: M7 has nowhere to put a candidate without
M5, M8 has nothing to evaluate, M9 nothing to promote from. And M7 is independently blocked on a ToS
review and on network egress, so M5 is the furthest the discovery path can advance right now.

## The live defect it closes

`corpus.tier_of` has returned `"shadow"` since M1, documented as *"stored and attributed, surfaced
nowhere, pending evaluation."* **That was false.** The boundary was enforced in
`story_service._fetch` alone, so a shadow outlet was excluded from clustering and **fully
searchable** — shadow and Tier B were the same thing in practice.

## The design decision, and why it is the store's default

Seven reader surfaces funnel through `store.search_feed_articles`: Search, Discover, the two facet
lists, publisher profiles, the coach, and the country facets. Enforcing shadow at each of them is
precisely how it came to be half implemented the first time.

> **`include_shadow=False` is the default on the store, not an opt-in at the caller.**

The store is otherwise policy-free and this is a deliberate exception, taken on the direction its
failure modes point:

| | forgetting the flag means |
|---|---|
| caller opt-**in** | unvetted sources reached readers — **silent** |
| store default-**out** | the evaluation harness cannot see the lane it evaluates — **loud, immediate** |

A new reader surface is therefore safe the day it is written. Evaluation and audit paths pass
`include_shadow=True` deliberately.

**Tier B and shadow are kept separate, and that separation is the tier split.** Tier B is a real
source that does not form stories and **is searchable**; shadow has not been evaluated and is
surfaced nowhere. `sql_exclusions()` (both, for clustering) and `shadow_exclusions()` (shadow only,
for readers) are distinct on purpose — merging them would make Tier B invisible and delete the point
of the tier.

## Bars

| bar | where | status |
|---|---|---|
| a shadow article is stored, absent from Search, and **present** with `include_shadow=True` | unit | ✅ |
| **Tier B stays searchable in the same fixture** — the distinction cannot collapse | unit | ✅ |
| a shadow publisher is not offered as a filter facet | unit | ✅ |
| shadow exclusion is the store **default** on all three catalog readers | structural | ✅ |
| nothing in shadow ⇒ every surface byte-identical, no SQL term added | unit | ✅ |
| both new behavioural bars **fail against pre-M5 code** (verified by reverting the default) | — | ✅ |
| Tier A workload unchanged | by construction — `corpus.select` and the M2 prefilter already dropped shadow before the builder | ✅ |

## What M5 does NOT do

No discovery, no validation, no fetching, no promotion. It builds the lane and proves nothing leaks
out of it. Filling the lane is M7 and needs the ToS review; deciding what leaves it is M8/M9.

`RWE_CORPUS_SHADOW` was already in the compose allowlist from M1, so there is no config change and
nothing to deploy beyond the code.

---

# Part 10 — M8 as built: evaluating a lane you cannot observe

**Landed in `claude/sleepy-gates-oecof1`.** Stage 4 of Part 4, built on the lane M5 opened.

## The problem M5 created, which is the whole of M8

M5's shadow lane is *stored and surfaced nowhere* — that is what makes it safe to point at 50,000
unvetted sources. It is also what makes the metric every earlier audit leaned on unavailable:
**story participation is structurally zero for a shadow outlet, forever**, because shadow rows never
reach the builder. `audit_source_cohort.py` cannot simply be pointed at shadow; it would rank every
outlet at 0% and read as though it had measured something.

So the question has to change shape, from observational to counterfactual:

> **Would this article have joined a story, had it been allowed to?**

That is the same question the clusterer answers. Answering it with a *second* implementation is how
two definitions of "same event" quietly drift apart — and this audit series has already corrected
three key-convention drifts, each of which produced confident, wrong numbers that nothing
contradicted. So the rule was **extracted rather than reimplemented**.

## What landed

| file | what it is |
|---|---|
| `examples/clustering.py` | `pair_admits(tx, ty, time_x, time_y, …)` — the pairwise rule, lifted out of `cluster`'s inner `pair_ok`, which now delegates to it. One definition, and any change to it moves both. |
| `examples/source_evaluation.py` | the policy module: `observed_days`, `freshness_hours`, `assignment_index`, `would_attach`, `assignment_rate`, `evaluate`. Pure — no store, no network, no env, no writes. |
| `examples/audit_shadow_cohort.py` | the runner. Reads the lane with `include_shadow=True`, measures, prints a verdict per outlet. Read-only. |
| `examples/store.py` | `publisher_first_seen` — catalog-wide `MIN(created_at)` per outlet (added by the fix below) |
| `tests/test_source_evaluation.py` | 24 tests |
| `tests/test_audit_shadow_cohort.py` | 13 tests |

`cluster`'s output is **byte-identical** after the extraction, verified on 4,000 synthetic items
across three parameter regimes against `HEAD`: defaults 30 clusters, quorum+idf 338, `min_shared=2`
1 — `identical=True` in all three.

## Why the graph's dependencies did not bind

Part 5 lists M8 after M7 (discovery) and M4 (Tier B assignment). Neither binds, for reasons that
have now recurred often enough to be worth stating as a pattern:

* **M7 (discovery)** — the cohort is already inside. `ingest_entries` has no admission gate, so
  ~4,000 outlet identities are already ingested and never evaluated. Discovery is what fills the
  lane with outlets we have *not* met; it is not what makes the harness runnable.
* **M4 (assignment as a production feature)** — M8 needs assignment as a **measurement**, computed
  offline over a built story set, not as a serving path. `assignment_index` + `would_attach` is that
  measurement in ~20 lines, and it costs the builder nothing because it never runs inside a build.

## ⚠ The first production run found a third trap — in M8 itself

**Run on `0eed1c6`, `--as-if "sportskeeda.com,newsbytesapp.com"`:**

```
Tier A built  : 26,926 articles -> 1,494 stories (6,093 covered)
cohort        : 989 articles
  arts  obs_d  attach  story   synd   host  fresh_h  outlet
   989    6.0      74     35     0%   100%      0.1  sportskeeda.com   INSUFFICIENT DATA
```

`observed 6.0d` is not a fact about sportskeeda. It is **the fetch window, reported as though it
were the outlet's history.** `observed_days` scanned `createdAt` over the rows the runner already
held, and those rows come from `story_service._fetch`, which is bounded to `scan_days()` = **6
days**. So the span could never exceed 6, the 14-day gate could never be satisfied **by any outlet,
ever**, and `INSUFFICIENT DATA` was the only verdict the harness could reach. It printed one clean,
plausible table and told us nothing.

This is the same shape this series keeps finding in its own instruments — Part 4's language
breakdown, `audit_source_cohort`'s membership lookup, `audit_registry_coverage`'s prettify
asymmetry — and it is worth naming: **a gate that cannot fire is worse than no gate, because it
reads as a measurement.** My own note when planning M8 said the observation window was "derivable
from `MIN(created_at)` per publisher"; I then built it from the windowed rows instead.

**The fix**, shipped as a follow-up:

* `store.publisher_first_seen(publishers)` — `MIN(created_at)` per outlet over the **whole
  catalog**, never a window. Bounded now only by *retention*, which is a real ceiling and is stated
  in the output rather than hidden. With age-based retention off (the shipped default) it reaches
  back months.
* `source_evaluation.observed_days(..., since=...)` — the catalog timestamp overrides the row scan.
  The row scan stays for callers that genuinely hold an outlet's whole history.
* `audit_shadow_cohort.observation_is_window_bound(table, scan_days)` — if **no** outlet's span
  exceeds the fetch window, the run says so in the output. False positives are possible and cheap: a
  genuinely new cohort really is younger than the window, and a `first seen` section is printed so
  the reader can tell the two apart.

Verified on a fixture reproducing the production shape: an outlet whose rows all sit inside a 6-day
window but whose catalog first-seen is 40 days back now reports `40.0` and reaches
`PROMOTE TO TIER B` — the gate fires in the pass direction for the first time.

**A second, smaller reporting gap in the same run:** two outlets were named, one matched, and the
output said nothing about the other. `newsbytesapp.com` contributed no rows and the run reported
`--as-if: 2 named outlets` regardless. Unmatched names are now listed explicitly, with the note that
everything below describes only what matched.

## The fixed run, and the third defect it exposed — in the *vocabulary*

**Run on `01ead25`, same command:**

```
Tier A built  : 26,917 articles -> 1,495 stories (6,095 covered)
cohort        : 999 articles
*** 1 NAMED OUTLET(S) MATCHED NOTHING: newsbytesapp.com
  arts  obs_d  attach  story  synd  host  fresh_h  outlet
   999   28.1      75     35    0%  100%      0.1  sportskeeda.com   PROMOTE TO TIER B
first seen: 2026-07-29T03:47:53
```

`observed 28.1d` against a first-seen of `2026-07-29T03:47:53` and a run time of ≈`2026-08-26T06:10`
— 28 days, 2 hours. **The gate fired in the pass direction on production for the first time.** The
fix works.

And the verdict it produced is wrong in a way no number can show. **`sportskeeda.com` is in Tier A
today**, by grandfathering. `PROMOTE TO TIER B` against a Tier A outlet is a **demotion wearing the
word "promote"**. The vocabulary was written for the shadow lane, where shadow is the bottom and
every move is upward — so "promote to Tier B" can only mean one thing. `--as-if` breaks that
assumption, because it evaluates outlets we *already carry*, and against Tier A the same phrase
points the other way. A reader acting on it would move an outlet the opposite of what the evidence
supports.

Fixed by computing the direction against where the outlet actually **is** — `corpus.tier_of` — and
printing it beside the verdict:

```
  arts  obs_d  attach  ...  now  outlet
    12   40.0       0  ...    A  vertical.example  PROMOTE TO TIER B  [*** DOWN from A — this is a DEMOTION ***]
```

`source_evaluation.evaluate` stays **tier-blind** and its tests are unchanged: it scores an outlet,
it does not know where one is. The direction lives in the runner, which does.

**Two smaller instrument gaps closed in the same pass:**

* **The retention floor.** `MIN(created_at)` is bounded by what has not been trimmed, so an outlet
  sitting *at* the catalog's oldest surviving row has not been observed for that long — its true
  first-seen is unknowable from what we hold. `store.catalog_first_seen()` reports the floor and the
  run marks any outlet pinned to it: *"span is a lower bound"*. Reading a floor-pinned span as an
  observation would be the same error as reading the fetch window as one.
* **Unmatched names now say WHICH cause.** `newsbytesapp.com` matched nothing, and "published
  nothing in the window" and "the name is not the identity we join on" are different problems. The
  catalog knows whether it has ever held the string, so the run says which.

## The third run: both open facts settled, and a drift I cannot yet explain

**Run on `4ae0549`, same command.** The direction annotation printed as designed:

```
  arts  obs_d  attach  story  synd  host  fresh_h   now  outlet
   999   28.1      75     35    0%  100%      0.1     A  sportskeeda.com  PROMOTE TO TIER B
                                                              [*** DOWN from A — this is a DEMOTION ***]
retention floor (oldest surviving row in the catalog): 2026-07-20T21:07:55
  sportskeeda.com  2026-07-29T04:38:01
```

**Settled #1 — the span is real.** sportskeeda's first-seen sits **8 days 7.5 hours above** the
retention floor, so it is not floor-pinned and `28.1d` is a genuine observation rather than a lower
bound.

**Settled #2 — `newsbytesapp.com` is not in the catalog** under that string. It was never a measured
candidate; it came from this document's own example command. Worth stating plainly, because an
example string that reads like a finding is how a guess becomes a fact.

**Not settled — the first-seen moved.** Between the two runs, 17m 43s apart on the wall clock,
sportskeeda's first-seen advanced **50 minutes** (`03:47:53` → `04:38:01`). Something removed rows,
or the query saw a different set. Two candidates, and I could only rule on one:

* **A bug I could see by inspection, now fixed.** The publisher strings looked up were gathered from
  the *fetched rows*. An outlet arriving under several spellings therefore contributed only the
  spellings the last 6 days happened to contain, so a variant ageing out of the window moves its
  "first seen" with nothing about its history having changed — the same shape as deriving the span
  from the window, one level down. `identity_first_seen` now asks the **catalog** which strings
  belong to the identity.
* **Retention erosion**, which I cannot confirm from the output I have. It would mean the oldest
  rows of a high-volume outlet are being trimmed, so its observable history shrinks from below while
  the global floor stays put. The run now prints each outlet's **whole-catalog row count beside the
  window count**, so a second run answers it directly: first-seen advancing while `catalog` falls is
  erosion; first-seen stable is the spelling bug that is now fixed.

The honest status: **the 50-minute drift is 0.12% of a 28-day span and changes no verdict here**,
but a first-seen that can move for reasons other than the outlet's history is the same defect class
as the two already corrected in this Part, and it now has an instrument pointed at it instead of an
explanation.

## The fourth run: the drift resolved, and the history is intact

**Run on `8bcf58c`, `--as-if "sportskeeda.com"`:**

```
   window  catalog  first seen                   outlet
      999    4,911  2026-07-29T04:38:01.236987   sportskeeda.com
retention floor: 2026-07-20T21:07:55.297873
```

**The spelling bug was not the cause.** Run 3 (windowed lookup) and run 4 (identity-resolved from
the catalog) report the same first-seen to the microsecond — `04:38:01.236987`. sportskeeda arrives
under one spelling, so the two methods agree, and the fix changed nothing for *this* outlet. It was
still worth making: the mechanism it removes is real, it just was not what happened here.

**So rows really were deleted** between run 2 and run 3, and **nothing was deleted** between run 3
and run 4 — first-seen and floor both byte-identical over that 18.5-minute interval.

**The mechanism, verified in the code rather than guessed:** `corpus_health.plan_retention` orders
prune candidates by **`publishedAt`** (falling back to `fetchedAt`); `publisher_first_seen` measures
**`created_at`**. Different orderings. So retention can remove the rows carrying an outlet's oldest
`created_at` while the catalog's *global* `MIN(created_at)` never moves — which is exactly the
pattern observed. What remains unverifiable from here is whether retention is switched on at all:
`RWE_RETENTION_MAX_COUNT` defaults to `0` and its value lives in `deploy/.env`.

**And the history is intact, which the new column settles directly.** 999 window articles are
**20.3%** of the 4,911 the catalog holds, while 6 days are **21.4%** of the 28.1-day span — a
1.1-point difference. Uniform publishing over the whole span would predict 4,679 catalog rows and
there are 4,911, i.e. **5% more history than a flat rate implies**, not less. The rates agree too:
174.8/day over the catalog, 166.5/day over the window. There is no erosion signal. The 50-minute
jump was a marginal trim at the very oldest edge, not a shrinking observation.

**Consequence for the instrument, now stated in `publisher_first_seen`:** a floor comparison shows
whether an outlet is *pinned* to the oldest surviving row; it does **not** prove the outlet's own
history is untrimmed, because the two use different columns. The whole-catalog row count beside it
is what answers that, and it is why the column earns its place.

## What the run does say, now that the numbers can be read

The four measurements that were **not** window-bound stand, and they were **stable across four
consecutive production runs**: 0% syndication, 100% host stability, 0.1h median fetch lag, ~999
articles per 6-day window (~166/day, and 174.8/day over the full 28-day catalog history). That is an
original publisher on its own domain, polled fast — not a republisher, and not a redirect farm. The
two `REJECT` criteria correctly do not fire.

Its attach rate is **75 of 999 (7.5%)**, touching 35 of ~1,497 stories. Stated as capacity: it is
**3.7% of the Tier A corpus** contributing **1.2% of coverage**. That is the profile of a vertical —
high volume, genuine reporting, low overlap with the general-news spine.

**This does not license a demotion, and the discipline is the whole point.** Attach rate is not a
gate, by a rule adopted *before* this number existed and for reasons that survive it. Nor does
capacity bind: 26,917 against the 83,000 Tier A budget means 3.7% is not scarce. And the two
criteria that *do* demote — syndication and host instability, the only two `audit_source_cohort`
ever let act — both pass cleanly at 0% and 100%.

**So the action this run supports is: none.** `PROMOTE TO TIER B` is evidence that sportskeeda
*would qualify for Tier B if it were in shadow*; it is not a finding that it should leave Tier A,
and the direction annotation now says so in the output rather than leaving it to the reader. What
the number is good for is the *next* question — whether a vertical belongs in a general-news
clustering corpus at all — and that is a product question with a counterfactual attached, not
something this table decides.

## The two traps found before the first run, and the guards that are in the code rather than in this document

**1. Self-scoring.** If the assignment index contains the cohort's own coverage, every article
attaches to itself and the rate is ~100% *by construction* — a number that looks like a strong
result and measures nothing. Shadow mode cannot hit it; `--as-if` is one forgotten rebuild away.
`self_scored(cohort, stories)` counts it and the run **refuses to report** when it is non-zero.

**2. Syndication measured against the wrong population.** A shadow outlet's syndication partner is
almost always a Tier A masthead it is republishing. Count carriers within the cohort alone and a
lone republisher scores **0%** — precisely the outlet the ceiling exists to catch. `carrier_index`
takes the Tier A corpus *and* the cohort, and a test pins the difference (1 carrier vs 2).

## `--as-if`: the harness is runnable today, on real data, with nothing in shadow

`--as-if "outlet,outlet"` evaluates outlets we **already carry in Tier A** as though they were in
shadow, rebuilding the Tier A story set without them first. Same de-risking order `audit_source_cohort`
used: exercise the evaluation stage on real data with zero crawl, zero ToS exposure and zero new
code in the serving path, *before* pointing it at a source we have never met. On a seeded fixture
the two modes agree exactly on the same outlet, which is the cross-check worth having.

## What M8 deliberately does NOT gate on

**`assignment_rate` is reported and never gated.** No bar for it has been measured. The temptation
is to pick one — "promote above 20%" — and this audit series has now had **two invented thresholds
die against data**: participation as a quality proxy (its list was full of real newsrooms), then
peer count as its excuse (refuted at `en` 214 peers → 27% vs `vi` 6 peers → 30%). A third guess
would be a worse mistake for having watched the first two fail. `test_no_gate_reads_the_assignment_fields`
makes this structural rather than intentional: `evaluate` must return the same verdict for an outlet
that would attach everywhere and one that would attach nowhere, every other input held equal.

**Tier A promotion is not decided here either.** It needs the clustering counterfactual on the
production bars — a whole-corpus measurement, not a per-outlet one. `evaluate` returns
`TIER A CANDIDATE` and names the run that would settle it.

## The verdicts

| verdict | when | note |
|---|---|---|
| `INSUFFICIENT DATA` | observed < 14 days | **not a rejection** — the safe direction. An outlet seen for three days has told us nothing. |
| `INSUFFICIENT VOLUME` | < 10 articles in the window | the measured floor: 3,442 of 4,083 identities sit below it with a *median of one article*. |
| `REJECT` | syndication > 35%, or host stability < 50% | the two language-independent defects. Read **before** the promoting facts, so a syndicator that also attaches everywhere reads as a republisher — its attachments are other publishers' coverage counted twice. |
| `TIER A CANDIDATE` | passes, and carries a lean | evidence, not a promotion. |
| `PROMOTE TO TIER B` | passes, unrated | needs no counterfactual: a Tier B row cannot alter the partition. **This is the asymmetry that scales to 50,000.** |

## Bars

| bar | where | status |
|---|---|---|
| `cluster` output byte-identical after extracting `pair_admits` | 4,000 items × 3 regimes vs `HEAD` | ✅ |
| `would_attach` agrees with `clustering.cluster` on attach **and** no-attach cases | unit, against the clusterer itself | ✅ |
| `would_attach` is deterministic across runs on identical input | unit | ✅ |
| the inverted index is exact, not approximate — pinned against a brute-force scan | unit | ✅ |
| `observed_days` reads `createdAt`, never `publishedAt` | unit | ✅ |
| **observation comes from the catalog, not the fetch window** — the same outlet is `INSUFFICIENT DATA` on the windowed span and reaches a real verdict on the catalog one | unit, both seams | ✅ (after the first production run) |
| a window-bound observation is **detected and reported** | unit + fixture run | ✅ |
| **a verdict is read against the outlet's CURRENT tier** — `PROMOTE TO TIER B` on a Tier A outlet prints as a demotion | unit (7 cases) + fixture run | ✅ (after the second production run) |
| an `INSUFFICIENT *` verdict carries **no** direction — it is not an instruction | unit | ✅ |
| an outlet pinned to the **retention floor** is marked as a lower bound | unit + fixture run | ✅ |
| **first-seen resolves the identity's spellings from the CATALOG**, not from the windowed rows | unit | ✅ (after the third production run) |
| whole-catalog row count printed beside the window count, so retention erosion is visible | fixture run | ✅ |
| `--as-if` names that matched nothing are listed, **with which cause** | fixture run | ✅ |
| too-new ⇒ `INSUFFICIENT DATA`, never `REJECT` | unit | ✅ |
| no verdict branches on `assignmentRate` / `assignmentStories` / `attached` | structural, both modules | ✅ |
| syndication sees the Tier A corpus, not the cohort alone | unit (1 carrier vs 2) | ✅ |
| self-scoring is detected and the run refuses to report | unit + manual sabotage run (exit 1) | ✅ |
| the policy module imports no store, network or `os` | AST, not text | ✅ |
| Tier A workload unchanged | by construction — the runner is read-only and never runs inside a build | ✅ |

## What M8 does NOT do

It does not act. Every verdict is evidence; moving an outlet between lanes or retiring it is **M9**,
and it is not built. It also does not fill the lane — that is M7, still blocked on the ToS review
and on network egress.

---

# Part 11 — M9 as built: acting on the evidence, without touching the serving path

**Landed in `claude/sleepy-gates-oecof1`.** Stages 5 and 6 of Part 4, built on M8's measurements.

## The design question M9 had to answer first

M8 stops at evidence deliberately. M9 acts — and the first thing to establish is *what "act" can
safely mean here*, because *tier membership is not database state.* It is `RWE_CORPUS_TIER_B` and
`RWE_CORPUS_SHADOW`, read from the environment by `corpus.tier_index`. That was M1's decision: tier
is a property of the outlet, derived at selection time, no article column and no migration.

So "automate promotion" can only mean one of two things: introduce a second, competing source of
truth for tiering, or **automate the decision and emit the configuration**. M9 does the second.

1. The roadmap already says Tier A promotion is *"gated, manual, and permanently narrow"* — bounded
   by rating throughput, which is a budget and not an algorithm. A pipeline that promoted into Tier
   A by itself would contradict the milestone it implements.
2. Every crossing of the Tier A boundary changes the story partition, the one thing this repo never
   changes without a counterfactual.
3. Applying it is a deploy either way, since the value lives in the compose allowlist. Emitting it
   costs nothing and keeps a human in the loop for free.

## The rule the whole automatic/manual split reduces to

> **Tier A's boundary is the only one that moves the partition, so every crossing of it — in either
> direction — needs the whole-corpus counterfactual and a human. Everything else is automatic.**

That is the same asymmetry the roadmap names as what makes 50,000 sources possible: a Tier B row
cannot alter what clusters, so admitting one needs no clustering bar. `crosses_tier_a` is that rule
in one line, and every non-automatic transition traces to it. **The demotion direction matters as
much as the promotion one** and is the half nobody thinks about: removing an outlet can strand
articles whose only link ran through it — the bar `audit_source_cohort` reports as *"OTHER articles
that LOST their story"*.

**The one exception is provable rather than argued.** An outlet silent longer than the clustering
window has no rows in the window, so removing it from Tier A *cannot* change the build — there is
nothing of it there to remove. Dormancy from Tier A is therefore automatic, and `plan` refuses to
apply that reasoning unless `silent_days` genuinely exceeds the window (a test pins the refusal).

## What landed

| file | what it is |
|---|---|
| `examples/source_lifecycle.py` | the pure state machine: `STATES`, `crosses_tier_a`, `target_for`, `plan`. No store, no env, no writes. |
| `examples/store.py` | `SourceLifecycle` (current state) + `SourceLifecycleEvent` (append-only ledger), `record_source_evaluation`, `apply_source_transition`, `publisher_last_seen` |
| `examples/audit_source_lifecycle.py` | the runner: evaluates via M8, records the ledger, emits the config diff. `--commit` writes the **ledger**, never the configuration. |
| `examples/audit_shadow_cohort.py` | `measure()` extracted, so M9 uses M8's numbers rather than its own |
| `tests/test_source_lifecycle.py` | 40 tests |
| `tests/test_audit_source_lifecycle.py` | 12 tests |

## Hysteresis: why two, and why it is not a threshold

A transition requires the same target on `confirmations` consecutive evaluations. This series has
had **two invented thresholds die against data**, so it is worth being precise that this is not a
third: it is a *repeat-measurement* rule, not a quality bar. It asserts nothing about how good an
outlet is; it asserts that one sample is one sample.

The default of 2 is the smallest value that means anything, and it is the argument
`clustering.DEFAULT_MIN_SUPPORT` already makes in this codebase — one witness is an anecdote, two is
corroboration. It costs nothing where the evidence is stable, and M8's production verdicts were
identical across four consecutive runs.

## ⚠ The first production run found the flaw in that argument

**Run on `3dcbe8b`, `--as-if "sportskeeda.com"`.** The output was exactly as designed — `WAITING` at
1 of 2, empty config diff, a Tier A outlet so nothing could move automatically. What it made visible
is that **the streak advances per committed evaluation, not per independent sample.**

The cohort is the last `scan_days` of articles. Run the harness twice in the same minute and the two
runs share essentially every row, so the second confirms nothing — yet it would have incremented the
streak. **My own end-to-end verification of M9 did precisely that**: two `--commit` runs seconds
apart, and the transition fired. M8's production history shows it from the other side — four runs
within the hour reporting near-identical numbers, which would have counted as four confirmations of
a single measurement.

So "one witness is an anecdote, two is corroboration" was right, and my implementation was counting
the *same* witness twice.

**The fix, and the interval is derived rather than picked.** `next_streak` requires
`min_spacing_days` between samples that count, defaulting to **one full clustering window**: after
`scan_days` the two cohorts share no article, which is exactly the point at which the samples become
independent. Anything shorter would be a fraction somebody chose.

A too-soon evaluation **holds** the streak — neither incrementing nor resetting. Resetting would be
wrong (the verdict did not change; we simply learned nothing new), and incrementing is the bug. The
run marks it, so a streak that failed to move never does so silently:

```
  arts  obs_d  silent     state   now  streak  outlet
    12   40.0      0d    shadow shadow      1*  vertical.example   PROMOTE TO TIER B

  * HELD: evaluated again less than 6d after the last one. The streak neither advanced nor
    reset — the verdict did not change, we simply learned nothing new.
```

A **changed** target still resets immediately, however soon it arrives: spacing governs
corroboration, not contradiction. An outlet that just flipped to `REJECT` must not keep a promotion
streak because the sample was early.

The arithmetic lives in `source_lifecycle.next_streak` and **both** the store and the runner's
dry-run path call it. Two copies is how the four drifted definitions in this series started.

**A second, smaller gap in the same run:** `state A` read identically whether the ledger recorded
"A" or had never seen the outlet at all — different facts. A state the ledger does not know is now
printed in `(parentheses)`.

## Why the ledger is a table and not an env var

Two columns earn it on their own:

* **`first_observed`, pinned and only ever moved earlier.** `observed_days` derives from
  `MIN(created_at)`, which retention erodes — Part 10 measured sportskeeda's apparent history
  advancing 50 minutes in 18 minutes of wall clock. An observation window that shortens on its own
  would let a long-observed outlet fall back below the 14-day gate. Once seen, the date is kept.
* **`streak`.** Hysteresis needs memory across runs, and a run is a fresh process.

And the event log is append-only because Stage 6 already said why: *"A retirement that deletes
evidence cannot be audited later, and the recurring lesson in `PERFORMANCE.md` is that the expensive
failures are the ones where the evidence was gone."* Each event carries the evidence snapshot that
justified it, so a decision can be re-read against the numbers it was actually made on — not
today's, which will have changed.

**A transition is recorded with `applied=False`.** It is a *decision*, not a claim about what the
running system is doing. The two are kept apart on purpose, so the ledger can show a decision that
was proposed and never shipped.

## Retirement is not automatic, and that is a refusal to guess

Stage 6 says *"no items in 30 days → dormant (daily probe), then retired"* and gives **no interval
for the second arrow**. Dormancy is reversible, evidence-preserving and harmless, so it is
automatic. Retirement is neither reversible nor measured, so it stays a human action that M9 records
and never initiates. Inventing "then retired after N" would be the third guess.

`dormant` and `retired` are also **ledger-only today** — the probe-cadence change they imply belongs
to the crawler (M6/M7), which is not built. They are recorded now so the evidence exists when there
is something to consume it, and `config_diff` puts them in neither serving list so they cannot
silently mean "Tier A" by omission.

## Verified end to end on a fixture

```
run 1  shadow  streak 1   WAITING     points at B, but on 1 of 2 consecutive evaluations
run 2  shadow  streak 2   AUTOMATIC   shadow -> B; neither side of this move clusters

    RWE_CORPUS_SHADOW=
    RWE_CORPUS_TIER_B=vertical.example
```

and the Tier A case held, as it must:

```
  NEEDS A HUMAN   vertical.example   A -> B
      PROMOTE TO TIER B confirmed on 2 consecutive evaluations, but this moves out of
      the clustering corpus and changes the story partition
      requires: clustering counterfactual (audit_clustering_change.py)
```

## Bars

| bar | where | status |
|---|---|---|
| every Tier A crossing, **both directions**, is non-automatic and names what would unblock it | unit | ✅ |
| shadow → B and B → shadow are automatic — neither side clusters | unit | ✅ |
| promotion into Tier A without a lean names **both** blockers, not just the first | unit | ✅ |
| dormancy from Tier A is automatic **only** when the interval exceeds the clustering window | unit | ✅ |
| a dormant outlet that resumes re-enters **evaluation**, not its old tier | unit | ✅ |
| retirement is never reached automatically | unit | ✅ |
| `INSUFFICIENT *` produces no transition in any state | unit (12 cases) | ✅ |
| an unknown state raises rather than defaulting | unit | ✅ |
| `streak` resets when the target changes — two different targets never confirm | unit | ✅ |
| **two evaluations inside one clustering window are one sample** — the streak holds, does not advance | unit + store + fixture run | ✅ (after the first production run) |
| a held sample does not RESET either; only a different answer does | unit | ✅ |
| a first evaluation is never held — no previous sample means no interval to judge | unit + **production** | ✅ |
| the store and the dry-run path share **one** streak implementation | unit on both seams | ✅ |
| a state the ledger does not know prints in `(parentheses)` | fixture + **production** | ✅ |

**Verified on production, `9ec2d6e`** (`--as-if "sportskeeda.com"`): `(A)` for an outlet the ledger
has never recorded, `streak 1` with no hold marker, `WAITING` at 1 of 2, and an empty config diff —
a Tier A outlet, so nothing about it can move automatically. Of the six production runs across M8
and M9, four exposed a defect in the instrument itself; this one exposed none.

**And M9, like M8, has no live subject yet.** The automatic path is shadow → B, and
`RWE_CORPUS_SHADOW` is unset, so nothing on production can currently reach it. Filling the lane is
M7 — still blocked on the ToS review and network egress. Until then `--as-if` is the only mode with
anything to evaluate, and every verdict it reaches crosses the Tier A boundary and therefore stops
at a human by design.
| `first_observed` only ever moves **earlier** | unit | ✅ |
| events are append-only and keep the evidence of superseded decisions | unit | ✅ |
| a promotion out of shadow **removes** from `RWE_CORPUS_SHADOW` as well as adding to `TIER_B` | unit | ✅ |
| a run with nothing to do emits **no** diff | unit | ✅ |
| the emitted value is stable under reordering | unit | ✅ |
| the runner opens no file, imports no `subprocess`, writes no `os.environ` | AST, not text | ✅ |
| the runner measures via `asc.measure` and re-derives none of M8's numbers | structural | ✅ |
| Tier A workload unchanged | by construction — read-only, never runs inside a build | ✅ |

## What M9 does NOT do

It does not change configuration, restart anything, or touch the serving path. It does not fill the
shadow lane — that is M7, still blocked on the ToS review and network egress. And it does not
promote anything into Tier A, which remains bounded by rating throughput rather than by code.

---

# Part 12 — M7 as built: discovery offline, validation behind a gate

**Landed in `claude/sleepy-gates-oecof1`.** Stages 1 and 2 of Part 4.

M7 is *"the first thing in the entire roadmap that touches a publisher"*, and the milestone splits
cleanly along that line. **Stage 1 needs no network at all** and is runnable today. **Stage 2 is the
network half** and is built but does not run without an explicit flag — and the ToS/robots review
the roadmap asks for is a human task that is still outstanding.

## The safety property is structural, not a convention

`source_validation` has **no fetcher of its own**. `validate(cand, fetch=None)` is the signature, and
`fetch` is never defaulted or constructed inside the module. A run without one executes the three
offline gates and reports every network gate as `UNKNOWN` — never `PASS`.

The alternative — a default fetcher disabled by `--dry-run` — puts the whole ToS question behind
somebody remembering to pass a flag. Here an offline run **cannot** be mistaken for a validated one,
because there is nothing for it to call. And `UNKNOWN` rather than `PASS` matters for the same
reason it did in M8: claiming a publisher's robots.txt permits us *without having read it* is the
exact shape of error this series keeps finding in its own instruments.

## What landed

| file | what it is |
|---|---|
| `examples/source_discovery.py` | Stage 1 + gates 6/7/8. Pure — no store, no network, no env. |
| `examples/source_validation.py` | Stage 2, gates 1–5. `fetch` injected and never defaulted. |
| `examples/audit_source_discovery.py` | the runner. Offline by default; `--probe` for the network pass. |
| `tests/test_source_discovery.py` | 27 tests |

Reused rather than rebuilt: `crawler.RobotsPolicy` (already fail-closed), `crawler.RateLimiter`
(per host, because the limit protects a *server*), `crawler.discover_rss` (= `rss_ingest.parse_feed`
verbatim) and `crawler._fetch_text` (the shared 429/5xx retry budget). M7 adds a way to **find** a
feed; it does not add a second way to read one. Four drifted definitions have been corrected in this
series — a second robots parser would be the fifth.

## The cheap gates run first, and that is a politeness decision

Three of the eight gates are answerable offline, so they run **before** any host is probed:

| gate | answered |
|---|---|
| 6 language identified · 7 not already tracked · 8 not an aggregator/proxy | offline, from catalog evidence |
| 1 robots · 2 feed discoverable · 3 ≥10 items · 4 ≥80% dated · 5 URLs on host | network |

**Every request not made is ToS exposure not incurred.** A candidate an offline gate rejects is
never probed — spending a publisher's bandwidth to confirm a decision that is already made is both
rude and pointless, and a test pins that zero requests are issued in that case.

The run also **prices Stage 2 before it is authorised**: hosts × 2 requests × the politeness
interval, printed in the offline run. That number is what a ToS review is actually asking about, so
it belongs in front of a human rather than in a post-hoc report.

## Gate 8 asks the registry first — and the split is measured

The registry resolves `news.google.com` → **`Google News kind=aggregator`**, and knows *none* of
`apple.news`, `flipboard.com`, `msn.com`, `substack.com`. So `EXCLUDED_KINDS` covers the outlets the
registry has and `PROXY_HOSTS` covers the ones it does not — **which is precisely the population
discovery works on.** Asking the registry first also means a curated `kind` correction improves this
gate instead of being shadowed by a hard-coded list.

A host that is both a proxy *and* tracked reports the **proxy** reason: "already tracked" would
suggest we carry `news.google.com` as a publisher, when the point is that its articles are other
publishers'. Same ordering principle `source_evaluation.evaluate` uses — the disqualifying fact
before the procedural one.

The gate exists because of a measured failure: the outlet-resolution counterfactual found **996 of
1,246** newly-attributed articles landing on "Google News" from `10tv.com @ news.google.com`,
`12news.com @ news.google.com` — real local broadcasters proxied through one host.

## Gate 4 is the one that proves Stage 2 has to be online

*"≥ 80% of items carry a publication date"* **cannot be asked offline**: `_fetch` is time-windowed,
so every catalog row has a date by construction and an offline probe would report zero rejections
whatever the feeds actually serve. A gate that cannot fail is not a gate. A test drives a feed of
undated items through the real parser and asserts gate 4 **fails**, which is the only way to know
the gate is load-bearing rather than decorative.

## What is NOT verified, and cannot be from here

**This session's egress gateway refuses CONNECT for arbitrary hosts** — confirmed against IANA's
reserved `example.com`, which returned `403 Forbidden` from the proxy, the same block that stopped
the MBFC and AllSides fetches earlier. So the live transport is unexercised here **by construction**.

What that leaves:

* every probe *decision path* is covered by tests with an injected fetch — ADMIT, robots refusal,
  an absent robots policy, gates 3/4/5 failing, no advertised feed, and per-host rate limiting;
* `crawler._fetch_text` itself is unchanged and pre-existing;
* whether it works against a real publisher is **untested**, and `CRAWLER_DESIGN.md` already records
  that no live crawl has ever run. Production has different egress (RSS ingestion works there), so
  `--probe` will probably work on the box — *probably* is the honest word, and the first `--probe`
  run is what settles it.

## ⚠ The first production run: the numbers held, two columns did not

**Run on `3452fd1`**, offline, against a 27,963-article window:

```
hosts seen    : 4,007
  already tracked by the registry : 524
  aggregator / proxy hosts        :  24
  below the 10-article floor      : 3,285
  CANDIDATES                      : 174     -> 348 requests, 11.6 minutes
```

The census sums exactly (524 + 24 + 3,285 + 174 = 4,007), and **174 candidates at 11.6 minutes
tracks the roadmap's estimate of 151 hosts and ~10 minutes.** The Stage 1 design holds.

Two defects in the *reporting*, both of the kind this series keeps finding:

**1. The `dated` column read 100% for all 30 candidates — by construction.** `story_service._fetch`
filters `published_at >= date_from`, so an undated row *cannot* be in the window. A column that can
only ever hold one value is not a measurement, and printing it beside real ones invites reading it
as one. My own module docstring had already said gate 4 "cannot be answered offline"; I printed the
offline version anyway. The runner no longer prints it, `datedShare` carries a warning where it is
computed, and a test pins both the 1.0-by-construction case and the unwindowed case where it is real
data.

**2. Gate 6 rejected real publishers for a gap in *our* metadata — and did it silently.** `goal.com`,
`vietnamnet.vn`, `gujaratsamachar.com` and `v6velugu.com` showed `lang ?`, and gate 6 read an absent
language as `FAIL`. Two consequences: the `why` column said *"no offline gate rejects it"* for hosts
an offline gate would reject, and — because a candidate with a failed offline gate is never probed —
the run would have promised 348 requests and quietly made fewer.

The underlying error is that `language` measures **our ingestion metadata, not the source**:
`rss_ingest.parse_feed` **discards channel language entirely**, so `FeedEntry.language` is populated
only by the non-RSS adapters and every RSS-sourced row carries none. `audit_source_cohort` had
already abandoned an entire analysis over this same sparsity — *"language known for N of M outlets …
TOO SPARSE TO CONCLUDE"* — and I reintroduced it as a gate.

Gate 6 now reports `UNKNOWN` offline and the **feed settles it**: `source_validation.feed_language`
reads RSS `<language>` / Atom `xml:lang`, stopping at the first item so a per-item language is never
mistaken for the feed's own. Once the feed has been read and still states nothing, the question *has*
been asked and `FAIL` is honest.

> **A better fix exists and is deliberately not taken here.** Teaching `rss_ingest.parse_feed` to
> return channel language would populate `language` for every RSS row in the catalog, not just for
> validation — closing the gap at its source. That changes a production ingestion path for a
> validation-only need, so it wants its own change and its own measurement.

## Bars

| bar | where | status |
|---|---|---|
| an offline run reports every network gate `UNKNOWN`, never `PASS`, and never `ADMIT` | unit | ✅ |
| an absent language is `UNKNOWN`, not a rejection, and does **not** skip the probe | unit | ✅ (after the first production run) |
| the feed's own `<language>` / `xml:lang` settles gate 6, stopping before the items | unit (4 cases) | ✅ |
| a source neither we nor the feed can place **does** fail gate 6 | unit | ✅ |
| `datedShare` is 1.0 by construction for a windowed fetch, real for an unwindowed one | unit | ✅ |
| the runner prints no `dated` column | structural | ✅ |

**Both fixes verified on production, `2ab7d60`:** 4,012 hosts = 524 tracked + 24 proxy + 3,291
below-floor + **173 candidates** (346 requests, 11.5 min), census summing exactly, no `dated` column,
and the `?` rows now honestly labelled — gate 6 defers to the feed, so the priced request count
covers exactly the hosts that will be probed.

Two things the run confirms about the design rather than about the code:

* **`theportugalnews.com` is recorded as `ar`** — an English-language paper tagged Arabic. That is
  visible evidence the catalog's `language` is *unreliable*, not merely sparse, which is precisely
  why gate 6 defers to the feed instead of trusting it.
* **Gate 7 is per-masthead, and that is load-bearing.** `navbharattimes.indiatimes.com` is proposed
  as a candidate while `timesofindia.indiatimes.com` resolves to The Times of India (lean 1.0) and
  the bare `indiatimes.com` resolves to nothing. That is correct — Navbharat Times is the Times
  Group's *Hindi* masthead, a different outlet sharing a domain, and our catalog independently
  recorded it as `hi`. Registering the parent domain to "tidy up" would silently apply an English
  paper's lean to a Hindi one. The right treatment is its own registry row.
| `validate` has no default fetcher and constructs none | structural (signature + source) | ✅ |
| a candidate rejected by an offline gate costs **zero** requests | unit | ✅ |
| robots refusal stops before the landing page and feed are fetched | unit (call list) | ✅ |
| an absent/HTML robots.txt is a **refusal**, not permission | unit | ✅ |
| gate 4 can actually FAIL on undated items — through the real feed parser | unit | ✅ |
| gate 5 rejects a feed whose articles live on another host | unit | ✅ |
| feed autodiscovery never leaves the declared host | unit | ✅ |
| the probe waits between requests to one host | unit (injected clock/sleep) | ✅ |
| gate 8 catches proxies **and** their subdomains, without substring over-matching | unit | ✅ |
| the registry/static-list split is asserted against the real registry | unit | ✅ |
| candidates group by HOST, so one outlet's name variants are one candidate | unit | ✅ |
| the census counts every rejection reason | unit | ✅ |
| Stage 2's cost is printed **before** authorisation, from eligible hosts only | unit + run | ✅ |
| live transport against a real publisher | — | ❌ **egress-blocked here; first `--probe` settles it** |

## Follow-up: the language gap closed at its source

Part 12 named a better fix and deferred it. It is now done, and it turned out to be larger than a
validation detail.

**Neither `_rss_item` nor `_atom_entry` ever set `language`.** So *every RSS-ingested article in the
catalog carries NULL*, and every language value present comes from the GDELT and NewsAPI adapters,
which supply their own per item. That single omission explains three separate things this audit
series has run into:

* `audit_source_cohort` abandoning a whole analysis — *"language known for N of M outlets above the
  floor … TOO SPARSE TO CONCLUDE"*;
* M7's discovery table showing `?` against `goal.com`, `vietnamnet.vn`, `gujaratsamachar.com`;
* `theportugalnews.com` recorded as `ar` — an English paper tagged Arabic, because the only value
  available was GDELT's.

`parse_feed` now fills each entry's language from the feed's own `<language>` / `xml:lang`, with
entry-level winning (correct XML inheritance — the nearest declaration governs). Only the *channel*
element is consulted for the feed's language: treating an item's own `<language>` as the feed's
would let one translated article relabel the entire source. `source_validation.feed_language` became
a lookup on the parsed entries instead of a second parser.

### The live behaviour this changed, and the guard it needed

`coverage_comparison` is **on by default** and has two language-dependent behaviours that were
**near-inert only because the data was missing**:

* `gate()` refusing with `cross_language` when an article's language differs from the coverage's
  majority;
* the `only_<lang>` finding — *"the only report in this language in the coverage set"*.

Populating language is forward-only, so for as long as the retention window a story mixes
language-bearing new rows with NULL old ones — and **a "majority" computed over that biased subset
is actively wrong.** Measured on a fixture: two of six members carrying a language (one German, one
French) makes *French* the majority language of a story that is four-fifths English, and an English
article is refused against it.

`_LANGUAGE_COVERAGE = 0.5` closes it: the majority language may only decide when it was computed
from a majority of the coverage. That is not an invented threshold — it is the definition of the
word the code already uses. You cannot call something *the majority language of this coverage*
having looked at only a minority of it. Below the bar the gate declines to refuse, which is the
direction that shows a comparison rather than withholding one on evidence we do not have — the same
fail-honest rule the rest of this repo applies to an absent measurement.

Both guard tests were **verified to fail with the guard disabled** — the first drafts of them passed
either way (one asserted on a payload key that does not exist, `unique` instead of `uniqueHere`; the
other never reached the branch because it had only one known language). A guard test that cannot
fail is the same defect as a gate that cannot fire.

`audit_source_discovery` now prints **language coverage by source type**, so the transition is
watched rather than assumed.

### ⚠ Measured on production, and it corrected my own claim

**Run on `790736d`**, over a 27,932-article window:

```
  source          known     rows   share
  currents        9,023    9,023   100%
  newsdata        4,170    4,664    89%
  gnews               0    4,562     0%
  googlenews      3,787    3,787   100%
  rss               389    3,327    12%
  gdelt             989      989   100%
  guardian          932      932   100%
  newsapi             0      648     0%
```

**The fix is not forward-only, as I had said it was.** `rss` reached **12% within minutes** of the
deploy — far more than new ingestion could explain at ~554 RSS rows/day. The mechanism is
`store.upsert_feed_article`'s `if language and not row.language`: **a re-poll backfills an empty
field**, so every poll cycle fills in the articles a feed is still serving, not just new ones.

That changes the expected shape from a ramp to a curve: coverage climbs fast, then **plateaus below
100%** — rows that aged out of their feed before the fix landed are never revisited and keep NULL
until retention removes them. Pinned by a test.

**`gnews` 0% and `newsapi` 0% are configuration, not a code gap.** Both adapters already read
`combo.get("lang")` / `combo.get("language")` — the *query* parameters. `KeyedJSONAdapter._combos`
omits an axis whose env list is unset, so 0% means those adapters' `LANGUAGE` combo axis is not
configured. Setting it would populate the field **and narrow what the API returns to that language**
— a real product tradeoff between metadata and breadth, not a free win, so it is the operator's call
and no code change is proposed here.

That is 5,210 rows, **18.7% of the window**, whose language is genuinely unknown rather than merely
unrecorded: if the query did not pin a language, we do not know what came back, and writing the
query's language onto the rows would be inventing data.

### An unrelated test expired mid-session, and it is worth naming

`test_crawler.py::test_the_summary_reports_age_drops_per_publisher_and_in_total` began failing
between two full-suite runs an hour apart. Not caused by any change here — **verified by stashing
the working tree and reproducing it on a clean checkout.**

The fixture was dated `2026-08-19T09:00:00Z` against `max_age_days=7`, and the wall clock crossed
`2026-08-26T09:00:00Z`. The test had been passing for a week and would have failed on every run from
then on. The cause was one seam: `_aged_plan` pins `now=lambda: _NOW`, but this test reached
`crawler.plan()` directly — and `plan` had no `now` parameter, so it ran against the real clock.

`plan` now takes `now` and passes it through to `PublisherCrawler`, which already accepted one. **A
test that expires is a latent failure**, and this repo already knows it — `conftest.py` widens
`RWE_STORIES_SCAN_DAYS` to 36,500 days for precisely this reason. The crawler was the gap because it
carries its own age filter rather than the story window's. Swept the rest of the suite for the same
shape: the other unpinned fixtures sit ~47 days from any boundary, so this was the only live one.

| bar | where | status |
|---|---|---|
| a feed's declared `<language>` / `xml:lang` reaches every entry (RSS 2.0, RSS 1.0, Atom, BCP-47) | unit (4 cases) | ✅ |
| an entry's own `xml:lang` beats the feed's | unit | ✅ |
| an **item's** `<language>` is never read as the feed's | unit | ✅ |
| a feed declaring nothing still yields `language=None` — the fix adds, never invents | unit | ✅ |
| a majority language drawn from a minority of the coverage does **not** refuse | unit, **verified to flip** | ✅ |
| the refusal still fires once the coverage is actually known | unit | ✅ |
| `only_<lang>` is not claimed from a minority of the coverage | unit, **verified to flip** | ✅ |

## The first live crawl (2026-08-26, `e24d754`) — and the false negative it found

Authorised under the beta-development policy. Five US hosts, **11 requests, 11.3s of politeness
waiting**, no ingestion, no writes.

| host | verdict | reqs | what it said |
|---|---|---|---|
| `decider.com` | **ADMIT** | 3 | allows; feed `/feed/`, 10 items, 100% dated, 100% on-host; 22 sitemaps |
| `6abc.com` | **ADMIT** | 3 | allows; feed `/feed/`, 20 items, 100% dated, **80% on-host — exactly at the bar** |
| `kait8.com` | REJECT | 2 | **allows**; 4 sitemaps declared; no `<link rel=alternate>` |
| `kwch.com` | REJECT | 2 | **allows**; 4 sitemaps declared; no `<link rel=alternate>` |
| `nysun.com` | REJECT | 1 | robots.txt unavailable → fail-closed |

**The transport works.** `crawler._fetch_text` had never contacted a real host in this project's
history; `CRAWLER_DESIGN.md` recorded that as an open unknown. It is now closed.

**Not one publisher disallowed us.** Every host that served a readable robots.txt permitted
`HiddenView-Crawler`. The robots gate rejected nobody — the one REJECT at gate 1 was an
*unavailable* file, not a refusal.

### ⚠ Gate 2 has a false-negative class, and it is exactly the cohort we want

`kait8.com` and `kwch.com` produced **byte-identical discovery shapes**: both Gray Media, both on
Arc XP (`/arc/outboundfeeds/…`), both declaring four sitemaps including a **news sitemap**, and
neither advertising `<link rel="alternate">` on the home page.

They are not undiscoverable. They publish machine-readable discovery documents *for exactly this
purpose* — just not the one gate 2 looks for. **Gate 2 asks "is there an RSS feed link?" when the
question is "is there a discovery document?"**

That matters far beyond two hosts. Arc XP and its peers run a large share of US local television,
which is precisely the cohort `SOURCE_COVERAGE_AUDIT.md`'s curation work targeted and which
discovery keeps surfacing (`kait8`, `kwch`, `6abc` all appear in the worklist unprompted). Left as
is, a full 182-host run would report a misleadingly low ADMIT rate and we would wrongly conclude
local TV is unreachable.

**The machinery already exists and is unused here.** `crawler.discover_sitemap` parses both
`urlset` and `sitemapindex`, and reads `news:title` / `news:publication_date` — a real headline and
a real timestamp, which is what gates 3–5 need. The crawler's ladder has always been *rss → sitemap
→ section*; M7's validation implemented only the first rung.

Adding the sitemap rung is a real piece of work rather than a one-liner (a sitemap index costs one
more fetch to descend, and gates 3–5 must read sitemap entries), so it is recorded here as the next
M7 decision rather than slipped in.

### A smaller fix, made immediately

`nysun.com` reported `robots.txt unavailable (HTTPError)`, and **that string cannot be acted on**:
`HTTPError` covers 404 (no robots.txt at all — RFC 9309 reads it as no restrictions), 403 (the
origin refused *us*, a **stronger** signal than a `Disallow`), and 5xx (an outage, which says
nothing). Filing all three under one label loses the only distinction an operator would act on.
The reason now carries the status code. **The posture is unchanged** — a 404 still fails closed for
discovery, because `CRAWLER_DESIGN.md` declines the 404-means-crawl-freely convention deliberately,
and reporting a code more precisely is not a licence to act on it differently.

### The gate, evaluated on the live poller

`rss_ingest.py run` on `a96d6fb`: **9 feeds, 9 ok, 0 failed, no `robots.txt REFUSED` line.** The
robots gate refuses none of our production sources — it is a no-op on what we already poll, which is
the outcome that makes shipping it safe. It is now *observably* so rather than inferred.

### `nysun.com` is HTTP 429 — a case I had not enumerated

Re-probed with the status-code fix: **`robots.txt unavailable (HTTP 429)`**, one request, no retry
(`sources._request` retries a 429 only with a `Retry-After` it is willing to honour, and there was
none).

I had predicted 404, 403 or 5xx. 429 is none of them, and on a *first* request it is not literal
rate limiting — two probes an hour apart, one request each. It is bot mitigation answering an
unrecognised agent, and 429 rather than 403 is the conventional way to soft-block without announcing
a hard one.

It is also the **only** status that means "later" rather than "never", so it is the one case that
deserves a re-probe rather than a conclusion. If it repeats, `nysun.com` is not answerable by
machine and wants a human or an email — not a workaround.

### The sitemap rung, built

Gate 2 now reads **"is there a discovery document?"** rather than "is there an RSS feed?" The ladder
is RSS first, then the best sitemap robots.txt already declared.

**Choosing which sitemap** is a heuristic, and named as one — but not an arbitrary one. `news`
scores positively because the Google News convention is what carries `news:title` and
`news:publication_date`: a real headline and a real timestamp, which is exactly what gates 3–5 need
and what a bare `<urlset>` of `<loc>` + `<lastmod>` does not have. It is also bounded to recent
content by that spec, where a site's full index spans years.

The negatives are `video`, `image`, `author`, `tag` — document types that are not articles.
Deliberately **not** `category` or `index`: kait8 declares its news sitemap as
`news-sitemap-index/category/news/`, so a negative on either word would reject the very file we
want. Positive signal outranks negative by construction, and ranked against kait8's real declared
list the news sitemap comes first and the video sitemap last.

**Descending an index goes newest child first**, and that is not a preference — it is a defect
`crawler._run_ladder` already had to fix. An index is conventionally oldest-first, so document order
spends the whole budget on the deepest archive and never reaches this week; Daily Maverick and
Premium Times both returned 100% `too_old` for exactly that. Re-deriving the rung here would have
re-earned the bug, so it reuses `crawler._published_utc` and `_UNDATED_SORTS_LAST`.

**A sitemap of bare `<loc>` + `<lastmod>` is rejected.** Those URLs have no headline, and
`clustering.MIN_TITLE_TOKENS` means a title-less article can never join a story — so such a document
is not a usable source however many URLs it lists.

**It is a fallback, not a replacement.** RSS runs first (one fetch, no descent) and the sitemap rung
fires only when the RSS rung yields nothing. A feed that parses but is short is reported by gate 3
rather than silently swapped for another source: switching rungs on a threshold would make the
answer depend on which document we happened to prefer, which is invisible in a report.

Verified against kait8's exact shape — its four real declared sitemaps, an oldest-first index, and
no `<link rel=alternate>`:

```
ADMIT   via news sitemap   (4 requests)
  robots.txt -> landing page -> news-sitemap-index -> sm/this-week.xml
  descended into the newest child, not the 2019 archive
```

### The rung, verified live (2026-08-26, `0e50bf3`)

```
ADMIT  kait8.com  (4 requests)  news sitemap — 32 items, 100% dated, 100% on-host
ADMIT  kwch.com   (4 requests)  news sitemap — 36 items, 100% dated, 100% on-host
```

**The false-negative class is closed**, verified on the exact two hosts that exposed it. And the
descent is visible in the output: both declared `news-sitemap-index/category/news/`, and both were
ingested from `news-sitemap/category/news/` — the *child*, reached by descending the index.

Both are also **cleaner than the RSS admissions**: 100% on-host against `6abc.com`'s 80%, which sat
exactly on the bar. The sitemap rung is not a compromise path for second-rate sources; on this
evidence it produces better-formed evidence than the feed rung did.

**US local television is reachable.** That was the open question this rung existed to settle, and it
decides whether the 50k path goes through local news at all.

### ⚠ An ADMIT verdict currently names a source with no ingestion path

Acting on those two verdicts would have failed **silently**. `rss_ingest.parse_feed` on a `<urlset>`
finds no `<channel>` and no `<item>`, so it returned **zero entries, raised no error**, and the feed
would have reported healthy forever — the same reports-healthy-does-nothing shape this series keeps
finding. The runner's own "what to do with the ADMIT verdicts" text said *"adding its feed"*, which
pointed straight into it.

`parse_feed` now **rejects a sitemap loudly**, naming `crawler.discover_sitemap` as the path that
does handle one — an error that only says "no" leaves the reader where they started, and here the
source is legitimate; it was the destination that was wrong. `ingest_all` catches per-feed errors,
so the rejection costs one feed rather than the run.

**The real gap this exposes is the next milestone, not a bug in this one.** Sitemap sources are
ingested by `crawler.py`'s ladder, and `crawler.py` says of itself: *"This module is not wired into
the poller. Nothing imports it from the ingestion path."* So M7 can now *find* US local television
and cannot yet *ingest* it. Wiring `CrawlAdapter` into the poller is the staged rollout
`CRAWLER_DESIGN.md` already describes — and it is now the thing standing between the worklist and a
shadow cohort.

### ⚠ The cost estimate was understated, and is corrected

`probe_cost` claimed **two** requests per host — `robots.txt` and one autodiscovery fetch. The live
crawl spent **three** for every ADMIT: robots.txt, the landing page, the discovery document. The
landing page was simply missing from the estimate, which would have understated a 182-host run by
182 requests. It now reads three, with the worst case (five, where the sitemap rung fires and needs
a descent) stated separately. **An estimate put in front of a human to authorise crawling has to be
the real number, not the optimistic one.**

### Read with care

`6abc.com` passes gate 5 at **exactly 80%**, the bar itself. Four of its twenty items sit off-host.
It is an admission, not a comfortable one, and worth knowing before anything is promoted.

## `CrawlAdapter` wired into the poller — the first change that lets crawled content reach the catalog

Everything before this was read-only probing. This crosses into ingestion, so the properties below
are **enforced rather than documented**.

`CrawlAdapter` already existed and already inherited the source chassis — `poll_once` does quota →
`ingest_entries` → health identically to every other adapter. What was missing was the wiring and,
much more importantly, the guards.

### The trap this nearly shipped

`CrawlAdapter.enabled()` read `config.enabled` and nothing else, and
`examples/data/crawler_publishers.json` shipped **six publishers marked enabled** — BBC, NPR, The
Guardian, Associated Press, HuffPost, Texas Tribune — whose URLs and `article_pattern`s
`CRAWLER_DESIGN.md` calls *"unverified guesses"*. They were inert while nothing registered the
adapter. Registering it makes `enabled: true` live.

Measured before fixing: with the flag on, **6 crawl adapters registered and all 6 were enabled**.
They are now `enabled: false`, and the two the live probe actually verified are in their place.

### Two switches, and the second is the one that matters

| gate | what it stops |
|---|---|
| `RWE_CRAWL_ENABLED`, default **off** | deploying the wiring changes nothing. The keyed adapters need an API key before they can act — an accidental safety catch a crawl config, being just a file, has no equivalent of |
| **shadow membership**, enforced in `enabled()` | `corpus.DEFAULT_TIER` is `"A"`, so an outlet nobody put in `RWE_CORPUS_SHADOW` does not land somewhere neutral: its articles go **straight into the clustering corpus and start voting in stories** |

The second is the load-bearing one. Stage 3 says a discovered source is `tier = 'shadow'` — stored,
deduped, attributed, surfaced nowhere — for a minimum of 14 days, and M8 measures it there before M9
proposes anything. **Promotion by omission is the one failure this change could cause that nobody
would notice** until a crawled outlet turned up in a blindspot claim, so `enabled()` refuses rather
than the docs asking nicely. A publisher enabled but unshadowed reports why, because silently not
running is indistinguishable from a broken config.

Verified: flag on and no shadow → **zero adapters run**, both naming the fix. Flag on and
`RWE_CORPUS_SHADOW=kait8.com,kwch.com` → both run.

### What the config says, and what it deliberately does not

*(Superseded one run later — see "The loop closed on the next run" below. Kept because the reasoning
for shipping it empty is the reason the pattern is trustworthy now.)*

`article_pattern` is **empty**, and that is a stated position rather than an oversight.
`CRAWLER_DESIGN.md`'s sharpest warning is that a pattern matching 0% of discovered URLs makes the
crawler ingest nothing while every gate reports healthy — so an **invented** pattern is worse than
none. We have not observed these publishers' article URL shape: the probe reported 100% on-host and
printed no samples. Empty means no pattern filter, which is defensible for a *news* sitemap (it
contains articles by specification) and is bounded by `max_age_days: 7` and `max_urls: 60`.

The lint still says so, and its wording was corrected on the way: it cited *section* discovery for
every publisher including sitemap-only ones, and a warning that does not describe your config is one
people learn to skip.

`unknown_publisher` also fires for both, and that one is **correct and expected** — these outlets
are unrated, which is precisely why they go to shadow rather than to Tier A.

The configured source is the **declared index**, not the child the probe descended to: the index is
what robots.txt advertises and is therefore the stable address, and the ladder descends on its own.

### Still no article bodies

`discover_sitemap` yields `url`, `title`, `published_at` and `body=None`. Verified directly, and
pinned by a test, because it is the constraint the whole design rests on.

### Three existing invariants had to be amended, and why that is not weakening them

The wiring failed three tests that all encoded the same assumption: **every configured publisher is
hand-picked and registry-known, and crawled content goes to Tier A.** M7 inverts that — it discovers
*unrated* outlets and routes them to shadow.

The load-bearing one read *"a name the registry does not know ingests with NO lean — the crawler
would be adding volume the product cannot describe."* That is **true for Tier A and false for
shadow**: a shadow article reaches no reader surface and no story, so it describes nothing to
anyone. It is evidence for M8 and nothing else.

**The protection is relocated, not removed.** It used to live in "the config may only contain rated
publishers", enforced by a test's vigilance. It now lives in `CrawlAdapter.enabled()` refusing to
run any publisher outside `RWE_CORPUS_SHADOW` — enforced by construction. The amended tests say
*resolves **or** is shadow-bound*, and lint output is permitted only for shadow-bound publishers and
only for the two expected codes.

`article_pattern` is likewise now required where discovery is an **HTML index** — a section page
links to tags, authors and the shop — and optional for a news sitemap, which contains articles by
specification.

**The deeper point: shadow is the mechanism for exactly this uncertainty.** The lane exists to
ingest something we are not sure about, measure it for 14 days, and decide. Declining to enable a
verified source because one attribute is unobserved would defeat the machinery built for that.

### Closing the loop on the unobserved pattern

The probe now prints and records up to three **sample article URLs** per admitted host, so an
`article_pattern` can be written from observation rather than guessed. That is the path from
"acceptable in shadow" to "verified", and it needs no new crawling — the URLs are already in the
discovery document the probe fetched.

### The loop closed on the next run: the pattern is now observed

The probe returned real article URLs, three per host:

```
https://www.kait8.com/2026/08/26/list-dozens-new-missouri-laws-take-effect-friday/
https://www.kwch.com/2026/08/26/wichita-city-council-adopts-2027-budget-despite-library-funding-concerns/
```

`article_pattern` is now `/\d{4}/\d{2}/\d{2}/`, **written from observation**: 6 of 6 observed URLs
match, across two independent hosts, and it rejects the `/news/`, `/authors/…`, `/tag/…` and
`/video/` shapes it exists to filter. It is also the **same pattern already configured for NPR** — a
different publisher on the same Arc XP date-path convention, which is corroboration rather than
coincidence.

Verified end to end through the crawler's own filter: a sitemap containing one real article and one
author page yields `discovered 2, pattern_rejected 1, candidates 1`, and the surviving entry carries
`body=None`.

**Honest bound on the evidence:** 6 URLs of the 71 the two sitemaps held (~8%). The safety net is
that being wrong is *loud* rather than silent — `_filter` counts `pattern_rejected`, and
`_why_empty` explicitly diagnoses a pattern matching 0% of discovered URLs. A wrong pattern shows up
in the crawl report; it does not quietly ingest nothing.

Setting it also cleared the `no_article_pattern` lint. `unknown_publisher` remains for both and is
**correct** — an unrated outlet is precisely what the shadow lane is for.

### ⚠ The runner was telling operators something false

Its ADMIT advice still read *"the crawler ladder (crawler.py), which is **not wired into the
poller** — that is the next thing this worklist needs."* True when written, false the moment the
wiring shipped, and printed on a production run against the very build that contains it.

Live output describing the system as it *was* is its own defect class. It now names both switches
and the config file, in the order they must be set. Naming only `RWE_CRAWL_ENABLED` would have been
worse than naming neither — it reads like the whole instruction, and following it crawls nothing.

**Sweeping for the rest of the class found two more in the same runner**, both in the offline
banner an operator sees *before* deciding whether to probe:

* *"the roadmap asks for a ToS / robots review that has never been done"* — conflating two things
  that have now diverged. The **robots** review has been done, on 7 hosts, and every readable
  robots.txt allowed us. The **ToS** review has not, and is the item live probing cannot close.
* *"CRAWLER_DESIGN.md records that no live crawl has ever run… This is the first thing in the whole
  roadmap that touches a publisher."* — false since `e24d754`. Worse than merely stale: it told an
  operator the run they were about to authorise was unprecedented, when it was the fourth.

`CRAWLER_DESIGN.md`'s own "what has not been verified" list carried the same claims, so it now
carries a dated update marking three of its four items closed and the ToS review still open. The
list is kept rather than rewritten — the reasoning that made those unknowns worth stating is what
makes the closures worth trusting.

Pinned by tests: the three retired sentences are asserted **absent** from the runner, and the ADMIT
advice is asserted to name both switches and the config file.

### Closing the loop on the unobserved pattern

The probe now prints and records up to three **sample article URLs** per admitted host, so an
`article_pattern` can be written from observation rather than guessed. That is the path from
"acceptable in shadow" to "verified", and it needs no new crawling — the URLs are already in the
discovery document the probe fetched.

### ⚠ The switch reached one container and its precondition reached the other

Caught while writing the operator instructions, before anything was set. The wiring added
`RWE_CRAWL_ENABLED` to **both** `api` and `ingest`, and `RWE_CORPUS_SHADOW` to **`api` only** —
`ingest`'s `environment:` block never had the corpus vars, and the stack has no `env_file:`, so an
undeclared variable does not reach that container whatever `deploy/.env` says.

Following the two-line instruction exactly would have produced, inside `ingest`:
`RWE_CRAWL_ENABLED=1` visible, `RWE_CORPUS_SHADOW` **absent**.

**It fails closed, and that part held.** An outlet absent from the shadow list is Tier A,
`CrawlAdapter.enabled()` demands shadow, so every adapter refuses. Nothing would have been promoted
by omission — the structural protection worked exactly as designed, in the one service where the
config was wrong.

**But it refuses for a reason the operator has already fixed.** `ingest` would print *"not in
RWE_CORPUS_SHADOW — this is promotion by omission"* naming a variable they had just set, in a file
they were looking at. A diagnostic that describes a container's environment while the operator reads
the host's is worse than no diagnostic: it sends them to re-check the thing that is already right.

Both vars are now declared on `ingest`, not just the shadow one: `corpus.tier_of` reads
`RWE_CORPUS_TIER_B` and `RWE_CORPUS_SHADOW` together, so passing one would resolve an outlet Tier B
in the api and Tier A in ingest — on the same catalog.

**Why the existing guard missed it.** `test_rec_flags_deployable.py` exists for precisely this
failure mode and has three prior occurrences recorded in its docstring. It greps the whole compose
file for the variable's name, so a declaration in *any* service passes — the right shape for "can
this flag ever reach a container", blind to "reaches the wrong one". This is the first occurrence
where the flag did reach a container, just not the one whose code reads it. The new guard is
per-service: every service given `RWE_CRAWL_ENABLED` must also be given both corpus vars, plus a
guard-the-guard asserting the loop is non-empty and covers `api` and `ingest`. Verified to fail on
the pre-fix file.

### Failure isolation

A malformed or missing crawl config returns an empty adapter list rather than raising. A supplement
that can break the thing it supplements is worse than one that is absent, and the RSS poller must
survive a bad crawl config.

## The switch-on, measured live (2026-08-26) — and the wall it found

`RWE_CORPUS_SHADOW=kait8.com,kwch.com` + `RWE_CRAWL_ENABLED=1`, on a 150,000-row catalog.

**The ladder closed end to end.** Two cycles per publisher: `new 2 / duplicates 27 / failed 0` for
kait8, `new 1 / duplicates 34 / failed 0` for kwch, ~2.4 s per poll. Robots passed, the declared
news-sitemap-index parsed and descended, and the observed `article_pattern` accepted real articles.

**The safety property held, verified rather than asserted:** 217 kait8 rows and 178 kwch rows in the
catalog, **100% `shadow`, zero in Tier A.**

### Turning on shadow is subtractive, and that was not said out loud

Tier is computed at **query time** from the outlet's identity (`corpus.tier_of`), not stamped on the
row at ingest. Only 3 of those 395 rows came from the crawler; the other ~392 arrived earlier
through the aggregators — the crawl-exhaust channel Stage 1 identified — and every one of them was
Tier A until the flag was set, because with both vars empty `corpus.enabled()` is False.

So the flag did not merely route new articles into shadow. It **removed ~392 existing articles from
Tier A and from every reader surface**, at the moment it was set. That is correct behaviour and
exactly what the lane is for; it was presented as purely additive, which it is not. An operator
adding a host we already carry heavily should expect coverage to *drop* first.

(One worry that proved unfounded: `shadow_exclusions()` matches on publisher-name strings, so a host
stored under a display name would slip the SQL prefilter. Both outlets store the publisher AS the
host — `names=['kait8.com']` — so prefilter and Python authority agree here.)

### ⚠ The post-cycle lock is at 87.8% occupancy, and it is the real scale ceiling

Measured over a clean 6 h window against 12 h of container uptime:

```
post-cycle   18,176.9 s
poll            797.6 s
              ---------
             18,974.5 s / 21,600 s  =  87.8%
```

`poll_adapter_once` holds `self._lock` across **both** `poll_once` and `_post_cycle`, and both
timings are taken inside it — so this is lock-HELD time, not queueing, and the two sum to true
occupancy. Ingestion is keeping up (`failed: 0` everywhere), but the headroom is ~12%.

`_post_cycle` runs whenever an adapter ingested anything (`postCycleMs` is exactly `0.0` on every
`new: 0` cycle — the dirty-check works). Its three segments decompose exactly (131722.1 + 29559.3 +
102174.1 = 263,455.5 ms against RSS's logged 263,455.9):

| segment | what it is | share |
|---|---|---|
| `cleanupMs` | `storage_lifecycle.run_cleanup` — retention + derived-table prunes | **38–50%** |
| `refreshMs` | `self._on_cycle(agg)` — the hot-refresh seam | **36–50%** |
| `warmMs` | `request_warm` (non-blocking) + `detect_breaking_stories` + push | 11–17% |

**The `warm` step is the smallest.** That matters because the code comment at `sources.py:1547`
records the previous investigation landing on it — *"~93% of the most expensive loop in the process
was inside a step nobody had timed"* — and the fix (make the warm non-blocking) worked. The cost
simply moved: cleanup and refresh are now the owners, and neither has been through that treatment.

**The structural problem is the word "cycle".** `_post_cycle`'s own comment describes *"one
incremental pass per cycle"*, which was true of a single poller loop. There are now ~11 adapter
threads, each triggering a full catalog-wide retention pass and a full hot refresh whenever it finds
even one article. Both costs scale with **catalog size**, not with what the adapter brought — which
is why kait8 paid **216 s of post-cycle for 2 new articles** while GNews paid 90 s for 10.

**This is pre-existing and was not caused by the crawler** — RSS is the single most expensive
provider in the sample at 263 s. But it reframes the crawl adapters: they are two more claimants on
the busiest lock in the process, and structurally the *lowest-yield* ones. At two publishers that is
noise. At M6's fan-out or M7's 50k sources, low-yield adapters each triggering full-catalog
maintenance is a wall, and this measurement puts a number on it.

**M6 already names the fix** — *"poller out of the API process, narrow the global lock"* — so this
is corroboration of a listed dependency rather than a new discovery. What is new is the ordering
argument: at 87.8% the lock is not a future problem to be handled during fan-out, it is the
prerequisite. Throttling catalog-wide maintenance to once per polling *window* rather than once per
adapter cycle is the cheap version and would cut the dominant cost by roughly the adapter count.

Not changed here. It is the ingest hot path, it is outside M7's scope, and it is a deliberate
decision rather than a tidy-up to fold into a crawler commit.

## What M7 does NOT do

It does not ingest. `--probe` reads `robots.txt`, one landing page and at most one feed per host, and
prints a verdict — it writes no feed row, no catalog row, no tier assignment. **Admitting a source
is a separate human step:** M7 emits a worklist, M8 measures what shadow ingest produced, M9 emits
the config. Nothing in the chain moves an outlet on its own.

**And the ToS / robots review is still outstanding.** Building Stage 2 does not discharge it. The
offline run is safe to run now and is the one to look at first; `--probe` should wait for that
review, and then start at `--limit 5`.

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
