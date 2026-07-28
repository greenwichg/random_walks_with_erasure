# Cluster trust — the launch gates over story clustering (2026-07-28)

Written as the technical review for a first public release. **Verdict: the clustering algorithm is
launch-ready; the way its output was ranked and consumed was not.** This records what shipped, why,
and the thresholds that decide what happens next.

## The finding

Correctness on the body of the catalog is good, and the audits say so:

| Measurement | Value |
|---|---|
| Story yield vs corpus growth | 766 → 925 stories (+21%) on 13,305 → 16,422 rows (+23%) — linear |
| Story size distribution | p50 = 2, p90 = 7 |
| Independently-scored clusters passing | **86 of 91 at geoCoherence ≥ 0.7 (94.5%)** |
| Known-bad clusters | 5 |
| Their share of covered articles | 271 of 3,602 (**7.5%**) |

The p50 matters more than it looks. Chaining needs `A~B`, `B~C` and `A≁C` — three members. A
two-article cluster is a single pairwise decision and is **structurally immune**, so the catalog's
median story cannot exhibit the defect at all.

What is *not* good is where the defect lands:

* `story_service.build_stories` ranked on `(publisherCount, totalCoverage, latest)`, and
  `/stories` defaults to that sort.
* **Single-linkage chaining accumulates publishers.** A cluster's wrongness and its rank therefore
  have the same cause.
* Measured: `Trump defends 50% tariffs on Canada` — 208 articles, 106 publishers, geoCoherence
  0.62, members located across CN, CU, DJ, GB, IL, IR, OM, PH, SA, SG, US, YE. With p90 = 7, it
  outranked every correctly-clustered story in the catalog.

So the defect rate is 5.5%, but the defect rate *at position 1* is far higher. Separately,
`_blindspot` is computed over the merged member set, which turns a grouping error into a **false
claim about publisher behaviour** — the product's most load-bearing assertion, surfaced on the
detail page, story cards, the home hero, the daily briefing and a dedicated module.

Two other defect classes were considered and are **not** blockers:

* **Non-news** (betting promos, TV air-date posts, press-release wires) — 5 clusters, 205 articles,
  5.7%. Correctly clustered, coherence 1.00. These have *low* publisher counts (4, 6, 3), so the
  existing sort already buries them. Belongs in `outlet_registry.csv`, not in clustering.
* **Unmeasured clusters** — 834 of 925 stories carry no coherence score. The rate among them is
  unknown and is not extrapolated here; see the caveat at the end.

## What shipped

### 1. A trust verdict — `story_service._cluster_trust`

One verdict per story, derived from `geoCoherence`, the only signal we have that is **independent**
of the clusterer (it comes from provider-extracted locations and knows nothing about titles, tokens
or publishers, so it can contradict the grouping).

| verdict | rule |
|---|---|
| `ok` | fewer than `MIN_CHAINABLE` (3) members — structurally cannot be a chain — or an actionable score ≥ floor |
| `low` | actionable score below `DEFAULT_COHERENCE_FLOOR` (0.7): the located members disagree |
| `unverified` | no actionable score, above `DEFAULT_UNVERIFIED_SIZE` (50) members |

**Actionable** means backed by at least `MIN_LOCATED_FOR_TRUST` (4) located members. The first
production run shipped without this precondition and it was the gate's one real defect: at two
located members a single dissenter scores 0.50, and in a small cluster the commonest cause of one
dissenter is a genuinely two-country story. It withheld blindspots from *"LIVE: F1 Hungarian Grand
Prix — Lando Norris"* (Hungarian race, British driver), *"Zelenskyy accuses Russia of assisting
Iran"*, and *"Mamdani reiterates ICC support after urging US to…"* — all correctly clustered, all
legitimately about more than one country. `audit_publisher_concentration.py` already required a
located-member minimum for exactly this reason; the precondition lived in the audit and not in the
gate. At four members the 0.7 floor means "three of four agree", which is a real minority rather
than a coin flip.

`low` and `unverified` are treated differently on purpose: **evidence of a problem reorders the
feed; absence of evidence only withholds claims.** Too few located members to judge is absence of
evidence, so it lands in `unverified`, never in `low`.

### 2. The blindspot gate (called the hard blocker — see the measurement below)

`blindspotSide` is emitted only when trust is `ok`. `blindspotWithheld` records what was suppressed
so the audit can count it rather than guess. A blindspot is a statement about the world; we do not
make one from a cluster our own instrument contradicts.

**On the live catalog this gate currently withholds nothing, and that is a finding rather than a
success.** The two populations barely overlap. A blindspot arises in a SMALL cluster — few
publishers, so a side goes uncovered — while an actionable coherence score needs FOUR located
members, which small clusters rarely have. Every cluster the signal distrusts (327, 34 and 19
articles) is large enough to carry left, centre and right coverage, so none of them made a
blindspot claim to withhold in the first place.

