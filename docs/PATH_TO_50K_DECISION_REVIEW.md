# What is actually stopping us reaching 50,000 outlets — a decision review

**Decision review, not a design. No code changed, and deliberately no new milestone.** M14 Stage 0
failed its pre-registered bar; this asks whether the goal it was serving is even well posed, and what
the next step toward it actually is.

Provenance on every number: **[M]** measured on production · **[D]** derived by arithmetic over
measured values · **[P]** projected from a measured rate, assumption stated · **[A]** untested
assumption.

---

## 0 · The decision

> **We are already at 9,397 outlets [M], and we got there with the crawler switched off and zero
> sources admitted.** Under the loosest reading of the goal we are 19% of the way; under the reading
> that matters we may be much further, because the roadmap's own model of "50,000 sources" assumes
> 3–10 articles per outlet per day and the **observed rate is 1.00** [D]. Nothing in discovery,
> validation, admission or ingestion throughput is close to binding.
>
> **The one thing that is genuinely missing is Tier B.** The roadmap's one-paragraph answer says
> *"Tier B scales to 50k because assignment is linear in new arrivals and cannot alter the
> partition… That one change is the first milestone and every other stage depends on it."* **M4 —
> Tier B story attachment — is not built, and Tier B has zero members** [M]. Every extra outlet
> therefore lands in Tier A, which is capped at 60,000 rows and truncates the clustering window to
> **29 hours** at 50k outlets [D].
>
> **M14 was a side investigation.** It refuted one way of improving *international clustering*; it
> revealed nothing about reaching 50,000 outlets, because outlet count was never its subject.

---

## 1 · "50,000 outlets" is not one number. It is five, and they differ by 30×

| # | population | definition | today | to reach 50k |
|---|---|---|---:|---:|
| A | **ingested / searchable** | distinct hosts with ≥1 article in the catalogue | **9,397** [M] | 5.3× |
| B | **materially present** | hosts with ≥10 articles in the retained catalogue | **1,525** [M] | 33× |
| C | **actively polled endpoints** | things the poller fetches on a schedule | **9 ingesting adapters** + the RSS list [M] | — |
| D | **Tier A (clustering)** | forms and votes in stories | **9,397** [M] — everything, by `DEFAULT_TIER` | 5.3× |
| E | **Tier B** | searchable, attributable, never clusters | **0** [M] | — |

Three consequences, and each of them changes the plan.

**D equals A today.** `corpus.DEFAULT_TIER` is `"A"`, so every outlet we ingest already forms and
votes in stories. We are *already running* a 9,397-outlet Tier A. If the goal were population D, the
answer to "what is stopping us" would be "nothing — go and ingest", and the interesting question
would be whether that is desirable rather than whether it is possible.

**The roadmap's volume model is 3–10× too high.** It sizes 50k as 150,000–500,000 articles/day.
Observed: 29,189 articles in the 6-day window across 4,854 outlet identities = **1.00 article per
outlet per day** [D]. At the observed rate, 50,000 outlets is **50,112 articles/day — 10.3× today**
[P], not 31–103×. Every capacity break in the roadmap is real but further away than modelled, and
sizing a plan against an assumption 3–10× off is worth correcting before spending on it.

**Population C is the one nobody is counting, and it is the cheap one.** Nine ingesting adapters
produce 9,397 hosts, because the aggregators fan out. We have never polled anything like 9,397
endpoints. Whether reaching 50,000 requires polling 50,000 endpoints is the single largest unexamined
assumption in the whole roadmap — see §4.5.

---

## 2 · Did M14's failure reveal a 50k blocker? **No.**

M14 asked: *does adding publishers in an under-represented language raise that language's
cross-publisher density?* The answer was no, with 15 monotonicity breaks [M].

That is a finding about **story formation quality**, not about **source count**. Reaching 50,000
outlets requires nothing from M14 — the two are orthogonal:

* M14 would not have added a single outlet. Its output was an *ordering* over candidates.
* Its refutation does not remove any path to 50k. It removes a hypothesis about what would make
  international stories form once the outlets are there.

**M14 was a side investigation and should be closed as one.** Three things it produced do matter,
and none of them is a milestone:

| | | why it matters here |
|---|---|---|
| the unlabelled quarter | ~4,700 rows, 16% of the window, no `language`; 1,203 of them non-Latin **and** tokenizer-dead [M] | a metadata gap that will bite *any* future per-language work; independent of 50k |
| the validated instrument | co-coverage 28.2% vs 27.9% story participation measured independently [M] | we can now measure co-coverage cheaply and trust it |
| **Tier A is bounded** | 60,000-row cap vs 29,189 today = ~2× headroom [D] | **this one is a genuine 50k constraint**, and it is the subject of §3 |

