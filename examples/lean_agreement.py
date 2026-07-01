"""Article-level RELIABILITY of the text-lean axis: how much do two independent
bias models agree on a *single* MIND headline?

The text-lean pipeline scores each article's lean from its title + abstract
(``classify_lean.py``). That per-article label is the finest-grained cell in the
whole design, and the honest question a reviewer asks is: *is one article's label
trustworthy, or only the population aggregate?* Outlet-level raters (AllSides,
Ad Fontes) sidestep this by rating a whole outlet with human panels; we score
each short headline with one model, so we should MEASURE the per-article noise
rather than assume it away.

This script takes 2+ ``news_id,position`` CSVs from ``classify_lean.py`` run with
DIFFERENT ``--model``\\s (e.g. ``lean.csv`` = politicalBiasBERT, ``lean_b.csv`` =
premsa/AllSides-BERT -- the two the notebook already builds for the ensemble) and,
for every pair, reports agreement two ways:

* **continuous** -- Spearman (rank) and Pearson (linear) over the shared articles;
* **categorical** -- bucket each score into Left / Center / Right, then Cohen's
  kappa (chance-corrected agreement on the discrete label), exact-agreement %, the
  3x3 confusion matrix, and the **side-flip rate** (share of articles one model
  calls Left and the other calls Right -- the most damning disagreement). Plus a
  2-class kappa on side only (centers dropped), since "does it even get the side
  right" is the question that matters for the ideological axis.

Two independent "experts" disagreeing at the article level is direct evidence the
single-article label is noisy (and hence that the axis is only reliable in
aggregate). High agreement is the opposite. Either way it's a number, not a vibe.

    python examples/classify_lean.py --mind-dir MIND --political-only --out lean.csv
    python examples/classify_lean.py --mind-dir MIND --political-only \
        --model premsa/political-bias-prediction-allsides-BERT \
        --label-positions=-1,0,1 --out lean_b.csv
    python examples/lean_agreement.py lean.csv lean_b.csv

Bucketing: the pipeline's default ``--scale 2`` puts pure-class points at +-2, so
``--band 1.0`` (default) means "nearest class" (|score| <= 1 -> Center). If your
files use a different scale the marginal label distribution printed per model will
look lopsided (e.g. 98% Center) -- then pass ``--band`` to match, or ``--terciles``
to bucket each model by its OWN 33/66 quantiles (scale-free, no threshold).
"""

import argparse
import os

import numpy as np

from rwe.mind import _load_positions_map

_NAMES = {-1: "L", 0: "C", 1: "R"}


def bucketize(x, band=1.0, terciles=False):
    """Continuous lean -> {-1: Left, 0: Center, +1: Right}; NaN stays NaN.

    Default: a Center dead-zone of half-width ``band`` in position units. With
    ``terciles=True`` the thresholds are the score's own 33rd/66th percentiles, so
    the split is scale-free (but forces ~1/3 into each bucket)."""
    x = np.asarray(x, dtype=float)
    fin = np.isfinite(x)
    if terciles:
        lo, hi = np.nanpercentile(x, [100 / 3, 200 / 3])
    else:
        lo, hi = -abs(band), abs(band)
    out = np.full(x.shape, np.nan)
    out[fin & (x <= lo)] = -1
    out[fin & (x >= hi)] = 1
    out[fin & (x > lo) & (x < hi)] = 0
    return out


def cohens_kappa(a, b, labels):
    """Cohen's kappa for two raters over the given ordered ``labels``.

    Returns ``(kappa, observed_agreement, confusion)`` where ``confusion[i, j]`` is
    the count of items rater-a called ``labels[i]`` and rater-b called ``labels[j]``.
    kappa = (p_o - p_e) / (1 - p_e): 1 = perfect, 0 = chance, <0 = worse than chance.
    """
    idx = {lab: i for i, lab in enumerate(labels)}
    C = np.zeros((len(labels), len(labels)), dtype=float)
    for x, y in zip(a, b):
        C[idx[x], idx[y]] += 1
    n = C.sum()
    if n == 0:
        return float("nan"), float("nan"), C
    po = np.trace(C) / n
    pe = float(((C.sum(1) / n) * (C.sum(0) / n)).sum())
    kappa = (po - pe) / (1 - pe) if pe < 1 else float("nan")
    return kappa, po, C


