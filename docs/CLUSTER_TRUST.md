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

`examples/audit_story_duplicates.py` measures it. It cannot reuse headline tokens — that is the
input the clusterer already failed on — so each story gets a profile built from every member's
headline **and description**, with rare-word weighting doing the comparing. On the four Seattle
clusters that profile scores 0.56 where the headlines score 0.15.

Two things it counts that a naive version would get wrong: **events, not pairs** (four clusters of
one event are six pairs but one duplicated event, so a pair count overstates the damage
quadratically), and **article volume**, because a dozen duplicate pairs sounds negligible until
they turn out to hold 300 articles. A time window keeps a recurring topic — a weekly fixture, a
monthly filing — from pairing with itself across the archive.

It produces **candidates, not findings**. Same-event is a judgement about the world and no signal
here can make it, so the pairs are printed with their titles and the count at any threshold is an
upper bound until they have been read.

**Adopted at 0.5.** Enable with `RWE_STORY_REPAIR_QUORUM=0.5` — reversible without a deploy, which
is why it goes in the environment before it goes in the code default.

### 5. Coverage-list batching

`CoverageList` renders 40 rows then "Load more". The largest cluster mounted 318 rows, each with a
Read and a Save button.

## Thresholds

### End state — measured 2026-07-28 with all three changes live

| | start of day | gates only | + repair 0.5 | + wire curation | + claim floor |
|---|---:|---:|---:|---:|---:|
| stories | 938 | 938 | 1,004 | 1,003 | **1,024** |
| covered articles | 4,169 | 4,169 | 4,151 | 4,006 | 4,091 |
| largest cluster | **327** | 327 | 115 | 100 | **100** |
| largest ÷ p90 | 46.7× | 46.7× | 16.4× | 14.3× | **14.3×** |
| largest share of covered | **7.8%** (trigger 8%) | 7.8% | 2.8% | 2.5% | **2.4%** |
| `ok` share of articles | 87.0% | 87.0% | 95.4% | 98.1% | **97.9%** |
| `low` | 3 / 380 articles | 3 / 380 | 1 / 16 | 1 / 16 | **2 / 25** |
| top-20 coherence coverage | — | 60% | 80% | 81% | **81%** |
| blindspot claims | 516 (89% on 1–2 pubs) | 516 | 516 | 516 | **57 (all 3+)** |

**The largest cluster in the catalog is now a real story** — *"Wildfires ravage parts of southern
France, Italy and Spain"*, 100 articles, 61 publishers, coherence 0.76 on 50 located members —
rather than a chained blob or a press release. Independently-condemned coverage fell 380 → 16
articles. The share monitor went from 0.2 points under its trigger to a third of it.

All four launch conditions from the review are met: top-20 coherence coverage is over the 80% bar,
both gates are live, and both monitors sit far below their pre-committed triggers.

Two things this leaves, neither of them a clustering defect:

* **The M.D. Sass press-release template** — 115 articles from 5 publishers, no geography. Fixed
  at the source; see the last section.
* **`Expected U.S.-Saudi Nuclear Deal…`** — 60 articles, 28 publishers, coherence 0.50 on **2**
  located members. Genuinely undecidable by the signal, and unchanged by the repair since
  `unverified` clusters are not touched. Needs a human read.

The one remaining `low` cluster is itself a piece the repair produced: *"Oil Prices Fall After
U.S. and Iran Pause Fighting"*, 16 articles, 7 located, 0.57. Repair does not recurse — a piece
that comes out condemned is not re-split. At 16 articles that is not worth changing, but it is a
known limit rather than an oversight.

### Measured before adoption, 2026-07-28 (938 stories, 4,169 covered articles)

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

## Wire sources — the defect no clustering signal can catch

The largest cluster in the catalog after the repair was not a false merge. It was 115 articles of
one press release, and it was **correctly clustered** — a template repeated 115 times really is
about one template. `geoCoherence` rates such a cluster perfectly coherent, and
articles-per-publisher was measured against the whole catalog and rejected at 0% precision and 0%
recall. No clustering signal can find this, because nothing about the clustering is wrong.

It is an identity fact about the source, so it is curated at the source. `outlet_registry.csv`
gained a `kind` column (column 8); `kind=wire` marks a machine-generated market-data feed, and
`build_stories` keeps those articles out of clustering entirely.

**The evidence, gathered before writing any rows.** Five publishers produced the cluster:

| publisher | in cluster | total in catalog |
|---|---:|---:|
| Lulegacy | 69 | 71 |
| MarketBeat | 38 | 66 |
| American Banking News | 4 | 8 |
| Markets Daily | 3 | 3 |
| Ticker Report | 1 | 1 |

Three of the five do essentially nothing else. All **34** articles the five published *outside*
the cluster were read by hand — "Baker Hughes Announces Quarterly Earnings Results", "Arrowstreet
Capital Limited Partnership Sells 265,500 Shares of Ralliant", "Sanmina Q3 Earnings Call
Highlights". Auto-generated earnings and 13F-filing copy without exception; no reporting. That is
what made a source-level rule safe rather than a guess — had any of the 34 been real journalism,
the filter would have had to be per-article and the outlets would have stayed.

Scope is deliberately narrow:

* **Stories only.** The articles remain in the catalog, on Discover, in search and on their
  publisher pages. They are real articles; it is their newsworthiness in question, not their
  existence. Nothing is deleted and no saved read breaks.
* **The mechanism does nothing on its own.** An outlet is excluded only where a human wrote
  `wire` in its row, so the blast radius is exactly what was curated. An unregistered outlet is
  never wire — absence of a row means unrated, not disqualified, or the whole uncurated long tail
  would silently stop producing stories.
* `RWE_STORY_EXCLUDE_WIRE=0` reverses it without a deploy.

This is what the publisher concentration gate was reaching for and got wrong. Same target,
opposite location: at the source, where it is explicit, auditable, one cell to undo, and
incapable of misfiring on a government-funding story the way a threshold in the clustering path
could.

The registry invariant moved with it. An unrated row previously had to carry a locality to earn
its place; it may now carry a locality **or** a kind. Both are curated facts, and a row with a
blank lean and neither still asserts nothing and is still rejected.

## Blindspot claims need a sample they could have been false in

Chasing the duplication measurement turned up a bigger defect in the feature the trust gate was
built to protect. **516 of 1,022 stories asserted a coverage gap**, and:

| rated publishers behind the claim | claims | share |
|---:|---:|---:|
| 1 | 254 | **49.2%** |
| 2 | 206 | **39.9%** |
| 3 | 35 | 6.8% |
| 4+ | 21 | 4.1% |

**89.1% of the product's coverage-gap claims rested on one or two rated outlets.** With 46% of
stories carrying no rated publisher at all (and so making no claim), that means roughly 94% of the
stories that *could* claim a gap did.

That is arithmetic, not editorial fact. There are three lean buckets, so fewer than three rated
publishers cannot fill them: an empty bucket is guaranteed whatever the outlets actually did. A
one-publisher story announcing "nobody on the left covered this" is reporting the size of its own
sample. And the catalog median story is 2 articles, so the feature was firing almost everywhere it
possibly could.

This is **the same defect `MIN_LOCATED_FOR_TRUST` fixes for geoCoherence** — a ratio acted on with
no sample-size floor — sitting inside the feature that gate exists to protect. It was fixed there
in the morning and walked past here until the duplication denominator forced the number into view.

`MIN_RATED_FOR_BLINDSPOT = 3` is the floor, chosen because three is where covering every bucket
becomes *possible*, so an empty one is finally an observation. Raise `RWE_STORY_MIN_RATED` to 4 for
a claim that carries weight rather than merely being permitted; set it to 1 to restore the old
behaviour without a deploy.

**This is a visible product change.** Confirmed live: claims fell **516 → 57**, and every survivor
now carries three or more rated publishers (36 at three, 21 at four-plus, zero below). The
blindspot module, the `?blindspot=` filter and the coverage-gap facets are all much thinner. That
is the point: the 460 removed claims were not findings. A coverage gap went from something half the
catalog announced to something 1 story in 18 does, which is a credible rate for an editorial
finding.

For scale, the duplication defect this search started from accounts for 15 contradicted claims
(2.9%); this one accounted for 460 (89.1%), thirty times larger — and it was only found because
someone asked for the near-duplicate count and the answer needed a denominator.

## Recall — the duplicate merge (`RWE_STORY_MERGE_SIM`, shipped disabled)

The one defect axis nothing else shipped touches. Measured: **22 duplicated events across 45
stories, 172 articles (4.3% of covered)**, with 16 of 23 candidate pairs confirmed true duplicates
by hand. The mechanism is structural — "Mass shooting reported at Seattle Center" and "…gunfire
erupts near Seattle" share **one** token against `MIN_SHARED_TOKENS = 3` and score 0.08 against
`sim = 0.28`. No linkage rule reaches that. Only richer text does: description-backed profiles score
**0.56** on the Seattle pair.

`_merge_duplicates` runs as a third pass, **after** the repair. The order is deliberate: split then
join, so the two constrain each other. Anything the repair over-separates that is genuinely
near-identical gets rejoined; anything the merge would over-join has already been vetted by
coherence.

### Three guards, because a merge pass is what built the mega-cluster

* **Complete linkage.** A group merges only when *every* pair inside it clears the threshold, not
  just some chain of them. This is the direct fix for a case the audit found: a *"Houthi attacks
  create tinderbox in Red Sea: what to know"* explainer paired with **two separate** Houthi events
  at 0.30 and 0.27. Single linkage would have glued those two events together through it.
* **The independent signal has a veto.** If the merged cluster carries an actionable `geoCoherence`
  below the floor, the merge is refused. This is the only check a text-similarity merge cannot mark
  its own homework on.
