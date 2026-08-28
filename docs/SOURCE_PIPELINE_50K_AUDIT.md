# What limits the real source pipeline — and the next milestone

**Audit first, then the milestones it named.** The question: with M6, M7, M8, M9 built and M3's
highest-value fixes deployed, what actually stops the crawl cohort growing from 2 real sources to
100, 1,000, 10,000 and 50,000?

§§0–5 are the audit as written, unedited except where a production measurement corrected it. **§6 is
M10, built and verified; §7 is M11, audited and built.** With L0, L1 and L2 closed, the first open
engineering limit is L3 (polling interval vs. N, binding at 1,000–10,000) and the first open limit of
any kind is **L6, the ToS review**, which binds at any size and is not an engineering item.

---

## 0 · The answer in one paragraph

> ### ⚠ Corrected after the production run
>
> The first version of this audit said *"supply is not the problem — Stage 1 already found 3,729
> unrated outlets in the catalogue"* and named admission as the next milestone. **The production run
> says 177 candidates, not 3,729.** I took "unrated outlet identities" from `source_discovery.py`'s
> docstring and used it as the candidate count; those are different numbers separated by a volume
> floor and two gates. Reaching for a figure that supported the conclusion I was already forming is
> the §9b pattern in `STORAGE_50K_DESIGN.md`, one document over.
>
> With the real number, **supply is the bottleneck, and admission is the one after it.**

```
window        : 28,217 articles          hosts seen : 4,238
  already tracked by the registry : 546
  aggregator / proxy hosts        :  28
  below the 10-article floor      : 3,487
  CANDIDATES                      :  177
```

**Every stage of the pipeline is built. The joins between them are not, and the first stage is
looking through the wrong window.** `discover → validate → crawl → shadow ingest → evaluate →
promote` is five pure modules — each explicitly *"no store, no network, no environment, no
writes"* — connected by a human editing two files, one of which ships **inside the image**.

But the binding limit is upstream of all of that. **Discovery calls `story_service._fetch(st)`** —
the *clustering* fetch — so its evidence window is `scan_days()`, which defaults to the **6-day
clustering window**, capped at 60,000 rows, with Tier B and shadow already excluded. It sees
**28,217 articles of a 150,076-row catalogue**. A host publishing twice a week never accumulates ten
articles inside six days, so it stays below the floor forever no matter how long we carry it.

**The next milestone is M10: discovery reads the catalogue, not the clustering window.**

---

## 1 · The chain, and where the state is

| stage | module | persists? |
|---|---|---|
| 1 · discover | `source_discovery.py` — *"Pure: no store, no network, no environment, no writes"* | ~~**no** — stdout~~ → `source_campaign.py seed` writes `candidate` rows (M11) |
| 2 · validate | `source_validation.py` — *"no store, no environment, no writes"*, 8 gates | ~~**no** — stdout + optional `--out`~~ → `source_campaign.py probe` writes the verdict (M11) |
| ⟂ admit | ~~a human edits `examples/data/crawler_publishers.json`~~ → `source_campaign.py admit` | ~~requires a git commit + deploy~~ → a row; the JSON still wins for the 8 hand-verified publishers |
| ⟂ admit | ~~a human edits `RWE_CORPUS_SHADOW`~~ → the same command writes `tier='shadow'` | ~~requires a container restart~~ → live within `corpus`'s 60 s snapshot; a **restart is still needed for the crawl adapters**, which the registry builds once at startup |
| 3 · shadow ingest | `crawler.CrawlAdapter`, gated on `RWE_CRAWL_ENABLED` **and** `in_shadow()` | articles ✔ |
| 4 · evaluate | `source_evaluation.py` — pure | via M9 ✔ |
| 5/6 · promote | `source_lifecycle.py` — *"emits the configuration; it never mutates serving state"* | `SourceLifecycle` + append-only ledger ✔ |
| **⟂ apply** | **a human edits `RWE_CORPUS_TIER_B`** | **requires a container restart** |

The right-hand column was the finding. **Persistence began at stage 3** — `SourceLifecycle.STATES`
is `("shadow", "B", "A", "dormant", "retired")` and there was **no `candidate` and no `validated`
state**, so the ledger only started tracking an outlet once it was already ingesting. Everything
before that was a batch job whose output a person retyped.

**M11 built the missing half** as a separate table (`store.SourceAdmission`) rather than by widening
`SourceLifecycle`: the two are keyed differently — a **host** before admission, an **outlet identity**
after — and their states answer different questions. They join at exactly one arrow, in
`store.admit_source`. See §7.

`SourceLifecycleEvent` even carries an `applied: bool` column — the schema already knows that
emitting a transition and applying it are different events, and that the second one is manual.

---

## 2 · What binds at each rung

| sources | polls/hr @900s | vs the ~9,000/hr lock budget | min interval | config file | env var | `tier_of` | threads¹ |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 100 | 400 | 4% | — | 0.04 MB | 2 KB | 7.9 µs | 113 |
| 1,000 | 4,000 | 44% | — | 0.44 MB | 20 KB | 17.1 µs | 1,013 |
| 10,000 | 40,000 | **444% ✗** | 1.1 h | 4.4 MB | 200 KB | ~100 µs | 10,013 |
| 50,000 | 200,000 | **2,222% ✗** | 5.6 h | **22 MB** | **1,000 KB** | **507.5 µs** | 50,013 |

¹ `RWE_POLL_WORKERS=0` in production — thread-per-adapter. M6.3's bounded pool exists and is **off**.

`tier_of` and env-var sizes are measured (§2.7 and §4 D6 of `STORAGE_50K_DESIGN.md`); the 1,000 KB at
50,000 hosts is a measured 999,999 bytes against a 2,097,152-byte `ARG_MAX`, duplicated across the
`api` and `ingest` services.

