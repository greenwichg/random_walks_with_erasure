# Content-mill stories: Option B challenged — and rejected again

**Question:** should the articles-per-publisher (a/p) admission gate ("Option B" in
`docs/STORY_CLUSTER_MERGES.md`) be implemented to remove the template/content-mill story class
(WWE dailies, obituary feeds, betting tips, transcripts)?
**Method:** read-only probe on the live post-quorum catalog (2026-08-03, 35,793 articles, 1,814
stories, 7,505 covered): template class identified by member-level format voting (a 9-pattern
lexicon — a *measurement instrument*, not a shipping heuristic), Option B simulated at four
thresholds with its full blast radius printed, three alternatives measured on the same build
(registry curation, per-publisher cap, rank-demotion/do-nothing), plus syndication structure and
the default-ranking harm baseline.
**Prior art:** `docs/PUBLISHER_CONCENTRATION_EVALUATION.md` (2026-07-27) already evaluated this
exact gate pre-quorum and rejected it at precision 0% / recall 0%. Option B was re-proposed in
the quorum verification's watch list without re-checking that document — this evaluation
corrects that.
**Verdict in one line:** **do not implement Option B.** The class is *correctly clustered
non-news* (every scored instance at coherence 1.00), a/p sees only one of its two shapes and
misses the single mill story that actually ranks, the thresholds that catch anything run
through real rolling-news at the 2.5 end — and the do-nothing baseline is already strong
(top-20 clean). The smallest effective change is **source curation** (registry rows / feed-list
review), which removes ~2/3 of the class with zero threshold risk and zero-to-two lines of code.

---

## 1. The class, measured

41 template stories, 398 articles — **5.3% of covered**. By format: obituary 15 stories,
betting 9, WWE-daily 9, lottery 3, TV-airdate/synd-wrap/transcript/gaming/box-office 1 each.
(Lexicon noise, stated: ~3 flagged stories are real news that mention betting words — the
Kalshi lawsuit, a Santos fine; ~2 mills escaped the lexicon — an earnings-digest pair, a Turkish
daily-weather template. Roughly cancels.)

Hypothesis tests the probe settles:

| hypothesis | verdict | evidence |
|---|---|---|
| the class is false merges / sparse chaining | **no** | all 11 scored flagged stories at geoCoherence **1.00**; density 0.52 vs 0.61 for real stories — barely distinguishable. Correctly clustered non-news, exactly the prior evaluation's category error |
| merge/repair heuristics produce it | **no** | mills are single-component primary clusters (earlier probe: `c=1`) |
| syndication is the discriminator | **no** | 540 cross-publisher duplicate-headline groups catalog-wide, led by *real* stories (Pastore 5, Idaho 4) — syndication is how wire news works |
| high a/p is the cause | **half** | mean a/p 3.64 vs 1.16 — but the class has **two shapes**: few-publisher repetition (obits 27/2, betting 33/2 — a/p visible) and broad syndication (Mega Millions 28 articles / **15 publishers**, a/p 1.9 — a/p **invisible**) |
| identifiable publisher behavior | **yes** | 4 outlets account for 57% of flagged articles, ~10 for ~76%; several are 78–100% mill-output feeds |

## 2. Option B, quantified

a/p distribution: p50 1.00, p95 2.00, **p99 3.00**, max 16.5 — better separated than the
pre-quorum catalog (p99 2.29), but the threshold still has no safe useful setting:

| threshold | removes | of which real news (read from the printed blast radius) | recall on the class |
|---|---|---|---|
| > 2.5 | 29 stories / 393 arts (5.2%) | **≥ 6 real stories**: Apple earnings 6/2, AI sell-off 8/3 (d 1.00), Tarik Skubal trade-deadline **26/10**, Elizabeth Waddell 13/5, a fatal Co Derry crash 6/2, Commonwealth Games rolling coverage 8/3 — all coherence 1.00 | 17/41 stories |
| > 3.0 | 17 / 282 (3.8%) | ~0 *on this window* (the 2 "non-template" hits are lexicon-missed mills) — but 3.0 sits exactly at p99, and the prior evaluation's structural finding stands: the moment the threshold matters it runs through live-blog news | 15/41 stories |
| > 4.0 / 5.0 | 15/268 · 10/190 | ~0 | 14 · 9 of 41 |

**The recall failure is the disqualifier, not just the false positives.** Option B misses 24–26
template stories holding 123+ articles — including **the only mill story in the default
top-50**: Mega Millions winning numbers at rank **#22**, a/p 1.9, because fifteen legitimate
outlets each print the lottery numbers once or twice. The broad-syndication shape of the class
is structurally invisible to a concentration gate. And the endgame is worse: a perfectly
clustered obituary (one deceased, two syndication mirrors) is a 2-article/2-publisher story at
a/p 1.0 — the *better* clustering gets, the less a/p can see the class at all.