* **A size cap** (`DEFAULT_MERGE_MAX_SIZE = 130`), just above the largest legitimate cluster
  measured, so no merge can start a runaway.

### The threshold

`0.33`, because that is where the measured sample stops making mistakes. Of 23 pairs at ≥ 0.25: 16
true duplicates, 5 same-story-different-angle, **2 false positives** — and both false positives sat
at **0.31** (a French wildfire paired with a Californian one; two unrelated Nvidia stories). Every
pair at 0.33 and above was a true duplicate. **n = 14**, so this is a floor picked from a small
sample, not a tuned optimum.

### The bars, fixed in advance

A merge and a split cannot be judged by the same rules, and applying the wrong set would reject a
good merge on principle — a falling story count *is the point* here (45 duplicate stories becoming
22 events), and a merge cannot strand a single-publisher fragment the way an oversplit can. So
`verdict(..., merging=True)` swaps them:

| | reject if |
|---|---|
| coverage | **any** article dropped — a merge adds coverage, so losing one is a bug |
| size | largest cluster over 120 |
| independent signal | bad-cluster count rises, **or** mean actionable coherence falls |

```
docker exec deploy-api-1 python /app/examples/audit_clustering_change.py --merge-sim 0.33 --pieces 5
```

Cost: **1.39s** over a synthetic 1,000-cluster catalog, down from 7.36s — `weighted_jaccard`
re-summed the union on every pair, and precomputing each profile's total weight makes only the
intersection variable (`|A ∪ B| = total_i + total_j − |A ∩ B|`). Identical arithmetic. That is what
makes this affordable in a cached request path rather than only in an audit.

### Measured and adopted, 2026-07-28

| | before | after |
|---|---:|---:|
| stories | 1,042 | **1,028** |
| clusters merged | — | **14** |
| articles dropped | — | **0** |
| largest cluster | 102 | **102** |
| bad clusters | 2 of 67 | 2 of 68 |

All 14 read by hand. Eight largest:

| articles | publishers | joined |
|---:|---:|---|
| 22 | 14 | *At Least 2 Killed in Shooting at Food Festival in Seattle* + *Two dead, five injured near Seattle's Space Needle* |
| 13 | 9 | *Fauci ruled out Wuhan market origin* + *Fauci diary entries: 'Press is going wild with me'* |
| 11 | 7 | *After Trump calls off bombing, Iran signals it will halt strikes* + *Trump paused attacks on Iran to make space for talks* |
| 7 | 7 | *J&J Agrees to Pay Up to $5.5 Billion to Settle Talc* + *J&J offers $5.5bn settlement* |
| 7 | 7 | *ICC Removes Prosecutor Karim Khan* + *International Criminal Court ousts chief prosecutor Karim Khan* |
| 7 | 6 | *NC woman missing in Grenada* + *NC physical therapist disappears in the Caribbean* |
| 6 | 6 | *Mamdani's Grocery Stores Will Offer 30 Percent Discount* + *City-owned groceries to offer 30% discounts* |
| 5 | 5 | *'God Of War: Laufey' and 'Fable' Play PS5-Xbox Release Date Chicken* + *The next Kratos God of War game will follow and connect to Laufey* |

Seven are the same event under different words. The eighth — God of War — is the
same-story-different-angle case: one is release-date jockeying, the other plot continuity. Arguably
two news items about one subject rather than one event. It is the only debatable join in fourteen,
and it is a judgement call, not a false merge.

**No false merge appeared.** Both known false positives from the duplicate audit — a French
wildfire paired with a Californian one, two unrelated Nvidia stories — sat at 0.31 and the 0.33
threshold excluded them, which is the sample that set the threshold behaving as designed. The Red
Sea explainer that paired with two separate Houthi events did not chain them either.

Two consequences worth knowing:

* **14 stories get new IDs on enable.** Story ids anchor to the earliest member, and merging
  changes which article that is. A one-time churn on deploy; the broader id-stability question is
  still open and unmeasured.
* **Merged stories can be titled by the smaller half.** *"Fauci diary entries"* (5 articles) titles
  the merged 13-article story because its earliest member is earliest overall. Consistent with the
  existing rule, but for a merged story the largest contributor is usually the better headline.

The eligible set for a bias summary should grow past 57: Seattle now carries 14 publishers where
its halves carried 9 and 6.

## Story-id churn — asserted, never measured

`story_service`'s own docstring says ids are "stable across rebuilds as a cluster evolves". That
holds for exactly the case it was designed against — a LATER article joining never disturbs
`min(members, key=publishedAt)` — and not for two that happen routinely:

* **The representative ages out.** The candidate set is a rolling time window, so every cluster
  eventually loses its oldest article and the anchor moves to the next-oldest.
* **An earlier article arrives.** Ingestion is not ordered by publication time; GDELT's GKG
  backfill attaches articles published hours or days earlier, which moves the anchor backwards.

A story id is what a saved or shared link points at, so this is data integrity rather than
tidiness. Adopting the duplicate merge made it concrete: **14 stories were reassigned ids in one
deploy**, because merging changes which member is earliest.

`examples/audit_story_id_churn.py` measures it by replaying the window over the catalog we already
have — build at successive cutoffs, match stories across consecutive builds **by member overlap**,
count how many surviving stories changed id. Matching by members rather than by id is the whole
design: matching by id could only ever report zero.

```
docker exec deploy-api-1 python /app/examples/audit_story_id_churn.py --step-hours 24 --steps 3
```

It separates the two causes, because they have different fixes. Reading it:

### Measured 2026-07-28: **5.1% per day**

| step | surviving stories | id changed | aged out | earlier arrived |
|---|---:|---:|---:|---:|
| 07-25 → 07-26 | 396 | 6 | 6 | 0 |
| 07-26 → 07-27 | 505 | 32 | 27 | 5 |
| 07-27 → 07-28 | 692 | 43 | 39 | 4 |
| **total** | **1,593** | **81 (5.1%)** | **72** | **9** |

Not a tail effect: the stories losing their ids include a 61-article story, a 58, a 52, a 49 and a
48, all at member overlaps of 0.58–0.92 — unmistakably the same story surviving under a new id.
And 89% of it is the representative ageing out, which is **structural**: the window rolls, so every
long-lived story eventually loses its oldest member. No member-derived anchor survives that,
because the failure *is* the anchor leaving.

### The fix — `story_member`, on by default

Ids are given back rather than recomputed. A `url → story_id` table records what the last build
served; on the next build a cluster that still holds a **majority** of some previous story's
articles inherits that id, whatever its earliest member is now.

Two exclusivity rules do the work, and they are what makes splits and merges behave:

* one story may claim only one prior id — a **merge** keeps its larger contributor's id and the
  smaller one retires, instead of both surviving on one story;
* one prior id may go to only one story — a **split** gives the id to the piece holding most of the
  original coverage, and the other pieces are new stories, which is what they are.

`stabilize_ids` is deliberately **not** part of `build_stories`. That function is pure — same rows
in, same stories, ids and order out — and the suite and every audit depend on it staying so.
Identity is a property of what was published *before*, not of the input rows, so it is applied only
where the product is served, and only on the unfiltered build: letting a topic- or date-filtered
view write the map would hand ids to partial clusters and then hand them back on the next full
build, which is churn caused by the fix for churn.

It fails soft in both directions. If the table cannot be read or written, stories keep their
derived ids — a churned id is a broken link, a 500 is a broken page. The table is rewritten
wholesale from the current window each build, which prunes it for free.

### Verified 2026-07-28 by replaying both

| step | surviving | derived | stabilized |
|---|---:|---:|---:|
| 07-25 → 07-26 | 398 | 6 | **0** |
| 07-26 → 07-27 | 492 | 29 | **1** |
| 07-27 → 07-28 | 686 | 42 | **1** |
| **total** | **1,576** | **77 (4.9%/day)** | **2 (0.1%/day)** |

A 97% reduction, measured rather than asserted — the auditor threads the identity map between
builds exactly as the store does, so both columns come off the same replay.

The two residual cases are the majority rule declining to carry an id, not a bug: those are
clusters whose membership turned over far enough that fewer than half their articles carried the
prior id, and whether such a thing is still "the same story" is a real question rather than an
obvious yes.

The auditor had to be taught to see this. Its first post-fix run reported the same 4.9%, correctly
and uselessly: it calls ``build_stories``, which is pure and knows nothing about ``stabilize_ids``,
so it was measuring the derived id — exactly the churn the fix routes around. A fix shipped
alongside an instrument that cannot evaluate it is not a measured change.

`RWE_STORY_STABLE_IDS=0` reverts without a deploy.

Related and smaller: a merged story can be titled by its smaller half — *"Fauci diary entries"* (5
articles) titles the merged 13-article story because it holds the earliest member. Consistent with
the rule, but for a merged story the largest contributor is usually the better headline. Cosmetic;
noted so it is a decision rather than an oversight.

## One outlet counted as several

`publisherCount` counted distinct publisher **strings**, and feeds do not agree on how to name an
outlet. Measured on the live catalog: **181 of 1,367 publisher names are duplicates of another**,
**60 stories carried an inflated count**, and — the part that is a correctness failure rather than
a cosmetic one — **35 stories cleared `min_publishers` only because one outlet was counted twice.**

The dominant case is a syndication network:

```
17 arts, 17 "publishers"  Accused Murderer Arrested While Trying To Board Cruise Ship
                          every member a *.iheart.com station hostname
10 arts, 10 "publishers"  Shah Projected Winner In AZ Democratic Primary
 9 arts,  9 "publishers"  Yellowstone Closes Three Rivers To Fishing
```

