# X4 — entity evidence at edge time: experiment plan

**Status: DESIGN ONLY. Nothing here is implemented, and nothing changes production — not
`link_quorum`, not the serving path, not the schema.** This document is the read-only deliverable
of the X4 design task: what entity/event evidence the catalog already carries, the smallest
offline experiment that can measure whether feeding it to the clusterer's *edge decision* helps,
and the bars that decide adoption — fixed here, before any number is seen, per the practice that
caught the IDF change (`story_service.use_idf`).

## 1. The problem, and why it is not another threshold

The X0 re-baseline (2026-08-16, recorded in `story_service.link_quorum()` and
`docs/CLUSTER_TRUST.md`) established both ends of the current regime on one day's catalog:

* **With the production stack** (quorum 0.2, repair 0.5, merge 0.33): 1,541 stories, largest
  cluster **64** — the Karoline Leavitt resignation, 37 publishers, a genuine single event.
* **Without it** (the library fallbacks — single linkage): largest cluster **787**, holding a
  Colombian earthquake, the Congo Ebola outbreak, a Zimbabwe ferry capsizing, a Red Sea attack
  and the Iran–US crisis under one White House staffing headline.

X0 also established that this knob is spent: 0.3 shaved the largest cluster 64 → 62 while
dropping 3.0% of covered articles and splitting 37-publisher events; 0.4 dropped 5.6%. Every
lexical-graph change tried so far — IDF weighting (reverted, −10.5% coverage), publisher
concentration (0% precision / 0% recall), quorum above 0.2 — fails the same way, because they all
adjust *how much lexical similarity is enough*, and boilerplate-token overlap between unrelated
events does not stop being overlap at any threshold.

The transferable finding from the SimClusters comparison is structural: **transitive closure is
safe when the edge predicate is close to an equivalence relation and dangerous when it is a
graded threshold.** X's `LargestDimensionClusteringMethod` runs the same connected-components
closure we do, safely, because its edge predicate is binary same-cluster/not. The lever that is
still untouched here is not the threshold — it is **what the edge is made of**. X4 adds a second,
non-lexical fact to the edge decision.

## 2. Inventory — entity/event evidence that exists TODAY

Verified against the code and catalog on 2026-08-16, with receipts, because the difference
between "exists" and "existed in a plan" has already burned one investigation this week.

### 2.1 Event countries — flowing, deterministic, already on the clustering rows

* **Table:** `article_event_locations` (`store.py` — `ArticleEventLocation`): 0..n rows per
  article, ISO-3166 alpha-2 country, provider-extracted, provenance-tracked (`source =
  "gdelt-gkg"`). Never inferred from article text by us.
* **Producer:** the GDELT GKG enricher (`gdelt_gkg.py`, poller-wired as
  `sources.GDELTGKGEnricher`), **ON in production** — `RWE_GDELT_GKG: ${RWE_GDELT_GKG:-1}`
  (`deploy/docker-compose.yml:75`), a 15-minute cycle over GDELT's `*.gkg.csv.zip`. Dominant
  country per article only (one stray mention never locates an article); FIPS trap handled by
  normalizing country *names*, unknown names dropped rather than mis-mapped.
* **Already on every row the clusterer sees:** `story_service._fetch` batch-annotates
  `r["eventCountries"]` via `store.event_countries_for_urls`, and
  `discover.feed_article_to_article` carries it onto the article dicts
  (`discover.py:118`). **The veto needs zero new queries and zero new plumbing** — the data
  rides along today and the edge predicate simply never looks at it.
* **Used today only POST-HOC:** `_geo_coherence` scores clusters after they form; `clusterTrust`
  demotes and withholds blindspots; `repair_quorum` re-splits condemned clusters. The entire
  Cluster Trust apparatus is this signal arriving *after* the damage. X4 asks whether it can
  arrive *before*.
* **Evidence it discriminates exactly the failure we care about:** the 787 counterfactual blob's
  ancestors scored geoCoherence 0.62 with members across twelve countries
  (`story_service.link_quorum()`); its X0 piece-read separates cleanly along country lines —
  earthquake (CO), Ebola (CD), ferry (ZW), staffing (US). Meanwhile `_geo_coherence`'s docstring
  documents the true-pair hazard: genuine two-country events (F1 Hungarian GP located HU and GB,
  Zelenskyy/Russia/Iran, Mamdani/ICC) whose members each carry a *different single* country.
  Both halves of the question are already in the file; §4 designs around the second.

### 2.2 What does NOT exist (so nobody re-proposes it as "already there")

* **LLM-extracted entities/facets: none.** Article Insights and the facets contract were
  **removed on 2026-08-03** (`15cf0f7`): *"dormant in production from the day it shipped (zero
  rows)"*. The `article_insights` table may linger in old DB files; it has no code and never had
  data. Recommending insight facets as an evidence source would be recommending the feature's
  reintroduction, not reuse.
* **Embeddings: none**, and SimClusters is not an argument for adding them — X's content
  embeddings are projections into communities learned from a user–user interaction graph we do
  not have.
* **Persons/organizations/themes: not parsed.** But note where they are: the *same* GKG record
  we already download and match to catalog articles carries `V1PERSONS`, `V1ORGANIZATIONS` and
  `V1THEMES` — `gdelt_gkg.py` currently reads columns 2, 4, 9 and 18 and skips the rest. Richer
  entity evidence is additional columns of a file already in the pipeline, not a new dependency.
  **Deliberately out of scope for X4** (schema + parser + storage change); it is rung 2, gated
  on rung 1's result.
* **Category** (`scored` JSON payload, e.g. `"category": "Politics"`) exists per article and is
  deterministic once stored, but it is model-assigned at ingest with unmeasured error, and a
  category-disagreement veto would fire on true pairs constantly (an eclipse is Science in one
  desk's taxonomy and World in another's). Recorded as available; not part of the experiment.

