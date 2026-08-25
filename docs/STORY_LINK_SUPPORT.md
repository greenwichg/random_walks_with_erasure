# Adapting the seeded-clustering architecture: support breadth + the entity veto

Two changes, one lineage. Both come from US11663254B2 (*System and engine for seeded clustering
of news events*, Conrad and Bender, Thomson Reuters), and both are cluster-level merge rules that
cannot be expressed as a pairwise test. The mapping of the patent's four ideas onto this codebase,
and what each one turned out to need, is in **Lineage** below.

## Part 1 — merge support breadth

**Status: MEASURED 2026-08-25 and REJECTED at 2.** `RWE_CLUSTER_MIN_SUPPORT` stays `1`. The rule,
the harness flag and the exhibit remain; the default does not move without a passing
counterfactual. Numbers and mechanism in *The measurement* below.

## The symptom

A Guardian article — *The Odyssey* becoming Nolan's highest-grossing film — served inside the
Spider-Man *Brand New Day* box-office story. Reported from a story-page screenshot, then
production-probed edge by edge:

| edge | j | shared tokens |
|---|---|---|
| Odyssey ↔ Spider-Man (direct) | **0.000** | — (none) |
| Odyssey ↔ round-up | 0.312 | becomes, film, grossing, highest, odyssey |
| round-up ↔ Spider-Man | 0.286 | box, fourth, man, office, spider, tops |

The round-up is a real comparative headline covering both films: *"'Spider-Man' tops box office in
fourth weekend; 'The Odyssey' becomes Nolan's highest-grossing film."*

## Why every previous lever was the wrong shape

This is **not** the template/boilerplate class, and treating it as one would have been a category
error:

* **No lexicon can fire.** Both bridge edges are carried by real film names and real box-office
  vocabulary. There is nothing here that is boilerplate, nothing to stop-list, and stop-listing
  the film names would destroy the two genuine stories. The edges are *correct*; the two events
  simply have no edge to each other.
* **The quorum is blind to it.** `link_quorum` measures what FRACTION of cross-pairs pass. The
  round-up shares six real tokens with the Spider-Man side, so it wins several cross-pairs
  honestly and the fraction is satisfied — while every one of those passing pairs runs through
  the same single article.
* **Raising the quorum is already spent.** 0.3 cost 3.0% of covered articles and raised the
  bad-cluster count; 0.4 cost 5.6%, over the bar. The reason is structural: a long-running
  story's coverage legitimately diverges, so the passing fraction falls exactly where clusters
  are largest. The knob cannot be pushed without deleting good coverage.
* **The semantic judge would catch it, and is dark.** The banded `event_identity` judge would
  likely veto the comparative edges per rubric rule 7 — but it needs an API key the deployment
  does not have. This fix is deterministic, free, and complementary; the judge remains the
  answer for pairs no structural rule can reach.

The defect is in the linkage **graph**, not in the vocabulary: two dense components joined through
a single article. So the fix is structural.

## The rule

> A merge requires **support breadth**: the passing cross-pairs must involve at least
> `min_support` **distinct members on each side**, capped at what each side can supply
> (`min(min_support, |side|)`).

`2` says exactly what `GEO_MIN_CONSENSUS` already says about event geography — one witness is an
anecdote, two is corroboration. Implemented in `clustering._link_ok`, which evaluates the quorum
and the breadth in one cross-pair scan (both criteria ANDed, both early-aborting).

**Why breadth succeeds where the fraction failed.** Breadth has no size coupling. A 60-article
story has many distinct members participating however low the passing fraction runs; a bridge weld
has exactly one, at any cluster size. The two rules are orthogonal and neither subsumes the other.

**Why it cannot disturb correct clustering** — *the argument the measurement refuted; kept as
written, because the correction is the point.* The cap keeps the rule off story *formation*: two
singletons have one member each to offer, so their requirement is 1 and the founding pair
satisfies it. Growth still works — a joining article must simply resemble `min_support` distinct
members rather than one. And the rule can only ever *refuse* a merge, never create one, so every
cluster under it is a **subset** of some cluster without it (pinned by test). The reachable
outcomes are a split or no change, never a reshuffle.

The last two sentences held. The first did not: "growth still works" quietly assumed a joining
article resembles several members, and 8.7% of covered articles say otherwise. See *The
measurement — REJECT*.

Both merge orders are covered. Whether the round-up reaches the Odyssey article first or the
Spider-Man story first, the side that has grown past one member cannot be annexed through a single
participant — pinned by test in both directions, because merges are consumed best-first and either
order is reachable in production.

