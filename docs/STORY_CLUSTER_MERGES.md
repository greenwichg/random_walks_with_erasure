# Story-cluster merge investigation: unrelated articles welded into one story

**Trigger:** the "Spider-Man: Brand New Day" story's coverage list contains — and is *titled by* —
"Jana Nayagan Box Office Day 11 BMS Sales: Thalapathy Vijay's Last Film Selling Tickets Every
Single Minute…" (Koimoi), an article about a different film.
**Scope:** the complete clustering pipeline — candidate generation, similarity, temporal
constraints, normalization, linkage, canonical-headline selection, post-processing.
**Status:** investigation complete; **nothing implemented** — the recommended fix is an env-var
change gated on a measurement the audit CLI computes a verdict for. Production measurements
(2026-08-03) are appended at the end: mechanism and bridge confirmed on the live catalog;
quorum 0.3 lands in the judgment band, titration runs prescribed.
**Date:** 2026-08-03, at `018c62e`.

**Verdict in one line:** the two articles were never judged similar — every direct pair between
them fails the similarity gate by 2× — they were **chained** through the daily box-office
*template* headline ("*«Film» Box Office Collection Day N*"), which pure single-linkage clustering
(`link_quorum = 0.0`, the deliberate, measured production default) welds transitively; the
corrupted headline is the downstream symptom of the earliest-published-member titling rule
applied to a wrong membership. It is systematic (the fourth documented instance of the same
class), and the countermeasure is already implemented, tested, and shipped **off** pending
exactly the measurement this report prescribes.

---

## 1. The pipeline as it actually is

One clustering primitive (`examples/clustering.py`) driven by `story_service.build_stories`.
Stages the request asked about that **do not exist** are named honestly.

