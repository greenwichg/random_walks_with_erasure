"""Ensemble several text-lean CSVs into one stronger left<->right axis.

Averaging *independent* bias models reduces single-model noise -- the codeable
lever for a stronger axis, because (a) the outlet-lean route is blocked on MIND
(its URLs are MSN URLs with no publisher to join AllSides/MBFC leans to), and
(b) Spearman is rank-based, so calibrating against a gold set cannot by itself
raise rank agreement -- a less-noisy ensemble can.

Each input is a ``news_id,position`` file from ``classify_lean.py`` run with a
DIFFERENT ``--model`` (and the matching ``--label-positions``).  Each model is
z-scored so its scale cannot dominate, averaged over the models that scored a
given article, then rescaled to the spread of the first (reference) file so the
ensemble drops into the existing pipeline unchanged.

    python examples/classify_lean.py --mind-dir MIND --political-only --out lean.csv
    # a second L/C/R bias model -- CHECK its printed id2label and match
    # --label-positions to it (label order differs between models):
    python examples/classify_lean.py --mind-dir MIND --political-only \
        --model premsa/political-bias-prediction-allsides-BERT \
        --label-positions -1,0,1 --out lean_b.csv
    python examples/ensemble_lean.py lean.csv lean_b.csv --out lean_ens.csv
    # then use lean_ens.csv anywhere lean.csv was used:
    python examples/ingest_mind.py --mind-dir MIND --political-only \
        --positions-csv lean_ens.csv --min-user-clicks 10 --min-item-clicks 10 \
        --out mind_text_ens.npz

It prints **pairwise model agreement** (convergent validity): if two models
correlate near 0, averaging them is dubious -- inspect before trusting the
ensemble.  To measure whether the ensemble actually beats a single model, score
both against a human gold set with ``validate_lean.py``.
"""

import argparse

import numpy as np

from rwe.mind import _load_positions_map


def _zscore(x) -> np.ndarray:
    """Standardise to mean 0 / std 1 over finite entries (NaNs preserved)."""
    x = np.asarray(x, dtype=float)
    sd = np.nanstd(x)
    return (x - np.nanmean(x)) / sd if sd > 0 else x - np.nanmean(x)


def _matrix(files):
    """Stack the CSVs into an ``(article, model)`` matrix aligned on the id union."""
    maps = [_load_positions_map(f) for f in files]
    ids = sorted({nid for m in maps for nid in m})
    pos = {nid: i for i, nid in enumerate(ids)}
    M = np.full((len(ids), len(maps)), np.nan)
    for j, m in enumerate(maps):
        for nid, v in m.items():
            M[pos[nid], j] = v
    return ids, M


def ensemble(files, target_std=None):
    """Average z-scored model positions over the union of news_ids.

    Returns ``(ids, positions, M)``: each model column is z-scored over its finite
    entries, averaged per article over the models that scored it, then centred at 0
    and rescaled so its std equals ``target_std`` (default: the std of the first
    file's raw positions, so the ensemble matches the reference axis's spread)."""
    ids, M = _matrix(files)
    ref_std = float(np.nanstd(M[:, 0]))
    Z = np.column_stack([_zscore(M[:, j]) for j in range(M.shape[1])])
    with np.errstate(invalid="ignore"):
        ens = np.nanmean(Z, axis=1)                     # mean over available models
    ens = ens - np.nanmean(ens)                          # centre at 0
    sd = float(np.nanstd(ens))
    tgt = ref_std if target_std is None else float(target_std)
    if sd > 0 and tgt > 0:
        ens = ens / sd * tgt
    return ids, ens, M


def pairwise_agreement(M, names):
    """Spearman/Pearson between every pair of model columns over shared items."""
    from scipy.stats import pearsonr, spearmanr

    out = []
    for a in range(M.shape[1]):
        for b in range(a + 1, M.shape[1]):
            m = np.isfinite(M[:, a]) & np.isfinite(M[:, b])
            if m.sum() >= 3 and M[m, a].std() > 0 and M[m, b].std() > 0:
                out.append((names[a], names[b], int(m.sum()),
                            float(spearmanr(M[m, a], M[m, b])[0]),
                            float(pearsonr(M[m, a], M[m, b])[0])))
            else:
                out.append((names[a], names[b], int(m.sum()),
                            float("nan"), float("nan")))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+", help="2+ news_id,position CSVs (one per model)")
    ap.add_argument("--out", default="lean_ens.csv")
    ap.add_argument("--target-std", type=float, default=None,
                    help="rescale the ensemble to this std (default: first file's std)")
    args = ap.parse_args()
    if len(args.files) < 2:
        ap.error("give at least two lean CSVs to ensemble")

    ids, ens, M = ensemble(args.files, target_std=args.target_std)
    n_models = np.isfinite(M).sum(axis=1)

    print(f"ensembling {len(args.files)} models over {len(ids)} articles "
          f"(union of news_ids)")
    for k in range(1, M.shape[1] + 1):
        c = int((n_models == k).sum())
        if c:
            print(f"  {c} articles scored by {k} model(s)")

    print("\npairwise model agreement (convergent validity):")
    import os
    names = [os.path.basename(f) for f in args.files]
    for a, b, n, sp, pe in pairwise_agreement(M, names):
        print(f"  {a} vs {b}: n={n}  Spearman={sp:+.3f}  Pearson={pe:+.3f}")
    print("  (near 0 = the models disagree; averaging them is then questionable)")

    keep = np.isfinite(ens)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write("news_id,position\n")
        for nid, p, ok in zip(ids, ens, keep):
            if ok:
                f.write(f"{nid},{p:.4f}\n")
    e = ens[keep]
    print(f"\nwrote {args.out}  ({int(keep.sum())} articles, "
          f"mean={e.mean():+.2f}, std={e.std():.2f}, "
          f"range=[{e.min():+.2f}, {e.max():+.2f}])")
    print("Next: validate it against a gold set --\n"
          f"  python examples/validate_lean.py --lean {args.out} --against label_template.tsv")


if __name__ == "__main__":
    main()