## Lineage — the Thomson Reuters seeded-clustering architecture

Adapted from US11663254B2, *System and engine for seeded clustering of news events* (Conrad and
Bender, Thomson Reuters). Four ideas were taken; the patent-specific components were not.

| Patent idea | What we already had | What this change adds |
|---|---|---|
| Candidate data set → **initial clusters** → **aggregate clusters** as separate stages | `clustering.cluster` forms groups; `_merge_duplicates` / `_merge_by_entities` are the aggregate stage; `_repair` re-splits between them | unchanged — the staging was already ours |
| Merge on evidence from **two independent sources** (text signature + named-entity tags) | the hook architecture — but see Part 2: only the GEO channel could ever refuse a merge | **X5c**, the entity channel spent in the veto direction |
| **Avoid single weak pairwise links** joining unrelated material | `link_quorum` — but it measures a fraction, which a bridge satisfies | **support breadth**, the rule that actually names the bridge |
| Merge decisions at the **cluster level**, not the article-pair level | `merge_ok`, `link_quorum`, `_merge_duplicates` cluster profiles | breadth is a property of the *merge*, not of any pair — it cannot be expressed pairwise at all |

### The aggregate stage, audited rather than assumed

The patent's third stage merges *initial clusters* into *aggregate clusters* agglomeratively.
`_merge_duplicates` is our equivalent and it is stronger than agglomerative: it requires
**complete linkage** — `all(score(a, b) >= min_sim for a in gi for b in gj)` — so no chain can
form there at all, which is the same defect support breadth addresses in `cluster()`. Guards
present on that pass, verified rather than asserted:

| guard | present |
|---|---|
| complete linkage (no chains) | yes |
| size cap | yes |
| time-gap cap | yes |
| coherence-floor veto | yes |
| geo-disagreement veto | yes |
| entity-disagreement veto | **added by X5c** |
| best-first determinism | yes |

Six of seven were already there. The seventh is Part 2, and its absence was the same asymmetry
described below — the aggregate stage could hear geography's objection and not entities'.

**Deliberately not taken:** the patent's editorially-supplied "seed" documents and topical labels
(that is curation, and the seeded/sub-topic hierarchy is a product model we do not have); its
digital-signature duplicate detection (our `_merge_duplicates` already occupies that role); and
the Calais entity tagger (X6 Phase 0 measured our entity coverage at 24% — the channel is an
extraction problem here, not a design one).

**Support breadth is not applied to `_merge_duplicates` / X5b**, on the same reasoning as the
template gate: those passes compare cluster profiles rather than pairwise edges, so breadth over
cross-pairs has nothing to measure there — and complete linkage already forbids the chain it would
be guarding against. X5c *is* applied there, because an entity disagreement is a statement about
two clusters and reads identically at either decision point.

## The measurement — REJECT

Live catalog, 27,856 articles, full production stack:

| | before | after (support 2) |
|---|---|---|
| stories | 1,499 | 1,550 |
| largest cluster | 60 | 56 |
| covered articles | 6,122 | **5,607** |
| **droppedOut** | — | **534 = 8.7%** (bar 5%) |
| clusters split | — | 371 |
| blindspot claims | 203 | **149** |
| independent signal | 0/63 bad, mean 0.953 | 0/51 bad, mean 0.967 |

The direction was right — largest cluster down, story count up — and the cost was two-thirds over
the bar. **The reasoning this refutes is the one that justified the rule.** Part 1 argued that "a
genuine new article resembles several members of the cluster it joins." On the real catalog that
is false often enough to matter: coverage of a running story diverges in vocabulary, so a
legitimate late article routinely matches exactly ONE member — the one phrased like it. The
dropped list is the receipt and it is not template chaff: Harry/Meghan lost 5 of 60, the England
v Pakistan Test live blog 6, the Diamondbacks/Ketel Marte story 6 across 4 publishers. Requiring
breadth of the RECEIVING side taxes precisely the growth that makes a story a story.

Two further readings worth keeping. The improved independent signal (0/63 → 0/51 bad) is **cost
presenting as quality** — the scored set shrank because clusters left it. And the
`odyssey-spiderman` exhibit read `separated → separated` on both sides: the weld had aged out of
the window, so this run priced the rule without ever exercising the defect it was built for. The
8.7% is measured; the benefit is not.

### The registered follow-up: `--support-scope groups`

