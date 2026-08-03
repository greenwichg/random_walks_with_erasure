# Catalog-wide verification: `RWE_CLUSTER_LINK_QUORUM=0.2` in production

**Scope:** global verification of the cluster-aware linkage adopted per
`docs/STORY_CLUSTER_MERGES.md` — metrics vs expected baseline, stratified story sampling across
topics/publishers/sizes, grouping integrity, over-splitting and residual-weld detection.
**Method:** two read-only runs on the box (2026-08-03, ~35,800-article window): the audit CLI in
*reverse* (`--link-quorum 0`, so the after-side is the single-linkage counterfactual and the diff
shows what the quorum is actively preventing), and an in-process probe that builds the catalog
both ways from identical rows and computes per-story title-graph internals — pairwise-gate
density, chain depth from the representative (`hop`), members with < 20% support against their
own cluster (`loose` — the residual-weld metric), and connected components (> 1 = joined by the
description-based merge pass). No code was modified; the probe ran from stdin.
**Date:** 2026-08-03, quorum live since the same day.

**Verdict in one line:** the adoption is working catalog-wide, not just on the trigger case —
weld risk collapsed (loose members −88%, deep chains −93%, density up, coherence up, blindspot
claims up) while same-event grouping *improved* (the merge pass consolidates duplicates it could
never reach inside the blobs); the costs are the measured coverage trade (−5.5% on identical
rows, half of it template-blob deflation) and a bounded over-split inventory (38 candidate pairs
≈ 2% of stories, dominated by one saga and a few unmerged duplicates).

---

## 1. Metrics vs expected baseline

Three independent measurements agree within catalog drift (the window rolls continuously):

| source | stories | largest | bad clusters | mean coherence |
|---|---:|---:|---:|---:|
| pre-change production (titration day) | 1,687 | 204 | 4/96 | 0.922 |
| titration prediction for 0.2 | 1,812 | 66 | 4/87 | 0.932 |
| flagless audit, post-restart | 1,815 | 66 | 4/87 | 0.932 |
| verification probe (this doc) | 1,813 | 67 | 4/87 | 0.932 |

The running configuration **is** the measured configuration.

## 2. Identical-rows comparison: production (0.2) vs single-linkage counterfactual (0.0)

Both catalogs built from the same 35,808 rows in one process:

| metric | quorum 0.0 | quorum 0.2 | reading |
|---|---:|---:|---|
| stories | 1,690 | 1,813 | +123 real events surfaced |
| covered articles | 7,950 | 7,510 | −5.5%, the measured trade (≈½ template blobs, §5) |
| largest cluster | 196 | 67 | the blob is gone |
| stories ≥ 20 articles | 45 | 29 | fewer giants… |
| stories 10–19 | 79 | 102 | …more real mid-size events |
| publishers ≥ 10 | 68 | 57 | chaining *accumulates* publishers; some big counts were weld inflation |
| chain depth ≥ 3 (n ≥ 4) | 140 | 122 | −13% |
| **chain depth ≥ 5** | **29** | **2** | **−93% — deep chaining eliminated** |
| **loose members (< 20% support, n ≥ 6)** | **1,520 in 129 stories** | **182 in 87** | **−88% — the weld-risk metric** |
| median title-graph density (n ≥ 6) | 0.53 | 0.60 | clusters are internally denser |
| multi-component stories (merge-pass joins) | 31 | 40 | the merge pass reaches *more* duplicates (§3) |
| blindspot claims (audit, same rows) | 214 | 229 | +15 from clusters honest enough to claim on |
| independent signal (audit, same rows) | 5/97 bad, 0.918 | 4/87 bad, 0.932 | quality favors 0.2 |

## 3. Related articles remain grouped — and grouping *improved*

The reverse audit's split table is the unexpected, decisive evidence: removing the quorum does
not merely re-weld blobs, it **fragments today's consolidated stories**. Under single linkage,
Vincent Pastore's death is *5 stories* (it is 1 today, 35 articles / 32 publishers), the Japan
earthquake is 4 (1 today), Hamas disarmament 5 (1), Ceuta 5 (1), Todd Blanche 3 (1). Mechanism:
the description-based merge pass requires *complete* linkage, which it could never achieve while
duplicate events sat inside different mega-blobs; cleaner primary clusters give it joinable
inputs. The quorum therefore improved recall, not only precision.

Anchor checks (all correct, production build):

| anchor | today | status |
|---|---|---|
| Jana Nayagan | 3 arts / 2 pubs, own story, Jana headline | weld severed, trigger case resolved |
| Spider-Man | 67 / 41, coh 0.83, Spider-Man headline | correctly titled, no foreign events found |
| Pastore / Japan quake / Idaho / Ceuta / Hamas | 35/32 · 34/18 · 31/24 · 24/17 · 28/20 | single consolidated stories |
| Fauci | 30/19 subpoena story (saga in beats — known trade) | see §6 |
| WWE | 29/7 template cluster | contained, pre-existing class |