## 3. The candidate mechanism — a fail-open country veto on the edge

One rule, stated completely:

> An edge between two articles is **vetoed** iff **both** are event-located **and** their country
> sets are **disjoint**. In every other case — either side unlocated, or any shared country —
> the lexical decision stands unchanged.

Why this shape and not a similarity bonus: adding graded geo-similarity to the score would be a
third weighting to calibrate and would re-enter the family of measured failures. A veto is
binary (the equivalence-relation end of the SimClusters spectrum), cannot *create* an edge, and
therefore cannot create a false merge — its entire risk budget is false splits, which is exactly
the axis our instruments (`droppedOut`, the split tables, the a/p tell, the must-keep set) are
built to measure.

Why fail-open on missing data: located coverage is partial (the scored-story minority is
documented in `CLUSTER_TRUST.md`). A veto that fired on absence would collapse coverage exactly
like the failures we reverted; a veto that fires only on *positive disagreement* leaves every
unlocated pair byte-identical to production. It also degrades gracefully: if GKG goes dark, the
veto silently stops firing rather than stopping clustering.

Where it slots (implementation sketch for the harness — **not implemented now**):
`clustering.cluster()`'s `pair_ok` is already the single predicate used by *both* candidate
admission and `_quorum_ok` cross-pair scoring, precisely so the two cannot drift. One optional
`evidence: Callable[[int, int], bool] | None = None` parameter ANDed into `pair_ok`
(default `None` = byte-identical behaviour, same opt-in pattern as `idf` and `link_quorum`)
covers admission, quorum and repair in one place. The duplicate-merge pass
(`_merge_duplicates`) compares cluster *profiles*, so it needs its own consensus-level check:
do not join two clusters whose `countries` consensus sets are both non-empty and disjoint.

Two variants to titrate, because the two-country hazard (§2.1) is real:

* **V-pair** — the veto applies to every pair, formation included. Maximum bite, maximum
  false-split exposure on two-country events.
* **V-growth** — the veto only gates merges where either side has ≥ `MIN_CHAINABLE` (3) members,
  mirroring `_quorum_ok`'s "two singletons always pass": stories always *form*; only *growth*
  is constrained, which is where chaining lives. Expected to spare the F1-shaped cases (the
  two-member seed forms; by the time the cluster is big enough to gate, a genuine two-country
  event's members carry both countries — that is `_geo_coherence`'s documented mechanism).

## 4. The experiment

### Phase 0 — data audit (read-only, no code change, ~2 minutes on the box)

The veto's reach is bounded by located coverage, and that number must be measured, not assumed:

```bash
cd /opt/ih && source deploy/ops/_compose.sh
dc run --rm -T api python - <<'PY'
# X4 phase 0 -- located coverage of the clustering window (read-only)
import sys
sys.path.insert(0, "examples")   # stdin scripts get no script-dir path entry; WORKDIR is /app
from collections import Counter
import store, story_service
rows = story_service._fetch(store.Store())
located = [r for r in rows if r.get("eventCountries")]
multi   = [r for r in located if len(r["eventCountries"]) > 1]
print(f"window articles      : {len(rows):,}")
print(f"event-located        : {len(located):,} ({len(located)/max(1,len(rows)):.1%})")
print(f"  multi-country      : {len(multi):,}")
print("top event countries  :", Counter(c for r in located for c in r["eventCountries"]).most_common(10))
stories = story_service.build_stories(rows)
big = sorted(stories, key=lambda s: -s["totalCoverage"])[:20]
print("\nlocated members in the 20 largest stories (veto evidence where blobs live):")
for s in big:
    print(f"  {s['totalCoverage']:>4} arts  {s['locatedMembers']:>3} located  "
          f"coherence {s['geoCoherence']}  {s['title'][:56]}")
PY
```

Go/no-go: if located share of the window is below ~10%, or the largest stories carry almost no
located members, the veto has nothing to act on and the experiment stops here — rung 2 (GKG
persons/orgs) becomes the prerequisite instead.

**Phase 0 result (2026-08-16, production): GO.** 28,433 window articles, **5,304 event-located
(18.7%)** — comfortably above the bar. 708 located articles are multi-country (13.3% of
located), which sizes the two-country hazard V-growth exists for. Top event countries: US 2,608,
GB 858, AU 356, IL 161, CA 159. The evidence concentrates exactly where blobs live: **every one
of the 20 largest stories carries located members** (2–25), at shares well above the catalog's
18.7% — the Leavitt must-keep story is 25/64 located at coherence 1.0 (the veto would see 25
members agreeing on US), Clacton 13/22, Colombia earthquake 9/23 at 0.778. One live finding
rode along: the current catalog's *"Total solar eclipse Aug. 12"* story (26 articles) scores
**coherence 0.333 on 6 located members** — a bad cluster in production today and a concrete
target run C must fix; it joins the must-sever evidence. The thinnest of the top 20 (Powerball,
2/22 located) shows the fail-open case: the veto would simply not fire there, leaving the
lexical decision untouched.

### Phase 1 — the offline harness (audit-only code; serving path untouched)

Extend `examples/audit_clustering_change.py` — the instrument every clustering decision here has
gone through — with `--geo-veto {pair,growth}` on the AFTER side, threading exactly as
`--link-quorum` does (`None` = production = off; the library default stays off, so an
unconfigured environment cannot drift). The audit additionally reports veto telemetry: pairs
examined, pairs where both sides were located, vetoes fired at admission / quorum / merge.

Four runs against the live catalog, all via `dc run --rm -T api` (the X0 lesson — the audit's
env guard now enforces it):

| run | config | question |
|---|---|---|
| A | production stack, no veto | the baseline (before == after, self-check) |
| B | **library fallbacks + veto** (single linkage, no repair/merge) | the mechanism test: does edge-time evidence prevent the 787 blob *without* the quorum's help? |
| C | production stack + V-growth | the adoption candidate |
| D | production stack + V-pair | the titration bracket |