~100 iHeartRadio station hostnames syndicating identical copy. This is the M.D. Sass template in a
new costume — one source, many hostnames — except it defeats the admission gate entirely, because
seventeen hostnames look like seventeen publishers. `Samsung Galaxy Z Fold 8` sat near the top of
the ranking on 26 publishers that are really 8.

`publisher_identity.groups` collapses them, and the pipeline uses the result in four places that
all read the same count: the `min_publishers` admission gate, `publisherCount`, `_distribution`
(one lean vote per outlet, so a fragmented outlet no longer votes many times), and the rated-
publisher floor. The story's publisher LIST names each outlet once, by the form it used most often.

### The false positive that shaped the rule

The first version keyed on the bare brand label and collapsed **`standard.net.au`** (the
Warrnambool Standard) into **`standard.co.uk`** (the London Evening Standard) — two unrelated
newspapers. Acting on that finding would have been worse than the problem it reported. So:

* **two host forms** collapse only on a matching brand domain (label + public suffix) — keeps every
  iHeart station together, keeps the two Standards and The Local's five national editions apart,
  still joins `obits.oregonlive.com` to its parent and `Videocardz.Com` to `Videocardz.com`;
* **a host and a bare name** collapse on the brand label, the only route from `Sportskeeda` to
  `Sportskeeda.Com` when neither is curated — but only where exactly one domain carries that
  label, so an ambiguous bare `Standard` is left alone rather than guessed.

Correcting it removed 13 false collapses (194 → 181 duplicate names).

The rule lives in `publisher_identity` and the audit imports it, because an audit measuring a
different rule than production applies would be measuring nothing.

**Expect ~35 fewer stories on the next deploy.** They were never stories.
`RWE_STORY_PUBLISHER_IDENTITY=0` reverts without a deploy.

### What this reorders

Alias gaps turned out to be almost nothing — **one**, Daily Mail → `Dailymail.Com`, 21 articles.
The registry is not missing forms, it is missing outlets. And rating outlets cannot help a story
that should not exist, so identity comes before curation. The remaining registry work, in order:
syndication networks and the ~30 domain/name pairs; `Globenewswire.Com` and `Prnewswire.Com` as
`kind=wire`; then lean curation for the genuine gaps (ESPN, Variety, Philadelphia Inquirer, The
Star Malaysia, Winnipeg Free Press).

### Curated, and closed

The rule's own worklist came back as exactly three names — `ESPN`, `Fool`, `Pr Newswire` — each a
brand word carried by more than one domain, which is the only case it declines to guess. All three
are one outlet running national editions, so a curated row settles them:

| row | effect |
|---|---|
| `PR Newswire`, `GlobeNewswire` → `kind=wire` | settles the identity **and** keeps press releases out of stories |
| `ESPN`, `The Motley Fool` → identity-only rows | aliases across national domains, country curated, **lean blank** |
| `Daily Mail` + `dailymail.com` | the single alias gap: a rated outlet whose 21 articles counted as unrated |

ESPN and The Motley Fool get lean-blank rows on purpose. They settle *who* the outlet is, not where
it stands — guessing a lean to close an identity gap is the L2.2 violation the registry exists to
prevent, and the curated country is what earns an unrated row its place under the integrity
invariant.

**Final state: 0 stories that are one outlet, 0 missing aliases, 0 unplaceable names.** ESPN now
collapses all four of its forms; the catalog settled at ~1,006 stories as the press releases left.

Two audit tests broke when this shipped, because they used `Dailymail.Com` and `Pr Newswire` as
examples of *unresolved* forms. That is the tests having quietly become assertions about registry
contents rather than about the rule, and they now use uncurated names instead.

### What remains is judgment, not measurement

Every surviving collision — Sportskeeda, iHeart, Yahoo, Sydney Morning Herald, The Age, Oregonlive
— is **handled correctly at runtime** by the identity rule. Curating them makes the resolution
permanent rather than heuristic; nothing is broken while they are not.

The one item that still moves a product number is lean curation for the genuine gaps: Variety,
Philadelphia Inquirer, The Star Malaysia, Winnipeg Free Press, Brisbane Times, Manila Times, The
West Australian. That is what takes coverage-gap claims from 61 toward the measured **41% ceiling**
(428 of 1,042 stories carry ≥ 3 publishers), and it needs defensible public ratings sourced by a
human rather than inferred.

## Curating seven outlets moved claims 61 → 62

Ratings were added for the outlets the identity audit surfaced as genuine registry gaps — Variety,
Philadelphia Inquirer, Winnipeg Free Press, The Manila Times, The West Australian, The Star
(Malaysia), all from Media Bias/Fact Check's published classifications. Brisbane Times was left
unrated because MBFC has no page for it, and inheriting from its sibling mastheads (SMH, The Age)
is the guess this file refuses.

**Coverage-gap claims went from 61 to 62.** The prediction was that ~150 newly-rated articles would
move it materially. That was wrong, and the reason is structural rather than a shortfall in the
curation:

**The real cause: a registry edit does not reach articles already in the catalog.** An article's
lean is written into its `scored` JSON at INGEST time, and `feed_article_to_article` reads it from
there — so a newly rated outlet keeps casting no vote until its next article arrives, and the
existing ones age out of the six-day window instead of being corrected. The audit went on listing
`Dailymail.Com`, `Winnipegfreepress.Com`, `Inquirer.Com` and `Variety.Com` as unrated, all of them
rated an hour earlier. The registry was right and the catalog had not heard.

`examples/backfill_lean.py` fixes it: rewrite the stored lean from the registry wherever the two
disagree. Narrow on purpose — only the lean field (category, register, emotion and confidence were
measured per article, while the lean is a property of the outlet), never invents a rating for an
unrated or unknown row, and idempotent, so it is safe after every curation pass.

```
docker exec deploy-api-1 python /app/examples/backfill_lean.py --dry-run
docker exec deploy-api-1 python /app/examples/backfill_lean.py
```

A second, smaller effect is real but was not the main one: **adding a rating does two opposite
things.** It can lift a story from two rated publishers to three and *enable* a claim; it can also
fill an empty lean bucket in a story that already had three and *remove* one.

So article volume is the wrong worklist, and it was the one being used. An outlet with sixty
articles spread across sixty stories that each already carry four rated publishers unlocks nothing.
The right worklist is **unrated outlets appearing in stories that are exactly one rating short** —
the only case a single registry row can convert. `audit_cluster_trust` now reports it, alongside a
count of the stories that are two or three short and need coordinated curation.

Identity applies inside that count too: one outlet under two name forms is one missing rating, not
two.

### The ceiling, restated

428 of 1,042 stories carry ≥ 3 publishers — **41%**, and that is capped by story size (p50 = 2
articles), not by the registry. Reaching it needs broad rating coverage across the long tail, not
a handful of rows at the head. The worklist now says which rows actually buy something.

### Verified after the backfill

| | before curation | after rows | after backfill |
|---|---:|---:|---:|
| coverage-gap claims | 61 | 62 | **72** |
| stories 1 rating short | — | 89 | **85** |

The six rated outlets dropped off the unlock worklist entirely, which is the check that the rows
reached the catalog rather than only the file. The +10 is the curation actually landing; the +1 it
managed beforehand was newly-ingested articles trickling in.

**Three of the four biggest remaining wins are outlets deliberately left unrated**, and that is the
honest ceiling on this approach rather than a backlog:

| unlocks | outlet | why unrated |
|---:|---|---|
| 6 | Page Six | no row yet — a genuine gap |
| 5 | ESPN | identity-only row: aliases and country curated, lean blank |
| 4 | Brisbane Times | MBFC has no page; inheriting from SMH/The Age is the guess this file refuses |
| 2 | Philippine Daily Inquirer | pre-existing locality-only row |

Rating them would unlock 11 stories between them, and doing it without a defensible public source
would put three fabricated claims into the product to gain eleven. The worklist is doing its job by
naming them; the answer is to find sources, not to fill the blanks.

### Finding the sources

Four of those five were findable. MBFC and AllSides both return **403 to an automated fetch**, so
each rating below comes from a search-result summary of the MBFC page rather than the page itself —
a defensible public source, and second-hand. That distinction is recorded in the registry beside the
rows, because it bears on how much they should be trusted.

| outlet | MBFC | on this scale | note |
|---|---|---:|---|
| Page Six | Right-Center, factual Mixed | +1 | rated **apart from** the New York Post (+2), which owns it |
| ESPN | Left-Center, factual High | −1 | was an identity-only row |
| Philippine Daily Inquirer | Left-Center, Mostly Factual | −1 | was a locality-only row |
| Ynetnews | Left-Center, factual High | −1 | |
| The News International | Right-Center, factual Mixed | +1 | |
| London Evening Standard | Right-Center, Mostly Factual | +1 | |

**Brisbane Times still has no rating and still gets none.** It shares an owner with the Sydney
Morning Herald and The Age, both rated; inheriting from a sibling masthead is precisely the guess
this file exists to refuse. Its four unlocks stay locked.

Page Six is the case worth naming. It is the New York Post's gossip desk, shares its newsroom, and
is rated one notch *less* right than its owner. An ownership prior would have written +2 — which is
why the file is a table of ratings and not a table of inferences.

### Curating a domain must not license a guess about a name

`publisher_identity` keys a name that resolves by its CANONICAL, and counted brand labels the same
way. That is a latent bug, and rating `standard.co.uk` would have triggered it:

* before: `standard.co.uk` and `standard.net.au` are two domains carrying the label `standard`, the
  label is contested, and a bare `Standard` is left unplaced — correct, since it could be either
  the London Evening Standard or the Warrnambool Standard;
