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
| `ok` | fewer than `MIN_CHAINABLE` (3) members — structurally cannot be a chain — or a score ≥ floor |
| `low` | scored, and below `DEFAULT_COHERENCE_FLOOR` (0.7): the located members disagree |
| `unverified` | no score at all, above `DEFAULT_UNVERIFIED_SIZE` (50) members |

`low` and `unverified` are treated differently on purpose: **evidence of a problem reorders the
feed; absence of evidence only withholds claims.**

### 2. The blindspot gate (hard blocker — the reason for the review)

`blindspotSide` is emitted only when trust is `ok`. `blindspotWithheld` records what was suppressed
so the audit can count it rather than guess. A blindspot is a statement about the world; we do not
make one from a cluster our own instrument contradicts.

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

### 5. Coverage-list batching

`CoverageList` renders 40 rows then "Load more". The largest cluster mounted 318 rows, each with a
Read and a Save button.

## Thresholds

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

| monitor | now | trigger |
|---|---|---|
| largest cluster ÷ p90 story size | 318 ÷ 7 = **45×** | **60×** |
| largest cluster share of covered articles | ~2% | **8%** |

Hitting either promotes the linkage work above whatever else is queued, with no re-litigation. The
reason this needs a pre-committed trigger rather than a judgement call later is the growth curve:
the largest cluster went **194 → 208 → 318 (+64%)** while the corpus grew **+23%**. It is
superlinear, so it degrades without any change from us.

## Configuration

| variable | default | effect |
|---|---|---|
| `RWE_STORY_COHERENCE_FLOOR` | `0.7` | geoCoherence below which a cluster is `low` |
| `RWE_STORY_UNVERIFIED_SIZE` | `50` | size above which having no score is notable |
| `RWE_STORY_TRUST_RANKING` | on | `0` restores pure size ordering |
| `RWE_CLUSTER_LINK_QUORUM` | `0.0` | cross-pair fraction required to merge (`0` = single linkage) |

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
