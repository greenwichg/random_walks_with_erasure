"""Quantify how ideological a lean-position file is, against a reference.

The text-lean axis is a *noisy proxy* for ideology, so before leaning on RQ3 in
a paper you want a number for how well it matches real partisan lean. This gives
two honest ways to get one:

1. **Gold labels (most credible).**  Make a stratified labeling template, fill the
   ``position`` column yourself (without seeing the model's score, to stay
   unbiased), then score agreement::

       python examples/validate_lean.py --lean lean.csv --news-dir MINDsmall_train \
           --sample 100 --out label_template.tsv          # n>=100 for a credible number
       # each rater fills `position` (e.g. -1/0/1) in their own copy, blind, saves
       python examples/validate_lean.py --lean lean.csv --against rater1.tsv

   For a **trustworthy** axis number, use **n>=100 and 2-3 raters** and pass them all
   with ``--raters``: it reports inter-rater agreement (mean pairwise Spearman +
   quadratic-weighted kappa) and validates the axis against the rater **consensus**::

       python examples/validate_lean.py --lean lean.csv \
           --raters rater1.tsv rater2.tsv rater3.tsv

2. **Convergent validity (automatable).**  Score the same articles with a *second*
   bias model and correlate the two::

       python examples/classify_lean.py --mind-dir MINDsmall_train --political-only \
           --model <other-bias-model> --out lean2.csv
       python examples/validate_lean.py --lean lean.csv --against lean2.csv

Reports Spearman/Pearson correlation and sign agreement over the shared items.
Toward +1 = the axis captures the reference lean; near 0 = it does not.
"""

import argparse
from pathlib import Path

import numpy as np

from rwe.mind import _load_positions_map, _read_news


def _correlate(a, b) -> dict:
    """Spearman/Pearson + non-centre sign agreement between two score arrays."""
    from scipy.stats import pearsonr, spearmanr

    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    m = np.isfinite(a) & np.isfinite(b)
    a, b = a[m], b[m]
    out = {"n": int(m.sum()), "spearman": float("nan"),
           "pearson": float("nan"), "sign_acc": float("nan")}
    if out["n"] >= 3 and a.std() > 0 and b.std() > 0:
        out["spearman"] = float(spearmanr(a, b)[0])
        out["pearson"] = float(pearsonr(a, b)[0])
        nz = (a != 0) & (b != 0)
        if nz.any():
            out["sign_acc"] = float(np.mean(np.sign(a[nz]) == np.sign(b[nz])))
    return out


def _stratified_sample(scores, n, seed=0):
    """Indices evenly spaced across the score *rank* (spans extremes + centre)."""
    order = np.argsort(np.asarray(scores, dtype=float))
    idx = np.linspace(0, len(order) - 1, num=min(n, len(order))).round().astype(int)
    return order[np.unique(idx)]


def _weighted_kappa(a, b) -> float:
    """Quadratic-weighted Cohen's kappa between two ordinal raters (labels rounded
    to integers; 1 = perfect, 0 = chance, <0 = worse than chance)."""
    a = np.round(np.asarray(a, dtype=float)).astype(int)
    b = np.round(np.asarray(b, dtype=float)).astype(int)
    cats = np.arange(min(a.min(), b.min()), max(a.max(), b.max()) + 1)
    K = len(cats)
    if K < 2:
        return float("nan")
    ci = {c: i for i, c in enumerate(cats)}
    O = np.zeros((K, K))
    for x, y in zip(a, b):
        O[ci[x], ci[y]] += 1
    O /= O.sum()
    E = np.outer(O.sum(axis=1), O.sum(axis=0))
    w = (cats[:, None] - cats[None, :]) ** 2 / (K - 1) ** 2
    denom = float((w * E).sum())
    return float(1 - (w * O).sum() / denom) if denom > 0 else float("nan")


