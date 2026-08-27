# What limits the real source pipeline — and the next milestone

**Audit only. No code changed.** The question: with M6, M7, M8, M9 built and M3's highest-value
fixes deployed, what actually stops the crawl cohort growing from 2 real sources to 100, 1,000,
10,000 and 50,000?

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
| 1 · discover | `source_discovery.py` — *"Pure: no store, no network, no environment, no writes"* | **no** — stdout |
| 2 · validate | `source_validation.py` — *"no store, no environment, no writes"*, 8 gates | **no** — stdout + optional `--out` |
| **⟂ admit** | **a human edits `examples/data/crawler_publishers.json`** | **requires a git commit + deploy** |
| **⟂ admit** | **a human edits `RWE_CORPUS_SHADOW`** | **requires a container restart** |
| 3 · shadow ingest | `crawler.CrawlAdapter`, gated on `RWE_CRAWL_ENABLED` **and** `in_shadow()` | articles ✔ |
| 4 · evaluate | `source_evaluation.py` — pure | via M9 ✔ |
| 5/6 · promote | `source_lifecycle.py` — *"emits the configuration; it never mutates serving state"* | `SourceLifecycle` + append-only ledger ✔ |
| **⟂ apply** | **a human edits `RWE_CORPUS_TIER_B`** | **requires a container restart** |

The right-hand column is the finding. **Persistence begins at stage 3** — `SourceLifecycle.STATES`
is `("shadow", "B", "A", "dormant", "retired")` and there is **no `candidate` and no `validated`
state**, so the ledger only starts tracking an outlet once it is already ingesting. Everything
before that is a batch job whose output a person retypes.

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
| **L1** | **Admission requires a code deploy** — crawl config is baked into the image | **100 – 1,000** | **none** |
| **L2** | **Validation is not resumable** — verdicts persist nowhere | **any rung; matters at 1,000+** | **none** |
| L3 | Polling interval must scale with N (roadmap B1) | 1,000 – 10,000 | designed in M6 (interval ceiling + dormancy), not built |
| L4 | Thread-per-adapter — 1,013 threads at 1,000 sources | ~1,000 | **built** — M6.3's pool, `RWE_POLL_WORKERS`, currently 0 |
| L5 | Tier lists in environment variables | ~30,000 | designed as M3/D6, not built |
| L6 | ToS / robots review | **any real expansion** | **not an engineering item** |
| ~~L7~~ | ~~Tier-A clustering grows with the cohort~~ | — | **cleared — structurally impossible; see §4** |

**L0 binds first and hardest.** L1 is painful but has a working path — 177 sources is one or two
batched deploys. L0 has no path at all: past ~180 there is nothing left to admit. Removing L1 raises
the ceiling from 177 to 177; removing L0 raises it into the thousands. That ordering is what changed
when the production numbers arrived.

L1 and L2 remain real, they are the *next* milestone after L0, and they are the same change as each
other.

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
| M11 | Source admission becomes data — one table replacing `crawler_publishers.json` and the tier env vars; `candidate`/`validated` states so a campaign is resumable. Subsumes M3's D6 | 100 – 1,000 |
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

**Built, not yet verified on production.** `store.list_discovery_rows()` is the narrow projection
(six fields: the host pair, publisher, language, publishedAt, and sourceType for the runner's
language table); `audit_source_discovery.py` calls it instead of `story_service._fetch`, keeping the
tier exclusions and dropping the window. `--window-days 6` reproduces the old behaviour so the change
stays auditable against what it replaced.

The bar is checked by re-running Stage 1 — offline, no network, no writes:

```bash
cd /opt/ih && source deploy/ops/_compose.sh
dc exec -T api python examples/audit_source_discovery.py 2>&1 | head -8
dc exec -T api python examples/audit_source_discovery.py --window-days 6 2>&1 | head -8
```

The second command must still report ~177, or the two runs are not measuring what they claim.
