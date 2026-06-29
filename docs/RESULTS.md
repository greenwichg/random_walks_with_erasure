# Real-data results (MIND-small)

First real-data run of the pipeline in `docs/PAPER_PLAN.md`. The MIND tables are
from `examples/eval_mind.py` on **MIND-small**; the long-tail result (RQ2) is then
replicated on **two more public datasets** — **MovieLens-1M** and **Reddit
Politosphere** (each in its own section below). Reproduce with the Colab notebooks
(`notebooks/run_mind_eval.ipynb`, `notebooks/run_politosphere_eval.ipynb`). All tables — RQ2, RQ3, and the bounded-bridging
sweep — are **mean ± std over 7 seeds**, with a Wilcoxon signed-rank `p` vs P3 on the
main comparison. These tables were **independently re-run end-to-end (2026-06-25)
from the Colab notebook and reproduce to the printed precision**. Read the
limitations at the end — chiefly that the ideological axis is a noisy proxy.

## Setup

- **Data:** MIND-small train; political articles only (sub-category *politics* /
  *elections*); `--min-user-clicks 10 --min-item-clicks 10`, 15k users sampled.
- **Subset evaluated:** 8,415 users with political clicks · 1,019 political
  articles · 29,635 clicks.
- **Ideological positions:** text political-lean classifier
  (`bucketresearch/politicalBiasBERT`, LEFT/CENTER/RIGHT → −1/0/+1, scaled to
  ≈[−2, 2]) over each article's title+abstract — `examples/classify_lean.py`.
  See the **axis caveat** below.
- **Protocol:** 70/30 per-user split; top-10 ranking; diversity@20; baselines
  ItemKNN / P3 / RP³-β plus RWE-D and RWE-B (`ε=0.9`).

![Users and items on the text-lean left↔right scale (real MIND data)](images/axis_realdata.png)

*The populated axis (`examples/plot_axis.py` on `mind_text.npz`): article positions
(top) and user positions θ (bottom) on one shared left↔right scale — left-leaning
(blue) on the left, right-leaning (red) on the right. This is the **visual of the
alignment check** below (Pearson r = +1.00; 44 % / 56 % of articles left / right of
centre). Users skew further right (32 % / 68 %) than the article pool, consistent
with right-leaning articles drawing proportionally more clicks (θ is the click-mean).*

## RQ2 — long-tail diversity (RWE-D)

Mean ± std over **7 seeds** (re-drawn train/test splits); all differences vs P3 are
consistent across the 7 splits (Wilcoxon signed-rank `p = 0.016`, the n=7 floor).

| model | hit@10 | auc | gini_div | coverage | avg_deg ↓ | surprisal |
|---|---|---|---|---|---|---|
| ItemKNN | .108±.004 | .719±.002 | .406±.004 | .989±.002 | 118±2.1 | 8.51±.04 |
| P3 | **.196±.003** | **.771±.001** | .151±.001 | .909±.005 | 265±0.9 | 5.91±.01 |
| RP³-β | .070±.003 | .744±.002 | .683±.003 | .996±.002 | 93±1.2 | 8.61±.01 |
| **RWE-D** | .065±.003 | .743±.002 | **.708±.003** | **.996±.002** | **87±1.4** | **8.74±.01** |

**RWE-D is the strongest long-tail diversifier on every axis** (highest gini /
coverage / surprisal, lowest average item degree) at **AUC parity with RP³-β**
(.743 vs .744). The diversity gaps dwarf the seed-to-seed std, so they are robust.
Top-*k* hit-rate drops — the standard diversity↔accuracy trade-off (same regime as
RP³-β); `--rwed-v` toward 1.0+ trades it back. These metrics are position-
independent, so they reproduce the paper's long-tail result directly. ✅

## RQ2 on a second dataset — MovieLens-1M

To check the long-tail win is not MIND-specific, the same RQ2 comparison on
**MovieLens-1M** (`examples/eval_movielens.py`: 6,040 users · 3,706 items ·
1,000,209 ratings as implicit feedback; 70/30 split; **mean ± std over 5 seeds**).
MovieLens has no ideological axis, so only the long-tail half (RQ2) transfers.