So the honest position: the gate is a correctness guarantee that costs nothing and will fire the
day a distrusted cluster does carry a blindspot, but it is **not** evidence that false blindspot
claims have been prevented. If chained small clusters are producing false blindspots, `geoCoherence`
structurally cannot see them, and we have no other instrument that can. That risk is open and
unaddressed — recorded here rather than left implied by a gate that reads as covering it.

### 3. The ranking gate

`_size_rank` sorts `low`-trust clusters last, on both `top` and `publishers` (same "biggest"
semantic — otherwise the publishers sort is a one-click route back to the same card). `latest` and
`oldest` are untouched: a reader asking for newest wants newest. Kill switch:
`RWE_STORY_TRUST_RANKING=0`.

**The trade this accepts, stated plainly:** the 208-article cluster does contain a genuine,
well-covered tariffs story alongside the contamination, and demoting it makes that coverage harder
to reach from this surface. It remains on Discover, in search, and on the publisher pages. Splitting
the cluster is the real fix; this is containment until that measures out.

### 4. Cluster-aware linkage — `clustering.link_quorum`, **shipped disabled**

The actual fix for chaining. A merge additionally requires that fraction of cross-pairs between the
two clusters to pass the same pairwise gate. Two singletons always have exactly one cross-pair — the
one that already passed — so the rule constrains **growth**, never formation, and the median story
never meets it.

Property change worth knowing: single linkage is order-independent (transitive closure is unique);
a quorum rule is not. Merges are consumed **best-first** (highest similarity, ties by index), so the
result is deterministic but greedy, not a global optimum. `link_quorum = 0.0` takes a separate code
path that is verified against a naive transitive closure in `tests/test_clustering.py`, so the
default cannot drift.

It is off because the last change that tightened matching on equally sound reasoning
(`use_idf`) cost **10.5% of covered articles** and was reverted.

**Measured 2026-07-28 against 16,857 live articles — REJECTED as a global rule:**

| quorum | stories | largest | dropped |
|---:|---:|---:|---:|
| 0.3 | 964 → 1,081 | 486 → **45** | 599 |
| 0.5 | 964 → 1,122 | 486 → **45** | 677 |

The mechanism works: the mega-cluster split into 61 pieces at 0.3, the largest cluster fell by an
order of magnitude, story count *rose* (so the `min_publishers` cliff did not fire), and mean
coherence improved 0.967 → 0.974. But it fragments stories nothing is wrong with — **Berlin pride,
77 articles from 54 publishers at coherence 0.94, split into six pieces**, and a dozen other
well-covered stories shed real coverage.

Two caveats on those percentages. First, both runs used the tool's old default BEFORE side
(`shared>=1, tokens>=1`), which is not production — so the headline 13.7%/15.4% includes the
already-paid cost of the admission gates and overstates the quorum's own cost. The default is
fixed; a re-run will be lower. Second, the rejection does not depend on that: the fragmentation of
Berlin pride is visible in the split table regardless of what the denominator says.

### 4b. Targeted repair — `story_service.repair_quorum`, **shipped disabled**

The variant the measurement points to. Apply the quorum rule **only to clusters `_cluster_trust`
has already condemned**, and leave every other cluster byte-identical.

Size cannot separate a good big cluster from a bad one — Berlin pride (77) and the mega-cluster
(327) are both large. The independent signal can: 0.94 against 0.61. So the stricter rule goes
where that signal already objects, which on the current catalog is **3 clusters holding 380
articles (9.1% of covered)**. That bounds the worst case: nothing outside those three can move.

Two guards against silent destruction, because dissolving a cluster improves every aggregate the
audit prints — a repair is discarded and the original kept whole if it yields only one piece
(nothing was separated) or retains under `REPAIR_MIN_RETENTION` (50%) of the articles.

**Measured 2026-07-28, production baseline, 3 clusters touched in both runs:**

| | stories | largest | dropped | actionable bad | mean coherence |
|---|---:|---:|---:|---:|---:|
| production | 940 | 336 | — | 3 of 63 | 0.966 |
| global quorum 0.3 | 1,086 | 45 | **9.3%** | 3 of 64 | **0.952** |
| repair 0.3 | 991 | 103 | 1.3% | 3 of 66 | 0.960 |
| **repair 0.5** | **1,000** | **115** | **1.6%** | **1 of 66** | **0.968** |

The global rule is rejected on three counts, not one: it costs 9.3% of covered articles, it does
not reduce the bad-cluster count, and the independent signal gets **worse** (0.966 → 0.952). It
shreds coherent stories — "Wildfires ravage parts of southern France, Italy and Spain", 100
articles from 61 publishers, went to eleven pieces.

