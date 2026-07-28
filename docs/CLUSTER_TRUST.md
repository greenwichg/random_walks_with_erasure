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
