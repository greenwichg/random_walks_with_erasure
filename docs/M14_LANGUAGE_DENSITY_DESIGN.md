# M14 — Language-targeted source admission

**Design only. No code changed.** The question: `--unicode-words` rescued 78 articles and cost 149,
reaching 3.0% of the population it was built for. If tokenization is not the binding constraint on
international stories, what is, and what admission strategy actually moves it?

---

## 0 · The answer in one paragraph

> **Tier A is a bounded, scarce resource — about 2× today's volume — and M14 is an allocation policy
> for it, not an admission-volume problem.** English is 63% of the clustering window and participates
> at 27.9%; everything that is neither English nor unlabelled is 7.5% of the window and participates
> at ~7%. The mechanism is `min_publishers = 2`: a story requires two *distinct publishers* covering
> one event within six days with ≥ 3 shared headline tokens, so what a language needs is not articles
> but **cross-publisher redundancy**. That makes the correct ranking metric the *marginal* number of
> new cross-publisher pairs a candidate creates in its language — a submodular coverage objective,
> greedily maximised — and it makes raw volume actively misleading: `sportskeeda.com` is the largest
> candidate in the pool at 5,089 articles and plausibly has near-zero marginal co-coverage, because
> nothing else in the corpus covers Indian sports.
>
> Three things have to be true before any of it works, and two of them are not yet: the **peer
> hypothesis is formally refuted in this repository** and has to be re-opened on evidence (§2); the
> **existing shadow-evaluation harness cannot measure a density expansion** — it scores attachment to
> stories that do not exist yet, so every candidate in a zero-coverage language scores 0% by
> construction (§5); and **Tier A promotion needs a lean rating**, which for most of these languages
> nobody has (§8).

---

## 1 · What a story actually requires

Everything below follows from four constants, so they are stated first:

| | | where |
|---|---|---|
| `min_publishers` | **2** distinct publishers | `story_service.build_stories` |
| `min_articles` | 2 | same |
| `MIN_SHARED_TOKENS` | 3 distinctive shared tokens | `clustering` |
| window | 6 days | `clustering.DEFAULT_WINDOW_DAYS` |

So: **two publishers must cover the same event, within six days, and their headlines must share three
content tokens.** Nothing else creates a story. A language with a thousand articles from one
publisher produces exactly zero.

That is why "more sources" is the wrong frame and "more *co-covering* sources" is the right one.

---

## 2 · The premise has a recorded refutation, and it has to survive it

`audit_source_cohort.verdict` carries this, and it is unambiguous:

> With the lookup fixed, the peer hypothesis is **refuted by its own measurement**: English with 214
> peers participates at 27%, Vietnamese with SIX peers at 30%. Peer count does not predict
> participation… **Two proposed justifications have now failed. Until a third survives contact with
> the data, low participation is an observation, not a verdict.**

M14's premise is that peer density *is* the constraint. That contradicts a measurement this
repository took deliberately and wrote down. It cannot simply be asserted past.

**The new evidence is that the counter-example was an artifact.** Vietnamese's 30% was measured under
the shipped tokenizer. Under real-word tokenization (`--unicode-words`, production 2026-08-27):

```
vi   covered 32 -> 0        every Vietnamese article left every story
```

and `--pieces` showed the largest Vietnamese cluster — **17 articles across 8 publishers** —
dissolving to **zero pieces**, not to a smaller core. A genuine eight-publisher event retains a core
when the tokenizer gets *more* precise. Dissolving completely is the signature of a cluster held
together by fragment coincidence: Vietnamese words reduce to short ASCII pieces (`Không` → `kh`/`ng`),
unrelated articles share those pieces, and the Jaccard rule cannot tell that from an event.

So Vietnamese's 30% was not participation. **The sole counter-example to the peer hypothesis was
measuring the tokenizer defect.** That does not make the hypothesis true — it returns it to
*untested*, and re-testing it properly is deliverable #1, not an assumption to build on.

> This is the third justification the docstring invites. It should be held to the same standard as
> the two that failed: if the stratified re-test (§3) does not show the relationship, M14 stops here.

