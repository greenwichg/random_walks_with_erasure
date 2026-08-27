# What limits the real source pipeline — and the next milestone

**Audit only. No code changed.** The question: with M6, M7, M8, M9 built and M3's highest-value
fixes deployed, what actually stops the crawl cohort growing from 2 real sources to 100, 1,000,
10,000 and 50,000?

---

## 0 · The answer in one paragraph

**Every stage of the pipeline is built. The joins between them are not.** `discover → validate →
crawl → shadow ingest → evaluate → promote` is five pure modules — each one explicitly *"no store,
no network, no environment, no writes"* — connected by a human editing two files. One of those files
is `examples/data/crawler_publishers.json`, which ships **inside the image**, so admitting a source
is a code deploy. The other is `RWE_CORPUS_SHADOW` in `deploy/.env`, which is a container restart.
Nothing persists a discovery candidate or a validation verdict, so a 50,000-host validation campaign
**re-probes every publisher on every run**. Supply is not the problem — Stage 1 already found
**3,729 unrated outlets sitting in the catalogue**, for free, with no crawling at all.

**The next milestone is M10: source admission becomes data rather than deployment.** It is the only
step in the chain with no mechanism at all, it binds between 100 and 1,000 sources, and it happens
to be the same change M3's audit independently identified as D6/S8.

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
| **L1** | **Admission requires a code deploy** — crawl config is baked into the image | **100 – 1,000** | **none** |
| **L2** | **Validation is not resumable** — verdicts persist nowhere | **any rung; matters at 1,000+** | **none** |
| L3 | Polling interval must scale with N (roadmap B1) | 1,000 – 10,000 | designed in M6 (interval ceiling + dormancy), not built |
| L4 | Thread-per-adapter — 1,013 threads at 1,000 sources | ~1,000 | **built** — M6.3's pool, `RWE_POLL_WORKERS`, currently 0 |
| L5 | Tier lists in environment variables | ~30,000 | designed as M3/D6, not built |
| L6 | ToS / robots review | **any real expansion** | **not an engineering item** |
| ~~L7~~ | ~~Tier-A clustering grows with the cohort~~ | — | **cleared — structurally impossible; see §4** |

L1 and L2 are the only two with no mechanism at all, they are the two earliest, and **they are the
same change**.

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

### M10 — Source admission becomes data, not deployment

One table, and the three readers that currently read a file or the environment read it instead.

**What it holds:** identity, hosts, lifecycle state (extended with `candidate` and `validated`),
tier, discovery URLs, the validation verdict and when it was reached, enabled flag.

**What changes:**

1. `crawler.load_config()` reads rows rather than `examples/data/crawler_publishers.json`.
2. `corpus.tier_index()` reads rows, with the existing env vars kept as an override so nothing breaks
   on day one. `corpus.tier_resolver()` — landed in M3/D2 — is already the seam for this.
3. `source_validation` writes its verdict, making a campaign resumable and idempotent per host.
4. M9's transitions become applyable rows; `SourceLifecycleEvent.applied` finally means something.

**Why it is the bottleneck now, and not before:** until M7/M8/M9 existed there was nothing to admit
and nothing to promote. They exist, they are validated, and Stage 1 has already found **3,729
candidates in the catalogue at zero network cost**. The pipeline is starved at exactly one step —
the one that needs a deploy.

**What it enables, concretely:**

* **100 → 1,000 sources without a deploy per cohort.** Admission becomes a row, so validated sources
  can be admitted continuously as a campaign completes rather than in image-sized batches.
* **A resumable validation campaign** — the property that makes 10,000+ hosts defensible to probe at
  all, because a restart no longer re-asks publishers who already answered.
* **The shadow↔B half of M9 can actually close its loop.** Those transitions are already
  `automatic=True` and already proven safe (neither side clusters); today they still stop at a
  config diff a human types.
* **It removes L5 for free** — a 22 MB config file and a 1 MB environment variable both disappear
  from the 50,000-source path, which is M3's D6 arriving as a side effect rather than as its own
  milestone.

**What it explicitly does not do:** it does not touch Tier A (§4), it does not fix L3 (interval
scaling — that is the milestone *after* it, binding at ~1,000–10,000), and **it does not discharge
the ToS review**, which still gates any real expansion at any size.

### The one thing to do before M10 is scoped

L1 and L2 are structural and measured from the code. The candidate pool is not: **3,729 unrated
outlets** comes from `source_discovery.py`'s docstring, measured when M7 was built. M10 should be
sized against today's number, and Stage 1 costs nothing to re-run because it touches no publisher:

```bash
cd /opt/ih && source deploy/ops/_compose.sh
dc exec -T api python examples/audit_source_discovery.py 2>&1 | head -40
```

No `--probe`, so no network, no writes, no ingestion — it is a `GROUP BY` over rows already paid
for. It reports how many candidate hosts exist today and what the eight gates say about them
offline, which is what turns "100 → 1,000" from a target into a worklist.