Repair at 0.5 is the only variant where the independent signal improves. Bad clusters fall 3 → 1,
mean coherence rises, and the cost is 1.6%. Note what the largest cluster becomes: **115 articles
is the M.D. Sass press-release template**, not a false merge — after repair, the biggest thing in
the catalog is a non-news problem that belongs in `outlet_registry.csv`.

Caveat that no amount of arithmetic removes: **n = 3**. Three bad clusters going to one is a
two-thirds reduction and also a sample of three. What makes it safe to adopt anyway is not the
ratio but the blast radius — only clusters the signal already condemns are touched, so the
downside is bounded to those three whatever the true rate is.

```
docker exec deploy-api-1 python /app/examples/audit_clustering_change.py --repair-quorum 0.5 --show 20
docker exec deploy-api-1 python /app/examples/audit_clustering_change.py --repair-quorum 0.5 --pieces 1
```

The second command is the one that decided it.

### What the 336-article cluster actually contained

56 pieces holding 271 of 336 articles, only 9 of them at 2 articles or fewer. The largest:

```
   16    14  Oil Prices Fall After U.S. and Iran Pause Fighting for a Second Day
   13     9  At Least 2 Killed in Shooting at Food Festival in Seattle
   11    11  U.S.-Iran War Pauses for 2nd Straight Day
    9     6  Two dead, five injured in shooting near Seattle's Space Needle
    8     7  Trump's 50% Tariffs on Canada: What to Know, and What's Next
    7     6  Houthis Claim Strikes on 2 Saudi Oil Tankers in Red Sea
    7     7  US to Impose Forced Labor Tariffs on Many Trading Partners
    6     6  House Passes Defense Bill Amid Iran War Divide
    5     4  Netanyahu and Zelensky coming to DC for Lindsey Graham funeral
```

One cluster was holding a US-Iran war, **a mass shooting in Seattle**, Canada tariffs,
forced-labour tariffs, Houthi attacks in the Red Sea and a senator's funeral — under a headline
about a dignified transfer of fallen soldiers. This is the chaining defect read directly rather
than inferred from a coherence score. Note that *"Trump's 50% Tariffs on Canada"* — the title this
same blob carried a week earlier — comes back out as its own 8-article story.

### The over-fragmentation objection, and why it does not hold

The obvious complaint is that the Seattle shooting appears as four pieces (13, 9, 7, 6 articles)
and the Iran pause as several. That reads like the quorum shredding one event.

It is not. Those pieces cannot merge under **any** linkage rule, because they never clear the
pairwise gate in the first place:

| pair | shared tokens | jaccard |
|---|---:|---:|
| "…Shooting at Food Festival in Seattle" vs "…shooting near Seattle's Space Needle" | 2 | 0.15 |
| "Mass shooting reported at Seattle Center" vs "…gunfire erupts near Seattle" | 1 | 0.08 |
| "U.S.-Iran War Pauses…" vs "After Trump calls off bombing, Iran signals…" | 1 | 0.07 |

Against `MIN_SHARED_TOKENS = 3` and `sim = 0.28`. They were in one cluster **only** via chaining.
So the quorum did not fragment a coherent story — it exposed a pre-existing recall limitation of
title-token Jaccard that the blob had been hiding. Near-duplicate stories in the feed are the new
visible problem, and they are a *different* problem: headlines describing one event in disjoint
vocabulary. That is the case entity or event signals would address, and it is now measurable
instead of buried inside a 336-article cluster.

**Adopted at 0.5.** Enable with `RWE_STORY_REPAIR_QUORUM=0.5` — reversible without a deploy, which
is why it goes in the environment before it goes in the code default.

### 5. Coverage-list batching

`CoverageList` renders 40 rows then "Load more". The largest cluster mounted 318 rows, each with a
Read and a Save button.

## Thresholds

### Measured, 2026-07-28 (938 stories, 4,169 covered articles)

| verdict | stories | articles | share |
|---|---:|---:|---:|
| `ok` | 933 | 3,626 | 87.0% |
| `low` | 3 | 380 | 9.1% |
| `unverified` | 2 | 163 | 3.9% |

The three demoted clusters, with located-member counts that make the scores readable:

| pubs | arts | loc | coh | title |
|---:|---:|---:|---:|---|
| 129 | 327 | 72 | 0.61 | Trump to attend dignified transfer of fallen soldier |
| 33 | 34 | 26 | 0.65 | Mount Olympus … World Heritage |
| 18 | 19 | 5 | 0.60 | Congo Ebola outbreak kills nearly 1,000 |