* after curating one of them: `standard.co.uk` resolves and moves off its domain token, leaving
  `standard.net.au` as the *only* `standard` domain — the label reads unambiguous, and a bare
  `Standard` is placed with the Warrnambool paper.

Curation would have created a wrong identity merge in a name it never touched. Fixed by counting
labels over **the form the feed sent**, not the resolved canonical: which brand words are contested
is a fact about the domains in the world, not about which of them someone has got round to rating.

The audit's unplaceable-names report is unaffected — it lists names that do not resolve, and a
curated name no longer needs placing.

### The tail stays blank

The catalog carries ~1,188 outlet identities against ~200 rows. The remainder is small local
mastheads — `Edenmagnet.Com.Au`, `Somdnews.Com`, `Batleynews` — that MBFC has never assessed and no
other public rater covers. MBFC's ~3,900 sources are heavily US/UK weighted and simply do not reach
this tail. Filling those blanks from an owner, a sibling, or a country prior would put on the order
of a thousand fabricated claims into the product to gain coverage, which inverts the thing the
product is for. **An outlet with no rating anywhere stays unrated for as long as that is true.**

### Sweeping the blank leans

The worklist finds rows where a rating unlocks a *claim*. It says nothing about rows that were
curated for identity and locality and then never revisited. There were twenty of those, and they
had never been looked up as a set — each had been skipped once, in passing, for a reason nobody
recorded.

Fourteen of the twenty have a published rating.

| filled | MBFC | | filled | MBFC |
|---|---|---|---|---|
| Der Spiegel | Left-Center | | Ahram Online | Right-Center |
| Die Zeit | Left-Center | | Clarín | Right-Center |
| Süddeutsche Zeitung | Left-Center | | La Nación | Right-Center |
| The Punch | Left-Center | | NHK | Right-Center |
| Mirror | Left-Center | | Nikkei Asia | Right-Center |
| The Motley Fool | Left-Center | | France 24 | Least Biased |
| Mail & Guardian | **Left** | | New Zealand Herald | Least Biased |

Plus two new rows the worklist named: **Yahoo News** (Left-Center) and **WDIV ClickOnDetroit**
(Least Biased).

Two of these are worth pausing on, because they are the reason the file holds ratings rather than
impressions. **NHK** is rated *right*-of-centre — MBFC cites story selection favouring Japanese
nationalism — which few would predict of a public broadcaster. **Mail & Guardian** is the only
outright Left (−2) in three curation passes, and it arrived with a *Mixed* factuality rating
attached; lean and reliability are different axes and this file only carries one of them.

The identity-only category is now empty: both of its members, ESPN and The Motley Fool, had ratings
sourced and moved to the rated blocks. That is the intended trajectory. **An identity-only row is a
stage, not a verdict** — nothing in this file should be read as "unratable".

### The six that stayed blank, and why that list is the deliverable

| outlet | why |
|---|---|
| O Globo | MBFC rates **Globo, the parent group**, not the newspaper |
| Folha de S.Paulo | confirmed absent from MBFC, AllSides *and* Ad Fontes |
| Milenio | no MBFC page |
| Nigerian Tribune | no MBFC page |
| The East African | only its owner, Nation Media Group, is rated |
| Brisbane Times | no MBFC page; its sibling mastheads are rated |

Off-registry, from the same worklist: **Sportskeeda** has an aggregated *factuality* score and no
bias rating anywhere — factuality is not lean, and substituting one for the other would be a
fabrication with a citation attached. **Sky Sports** is unrated; Sky News UK is Least Biased and is
a different masthead. **WGAU** is unrated; MBFC rates WUGA, the NPR station in the same city.

O Globo is the one that costs something. A rating for its parent was available and would have looked
perfectly citable — and it is the same ownership inference refused for Page Six one commit earlier,
where the masthead turned out a notch away from its owner. A refusal that only ever applies when it
is free does not mean anything.

`test_the_unrated_set_is_exactly_the_documented_one` pins this: every blank lean in the file must be
one the comments give a reason for. Adding an unrated row stays legal — adding one *silently* does
not.

### One stale comment, corrected

The pass-1 log read *"Confirmed NOT rated by AllSides (lean stays blank): … France 24, Le Monde,
Der Spiegel, ABC Australia, Sydney Morning Herald."* All eight of those names have since been filled
from MBFC. **One rater having no page is a fact about that rater, not about the outlet** — and it is
not a reason to stop looking. The comment now says so, because as written it was quietly telling the
next person those rows were closed.

### Fourth pass: coverage, not repair

The first three passes filled blanks in rows that already existed. That is bounded by whatever the
file happened to list, and the file was never audited for what it *omitted*. Probing it against a
list of large mastheads a global feed carries turned up 38 with no row at all.

Fourteen are now rated:

| | | | | |
|---|---|---|---|---|
| Rolling Stone −2 | Global News −1 | RNZ 0 | Newsweek +1 | The Sun (UK) +2 |
| | AFP −1 | The Oregonian 0 | The Australian +1 | Daily Express +2 |
| | Metro (UK) −1 | | National Post +1 | Anadolu Agency +2 |
| | | | India Today +1, Barron's +1 | |

Two raters disagree on **Newsweek** (MBFC Right-Center, AllSides Center) and on **Barron's** (MBFC
Right-Center, AllSides Center). Both are recorded in the file rather than smoothed away — the
mapping this file uses is MBFC's, and where AllSides dissents that is worth knowing.

Two near-misses worth naming, because the fix is a domain and not a judgment: MBFC rates **The Sun
(UK)** and **The US Sun** as separate outlets, and the **Daily Express** separately from **The
Express US**. Only the rated domain is aliased, so `thesun.com` resolves to nothing rather than
inheriting a RIGHT rating it was never given.

### Rated, and deliberately not imported

MBFC publishes a **credibility** verdict alongside the lean. For four of the outlets found this
pass — Xinhua, Global Times, RT and The Economic Times — that verdict is *Questionable* or *Low
Credibility*: state propaganda, failed fact checks, very low factual reporting. A lean exists for
each and could have been pasted in.

It was not, and the reason is structural rather than editorial. **This file has no credibility
column.** A Questionable source's lean would reach `_distribution` and the ≥ 3 rated publishers
floor carrying exactly the weight of Reuters', so a coverage-gap claim could come to rest on two
state broadcasters with nothing in the product showing it. Identity and country are still curated —
those are facts — and the lean waits until the file can say *rated, but not credible*.

The line is **MBFC's own flag, not an impression of the outlet.** State-aligned outlets MBFC rates
at Medium credibility or better are rated here: Daily Sabah (+2), Ahram Online (+1) and Anadolu
Agency (+2) are all in the file. Without that constraint the rule would quietly become "outlets I
distrust", which is the same fabrication this file exists to prevent, pointed the other way.
`test_the_questionable_line_is_mbfcs_own_flag_not_an_impression` pins it.

The same reasoning keeps **Billboard** out: AllSides rates it Left but flags *low confidence*, and a
file with no confidence column would present that identically to a settled rating.

**This is one cell each to reverse, and it is a product decision rather than a data one** — if the
registry grows a credibility column, all four can be rated and displayed with the caveat attached.
That is the obvious next step for this table.

### O Globo, re-checked and unchanged

Worth recording because the second look *appeared* to overturn the first. Ground News reports a
Lean Right bias for Globo — but its entry is `/interest/globo`, the parent group, and the rating it
aggregates is MBFC's Globo page. Same source, one hop further away. O Globo stays blank.

A second search that returns the same evidence dressed differently is not new evidence.

### Fifth pass: the US metro dailies

Probing the registry against 181 outlets a global feed plausibly carries found **172 with no row at
all**. The largest single hole was domestic: **twenty of the biggest US city papers were missing.**

That is not a cosmetic gap. A US story reaching the Boston Globe, the Star Tribune, the Arizona
Republic, the Tampa Bay Times and the Kansas City Star was reaching five *unrated* publishers, so it
could not support a coverage-gap claim at all — the exact story shape the product exists to explain.
All twenty are rated by MBFC.

The result is lopsided, and it is worth stating rather than burying: **seventeen Left-Center, three
Right-Center.** That is what MBFC publishes for this set.

| Right-Center | why MBFC says so |
|---|---|
| Chicago Tribune | free-market, limited-government editorials |
| Dallas Morning News | slightly right-leaning editorial bias |
| The Detroit News | has never endorsed a Democrat for president; backed Gary Johnson in 2016 |

The Chicago Tribune is the one to double-take on — a big-city daily rated *right*-of-centre is
counterintuitive, and it is MBFC's rating rather than an impression, the same as NHK.

Detroit is the case that justifies per-masthead rows: **the Free Press is −1 and the Detroit News is
+1, in one city.** Collapsing them on locality would erase the only interesting thing about the pair.

AllSides dissents to Center on four — Houston Chronicle, Arizona Republic, St. Louis Post-Dispatch,
Cleveland (low confidence) — and Ad Fontes puts several in "Middle". Recorded rather than averaged
away; the mapping this file uses is MBFC's.

Two rows carry a masthead's rating on that masthead's own website (`cleveland.com` for The Plain
Dealer, `chron.com` for the Houston Chronicle). That is the same publication under its own domain,
**not** the ownership inference refused for Page Six and O Globo — and it is pinned by a test,
because Ad Fontes rates the website separately from the paper and the two are easy to confuse.
SFGate gets its own row for the same reason: MBFC rates it separately from the SF Chronicle, and
they happen to agree today, which is exactly what would make a shared row look harmless until one
of them is re-rated.

### Where the registry stands

| | start of day | now |
|---|---:|---:|
| rows | 154 | **199** |
| rated | 143 | **182** |
| identity-only, reason recorded | 4 | 10 |
| wire feeds (blank by construction) | 7 | 7 |

