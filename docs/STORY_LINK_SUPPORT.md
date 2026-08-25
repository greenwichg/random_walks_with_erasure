# Merge support breadth — the comparative-bridge weld, and the cluster-level fix

**Status: implemented, registered, NOT yet adopted.** `RWE_CLUSTER_MIN_SUPPORT` defaults to `1`
(off, byte-identical) in `deploy/docker-compose.yml`. It becomes a compose default only when
`audit_clustering_change.py --min-support 2` clears the bars registered on
`story_service.link_quorum` — the same discipline that adopted the template gate and REJECTED
hyphen compounds and derived boilerplate.

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

**Why it cannot disturb correct clustering.** The cap keeps the rule off story *formation*: two
singletons have one member each to offer, so their requirement is 1 and the founding pair
satisfies it. Growth still works — a joining article must simply resemble `min_support` distinct
members rather than one. And the rule can only ever *refuse* a merge, never create one, so every
cluster under it is a **subset** of some cluster without it (pinned by test). The reachable
outcomes are a split or no change, never a reshuffle.

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
| Merge on evidence from **two independent sources** (text signature + named-entity tags) | the `evidence` / `merge_ok` hooks: geo veto (X4), entity merge (X5b), template gate, banded judge | unchanged — the hook architecture was already ours |
| **Avoid single weak pairwise links** joining unrelated material | `link_quorum` — but it measures a fraction, which a bridge satisfies | **support breadth**, the rule that actually names the bridge |
| Merge decisions at the **cluster level**, not the article-pair level | `merge_ok`, `link_quorum`, `_merge_duplicates` cluster profiles | breadth is a property of the *merge*, not of any pair — it cannot be expressed pairwise at all |

**Deliberately not taken:** the patent's editorially-supplied "seed" documents and topical labels
(that is curation, and the seeded/sub-topic hierarchy is a product model we do not have); its
digital-signature duplicate detection (our `_merge_duplicates` already occupies that role); and
the Calais entity tagger (X6 Phase 0 measured our entity coverage at 24% — the channel is an
extraction problem here, not a design one).

**Not applied to `_merge_duplicates` / X5b**, on the same reasoning as the template gate: those
passes compare cluster profiles rather than pairwise edges, so they are a different mechanism with
their own evidence rules.

## Measuring it

```bash
cd /opt/ih && source deploy/ops/_compose.sh
dc run --rm -T api python examples/audit_clustering_change.py --min-support 2 --show 20
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