**Validation campaign cost**, at the 3 requests/host and 2 s politeness the code configures:

| hosts | requests | wall time at 16-way | resumable? |
|---:|---:|---:|---|
| 100 | 300 | 38 s | **no** |
| 1,000 | 3,000 | 6 min | **no** |
| 10,000 | 30,000 | 1.0 h | **no** |
| 50,000 | 150,000 | 5.2 h | **no** |

Nothing reads a `--out` record back, and `source_validation.validate()` takes a candidate dict with
no notion of a prior verdict. **A campaign that stops halfway restarts from the top**, re-asking
publishers who already answered. At 100 hosts that is a nuisance. At 50,000 it is 150,000 avoidable
requests against real publishers, which is a politeness and ToS problem rather than a speed one —
the one category where "just run it again" is not an acceptable answer.

---

## 3 · Ranked limits

| # | limit | binds at | has a mechanism? |
|---|---|---|---|
| **L0** | **Discovery supply** — Stage 1 reads the 6-day clustering window, so it yields **177 candidates**; a host publishing twice a week never reaches the 10-article floor | **~180** | **none** |
| ~~L1~~ | ~~**Admission requires a code deploy** — crawl config is baked into the image~~ | ~~100 – 1,000~~ | **closed by M11 — §7** |
| ~~L2~~ | ~~**Validation is not resumable** — verdicts persist nowhere~~ | ~~any rung; matters at 1,000+~~ | **closed by M11 — §7** |
| L3 | Polling interval must scale with N (roadmap B1) | 1,000 – 10,000 | designed in M6 (interval ceiling + dormancy), not built |
| L4 | Thread-per-adapter — 1,013 threads at 1,000 sources | ~1,000 | **built** — M6.3's pool, `RWE_POLL_WORKERS`, currently 0 |
| L5 | Tier lists in environment variables | ~30,000 | **shadow half closed by M11** (`store.SourceAdmission.tier`, unioned with the env — §7.2); Tier B still env-only |
| L6 | ToS / robots review | **any real expansion** | **not an engineering item** |
| ~~L7~~ | ~~Tier-A clustering grows with the cohort~~ | — | **cleared — structurally impossible; see §4** |

**L0 binds first and hardest.** L1 is painful but has a working path — 177 sources is one or two
batched deploys. L0 has no path at all: past ~180 there is nothing left to admit. Removing L1 raises
the ceiling from 177 to 177; removing L0 raises it into the thousands. That ordering is what changed
when the production numbers arrived.

L1 and L2 remain real, they are the *next* milestone after L0, and they are the same change as each
other. **Both were closed by M11 — see §7.** With L0, L1 and L2 gone, the first open limit is L3
(polling interval vs. N), which binds at 1,000–10,000, and L6 (the ToS review), which binds at any
size and is not an engineering item.

---

## 4 · Tier A is already bounded, and not by luck

The brief asks to keep Tier-A clustering bounded while the cohort grows. It already is, structurally:

```python
if crosses_tier_a(state, to):
    needs = [NEEDS_COUNTERFACTUAL]
    if to == "A" and not rated:
        needs.append(NEEDS_LEAN)
    return Transition(state, to, automatic=False, ...)
```

**Every** move into or out of Tier A is `automatic=False` and requires a counterfactual — plus a lean
rating to enter. Only shadow↔B transitions are automatic, and the code states exactly why: *"neither
side of this move clusters, so the partition cannot change."*

So 50,000 shadow sources cannot enlarge the clustering corpus. What bounds Tier A is human rating
throughput, which is the strategic constraint `SCALE_ROADMAP.md` names repeatedly and which no
milestone here changes. **This is not a risk to manage during the expansion; it is a property that is
already enforced.**

---

## 5 · The next milestone

### M10 — Discovery reads the catalogue, not the clustering window  ✅ **BUILT**

**The defect, in one line.** `audit_source_discovery.py:74` is

```python
rows = story_service._fetch(st)
```

`_fetch` is the *clustering* candidate fetch. Its window is `scan_days()`, which defaults to
`clustering.DEFAULT_WINDOW_DAYS` — **six days** — and its `max_scan` caps at 60,000 rows. So Stage 1
looks at **28,217 of 150,076 catalogue articles** and asks "which hosts have ten articles here?"

A host publishing twice a week has **~1.7 articles in six days and ~9 in the catalogue's span**. It
is invisible to discovery permanently, however long we carry it — and the long tail of local and
regional publishers that 50,000 sources is *made of* publishes exactly at that rate. The 3,487 hosts
below the floor have a median of one article **in six days**, which is not the same statement as a
median of one article ever.

Reusing the clustering fetch also inherits its exclusions, and those are right for discovery —
shadow and Tier B are already-known outlets and re-discovering them would be noise. **The window is
the part that is wrong**, and `_fetch` already accepts `date_from` and `max_scan`, with a documented
promise that "a caller-supplied `date_from` still wins".

**What M10 is:**

1. Stage 1 reads the full retained catalogue with the same tier exclusions, not the 6-day window.
2. Host observations accumulate in their **own table**, so evidence is no longer bounded by
   article retention either. This is the durable half: once the catalogue is age-capped, the
   6-day problem becomes a 30-day problem rather than disappearing, and a host seen five times a
   month for six months should be a candidate on the strength of thirty observations.
3. The floor stays at 10. It is a cost bound and it is correct; what changes is the window it counts
   over.

**Why it is the bottleneck now:** every other stage is built and validated, Tier A is structurally
protected (§4), and the pipeline's very first step is the one throttling it. 177 candidates is not
enough to reach 1,000 sources by any route.

**What it enables:** the 1,000-source rung becomes reachable at all. It is also the cheapest
milestone on this list by a wide margin — the window is two arguments; the observations table is the
part worth designing.