Still missing from the 181-outlet probe: UK nationals beyond the ones added, Canadian and
Australian mastheads, the Indian dailies, the state wires, and most of continental Europe and Latin
America. Those are the next tranches, and they are findable — unlike the ~1,000-outlet long tail of
small local mastheads, which no public rater covers and which stays blank.

### Sixth pass: UK, Canada, Australia, India

Twenty more rated, two more withheld.

| | |
|---|---|
| **UK** | iNews −1, New Statesman −1, The Herald (Scotland) −1, The Scotsman 0, The Spectator (UK) +1 |
| **Canada** | Financial Post +1, Montreal Gazette +1, Vancouver Sun +1, Toronto Sun **+2** |
| **Australia** | Crikey −2, SBS News −1, The New Daily −1, 7NEWS +1, 9News +1, AFR +1 |
| **India** | Scroll.in −1, Business Standard +1, Firstpost +1, ThePrint +1 |

**India is the finding.** MBFC rates three of the four Right-Center for the same stated reason —
coverage favouring the ruling party. That is a property of that market, not of this file, and a
reader looking at an Indian story should be able to see it. It is also the strongest argument yet
for rating outside the US/UK: a story covered by four Indian outlets looked like four neutral
sources an hour ago.

**Toronto repeats Detroit, wider.** The Toronto Star is 0 and the Toronto Sun is +2 — one city, two
points apart. Any scheme that collapsed mastheads by locality would have merged them.

Two more near-misses fixed by a domain: MBFC rates **The Spectator (UK)**, **The Spectator (USA)**
and **Spectator World** as three separate outlets, so only `spectator.co.uk` is aliased. And a **bare
"CBC"** now reaches the CBC News row — an alias, not a rating: the outlet was rated all along and
the feed's commonest form simply did not resolve. That remains the cheapest class of fix in this
file, and the publisher-identity audit is what surfaces it.

**Daily Star (UK)** and **GB News** join the withheld set — MBFC publishes a lean for both and
classes both *Questionable*. That is now six outlets rated-but-withheld, which is enough to say the
rule is load-bearing rather than a one-off: a credibility column is the next thing this table needs.

| | start of day | now |
|---|---:|---:|
| rows | 154 | **220** |
| rated | 143 | **201** |
| rated-but-withheld (Questionable) | 0 | 6 |
| unrated anywhere | 4 | 6 |
| wire feeds | 7 | 7 |

### Seventh pass: US nationals, the news agencies, Europe, Latin America

The last of the coverage audit's findable set — **33 rated, 2 more withheld**.

**The four news agencies are the quiet win.** DPA, Agencia EFE, PA Media and Kyodo News are all
Least Biased, joining Reuters and the AP at 0. That is completely unsurprising, which is exactly why
it matters: **a centre with real weight is what makes a lean distribution mean anything**, and wire
copy is where most of it comes from. Note these are news *agencies*, not the `kind=wire` rows —
that flag means machine-generated market-data and press-release copy, which has no editorial stance
to rate at all.

**The Netherlands and Sweden each landed a pair on opposite sides:** NRC −1 against De Telegraaf +2,
Dagens Nyheter +1 against Aftonbladet −2. A Dutch or Swedish story can now show a real spread
instead of a row of unrated names — which is the whole product, applied to a country that had no
coverage an hour ago.

The Conversation is the only **Very High** factual rating in the file. It is recorded in a comment
rather than a column, which is the same gap the withholding rule keeps running into.

**Sputnik and TASS** join the withheld set — MBFC rates both Right-Center and classes both
*Questionable* ("100% Russian propaganda", factual Very Low). That is **eight** rated-but-withheld
outlets now.

Checked and not rated anywhere, so no row: Página/12 (AR), El Tiempo and El Espectador (CO),
Estadão and UOL (BR), Belfast Telegraph (GB).

### Where the registry ended up

| | start of day | end |
|---|---:|---:|
| rows | 154 | **255** |
| rated | 143 | **234** |
| countries with at least one outlet | ~30 | **40** |
| rated-but-withheld (Questionable) | 0 | 8 |
| no rating anywhere | 4 | 6 |
| wire feeds (no editorial stance) | 7 | 7 |

The rated set spans the scale rather than clustering: **19 at −2, 91 at −1, 46 at 0, 58 at +1,
20 at +2.** The −1 bucket is the largest by a distance, and that is MBFC's shape for large
English-language mastheads, not a thumb on the scale here — it is visible in the file precisely so
someone can argue with it.

### What is left, and why it stops here

The remaining catalog tail is on the order of a thousand small local mastheads — `Edenmagnet.Com.Au`,
`Somdnews.Com`, `Batleynews`. **No public rater covers them and none will.** They stay blank, and
that is a finished state rather than a backlog.

The next real move is not more rows. It is a **credibility column**: eight outlets now have a
published lean this file deliberately refuses to import, and that number only grows. With one more
column they could be rated *and* shown with the caveat attached, instead of being invisible.

After that, the measurement that matters is not registry size at all — it is whether coverage-gap
claims went up. `backfill_lean.py` then `audit_cluster_trust.py` will say.

## The credibility column

Eight outlets ended the coverage audit in a bad state: a **published MBFC lean** sitting next to an
**MBFC *Questionable* / *Low Credibility* verdict**, and a registry that could record only one of
those. The workaround was to leave the lean blank — which threw away a true fact to avoid a
misleading one, and made Xinhua indistinguishable from an outlet nobody has ever assessed.

`credibility` is column 9: `high` / `medium` / `low`, blank = uncurated.

**Blank is not `low`.** Absence of a verdict never disqualifies an outlet, exactly as absence of a
lean never centres one. Only ~30 of 255 rows carry a verdict, because only those were seen stated —
the column is sparse *on purpose* rather than filled in by impression.

### What `low` does

A low-credibility outlet is **full coverage and no vote**:

| | counted? |
|---|---|
| `totalCoverage`, `publisherCount`, `publishers`, `coverage` | **yes** — it really did cover the story |
| the article's own `lean` / `leanBucket` | **yes** — this is the fact the old workaround destroyed |
| `distribution` (one vote per outlet) | no |
| the ≥ 3 rated-publishers floor for a coverage-gap claim | no |
| `lowCredibilityPublishers` (new, on the story and the API) | listed by name |

The floor and the distribution share one helper, `_votes`, so they cannot drift into disagreeing
about who counts — a story clearing "three rated publishers" on a sample the distribution then
declines to use would be a subtle and permanent lie.

The concrete failure this prevents, from the test: two left-leaning outlets plus TASS. Ungated, TASS
is the third rated publisher, the story clears the floor, and the product asserts a right-side
coverage gap **on the strength of a state wire its own rater calls Questionable**.

### Two properties worth keeping

**The verdict is resolved from the registry at build time, not read from stored article JSON.** The
lean is written into `scored` at ingest and needs `backfill_lean.py` to change; the credibility gate
deliberately does not. Correct a verdict and the next build has it.

**The bar is the rater's own flag.** State-aligned outlets MBFC rates at Medium or better are rated
and voted normally — Daily Sabah (+2), Ahram Online (+1), Anadolu Agency (+2) are all in the file
and all vote. Without that constraint the column drifts into "outlets I distrust", which is this
file's founding fabrication pointed the other way. There is a test named after it.

`RWE_STORY_CREDIBILITY_GATE=0` restores pre-column behaviour with the leans left in place: if a
verdict turns out to be wrong the fix is a flag, not a re-curation.

### What is left

The API now carries `lowCredibilityPublishers`; **the web surface does not render it yet.** Until it
does, a reader sees TASS in the publisher list with no indication that its rating was set aside —
better than the outlet vanishing, but not the finished story. That is the next piece of work, and it
is a design question (badge? footnote? filter?) rather than an engine one.

## Found in production: a disambiguating parenthetical claimed the bare word

The first post-deploy identity audit reported:

```
51  Thestar.Com.My (40) | The Star (Malaysia) (7) | The Star (4)
```

`_name_key` drops parentheticals — that is how `"Fox News (Online News)"` reaches Fox News. But it
does the same to a canonical, so **`The Star (Malaysia)` registered under the bare word `star`** and
answered every feed's bare "The Star". A Toronto Star article arriving under that name got
**lean +2 instead of 0, and country MY instead of CA.** Four articles, two points and a continent.

The rule is now: **a name form carrying a parenthetical is registered under its FULL key only.**
A suffix on an undisambiguated canonical still strips (`Fox News (Online News)` → Fox News); a
disambiguator no longer claims the generic word. Structural, not a patch for one row — `Metro`,
`Vanguard`, `Daily Star`, `The Herald` and `The Spectator` all stopped answering to their bare forms,
and each of those words belongs to more than one real outlet. Explicit aliases are unaffected:
`RT` still reaches `RT (Russia Today)` because someone wrote it down.

### The same bug, one layer down

`publisher_identity.groups` keyed a resolved name on `_name_key(canonical)` — the bare word `star`
again. Fixing resolution alone would have left the two mastheads merged in the identity map, which
is what feeds `publisherCount`. Resolved names are now keyed on the **canonical string itself**.

### And the regression that fix nearly caused

Keying on the canonical alone severed the bridge from a curated row to the uncurated host form the
feed actually sends — which is **how a missing alias is detected at all** (`Daily Mail` ↔
`dailymail.com` was found exactly that way). The stubbed missing-alias test caught it immediately.

A canonical without a parenthetical now also joins its own bare name key, restoring the bridge; one
carrying a parenthetical does not, for the same reason the registry declines to register it. Both
directions have a test, named after what they protect.