def pair_reliability(a, b, band=1.0, terciles=False):
    """All article-level agreement stats for two aligned score vectors.

    ``a``/``b`` are same-length arrays (NaN where a model didn't score the article);
    stats are computed over the subset finite in BOTH. Returns a dict of numbers."""
    from scipy.stats import pearsonr, spearmanr

    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    m = np.isfinite(a) & np.isfinite(b)
    n = int(m.sum())
    r = {"n": n, "spearman": float("nan"), "pearson": float("nan"),
         "kappa3": float("nan"), "exact": float("nan"), "confusion": None,
         "kappa_side": float("nan"), "flip_rate": float("nan"),
         "marg_a": None, "marg_b": None}
    if n < 3:
        return r
    av, bv = a[m], b[m]
    if av.std() > 0 and bv.std() > 0:
        r["spearman"] = float(spearmanr(av, bv)[0])
        r["pearson"] = float(pearsonr(av, bv)[0])

    ba = bucketize(av, band, terciles).astype(int)
    bb = bucketize(bv, band, terciles).astype(int)
    kappa3, po, C = cohens_kappa(ba, bb, labels=(-1, 0, 1))
    r["kappa3"], r["exact"], r["confusion"] = kappa3, po, C
    r["marg_a"] = {lab: int((ba == lab).sum()) for lab in (-1, 0, 1)}
    r["marg_b"] = {lab: int((bb == lab).sum()) for lab in (-1, 0, 1)}

    # side-only (drop items either model called Center): does it get L vs R right?
    side = (ba != 0) & (bb != 0)
    if side.sum() >= 3 and len({*ba[side]}) > 0 and len({*bb[side]}) > 0:
        ks, _, _ = cohens_kappa(ba[side], bb[side], labels=(-1, 1))
        r["kappa_side"] = ks
    # flip rate over ALL shared articles: one says Left, the other Right
    flips = ((ba == -1) & (bb == 1)) | ((ba == 1) & (bb == -1))
    r["flip_rate"] = float(flips.mean())
    return r


def _fmt_confusion(C):
    labs = [-1, 0, 1]
    head = "        b:L     b:C     b:R"
    rows = [head]
    for i, la in enumerate(labs):
        cells = "  ".join(f"{int(C[i, j]):6d}" for j in range(3))
        rows.append(f"  a:{_NAMES[la]}  {cells}")
    return "\n".join(rows)


def _load_aligned(files):
    """Stack the CSVs into an ``(article, model)`` matrix aligned on the id union."""
    maps = [_load_positions_map(f) for f in files]
    ids = sorted({nid for m in maps for nid in m})
    pos = {nid: i for i, nid in enumerate(ids)}
    M = np.full((len(ids), len(maps)), np.nan)
    for j, m in enumerate(maps):
        for nid, v in m.items():
            M[pos[nid], j] = v
    return ids, M


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+", help="2+ news_id,position CSVs (one per model)")
    ap.add_argument("--band", type=float, default=1.0,
                    help="Center half-width in position units (default 1.0, correct "
                         "for the pipeline's [-2,2] scale); ignored with --terciles")
    ap.add_argument("--terciles", action="store_true",
                    help="bucket by each model's own 33/66 quantiles (scale-free)")
    ap.add_argument("--out", default=None,
                    help="optional CSV of per-article disagreements for auditing")
    args = ap.parse_args()
    if len(args.files) < 2:
        ap.error("give at least two lean CSVs to compare")

    ids, M = _load_aligned(args.files)
    names = [os.path.basename(f) for f in args.files]
    scheme = "terciles (scale-free)" if args.terciles else f"band=+-{args.band:g}"
    print(f"article-level reliability over {len(ids)} articles (id union); "
          f"L/C/R buckets by {scheme}\n")

    for x in range(M.shape[1]):
        for y in range(x + 1, M.shape[1]):
            r = pair_reliability(M[:, x], M[:, y], args.band, args.terciles)
            print(f"=== {names[x]}  vs  {names[y]}   (n={r['n']} scored by both) ===")
            if r["n"] < 3:
                print("  too few shared articles for a stable estimate\n")
                continue
            print(f"  continuous : Spearman {r['spearman']:+.3f}   "
                  f"Pearson {r['pearson']:+.3f}")
            print(f"  L/C/R      : Cohen kappa {r['kappa3']:+.3f}   "
                  f"exact agreement {100 * r['exact']:.0f}%")
            print(f"  side only  : Cohen kappa {r['kappa_side']:+.3f}  (Left vs Right, "
                  f"Centers dropped)")
            print(f"  SIDE FLIPS : {100 * r['flip_rate']:.1f}%  "
                  f"(one model Left, the other Right)")
            ma, mb = r["marg_a"], r["marg_b"]
            print(f"  labels {names[x]}: L={ma[-1]} C={ma[0]} R={ma[1]}   "
                  f"{names[y]}: L={mb[-1]} C={mb[0]} R={mb[1]}")
            print(_fmt_confusion(r["confusion"]))
            k = r["kappa3"]
            verdict = ("slight" if k < 0.2 else "fair" if k < 0.4 else
                       "moderate" if k < 0.6 else "substantial" if k < 0.8 else
                       "almost perfect")
            print(f"  -> kappa {k:+.2f} = '{verdict}' agreement (Landis-Koch); the "
                  f"per-article label is only as reliable as this.\n")

    if args.out and M.shape[1] >= 2:
        ba = bucketize(M[:, 0], args.band, args.terciles)
        bb = bucketize(M[:, 1], args.band, args.terciles)
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(f"news_id,{names[0]},{names[1]},bucket_a,bucket_b,disagree\n")
            for i, nid in enumerate(ids):
                if np.isfinite(ba[i]) and np.isfinite(bb[i]):
                    da = int(ba[i] != bb[i])
                    f.write(f"{nid},{M[i,0]:.4f},{M[i,1]:.4f},"
                            f"{_NAMES.get(int(ba[i]),'')},{_NAMES.get(int(bb[i]),'')},{da}\n")
        print(f"wrote per-article buckets/disagreements -> {args.out}")


if __name__ == "__main__":
    main()