Without the quorum, 104 of today's stories would re-weld into blobs — the top eight re-welds are
the WWE (195 from 8 stories), sports-betting (194 from 16), obituary (166 from 12), Fauci (125
from 14, re-absorbing the unrelated St. Paul police-chief story), Zendaya/Spider-Man (108 from
9, re-absorbing the Jana story), Purja, earnings, and wildfires clusters.

## 4. Stratified samples (production build)

Read `d` = pairwise-gate density, `hop` = chain depth, `loose` = low-support members.

*Largest 5:* Zendaya 67/41 (d 0.42, hop 3, loose 12, coh 0.83 — §6), Purja 37/24 (0.63/2/1),
Pastore 35/32 (0.65/2/0, coh 1.00), Japan quake 34/18 (0.56/2/0, 1.00), and one pre-existing
template blob (FC Midtjylland betting, 33 articles / **2** publishers). Four of five are dense
real events.
*Median band (n = 8):* densities 0.54–0.86, hop ≤ 2, loose ≤ 1, coherence 1.00 across U.S. /
Business / Politics / World — healthy.
*Small (n = 3–5):* densities 0.40–1.00, hop ≤ 2 — healthy; two carry legitimate merge-pass
joins (c = 2).
*Top-6 topics:* Politics (243 stories), Sports (240), Business (209), U.S. (136), World (108),
untopiced (587) all sampled; the only unhealthy largest-of-topic entries are the two 2-publisher
template blobs (Sports betting; the "(none)" syndication pair), both pre-existing class.

## 5. The coverage trade, attributed

−440 net covered articles on identical rows. From the counterfactual's re-weld table, roughly
half sits in the four content-mill blobs (WWE, obituaries 166/2, betting, earnings transcripts)
whose fragments fail `min_publishers` — the drops the audit's own docs class as the change
working. The remainder is the long tail of 2-article satellite pieces around real events. In the
*other* direction, 84 articles are covered **only** because of the quorum (consolidations that
single linkage buries).

## 6. Regressions found (all bounded, none blocking)

1. **Over-split inventory: 38 candidate pairs (2.1% of stories)** — separate stories whose
   representative headlines still pass the pairwise gate. Read sample classifies them:
   * *Real fragmentation, one saga:* the FIFA/UEFA World-Cup-investment story is ~5–6 stories
     (30/21 core + satellites at j 0.31–0.38). The merge pass has partially consolidated it and
     may finish as coverage accretes; the beats are at least individually coherent.
   * *Unmerged true duplicates (merge-pass recall, pre-existing):* "Rand Paul releases Fauci
     diaries" twice (8/6 vs 7/6), US GDP slowdown (6/6 vs 3/3), an Idaho 2-article remnant.
   * *Correct separations flagged by the detector's design:* "Russian strikes kill at least 8
     across Ukraine" vs "Israeli strikes kill at least four" (j 0.33 on template phrasing,
     different wars) — evidence the *pairwise gate alone* still cannot be trusted, i.e. the
     quorum is doing exactly its job.
2. **Saga beats:** Fauci is 13–14 beat-stories (subpoena / diary release / hearing / contempt),
   correctly excluding the unrelated St. Paul story. Known trade, accepted at adoption.
3. **Residual welds at 0.2:** the Zendaya story keeps 12 loose members — all film-ecosystem
   satellites (interviews, reviews, tie-ins), no foreign events found; the two remaining
   hop-≥ 5 chains include the Elizabeth Waddell true-crime story (13/5, d 0.23) — same case
   throughout, but the loosest structure in the catalog. Watch items, not defects.
4. **Template blobs persist** (WWE 29/7, obituary residue 29/2, betting 33/2) — the
   pre-existing class this change deliberately did not target; Option B (articles-per-publisher
   admission gate) remains the deferred fix.

## 7. Conclusion

Every verification axis passes: metrics match the measured prediction; weld risk is down an
order of magnitude on identical rows; related coverage is grouped *better* than under single
linkage (five consolidated anchors that fragment without the quorum); over-splitting is a
bounded 2% inventory dominated by one saga and a known recall limitation; the independent
quality signal and blindspot yield both improved. The coverage cost is the one measured and
accepted at adoption, half of it content-mill deflation. Watch list for the next routine audit:
the FIFA saga's consolidation, the Zendaya satellites, Waddell-style deep chains, and —
worth a measured look separately — whether `RWE_STORY_MERGE_SIM` slightly below 0.33 closes the
duplicate pairs in §6.1 without waking the blob (its own adopt/reject bar is already documented
in `story_service.merge_similarity()`).