def _pairwise_irr(raters: list) -> dict:
    """Inter-rater agreement over all rater pairs: mean pairwise Spearman + mean
    quadratic-weighted kappa, on the items each pair both labeled."""
    from itertools import combinations
    from scipy.stats import spearmanr
    sp, wk, ns = [], [], []
    for ra, rb in combinations(raters, 2):
        shared = [k for k in ra if k in rb]
        if len(shared) < 3:
            continue
        a = np.array([ra[k] for k in shared], dtype=float)
        b = np.array([rb[k] for k in shared], dtype=float)
        if a.std() > 0 and b.std() > 0:
            sp.append(float(spearmanr(a, b)[0]))
        wk.append(_weighted_kappa(a, b))
        ns.append(len(shared))
    return dict(mean_spearman=float(np.nanmean(sp)) if sp else float("nan"),
                mean_wkappa=float(np.nanmean(wk)) if wk else float("nan"),
                n_pairs=len(ns), median_overlap=int(np.median(ns)) if ns else 0)


def _consensus(raters: list, ids) -> dict:
    """Per-item median over the raters who labeled it (robust gold consensus)."""
    out = {}
    for k in ids:
        vals = [r[k] for r in raters if k in r]
        if vals:
            out[k] = float(np.median(vals))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lean", required=True, help="news_id,position file (classifier)")
    ap.add_argument("--against", default=None,
                    help="reference news_id,position file (gold labels or 2nd model)")
    ap.add_argument("--raters", nargs="+", default=None,
                    help="2+ rater label files (news_id,position) -> inter-rater "
                         "agreement (Spearman + weighted kappa) + axis-vs-consensus check")
    ap.add_argument("--news-dir", default=None, help="dir with news.tsv (for --sample)")
    ap.add_argument("--sample", type=int, default=None,
                    help="write a labeling template of N stratified articles")
    ap.add_argument("--out", default="label_template.tsv")
    args = ap.parse_args()

    lean = _load_positions_map(args.lean)

    if args.sample:
        if not args.news_dir:
            ap.error("--sample needs --news-dir (for the article titles)")
        news = _read_news(Path(args.news_dir) / "news.tsv")
        ids = [nid for nid in lean if nid in news]
        pick = _stratified_sample([lean[nid] for nid in ids], args.sample)
        with open(args.out, "w", encoding="utf-8") as f:
            f.write("news_id\tposition\ttitle\n")           # leave position blank
            for i in pick:
                nid = ids[i]
                f.write(f"{nid}\t\t{news[nid][2]}\n")
        print(f"wrote {args.out} ({len(pick)} rows). Fill the 'position' column "
              "(e.g. -1/0/1) WITHOUT peeking at the model, save, then run:\n"
              f"  python examples/validate_lean.py --lean {args.lean} --against {args.out}")
        return

    if args.raters:
        raters = [_load_positions_map(p) for p in args.raters]
        if len(raters) >= 2:
            irr = _pairwise_irr(raters)
            print(f"inter-rater agreement ({len(raters)} raters, {irr['n_pairs']} "
                  f"pairs, ~{irr['median_overlap']} shared items/pair):")
            print(f"  mean pairwise Spearman  = {irr['mean_spearman']:+.3f}")
            print(f"  mean weighted kappa     = {irr['mean_wkappa']:+.3f}")
            print("  (> ~0.6 = raters agree, so the gold labels are trustworthy)\n")
        cons = _consensus(raters, set().union(*[set(r) for r in raters]))
        shared = [nid for nid in lean if nid in cons]
        res = _correlate([lean[n] for n in shared], [cons[n] for n in shared])
        print(f"axis vs {len(raters)}-rater consensus over {res['n']} items:")
        print(f"  Spearman r              = {res['spearman']:+.3f}")
        print(f"  Pearson  r              = {res['pearson']:+.3f}")
        print(f"  sign accuracy (non-0)   = {res['sign_acc']:.2f}")
        print("\nToward +1 = the axis matches the human consensus; near 0 = weak proxy.")
        return

    if not args.against:
        ap.error("give --sample N (+ --news-dir), --against FILE, or --raters F1 F2 ...")

    ref = _load_positions_map(args.against)
    shared = [nid for nid in lean if nid in ref]
    res = _correlate([lean[n] for n in shared], [ref[n] for n in shared])
    print(f"axis agreement over {res['n']} shared labeled items:")
    print(f"  Spearman r              = {res['spearman']:+.3f}")
    print(f"  Pearson  r              = {res['pearson']:+.3f}")
    print(f"  sign accuracy (non-0)   = {res['sign_acc']:.2f}")
    print("\nToward +1 = the lean axis aligns with the reference; near 0 = it does "
          "not capture that signal (the axis is then a weak ideology proxy).")


if __name__ == "__main__":
    main()