---

## 3 · Which languages are constrained — and by *what*, which is three different things

The original test pooled populations with three distinct failure modes. Stratify first.

Production 2026-08-27, 29,152-article window, 1,614 stories, 6,887 covered (23.6%):

| lang | articles | covered | participation | peers¹ | failure mode |
|---|---:|---:|---:|---:|---|
| en | 18,483 | 5,154 | **27.9%** | 225 | — (the reference) |
| ? | 7,545 | 1,526 | 20.2% | — | **unlabelled** (§3.1) |
| de | 390 | 74 | 19.0% | 11 | healthy, thin |
| es | 339 | 19 | 5.6% | 7 | healthy, thin |
| ru | 318 | 0 | **0.0%** | 5 | **tokenizer-dead** |
| tr | 245 | 22 | 9.0% | 6 | fragment-coincidence |
| ar | 182 | 0 | **0.0%** | 6 | **tokenizer-dead** |
| pt | 161 | 11 | 6.8% | — | healthy, thin |
| ja | 148 | 1 | 0.7% | 3 | **tokenizer-dead** |
| ko | 139 | 0 | **0.0%** | 4 | **tokenizer-dead** |
| fr | 134 | 17 | 12.7% | — | healthy, thin |
| vi | 121 | 32 | 26.4% | 5 | **fragment-coincidence** (artifact) |

¹ outlets above the 10-article floor, from the same run's cohort table.

**English is 63.4% of the window. Everything that is neither English nor unlabelled is 7.5% of it,
and participates at 8.1% — 7.0% once Vietnamese's artifact is removed.**

### Group A — tokenizer-dead: `ru ar ko zh ja ta hi th`

Zero tokens under `[a-z0-9]+`, and `pair_admits` rejects on token count before any other test.
**Their density cannot be measured at all until the tokenizer is fixed**, and no admission campaign
can help them: adding ten Korean publishers to a corpus that cannot tokenize Korean produces ten
publishers' worth of nothing. `--unicode-fallback` (built, unmeasured) is a **hard prerequisite**.

### Group B — fragment-coincidence: `vi tr`, partly `es pt hu`

Accented Latin. Tokenizes to something, but the something is fragments, so participation is inflated
by false merges and the number cannot be trusted in either direction. These need the tokenizer fix
too — not to rescue them, but so their density can be *measured honestly*.

### Group C — healthy and genuinely thin: `de fr es pt id`

Latin script, low diacritic load, tokenizer works. These are the only languages where today's
participation number means what it says, and they are the correct population for the re-test:

```
en  225 peers -> 27.9%
de   11 peers -> 19.0%
es    7 peers ->  5.6%
id    5 peers ->  1.3%
```

Monotone across a 45× range in peer count. **That is the hypothesis, stated on the stratum where it
is testable** — and four points are not a finding, which is exactly why §9 Stage 0 is a measurement
and not a campaign.

### 3.1 · The unlabelled quarter is a prerequisite, and it is cheap to settle

`?` is **7,545 articles — 25.9% of the window** — with no `language`. Any language-targeted strategy
is blind to a quarter of the corpus, and M10 already found this gap in a different guise.

Its 20.2% participation is close to English's 27.9% and nothing else's, which is a testable
hypothesis: **the unlabelled bucket is mostly English with missing metadata.** Settle it by
script-detecting the titles (no network, no new data) before spending anything on targeting. If it
is mostly English the blindness is harmless; if it hides a large non-English population, the whole
priority order in §9 changes.

---

## 4 · The ranking metric: marginal cross-publisher co-coverage

`source_discovery.candidates` ranks by article volume, and its reason is sound *for its own question*
— volume is the evidence that a network request is justified. It is the wrong order for M14, and the
pool shows why: `sportskeeda.com` is the largest candidate at 5,089 articles and covers Indian sports
that nothing else in the corpus covers, so its contribution to *co-coverage* is plausibly ~0.

### The objective

For a language `L` with an admitted publisher set `S`, define