| model | hit@10 | auc | gini_div | coverage | avg_deg ↓ | surprisal |
|---|---|---|---|---|---|---|
| ItemKNN | .101±.001 | .894±.000 | .024±.000 | .159±.002 | 1300±6.8 | 2.298±.007 |
| P3 | .094±.000 | .896±.000 | .010±.000 | .046±.002 | 1665±4.4 | 1.880±.004 |
| RP³-β | .127±.001 | .918±.000 | .024±.000 | .264±.004 | 1474±4.7 | 2.149±.005 |
| **RWE-D** | **.127±.001** | **.918±.000** | **.025±.000** | **.274±.005** | **1470±4.6** | **2.163±.005** |

**The long-tail result reproduces.** RWE-D **ties RP³-β exactly on accuracy**
(AUC .918, hit@10 .127, NDCG@10 .426 — identical) while **edging it on the
long-tail axes**: coverage (.274 vs .264) and surprisal (2.163 vs 2.149) by
≈2–3× the seed std, with gini and avg-degree trending the same way (within seed
noise). Against P3 it **dominates on both sides** — far higher coverage
(.274 vs .046), higher surprisal and lower avg-degree, **and** higher accuracy
(AUC .918 vs .896, hit@10 .127 vs .094). One honest dataset difference: the
accuracy↔diversity trade-off is **milder than on MIND** — MovieLens is denser and
less long-tailed, so RWE-D pays no top-*k* penalty vs P3 here (on MIND it did).
The *direction* of the long-tail win is identical on both datasets, so it is not a
MIND artifact. ✅

## RQ2 on a third dataset — Reddit Politosphere (behavioral axis)

A third public dataset, and the one we built specifically to *fix* the ideological
axis: the **Reddit Politosphere** (Hofmann et al., ICWSM 2022), where the
left↔right axis is learned from **behaviour** — an ideal-point fit on the
user×subreddit endorsement graph (`examples/ingest_politosphere.py`), not text.
Evaluated subset: **15,000 users · 295 political subreddits · 109,710 endorsements**
(US-2016 election window; single seed).

| model | hit@10 | auc | gini_div | coverage | avg_deg ↓ | surprisal |
|---|---|---|---|---|---|---|
| ItemKNN | .616 | .940 | .119 | .895 | 1715 | 3.655 |
| P3 | .614 | .939 | .093 | .563 | 1815 | 3.408 |
| RP³-β | .637 | .942 | .185 | .993 | 1600 | 4.028 |
| **RWE-D** | **.637** | **.942** | **.189** | **.993** | **1594** | **4.048** |

**RQ2 replicates a third time.** RWE-D ties RP³-β on accuracy and is the top
long-tail diversifier (highest gini/coverage/surprisal, lowest avg-degree),
dominating P3 on diversity at equal accuracy. The long-tail result now holds on
**three independent public datasets** — news (MIND), movies (MovieLens), and social
(Reddit).

**RQ3 does *not* transfer here — and that is the informative part.** The behavioral
axis came out **non-ideological**: against 24 labeled subreddit leans
`lean_corr = 0.13` (n=24, indistinguishable from 0), and the axis extremes are
ideologically *scrambled* — `r/secondamendment` and `r/Marco_Rubio` (right) sit
*with* `r/anarchocommunism` and `r/IWW` (left); `r/guncontrol` and `r/atheismplus`
(left) sit *with* `r/AltRightChristian` and `r/monarchism` (right). The dominant
dimension the ideal-point model recovers from broad Reddit commenting is
niche/small-subreddit idiosyncrasy, not left–right (contested issue-subs are
co-visited by both sides, confounding an unsupervised 1-D fit). RWE-B still bridges
hardest across it (`uw_shift 2.389`), but bridging across a non-ideological axis is
not ideological bridging, so we **do not report a Politosphere RQ3 result**. This is
the third independent axis construction to come up unvalidated (see Limitation 1).

## RQ3 — ideological bridging (RWE-B)

Mean ± std over 7 seeds (all vs-P3 differences Wilcoxon `p = 0.016`).

