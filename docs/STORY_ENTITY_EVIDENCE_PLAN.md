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