Run B zeroes the stack **explicitly** — `dc run --rm -T -e RWE_CLUSTER_LINK_QUORUM=0
-e RWE_STORY_REPAIR_QUORUM=0 -e RWE_STORY_MERGE_SIM=0 api …` — rather than using an
unconfigured container. The variables stay present, so the audit's environment guard correctly
treats it as a deliberate operator override; the printed tags (no quorum/repair/merge on the
before line) are the record that run B measured the fallback regime on purpose.

**Phase 1 harness: BUILT (2026-08-16).** `--geo-veto {pair,growth}` threads through
`build_stories` → `clustering.cluster(evidence=…, merge_ok=…)` exactly as `--link-quorum` does
(None = production = off; every default byte-identical, pinned by tests in all three suites).
The audit prints a `geo-veto telemetry` line — pairs checked / both located / vetoed, merges
checked / gated / vetoed — and tags the AFTER side `veto pair|growth`. Determinism is pinned by
the unit tests (same input → same clusters; the closures are pure functions of stored data), not
by consecutive box runs, which ingestion drift makes non-identical by construction. The four
runs, ready to paste (wall times include ~seconds of container start, identical on every side).

**The deployed image predates this harness** — running `--geo-veto` from the image alone would
fail exactly the way the crawler verifier once did. The runs therefore bind-mount the branch's
`examples/` read-only from a THROWAWAY worktree: the deployed image, the running containers and
the `/opt/ih` tree are all untouched (the 2026-08-16 431-commit checkout incident is why the
tree is never switched for this). The branch's `examples/` is behaviour-identical to the
deployed one for every default path — that is pinned by the suites, and run A double-checks it
on the box (before == after, no veto tag, no telemetry line).

```bash
cd /opt/ih && source deploy/ops/_compose.sh
git fetch origin claude/sleepy-gates-oecof1
git worktree add --detach /tmp/x4-code origin/claude/sleepy-gates-oecof1
V="-v /tmp/x4-code/examples:/app/examples:ro"
LOG=/tmp/x4_phase1_$(date -u +%Y%m%dT%H%M%SZ).log
{
  echo "===== run A: production baseline (self-check; expect before == after) ====="
  time dc run --rm -T $V api python examples/audit_clustering_change.py --show 5
  echo "===== run B: library fallbacks + veto pair (mechanism test vs the 787 blob) ====="
  time dc run --rm -T $V -e RWE_CLUSTER_LINK_QUORUM=0 -e RWE_STORY_REPAIR_QUORUM=0 \
      -e RWE_STORY_MERGE_SIM=0 api \
      python examples/audit_clustering_change.py --geo-veto pair --show 10 --pieces 5
  echo "===== run C: production + veto growth (adoption candidate, 1% bar) ====="
  time dc run --rm -T $V api python examples/audit_clustering_change.py \
      --geo-veto growth --show 10 --max-dropped 0.01
  echo "===== run D: production + veto pair (titration bracket, 1% bar) ====="
  time dc run --rm -T $V api python examples/audit_clustering_change.py \
      --geo-veto pair --show 10 --max-dropped 0.01
} 2>&1 | tee "$LOG"
git worktree remove /tmp/x4-code
```

### Phase 1 results (2026-08-16, production catalog, 28,418 articles)

| | A: baseline | B: fallbacks+pair | C: prod+growth | D: prod+pair |
|---|---|---|---|---|
| stories | 1,555 | 1,336 → 1,343 | 1,555 → **1,559** | 1,555 → **1,553** |
| largest | 64 | 790 → **564** | 64 → 64 | 64 → 64 |
| dropped / newly | 0 / 0 | 28 (0.4%) / 0 | **27 (0.4%) / 26** | 23 (0.4%) / 3 |
| bad clusters | 4/74 (.925) | 8/69 → 5/71 (.912) | 4/74 → **1/69 (.978)** | 4/74 → 2/69 (.95) |
| veto telemetry | none (correct) | 20,303 pairs, 1,551 both-located, **161 vetoed** | 8,620 merges, 2,326 gated, **1,107 vetoed** | 53,790 pairs, 4,063 both-located, 448 vetoed |
| wall | 23.2s | 16.9s | 22.6s | 37.2s |
| verdict | self-check PASS | mechanism shown | **all aggregate bars PASS** | **REJECT** (story count fell) |

* **Run A** proved the mounted code byte-identical on the box: before == after, no tag, no
  telemetry.
* **Run B** answered the mechanism question with a precision the aggregates undersell: 161
  pairwise vetoes pulled the **entire 206-article Colombia earthquake out of the Iran-war
  blob** — plus the NJ-victim, Colombia/Golan and Netanyahu-AI clusters — for 9 articles
  dropped, and the eclipse/sports blob shed Perseid + eclipse-glasses pieces. But the blob
  floor is **564, not ~64**: the remaining chain is located-compatible or bridged by unlocated
  members, so the veto is a scalpel that composes with the quorum, NOT a replacement for it.
  The quorum stays load-bearing.
* **Run C is the candidate and passed every aggregate bar**: coverage 0.4% (bar 1%) and nearly
  net-zero (27 out, 26 in), story count UP, largest unchanged (Leavitt intact — absent from the
  split table, as are the eagle and Lala tripwires), cost within noise, and the Phase-0 live bad
  cluster (eclipse, 0.333) severed into 5 pieces. Bad-cluster count fell 4 → 1 — with the
  stated caveat that this metric shares the veto's data; the count argument (a veto cannot
  manufacture located members, so condemned shapes simply no longer form) is why it still
  carries weight, but confirmation belongs to the hand-read below, not the score.