```
cover(S) = |{ (a, b) : a, b ∈ L-corpus,  publisher(a) ≠ publisher(b),
              publisher(a), publisher(b) ∈ S,
              clustering.pair_admits(tokens(a), tokens(b), t(a), t(b)) }|
```

— the number of **cross-publisher admissible pairs**, which is precisely the raw material
`build_stories` turns into stories. The value of candidate host `H` is the *marginal* gain:

```
Δ(H | S) = cover(S ∪ {H}) − cover(S)
```

Three properties make this the right metric rather than a cleverer-sounding one:

* **It is submodular** — a publisher's value falls as its peers are admitted, which is the correct
  shape: the second local paper covering a city council is worth more than the twentieth. Greedy
  maximisation is the standard, defensible selection rule, and it needs no invented threshold.
* **It is zero for a syndicator by construction.** A republisher's articles pair with the original,
  but `SYNDICATION_CEILING` already flags those; more usefully, its pairs are with articles the
  corpus *already holds*, so admitting it adds no event we did not have.
* **It uses `clustering.pair_admits`** — the clusterer's own rule, extracted "precisely so there is
  one definition". No second implementation of what "same event" means.

### It is computable today, offline, for the whole M11 pool

Every one of the 1,173 candidates is a host **we already ingest** — that is what crawl-exhaust
discovery means. So `Δ(H)` needs no network request and no admission: the articles are in the
catalogue. This is the same de-risking order `audit_source_cohort` and `audit_shadow_cohort` both
used deliberately — exercise the machinery on data we already hold before pointing it at a stranger.

---

## 5 · Why the existing evaluation harness cannot measure this

**This is the most important technical finding in the design, and it is the recurring defect shape.**

`source_evaluation.assignment_rate` — M8's metric, the one M9 acts on — asks *"would this outlet's
articles attach to an existing story?"* via `assignment_index(stories)`. For a language with no
stories, there is no index to attach to:

```
ko   139 articles   0 covered   ->  the Korean story index is EMPTY
                                ->  every Korean candidate scores 0% attachment
                                ->  forever, regardless of how good it is
```

A metric that reports 0 for every member of a population, whatever its members do, is **a gate that
cannot fire reading as a gate that failed**. M8 would rank a perfect Korean cohort identically to a
worthless one, and M9 would decline them all with a straight face.

**The fix is structural, not a threshold change.** Attachment-to-incumbent is the wrong question for
a language with no incumbent; the right one is **mutual co-coverage within the candidate set**, which
is `cover(S)` above. Concretely, M14 needs a *pairwise* evaluation mode:

| existing | M14 |
|---|---|
| does H attach to stories that exist? | do H and its peers create stories that do not exist yet? |
| `assignment_index(stories)` | cross-product over the candidate set |
| 0 by construction in an empty language | non-zero exactly when the cohort would cluster |

This is a genuine addition to `source_evaluation`, and it is small — the same `pair_admits`, a
different index. It must carry the same self-assignment guard `audit_shadow_cohort` already asserts:
*"no cohort member may appear in the story set it is scored against"*, or a cohort scores ~100%
against itself.

---

## 6 · How many publishers per language

Refusing to answer with a fitted constant. Four points across `en/de/es/id` would give one, and a
threshold fitted from four points is the kind of number this repository has killed twice.

**Express the target as the measurable quantity and let the publisher count fall out:**

```
target:  co-coverage rate of language L  ≥  the rate German achieves today (≈19%)
```

German is the right anchor because it is the *thinnest* language where participation is
unambiguously real: Latin script, low diacritic load, tokenizer-healthy, 11 above-floor peers. It is
an existence proof that a non-English language can reach two-thirds of English's participation, and
it is reachable rather than aspirational.

The observed peer counts bracket the transition — `id` 5 → 1.3%, `es` 7 → 5.6%, `de` 11 → 19.0% —
so **the working estimate is 10–12 above-floor publishers per language** (above-floor = ≥ 10 articles
per 6-day window ≈ 1.7/day). Stated as an estimate to be checked at Stage 0, not a target to be
hit. What Stage 0 measures is whether adding publishers to `de` moves its co-coverage rate *at all*
in the predicted direction; the constant is whatever the curve turns out to be.