Every article in that 8.7% is a **singleton joining a cluster**. `groups` scope asks for breadth
only when BOTH sides already have ≥ 2 members, exempting that case entirely and keeping the
requirement for the merge of two established groups — which is the shape the bridge weld actually
has. Corroboration is demanded when two bodies of coverage claim to be one event, not when one
article claims to belong.

It is **weaker on purpose, and the weakness is pinned by test**. Which merge order a bridge takes
depends on where its strongest edge points, and merges are consumed best-first:

* **Order A** (production's: bridge↔Odyssey 0.312 > bridge↔Spider-Man 0.286) — the bridge lands on
  the foreign side first, so the remaining merge is group-to-group. `groups` refuses it, exactly
  as `any` does.
* **Order B** (bridge's strongest edge points at the large side) — it joins as an unGated
  singleton and the foreign article follows as one too. `any` refuses; `groups` does not.

**Measured 2026-08-25 — 1.8% dropped, adoption HELD.** 27,885 articles, baseline already
carrying the adopted X5c veto:

| | before | after (support 2 / groups) |
|---|---|---|
| stories | 1,508 | 1,553 |
| largest cluster | 60 | **60 — unchanged** |
| covered articles | 6,135 | 6,044 |
| **droppedOut** | — | **113 = 1.8%** (bar 5%) |
| clusters split | — | 106 (vs 371 under `any`) |
| blindspot claims | 200 | 192 |
| independent signal | 0/63 bad, mean 0.953 | 0/59 bad, mean 0.956 |

The scope does what it was designed to do — four-fifths of the `any` variant's cost, gone. The
harness printed ADOPT and the decision is still held, because **that verdict line is a cost check
and the criterion is not**. The bar registered on `link_quorum` reads "largest cluster well down,
droppedOut ≤ 5%, no story-count fall", and the largest cluster did not move. The
`odyssey-spiderman` exhibit read `separated → separated` on both sides for a second run, so the
weld was again absent from the window and the benefit is unobserved. That is 113 articles and 8
blindspot claims spent on something no instrument in the run can see. The precedent is X6, in
`STORY_TEMPLATE_GATE.md`: *a printed PASS overruled by the criterion as registered.*

What settles it is the split READ rather than another aggregate — `--pieces N` prints the pieces
of the biggest split clusters, which is the read that tells a separated event from a shredded
story. The dropped list divides visibly:

* **plausibly correct** — one outlet repeating a template: "The Shards" next-episode (6 of 6,
  a/p 3.0), "First Alert Weather", "Fantasy football rankings" (both a/p 2.0);
* **plausibly damage** — "US hits Canadian goods with 50% tariffs" (−2 of 11 across 10
  publishers, a/p 1.1), "At least five killed in Russian missile strikes on Kyiv" (−2 of 8
  across 7).

Which of those dominates the 106 is the adoption decision.

One number worth explaining rather than glossing: the run reports **12 clusters merged** under a
rule that can only refuse merges. Both are true. Refusal is subtractive inside `cluster()` — the
subset property is pinned by test at that layer — but the aggregate `_merge_duplicates` and the
repair pass then receive *different inputs*, and a merge those passes previously declined on a
size cap or a profile can now succeed. End-to-end containment was never claimed; only linkage
containment is.

## Measuring it

```bash
cd /opt/ih && source deploy/ops/_compose.sh
dc run --rm -T api python examples/audit_clustering_change.py --min-support 2 --show 20
dc run --rm -T api python examples/audit_clustering_change.py \
    --min-support 2 --support-scope groups --show 20
```

Bars, registered in advance and unchanged from `link_quorum`:

* **adopt** — droppedOut ≤ 5% of covered articles, no story-count fall, bad-cluster count not up
* **reject** — droppedOut > 10%, or the total story count falls (the `min_publishers` cliff:
  splitting a 4-article/2-publisher cluster into 2+2 can leave two single-publisher fragments,
  both dropped, so oversplitting deletes stories rather than merely shrinking them)

The run must come from a container carrying the deploy environment, or the `[PRODUCTION BASELINE]`
tag is fiction — `RWE_CLUSTER_MIN_SUPPORT` is in the harness's env-presence allowlist for exactly
that check.

## Rollback

`RWE_CLUSTER_MIN_SUPPORT=1` in `deploy/.env` (or removing the compose default) restores pre-rule
clustering exactly. No data was migrated and no stored row changed.


---

# Part 2 — X5c, the entity-disagreement veto

**Status: MEASURED 2026-08-25 and ADOPTED.** `RWE_STORY_ENTITY_VETO` is a compose default of `1`
(`0` is the kill switch). Numbers in *The measurement* below.

## The asymmetry

The patent's central merge claim is that decisions come from **two distinct evidence sources** —
a digital signature over unstructured text, and named-entity tags from a separate tagger. We had
two channels and were spending only one of them in both directions:

| channel | can PROPOSE a merge | can REFUSE a merge |
|---|---|---|
| text (tokens, profiles) | yes — it is the primary signal | n/a |
| geography | no | **yes** — `_geo_closures` growth veto, and the located-consensus block in `_merge_duplicates` |
| entities | yes — X5b `_merge_by_entities` | **no — nothing existed** |

So a text-similarity merge in any domain without event geography had no independent second
opinion at all. That is not a marginal set: entertainment, business and sport are exactly where
every weld in this repo's record lives, and `clusterTrust` is honestly *unknown* for them because
located consensus never exists — which is also why the repair pass can never trigger there.

## The rule

> Refuse a cluster merge iff **both** sides carry a corroborated entity consensus and those
> consensuses share **no** name.

Deliberately the geo veto's rule over the other channel, including its corroboration standard: a
consensus is a non-noise name carried by ≥ 2 members (`_story_entity_consensus`, already written
and measured for X5b), because one member's testimony is a sample of one. Every other state fails
open — one side unextracted, one side uncorroborated, any overlap at all.

Applied at **both** cluster-level decision points: the build-time merge gate (composed onto the
same `merge_ok` hook as the geo veto, via `_and_merge_ok`) and the aggregate dup-merge. The repair
re-cluster receives the mapping too, for the `article_tokens` reason — a pass that links on a
different rule than the primary build re-splits on the disagreement rather than on a defect.

`evidence` is always `None` for this closure, and that is a statement about the rule rather than
an omission: a consensus needs two corroborating members, so the test has nothing to say about a
single pair. **It is cluster-level by construction** — which is precisely the patent's shape.

## Why the coverage objection does not carry over

X6 Phase 0 killed entities as an edge-*admission* channel on coverage: 24% of articles carry
extracted entities, so a pairwise entity test is blind most of the time. Two things make the
cluster-level veto a different proposition.

* **Coverage aggregates.** The question is whether a *cluster* has extraction, not whether an
  article does — and the odds improve with exactly the cluster size where a weld does damage.
* **The direction is safe.** Absence of evidence fails open, so the uncovered majority is left
  untouched rather than mis-served. A recall rule with 24% coverage is crippled; a veto with 24%
  coverage is simply quiet.

And the same Phase 0 run is the receipt that the signal works where it exists: in the Mirzapur
weld, the two articles that carried entities shared **zero** names — discriminating perfectly on
the pair the lexical route needed a whole lexicon to reach.

## What it cannot do

Only a veto is offered. Entity evidence *proposing* merges is X5b's job, is measured separately,
and stays where it is. This knob cannot create a cluster, only decline one — so, like support
breadth, every story under it is a subset of some story without it, and the reachable outcomes are
a split or no change. Pinned by test.

## The measurement — ADOPT

Live catalog, 27,876 articles, full production stack:

| | before | after (entity-veto) |
|---|---|---|
| stories | 1,501 | 1,502 |
| largest cluster | 60 | 60 |
| covered articles | 6,127 | 6,127 |
| **droppedOut** | — | **0 = 0.0%** |
| clusters split | — | 1 |
| blindspot claims | 202 | 202 |
| independent signal | 0/63 bad, mean 0.953 | 0/63 bad, mean 0.953 |

One cluster moved: a 15-article/11-publisher Trump–Iran economic announcement resolved into two,
and **no article left a story**, so both halves cleared `min_articles`/`min_publishers`. That is
the shape a veto should have at this coverage — quiet where extraction is absent, decisive where
two clusters genuinely name different people. Nothing else in the catalog noticed.

## Measuring it

```bash
cd /opt/ih && source deploy/ops/_compose.sh
dc run --rm -T api python examples/audit_clustering_change.py --entity-veto --show 20
```

The run now prints an `X5c telemetry` line — merges checked, how many had consensus on both
sides, how many were vetoed, and the dup-merge count separately — so the two decision points can
be read apart from the geo veto's numbers on the line above.

Same bars as everything else in this file. The run reports `entityMergeVetoed` /
`dupMergeEntityVetoed` telemetry so the two decision points can be read separately. It requires a
backfilled `article_entities` table — the same one X5b already depends on, kept current by the
GKG entity cycle — and `_entities_for` now pays for the query when *either* consumer is enabled.

## Rollback

`RWE_STORY_ENTITY_VETO=0`. No data was migrated and no stored row changed.