## 3. The alternatives, measured on the same build

**C1 — source curation (recommended if acting).** The class is source-concentrated:
Sportskeeda (two name forms) 122 flagged articles at 66% mill-share of its window output;
The Oregonian 70 at **90%** (its ingested feed is dominated by obituaries); Daytondailynews /
Obits.Lehighvalleylive / Mlive / Obits.Oregonlive / Wkyc at **100%**; Springfieldnewssun 83%;
Nwfdailynews 78%; Seeking Alpha 68% (transcripts). Excluding the ~10 dedicated-mill sources
removes **≈ 250–280 flagged articles (~65–70% of the class), including every obituary,
transcript, and daily-wrap feed** — with zero threshold risk, by construction. Second-order
bonus: most 2-publisher betting/obit stories lose their partner and fall below
`min_publishers`. What curation cannot reach: the NY Post betting desk (44 flagged articles,
10% of a real outlet's output — cannot exclude the outlet) and the broad-syndication lottery
story (a content-policy question, not a source question). Mechanism already ships:
`EXCLUDED_KINDS` removes curated kinds from clustering while the articles stay on Discover,
search, and publisher pages. `kind=wire` ("machine-generated feed") is semantically exact for
obit/transcript/wrap feeds — those rows are **data-only, zero code**. Sportskeeda is
human-written; if excluding it, the honest labelling is a new `kind` (e.g. `mill`) added to two
constant tuples in `outlet_registry.py` — a two-line change. Cleanest of all for the obit
feeds: review the ingestion feed list (the prior evaluation's own recommendation — "that
belongs at ingestion") and stop pulling obituary/wrap feeds at the source.

**C2 — per-publisher cap: rejected.** k=2 trims 215 mill articles but **310 real ones**
(Zendaya −11, Skubal −10, **Japan earthquake −9**, Purja −6 — rolling coverage of real events
is exactly what a cap taxes); k=3 still trims 110 real (quake −5, Purja −3). And it deletes no
mill story — the obit cluster survives, smaller. Wrong shape entirely.

**C3 — rank demotion / do-nothing: stronger than expected.** Template stories in the default
top-20: **0**. Top-50: 2, one of which is real news (the Kalshi lawsuit — lexicon noise), so
the true figure is **one mill story at #22**. The quorum already demoted the class
structurally: mills have few publishers and the ranking is publisher-first. The 5.3% of covered
articles is real but almost entirely below the fold. (Residual unknown, stated: topic-scoped
browsing — e.g. the Sports tab — will rank WWE/betting stories higher within their topic; not
measured here.)

## 4. Comparison

| approach | class removed | real-news damage | code | verdict |
|---|---|---|---|---|
| **B: a/p gate @2.5** | 393 arts, 41% of class stories | ≥ 6 real stories incl. 26/10 | new admission gate | **reject** |
| **B: a/p gate @3.0** | 282 arts, 37% of class stories | ~0 this window; threshold at p99, prior eval says it misfires when it matters; misses the #22 lottery story forever | new admission gate | **reject** |
| **C1: curate ~10 sources** | ~250–280 arts incl. all obit/transcript/wrap feeds | 0 by construction (curated, auditable, reversible) | 0 lines (kind=wire rows) or 2 (new kind) | **adopt if acting** |
| **C2: per-publisher cap** | shrinks, deletes nothing | 110–310 real articles from live coverage | new mechanism | **reject** |
| **do nothing** | — | — | — | **defensible**: top-20 clean, one mill at #22 |

## 5. Recommendation

1. **Option B is rejected** — for the second time, by two independent evaluations on two
   different catalogs. The prior evaluation's sentence stands verbatim: a concentration gate is
   a content filter wearing a clustering costume; and the post-quorum measurement adds the
   sharper fact that the gate cannot see the only class member users actually encounter.
2. **If the class should shrink, curate sources**: registry `kind` rows for the ~8
   machine-generated feeds (data-only), an operator review of the ingestion feed list for
   obituary/wrap feeds (zero code, fixes the true cause), and a product decision on Sportskeeda
   (new `kind=mill`, two constants) and the NY Post betting desk. Measure with the standard
   before/after audit; a curation change drops only what was curated, so the verdict is
   read-not-computed.
3. **Doing nothing is acceptable**: the quorum already removed the class from the surfaces that
   matter most. The one visible offender (lottery results) is a content-policy question —
   fifteen real outlets publish it — not a clustering or curation defect.

**What would change the verdict on Option B:** a mill shape that is high-a/p, source-diverse,
and template-lexicon-invisible — none observed in either evaluation window — or a catalog where
the a/p distribution opens a gap (p99 ≪ threshold ≪ mill floor) that holds across windows.
