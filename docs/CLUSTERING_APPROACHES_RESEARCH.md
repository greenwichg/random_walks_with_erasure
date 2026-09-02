# News-event clustering: the field's approaches, measured against Hidden View's record

**Status: research and recommendation only. No system change.** This document compares the
clustering approaches used in industry and the literature against what Hidden View runs and
what it has measured, and recommends where the next accuracy gain is. Every figure about Hidden
View is **[M] measured on production** (with its source document named), **[D] derived from
measured values**, or **[P] projected with the assumption stated** — the convention
`SCALE_ROADMAP.md` and `capacity_report.py` use. Figures about external systems come from their
published papers or patents; where a claim rests on my reading of a paper rather than a number
in it, it says so. References are listed at the end; they are from memory of the literature and
should be checked against the originals before being cited outside this repository.

The question the user asked is the right one and it has a precise form here: **which algorithms
would improve accuracy** — fewer false merges (two events served as one story) and fewer false
splits (one event served as several) — **on this catalog, under this box's constraints, without
losing the properties the product depends on** (determinism, stable story ids, an audit harness
that can price every change before it ships).

---

## 0 · The answer in one page

Hidden View's clustering is **not** a naive lexical clusterer, and the approaches that beat naive
lexical clustering in the literature are largely things it already has. It runs a two-stage
seeded/agglomerative pipeline (initial clusters → aggregate merge, the Thomson Reuters shape),
with cluster-level linkage instead of single linkage, three independent evidence channels
(template lexicon, event geography, extracted entities) each spent as a veto, an entity
corroboration channel for recall, a coherence-based trust verdict, and a banded LLM same-event
judge that is built, benchmarked and dark. Its record shows what is left:

| failure class | share of the problem today | what fixes it |
|---|---|---|
| **F1 chaining** — transitive closure welding unrelated events | largely closed: largest cluster 787 → 64 [M], deep chains −93% [M] | done (quorum 0.2, repair, vetoes) |
| **F2 template welds** — headlines sharing format words, not event words | closed for the registered lexicons [M]; **open for instance-numbered templates** ("Day 2" vs "Day 21", "package 0" vs "package 5") because pure digits are dropped from tokens | **number/ordinal anchors** (deterministic, zero deps) |
| **F3 bridge articles** — a round-up genuinely about two events | open; support breadth priced at 8.7% coverage and rejected [M]; entity veto reaches ~6% of merges [M] | the **LLM judge** (built, dark) and **graph-level bridge tests** |
| **F4 false splits** — one event in disjoint vocabulary; and one event in two languages | open and **the largest remaining loss**: the Seattle shooting in four pieces at title Jaccard 0.15 [M]; ru/ar/ko at 0.0% story participation vs 27.9% English before the fallback tokenizer [M], and only ar 9 / ja 13 / ko 4 / ru 2 in-story articles after it [M] — the tokenizer was the blocker, corpus density per language is the constraint underneath | **embeddings as a second channel** (never as sole evidence); the Unicode fallback tokenizer is already live |
| **F5 same actor, different event** | residual, < 0.1% of covered [M] | judge only; no structural rule reaches it |

So the strongest approach for Hidden View is not a replacement algorithm. It is an
**evidence-weighted hybrid** built on the pipeline that exists: keep the deterministic
candidate generation, the quorum linkage and the veto channels; **replace the single
title-Jaccard threshold with a calibrated pairwise score over several signals** — lexical
overlap, distinctive-token overlap, instance anchors (numbers, dates, places), entity overlap,
time gap, and a sentence-embedding cosine once that dependency is accepted; use embeddings
where the record says the loss is (the aggregate merge pass, and cross-lingual joining); and
turn on the banded judge for the residue. Each step is measurable with the harness the repo
already has. Density clustering (DBSCAN/HDBSCAN) and pure embedding clustering are unsuitable
here for reasons the catalog's own shape makes unavoidable (§2.6, §2.4). Section 4 gives the
staged plan.

---

## 1 · What Hidden View runs today, and what it has measured

Every approach below is judged against this baseline, so it is stated precisely.

### 1.1 The pipeline (`examples/clustering.py`, `examples/story_service.py`)