**Three layers, one normalisation mistake.** The registry, the identity map, and the audit that
reads them all had to agree — and the only reason the third didn't ship broken is that a test was
written against the *rule* rather than against a real outlet.

## Two rows the production audit named

Not from a probe list — from the live collision table.

* **Fortune** `+1` — MBFC Right-Center, factual High. Ad Fontes and AllSides both say *Centre*;
  recorded, not averaged away. It was also worth 2 unlocks on the worklist.
* **WAtoday** — **no rating.** MBFC has no page. Biasly has one and its own summary contradicts
  itself (−14% "Somewhat Left" in one place, −6% "center" in another), which is not a source to rate
  an outlet from — the same call already made for Brisbane Times. Locality earns the row.

## The worklist was double-counting

`blocked_by_ratings` computed everything in identity space and then reported in **name** space, so
production listed `Brisbanetimes.Com.Au` at 4 unlocks and `Brisbanetimes` at 2 — one masthead worth
4. It now aggregates on the identity key and displays the commonest form, the same rule
`_display_publishers` uses. A worklist that double-counts is not safe to prioritise from, which is
the only thing a worklist is for.

## Post-deploy measurement

| | before curation | after deploy + backfill |
|---|---:|---:|
| coverage-gap claims | 72 | **87** |
| — resting on 4+ rated publishers | 23 (32%) | **35 (40%)** |
| stories with outlets but not ratings | 271 | **245** |
| `ok` share of articles | 98.0% | 98.0% |
| stories that are one outlet twice | 0 | **0** |
| missing registry aliases | 0 | **0** |
| largest ÷ p90 · largest share | 14.9× · 2.5% | 15.3× · 2.5% |

1,432 articles across 139 publisher forms were backfilled. The catalog grew during the same window
(1,008 → 1,037 stories), so claims alone would be an unreliable read — **the 4+ bucket rising 52% is
the cleaner signal**, because it measures how well-supported a claim is rather than how many exist.

## The backfill wrote leans but never withdrew them

Measured right after the disambiguation fix shipped: **10 articles carried a lean the registry had
stopped asserting.**

| publisher | stored lean | what it was actually claiming |
|---|---:|---|
| The Star ×6 | +2 | the Malaysian paper — possibly Toronto's (0) or Kenya's |
| Metro ×2 | −1 | Metro UK — possibly Metro Philadelphia |
| The Sun ×1 | +2 | The Sun UK — MBFC rates The US Sun separately |
| The Herald ×1 | −1 | Herald Scotland — there are many Heralds |

Nothing about those articles changed. The **registry** did, and `backfill_lean.py` had no way to
follow: it writes a lean when the registry has one, and returns `None` — no write — when it doesn't.
So a *rating* fix reaches stored articles and a *resolution* fix does not. The ten went on voting a
lean nobody stood behind.

`--clear-orphaned` closes the direction. An outlet stops asserting a lean two ways and both count:
it stops resolving (a disambiguation, an alias removed), or it resolves to an unrated row (a rating
withdrawn). Either way the honest stored value is `null` — real coverage, no vote.

Two deliberate asymmetries:

* **Always reported, never silently acted on.** An orphaned lean is invisible from the registry side
  — the row simply isn't there — so a count nobody prints is no better than the bug. It appears in
  every run, including `--dry-run`.
* **Opt-in to act,** because this *removes* data rather than correcting it. Ten articles age out of
  the six-day window on their own; the flag exists for when they shouldn't have to.

A stored lean is only ever written *from* the registry, so one the registry cannot account for is
stale by construction — that is what makes clearing safe rather than lossy. `plan` and `plan_orphans`
are asserted never to overlap: an article in both would mean the registry simultaneously does and
does not rate its outlet.

**This is the third time one change had to be chased across a layer it did not obviously touch** —
resolution → identity map → audit, and now registry → stored article. The pattern is worth naming:
*a fact cached at write time does not follow a rule corrected at read time.*

## What is left to curate — `audit_registry_coverage.py`

Three audits each answered a piece of this and none answered it whole. `outlet_coverage` ranks
unknown outlets by article volume but counts **name strings**, so one masthead arriving as
`Yahoo.Com`, `Finance.Yahoo.Com` and `Yahoo! News` is three entries with its volume split three ways.
`audit_publisher_identity` groups those correctly but says nothing about ratings.
`audit_cluster_trust` reports the unlock worklist, which is the right worklist and the wrong
denominator for "how much is left".

The new audit joins them. Everything is counted **per outlet identity**, and every unresolved or
unrated outlet lands in **exactly one** bucket — a partition, because overlapping labels would
double-count the one number the report exists to give:

| bucket | meaning | is it work? |
|---|---|---|
| `untracked` | no row, brand word unambiguous | **yes** — a curator can add it |
| `ambiguous` | no row, bare name carried by several domains (`The Local`, `RTL`) | a row per edition, not a rating |
| `low-credibility` | rated, rater called it Questionable — lean recorded, not voted | no, a decision taken |
| `locality-only` | row exists, no public rater covers it | no, blocked on a source |
| `wire` | machine-generated market-data copy | no, nothing to rate |

Two rankings, because they answer different questions. **Article volume** says how much of the feed
an outlet accounts for. **Unlocks** says how many coverage-gap claims one row would enable — stories
exactly one rating short. They disagree often, which is the point.

### The bug its own fixture caught

The first run reported **0 unlocks** for an untracked outlet sitting in a story that was plainly one
rating short. The audit built its rated set from `leanBucket`, and a low-credibility outlet **carries
a lean it does not vote** — so TASS filled the third slot, the story looked fully supported, and the
curatable row beside it vanished from the worklist.

`audit_cluster_trust` had the same bug in `blocked_by_ratings` **and** in `rated_publishers`, which
feeds the claim-support table. So the worklists read in this thread were understating the work by
hiding stories a curator could actually fix. Both now go through one helper that asks the registry
directly, exactly as `story_service._votes` does.

**Fourth time in this thread that one rule had to be chased across a layer.** The credibility gate
shipped correct in the engine and wrong in two audits, because a story's `coverage` rows carry
`leanBucket` but not the flag — a derived view that lost the qualifier the original had.

### A number nobody could read correctly

The first run of that audit printed:

```
publisher names  : 4,431
outlet identities: 3,879
  fully tracked and rated : 183
```

and was read, reasonably, as saying the registry holds 183 rated outlets. It holds **243**. The 183
was a property of the **feed** — how many rated outlets published anything in the six-day window —
sitting directly under two registry-sounding headings with nothing to distinguish it.

Both numbers now travel together and are named for what they measure:

```
REGISTRY (the file)  : 257 rows, 243 rated
WINDOW   (the feed)  : 19,328 articles, 1,045 stories
    rated            : 183   (60 rated registry outlets published nothing in this window)
```

The 60-outlet gap is not a defect. Jacobin, Democracy Now, Middle East Eye, El Universal and most of
the national papers added in the coverage passes simply do not appear every week. **A registry is
sized for the outlets that can appear, not the ones that did.**

Worth recording as its own class of mistake: this was not a wrong calculation. Every number was
correct and the report still misled, because a label borrowed its meaning from the lines above it.

## Ninth pass: curating from the feed instead of from a list

Every previous coverage pass worked a list I assembled — plausible outlets a global feed *might*
carry. This one worked the **live audit output**: high-volume untracked outlets, and outlets sitting
in stories exactly one rating short. Nothing here was guessed at from a distance.

**17 rated**, one more withheld, two wires, three identity-only.

| | |
|---|---|
| −1 | The Verge, Manila Bulletin, The Korea Times, BuzzFeed News, AOL, BreakingNews.ie, Athens Banner-Herald, The Express-Times |
| 0 | KING 5, The Republican/MassLive, Detik, Inland Valley Daily Bulletin, ARD |
| +1 | KOMO News, Seeking Alpha, TheStreet |
| **withheld** | **News18** — MBFC Right-Center *and* Questionable/Low Credibility. 58 articles: the largest low-credibility source in the catalog. |

**`aktiencheck` and `FinanzNachrichten` are wires** — 478 articles between them in six days,
automated market-data and regulatory-disclosure copy. Template copy clusters *correctly*, so no
clustering signal can find it; only curated source identity can.

Three rows exist for **identity alone**, and each is a different reason a rating would be wrong:

* **Zazoom** (815 articles, the single biggest untracked name) is an **aggregator** — it republishes
  third-party headlines with reference links, so its articles are other outlets' stories wearing a
  second byline. Not a wire (nothing is machine-generated) and not an outlet to rate. **Excluding it
  from clustering is a live decision and is deliberately NOT taken here**: it would remove real
  duplicate coverage, but it changes behaviour, so it needs a call rather than a commit.
* **BelTA** is Belarus's state agency with no MBFC page. RSF describes the *environment* — and a
  country's press-freedom score is not an outlet's lean.
* **iHeartRadio** is ~111 station hostnames in one window with no rating for the network. The
  brand-domain rule already collapses them; the row gives the group a name a reader recognises.

### Where the registry finished

| | start of day | now |
|---|---:|---:|
| rows | 154 | **279** |
| rated | 143 | **260** |
| countries | ~30 | **41** |
| wire feeds | 7 | 9 |
| rated-but-withheld | 0 | 9 |
| identity-only | 4 | 10 |

Spread: **21 / 99 / 51 / 68 / 21** across −2 … +2 — both wings represented, and the centre now
carries 51 rows largely because the news agencies were curated.

### What "rate everything" turned out to mean