Sizing the gap for Group C, against a 10-publisher target:

| lang | peers now | to add |
|---|---:|---:|
| de | 11 | 0 — already there, use it as the control |
| es | 7 | 3 |
| pt | ? | measure |
| fr | ? | measure |
| id | 5 | 5 |

Group A/B languages are not sized here: their current numbers are not measurements.

---

## 7 · Measuring benefit without damaging existing stories

The machinery exists and needs one addition. The safety argument has three layers.

### Layer 1 — the shadow lane is the measurement chamber

M11 admission assigns `tier = 'shadow'`, and shadow rows *never reach the builder*. So a candidate
can be crawled, ingested and measured for 14 days with **zero product effect**. This is what M5 built
the lane for and it is exactly the risk-free chamber M14 needs. Nothing about M14 requires touching
Tier A before the evidence is in.

### Layer 2 — the additive counterfactual

Existing counterfactuals measure *removal* (`audit_source_cohort --hosts`, the A→shadow direction).
M14 needs *addition*: build with the cohort's rows included versus excluded. `counterfactual()`
already takes a predicate, so this is the same machinery read in the other direction.

**The risk bar is different for an addition, and this matters.** Removal risks *stranding*
(articles lose their story). Addition risks **spurious merging** — a new source's articles bridging
two distinct events, which is the "bridge weld" `DEFAULT_MIN_SUPPORT` exists to catch. So the bars:

| bar | source | why |
|---|---|---|
| `clusters merged` with no exhibit regression | `audit_clustering_change` | a merge that moves a ratified exhibit is a false join |
| `largest cluster` bounded | `MERGE_MAX_LARGEST` | the runaway that started all of this |
| `OTHER articles that LOST their story` ≈ 0 | `audit_source_cohort` | an *addition* stranding anything is a bug, not a trade |
| `independent signal` not worse | `_coherence_stats` | a change that rearranges without correcting |
| `blindspot claims` accounted for | `beforeClaims/afterClaims` | the product's load-bearing claim |
| `capBound` false | `corpus.select` | §8 — new rows consume the cap |

### Layer 3 — the reach table, pointed at languages

`audit_clustering_change`'s reach table (built this session) already splits benefit from cost. For
M14 the split should be **by language** rather than by tokenizer-reachability — the secondary table
already there becomes the primary one. The `--unicode-words` rejection is the template for reading
it: `78 rescued against 149 lost` was the number that overrode an `ADOPT` verdict, and the same
arithmetic decides each M14 tranche.

---

## 8 · The two walls nobody has costed

### 8.1 · Tier A is bounded at ≈ 2× today

`RWE_STORIES_MAX_SCAN` = **60,000** rows, and `story_service.max_scan_default` records that it sits
*below* `corpus.tier_a_budget()` (83,000), so **the row cap is the binding constraint**. Today's
window is 29,152.

```
headroom = 60,000 − 29,152 = 30,848 in-window articles
observed = 6.3 in-window articles per candidate host   (7,343 / 1,173, M11 cohort)
         ⇒ ≈ 4,900 additional long-tail sources fit in Tier A
```

**So Tier A can absorb roughly 5,000 more long-tail sources, and then it is full.** Beyond that,
growth is Tier B — which is exactly what `corpus.py` says makes 50,000 possible, and equally what
means those sources will never form stories. The 50k corpus is ~5,000 Tier A and ~45,000 Tier B.

This is why M14 is an **allocation** problem. Those ~5,000 slots are the entire international-story
budget, and today's selection rule (volume) spends them on English.

### 8.2 · Tier A promotion needs a lean, and for these languages nobody has one

`source_lifecycle` requires `NEEDS_LEAN` to enter Tier A: *"an unattributed rating is
indistinguishable from a guess"*. The roadmap calls Tier A promotion "gated, manual, and permanently
narrow — bounded by rating throughput, which is a budget and not an algorithm."

For Korean, Arabic, Japanese there is no rating capacity at all. Which forces a product question this
design cannot answer alone:

> An unrated outlet does not vote (`story_service._votes`), so a story built entirely from unrated
> Korean outlets has an all-zero lean distribution and `blindspotSide = None`. **Is a story with
> coverage and no bias comparison the product?**

Two honest answers, and the choice is the user's:

* **No** → M14 is blocked behind per-language rating capacity, and the staged plan below is a plan
  for building that capacity, not for admitting sources.
* **Yes, for a defined period** → non-English sources enter Tier A unrated, producing coverage-only
  stories, with the lean distribution explicitly absent rather than fabricated. This is defensible —
  `_votes` already withholds their vote, so nothing is being faked — but it is a change in what a
  story *is*, and it belongs to a decision, not to an inference.

**Note the asymmetry that makes the second option safer than it sounds**: the ~1,173 candidates are
already Tier A today, by `DEFAULT_TIER` grandfathering. Unrated outlets are *already* forming
English stories. Extending that to other languages is not a new class of risk; it is the existing
policy applied evenly.

---

## 9 · The staged plan

Each stage has an entry condition, a falsifiable bar, and an exit that is a decision rather than a
default. **Stage 0 is the whole design's hinge — if it fails, the rest does not run.**

### Stage 0 — falsify the hypothesis (no admission at all)  ✅ **BUILT**

Offline, on the catalogue we already hold. `examples/source_density.py` (pure) +
`examples/audit_language_density.py` (read-only runner):

```
dc run --rm -T api python examples/audit_language_density.py --db "$RWE_DB_URL"
```

1. **Settle the unlabelled quarter** (§3.1) by script — and the strata are **derived from the
   headlines**, not from a language list, so the classification does not bake today's corpus into
   the code.
2. **Re-test the peer hypothesis on the healthy stratum only**, with the tokenizer-dead and
   fragment languages excluded because their numbers are not measurements. `?` is excluded too: it
   is a metadata gap, not a language.
3. **Rank publishers by `Δ`** — marginal cross-publisher coverage, greedily — and compare against
   the volume ranking.

**Bar:** within the healthy stratum, **mean partners** rises with publisher count *and varies*
across it; and the `Δ` ranking differs substantially from the volume ranking. *If `Δ` and volume rank the pool the
same way, the whole premise of M14 is wrong and volume-ordered admission was right all along.*

The runner prints both bars with its own RESULT line, so the verdict is stated rather than inferred
— and it refuses to test the hypothesis on fewer than three healthy languages, because a
relationship read off two points is the four-point curve fit §6 declined to do.

> **Three findings already, from building it.** A cross-publisher pair needs **two** publishers, so
> from a cold start every singleton scores zero and a pure greedy returns an empty ranking on a
> corpus with 121 admissible pairs — the selection now bootstraps on the best *pair* and credits the
> first step 0, because admitting only it buys nothing. And Latin must **not** win a mixed headline:
> a real production line, `Champions League: Πέρασαν στη league phase Φενέρμπαχτσε…`, is 11 Latin
> characters to 10 Greek, so a plurality vote files a Greek outlet under Latin and the hidden
> non-Latin corpus is under-counted — the one direction that would license targeting a corpus we
> cannot see.
>
> The third is a defect in the instrument itself, found by rehearsing the runner against a corpus
> with three healthy languages at 8, 4 and 2 publishers. **All three reported 100% co-coverage**,
> and the monotonicity test — `all(cov[i] >= cov[i+1])` — scored that flat column as *"the
> hypothesis survives"*. Co-coverage asks only whether an article has **at least one**
> cross-publisher partner, so it saturates the moment two publishers overlap, and a flat line passes
> a `>=` test: a gate that cannot fail, in the instrument built to test a hypothesis. The profile now
> also reports **mean partners** — distinct other publishers holding a partner, averaged over
> articles, which two publishers cap at 1.0 however complete their overlap — and the verdict reads
> that column and requires it to *vary*. On the same rehearsal it reads 7.00 / 3.00 / 1.00.

### Stage 1 — 100 sources, ONE language, as an experiment