**What it does not do:** it does not remove L1 (admission still needs a deploy — that is the next
milestone, and it is what makes the candidates *usable*), it does not touch Tier A, and **it does not
discharge the ToS review**, which gates any real expansion at any size.

### Then, in order

| after M10 | milestone | binds at |
|---|---|---|
| ~~M11~~ | ~~Source admission becomes data~~ — **built, §7.** `store.SourceAdmission` + `source_campaign.py`; seven states, per-host claims, resumable campaigns. Subsumes M3's D6 for the shadow lane | ~~100 – 1,000~~ |
| M12 | Polling interval scales with N — M6's interval ceiling + dormancy | 1,000 – 10,000 |
| — | `RWE_POLL_WORKERS` off 0 — a setting, M6.3 already built the pool | ~1,000 |

---

## 6 · M10 sized, on production

Measured 2026-08-27, read-only, hosts keyed the way `source_discovery.candidates` keys them:

```
distinct hosts in the catalogue: 9397
  hosts with >= 10 articles: 1525
  hosts with >=  5 articles: 2657
  hosts with >=  3 articles: 3912
```

| | 6-day clustering window | full catalogue | |
|---|---:|---:|---:|
| distinct hosts | 4,238 | **9,397** | 2.22× |
| hosts ≥ 10 articles | 751 | **1,525** | 2.03× |

The gates remove **574 specific hosts** — 546 the registry already tracks, 28 aggregators/proxies.
That is a fixed set, not a percentage, so it subtracts the same way from a wider window:

```
1,525 − 574  =  951 candidates      5.4× today's 177
1,525 − 650  =  875 candidates      4.9×, allowing for more registry hosts appearing over 30 days
```

**M10 takes the candidate pool from 177 to roughly 900.** That is the difference between a ceiling
below 200 sources and a 1,000-source cohort being reachable at all — and it comes from correcting
which window one function reads.

It also confirms the mechanism rather than just the outcome: the *host count itself* more than
doubles (4,238 → 9,397). Two thirds of the hosts we already ingest from are invisible to discovery
purely because their articles are older than six days.

### What this does not license

**Lowering the floor.** At floor 5 the pool would be ~2,000 and at floor 3 ~3,300 — but that is
6,000 and 9,800 probe requests against real publishers, and the floor's stated rationale still
holds: it is a cost bound, and a host with three articles is one we *have no evidence about* rather
than one we have judged. The floor is not what is broken here; the window is. Widen the window
first, keep the floor at 10, and revisit only if 900 candidates prove insufficient.

### The bar this milestone has to clear

M10 is worth building if a discovery run over the full catalogue reports **≥ 800 candidates**. That
is the number this section predicts, it is checkable by re-running `audit_source_discovery.py` after
the change with no network involved, and if it comes back near 177 the change did not do what this
audit says it does.

**Verified on production 2026-08-27, running `b0bd3b1`:**

| | `--window-days 6` (the old behaviour) | default (the catalogue) | |
|---|---:|---:|---:|
| articles | 28,246 | 149,647 | 5.30× |
| hosts seen | 4,244 | 9,388 | 2.21× |
| already tracked | 547 | 837 | 1.53× |
| aggregator / proxy | 28 | 59 | 2.11× |
| below the floor | 3,492 | 7,319 | 2.10× |
| **CANDIDATES** | **177** | **1,173** | **6.63×** |

**The bar was ≥ 800. The result is 1,173.** The control run still reports exactly 177 — the
pre-M10 number — so the two runs are measuring what they claim.

> **My estimate of ~951 was 23% low, and the reason is worth recording.** `census()`'s buckets are
> **mutually exclusive**, with precedence `eligible > proxy > tracked > belowFloor` — so
> "below the floor: 7,319" means *below the floor AND not tracked AND not proxy*. Of the 896 hosts
> the gates remove, only **352 are above the floor**; the other 544 were below it and would never
> have been candidates. My arithmetic subtracted all 574 of the gate-removals from the 1,525
> above-floor hosts, double-counting the ones the floor had already taken out. `1,525 − 352 = 1,173`
> — the measured answer, exactly.
>
> Conservative in the useful direction this time, but conservative by accident rather than by
> design, which is not the same thing.

**Built, and this is what it does:** `store.list_discovery_rows()` is the narrow projection
(six fields: the host pair, publisher, language, publishedAt, and sourceType for the runner's
language table); `audit_source_discovery.py` calls it instead of `story_service._fetch`, keeping the
tier exclusions and dropping the window. `--window-days 6` reproduces the old behaviour so the change
stays auditable against what it replaced.

**What 1,173 candidates now costs at Stage 2** — the number a ToS review is actually being asked
about: **3,519 requests** (up to 5,865 if every host needs a sitemap descent), ~2.0 hours serial at
the configured 2 s politeness. That is a scheduled campaign against real publishers, and it is
exactly the point at which L2 — *validation is not resumable* — stops being a nuisance and starts
being the reason to build M11 before running it.

---

## 7 · M11 — Source admission becomes durable, resumable data  ✅ **BUILT**

M10 removed L0. That promoted **L1** (*admission requires a code deploy*) and **L2** (*validation is
not resumable*) to the front, and — as §3 said — they are the same change. This section is the audit
that preceded the build, then what was built.

### 7.1 · The audit: every place admission state lived

Read in full before any code: `store.SourceLifecycle` / `SourceLifecycleEvent` and their four store
methods, `source_lifecycle.py`'s state machine, `source_validation.validate` and its eight gates,
`audit_source_discovery.py`'s `--probe` loop, `crawler.load_config` / `PublisherCrawlConfig`,
`crawler.CrawlAdapter.enabled` / `in_shadow`, `sources.default_registry` / `_crawl_adapters`, and
`corpus.tier_index` / `tier_resolver` / `sql_exclusions` / `shadow_exclusions`.