---

## 3 · The blocker nobody has been looking at: **Tier B does not exist**

The roadmap's own summary makes Tier B load-bearing:

> "Tier B scales to 50k because assignment is linear in new arrivals and cannot alter the partition.
> **That one change is the first milestone and every other stage depends on it.**"

Status [M]: **M4 (Tier B story attachment by assignment) is not in the built list**, `story_service`
contains no attachment path, and `RWE_CORPUS_TIER_B` is unset — **zero outlets are in Tier B**. The
only Tier B code that exists is `corpus.sql_exclusions`, which *removes* Tier B rows from the
clustering corpus. So today Tier B is a hole, not a tier: an outlet placed in it would vanish from
stories and gain nothing.

That is the actual wall, and the arithmetic is unforgiving [D]:

```
50,000 outlets x 1.00 article/day  =  50,112 articles/day
6-day window                       =  300,670 articles
RWE_STORIES_MAX_SCAN               =   60,000 rows      -> 5.0x over
effective clustering window        =  29 hours, not 6 days
```

**Every outlet added beyond ~2× today's volume makes the product worse until Tier B exists**, by the
exact mechanism `_fetch`'s docstring already records from the last time this happened: *"every
provider added shrank the hours those 2000 rows covered, so integrating more sources produced FEWER
stories."*

This is not a new discovery — it is Break #1 of the roadmap, written down at the start and then
overtaken by eight milestones of discovery and admission machinery for sources we cannot yet afford
to ingest.

---

## 4 · The constraints, ranked, with provenance

Ranked by *does it bind within the next 5×*, then by whether it is measured.

### 4.1 · ToS / legal review — **binds at any size** · [M] outstanding

Unchanged since M7. Not an engineering item, and it gates the 1,173-candidate probe campaign that
M11 made resumable. It does **not** gate aggregator-side growth, which is why §6's experiment avoids
it entirely.

### 4.2 · Tier B does not exist — **binds at ~2×** · [M] M4 unbuilt, 0 members

§3. The mechanism the plan rests on has never been built or run.

### 4.3 · Tier A row cap (60,000) — **binds at ~2×** · [M] cap, [D] projection

Below `corpus.tier_a_budget()` (83,000), so the *row cap* is the binding constraint, not CPU
(`story_service.max_scan_default` says so explicitly). Same wall as 4.2 seen from the other side: it
binds only because everything is Tier A.

### 4.4 · Storage — **binds between 1×/day and 3×/day** · [D]

3,911 bytes/article [D from 587,153,280 / 150,110]. At 50k outlets:

| assumed rate | 30-day catalogue | vs 12 GB free [M] |
|---|---:|---|
| observed 1.0/day | 5.9 GB | fits |
| roadmap 3/day | 18 GB | **does not fit** |
| roadmap 10/day | 59 GB | does not fit |

So storage is *not* a blocker at the observed rate and *is* one at the assumed rate. Which is
correct is an empirical question nobody has asked, and it decides whether a volume upgrade is needed
at all.

### 4.5 · Polling interval / lock budget — **binds only under an untested assumption** · [D] + [A]

50,000 individually-polled sources at 900 s = 200,000 polls/hour against a ~9,000/hour budget = 22×
[D]. **But the assumption that 50,000 outlets requires 50,000 polled endpoints is untested [A]**, and
the evidence points the other way: 9 ingesting adapters currently yield 9,397 hosts [M]. M12 exists
to fix a constraint we may never encounter.

### 4.6 · Discovery — **not binding** · [M]

1,173 candidates, 9,397 hosts seen, 1,525 above the floor [M]. Supply is not the wall at the next 5×.

### 4.7 · Validation / admission — **not binding** · [M]

M11 built, resumable, idempotent, verified on production. **Zero sources admitted** [M] — the
machinery works and has nothing to do, because there is nowhere affordable to put them (§3).

### 4.8 · Ingestion throughput — **not binding, by 569×** · [M]

330 articles/s measured with zero lock errors; 50k outlets needs 0.58/s [D]. This was M3's headline
finding and it still holds.

### 4.9 · Clustering — **not binding on source count** · [M]

Tier A is bounded by design. Clustering quality is a separate axis (M13/M14) and does not constrain
how many outlets we carry.

### 4.10 · Cost — **unmeasured** · [A]