| stage | rule | source |
|---|---|---|
| representation | headline content tokens: lowercase `[a-z0-9]+`, length > 2, **pure digits dropped**, two stop-lists (function words; calendar/editorial filler). Optional dek tokens (first 12, off). Unicode/bigram tokenizer in **fallback** mode — **on since 2026-08-28** (fires only when ASCII yields < 3 tokens; replace mode measured and rejected: 78 rescued for 149 lost). *Correction 2026-09-02: an earlier draft of this row called the fallback "unmeasured"; it was measured ADOPT and is live, see `story_service.unicode_words`.* | `clustering.title_tokens` |
| candidate generation | inverted token postings — only pairs sharing ≥ 1 token are scored; exact, not approximate | `clustering.cluster` |
| pairwise gate | plain Jaccard ≥ 0.28 **and** ≥ 3 shared tokens **and** ≥ 3 tokens each side **and** within a 6-day window; IDF weighting built, **off** (measured −10.5% coverage) | `pair_admits` |
| evidence vetoes on the edge | template gate: an edge must share ≥ 1 token outside the registered lexicons (announce, tracker, preview, recall) — **on**; geo veto on cluster growth — **on**; banded LLM judge verdicts — built, **off** (`RWE_EVENT_JUDGE=0`) | `_template_closure`, `_geo_closures`, `_event_identity_closure` |
| linkage | best-first merges with a **cross-pair quorum of 0.2** (single linkage is the library default; production opts in) — a merge needs 20% of sampled cross-pairs to pass the gate independently | `_link_ok`, `RWE_CLUSTER_LINK_QUORUM` |
| cluster-level vetoes | corroborated geo consensus disagreement; corroborated entity consensus disagreement (X5c) — both **on**, both fail open on absence | `merge_ok` hook |
| repair | clusters the independent geo signal condemns (coherence < 0.7) are re-split at quorum 0.5 — **on** | `_repair`, `RWE_STORY_REPAIR_QUORUM` |
| aggregate merge | complete-linkage merge of cluster **profiles** (every member's headline + dek, IDF-weighted) at 0.33, gap ≤ 48 h, size ≤ 130, geo/entity veto — **on** | `_merge_duplicates`, `RWE_STORY_MERGE_SIM` |
| entity-corroborated merge | clusters sharing ≥ 2 corroborated non-noise entity names join (X5b) — **on** | `_merge_by_entities`, `RWE_STORY_ENTITY_MERGE` |
| admission | ≥ 2 articles, ≥ 2 distinct publishers, ≥ 3 rated outlets; wire and aggregator sources excluded by curated kind | `build_stories` |
| trust and ranking | `geoCoherence` ≥ 0.7 → `ok`, else `low`/`unverified`; demoted in ranking; blindspot claim withheld unless `ok` | `_cluster_trust` |
| identity | id anchored to the earliest member's canonical URL; `story_member` persistence fixed 5.1%/day id churn | `_story_id`, `CLUSTER_TRUST.md` |
| serving | **full deterministic rebuild** every poll cycle (600 s) over the 6-day window; verdict store for judge outputs; no incremental state | `warm_cache` |

### 1.2 The measured state [M]

| measure | value | source |
|---|---|---|
| clustering window | 27,809–29,152 articles, 6 days | `SCALE_ROADMAP.md`, `M14_LANGUAGE_DENSITY_DESIGN.md` |
| stories | 1,499–1,815 | `STORY_LINK_SUPPORT.md`, quorum verification |
| articles in a story | 23.6% of the window (6,887 / 29,152) | M14 §3 |
| story size | p50 = 2, p90 = 7 | `CLUSTER_TRUST.md` |
| largest cluster | 60–67 (was 204 pre-quorum, 787 under single linkage) | quorum verification, entity plan §1 |
| chain depth ≥ 5 | 29 → 2 on identical rows (−93%) | quorum verification §2 |
| loose members (< 20% support) | 1,520 → 182 (−88%) | quorum verification §2 |
| independently-scored bad clusters | 4 of 87–96 (≈ 4–5%), mean coherence 0.93 | same |
| build time | 5.1 s at 22,493 rows; 8.5 s at ~27.8k; **n^2.15** in clustering, 76% of the build | `PERFORMANCE.md`, `SCALE_ROADMAP.md` |
| cost driver | the ten most frequent tokens = 86.4% of candidate-walk work | `PERFORMANCE.md` |
| entity coverage | 24% of articles carry any extracted person/org (GDELT GKG) | X6 Phase 0 |
| event-geography coverage | 18.7% of articles located | X4 |
| language participation | en 27.9%; de 19.0%; fr 12.7%; es 5.6%; **ru 0.0%, ar 0.0%, ko 0.0%, ja 0.7%** (2026-08-27, before the Unicode fallback; after it ar 0→9, ja 1→13, ko 0→4, ru 0→2 in-story articles — 79 of 2,653 structurally-excluded articles reached, 3.0%) | M14 §3; `story_service.unicode_words` |
| judge triage band | ~451 new pairs/day; the lexical layer auto-decides 78% of labeled pairs with 0/60 errors | `event_identity.py` |
| box | t3.medium, 2 vCPU, 4 GiB; image carries numpy only, no torch/sklearn; judge is stdlib HTTP | `CAPACITY_AND_COST.md`, `Dockerfile.api` |

### 1.3 The measured dead ends [M] — so they are not re-proposed

| lever | result | where |
|---|---|---|
| raise the Jaccard threshold or quorum | 0.3 cost 3.0% coverage and raised bad clusters; 0.4 cost 5.6% | entity plan §1 |
| IDF-weighted Jaccard on titles | −10.5% of covered articles; reverted | `story_service.use_idf` |
| corpus-derived boilerplate (df + day-spread) | −17.0%: no distributional statistic separates "frequent because template" from "frequent because important" | `derived_boilerplate_on` |
| Unicode tokenizer, replace mode | rescued 78 articles, cost 149 already in stories; reached 3.0% of the excluded population | `title_tokens` |
| merge support breadth (each side ≥ 2 distinct supporters) | −8.7% coverage; legitimate late articles match exactly one member | `STORY_LINK_SUPPORT.md` |
| articles-per-publisher gate | precision 0%, recall 0%; then rejected again post-quorum (misses the broad-syndication shape) | `CONTENT_MILL_STORY_EVALUATION.md` |
| per-publisher cap | trims 110–310 real articles of rolling coverage per window | same |
| hyphen-compound tokens | 121 clusters split, −2.6% coverage; union growth | `title_tokens` |
| pairwise geo-disjointness veto | dissolves legitimate multi-country stories (Europe heat records, Eurovision) | X4 run D |

The pattern across all of them: every lever that adjusts *how much lexical similarity is enough*
fails the same way, because boilerplate overlap between unrelated events does not stop being
overlap at any threshold, and vocabulary divergence inside a real story does not stop being
divergence. The entity plan states the transferable principle: **transitive closure is safe when
the edge predicate is close to an equivalence relation and dangerous when it is a graded
threshold.** The remaining lever is what the edge is made of.

### 1.4 A fresh exhibit from today's dev seed

While seeding a development catalog for unrelated UI work, six synthetic Politics headlines of
the form *"Senate advances budget package {N} after late-night vote"* (N = 0…5, three publishers
each) clustered into **one** story. The tokens are identical once the digit is dropped, so title
Jaccard is ≈ 0.8 across instances and every gate passes. Synthetic, but it is exactly rubric
rule 6 ("numbers and ordinals are identity anchors when they name the instance") and the
recorded production class behind it: box-office *Day 2* vs *Day 21*, earnings by quarter, results
by match day. The digit drop is a documented trade (`title_tokens`: a shared year linking two
listicles is commoner than a lost "737"); the trade is right for **similarity** and wrong for
**identity**, which is the distinction §2.8 builds on.

---

## 2 · The approaches

Each subsection answers the same questions: how it works; how it handles false merges; how it
handles false splits; strengths; weaknesses; scalability; suitability for Hidden View; verdict.

### 2.1 Lexical similarity: TF-IDF cosine and BM25

**How it works.** Represent each article as a term-weight vector (TF-IDF, or BM25's saturated
term frequency with document-length normalisation), score pairs by cosine, cluster by threshold.
This is the Topic Detection and Tracking (TDT) baseline from 1998 onward: single-pass on-line
event detection compares each incoming document to existing cluster centroids and opens a new
cluster when the best match is under a threshold (Allan, Papka & Lavrenko 1998; Yang, Pierce &
Carbonell 1998). Later systems add source-specific term statistics and time decay (Brants, Chen
& Farahat 2003).

**False merges.** IDF down-weights everywhere-words, which is the naive fix for boilerplate
welds. Hidden View measured this exact idea and reverted it: on titles, IDF cost 10.5% of
covered articles, and the corpus-derived boilerplate variant cost 17%. The reason generalises to
TF-IDF/BM25: a template word ("cast", "release", "box office") is *frequent* in a window because
many articles use the format, but so is "earthquake" during an earthquake week — the statistic
cannot tell the two apart. BM25's length normalisation helps a little with long deks but is
irrelevant for headlines.

**False splits.** Cosine over title + dek is more forgiving than Jaccard over titles, because the
dek adds who/what vocabulary. Hidden View already has that lever (`desc_tokens`) and measured
the trade: dek tokens help paraphrases (8–10 shared, sim 0.35–0.48) but help templates **more**
(10–15 shared, 0.69–0.88), so lengthening the lexical signal cannot fix splits without opening
merges. The aggregate merge pass already runs IDF-weighted profiles over title + dek at 0.33
with complete linkage, which is the safe place for this signal.

**Strengths.** Zero dependencies; deterministic; interpretable (a merge can be printed as its
shared tokens, which is how every exhibit in this repo was diagnosed); linear-ish with blocking.

**Weaknesses.** Vocabulary-bound: synonyms, paraphrase and translation are invisible; format
vocabulary is indistinguishable from event vocabulary; digits and short tokens are lost.

**Scalability.** With postings blocking, cost is Σ df² over tokens — Hidden View's measured
n^2.15, dominated by ten tokens. TF-IDF changes weights, not the candidate set, so it does not
change this curve.

**Suitability.** Already in place in the two forms that measured well (Jaccard for admission,
IDF profiles for the merge). Swapping Jaccard for cosine would be a re-tuning exercise with no
new information in the edge.

**Verdict: no further gain as a standalone lever.** Keep as the first feature of the hybrid
score (§2.8), where it remains the cheapest and most explainable signal.

### 2.2 Seeded and agglomerative clustering

**How it works.** Hierarchical agglomerative clustering (HAC) starts from singletons and merges
the closest pair of clusters repeatedly; the linkage rule (single, complete, average, centroid)
defines "closest" between clusters. "Seeded" clustering — the Thomson Reuters patent
(US 11,663,254 B2, Conrad & Bender) — forms *initial* clusters from a candidate set, then
merges them into *aggregate* clusters using evidence from two independent sources (a text
signature and named-entity tags), with merge decisions taken at the cluster level.

**False merges.** This is where linkage matters. Single linkage merges A and C whenever A~B
and B~C — the chaining that built Hidden View's 787-article blob. Complete linkage requires
every cross-pair to pass and cannot chain; average linkage sits between. Hidden View's
production linkage is a **quorum** (a fraction of cross-pairs) consumed best-first, which is a
constrained agglomeration — it removed 88% of loose members and 93% of deep chains on identical
rows [M], at a 5.5% coverage cost, half of it content-mill deflation. The aggregate merge pass
is full complete linkage. So the linkage lever has been pulled to where the record says the
cost/benefit turns: raising the quorum to 0.3 or 0.4 measured worse.

**False splits.** Complete and average linkage fragment long-running stories whose coverage
diverges in vocabulary: the quorum verification recorded the Fauci saga breaking into beats and
a bounded ~2% over-splitting inventory. Support breadth (a stricter cluster-level rule)
measured −8.7% for the same reason: a legitimate late article routinely matches exactly one
existing member.

**Strengths.** Deterministic given an order; explicit control over the merge criterion; the
two-stage shape lets the expensive, precise evidence run at the cluster level where it is
cheap (Hidden View's geo and entity vetoes are exactly this).

**Weaknesses.** O(n²) in the naive form (28k articles → 390M pairs); threshold-sensitive; every
linkage rule trades one failure class for the other.

**Scalability.** Only viable with candidate blocking (Hidden View's postings index) and
sampling on cluster-level tests (`LINK_SAMPLE = 32`). At the Tier A budget of 83,000 articles
[D] the build is projected at ~60 s, which the roadmap accepts.

**Suitability.** Already the architecture. The Reuters comparison in `STORY_LINK_SUPPORT.md`
found six of the patent's seven merge guards present before X5c added the seventh.

**Verdict: adopted in substance; no algorithmic change recommended.** What remains is the
quality of the *evidence* the linkage consumes.

### 2.3 Entity-based clustering

**How it works.** Extract named entities (persons, organisations, places, dates, numbers) and
use entity overlap either as the similarity itself, as features alongside text, or as
constraints (two clusters naming disjoint actors must not merge). Event Registry (Leban et al.
2014) clusters multilingual news using Wikipedia-concept annotations, which also gives
cross-lingual linking for free; Rupnik et al. (2016) extend this to cross-lingual event
tracking; the Reuters patent uses entity tags as the second evidence source.

**False merges.** Entities are the natural answer to template welds: two "cast/date/release"
articles about different shows share no actor, no title, no studio. Hidden View measured this
on its own data: in the Mirzapur weld, the two articles that carried entities shared **zero**
names — perfect discrimination on the pair the lexical route needed a whole lexicon to reach —
and the X5c cluster-level veto adopted at **zero coverage cost** [M]. The ceilings are also
measured: **coverage** (24% of articles carry entities, so ~6% of merges have both sides
extracted); **ubiquity** (Trump is in both court cases; USGS attends every earthquake);
**granularity** (two US events both read {US} at country level).

**False splits.** Entity corroboration is a recall channel too: X5b joins clusters sharing ≥ 2
corroborated names, adopted with 44 joins and zero drops [M] — the Mangione court family, the
Farage stories. It cannot help where extraction is absent, which is most of the catalog.

**Strengths.** Precise on *who* and *where*; interpretable; cross-lingual when entities are
linked to a knowledge base (Wikidata QIDs make a Japanese and an English report share
"Garmin"); cheap to compare once extracted.

**Weaknesses.** Extraction coverage and quality decide everything; disambiguation (which
"Minnesota"?); same-actor-different-event is invisible to entities by construction (the
residual class in X5b's hand-read); *what* and *when* are not entities.

**Scalability.** Extraction cost is per article at ingest, not per pair at build — the right
shape for this pipeline. A small spaCy model runs at roughly 5–15 ms per short text on one CPU
core [P]; at ~5,000 new articles/day that is under two minutes of CPU per day. Comparison at
build time is set intersection.

**Suitability.** Very high — the channel exists, the vetoes and corroboration rules exist and
are measured, and **the binding constraint is coverage, not design**. Raising coverage from 24%
toward 90% is the single change that would make the existing entity rules fire on the merges
they currently cannot see. Two routes, in order of dependency weight: (a) a **deterministic
proper-noun and instance-anchor extractor** in stdlib — capitalised spans not at sentence start,
plus numbers, ordinals, dates and place names from the existing location resolver — which gets
much of the English value with no new dependency; (b) a real NER model at ingest (spaCy
`en_core_web_sm` plus multilingual models, or LLM extraction through the existing stdlib HTTP
adapter at roughly $1–2/day at Haiku pricing for ~5k articles [P]), which is the route to
cross-lingual entity linking.

**Verdict: the highest-value structured signal to expand.** Not as the sole similarity — the
ubiquity and same-actor ceilings are real — but as the channel whose coverage gap is currently
the most expensive thing in the pipeline.

### 2.4 Embeddings and semantic similarity

**How it works.** Encode title (+ dek) with a sentence-embedding model (SBERT-family: Reimers &
Gurevych 2019; multilingual distillations such as paraphrase-multilingual-MiniLM or the E5
family), score pairs by cosine, cluster by threshold or with agglomerative/density methods.
Modern news-stream clustering combines dense and sparse signals (Staykovski et al. 2019;
Miranda et al. 2018 learn a weighted combination of TF-IDF, entity and time features); the
SemEval-2022 news-similarity task showed multilingual encoders scoring cross-lingual article
pairs well.

**False merges.** This is where embeddings are **worse** than tokens, and it matters
specifically for Hidden View's recorded failure classes. An encoder measures topical and
phrasal similarity; "Trump wins Ohio" and "Trump wins Iowa" embed almost identically, as do "Day
2" and "Day 21" of two films, or two daily gold-price reports. Every template class Hidden View
has fought is *semantically* near-identical by construction — the template is the meaning the
encoder sees. A pure embedding threshold would re-open F2 at scale. Embeddings therefore must
never be the **sole** evidence for an edge; they need identity anchors (numbers, places,
entities, dates) beside them — which is the hybrid scorer.

**False splits.** This is where embeddings are strongest, and it is Hidden View's largest
remaining loss. "Mass shooting reported at Seattle Center" and "gunfire erupts near Seattle"
share one token and can never merge lexically at any threshold; an encoder scores them high.
The four Seattle pieces already score 0.56 on the merge pass's richer profiles versus 0.15 on
titles [M] — a dense representation extends that reach to the pairs whose deks also diverge.
Cross-lingually, a multilingual encoder is the only approach in this list that can join a
Korean and an English report of one event **without a tokenizer fix and without entity
linking**, and Group A languages currently sit at 0.0% participation [M].

**Strengths.** Paraphrase and translation invariance; one representation for every language;
cheap to compare (one dot product); can be computed **at ingest** and stored, so build-time
cost is unchanged; sits naturally inside the aggregate merge pass, which already runs complete
linkage and vetoes.

**Weaknesses.** Conflates topic with event; opaque (a merge cannot be printed as its shared
evidence, which the repo's whole diagnostic method depends on — mitigated by keeping the lexical
and entity signals beside it); a real dependency: PyTorch is ~700 MB on CPU, ONNX Runtime with
a quantised MiniLM is ~50 MB of runtime plus a 25–120 MB model [P]; models drift, so verdicts
must be versioned (the judge's `prompt_version` discipline applies); no vendor embeddings API
is available through the existing Anthropic adapter, so the choice is local ONNX or a second
vendor.

**Scalability.** Encoding is per article at ingest: MiniLM-class models run at roughly
100–300 short texts per second on two vCPUs [P], so 5,000 new articles/day is well under a
minute of CPU, and a one-off backfill of a 28k window is a few minutes. Storage is 28k × 384
floats ≈ 43 MB [D]. Pairwise scoring must be **blocked**, not brute force: 28k² cosines is
~3 × 10¹¹ multiply-adds per build [D], too much for a 600 s cycle on this box, but scoring
only the pairs the lexical/entity/time blocking proposes (plus a bounded cross-lingual
candidate set found by approximate nearest neighbour on the small non-English slice) keeps it
in the noise.

**Suitability.** High as a **second channel**, unsuitable as the primary clusterer. The
adoption bar is the same counterfactual harness every gate went through, plus the golden
pairs' kill condition (one `same_event` on a labeled-different exhibit disqualifies).

**Verdict: adopt in stages, never alone.** First inside the aggregate merge pass (complete
linkage, size cap and vetoes already guard it; this is where F4 lives), then as a feature in
the pairwise scorer, then as the cross-lingual bridge for the languages nothing else reaches.

### 2.5 Graph-based clustering

**How it works.** Build the article similarity graph (which Hidden View already does
implicitly: nodes are articles, edges are pairs passing the gate) and partition it with a
community-detection method — Louvain (Blondel et al. 2008), Leiden (Traag, Waltman & van Eck
2019), label propagation, Chinese Whispers — instead of taking connected components.
Modularity-based methods reward dense internal structure and cut sparse bridges.

**False merges.** This is the family that speaks directly to the **bridge class** (F3): a
round-up article genuinely similar to two dense components is exactly the sparse cut community
detection makes, and it makes it without the coverage tax support breadth paid, because it
measures the *global* density on each side rather than demanding that every joining article
resemble several members. Hidden View's quorum is a local, pairwise-sampled approximation of
the same idea.

**False splits.** Modularity has a resolution limit: small communities get absorbed into
neighbours in large graphs, and a resolution parameter trades that against fragmenting large
stories. On a graph whose median component has two nodes, most stories are below any
resolution's radar and would pass through unchanged — which is fine — while the 60-article
stories are where the parameter bites both ways.

**Strengths.** Targets the one open false-merge class; well-understood; Leiden is fast
(near-linear in edges) and guarantees connected communities.

**Weaknesses.** **Non-deterministic by default** (random node order, tie-breaking) — Hidden
View requires same-input-same-output for story ids, the audit harness and the verdict store, so
any implementation must fix node order and seeds and be pinned by test; a compiled dependency
(igraph/leidenalg) or a slower pure-Python one; a new parameter to titrate; the partition is
global, so one change anywhere can move an unrelated community (the path-dependence the
roadmap flags for incremental clustering).

**Scalability.** Hidden View's graph is small: tens of thousands of nodes, and only ~19,000
edges passed the gate in a 26,565-article window [M]. Leiden on that is sub-second.

**Suitability.** Medium. The bridge class is real but small: the entity veto now catches the
fraction where both sides carry entities, the judge is designed to catch the rest, and support
breadth's measurement shows how easily a bridge rule taxes legitimate growth. A deterministic
Leiden pass **restricted to components above a size floor** (say ≥ 8 members, where a bridge
can do damage and where the quorum's sample of 32 starts to blur) is the shape worth measuring.
A cheaper deterministic cousin exists inside the current code: run the quorum test on the two
sides *with the candidate bridge article removed* — if the components fall apart without it,
it is a bridge. That needs no dependency and can be measured with the existing harness first.

**Verdict: worth one measured experiment on large components only, deterministic or not at all;**
not a primary clusterer.

### 2.6 DBSCAN and HDBSCAN

**How it works.** Density-based clustering: DBSCAN grows clusters from core points that have
at least `minPts` neighbours within `eps`; HDBSCAN (Campello, Moulavi & Sander 2013) builds the
hierarchy over all densities and extracts the most stable clusters, labelling sparse points
as noise. BERTopic pairs HDBSCAN with embeddings and UMAP for topic discovery.

**False merges.** DBSCAN's density-reachability **is chaining**: with small `minPts` it is
single linkage within `eps`, the defect Hidden View just removed. HDBSCAN's stability
extraction resists chaining better but still joins components through dense bridges.

**False splits.** Both methods declare low-density regions noise. Hidden View's story-size
distribution is p50 = 2, p90 = 7 [M]: **the median story is a single pair**, which no density
method with `minPts ≥ 2` will ever call a cluster, and even `minPts = 2` (which reduces DBSCAN
to threshold linkage) treats every 2-article event as marginal. A method whose default outcome
for the majority of true events is "noise" is mis-shaped for this catalog regardless of the
representation under it.

**Strengths.** No cluster count needed; handles arbitrary shapes; HDBSCAN is robust across
densities and useful for **exploration** — e.g. finding substructure inside a suspicious
mega-cluster, which Hidden View's repair pass already does with a targeted quorum.

**Weaknesses.** Noise handling is the wrong default here; `eps` on cosine space is as brittle
as any threshold; not incremental; compiled dependencies (scikit-learn, hdbscan); non-trivial
memory for the mutual-reachability graph at 28k points.

**Scalability.** Roughly O(n log n) with spatial indexes on low-dimensional data, but on
384-dimensional embeddings the neighbour search dominates and it is materially slower than the
blocked postings walk at this size.

**Suitability.** Low as the primary clusterer, for the size-distribution reason above; possible
as an offline diagnostic for the largest components, which has no production value beyond what
`_repair` and `audit_story_duplicates.py` provide.

**Verdict: not suitable for Hidden View.**

### 2.7 Temporal event clustering

**How it works.** Time is treated as evidence, not only as a window: similarity decays with
the gap between publication times (Yang et al. 1998 add time-decay to TDT detection; Brants et
al. 2003 use it explicitly), bursts of matching vocabulary mark new events (Kleinberg 2002),
and event threading orders sub-events within a topic (Nallapati et al. 2004). Streaming
systems (Miranda et al. 2018) feed the time gap into the learned pairwise model.

**False merges.** A decaying threshold separates instances of a **recurring series**: yesterday's
daily column resembles today's, and a hard 6-day window lets a five-day chain of them weld
(the WWE daily and obituary chains the quorum titration recorded — "the blobs are themselves
sparse chains"). Rubric rule 3 (different days of a recurring series are different events) is
partly a temporal rule. Time cannot separate same-day template instances (election results by
state), which need place and number anchors.

**False splits.** Decay penalises long sagas exactly where the quorum already fragments them
(the Fauci beats). The merge pass's 48-hour gap cap is already a hard temporal split rule; a
soft decay is gentler than that cap and could replace it.

**Strengths.** Zero dependencies; deterministic; incremental-friendly; cheap; principled (an
event is an occurrence at a time).

**Weaknesses.** A tunable curve; interacts with the publication-time quality Hidden View has
had to defend (future-dated clamps, delayed aggregators — see `FRESHNESS_*` docs); rolling
coverage of one event across a week is legitimate and common.

**Scalability.** Free.

**Suitability.** High as a **modifier** inside the pairwise gate — an age-scaled requirement
rather than a hard window — and as a feature of the hybrid score. Low risk; measurable in an
afternoon with the harness.

**Verdict: adopt as a feature/modifier, not as a clusterer.**

### 2.8 Hybrid approaches: evidence-weighted scoring, learned pairwise models and an LLM verifier

**How it works.** The field's stronger systems do not pick one representation; they combine
several signals in a pairwise decision and keep the clustering machinery simple. Miranda et al.
(2018) — the system behind Priberam's multilingual news clustering, which won the
Multilingual News Stream shared task — computes per-pair features (TF-IDF similarity on
several fields, entity overlap, timestamp difference) and learns their weights with a linear
SVM on labeled pairs; clustering is then a single pass with that learned score. Event Registry
combines concepts, entities and text. The Reuters patent merges on two independent evidence
sources at the cluster level. And the current frontier for the ambiguous residue is a **model
that answers the same-event question directly** for a pair — which Hidden View has built: a
rubric-driven, quote-verified, fail-closed, veto-only judge that runs out of band over a
triage band of ~451 pairs/day and stores verdicts once.

**False merges.** A calibrated score can demand **corroboration across channels** — "high
lexical overlap AND no conflicting instance anchor AND no disjoint corroborated entities" —
which is exactly what the template, geo and entity vetoes do today as binary rules. Learning
the weights lets the system express what the record shows: lexical overlap is strong evidence
when it includes distinctive tokens and weak when it is format vocabulary; a number
disagreement on a shared template is decisive (rule 6); a place disagreement is decisive
(rule 7). The judge handles the residue no structural rule reaches (bridges, same-actor
different-event).

**False splits.** The same score can accept pairs a single threshold rejects — moderate
lexical overlap **plus** shared entities **plus** embedding agreement **plus** a same-day
timestamp — which is the Seattle case, and the cross-lingual case once embeddings or linked
entities exist.

**Strengths.** Uses every signal the pipeline already computes; **explainable** (a linear model
over named features prints as its evidence, the diagnostic property this repo depends on);
**deterministic** given the features; trainable on the labeled data that exists (the V1 golden
pairs, with provenance tiers, plus the 17 ratified exhibits as the kill set); numpy is enough
for logistic regression; each new signal is one feature and one measured change.

**Weaknesses.** Labeled data is small (hundreds of pairs, most of them rule-labelled rather
than human-labelled, and the benchmark set is deliberately hard); overfitting to exhibits is a
real risk, so regularisation and the counterfactual harness (not the training set) must be the
adoption bar; a learned score is one more thing that can drift when the catalog's mix changes;
the LLM judge costs money and needs a key the deployment does not yet carry.

**Scalability.** The scorer replaces one Jaccard comparison per candidate pair with a
ten-feature dot product — no change to the candidate set or the n^2.15 curve. The judge is
bounded by its band and its budget (`RWE_EVENT_JUDGE_BUDGET`), roughly $0.5–1 per day at Haiku
pricing for ~450 verdicts [P], and each pair is judged once.

**Suitability.** This is the approach that fits every constraint at once: it keeps
determinism and ids, keeps the harness, adds no dependency until embeddings are chosen, and
spends new signals where the record says the losses are.

**Verdict: recommended global approach** — see §4.

---

## 3 · Comparison matrix

| approach | false merges | false splits | deterministic | incremental-friendly | multilingual | new deps | fit for Hidden View | verdict |
|---|---|---|---|---|---|---|---|---|
| TF-IDF / BM25 cosine | weak (template = frequent) | modest | yes | yes | no | none | already have the measured-good forms | keep as a feature |
| seeded / agglomerative (quorum, complete linkage) | strong vs chaining | fragments sagas | yes (best-first) | partly | n/a | none | **is the architecture** | keep |
| entity-based | strong where extracted (0-cost veto) | recall via corroboration | yes | yes | yes with linking | NER model or LLM extraction | high; coverage is the gap | **expand coverage** |
| embeddings | **weak** (template = meaning) | **strong** (paraphrase, translation) | yes | yes (encode at ingest) | **yes** | ONNX + model | high as 2nd channel only | **adopt in stages** |
| graph community detection | strong vs bridges | resolution limit | only if seeded/ordered | no (global) | n/a | igraph or pure-Python | medium, large components only | one measured experiment |
| DBSCAN / HDBSCAN | chaining (DBSCAN) / bridges | **declares 2-article events noise** | mostly | no | via embeddings | sklearn/hdbscan | low: p50 story = 2 | not suitable |
| temporal decay / bursts | separates series instances | penalises long sagas | yes | yes | n/a | none | high as modifier | adopt as feature |
| hybrid scorer + LLM judge | strong (corroboration across channels; judge for residue) | strong (accepts multi-signal pairs) | yes | yes | with embeddings/entities | numpy; API key for judge | **highest** | **recommended** |

---

## 4 · Recommendation: an evidence-weighted hybrid, staged so every step is measured

The principle, from the record: **change what the edge is made of, not how much of one thing
is enough.** Keep the pipeline shape — blocked candidates, quorum linkage, complete-linkage
aggregate merge, veto channels, trust verdict, full deterministic rebuild — and upgrade the
pairwise decision from a single title-Jaccard threshold to a calibrated score over independent
signals, adding the two signals the catalog lacks (dense semantics, broad entity coverage) where
the losses are. Each stage has a pre-registered bar and uses the existing harness
(`audit_clustering_change.py --pieces`, the golden pairs with their kill condition, and the
independent coherence signal); a stage that fails its bar stops there, as every adoption in
this repo has.

### Stage 0 — no new dependency (deterministic, days each)

**Status 2026-09-02, after the first production measurements.** Items 1–4 were built, tested
and shipped OFF behind their own knobs; item 5 turned out to be already live. Then three of
them met the live catalog (45k-article window, full production stack):

| item | result | verdict |
|---|---|---|
| 1 · instance-anchor veto | 0.3% dropped, 182 edges vetoed, series separated correctly — but stories 2,410 → 2,405 and two welds manufactured downstream | **REJECTED** as registered; audit instrument only |
| 2 · time decay 0.02 | 7.5% dropped against the 1% bar, 437 splits, the sagas fragmented and re-welded | **REJECTED**; not to be titrated |
| 3 · entity spans | coverage 17.1% → 65.2% (English 64.9%); X5c consulted-with-consensus 8.8% → 23.2%; 0.6% dropped; stories 2,421 → 2,422; largest 86 → 79; bad clusters 1 → 0; cross-language joins (Messi en/vi, Tupac trial en/nl) | **ADOPTED** on the second run, after an extractor precision fix; compose defaults both switches on |
| 4 · the judge | harness arm and audit flag built; the V1 gate is the operator's run | **DEFERRED 2026-09-02 on cost**, not on merit |

The full records are on `story_service.anchor_veto`, `time_decay` and `entity_spans`. Two
lessons the run added to the record: refusal inside `cluster()` frees singletons that the
remaining template edges absorb (the anchor veto's two welds — the containment mechanism
`support_scope` documented, now seen a third time), and a requirement that grows with the
publication gap is backwards for exactly the coverage that spans days. Each item below keeps
what was built, the knob, the measurement command and the bar fixed before any production
number. Every production run is from a container carrying the deploy environment (`cd /opt/ih
&& source deploy/ops/_compose.sh`, then `dc run --rm -T api …`).

1. **Instance anchors as a veto** — `clustering.instance_anchors` /
   `story_service.anchor_veto`, knob `RWE_CLUSTER_ANCHOR_VETO` (off). Explicit calendar dates
   and enumerated series slots (week/gameweek, matchday, game, episode, season, part, chapter,
   leg, Test/ODI/T20I, volume/issue/edition, compact Q1–Q4 and S2E5) are read *separately from*
   the similarity tokens, so the digit-drop trade stands; an edge, a build-time merge, an
   aggregate profile merge and an entity merge are all refused when the two sides carry the same
   slot with no value in common. Deliberately **not** anchored: `day` (rule 2 — the
   `batwara-days` exhibit is one film's day 2 and day 3), rounds/laps/halves/the word quarter
   (updates of one occurrence), years (context inside a six-day window) and bare counts (rule 6's
   second clause). Places were dropped from the design: no gazetteer exists in the repo, and
   country-level geography is already the X4 veto.
   Measure: `dc run --rm -T api python examples/audit_clustering_change.py --anchor-veto
   --pieces 8`. Bar: `batwara-days` stays together and `batwara-vishwanath` stays separated;
   droppedOut ≤ 1%; no story-count fall; the pieces read shows series instances separating
   (Wordle/Connections dates, gameweeks, fiscal quarters), never one event shredding.
2. **Time decay inside the gate** — `clustering.required_sim` / `story_service.time_decay`, knob
   `RWE_CLUSTER_TIME_DECAY` (0 = off). The requirement becomes `sim + decay × gap_days` inside
   the unchanged six-day window, threaded through the one `pair_admits` rule to admission,
   quorum cross-pairs and the repair re-cluster. The merge pass's 48-hour cap is left alone —
   one variable per measurement.
   Measure: `… audit_clustering_change.py --time-decay 0.02 --pieces 8`. Bar: recurring-series
   chains separate in the pieces read; the Fauci-class sagas do not fragment further than
   today; droppedOut ≤ 1%; no story-count fall. Titrate 0.01 / 0.02 / 0.04 if 0.02 misses.
3. **Stdlib entity spans at ingest** — `entity_spans.extract` (capitalised multi-word spans,
   connectors allowed, Title Case headlines and noun-capitalising languages skipped), written
   as `span`-kind rows under their own source; two knobs on purpose: `RWE_INGEST_ENTITY_SPANS`
   writes, `RWE_STORY_ENTITY_SPANS` (`story_service.entity_spans`) reads. The store returns
   spans only to a caller that names the kind, so every existing consumer is byte-identical
   while the table fills. Anchors are not stored — they are recomputed from the headline at
   build time (item 1).
   Measure, in order: `… entity_span_backfill.py --show 12` (prints provider coverage vs
   coverage with spans, overall and for English — the 24% → 70%+ bar), then
   `… audit_clustering_change.py --entity-spans --pieces 8`. Bar: X5c's
   consulted-with-consensus share rises materially above 6.2%; droppedOut ≤ 1%; bad clusters do
   not rise; largest cluster within noise; exhibits unmoved; every span-driven X5b join in the
   pieces read is a duplicate family, never a same-name weld.
4. **The banded judge under its pre-registered bars** — `event_identity` is built and dark.
   What Stage 0 added: the V1 harness now takes `--adapter claude`, running the **production**
   adapter and prompt (`event_identity.ClaudeAdapter`, model `RWE_EVENT_JUDGE_MODEL`) through
   the same V1a–V1d scoring the Gemini arm ran, and the clustering audit takes
   `--event-verdicts` so the judge's persisted vetoes can be priced as a counterfactual. The
   gate is unchanged: one `same_event` on a labeled-different exhibit disqualifies. Needs
   `ANTHROPIC_API_KEY` in `deploy/.env` (set by the operator; never pasted anywhere).
   Runbook: (a) emit and label the sheet — `… audit_verifier_band.py --emit-pairs
   /app/data/v1_pairs.jsonl` then `… audit_v1_labelset.py --pairs /app/data/v1_pairs.jsonl
   --out /app/data/v1_labeled.jsonl`; (b) the gate on the production adapter —
   `… audit_v1_verifier.py --adapter claude --pairs /app/data/v1_labeled.jsonl --out
   /app/data/v1_claude.jsonl`; (c) only on SCREENING PASS with no KILL line, set
   `RWE_EVENT_JUDGE=1` in `deploy/.env`, `dc up -d api`, let the worker drain the band for a
   day (`dc logs api | grep event_judge`), then (d) `… audit_clustering_change.py
   --event-verdicts --pieces 8` — droppedOut ≤ 1%, no story-count fall, the `--pieces` read
   showing bridge round-ups and same-template-different-referent pairs separating.
5. **Unicode tokenizer in fallback mode** — *already adopted 2026-08-28* (79
   structurally-excluded articles reached a story, 0 lost, 0 splits, 0 merges; ar 0→9, ja
   1→13, ko 0→4, ru 0→2) and live via `deploy/.env`. What Stage 0 changed: it is now the
   **compose default** (`RWE_CLUSTER_UNICODE_WORDS: fallback`), the same lost-env-file
   discipline as the quorum/veto/template knobs; `0` is the kill switch. The remaining Group A
   loss is corpus density, not tokenization — Stage 1's cross-lingual bridge is the next lever.

### Stage 1 — one dependency decision: sentence embeddings at ingest

**Gate first (built 2026-09-02): `examples/preflight_embeddings.py`.** The first dependency ever
added to the serving image lands on a 2-vCPU / 4 GiB box, so the decision is measured on that
box rather than estimated: a throwaway `pip --target` install of ONNX Runtime + tokenizers, one
candidate encoder downloaded (the box's CPU flags pick the quantised file), the session loaded
with memory read before and after, 500 real window headlines encoded at one thread, and the
two semantic properties Stage 1 spends embeddings on checked on fixed exhibits. Bars,
registered in the script before any number: image growth ≤ 500 MB, resident ≤ 400 MB,
MemAvailable with the model loaded ≥ 1 GiB, ≤ 50 ms/article, paraphrase margin ≥ 0.15,
cross-lingual cosine ≥ 0.5. The template-trap cosine (eye drops vs fruit bars) is printed and
deliberately not barred: it reads high, and that number is the standing reason embeddings are
a second channel and never sole evidence. Run: `dc run --rm -T api python
examples/preflight_embeddings.py --install` (exit 0 GO, 2 NO-GO, 1 undetermined; nothing is
changed either way).

Choose a small multilingual encoder run through ONNX Runtime (a quantised
paraphrase-multilingual-MiniLM or E5-small class model), encode title + dek **at ingest**, store
the vector on the article row, version it. Then, in order:

1. **Aggregate merge pass first.** Add cosine over member-averaged vectors as a second profile
   similarity beside the IDF profile, under the pass's existing complete linkage, size cap, gap
   cap and geo/entity vetoes. This is where F4 lives (the Seattle pieces), and it is the safest
   place for a semantic signal because the guards are already there. Bar: `audit_story_duplicates`
   candidate count falls; droppedOut ≤ 1%; largest cluster within noise; exhibits unmoved.
2. **Pairwise gate second**, as one feature of the score — never as sole evidence (the
   template trap). Bar: the standard adopt/reject bars plus the golden-pairs kill condition.
3. **Cross-lingual bridge third.** For the non-English slice only (a few thousand articles),
   propose candidate pairs by nearest neighbour over vectors and admit them through the same
   scorer with the same anchors and vetoes. Bar: ru/ar/ko/ja participation rises from 0.0%;
   no English story changes.

### Stage 2 — the calibrated pairwise scorer

Replace the fixed threshold with a logistic model (numpy) over ~10 features: Jaccard,
distinctive-shared-token count, template-only-evidence flag, instance-anchor agreement,
place agreement, entity overlap and disjointness, time gap, embedding cosine (when present),
dek-profile similarity. Train on the golden pairs with provenance weighting (human exhibits
weigh most), hold out the exhibits as the kill set, regularise hard, and **adopt on the
counterfactual harness, not on training accuracy**. Keep the quorum linkage over the scored
edges unchanged. Bar: pairwise precision/recall on held-out labeled pairs beats the current
gate on both axes, and the catalog-level bars hold.

### Stage 3 — only if the record demands it

A deterministic graph community pass on components ≥ 8 members for the bridge class, and
incremental assignment (roadmap M10) only if Tier A must exceed its 83,000-article budget.
Neither is needed for accuracy today.

### What this deliberately does not do

- It does not replace the clusterer with HDBSCAN, an embedding-only threshold, or a
  non-deterministic community method — each fails a property the product depends on (§2.4,
  §2.5, §2.6).
- It does not touch content-mill and wire "stories": they are correctly clustered non-news, and
  the measured fix is source curation (`CONTENT_MILL_STORY_EVALUATION.md`).
- It does not raise any existing threshold. That knob is spent, twice over.

### Expected effect, stated as what would be measured

- False merges: the instance-anchor veto closes the numbered-template class the lexicon cannot
  name; broader entity coverage lets X5c fire on many more of the merges it currently cannot
  see; the judge takes the bridges. Target: independently-scored bad clusters ≤ 2 of ~90, and
  zero recorded-exhibit welds in the window.
- False splits: embeddings in the merge pass rejoin disjoint-vocabulary pieces (Seattle-class);
  the cross-lingual bridge moves Group A languages off zero. Target: `audit_story_duplicates`
  duplicated-event count down by half; story participation up from 23.6% with droppedOut ≤ 1%
  per stage.
- Cost: no change to the build's n^2.15 curve (the candidate set is unchanged); ingest gains
  a per-article encode and extraction of under two CPU-minutes per day on the current box [P];
  the judge is bounded by its budget.

---

## 5 · References

Papers and patents, from memory of the literature — verify against the originals before
citing externally.

- Allan, J., Carbonell, J., Doddington, G., Yamron, J., Yang, Y. (1998). *Topic Detection and
  Tracking Pilot Study: Final Report.* DARPA Broadcast News Workshop.
- Allan, J., Papka, R., Lavrenko, V. (1998). *On-line New Event Detection and Tracking.* SIGIR.
- Yang, Y., Pierce, T., Carbonell, J. (1998). *A Study on Retrospective and On-line Event
  Detection.* SIGIR.
- Brants, T., Chen, F., Farahat, A. (2003). *A System for New Event Detection.* SIGIR.
- Kleinberg, J. (2002). *Bursty and Hierarchical Structure in Streams.* KDD.
- Nallapati, R., Feng, A., Peng, F., Allan, J. (2004). *Event Threading within News Topics.* CIKM.
- Leban, G., Fortuna, B., Brank, J., Grobelnik, M. (2014). *Event Registry: Learning About World
  Events from News.* WWW Companion.
- Rupnik, J., Muhič, A., Leban, G., Škraba, P., Fortuna, B., Grobelnik, M. (2016). *News Across
  Languages — Cross-Lingual Document Similarity and Event Tracking.* JAIR.
- Miranda, S., Znotiņš, A., Cohen, S. B., Barzdins, G. (2018). *Multilingual Clustering of
  Streaming News.* EMNLP.
- Staykovski, T., Barrón-Cedeño, A., Da San Martino, G., Nakov, P. (2019). *Dense vs. Sparse
  Representations for News Stream Clustering.* Text2Story workshop at ECIR.
- Laban, P., Hearst, M. (2017). *newsLens: Building and Visualizing Long-Ranging News Stories.*
  ACL workshop on Events and Stories in the News.
- Reimers, N., Gurevych, I. (2019). *Sentence-BERT: Sentence Embeddings using Siamese
  BERT-Networks.* EMNLP; and (2020) *Making Monolingual Sentence Embeddings Multilingual using
  Knowledge Distillation.* EMNLP.
- SemEval-2022 Task 8: *Multilingual News Article Similarity* (Chen, X. et al.). SemEval.
- Ester, M., Kriegel, H.-P., Sander, J., Xu, X. (1996). *A Density-Based Algorithm for Discovering
  Clusters in Large Spatial Databases with Noise.* KDD. Campello, R., Moulavi, D., Sander, J.
  (2013). *Density-Based Clustering Based on Hierarchical Density Estimates.* PAKDD.
  Grootendorst, M. (2022). *BERTopic.* arXiv.
- Blondel, V., Guillaume, J.-L., Lambiotte, R., Lefebvre, E. (2008). *Fast Unfolding of
  Communities in Large Networks.* J. Stat. Mech. Traag, V., Waltman, L., van Eck, N. J. (2019).
  *From Louvain to Leiden: Guaranteeing Well-Connected Communities.* Scientific Reports.
- Conrad, J. G., Bender, M. (Thomson Reuters). *System and Engine for Seeded Clustering of News
  Events.* US Patent 11,663,254 B2 — already the lineage of `STORY_LINK_SUPPORT.md`.
- Liu, X. et al. (2017). *Reuters Tracer: Toward Automated News Production Using Large Scale
  Social Media Data.* IEEE Big Data.

Industry systems without published clustering internals, noted for completeness: Google News
(story grouping unpublished; Das et al. 2007 covers its recommendation side), Ground News
(≈ 50k sources; licenses AllSides/MBFC/Ad Fontes ratings rather than generating them —
`SCALE_ROADMAP.md`; no clustering method published), AllSides (editorial curation).

Internal records this document rests on: `CLUSTER_TRUST.md`, `STORY_CLUSTER_MERGES.md`,
`STORY_CLUSTER_QUORUM_VERIFICATION.md`, `STORY_LINK_SUPPORT.md`, `STORY_TEMPLATE_GATE.md`,
`STORY_ENTITY_EVIDENCE_PLAN.md`, `EVENT_IDENTITY_RUBRIC.md`, `CONTENT_MILL_STORY_EVALUATION.md`,
`M14_LANGUAGE_DENSITY_DESIGN.md`, `PERFORMANCE.md`, `SCALE_ROADMAP.md`, and the docstrings of
`examples/clustering.py`, `examples/story_service.py`, `examples/event_identity.py`.