| # | finding | evidence |
|---|---|---|
| A1 | **A probe verdict is written nowhere.** `--probe` prints, and `--json` writes a file **nothing reads back** | `audit_source_discovery.py:225–243` — the only consumer of that file is a human |
| A2 | **`--limit N` is a stable prefix, not a cursor.** It takes the top N by article count, so running it twice probes the same N twice. There is no "next N" | `targets = work[:args.limit]`, `work` sorted by `-articles` |
| A3 | **`--hosts` is the only way to advance**, and it requires the operator to keep the done-list outside the system | `audit_source_discovery.py:183–189` |
| A4 | **A rejection is not remembered.** Nothing stops the next run re-asking a publisher whose robots.txt refused us | no persistence at all in the Stage-2 path |
| A5 | **`crawler.RateLimiter` is per process.** Two campaigns each believe they are polite; the publisher sees double | `RateLimiter` is constructed per run, in `main()` |
| A6 | **`SourceLifecycle` starts at `shadow`.** `STATES = ("shadow", "B", "A", "dormant", "retired")` and `initial_state="shadow"` — there is no state for "found, not yet probed" or "probed, failed a gate", so the ledger only begins once a source is already ingesting | `source_lifecycle.py:68`, `store.record_source_evaluation` |
| A7 | **The crawl config is baked into the image.** `_CONFIG_PATH` is `examples/data/crawler_publishers.json`, 8 publishers, no compose mount — adding one is a git commit and a deploy | `crawler.py:85` |
| A8 | **Tier lists are environment strings**, and `corpus.tier_resolver`'s own docstring measures the cost: **~500 µs per `tier_of` call** against a 50,000-host list, of which ~380 µs is hashing the setting to find its memo. `ARG_MAX` was *the second* problem with storing them there | `corpus.py` |

And what was already right, and had to survive unchanged:

* `source_validation.validate` has **no default fetcher** — an offline run structurally cannot look
  like a validated one;
* `crawler.RobotsPolicy` is fail-**closed** (absent or unparseable robots.txt is a refusal);
* `CrawlAdapter.enabled()` requires `RWE_CRAWL_ENABLED` **and** `config.enabled` **and**
  `in_shadow()`;
* `corpus.DEFAULT_TIER == "A"`, which is why `in_shadow()` is a hard precondition rather than a
  preference — crawling an unshadowed outlet is *promotion by omission*;
* `_tier_with` tests shadow before B, "so an outlet named in both lands in the more restrictive one".

### 7.2 · The one design decision worth arguing

**The table is unioned with the environment lists, never substituted for them.**

`DEFAULT_TIER` is `"A"`. If the table were the source of truth and a read came back empty — a
migration not yet applied, a store not wired, a query that raised — **every outlet in the corpus
would silently become Tier A**, and it would present as "clustering suddenly has more sources"
rather than as an error. Unioned, an empty read degrades to exactly today's shipped behaviour, and
an operator can pin an outlet in the environment that no table write can un-pin. This is the same
asymmetry `_tier_with` already applies one level down.

The corollary is that `corpus.enabled()` had to learn about the table: it gates the whole tier
filter, so an admission that did not reach it would be a shadow row `tier_of` never consults.

Two more that follow from `DEFAULT_TIER == "A"`:

* **`withdrawn` keeps its shadow assignment.** Clearing the tier on withdrawal would take every
  article already ingested from that host and put it into the clustering corpus — an operator
  *reducing* a source's reach would be promoting it. Withdrawal stops the crawl; where those rows go
  next is M9's decision, on M9's evidence.
* **`crawler.admitted_configs` re-checks every row against `corpus.is_shadow`.** The crawl set is
  then always a subset of the shadow set as corpus currently sees it, so the 60-second admission
  snapshot can only ever *remove* a source from the crawl, never add an unshadowed one.

### 7.3 · Why the milestone's four states became seven

The brief named `candidate → validated → rejected / admitted`. That is the shape of the *decision*.
`probing` and `incomplete` are the shape of the *failure*, and leaving them out makes both failures
indistinguishable from success:

* without **`probing`**, a process killed between "request sent" and "verdict written" leaves the
  host looking untouched — the next run cannot tell an interrupted host from a fresh one, and two
  concurrent runs cannot tell that a host is in flight;
* without **`incomplete`**, `validate`'s third verdict has nowhere to go. Folding it into `rejected`
  records a publisher as having refused us when our own network failed, and makes that permanent,
  because a rejection is never retried. `source_validation` was built around *a gate that cannot fire
  reading as a gate that passed*; this is its mirror — a gate that could not be **asked** must not
  read as a gate that **failed**.

`withdrawn` is the seventh, and it exists because admission is the first thing in this pipeline that
mutates serving state, and everything in this repository that mutates serving state is reversible.

### 7.4 · What was built

| piece | what it is |
|---|---|
| `examples/source_admission.py` | the state machine as **policy** — no store, no network, no env, mirroring `source_lifecycle.py`. `may_probe` is the single definition of resumable/idempotent/never-re-probed, so the runner cannot grow a second one |
| `store.SourceAdmission` | keyed on **host** (discovery has no outlet identity to key on). `state` and `tier` are separate columns; `probe_count` / `requests_spent` accumulate and are never reset |
| 12 store methods | seed, claim, record, admit, withdraw, reopen, census, and the two serving reads |
| `examples/source_campaign.py` | the runner: `seed` / `status` / `probe` / `admit` / `withdraw` / `reopen` / `emit-config`. A new file because `audit_source_discovery.py`'s first docstring line is *"Read-only: no writes, no ingestion, no curation"* — the sentence a ToS reviewer reads, and adding writes would have falsified it |
| `corpus.wire_admissions` | explicit, never implicit. Wiring from `Store.__init__` would have every test's store hijack a module global |
| `crawler.load_config(store_=)` | admitted rows appended; the hand-verified JSON wins on a duplicate publisher |