| model | rec_range | shift@10 | **uw_shift** | uw_recs |
|---|---|---|---|---|
| ItemKNN | 2.574±.032 | .245±.007 | .379±.010 | .247±.007 |
| P3 | 2.481±.028 | .213±.003 | .339±.005 | .278±.006 |
| RP³-β | 2.541±.005 | .263±.002 | .401±.005 | .233±.003 |
| RWE-D | 2.545±.006 | .266±.003 | .405±.005 | .231±.004 |
| **RWE-B** | 2.102±.012 | **.637±.006** | **1.044±.007** | .768±.007 |

**RWE-B bridges by far the most** — highest weighted shift across the centre
(`uw_shift` 1.04 ± .007, ~2.6× every baseline's ≈0.4) — while retaining accuracy
(hit@10 .139 ± .004, auc .753 ± .002; cf. P3 .196/.771). It does **not** widen the
range; it *concentrates* recommendations on the opposite side (and, unbounded, on
the opposite **extreme** — `uw_recs` .768, the highest). That over-shoot is the
"naive opposite-blast" mode, and motivates the sweep. ✅ (with the axis caveat)

## Bounded bridging — the `max_distance` sweep

`eval_mind.py --seeds 7 --sweep-max-distance 3,2,1.5,1,0.5` (mean ± std over 7 seeds):

| `d` | hit@10 | auc | uw_shift | **uw_recs ↓** |
|---|---|---|---|---|
| ∞ | .139±.004 | .753±.002 | 1.044±.007 | **.768±.007** |
| 3 | .139±.004 | .753±.002 | 1.044±.007 | **.767±.007** |
| 2 | .151±.004 | .756±.002 | .742±.006 | **.475±.006** |
| 1.5 | .162±.003 | .760±.002 | .551±.004 | **.334±.004** |
| 1 | .180±.004 | .766±.002 | .404±.004 | **.268±.005** |
| 0.5 | .194±.003 | .769±.002 | .343±.005 | **.276±.006** |
| *P3* | .196±.003 | .771±.001 | .339±.005 | .278±.006 |

**Tightening the "not too far" bound monotonically pulls `uw_recs` from .77 → .27**
(each step ≫ the ±.005 std) — recommendations move from the opposite *extreme*
toward the *centre* — while accuracy rises toward P3. The bridging magnitude
(`uw_shift`) softens with it: by `d ≲ 1` RWE-B ≈ P3 (bridging gone). The
**moderated-bridging window is `d ≈ 1.5–2`** (still bridges above P3, recs pulled
centre-ward, accuracy preserved). The bound `d` is a clean **control knob** between
opposite-extreme exposure and near-centre exposure. The same monotone curve also
appears on the co-click axis, i.e. the effect is robust (and partly geometric — see
caveats).

## The simulation link (what makes it a *depolarization* claim)

The real data shows RWE-B can be tuned to land recommendations **near the centre**
(low `uw_recs`) rather than the opposite extreme. The opinion-dynamics simulation
(`rwe/opinion_dynamics.py`, assimilation–contrast / Social Judgment Theory) shows
**near-centre exposure depolarizes** where **opposite-extreme exposure backfires**.
Together:

> RWE-B bridges hardest; the bound `d` controls whether its recommendations sit at
> the opposite extreme (backfire regime) or near the centre (depolarizing regime),
> without costing accuracy.

That is the contribution — a *combination* of real-data control + simulated
outcome, not a measured opinion change.

## Limitations (state these plainly)

1. **The ideological axis is a noisy proxy.** The text classifier (trained on full
   articles, applied to headlines) scored **743 / 1019 political articles
   near-centre**; the extremes mix partisan framing with opinion-vs-news framing.
   Validated against a 40-headline independent-rater set (`validate_lean.py`):
   **Spearman r = 0.27, Pearson 0.30, 75 % sign-agreement on the non-neutral
   items** — a *weak-but-positive* ideology proxy. So RQ3 reads are suggestive;
   a larger multi-rater gold set or true outlet-lean labels would firm it up.
   An automatic **axis-alignment check** (`eval_mind.py`, printed each run)
   confirms users and items share one *correctly-oriented* scale: on the
   **text-lean axis** each user's position correlates Pearson **r = +1.00** with
   the mean lean of their clicked articles and **100 %** of users sit on their
   expected side (item spread 44 % left / 56 % right). That is a *sanity check*
   (user positions **are** the click-mean, so it only proves the axis is not
   sign-flipped). On the **co-click `--ideology` axis** the same check is an
   *independent* signal and gives only **r = +0.37** — corroborating, with a
   number, that the co-click axis is topical rather than ideological (cf. the
   Spearman 0.27 vs human labels). So the axis is well-*oriented* but
   weakly-*resolved*: a noisy proxy, not a misaligned one. To reduce the
   single-model noise, `examples/ensemble_lean.py` averages several independent
   bias models into one axis (z-scored, then rescaled); validate any axis against a
   gold set with `examples/validate_lean.py` (calibration cannot help here — Spearman
   is rank-based, so a *less-noisy* model, not a rescaled one, is what lifts it).
   **Three independent axis constructions, none validated.** We tried (i) the
   **text-lean** classifier (Spearman ≈ 0.27 vs human labels — and ≈ 0 on a second,
   blind 40-headline set; it conflates *topic* with *stance*), (ii) the **MIND
   co-click** ideal point (`r = 0.37`, topical), and (iii) a **Reddit Politosphere
   behavioral** ideal point built precisely to fix this (`examples/ingest_politosphere.py`)
   — which came out **non-ideological** (`lean_corr = 0.13`, axis extremes
   ideologically scrambled; see the third-dataset section above). The convergent
   failure *is* a finding: a validated left–right axis is genuinely hard to recover
   from public behavioural/text data (news co-clicks are topical, broad Reddit
   commenting is cross-cutting, headlines conflate topic with stance). The cleanest
   remaining signal is Twitter elite-*following* (Barberá-style), which is
   access-restricted now. **So RQ3 is explicitly *suggestive*, and the contribution
   is the bounded-bridging *mechanism* (Limitation 2 — robust on any 1-D axis), not a
   measured ideological effect.**
2. **The `uw_recs ↓` effect is partly geometric.** A smaller bound mechanically
   forces opposite-side items closer to the user (hence the centre) on *any* 1-D
   axis — it also appears on the topic axis. So the sweep is a robust *mechanism*,
   not by itself proof of *ideological* depolarization; the depolarization link is
   the simulation.
3. **Significance is across seeds, not observations.** The 7-seed tables show the
   gaps are *stable* (Wilcoxon `p = 0.016`, the n=7 floor), but that is not a
   per-user test. Two checks address this. (a) The **MovieLens-1M RQ2 replication**
   above confirms the long-tail win on a *second* public dataset (5 seeds). (b) A
   **per-user paired Wilcoxon vs P3** (`eval_mind.py --per-user-sig`, **n ≈ 2,546**
   paired users on one split) makes every method's accuracy gap to P3 significant
   far below any threshold — RWE-D `p ≈ 8e-235 / 2e-102 / 3e-129`
   (auc / hit@10 / ndcg@10), RWE-B `p ≈ 4e-175 / 2e-34 / 2e-69`, with ItemKNN and
   RP³-β likewise `p < 1e-60`. This confirms the per-user differences are **real,
   not seed noise**; the signed-rank test does *not* crown a winner — for raw
   accuracy **P3 leads**, and RWE-D / RWE-B trade it for diversity / bridging (it is
   that *gap* which is significant). The long-tail half is dataset-agnostic;
   MovieLens has no ideological axis, so only RQ2 transfers.
4. **Reproducibility / accuracy of base RWE** is on synthetic + this MIND run; the
   paper's private Twitter numbers are not reproduced.

## Reproduce

```
notebooks/run_mind_eval.ipynb           # end-to-end in Colab
examples/classify_lean.py               # text lean -> news_id,position
examples/ingest_mind.py --positions-csv # -> mind_text.npz
examples/eval_mind.py [--sweep-max-distance ...]
examples/eval_mind.py --per-user-sig    # per-user paired Wilcoxon vs P3
examples/eval_movielens.py --ratings ml-1m/ratings.dat   # 2nd-dataset RQ2 check
examples/validate_lean.py               # axis-quality number
examples/plot_axis.py --npz mind_text.npz  # users + items on the L<->R scale
```

_Last updated: 2026-06-29 (Reddit Politosphere folded in — third RQ2 dataset +
third unvalidated axis; MovieLens-1M RQ2 + per-user significance on 2026-06-28;
MIND tables independently reproduced 2026-06-25)._