Position 1 of the default sort is now *"Wildfires ravage parts of southern France, Italy…"* — 61
publishers, 49 located members, coherence 0.78. A real, coherent, well-covered story.

Two clusters sit in `unverified`: the U.S.-Saudi nuclear-deal cluster (60 articles, 28 publishers,
**only 2 located members** at 0.50 — genuinely undecidable, and worth a manual read) and the
103-article press-release template, which has no geography at all.

### Before trusting the gates — run this first

```
docker exec deploy-api-1 python /app/examples/audit_cluster_trust.py --top 20
```

The gates key off a signal only ~11% of stories carry. The defence is that coverage is not uniform:
scoring needs three located members, so it is densest on exactly the large clusters the gates apply
to. **The top-20 table tests that defence.** At ≥ 80% scored the gates are load-bearing. Below that
they are mostly missing their targets and the thresholds need a size-based fallback instead of a
coherence-based one (`RWE_STORY_UNVERIFIED_SIZE` becomes the primary rule).

### Before enabling link_quorum

```
docker exec deploy-api-1 python /app/examples/audit_clustering_change.py --link-quorum 0.3 --show 20
```

Bars fixed in advance and computed by the tool, not eyeballed:

| | bar |
|---|---|
| adopt | droppedOut ≤ **5%** of covered articles, story count does not fall, largest cluster well down, `bad/scored` coherence improves |
| reject | droppedOut > 5%, **or** total story count falls |

The story-count rule is the `min_publishers` cliff: splitting a 4-article/2-publisher cluster into
2+2 can leave two single-publisher fragments, and **both** are then dropped. Oversplitting deletes
stories rather than merely shrinking them, and the article counter alone does not show it.

### Post-launch monitors — agreed triggers

Both are ratios, so they stay comparable as the corpus grows, which raw counts do not. Reported by
`audit_cluster_trust.py` and by `story_service.diagnostics`.

| monitor | measured 2026-07-28 | trigger |
|---|---|---|
| largest cluster ÷ p90 story size | 325 ÷ 7 = **46.4×** | **60×** |
| largest cluster share of covered articles | **7.8%** (325 of 4,168) | **8%** |

Hitting either promotes the linkage work above whatever else is queued, with no re-litigation. The
reason this needs a pre-committed trigger rather than a judgement call later is the growth curve:
the largest cluster went **194 → 208 → 318 → 325** while the corpus grew far more slowly. It is
superlinear, so it degrades without any change from us.

**The share monitor is 0.2 points from its trigger.** An earlier revision of this table recorded it
as "~2%", which was wrong: that divided the largest cluster by the whole corpus rather than by
articles in stories, which is what the monitor measures. Corrected here — the linkage work is not a
future iteration, it is next.

The 2026-07-28 run also changed which cluster is largest. It is no longer *"Trump defends 50%
tariffs on Canada"* but *"Trump to attend dignified transfer of fallen soldier"* — 325 articles,
129 publishers, coherence 0.61. Same blob, different title, because the title comes from the
earliest-published member and the chain keeps absorbing earlier bridging articles. Story IDs are
anchored to that same member, so **the mega-cluster's id churns as it grows** and links to it go
stale. That is a second, previously unrecorded cost of the defect.

## Configuration

| variable | default | effect |
|---|---|---|
| `RWE_STORY_COHERENCE_FLOOR` | `0.7` | geoCoherence below which a cluster is `low` (needs 4+ located members) |
| `RWE_STORY_UNVERIFIED_SIZE` | `50` | size above which having no score is notable |
| `RWE_STORY_TRUST_RANKING` | on | `0` restores pure size ordering |
| `RWE_CLUSTER_LINK_QUORUM` | `0.0` | GLOBAL cross-pair fraction required to merge (`0` = single linkage) — measured and rejected |
| `RWE_STORY_REPAIR_QUORUM` | `0.0` | TARGETED: same rule, condemned clusters only |

## Caveats, stated rather than buried

* Only **91 of 925 stories (11%)** carry a coherence score. Every precision claim here rests on
  that minority.
* There are **5** known-bad clusters. Rates estimated from 5 positives have wide error bars.
* The 5.5% bad rate is **not** extrapolated to the 834 unscored stories. Scored stories are
  selected for having ≥3 located members, which selects for size, which is where chaining lives —
  the scored subset is enriched for the failure being measured. The unscored population is
  smaller-clustered and probably cleaner, but that is judgement, not measurement.
* `geoCoherence` detects one *kind* of bad cluster. Others may exist that it cannot see.

## Related

* `docs/PUBLISHER_CONCENTRATION_EVALUATION.md` — the heuristic that was proposed for this problem,
  measured, and rejected at 0% precision and 0% recall.
* `docs/STORY_PIPELINE_AUDIT.md` — the scan-window defect that collapsed the story count.