**Resume is a set difference over per-host state, not an offset.** That is not a stylistic choice:
the candidate ordering is by article count over a catalogue that keeps growing, so position *k* is a
different host on every run and "skip the first k" would silently skip the wrong hosts.

### 7.5 · What it does not do

It does not promote to Tier A, and no flag makes it. `source_admission.check_admission_tier` refuses
any tier but `shadow` at the policy, and `store.admit_source` refuses it again at the write. It does
not relax `RWE_CRAWL_ENABLED` (still off by default), the robots gate, the rate limiter, or the
offline discovery gates — the table changes **which** hosts are asked, never **how**. And it does not
discharge the ToS review, which still gates the 1,173-candidate campaign it makes runnable.

### 7.5b · The defect the production dry-run exposed: admission is a DEMOTION

Seeding on production put `sportskeeda.com` at the head of the probe queue with **5,079 articles**,
and that number is what makes the problem visible.

`source_discovery` mines the **crawl exhaust** — it finds hosts *we already ingest from* — and the
10-article floor guarantees every candidate has a history. Those articles are Tier A **today**,
because `corpus.DEFAULT_TIER` is `"A"` and nothing has said otherwise. So admitting a candidate is
not "add a source". It is an **A → shadow move on live rows**:

* they leave the **story partition** — `select()` drops non-Tier-A rows;
* they leave **Search and Discover** — `corpus.shadow_exclusions` hides the shadow lane from readers.

`source_lifecycle.crosses_tier_a("A", "shadow")` returns **True**, and `plan()` marks exactly that
move `automatic=False` requiring `NEEDS_COUNTERFACTUAL`. As first shipped, `admit_source` performed
it with `applied=True` and no counterfactual, and `admit --all-validated` did it in bulk behind a
message that read like an addition. **I built the thing M9 refuses to do, one table over.**

Verified rather than argued — 40 articles on one host, through `corpus.select`:

```
BEFORE admission   tier_of: A        in Tier A: 40 of 40    searchable: True
AFTER  admission   tier_of: shadow   in Tier A:  0 of 40    searchable: False
```

The move itself is defensible: carrying an unrated, unregistered host in the clustering corpus is
promotion by omission, and shadow is where an unevaluated source belongs. The defect was never
saying so. The fix:

* `store.admission_partition_impact(host)` reports **two** numbers — articles in the clustering
  window (the partition impact) and in the whole catalogue (the reader-surface impact, much the
  larger). Built on `list_discovery_rows`, memoized per `Store`, so a 1,173-host bulk admit pays the
  scan once rather than 1,173 times.
* `admit_source` **refuses** unless `accept_partition_change=True` whenever either count is non-zero.
  Computed by the store, so no caller can pass a convenient zero. A host whose articles have aged
  out is genuinely neutral and needs no acknowledgement — a guard that fired there would be
  demanding a counterfactual for a no-op.
* `source_campaign.py admit` prints a pre-flight naming the totals and the ten largest hosts, and
  exits 2 without `--accept-partition-change`.
* `store.admission_cohort_impact` sizes a whole state group, and `status` reports it for
  **`candidate` as well as `validated`**. Sizing only the validated set — which is what the first
  version did — delivers the number *after* the campaign the decision was about. Over `candidate`
  rows it is an upper bound (not every host will pass its probe), it costs **no network request**,
  and it reports the **share** of the catalogue alongside the count: "40,000 articles" and "27% of
  everything we carry" are the same fact, and only the second one is a decision.

**The first 54-test pass did not catch this, and the reason is the point of §7.6.** Every admission
test built an `source_admission` row without any `feed_articles` rows behind it — a state no real
candidate is ever in — so the guard could not fire and its absence was invisible. The new tests
ingest the catalogue rows first.

### 7.5c · The cohort sized on production, and why it should NOT be admitted as one

Measured 2026-08-27 running `415bc7c`, no network request:

```
1,173 host(s), 1,173 with live rows          <- not one candidate is neutral
  7,343 of  29,194 in the 6-day window       25.2%   would leave the STORY PARTITION
 39,119 of 150,110 in the catalogue          26.1%   would leave SEARCH and DISCOVER
```

Concentrated, and the concentration matters: **sportskeeda.com alone is 5,089 articles — 13.0% of
the cohort's mass and 13.2% of its window mass.** The top ten are 17.1%; the remaining 1,163 hosts
average 27.9 articles each. The cohort's recency profile (18.8% of its articles inside six days) is
indistinguishable from the catalogue's (19.4%), so these are actively-publishing hosts, not an
archive.

#### What those 39,119 articles actually do in Tier A today

This is the fact that decides the question, and it is already settled in the code.
`story_service._votes` / `_distribution` count only members with a `leanBucket`, and **every one of
these hosts is unrated by construction** — gate 7 is "not already tracked by the registry". So:

| | in Tier A today |
|---|---|
| vote in the L/C/R distribution | **no** — "counting it as centre would fabricate a lean (L2.2)" |
| set or move a blindspot claim | **no**, not directly — a blindspot is computed from the distribution |
| appear as `coverage` / `publishers` / `totalCoverage` | **yes** — "Both are still real COVERAGE" |
| help clusters form and grow (`MIN_SUPPORT`, link quorum) | **yes** |
| count toward `_cluster_trust(total, …)` | **yes** — so removing them changes trust verdicts, and therefore which blindspots are *asserted* |

So the thing that would justify shadowing them wholesale — *they are distorting our lean claims* —
**is already handled**. What they contribute is coverage and cluster mass, both of which admission
removes.