Entry: Stage 0 passed. Target language: **German** — Group C, tokenizer-healthy, already at 19%, so
the control and the treatment are both interpretable.

Take `de` from 11 to ~25 above-floor publishers, selected by greedy `Δ`, admitted through M11 into
**shadow**, measured for 14 days by the pairwise metric (§5), then promoted as one tranche behind an
additive counterfactual (§7).

**Bar:** German co-coverage rate rises materially — and `en` participation, `largest cluster`, and
the ratified exhibits are unmoved. A German gain paid for in English losses is not a gain.

**This is the falsification stage.** ~25 publishers in one language is small enough to be reversible
(withdrawal keeps the shadow assignment — `store.withdraw_source`) and large enough that a real
effect would show.

### Stage 2 — 1,000 sources, Group C only

Entry: Stage 1 showed the effect. Extend to `fr es pt it nl` on the same rule. Still no Group A/B —
their numbers are not yet measurements.

Binds here: **M12** (polling interval vs N — 1,000 sources at 900 s is 444% of the ~9,000 polls/hour
lock budget) and `RWE_POLL_WORKERS` off 0. Both designed, neither built.

**Bar:** each language's co-coverage rate reaches the German anchor; Tier A row count stays under the
60,000 cap with `capBound` false.

### Stage 3 — 10,000 sources, Group A unlocked

Entry: **`--unicode-fallback` measured and adopted.** Without it, every Group A source admitted is a
source that cannot cluster, and the stage is spending Tier A slots on nothing.

Also required here: **per-language Tier A budgets**. §8.1's ~5,000 slots must be allocated
deliberately, or English volume takes them by default. This is a new mechanism — `corpus.tier_index`
would need a per-language cap — and it is the first point where M14 requires serving-path code.

**Bar:** no language's admission displaces another's below its Stage-2 rate.

### Stage 4 — 50,000 sources, mostly Tier B

Entry: the §8.2 product decision, explicitly taken.

At this scale the corpus is ~5,000 Tier A and ~45,000 Tier B by construction. Tier B sources are
searchable and attributable and never cluster, which is the design's own answer to how 50,000 is
possible at all. **The international-story goal is achieved or not achieved in Stages 1–3**; Stage 4
is breadth of *search*, not of stories, and saying so plainly is what stops the number from implying
something it does not deliver.

---

## 10 · What to build, in order

Nothing in Stage 0 touches the serving path.

| | what | where | serving risk |
|---|---|---|---|
| 1 | script-detect the unlabelled bucket | new offline audit | none |
| 2 | stratified peer re-test | extend `audit_source_cohort`'s language table | none |
| 3 | `Δ(H)` marginal co-coverage + greedy selection | new: `source_density.py` (pure, like `source_discovery`) | none |
| 4 | pairwise cohort evaluation (§5) | extend `source_evaluation` | none |
| 5 | additive counterfactual mode | extend `audit_source_cohort` | none |
| 6 | language-ranked admission | flag on `source_campaign.py` | writes admission rows only |
| 7 | per-language Tier A budgets | `corpus` — **first serving-path change**, Stage 3 | real |

Items 1–3 are the ones that decide whether M14 exists. They are offline, they use data already in
the catalogue, and they can be done before the ToS review that still gates any probing campaign.

---

## 11 · What this design does not claim

* **Not that the peer hypothesis is true.** Its recorded refutation has been shown to rest on an
  artifact, which re-opens it. Stage 0 tests it; Stage 0 can fail.
* **Not that 10–12 publishers is the right number.** It is a bracket from four points, offered as a
  starting estimate for a measurement.
* **Not that Vietnamese's clusters were definitely false.** Dissolving to zero pieces under a more
  precise tokenizer is strong evidence, not proof; reading the 17 headlines would settle it and
  nobody has.
* **Not that international stories are worth their cost.** §8.2 is a product decision with a real
  argument on both sides, and it belongs to the user.
* **Not that the 50,000 figure delivers international stories.** By §8.1's arithmetic it delivers
  ~5,000 clustering sources and ~45,000 searchable ones, and the honest version of the goal says so.