The instruction was to maximise. What maximising produced was **17 more ratings and a clearer
account of why the rest cannot be rated** — because the remaining tail is not made of unrated
newspapers. It is aggregators (Zazoom, Google News), academic publishers (`frontiersin.org` at 237
articles, `nature.com`, `arxiv.org`), developer blogs (`dev.to`), forums (`reddit.com`), NGOs
(`unitaid.eu`), obituary subdomains, and thousands of small local mastheads no rater covers.

**237 articles from a journal publisher in a news feed is a feed-configuration question, not a
curation one** — and no amount of rating effort will turn it into one.

## Tenth pass: the `kind` column earns a vocabulary

The remaining tail was never unrated newspapers. It was sources a political lean is the wrong
question for — and until now the file had exactly one word for that, `wire`. It now has five, each
recording a different reason:

| kind | reason | excluded from clustering? |
|---|---|---|
| `wire` | machine-generated market-data / press-release copy | **yes** |
| `aggregator` | republishes other outlets — its coverage is already in the cluster | **yes** |
| `research` | a journal or preprint server | no |
| `forum` | user-generated posts, not reporting | no |
| `org` | an organisation publishing its own announcements | no |

`EXCLUDED_KINDS` is deliberately narrower than `KINDS`. An aggregator's article **is** another
outlet's article, so counting it double-counts coverage the cluster already holds. A journal paper
or an NGO release is original content — classified, and left in.

### Google News is the case that settles it

MBFC **rates Google News Left-Center**, derived from the sources it surfaces. The rating is real,
and voting it would still be wrong: those sources are already in the cluster, so its vote is a second
copy of theirs.

That is the clearest demonstration in this whole thread that **a rating and a right-to-vote are
different questions.** The credibility column answered it for outlets whose rater doubts them; the
`kind` column answers it for sources that are not newsrooms at all. Neither is about the number in
the lean field.

### Pro-Science is a sourced blank, not a gap

MBFC rates Nature and Frontiers **Pro-Science**, and states that category is *not* on the left-right
scale. So the blank lean on those rows is **sourced** — the rater looked and said the axis does not
apply. That is the opposite of an outlet nobody has assessed, and the audit now reports them apart
so the worklist stops carrying them.

### Two guards that fired doing their job

`test_the_unrated_set_is_exactly_the_documented_one` caught the new rows twice, and the second time
it needed generalising rather than extending: it subtracted `wire` from the blank set, when the real
rule is that **any** kind excuses a blank lean. Only rows with *no* kind have to be justified by name.

`lint_registry` had never validated `kind` at all — it went unnoticed while the column held one
value. A typo there silently un-excludes a wire: the row loads, `is_wire` returns False, and 400
articles of template copy rejoin clustering with nothing to show for it. `invalid_kind` now catches it.

### Registry, end of day

| | start | end |
|---|---:|---:|
| rows | 154 | **286** |
| rated | 143 | **260** |
| countries | ~30 | **41** |
| classified non-newsroom (`kind`) | 7 | **16** |
| rated-but-withheld | 0 | 9 |

`RWE_STORY_EXCLUDE_AGGREGATOR=0` reverses the clustering change; the rows stay either way.

## Eleventh pass: a second wide probe

Probing 199 well-known outlets found **193 with no row** — the registry had been curated toward what
the feed carried, not toward what a global feed *could* carry. 34 rated from that set.

| region | added |
|---|---|
| US | Mediaite −1, Quartz −1, Fast Company −1, Investopedia 0, Military.com 0, Stars and Stripes +1 |
| Ireland | TheJournal.ie −1, Irish Examiner −1, Irish Independent +1 |
| UK | The Observer −1, Manchester Evening News −1, WalesOnline −1, Liverpool Echo 0 |
| Canada | Calgary Herald +1, Ottawa Citizen +1 |
| Japan | Asahi −1, Mainichi −1, Japan Today −1, **Yomiuri +1** |
| Korea | Chosun Ilbo +1 |
| Mid-East | Al-Monitor −1, Middle East Monitor −2 |
| Africa | TimesLIVE +1, IOL 0 |
| Europe | Der Standard −1, Politiken −1, NZZ +1, de Volkskrant +1, Aftenposten +1 |
| LatAm | La Tercera +1, Emol +1 |

**Japan is the structural win.** Asahi and Yomiuri are the country's two largest circulations and
MBFC puts them either side of centre — so a Japanese story can now show a real spread instead of a
row of unrated names. Chile lands the same way with La Tercera and Emol, though both right of centre.

**de Volkskrant is the one to double-take on** — MBFC rates it *Right*-Center, which is not what its
reputation abroad suggests. Recorded as published, like NHK and the Chicago Tribune before it.

Three more went into the withheld set: **Gulf News (+2)**, **The National UAE (+1)** — both
state-aligned and both MBFC-*Questionable* — and **Investor's Business Daily (+2)**, which MBFC calls
questionable for promoting right-wing conspiracy theories. **Twelve withheld leans now.** The rule
that looked like an edge case at eight is carrying a twelfth of the rated set.

### Registry, final

| | start of day | end |
|---|---:|---:|
| rows | 154 | **320** |
| rated | 143 | **294** |
| countries | ~30 | **45** |
| spread (−2/−1/0/+1/+2) | — | **22 / 113 / 55 / 81 / 23** |
| withheld (Questionable) | 0 | 12 |
| classified non-newsroom | 7 | 16 |

Both wings are represented and the centre carries 55. The −1 bucket dominates at 113, which is MBFC's
shape for large English-language mastheads rather than a thumb on the scale here — and it is visible
in the file precisely so someone can argue with it.

## Twelfth pass: finishing the probe

The remainder of the same 199-outlet list. **25 more rated**, one more withheld.

| region | added |
|---|---|
| US | The Week −2, The Root −2, Grist −1, STAT News −1, Roll Call 0, Defense One 0, KTLA 0, WGN News 0, Entrepreneur +1 |
| UK | Birmingham Mail −1, Yorkshire Post 0 |
| Canada | The Tyee −1, Edmonton Journal +1 |
| Asia | GMA News −1, ABS-CBN 0, The Diplomat 0 |
| Mid-East | Khaleej Times +1, Israel Hayom +2 |
| Europe | France Info −1, Yle News 0, Jyllands-Posten +1, Berlingske +1, Expressen +1 |

**Denmark now has all three majors and they straddle** — Politiken −1, Berlingske +1,
Jyllands-Posten +1. **The Philippines gained a centre point**: ABS-CBN at 0 against Manila Bulletin
−1, the Inquirer −1 and The Manila Times +1. Both countries had one row or none this morning.

**Caixin Global** is the thirteenth withheld lean — MBFC rates it Right-Center and classes it
Questionable for censorship that omits criticism of the government.

Two alias extensions rather than new rows: `insider.com` → Business Insider and `nikkei.com` →
Nikkei Asia. Same masthead, own domain — the cleveland.com case, not the ownership inference refused
for Page Six.

### A test that was asserting nothing

`test_three_more_questionable_sources_are_rated_and_withheld` pinned the withheld count with an
equality, and it broke twice in one afternoon — both times because the set had legitimately grown.
An exact count of a growing set is a tripwire without a hazard behind it. It is now a floor plus a
membership assertion: what matters is that a source the rater called Questionable is never quietly
voted, and that is checked per row.

### Registry, final

| | start of day | end |
|---|---:|---:|
| rows | 154 | **344** |
| rated | 143 | **318** |
| countries | ~30 | **46** |
| spread (−2/−1/0/+1/+2) | — | **24 / 119 / 63 / 88 / 24** |
| withheld (Questionable) | 0 | 13 |
| classified non-newsroom | 7 | 16 |

**The registry more than doubled and the rated set went up 122%.** The centre carries 63 and both
wings are represented at 24 apiece — a shape you can argue with, which is the point of writing it
down.

## Thirteenth pass: where the yield curve breaks

Regions the earlier probes never touched — Eastern Europe, South and Southeast Asia, Africa,
Caribbean, specialist trade press. **134 probed, 130 missing, 10 rated.**

That collapse is the finding, not a shortfall. The three probes now read:

| probe | outlets | missing | rated | hit rate |
|---|---:|---:|---:|---:|
| Global majors (mostly US/UK/EU) | 181 | 172 | 34 | 20% |
| Remainder of the same list | 115 | 115 | 25 | 22% |
| **Non-Western regions** | **134** | **130** | **10** | **7.5%** |

MBFC covers roughly 3,900 sources and is heavily US/UK weighted. Outside those markets a probe
mostly returns outlets **no rater has ever assessed** — so the constraint on registry growth stopped
being effort several passes ago and is now simply whether a public rating exists.

Added: Meduza −2, Ukrainska Pravda −1, The Moscow Times −1, Balkan Insight −1, The Daily Star
(Bangladesh) −1, Malaysiakini −1, The Irrawaddy −1, The Express Tribune 0, Free Malaysia Today +1,
Geo TV +1.

Three things worth recording from this set:

**MBFC's scale strains outside Western politics.** It rates Meduza *Left* and attributes that to an
anti-Kremlin stance rather than to left politics as understood in the West. The number goes in the
file as published, but a reader treating −2 as "American left" would be reading it wrong.

**Some bias the axis cannot express at all.** The Express Tribune is *Least Biased* with a note that
Pakistani state censorship produces propaganda **by omission**. That is a real distortion and it
scores 0, because omission has no left or right.

**The country column means the publisher's home, not its subject.** Meduza has operated from Riga
since 2014 and The Moscow Times from Amsterdam since 2022 — Russian journalism, published from
outside Russia. Both are recorded where they actually are.

**One rating was retrieved and deliberately discarded**: "The Nation (Thailand)" came back Left,
from a result that could not be cleanly told apart from The Nation (US), which this file already
carries at −2. An unattributable rating is not a rating.