#### The reframe: admission exists to start a CRAWL, not to reclassify a backlog

The pipeline is discover → validate → **crawl** → shadow ingest → evaluate → promote. The point of
admitting a source is to fetch it *properly* — a feed or news sitemap on a schedule — instead of the
incidental handful that arrive via GDELT and aggregators. The shadow lane exists so that **new**
crawled volume does not flood Tier A unevaluated.

Moving the *pre-existing* 39,119 articles is a side effect of tier being an outlet-level,
whole-history property. `corpus.py` chose that deliberately and gives the reason: "a demotion (A→B
when an outlet turns out to be a syndicator) takes effect on the next build over the outlet's whole
history, which is what a demotion should mean." Right for a demotion. Unintended for an admission.

#### Recommendation

**Do not admit the cohort.** Admit small tranches where the crawl is actually wanted, and measure
each with `audit_source_cohort.py` — whose bar is already the right one, *"OTHER articles that LOST
their story"*, the articles stranded when a host whose links held a cluster together is removed.

> **That recommendation did not execute when it was written, and the tool has been extended so it
> does.** `audit_source_cohort.py` selected its own cohort by volume floor and had no way to take a
> host list — a recommendation pointing at a tool that cannot accept its input is the "diagnostic
> nothing invokes" defect this repository keeps finding, committed in a document rather than in code.
> It now takes `--hosts`, or `--from-admission {cheapest,candidate,validated,admitted}` with
> `--tranche N`, and filters rows through **`corpus._matches` itself** rather than a re-derived host
> match, so what is simulated is exactly what admission does — subdomains and bare-domain publisher
> strings included.

```
dc run --rm -T api python examples/audit_source_cohort.py --db "$RWE_DB_URL" \
    --from-admission cheapest --tranche 25
```

The sizing inverts the intuitive pick. **sportskeeda.com is the worst possible first admission**, not
the best: it is the largest existing contributor in the cohort, so admitting it removes 967 in-window
articles to gain a crawl of a source we already receive 5,089 articles from. `paloaltoonline.com` —
127 articles, a real local newsroom — is the shape the long tail of a 50,000-source corpus is
actually made of, and moving it costs 35 in-window articles.

A useful default tranche ordering: **ascending existing volume** (`--from-admission cheapest`), not
descending. The discovery report ranks by volume because volume is the evidence that a *request* is
justified; it is the wrong order for deciding what to *admit*, since the highest-volume candidate is
the one whose admission costs the product most.

**But ordering is not the answer, and a fixture in `tests/test_cohort_tranche.py` is the
demonstration.** Two hosts covering the same events, 14 articles and 11: admitting the *smaller* one
— the cheapest by volume — strands **all 14** of the other's articles, because the story loses the
support that held it together. Cheap to move is not the same as cheap to lose. Volume orders the
candidates; only the counterfactual measures them, which is why `--tranche` walks up in steps.

#### What this does not change

M11 is still the right milestone and still built: without it there is no resumable campaign, no
memory of a rejection, and no cross-process politeness. What the measurement changes is the *size of
the first batch* — and that it now has to be a decision rather than a default, which is what
`--accept-partition-change` makes it.

### 7.6 · Verification

55 tests in `tests/test_source_admission.py`, and the two that carry the requirement are
`test_a_second_full_campaign_makes_no_requests` and
`test_an_interrupted_campaign_resumes_where_it_stopped`. Both assert *how many times `validate` was
called* and *the per-host `probe_count`*, not that the output got shorter — the latter would pass for
any change that printed less.

Every guard was checked by breaking the product and confirming a test fails. **21 mutations, 21
caught:**

```
COMPLETED guard removed                          the probing claim is a no-op
re-seed downgrades the state                     record refuses nothing
check_admission_tier never raises                withdrawal clears the tier
corpus.enabled ignores admissions                the table REPLACES the env shadow list
crawl configs skip the is_shadow re-check        the runner swallows a real interruption
INCOMPLETE is recorded as a rejection            an incomplete probe gets no cooloff
_lifecycle_identity drifts from M8's              the partition guard never fires
the CLI pre-flight does not refuse               the window count is the catalogue count
shadow_exclusions stops hiding admitted hosts    status sizes only the validated set
the cohort share is dropped                      the catalogue is rescanned per host
cohort impact counts hosts, not articles
```

The last one is worth naming, because it is a defect I introduced and the mutation pass found rather
than the design: `admit_source` first wrote the lifecycle row under the raw publisher string, while
M8 keys its cohort through `audit_shadow_cohort._identity` (registry canonical, else lower-cased).
For any unregistered outlet stored under a mixed-case name those are **different keys**, so the
source would have looked un-evaluated forever while two rows described it. It is pinned
differentially — the test compares against the real function rather than restating the rule, because
a restatement would be the third copy and a third copy cannot detect drift in the other two.

Full suite: **4,041 passed, 9 skipped**.


---

## 8 · The blocker the M11 tranche run exposed: the tokenizer excludes most of the world

`audit_source_cohort.py`'s language table has carried this line for two days: *"every non-Latin-script
language sits at exactly 0%, which is a question about the tokenizer, not a finding."* Running it for
the M11 tranche made it worth answering. It is both.

### 8.1 · Measured

`clustering.title_tokens` matches `[a-z0-9]+`. That is ASCII-only, so it returns **zero tokens** for
a Korean, Arabic, Chinese, Japanese, Russian, Tamil or Hindi headline. And `clustering.pair_admits`
rejects on token count **before any other test**:

```python
floor = max(1, min_tokens)
if len(tx) < floor or len(ty) < floor or len(tx & ty) < min_shared:
    return False
```

So those articles **cannot join a story under any configuration**. Not "cluster poorly" — there is no
threshold that admits them and no second route in; the `evidence` hook is an additional veto, not an
alternative path. Production, same run:

| | outlets | articles | in-story | |
|---|---:|---:|---:|---:|
| ko | 4 | 118 | 0 | 0% |
| ar | 6 | 98 | 0 | 0% |
| ru | 5 | 90 | 1 | 1% |
| zh | 4 | 67 | 0 | 0% |
| ja | 3 | 52 | 0 | 0% |
| ta | 1 | 47 | 0 | 0% |
| hi | 2 | 44 | 2 | 5% |
| **non-Latin** | **23** | **516** | **3** | **0.6%** |
| Latin, non-English | 36 | 632 | 81 | 12.8% |
| en | 225 | 17,490 | 4,987 | 29% |

**It is not only an international defect.** `Erdoğan` tokenizes to `erdo` and `Orbán` to `orb`, so two
ENGLISH headlines about one event — one keeping the diacritics, one not — share only `budapest` and
`meets`, fall below `MIN_SHARED_TOKENS`, and land in different stories. Accented Latin does not fail
outright; it fragments (`kündigt` → `ndigt`, `cumhurbaşkanı` → `cumhurba`), which is consistent with
de at 22% and tr at 9% against en's 29%.

### 8.2 · Why this outranks the rest of the roadmap for a 50,000-source corpus

A 50,000-source corpus is necessarily international, and Hidden View's product is cross-source
comparison. A source whose articles cannot join a story contributes nothing to coverage, nothing to a
blindspot claim, and nothing to a lean distribution — only Search volume. **The tokenizer already
enforces a de facto Tier B on every non-Latin-script outlet**, and M9 could "promote" one to Tier A
today with no effect whatsoever.

That reframes M11's own numbers. Admitting a Korean or Arabic source is currently free of partition
cost — those rows were never in a story — and equally free of partition *benefit*.

### 8.3 · What was built: the instrument, not the fix

`title_tokens` decides the story partition for the entire product, and this repository has already
measured one tokenizer candidate against the live catalogue and **rejected** it: `hyphen_compounds`
split 121 clusters and dropped 2.6% of covered articles. So this lands the same way — a candidate
plus an instrument, defaulted off, shipping nothing:

* `clustering.title_tokens(..., unicode_words=True)` — `\w` plus combining marks, with character
  **bigrams** for scripts that have no word separator (CJK, Thai). Two mechanisms, because they are
  two different problems: Hangul is deliberately word-split, not bigrammed, since Korean uses spaces.
  Grouping "non-Latin" into one bucket is the error the range list exists to avoid.
* Combining marks are included because `\w` excludes categories `Mn`/`Mc`. Without them Tamil
  `அதிபர்` splits at U+0BBF into two-character fragments the length floor drops — `\w+` alone left
  Tamil and Hindi at **zero** usable tokens, so the candidate would have looked like it fixed
  "non-Latin scripts" while leaving two of the largest exactly as broken.
* `story_service.unicode_words()` / `RWE_CLUSTER_UNICODE_WORDS`, resolved **once per build** and
  threaded to the clusterer, the repair re-split, the event-identity closure and the template gate —
  the same signal in all four, which is what `article_tokens` exists to guarantee.
* `audit_clustering_change.py --unicode-words`.

**What it deliberately does NOT do is fold diacritics**, so `Erdoğan` and `Erdogan` remain different
tokens and the English pair above still fails to cluster. Folding is a separate candidate with a
separate risk profile — it merges Turkish `ı`/`i` and German `ö`/`o` — and pairing them would make
one measurement unable to attribute either result.

### 8.4 · Verification

54 tests. The load-bearing one is byte-identity of the default path, asserted against a
re-implementation of the shipped expression rather than against the function under test. 10
mutations, 10 caught — after three that were **missed** and are worth recording, because all three
were the same shape:

| missed at first | why the tests could not see it |
|---|---|
| the flag never reaches `clustering.cluster` | every test exercised `title_tokens` directly, so the instrument would have reported healthily while changing nothing |
| the flag never reaches the event-identity closure | counting in-band edges passes either way — a closure with no tokens scores 0.0, which is *below* any band ceiling, so it bands everything. The separating assertion is the opposite one: the fixture scores 0.6, above the 0.5 ceiling, so a correctly-wired closure bands **nothing** |
| the flag never reaches the template gate | the gate is off by default, so the mutation was inert until a test switched it on |

One pass-through — `derived_boilerplate` — is wired and **not** pinned by a test. Its observable is a
corpus-derived document-frequency set, and a four-article fixture cannot exercise one meaningfully;
recorded rather than papered over.

### 8.5 · The measurement, run — and why ADOPT is not yet supportable

```
before : 1,612 stories, largest 70   [PRODUCTION BASELINE]
after  : 1,623 stories, largest 70
clusters split 65   merged 5
articles in a story: 6,870 -> 6,826  (dropped out 150, newly covered 106)
blindspot claims: 223 -> 222     independent signal: 0/97 bad, mean 0.97 -> unchanged
exhibits: both in-window ones unchanged [ok]
VERDICT: ADOPT (dropped 2.2% of covered articles)
```

**Strictly better than the candidate this repository rejected.** `hyphen_compounds` was turned down
at 121 splits, 2.6% dropped, and a *falling* story count. This is 65 splits, 2.2%, and +11 stories.

**And it still should not be adopted on that, because the benefit is unmeasured.** The tool's own
docstring says so — *"The VERDICT line is a COST check, not the whole criterion… Two candidates have
now printed ADOPT and been rejected on the rest of it"* — and the specific gap is that
**`newlyCovered: 106` does not say who**. 106 rescued Korean and Arabic articles and 106 more English
wire duplicates print the same number. That is the defect `audit_source_cohort` already had to fix in
itself: *"29 collateral losses against an unquantified good is not a trade, it is half a trade."*

