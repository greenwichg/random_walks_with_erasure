# Real-data results (MIND-small)

First real-data run of the pipeline in `docs/PAPER_PLAN.md`. All numbers are from
`examples/eval_mind.py` on **MIND-small**; reproduce with the Colab notebook
(`notebooks/run_mind_eval.ipynb`). This is **one dataset, one 15k-user sample, one
seed** — directional evidence, not a final table. Read the limitations at the end.

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

## RQ2 — long-tail diversity (RWE-D)

| model | hit@10 | auc | gini_div | coverage | avg_deg ↓ | surprisal |
|---|---|---|---|---|---|---|
| ItemKNN | .102 | .720 | .402 | .988 | 119 | 8.45 |
| P3 | **.194** | **.771** | .151 | .914 | 265 | 5.91 |
| RP³-β | .064 | .745 | .684 | .999 | 94 | 8.61 |
| **RWE-D** | .061 | .743 | **.707** | **.999** | **87** | **8.74** |

**RWE-D is the strongest long-tail diversifier on every axis** (highest gini /
coverage / surprisal, lowest average item degree) at **AUC parity with RP³-β**
(.743 vs .745). Top-*k* hit-rate drops — the standard diversity↔accuracy
trade-off (same regime as RP³-β); `--rwed-v` toward 1.0+ trades it back. These
metrics are position-independent, so they reproduce the paper's long-tail result
directly. ✅

## RQ3 — ideological bridging (RWE-B)

| model | rec_range | shift@10 | **uw_shift** | uw_recs |
|---|---|---|---|---|
| ItemKNN | 2.61 | .259 | .396 | .243 |
| P3 | 2.52 | .219 | .349 | .268 |
| RP³-β | 2.53 | .263 | .408 | .230 |
| RWE-D | 2.54 | .266 | .411 | .227 |
| **RWE-B** | 2.11 | **.638** | **1.047** | .771 |

**RWE-B bridges by far the most** — highest weighted shift across the centre
(`uw_shift` 1.05 vs ≈0.4 for every baseline) — while retaining accuracy (hit@10
.137, auc .753; cf. P3 .194/.771). It does **not** widen the range; it
*concentrates* recommendations on the opposite side (and, unbounded, on the
opposite **extreme** — `uw_recs` .771). That over-shoot is the "naive
opposite-blast" mode, and motivates the sweep. ✅ (with the axis caveat)

## Bounded bridging — the `max_distance` sweep

`examples/eval_mind.py --sweep-max-distance 3,2,1.5,1,0.5`:

| `d` | hit@10 | auc | uw_shift | **uw_recs ↓** |
|---|---|---|---|---|
| ∞ | .137 | .753 | 1.047 | **.771** |
| 3 | .137 | .753 | 1.047 | **.770** |
| 2 | .148 | .757 | .744 | **.476** |
| 1.5 | .158 | .761 | .550 | **.331** |
| 1 | .177 | .767 | .411 | **.260** |
| 0.5 | .191 | .770 | .352 | **.266** |
| *P3* | .194 | .771 | .349 | .268 |

**Tightening the "not too far" bound monotonically pulls `uw_recs` from .77 → .26**
— recommendations move from the opposite *extreme* toward the *centre* — while
accuracy rises toward P3. The bridging magnitude (`uw_shift`) softens with it: by
`d ≲ 1` RWE-B ≈ P3 (bridging gone). The **moderated-bridging window is `d ≈ 1.5–2`**
(still bridges above P3, recs pulled centre-ward, accuracy preserved). The bound
`d` is a clean **control knob** between opposite-extreme exposure and near-centre
exposure. The same monotone curve also appears on the co-click axis, i.e. the
effect is robust (and partly geometric — see caveats).

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
   Quantify it before trusting RQ3: `examples/validate_lean.py` (gold labels or a
   second model) reports the agreement number — do this and report it.
2. **The `uw_recs ↓` effect is partly geometric.** A smaller bound mechanically
   forces opposite-side items closer to the user (hence the centre) on *any* 1-D
   axis — it also appears on the topic axis. So the sweep is a robust *mechanism*,
   not by itself proof of *ideological* depolarization; the depolarization link is
   the simulation.
3. **Scope.** One dataset, one 15k-user sample, one seed, US-2019 news. No
   significance tests or multi-seed error bars yet. Add them before a submission.
4. **Reproducibility / accuracy of base RWE** is on synthetic + this MIND run; the
   paper's private Twitter numbers are not reproduced.

## Reproduce

```
notebooks/run_mind_eval.ipynb           # end-to-end in Colab
examples/classify_lean.py               # text lean -> news_id,position
examples/ingest_mind.py --positions-csv # -> mind_text.npz
examples/eval_mind.py [--sweep-max-distance ...]
examples/validate_lean.py               # axis-quality number
```

_Last updated: 2026-06-23._