The aggregator APIs (NewsAPI, NewsData, GNews, MediaStack, Currents, Guardian) have quota tiers, and
**nobody has costed what 5× the current article volume does to them.** If the cheapest path to 50k
outlets is aggregator breadth (§4.5), then API quota is the real constraint on that path and it is
completely unquantified. This is the largest gap in the evidence base.

---

## 5 · Predict before ingesting, or ingest → observe → promote?

**Observe. The architecture already decided this, and M14 quietly reversed it.**

`audit_shadow_cohort` states the principle: *"would this article have joined a story, had it been
allowed to?"* — measured with the clusterer's own rule against articles we hold, not predicted from
metadata. The shadow lane exists precisely so prediction is unnecessary: ingest with zero product
exposure, observe for 14 days, measure, then promote selectively.

Prediction is worse on both sides of the split:

* for a **genuinely new** source there is nothing to predict from — no articles, no headlines, no
  co-coverage. Any predictor is a proxy for a proxy;
* for a **catalogue-resident** source prediction is pointless, because the articles are already here
  and can simply be measured. All 1,173 M11 candidates are in this category [M].

M14's `Δ` metric was a predictor. Its one legitimate use is **ordering probe effort** — a cost bound
on which publishers to spend requests on — not deciding admission.

**One caveat that matters:** "ingest to Tier B → observe → promote" is a plan, not a capability.
The pipeline as built routes admitted sources to **shadow**, not Tier B, and Tier B has no
attachment path (§3). So the answer to the question is *observe*, and the reason we cannot yet act on
it is the same missing piece as everything else.

---

## 6 · The smallest experiment that removes the biggest uncertainty

The biggest uncertainty is not "which sources should we add". It is:

> **Does Tier B work at all?**

Everything downstream of it — the 45,000 outlets that the 50k goal is mostly made of, the claim that
assignment is linear, the claim that it cannot alter the partition — is **untested [A]**. If Tier B
works, 50k is largely a data and cost exercise. If it does not, eight milestones of admission
machinery are pointed at a lane that cannot receive them.

**The experiment: put a measured slice of outlets we already carry into Tier B, and read the existing
bars.**

* **No new sources, no network, no ToS exposure, no probing.** It uses the catalogue we hold.
* The candidates already exist: the 8 outlets `audit_source_cohort` gives a `TIER B` verdict on
  syndication and host-instability grounds [M] — outlets a measured, published bar already says do
  not belong in Tier A.
* The counterfactual already exists: `audit_source_cohort --hosts` measures exactly this move and
  has already reported its cost — **42 articles stranded, 4 blindspot claims lost, against 86
  double-counted coverage entries removed** [M].
* What is missing is only the *other half*: those outlets currently **disappear** rather than attach.
  M4 is the attachment.

Three questions it answers, in order of how much they matter:

1. **Does assignment preserve the partition?** The roadmap's central claim, and the containment test
   is already specified as "byte-identical".
2. **What does assignment cost per new arrival?** The claim is *linear*; it has never been timed.
3. **Is a story with Tier B coverage still the product?** Tier B outlets are unrated, so they add
   coverage and no lean vote. This is the same question §8.2 of the M14 design raised and did not
   answer — and it is a product decision, not a measurement.

If (1) or (2) fails, the 50k plan needs rethinking and it is far better to learn that from 8 outlets
than from 40,000.

---

## 7 · What this review is NOT proposing

* **Not a replacement milestone for M14.** M14 is closed as a side investigation.
* **Not "beat overlap" as the next hypothesis.** It remains an unverified explanation of one
  observation, with a named check (syndication on `nl`) nobody has run. It belongs to international
  clustering quality, which is not on the 50k path.
* **Not more discovery, admission, or crawler work.** All built, none binding, and the crawler has
  never ingested an article [M: `RWE_CRAWL_ENABLED` off, 0 sources admitted].
* **Not a decision on the ToS review.** Still outstanding, still the user's, and still gating the
  probe campaign — but no longer on the critical path, because the critical path no longer runs
  through crawling.

## 8 · The two questions this review cannot answer

Both are decisions, not measurements, and both change the plan:

1. **Which population is the goal?** If "50,000 outlets" means population A (ingested and
   searchable), we are 19% there and the path is aggregator breadth plus Tier B. If it means
   population C (individually polled sources with a direct relationship), it is a far larger and
   more expensive programme, and the roadmap's crawler machinery is the right investment after all.
   **These differ by more than an order of magnitude in cost and the roadmap does not distinguish
   them.**
2. **Is a coverage-only story the product?** Tier B and unrated Tier A sources both produce stories
   with coverage and no lean distribution. Answering "no" makes rating throughput the binding
   constraint on everything, and no amount of source admission moves it.