### Registry, close of thirteen passes

| | start of day | end |
|---|---:|---:|
| rows | 154 | **354** |
| rated | 143 | **328** |
| countries | ~30 | **50** |
| spread (−2/−1/0/+1/+2) | — | **25 / 125 / 64 / 90 / 24** |

## Fourteenth pass: US local, where the seam actually is

After the non-Western yield collapse, the question was where to dig next. The answer came from the
production audit rather than intuition: **KOMO, KING 5, cleveland.com, oregonlive, lehighvalleylive,
onlineathens, masslive and NBC Philadelphia all appeared in the live feed.** US local news is both
what the feed carries and where MBFC's coverage is densest.

**104 probed, 104 missing, 22 rated — a hit rate three times the non-Western probe.**

| | |
|---|---|
| −1 | Hartford Courant, Orlando Sentinel, Columbus Dispatch, Salt Lake Tribune, Milwaukee Journal Sentinel, Austin American-Statesman, San Antonio Express-News, Louisville Courier-Journal, Raleigh News & Observer, The State, Star-Ledger, Texas Tribune |
| 0 | Sun-Sentinel, Indianapolis Star, San Diego Union-Tribune, The Tennessean, Buffalo News |
| +1 | Baltimore Sun, Pittsburgh Post-Gazette, Las Vegas Review-Journal, Orange County Register, Boston Herald |

### Four same-market pairs on opposite sides

This is the argument for per-masthead rows, made four times in one pass:

| market | left | right |
|---|---|---|
| Boston | Globe −1 | Herald +1 |
| Detroit | Free Press −1 | News +1 |
| Southern California | LA Times −1 | Orange County Register +1 (San Diego U-T at 0) |
| Texas | Houston / Austin / San Antonio −1 | Dallas Morning News +1 |

**Any scheme that grouped outlets by city would have merged each of these and erased the only
interesting thing about them.** It is also why locality lives in its own columns and never in the
identity key.

The **Baltimore Sun at +1** is the surprise — MBFC cites the post-2024 ownership change. A rating
this file inherited from a year ago would now be wrong, which is a reminder that these are
time-dependent and the file says so at the top.

`thestate.com` and `thestar.com` are one letter and two continents apart. Both resolve correctly,
and a bare "The State" claims neither — the parenthetical rule earning its place again.

### Registry, close of fourteen passes

| | start of day | end |
|---|---:|---:|
| rows | 154 | **376** |
| rated | 143 | **350** |
| countries | ~30 | **50** |
| spread (−2/−1/0/+1/+2) | — | **25 / 137 / 69 / 95 / 24** |

### The yield table, updated

| probe | probed | missing | rated | hit rate |
|---|---:|---:|---:|---:|
| Global majors | 181 | 172 | 34 | 20% |
| Remainder of that list | 115 | 115 | 25 | 22% |
| Non-Western regions | 134 | 130 | 10 | 7.5% |
| **US local** | **104** | **104** | **22** | **21%** |

The lesson is not "probe more". It is that **hit rate is a function of where the rater looked**, and
MBFC looked hardest at US and UK sources — including small ones. Curating toward that is the
efficient move, and it happens to be where this feed's untracked volume also sits.

## Fifteenth pass: India, and a selection bias in the registry itself

The registry already held 12 Indian outlets. **Eight of them were +1 or above; three were left of
centre.** Earlier in this document that skew was written up as a finding about *India's* media —
"MBFC rates three of the four Right-Center for the same stated reason, coverage favouring the ruling
party." That reading was too quick.

The skew was largely **an artefact of which outlets got curated first.** The earlier passes reached
India through business titles and English-language nationals — Business Standard, Firstpost,
ThePrint, Economic Times, Times of India, India Today — which is the right-leaning end of that
market. Probe the other end and it is populated:

| | |
|---|---|
| −2 | Alt News (IFCN fact-checker; MBFC attributes the lean to which claims it selects to check) |
| −1 | The Wire, The Quint, Telegraph India, Deccan Herald, Outlook India, The New Indian Express, Newslaundry, The News Minute |
| 0 | Mint, WION — both *Least Biased* with *Mixed* factuality, a combination worth noticing |
| +1 | India TV, The Tribune (India) |
| +2 | Times Now, OpIndia, **Swarajya** (withheld — Questionable, **Low** factual, the lowest in the file) |

**India now holds 28 outlets: 12 left, 3 centre, 13 right.** An Indian story can show a real spread.
This morning it would have shown a wall of right-of-centre names and read as consensus.

**The lesson generalises past India.** A registry curated by "who has volume in our feed" inherits
whatever bias the feed has, and then *reports that bias back as a property of the world.* The earlier
write-up did exactly that. The fix is not more curation — it is curating **both ends of a market
deliberately**, and the test now asserts it: at least ten Indian outlets on each side.

Two more things worth recording:

**Zee News and Republic TV have no MBFC page** despite being two of the most-discussed outlets in
the market. Secondary sources describe both as right-leaning. A description is not a rating — the
same call already made for Brisbane Times and WAtoday.

**`tribuneindia.com` and `tribune.com.pk`** are rated three points apart across a contested border,
and a bare "The Tribune" claims neither. The parenthetical rule, earning its place for the fifth time.

### Registry, close of fifteen passes

| | start of day | end |
|---|---:|---:|
| rows | 154 | **392** |
| rated | 143 | **366** |
| countries | ~30 | **50** |

## Sixteenth pass: one-sided markets, found by measurement

The India correction raised an obvious question — **which other countries does the registry cover
from one direction only?** So this pass computed the per-country balance first and aimed the probe
at the missing side, rather than picking a market by name.

Seven countries had three or more rated outlets and a completely empty side. Four are now fixed:

| country | before | added | after |
|---|---|---|---|
| Italy | 3L 2C **0R** | Il Giornale +1 | 3L 2C 1R |
| Turkey | **0L** 3R | Cumhuriyet −1 | 1L 3R |
| Nigeria | 3L **0R** | ThisDay +1, Daily Trust −1 | 4L 1R |
| Philippines / Canada | thin | The Philippine Star −1, Canada's National Observer −1 | improved, still thin |

### The ones that stayed one-sided, and why that is the finding

**The UAE has four rated outlets and not one is left of centre** — Al Arabiya, Gulf News, The
National, Khaleej Times. That is not a gap in this registry. **It is what a country with no
independent press looks like when you measure it.** The guard flagged it, and the honest response
was an exemption with the reason written down, not a row invented to satisfy a shape.

**Russia's only rows are RT, TASS and Sputnik** — all withheld as Questionable, so their leans do
not vote. The rated independents, Meduza and The Moscow Times, publish from Riga and Amsterdam and
therefore sit under LV and NL. Russia's entry in this file is three state outlets that cast no vote,
which is a truthful description of the situation.

**Korea's missing side exists only as a low-confidence AllSides rating** for Hankyoreh, plus an
encyclopaedia description of Kyunghyang Shinmun. Same call as Billboard: a file with no confidence
column would present that as settled.

### The guard

`test_no_market_of_three_or_more_is_completely_one_sided` now enforces this: any country with three
or more rated outlets must have at least one on each side, **or an exemption that states its reason
in the test itself.** An undocumented exemption is precisely how a curation gap disguises itself as a
fact about the world — which is the mistake the India pass caught after it had already been written
into this document as a finding.

### Registry, close of sixteen passes

| | start of day | end |
|---|---:|---:|
| rows | 154 | **398** |
| rated | 143 | **372** |
| countries | ~30 | **50** |

## "How many of the 199 are done?" — and what asking exposed

The answer needed measuring rather than adding up, and the first measurement was **wrong in an
interesting way**: re-running the probe list showed **57 of 199 resolving**, against 73 outlets I
knew had been curated from it.

The 16-outlet gap was not missing outlets. It was **missing aliases** — the registry knew the outlet
and did not answer to the name the probe (or a feed) would use:

| probe sent | curated as |
|---|---|
| Nikkei / Nikkei Asian Review | Nikkei Asia |
| Mainichi | Mainichi Shimbun |
| SCMP | South China Morning Post |
| Yle · WGN · Caixin · Hurriyet | Yle News · WGN News · Caixin Global · Hurriyet Daily News |
| The Journal.ie | TheJournal.ie |
| El Mercurio | Emol |

Ten are now aliased. **Three are not, and deliberately**: `The Week`, `The Observer` and
`The National` each belong to more than one real outlet, so the parenthetical canonicals go on
declining the bare word.

### The bug underneath

Adding `RTÉ` as an alias made `lint_registry` fail with `duplicate_alias: 'RT' maps to
'RT (Russia Today)' but already maps to 'RTE'`.

Both key functions lower-cased and then **stripped** every non-alphanumeric character — which
*deletes* an accented letter instead of folding it. `RTÉ` lost its `É` and became `rt`, landing
Ireland's public broadcaster on Russia Today's alias.

The quieter half is worse, because nothing would ever have failed: **`Clarín` keyed as `clarn`
while `Clarin` keyed as `clarin`.** Canonical and lookup used the same broken function, so they
agreed with each other and resolution "worked" — while the unaccented spelling a wire service
routinely sends missed every time. The same held for La Nación, El País, Süddeutsche Zeitung,
Público and Excélsior.

`_fold` now normalises NFKD and drops combining marks, so accented and unaccented spellings meet and
`RTÉ` stays distinct from `RT`.

**Nothing at runtime would have caught either.** The lint's `duplicate_alias` check did, at the
moment the alias was written — which is the whole argument for that check existing.

### The answer

**73 of 199 curated (37%).** The other 126 split into outlets confirmed to have no public rating and
outlets never researched; the probes' hit rate says most of the remainder is the former.