| stage | what production does | where |
|---|---|---|
| Candidate generation | inverted token-postings index; only pairs sharing ≥ 1 title token are scored (exact, not approximate) | `clustering.py:253` |
| Title normalization | `title_tokens`: lowercase `[a-z0-9]+`, length > 2, pure digits dropped, stopwords removed (incl. calendar/editorial fillers: months, weekdays, "new", "news", "update", "roundup"…) | `clustering.py:71` |
| Lexical similarity | plain Jaccard over title-token sets, gate `DEFAULT_SIM = 0.28`; admission floors `MIN_SHARED_TOKENS = 3`, `MIN_TITLE_TOKENS = 3` | `clustering.py:83,243` |
| Embedding similarity | **none — no such stage.** The pipeline is purely lexical | (verified by sweep) |
| Entity extraction / NER | **none — no such stage.** "Jana Nayagan" and "Spider-Man" are just tokens | (verified by sweep) |
| IDF rarity weighting | implemented, **OFF** (`use_idf()`; measured revert — cost 10.5% of covered articles) | `story_service.py:435` |
| Temporal constraint | 6-day window (`DEFAULT_WINDOW_DAYS`); missing timestamps never block | `clustering.py:138` |
| Linkage / merge logic | union-find, **single linkage** — `link_quorum() = 0.0`: A~B and B~C merges A, B, C even when A and C share nothing. The cluster-aware alternative (`_quorum_ok`) is implemented and shipped **off** | `clustering.py:287`, `story_service.py:473` |
| Second-pass duplicate merge | `_merge_duplicates` — code default OFF; **the box runs it at 0.33** (`RWE_STORY_MERGE_SIM`, revealed by the audit's baseline tag). Complete linkage over headline+description profiles, geo veto, size cap | `story_service.py:524,827` |
| Targeted repair | `_repair` re-splits condemned clusters — code default OFF; **the box runs it at 0.5** (`RWE_STORY_REPAIR_QUORUM`). Split-only, and only where `_cluster_trust` condemns | `story_service.py:597` |
| Canonical headline | representative = **earliest-published member** (deterministic); its headline titles the event, its description is the summary | `story_service.py:279,306` |
| Post-processing | admission (≥ 2 articles, ≥ 2 publishers), trust scoring (`geoCoherence` vs floor 0.7), trust-aware ranking that *demotes* condemned clusters — containment, not correction | `story_service.py:662,643` |

## 2. Exactly why these two articles are one story

Reproduced with the **real production primitives at the real production configuration** (plain
Jaccard, sim 0.28, min_shared 3, min_tokens 3, window 6 d, quorum 0.0). Anchors A and D–F are the
screenshot's articles; B and C are the daily tracker template ("*«Film» Box Office Collection
Day N*") that entertainment outlets (Koimoi, Sacnilk, Filmibeat…) publish for **every running
film, every day** — the genre both anchors belong to.

```
A  Jana Nayagan Box Office Day 11 BMS Sales: Thalapathy Vijay's Last Film…   (Koimoi, earliest)
B  Jana Nayagan Box Office Collection Day 11                                  (tracker template)
C  Spider-Man: Brand New Day Box Office Collection Day 2                      (tracker template)
D  Spider-Man: Brand New Day Box Office Collection: Tom Holland's Film Sees…  (NDTV Profit)
E  Spider-Man Brand New Day box office collection Day 2: Tom Holland film…    (India Today)
F  Spider-Man Brand New Day: Tom Holland film swings to huge box office…      (BBC)
```

Pairwise gate results (jaccard ≥ 0.28 AND shared ≥ 3):

| pair | jaccard | shared tokens | gate |
|---|---:|---|---|
| **A–C / A–D / A–E / A–F** (Jana ↔ every Spider-Man article) | **0.150–0.167** | {box, day, office(, film)} | **FAIL — never similar** |
| A–B (Jana long ↔ Jana template) | 0.294 | {box, day, jana, nayagan, office} | PASS |
| **B–C (Jana template ↔ Spider-Man template)** | **0.444** | **{box, collection, day, office} — zero film-identifying tokens** | **PASS — the weld** |
| C–D / C–E / C–F (template ↔ Spider-Man core) | 0.462–0.538 | incl. {spider, man, brand} | PASS |
| D–E / D–F / E–F (Spider-Man core) | 0.562–1.000 | — | PASS |

Single linkage takes the transitive closure of PASS edges: **A–B–C–{D,E,F} become one cluster**.
The representative is the earliest-published member — the Koimoi Jana article — so its headline
titles the merged story (`story_service.py:279`). The headline corruption in the screenshot is
therefore not a headline-selection bug; it is the correct rule applied to a wrong membership.

Two aggravators make this genre unusually weldable:

1. **"new" is a stopword**, so "Brand **New** Day" tokenizes to {brand, day} — the film's own
   subtitle donates "day" to every "…Box Office **Day** N" headline it meets.
2. **The floor is reachable on template boilerplate alone.** "Jana Nayagan Box Office Day 11" vs
   "Spider-Man Brand New Day Box Office Day 2": shared = {box, office, day} = exactly
   `MIN_SHARED_TOKENS`, jaccard 0.375 ≥ 0.28 → the pair merges on three words that describe the
   *genre*, not the *event* (digits — the only distinguishing part of a template headline — are
   deliberately dropped by `title_tokens`).

The same input under the shipped-but-disabled cluster-aware linkage (`link_quorum = 0.3`) splits
correctly: `[A, B]` titled "Jana Nayagan…", `[C, D, E, F]` titled "Spider-Man: Brand New Day…".
The bridge is rejected exactly as `_quorum_ok`'s docstring predicts: B matches **1 of 4**
Spider-Man members (0.25 < 0.3) — "a genuine new article about the same event resembles most of
the cluster; a chaining bridge resembles exactly one member."

## 3. Alternative hypotheses, eliminated

| hypothesis | evidence against |
|---|---|
| Direct similarity match between the two articles | Measured with the real tokenizer: every Jana↔Spider-Man pair scores 0.150–0.167 vs gate 0.28. Impossible directly. |
| `_merge_duplicates` (second-pass, description-backed merge) joined them | The box runs the pass at 0.33, but it is irrelevant to this weld: the production forensic (below) shows the Jana articles reach the Spider-Man core over **primary-gate edges alone** — the weld edge scores 0.33 ≥ 0.28 on the plain pairwise gate — so single linkage by itself produces the membership. A profile merge additionally requires *complete* linkage (every cross-pair ≥ the bar), which the failing direct pairs above rule out. |
| `_repair` re-split something wrongly | Runs at 0.5 on the box but can only *split*, never join — and it never sees this cluster: measured `trust=ok` at geoCoherence 0.824 (below), so the condemnation it is scoped to never fires. |
| An embedding/NER stage mis-fired | No such stages exist anywhere in the pipeline (swept `clustering.py`, `story_service.py`, `ingest.py`, `feed_source.py`). |
| IDF weighting distorted the score | OFF in production; and IDF would *lower* the template pair's score (box/office/collection/day are the commonest tokens in the entertainment window), not raise it. |
| Temporal gate too wide | Irrelevant here: both films were genuinely in theatres the same week; no window short enough to separate them would leave multi-day stories intact. |
| Canonical-headline selection bug | Selection is deterministic and correct (earliest member); the defect is membership. Fixing headline selection would re-title, not un-merge. |
| Fixable by tightening the existing parameters | `min_shared = 4` keeps the whole chain (A–B shares 5, B–C 4, C–D 7). Breaking B–C needs `sim > 0.444` — but genuine same-story pairs (C–F at 0.462, D–F at 0.562) sit just above it, so that dial destroys real stories before it separates these. The chain is invisible to every *pairwise* dial; only the **linkage rule** distinguishes "matches one member" from "matches the cluster". |

## 4. Isolated or systematic? Systematic — the fourth documented instance of one class

The class is *formulaic/templated headlines + single linkage*, and the codebase's own comments
carry the measured trail:

- "Local news in brief, July 21" / "…July 22" → 65 articles from 42 publishers merged into one
  "story" (the reason the calendar stopword block exists — `clustering.py:57`).
- A 203-article cluster built from ~12 unrelated stories, each link individually plausible on
  everywhere-words (`clustering.py:91`, the reason `idf_weights` exists).
- The production mega-cluster: grew 194 → 208 → 318 while the corpus grew 23% (`story_service.py:477`),
  measured at **486 articles** in the last full audit (`story_service.py:604`), geoCoherence 0.62
  against floor 0.7, members located across twelve countries; a 106-publisher instance sorted
  ahead of every correctly-clustered story until trust-aware ranking demoted it (`story_service.py:643`).
- This case: the daily box-office tracker template. New genre, same mechanism.

Every templated genre the feeds carry (box-office trackers, market wraps, sports round-ups)
reproduces it, because a template headline's content tokens describe the *format*, not the event.
Note the containment already live is *ranking-only* — and measured blind to this weld: the
107-article Spider-Man cluster scores `trust=ok` at geoCoherence **0.824**, because three Indian
articles inside a hundred co-located entertainment reports do not dent the consensus. A welded
cluster the geo signal *approves of* is demoted by nothing and repaired by nothing; it sails to
the product surface.

## 5. Quantifying it on the current production catalog

The exact current count cannot be read from this repo (the box's catalog is the input); the
instrument built for precisely this measurement is `examples/audit_clustering_change.py` — it
rebuilds the live window under both linkage rules and prints a **computed** ADOPT/REJECT verdict
against bars fixed in advance. On the box:

```bash
# [read-only] the quantification: how many stories are held together only by sub-quorum bridges
cd /opt/ih && sudo docker exec -i deploy-api-1 \
  python examples/audit_clustering_change.py --link-quorum 0.3 --show 20 --pieces 10
```

Read `clusters split` as the count of stories currently welded by bridges that fewer than 30% of
cross-pairs support — the upper bound on chained merges of this kind; `--pieces` prints what the
biggest ones separate into, which is the by-hand check that the pieces are recognisably different
events (this story should appear as a Jana piece and a Spider-Man piece). The independent signal
at the last full measurement (16,857-article catalog): 3 condemned clusters holding 380 articles
— **9.1% of covered articles** sit in clusters the geo signal already contradicts
(`story_service.py:613`). A historical caution the audit itself documents: an early link-quorum
run scored "13.7% dropped coverage", but most of that was a baseline artifact (the BEFORE side
lacked the admission gates production already had) — the audit has since been fixed to default
its baseline to production, so a fresh run's numbers are attributable to the quorum alone.

And the forensic print for this specific story — its members, trust verdict, and the actual
bridge chain (which article welded the two events, with the shared tokens on each hop):

```bash
cd /opt/ih && sudo docker exec -i deploy-api-1 python - <<'EOF'
import os, sys
from collections import deque
for _cand in ("/app/examples", os.path.join(os.getcwd(), "examples"), "/opt/ih/examples"):
    if os.path.exists(os.path.join(_cand, "store.py")):
        sys.path.insert(0, _cand); break
import clustering, story_service
import store as store_mod

NEEDLE = "jana nayagan"
rows = story_service._fetch(store_mod.Store())
stories = story_service.build_stories(rows)
hit = next((s for s in stories if any(NEEDLE in (c["headline"] or "").lower()
                                      for c in s["coverage"])), None)
if hit is None:
    print("no current story carries that headline — the window has rolled past it")
    raise SystemExit(0)
print(f"story: {hit['title'][:78]}")
print(f"  articles={hit['totalCoverage']}  publishers={hit['publisherCount']}  "
      f"trust={hit.get('clusterTrust')}  geoCoherence={hit.get('geoCoherence')}")
mem = hit["coverage"]
toks = [clustering.title_tokens(m["headline"]) for m in mem]
times = [clustering.parse_time(m["publishedAt"]) for m in mem]
ms, mt = story_service.min_shared_tokens(), story_service.min_title_tokens()
n = len(mem); adj = [[] for _ in range(n)]
for i in range(n):
    for j in range(i + 1, n):
        if (len(toks[i]) >= mt and len(toks[j]) >= mt and len(toks[i] & toks[j]) >= ms
                and clustering.jaccard(toks[i], toks[j]) >= clustering.DEFAULT_SIM
                and clustering.within_window(times[i], times[j], clustering.DEFAULT_WINDOW_DAYS)):
            adj[i].append(j); adj[j].append(i)
src = next(i for i, m in enumerate(mem) if NEEDLE in (m["headline"] or "").lower())
prev, q = {src: None}, deque([src])
order = []
while q:
    x = q.popleft(); order.append(x)
    for y in adj[x]:
        if y not in prev:
            prev[y] = x; q.append(y)
print(f"  link graph from the '{NEEDLE}' article (hop, via-edge jaccard, shared tokens):")
for i in order:
    p = prev[i]
    if p is None:
        print(f"  hop 0  --      {mem[i]['publisher'][:18]:18} {mem[i]['headline'][:56]}")
        continue
    d, x = 1, p
    while prev[x] is not None: d += 1; x = prev[x]
    sh = sorted(toks[i] & toks[p])
    print(f"  hop {d}  j={clustering.jaccard(toks[i], toks[p]):.2f} "
          f"{mem[i]['publisher'][:18]:18} {mem[i]['headline'][:44]}  via {sh}")
EOF
```

The hop-1 → hop-2 boundary in that output is the production weld: the first edge whose shared
tokens name no film is the bridge this report reproduces synthetically above.

## 6. Smallest recommended fix

**No code change.** The countermeasure — cluster-aware linkage — is already implemented
(`clustering._quorum_ok`), already tested, already plumbed through an env var, and shipped
disabled *specifically pending this measurement* (`story_service.py:473`: "Measure a candidate
with `examples/audit_clustering_change.py --link-quorum 0.3` before enabling it anywhere").

1. **Measure** (read-only, command above). The audit prints the verdict against the bar fixed in
   the code before any measurement: adopt if the largest cluster is well down, droppedOut ≤ 5% of
   covered articles, and the story count does not fall; reject if droppedOut > 10% or stories
   fall (the `min_publishers` cliff).
2. **If ADOPT: set `RWE_CLUSTER_LINK_QUORUM=0.3` in `deploy/.env` and restart the engine.** One
   env line — no deploy, no code. This fixes the whole class (mega-cluster included), not just
   this story, and the reproduction above confirms it separates this exact case while keeping
   both resulting stories above the 2-article/2-publisher admission floor.
3. **If REJECT on dropped coverage** (the Berlin-pride failure mode — a legitimate 77-article
   story split into six at global quorum in the early mis-baselined run): fall back to the
   *targeted* variant, `RWE_STORY_REPAIR_QUORUM=0.3`, which applies the same rule only to
   clusters the geo signal already condemns and leaves every other story byte-identical
   (`story_service.py:597`). Honest limitation, visible in the forensic output's `trust` line: if
   this story's cluster is small and `unverified` rather than condemned, the targeted variant
   will not touch it — which is why the global quorum is the first choice and the audit run the
   deciding evidence.

*Not* recommended: adding box/office/collection/day to the stopword list (they are the
content-bearing tokens of a *legitimate* single-film box-office story — removing them would
un-cluster the very coverage the template genre correctly produces), raising `sim`/`min_shared`
(shown in §3 to destroy genuine stories before separating these), or re-titling rules (the
headline is a symptom; any member of a wrong cluster is the wrong title for someone).

---

## Measured on production (2026-08-03, 35,662-article window)

Both commands from §5 were run on the box. Production's real baseline, revealed by the audit's
tag line: `shared>=3, tokens>=3, repair 0.5, merge 0.33` — the box **already runs** the targeted
repair and the duplicate merge via env overrides (which is why the mega-cluster now tops out at
204, not the historical 486). The §1 table rows above are corrected accordingly.

### The weld, found

The Jana article lives in the **107-article / 58-publisher** "Zendaya Stuns 'Spider-Man: Brand
New Day' Premiere…" cluster — `trust=ok`, `geoCoherence=0.824`. The bridge chain is exactly the
reproduced mechanism, hop for hop:

```
hop 0  Koimoi              Jana Nayagan Box Office Day 11 BMS Sales…
hop 1  The Indian Express  Jana Nayagan Box Office Collection Day 7/9 Updates   j=0.30–0.32
hop 2  India Today         Spider-Man Brand New Day box office Day 2…           j=0.33
       via {box, crore, day, earns, film, office}   ← THE WELD: six shared tokens,
                                                       none naming a film, two of them currency
hop 3+ 100 legitimate Spider-Man articles                                       j=0.29–1.00
```

The same cluster also holds **Fortnite v41.30 patch notes** (welded through the
Fortnite×Spider-Man collab article on `{all, fortnite, skins}`) and a **Norway "Viking Row"
World-Cup fan piece** (through premiere fan coverage) — three foreign events inside one story the
trust signal approves. This settles the fallback question in §6.3: `repair` (running at 0.5!)
never fires on a trust-ok cluster, so **only the global link quorum can fix this class**.

### The quantification

At `--link-quorum 0.3` against the production baseline: **208 of 1,686 stories (12.3%) split** —
they are held together only by bridges fewer than 30% of cross-pairs support. That is the upper
bound on chained merges, and the split lists read as real welds: the Fauci cluster contained an
unrelated St. Paul police-chief story (15 articles); the "UPS earnings" cluster was
Apple + Corning + GE HealthCare + SoFi + transcript boilerplate; the sports blob mixed MLB trade
coverage, ATP previews and Polymarket promo codes. The trigger case resolves correctly: the
`--pieces` output shows "Jana Nayagan Box Office Collection Day 7 Updates…" re-emerging as its
own 3-article/2-publisher story. Side effects on identical rows: blindspot claims 214 → 231, and
49 legitimate same-event consolidations (Vincent Pastore, FIFA/Infantino, Japan quake, Idaho
shooting…) once the blobs release their members. Independent signal: 4/96 → 5/75 bad clusters
(mean 0.922 → 0.919) — one new bad cluster, worth re-checking at the adopted value.

### The verdict, read honestly

The CLI printed **REJECT: dropped 9.3%** of covered articles (741 of 7,926) against its 5%
default. The in-code policy bar (§6.1 / `story_service.py:483`) is *adopt ≤ 5%, reject > 10%* —
9.3% is the judgment band, and the audit's own attribution table shows the mix: six clusters
with the template signature (articles-per-publisher ≥ 4) account for **337 of the 741** —
WWE Raw 114 (a/p 12.8), an obituary blob of 166 articles from **two** publishers 91 (a/p 83.0),
a sports-betting blob 76, betting tips 30, earnings-call transcripts 18, one club template 8.
Those are drops the audit's own docs class as "the change working" (one outlet repeating a
format is not coverage). But the genuine losses are real too — Fauci 19, Zendaya 16, Purja 8,
Seattle 9 — so 0.3 does not ship.

### Next measurement: titrate the quorum down

The weld's support in the production graph is tiny: the Jana side has 3 members against a
32-member sample of the Spider-Man side, with ~2–6 passing cross-pairs out of ~96 ≈ **2–6%
support** — a quorum of 0.15, even 0.10, still severs it. The dense template blobs (whose
splitting causes most of the 9.3%) hold much higher internal support, so a lower quorum leaves
more of them intact — the status quo, not a regression — while still breaking single-bridge
welds. Find the largest value that lands ADOPT:

```bash
cd /opt/ih && sudo docker exec -i deploy-api-1 \
  python examples/audit_clustering_change.py --link-quorum 0.2 --show 12 --pieces 5
cd /opt/ih && sudo docker exec -i deploy-api-1 \
  python examples/audit_clustering_change.py --link-quorum 0.15 --show 12 --pieces 5
```

Adopt by setting `RWE_CLUSTER_LINK_QUORUM=<largest ADOPT value>` in `deploy/.env` and restarting
the engine; re-run the forensic print from §5 afterwards — the expected output is "no current
story carries that headline" once the Jana articles form their own story. The template blobs
that survive at the adopted value are a *different, pre-existing* defect (intra-template,
single-outlet — the obituary blob is the extreme case) best addressed separately, e.g. by an
articles-per-publisher gate; deliberately out of scope here.