The losses point the same way. The largest single drop is a 17-article, 8-publisher **Vietnamese**
cluster that dissolved entirely, and Turkish and more Vietnamese clusters follow it. Vietnamese and
Turkish are accented Latin — precisely where the change is most aggressive, because their words go
from ASCII fragments to whole words. Whether those splits are *corrections* (clusters that were held
together by fragment coincidence) or *regressions* (real events now severed) is not decidable from a
count, and only two of fourteen ratified exhibits are in this window, so the rubric contributes
almost nothing here.

### 8.6 · What was built in response: the benefit half

`audit_clustering_change.py` now reports **who a change reached**, split by the defect rather than by
a language guess:

```
  population    articles  covered before    after  dropped   newly
  reachable            4               4        4        0       0
  excluded             4               0        4        0       4

  the trade: 4 article(s) reached a story that structurally could not,
             against 0 lost from stories that already worked.
```

`excluded` is every article whose headline yields fewer than `MIN_TITLE_TOKENS` under the **shipped**
tokenizer — the exact condition `pair_admits` rejects on, derivable from the title alone, and
independent of `language`, which is populated for ~80% of rows and 0% for some adapters. Its
`dropped` is 0 by construction, so its `newly` **is** the entire measured benefit. A change that
reaches nobody now prints `*** THE BENEFIT IS ZERO ***` rather than leaving it to be inferred.

A per-language table follows as the secondary view, and `member_key` is now shared with
`audit_source_cohort` and pinned differentially — the lookup whose earlier drift "invalidated the
first two production runs" and reported participation 20× low.

### 8.7 · The re-run: REJECTED, on the evidence the reach table added

```
  population    articles  covered before    after  dropped   newly
  reachable       26,522           6,887    6,765      149      27
  excluded         2,630               0       78        0      78

  the trade: 78 article(s) reached a story that structurally could not,
             against 149 lost from stories that already worked.
```

**VERDICT: ADOPT. The answer is no.** Two independent reasons, and the second is the larger:

1. **The cost is 1.9× the benefit.** 149 articles lost against 78 rescued; net coverage −44.
2. **It does not fix the defect it was built for — 3.0% reach.** 2,630 articles in the window are
   structurally excluded; the change reaches 78 of them.

By language, before → after covered:

| | before | after | dropped | newly | |
|---|---:|---:|---:|---:|---|
| vi | 32 | **0** | 32 | 0 | **wiped out** |
| tr | 22 | 11 | 11 | 0 | halved |
| en | 5,154 | 5,137 | 21 | 4 | |
| ar | 0 | 9 | 0 | 9 | |
| ja | 1 | 8 | 1 | 8 | |
| ko | 0 | 4 | 0 | 4 | |
| ru | 0 | 3 | 0 | 3 | |

The cost lands on **accented Latin**, and the mechanism is clear: Vietnamese and Turkish words
fragment into short ASCII pieces today, many *unrelated* articles share those pieces, and replacing
them with whole words dissolves the clusters that coincidence built. `--pieces` shows the largest
loss — a 17-article, 8-publisher Vietnamese cluster — dissolving to **zero** pieces, not to a
smaller core. A genuine eight-publisher event would retain one; dissolving completely is what a
false merge looks like. So those splits are plausibly *corrections* — but corrections nobody asked
for, at a price paid in coverage.

### 8.8 · The finding underneath, which is bigger than the tokenizer

**3% reach is the number to take away.** Giving a Korean headline tokens does not give it a Korean
*peer* to cluster with. A story needs ≥ `MIN_SHARED_TOKENS` with another article, from a different
publisher, inside the same six-day window — and the window holds 139 Korean articles across 4
outlets, 98 Arabic across 6, 52 Japanese across 3.

**The binding constraint on international stories is corpus density per language, not the
tokenizer.** The tokenizer is necessary and nowhere near sufficient.

That connects straight back to M11 and changes what the 50k expansion is *for*. Discovery ranks
candidates by article volume, which is dominated by English, so a volume-ordered expansion adds
English sources to a corpus that already clusters English well. **Reaching international stories
needs language-targeted admission** — enough peers per language to clear the shared-token floor —
which is a different selection rule from the one `source_discovery` implements today.

### 8.9 · The variant the measurement indicates

`--unicode-fallback`: take the Unicode path **only when the ASCII tokenizer yields fewer than
`MIN_TITLE_TOKENS`**. An article that already clusters keeps its exact token set, so it cannot lose
one and the 149-article cost is **zero by construction**; the excluded population gets exactly the
tokens replace gave it. Built, defaulted off, not yet measured:

```
dc run --rm -T api python examples/audit_clustering_change.py --db "$RWE_DB_URL" \
    --unicode-fallback --pieces 5
```

The prediction, stated before the run so it can be wrong: `reachable → dropped` should be **0 or
very near it**, and `excluded → newly` should be **at least the 78** replace achieved. If dropped is
materially above zero, the fallback is leaking through cluster composition — an excluded article
joining a cluster can still change it — and that is worth knowing precisely.

It still will not move the 3% reach, because that is not a tokenizer problem. §8.8 is the milestone,
and it is now designed in full: **`docs/M14_LANGUAGE_DENSITY_DESIGN.md`**.

That design's own headline finding is sharper than §8.8 stated. `RWE_STORIES_MAX_SCAN` caps the
clustering window at 60,000 rows against today's 29,152, and the M11 cohort measures 6.3 in-window
articles per long-tail host — so **Tier A can absorb roughly 4,900 more sources and then it is
full**. The 50,000-source corpus is ~5,000 clustering sources and ~45,000 searchable ones. M14 is
therefore an *allocation* policy for a bounded resource, and today's volume ordering spends the whole
budget on English.