* **Run D is rejected on the pre-registered bar**: the story count fell — pair mode dissolves
  legitimate multi-country stories exactly as predicted (§3): Europe-wide heat records (3/3,
  gone), eclipse-across-Europe pieces, Eurovision. The two-country hazard is real and growth
  mode is the shape that avoids it. Pair mode is closed.

**C is NOT yet adopted.** The must-keep clause requires reading C's split pieces by hand before
any verdict: the split table contains presumptively-legitimate two-country stories — the
Ukrainian strike on the oil depot (22/16, coherence 0.75 → 2 pieces), the Colombia quake
family (23/16, 0.778 → 3), the Iran-war umbrella (62/25, 0.733 → 2), Ronaldo's wedding
(16/13 → 2) — and whether those pieces are separate events or one event shredded is exactly
what no aggregate can say. That read is the remaining gate.

### The run-C hand-read (2026-08-16) — and V-growth-2

The pieces run (`--pieces 12`) settled it in both directions at once.

**True splits — production false merges, fixed by the veto:** the story served today as
*"Colombia earthquake: Death toll rises to over 100"* is actually THREE disasters on three
continents (Baguio landslide PH 13/8 + Colombia quake 13/9 + Congo Ebola 10/8) fused on
"death toll rises" phrasing; the eclipse story contained the Samsung One UI update (9/9 out);
*"Judge sides with Trump"* was two different court cases (11/8 + 6/5); *"Hers Health results"*
was two different companies' earnings (8/8 + 6/6). The veto also CONSOLIDATED real duplicates:
the Colombia quake family 4 stories → 2, Matisse, Kidman, and the cross-language Air Force One
decoy story (Norwegian + English coverage joined).

**False split:** Ronaldo's wedding, 16 → 9+7 — one event, two stories, and the same location
disagreement blocks the dup-merge pass's rejoin (its coherence guard refuses), so the duplicate
persists.

**New false merge:** *"At least 30 dead as major earthquake hits Colombia (VIDEOS)"* at 31/21
absorbed THREE Indonesia-quake stories. Mechanism identified: the `MIN_CHAINABLE` size
exemption — two 2-member seeds of different earthquakes fuse UNGATED on template vocabulary
("magnitude 7.7 earthquake strikes"), and the fused cluster's {CO, ID} tie-consensus then
overlaps both sides of every later check. Poisoned consensus, absorbs freely.

**V-growth-2** replaces the size rule with a corroboration rule — *veto iff the located
consensuses are disjoint and EITHER side's winning vote count ≥ `GEO_MIN_CONSENSUS` (2)* —
built from three measured failures (each recorded at the constant): the Ronaldo split shows a
sample of one must not testify against a sample of one; the Colombia+Indonesia fusion shows
small merges need gating when the evidence is real; and the first draft (a symmetric ≥2-located
floor) never reached the box because its own unit test showed a singleton always fails a
per-side floor, so clusters absorb located-disagreeing singletons one at a time — single
linkage rebuilt by absorption. Corroboration is asymmetric on purpose: a corroborated receiver
may reject a thinly-located dissenter (bounded damage, measured by the 1% bar), while two
samples of one never veto each other. The same rule now gates `_merge_duplicates`, because
one-sided corroboration can leave the pooled located set at 3 — under `MIN_LOCATED_FOR_TRUST`,
where that pass's coherence guard is silent and would otherwise quietly rejoin what the veto
severed. Run E (below) re-measures C's configuration under V-growth-2.

```bash
cd /opt/ih && source deploy/ops/_compose.sh
git fetch origin claude/sleepy-gates-oecof1
git worktree remove --force /tmp/x4-code 2>/dev/null; \
git worktree add --detach /tmp/x4-code origin/claude/sleepy-gates-oecof1
dc run --rm -T -v /tmp/x4-code/examples:/app/examples:ro api \
   python examples/audit_clustering_change.py \
   --geo-veto growth --show 20 --pieces 12 --piece-limit 12 --max-dropped 0.01 \
   2>&1 | tee /tmp/x4_runE_$(date -u +%Y%m%dT%H%M%SZ).log
git worktree remove /tmp/x4-code
```

Run E's hand-read checks, fixed before the run: the Colombia+Indonesia merge must be GONE from
the merged table; Ronaldo must appear at most once in the split table's re-partitions (ideally
not at all); the death-toll trio, courts, earnings and eclipse/Samsung severances must SURVIVE;
and the aggregates must hold their run-C levels (≤ 1% dropped, story count not falling, largest
≤ baseline, bad count not above 4).

**Run E result (2026-08-16): mixed — the aggregates are the best yet and the headline check
FAILED.** 1,563 → 1,565 stories, largest 64, dropped 16 (0.3%) against 14 newly covered, bad
clusters 4/74 → 2/71 at mean 0.964, 780 merge vetoes. Ronaldo is intact (the corroboration rule
did its job) and the Ebola severance survived. But the **Colombia+Indonesia merged story is
still in the merged table, unchanged at 31/21**, and three run-C true severances did not
survive corroboration's permissiveness: the two court cases, the two companies' earnings and
the eclipse/Samsung split are fused again, while two new one-event-in-two-pieces candidates
appeared (the Assad trial 16+4, the Zuckerberg yacht 11+3).

The CO+ID survival has exactly two possible explanations with OPPOSITE conclusions, and the
next step is the measurement that separates them rather than a fourth rule revision:

* the Indonesia articles are **unlocated** (ID is absent from Phase 0's top-10) — then no
  location rule can ever sever this pair, the veto's reach is bounded by GKG's 18.7%
  article coverage, and the ceiling is DATA, not design;
* they are located **one member per sub-story** — then the 1v1 seed fusion fails open (that IS
  the Ronaldo protection), the fused {CO, ID} tie overlaps the corroborated {CO} side, and the
  poisoning path survives corroboration: a genuine design tension, because the same rule that
  protects Ronaldo admits the tie.

The diagnostic is a read-only print of `eventCountries` for the members of the survived merge
and the changed splits; its output decides between "adopt as a bounded incremental win" and
"the corroboration rule needs a tie-handling amendment".

## X4 conclusion (2026-08-16, diagnostic complete)

The member-level located print (312 articles) resolved every open case, and the answer is a
**data ceiling, not a rule defect** — in two distinct forms:

* **Coverage ceiling.** The Indonesia-quake stories that fused with Colombia are built from
  UNLOCATED members (the ID-located Indonesia articles sit in other clusters), and the Samsung
  articles are unlocated too. The veto cannot sever what GDELT did not locate; 18.7% article
  coverage is the reach.
* **Granularity ceiling.** The two court cases are both US events — the consensuses overlap and
  the veto CORRECTLY declines. Country-level evidence cannot separate two same-country events
  (courts, the two companies' earnings). Run C's severances there were accidents of the cruder
  rule, not wins corroboration lost.
* **The residual sharp edge.** The Assad (16+4) and Zuckerberg-yacht (11+3) splits fired
  through single MISLOCATED members — `IN` on a Syria trial, `PF` on an Alaska incident —
  meeting a corroborated opposite consensus. Bounded (small pieces, zero dropped), visible, and
  the symmetric fix was already measured to be worse (the absorption hole).

**What V-growth-2 measurably delivers at that ceiling** (run E, production config): bad
clusters 4 → 2, largest cluster unchanged with every named tripwire intact, 0.3% coverage cost
at net −2 articles, deterministic, ~zero compute — and in the counterfactual regime (run B
shape) the same evidence removes hundreds of articles of cross-country chaining from a
regrowing blob for single-digit drops. As insurance against the monitors' regrowth scenario it
is cheap and real.

**Recommendations, in order:**

1. **Adoption of V-growth-2 is a product call, not a measurement call**, and the measurements
   are now complete: +2 fewer independently-bad clusters and mega-cluster insurance, against
   two small mislocation-driven duplicate splits per catalog. If adopted: set
   `RWE_CLUSTER_GEO_VETO=growth` in deploy config (one env row + restart; the code ships
   dormant either way). This plan makes no production change.
2. **The real unlock is rung 2** — `V1PERSONS` / `V1ORGANIZATIONS` / `V1THEMES` from the SAME
   GKG file the enricher already downloads every 15 minutes. Person/org evidence dissolves both
   ceilings at once: it separates two US court cases (zero shared persons between Harvard and
   the Minnesota case) and reaches articles GDELT located but we only stored countries for.
   That is a new experiment (schema + parser + the same audit discipline), gated on its own
   go-ahead.
3. The X0 lesson generalizes and closes the loop on the SimClusters comparison: every
   remaining failure is now attributable to what the evidence IS (coverage, granularity,
   mislocation) rather than how the threshold is tuned — which is exactly where a
   representation-level experiment was supposed to land.

## X5 — rung 2: persons and organizations from the same GKG file

**Status: Phase 0 BUILT (2026-08-16), dormant everywhere.** Approved as its own experiment
after X4's conclusion. Same discipline: measure first, design the rule from the measurement,
pre-register bars before any rule run.

### What shipped (all inert by default)

* `store.ArticleEntity` — a side table with the `ArticleEventLocation` contract
  (provider-extracted, provenance per row, never inferred by us). **Nothing in the serving path
  reads it.** Auto-created like every other table; no migration.
* `gdelt_gkg.parse_gkg_entity_lines` — a SEPARATE streaming pass over the same downloaded zip
  (V1PERSONS col 11, V1ORGANIZATIONS col 13; normalized lower-case, deduped before a 24-name
  cap), so the location/image record shape and every consumer of it stay byte-identical.
* Steady-state opt-in: `RWE_GDELT_ENTITIES=1` makes the existing enricher cycle also persist
  entities (one extra decompression, zero extra HTTP). Default off; set by no deploy config.
* `examples/gdelt_entity_backfill.py` — the one-shot history backfill for the current window,
  with **production-data neutrality as a tested contract**: it writes ONLY `article_entities`;
  a location backfill would move geoCoherence/trust/blindspots mid-experiment and is refused by
  design. Request-rate honesty in the docstring: `--hours 48` is 193 sequential downloads
  (~1–2 GB), a one-time burst, not the sustained misconfiguration that once rate-limited the
  DOC adapter.
* `examples/audit_entity_separability.py` — the rule-design instrument. Read-only and
  deterministic. Measures coverage, the ubiquity (df) table, and THE number: shared-entity
  rates among within-story pairs versus confusable cross-story pairs (different stories whose
  titles share ≥ MIN_SHARED_TOKENS). The suspected failure is written down before the data:
  ubiquitous names ("donald trump" appears in both court cases), so the instrument reports a
  `rare` column under a df floor alongside raw overlap. The constructed smoke already shows the
  expected signature — org overlap separating while a ubiquitous person does not — but a
  constructed example is a hypothesis, not a measurement.

### Phase 0 on the box

```bash
cd /opt/ih && source deploy/ops/_compose.sh
git fetch origin claude/sleepy-gates-oecof1
git worktree remove --force /tmp/x5-code 2>/dev/null; \
git worktree add --detach /tmp/x5-code origin/claude/sleepy-gates-oecof1
V="-v /tmp/x5-code/examples:/app/examples:ro"

# 1. Backfill entities for the last 48h of GKG history (~15-30 min, one-time burst).
dc run --rm -T $V api python examples/gdelt_entity_backfill.py --hours 48 \
   2>&1 | tee /tmp/x5_backfill_$(date -u +%Y%m%dT%H%M%SZ).log

# 2. The separability measurement.
dc run --rm -T $V api python examples/audit_entity_separability.py \
   2>&1 | tee /tmp/x5_separability_$(date -u +%Y%m%dT%H%M%SZ).log

git worktree remove /tmp/x5-code
```

### Go/no-go, fixed before the numbers

* **Coverage**: entity-covered share of the window materially above the located 18.7% (the
  whole point of rung 2 is more reach). Backfill depth caps coverage at ~48h of the 6-day
  window — read the number against that, not against 100%.
* **Separability**: the within-story shared-any (or shared-rare) rate must sit WELL above the
  confusable rate — a gap wide enough that a fail-open corroborated rule (the X4 shape) has
  room to act. If the two lines are close, or only ubiquitous names carry the overlap, rung 2
  stops here and that is the finding.
* Phase 1 (the rule + audit runs) is designed FROM this output and gated on its own go-ahead —
  the rule is not written yet, deliberately.

### Phase 0 first run (2026-08-16): backfill GOOD, instrument measured its own join

The backfill was clean — 192/192 windows, 146,364 entity records, 1,793 catalog articles,
13,286 rows, locations/images untouched. Two findings and one defect:

* **6.2% overall coverage is the EXPECTED value, not a shortfall**: a 48h backfill covers ~1/3
  of the 6-day window, and 1/3 × 18.7% ≈ 6.2% — per covered span, entity coverage matches
  located coverage, as it must (same records, same matcher). The instrument now prints coverage
  by article age so the arithmetic is visible. A 6-day backfill (`--hours 144`, 577 downloads)
  is the lever if Phase 1 wants full-window reach.
* **The df table shows extraction noise beyond ubiquity**: `instagram` (127) / `facebook` /
  `reuters` / `associated press` as "organizations" are share-chrome and bylines; `los angeles`
  as a "person". The rare-floor absorbs them numerically, but any Phase-1 rule must treat
  high-df names as non-evidence — which the floor already encodes.
* **The defect**: story coverage entries carry the DISPLAY url, the instrument's index was
  keyed by canonical, so most members never joined — 150 within pairs from ~1,500 stories,
  zero both-covered, while the df table itself proved the data existed ("luigi mangione"
  df 30 against a Mangione story showing none). Fixed (join through both url forms), and pair
  formation is now CONDITIONAL on entity coverage — the rule-design question is "given both
  sides carry entities, do they discriminate?", and coverage has its own line. Both lessons
  are pinned by tests. The separability rerun is the outstanding measurement.

### Phase 0 second run (2026-08-16): measured — and two instrument parameters indicted

With the join fixed: within-story 379 pairs at shared person **67.0%** / org **53.0%** / any
**85.0%** / rare 84.7%; confusable 187 pairs at **62.0% / 27.3% / 72.2% / 72.2%**. Read raw,
that gap is NOT "well above" — a no-go — **except the output indicts its own parameters**:

* The ubiquity floor (df ≥ 88, i.e. 5% of a 6.2%-covered catalog) marked exactly THREE names
  ubiquitous, so `reuters` (86), `white house` (73), `facebook` (63) and `associated press`
  (38) all counted as *rare shared evidence* — two unrelated wire stories sharing a byline
  scored as a rare-entity match, inflating the confusable line.
* A df floor is the wrong tool anyway: `luigi mangione` reached df 30 BECAUSE the story is
  big. The noise is identifiable by IDENTITY — outlet-registry resolves (bylines, media
  names), platform chrome, country names extracted as entities — and the instrument now
  filters by identity, reports exactly what it removed, and keeps `--no-noise-filter` as the
  raw view. Pinned by tests.
* One clean positive: per-span entity coverage is **24.3–29.9%**, ABOVE the 18.7% located rate
  — a full-window backfill clears the coverage bar with room.
* The org column already separates ~2× (53.0% vs 27.3%) even under the noise. Persons barely
  (67% vs 62%) — confusable pairs are related-but-distinct events by construction, and the
  same actors legitimately span sub-events. Whatever Phase 1 becomes, it leans on
  organizations, not persons.

Next round on the box, one command each: deepen the backfill to the full window
(`--hours 144`, 577 downloads — the one-time cost of full reach) and rerun the instrument
with the noise filter. The go/no-go is then read as originally registered, on clean numbers.

### Phase 0 third run (2026-08-16, full window, denoised): coverage PASSES, pairs are the
### wrong altitude

Backfill: 576/576 windows, 7,408 articles, 53,697 rows, locations untouched. **Coverage
25.6%, above the located 18.7% and uniform across age buckets — the coverage bar clears.**
The noise filter removed 42 names / 2,771 occurrences (united states 638, instagram 473,
reuters 326 — the receipt matched the prediction).

Pairwise separability on ~7× the pairs: person 67.2% vs 51.6%, org 41.0% vs 29.9%, any 78.6%
vs 60.9%, rare 77.5% vs 58.4%. Moderate gaps — and decisively, **21.4% of TRUE same-story
pairs share no entity at all**, so any pairwise entity veto would sever a fifth of legitimate
edges. Pair mode is dead for entities, as it was for countries (run D).

But the exhibits show the signal concentrating exactly where X4's did: at CLUSTER level. The
Leavitt story's covered members corroborate `white house` ×32 and `karoline leavitt` ×31; the
Mangione story `mangione` ×18 / `thompson` ×17. Same-story articles quote different people —
pairwise overlap is structurally patchy — while the aggregate is overwhelming. The instrument
now measures the consensus altitude directly (a story's consensus = names with ≥2 member
votes, the `GEO_MIN_CONSENSUS` discipline): **member agreement** (covered members sharing
their own story's consensus — the false-split proxy, want HIGH) and **confusable-story
disjointness** (confusable story pairs whose consensuses share nothing — the true-fire proxy,
want HIGH), plus named exhibits for the top confusable story pairs. The consensus rerun is
the measurement the go/no-go is read from.

## X5 Phase 0 conclusion (2026-08-16, consensus run)

**Against the registered gates: member agreement PASSES, disjointness FAILS.**

* **Member agreement 93.1%** (951/1,021 covered members share their own story's consensus) —
  above the "well above 90%" bar. With 74.5% of articles uncovered and failing open, worst-case
  member-level exposure of a consensus gate is ~1.8% of all members. Cluster-level entity
  consensus is SAFE to gate on.
* **Confusable-story disjointness 35.0%** (71 of 203) — not a clear majority. By the
  pre-registered reading, rung 2 does not auto-proceed.

**What the exhibits force us to admit about that 35%.** The confusable denominator conflates
two populations the ground truth cannot separate automatically:

* The OVERLAPPING pairs are dominantly **same event families that production keeps apart** —
  Farage/Clacton ×3 (sharing harborne/cottrell/burnham), Mangione's court stories ×2, the
  Cambridge scandal, the Trump jet-switch pair. For every one of those, NOT firing is the
  CORRECT behaviour — these are the duplicate-recall problem, not false merges.
* The DISJOINT pairs read as genuinely distinct events — led by the single most-linked
  confusable pair in the catalog (73 links): the UAE/ADNOC vessel attack vs the Iran-war live
  blog. Where the gate would fire, it fires right.
* One new noise class surfaced with a receipt: **type-level responder agencies**. The two
  Colombia-quake stories share only `u s geological` — and USGS attends every earthquake, so a
  Colombia↔Indonesia cross would share it too. The X4-unreachable quake pair is ALSO
  entity-unreachable through agency overlap unless agencies become a curated identity class.
  The slope from "filter reuters" to "filter every agency" is real and steep.

**So the honest verdict**: as a false-merge veto, entity consensus fires precisely but rarely,
and the 35% understates precision because the denominator is heavily contaminated with
legitimate duplicates. Meanwhile the measurement's strongest, cleanest signal is the opposite
polarity: **65% of confusable story pairs share corroborated names because they ARE the same
family** — which is the recall problem `_merge_duplicates` exists for, with entity overlap as
exactly the "richer text" its docstring says the Seattle case needs.

**Options, in order of what the evidence supports (the choice is a product call):**

1. **Pivot the polarity (X5b)**: entity-consensus overlap as MERGE corroboration — a recall
   experiment against `_merge_duplicates`' measured 4.3%-of-covered duplicate population, with
   its own bars (the existing merge bars apply: largest ≤ ~120, coherence guard, complete
   linkage). This is what the data measured strongest.
2. **Bounded disambiguation**: hand-label the top ~25 confusable story pairs (the instrument
   prints them) as same-family vs distinct, turning the contaminated 35% into a real
   precision/recall estimate for the veto before any rule is written.
3. **Stop at the finding**: the entity data, backfill and instrument remain (all dormant), the
   veto question closes as measured-and-narrow, and the X4 V-growth-2 adoption decision stands
   on its own.

Nothing was implemented beyond the instrument; production untouched; `link_quorum` untouched.

## X5b — entity-corroborated merge recall (the pivot the data chose)

**Status: BUILT (2026-08-16), dormant twice over** — `RWE_STORY_ENTITY_MERGE` defaults to 0 AND
the pass requires the caller to inject the entity mapping (`_fetch` never queries it, so a
production build costs nothing whatever the env says). The audit injects it only under
`--entity-merge N`.

### The rule, designed from the phase-0 measurements

Two stories join when their **corroborated entity consensuses share ≥ 2 non-noise names**
(`_story_entity_consensus`: one vote per member per name, ≥ 2 votes to count — the 93.1%
member-agreement receipt; `entity_noise`: identity-filtered, with the USGS residual named at
the definition). Two names because one can be a type-level responder agency. Every guard the
lexical merge pass taught us applies, plus X4's:

* complete linkage over constituent stories (never a chain);
* **the X4 geo-consensus veto, unconditional**: disjoint corroborated located consensuses
  refuse the join whatever the entities say — the Colombia↔Indonesia protection;
* the coherence guard (the independent signal keeps its veto over an entity decision);
* the size cap (`merge_max_size`) and gap window (`merge_max_gap_hours`), same constants.

The measured target population: the Farage/Clacton family (3 shared names), Mangione's court
stories (2–3), the Seattle-shaped pairs `_merge_duplicates`' own docstring names as lexically
unreachable, and the cross-language duplicates. Telemetry counts candidates / joined /
geo-vetoed / coherence-vetoed / size-capped / gap-blocked.

### The run (merge-direction bars apply — the audit computes them)

```bash
cd /opt/ih && source deploy/ops/_compose.sh
git fetch origin claude/sleepy-gates-oecof1
git worktree remove --force /tmp/x5-code 2>/dev/null; \
git worktree add --detach /tmp/x5-code origin/claude/sleepy-gates-oecof1
dc run --rm -T -v /tmp/x5-code/examples:/app/examples:ro api \
   python examples/audit_clustering_change.py --entity-merge 2 --show 10 --pieces 12 \
   2>&1 | tee /tmp/x5b_$(date -u +%Y%m%dT%H%M%SZ).log
git worktree remove /tmp/x5-code
```

Pre-registered reading (the merge bars, `verdict(merging=True)`, computed by the audit):
**adopt** requires zero dropped coverage (a merge that loses articles has a bug), largest
cluster ≤ 120, bad-cluster count not rising, mean coherence within the denominator tolerance —
plus the hand-read: the `mergedFrom` exhibits must be the named recall families (Farage,
Mangione, cross-language pairs), and the Colombia↔Indonesia pair must appear in
`geo-vetoed`/absent, never in `joined`. **Reject** on any of those failing. Story count
falling is the POINT of a merge and does not count against it.

### X5b run 1 (2026-08-16): REJECT at largest 130 — and every named target delivered

The bars did their job. Zero dropped, coherence-veto never needed, and the recall list reads
exactly as designed: Mangione's four court stories joined (71/31), the Farage/Clacton family
of five (64/36), the Colombia quake family **with no Indonesia in it and the geo veto firing
23 times**, USS Lincoln's arc, the eagle pair. 608 candidates, 73 joins, 312 size-capped.

And the blob rebuilt through ubiquitous political entities: *"Trump to Supreme Court: Make
Me"* fused ELEVEN unrelated stories (vaccines, Mamdani's tax, Hormuz, the Tate brothers) into
130/52, with two more Trump-chains at 129/55 and 73/37. Complete linkage held — every pair
really does share `{donald trump, white house}`. It is the `MIN_SHARED_TOKENS` lesson at
entity altitude ("Trump wins Ohio"/"Trump wins Iowa" with names for tokens), and the USGS
finding generalized: the political equivalent of a responder agency is the president.

**Rule v2** (implemented, tested): a name's power to propose is its power to DISCRIMINATE,
which is its **story-consensus df** — `luigi mangione` sits in ~4 consensuses, all genuinely
him; `donald trump` in dozens that are dozens of events. Names in more than
`ENTITY_MERGE_MAX_STORY_DF` (6 — just above the largest genuine family run 1 measured,
Farage's 5) story consensuses are excluded from the shared count, computed from the build's
own consensuses: no external state, deterministic, self-calibrating. Run 2 is the same audit
command; the same bars apply unchanged.

### Ground truth — selection procedures, not fixed IDs (the catalog moves daily)

* **Must-sever set** (false-merge labels): re-run the X0 counterfactual (`fallbacks, --pieces`)
  and take the cross-piece pairs of its largest blob with disjoint located countries — the
  earthquake/Ebola/ferry/Hormuz/staffing family, and eclipse↔US-primaries from the second blob.
  Scored on run B: what fraction of these pairs end in different clusters.
* **Must-keep set** (false-split tripwires), scored on runs C and D — all must survive intact:
  1. the largest current single event (X0's: Leavitt, 64 articles / 37 publishers);
  2. every story with a two-country consensus, ≥ 4 located members and coherence ≥ 0.7 — the
     F1-shaped legitimate multi-country events the veto is most likely to wrong;
  3. the quorum-0.3 false-split victims (X0: Leavitt → 2, the Big Bear eagle 24/16 → 2,
     Tropical Storm Lala 23/17 → 2) — the precision claim is exactly that the veto does NOT
     make the mistakes the blunter instrument made.

### The seven axes, and the bars — fixed in advance

| axis | instrument | adopt requires | reject on |
|---|---|---|---|
| false merges | must-sever severed (run B); bad-cluster count (runs C/D) | run B largest cluster **well below 787** and must-sever majority severed; bad count 4/73 **not up** | mechanism absent in run B |
| false splits | must-keep intact; split tables read by hand (a/p tell) | **zero** must-keep splits; no two-country story fragmented | any must-keep split |
| largest cluster | audit output | ≤ baseline (64-equivalent) in C/D | growth |
| story coverage | `droppedOut` / `newlyCovered` | **≤ 1%** dropped in C (the veto fails open — real drop means it is firing on true pairs; this bar is deliberately tighter than the 5% splitting bar) | > 2% in C |
| coherence / independent signal | audit signal line — **with the caveat below** | bad count not up; mean reported, not adopted on | bad count up |
| determinism | two identical runs; membership hash; the veto is a pure set-intersection of stored data — no clock, network, model or randomness | byte-identical membership | any divergence |
| processing cost | build wall-time A vs C/D; veto-call counters | ≤ +10% (expected ≈ 0: one O(min(m,n)) set intersection per admitted candidate pair, on data already in memory) | > +25% |

**The independence caveat, stated up front:** geoCoherence and this veto consume the *same*
location data. After adoption, the "independent signal" is no longer independent of the linkage
— a veto'd build will look geographically coherent partly by construction. For the experiment
this is handled three ways: the bad-cluster *count* bar still bites (a veto cannot manufacture
located members), the false-split axis is judged by hand-read split tables and the a/p tell
(publisher structure, which the veto never sees), and the must-sever/must-keep sets are labeled
from the X0 piece-read, not from coherence. If X4 is adopted, `CLUSTER_TRUST.md`'s claim that
geoCoherence "can contradict the clusterer" must be re-scoped to the merge pass and the
unlocated majority — that edit rides with adoption, not with this plan.

### Expected side-question the runs answer for free

Production runs `repair_quorum 0.5` — the *post-hoc* consumer of this same signal, which
re-splits condemned clusters after formation. If run C shows the veto preventing what repair
currently repairs (bad count at or below baseline with repair's contribution shrunk to zero
touched clusters), the eventual adoption could *simplify* the pipeline rather than extend it.
Measured, not assumed; noted so the possibility is not discovered twice.

## 5. What this plan deliberately does not do

* **No new model, no embeddings, no LLM, no new dependency.** The nearest richer evidence
  (persons/orgs/themes) is more columns of a file already downloaded every 15 minutes — and even
  that waits for rung 1's verdict. The repo's one LLM extraction pipeline was removed as
  never-adopted three weeks after shipping; that history is an argument for exhausting
  deterministic, provenance-tracked evidence first.
* **No `link_quorum` change.** 0.2 is the measured baseline in every run here, per X0.
* **No production change of any kind** until the Phase-1 numbers are read against the bars
  above. The harness itself follows the `idf`/`quorum` precedent: opt-in parameters whose
  defaults leave every existing caller byte-identical.

## 6. Cost of the experiment itself

Phase 0: one read-only script, ~2 minutes on the box. Phase 1: the harness extension (small —
one parameter through two functions plus audit flags and telemetry, with tests mirroring the
quorum tests) and four audit runs, ~2 minutes each on the current 28k-article window. No
schema change, no backfill, no new infrastructure.
