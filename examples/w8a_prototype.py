"""W8A — offline behavioral-graph prototype, Phase-2-ready.

**Offline research tool. Imports nothing from the serving path; nothing in the serving path
imports it. No production / API / report-contract / explainability code is touched, and the
existing `eval_mind` / `rwe` modules are REUSED, never modified.**

Phase 1 proved the pipeline runs + is deterministic on the demo fixture (Gate G1). This revision
implements the Phase-2 prerequisites from the architecture audit:

  * SCALABILITY — `--political-only` + k-core (`--min-user-clicks/--min-item-clicks`) +
    `--sample-users`, and a pre-flight that reports users×items vs the library `max_cells`
    guard and the estimated dense-fit memory, so a MIND-full run is sized BEFORE it is launched.
  * NO LEAKAGE — ideology is refit on each TRAINING split (never the full matrix), so held-out
    clicks never inform the positions used to recommend/score. Both graphs go through the same
    refit path (fitted-vs-fitted), which also removes the Phase-1 gold-vs-fitted asymmetry.
  * REUSE THE HARNESS — the eval reuses `eval_mind._recommenders` (full baseline set:
    ItemKNN / P3 / RP3-beta / RWE-D / RWE-B / BPRMF), `rwe.experiment.compare`, and
    `eval_mind._wilcoxon_vs_ref` (paired significance). The only structural difference from
    `eval_mind._eval_across_seeds` is the per-split refit — that function reuses FIXED
    full-dataset positions (the leak), so it is mirrored, not called.
  * DIAGNOSTICS — runtime + peak RSS per stage; degree / component-size / popularity
    distributions; convergence trace + per-restart objective spread; seed STABILITY
    (sign-invariant); and an axis-proxy check (the substitute for `lean_corr`, which is
    structurally None on label-free MIND).

Nothing here EXECUTES the MIND-full evaluation — it prepares the prototype. Default input is the
license-free fixture; a MIND-full run is a flags-only change (see `--help` / the readiness doc).

    python examples/w8a_prototype.py --fixture tests/fixtures/mind_demo --out-dir /tmp/w8a
    python examples/w8a_prototype.py --fixture <MIND_dir> --preflight \\
        --political-only --min-user-clicks 5 --min-item-clicks 5 --sample-users 8000
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import resource
import sys
import time
import tracemalloc
import types

import numpy as np
import scipy.sparse as sp
from scipy.sparse.csgraph import connected_components
from scipy.stats import spearmanr

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))         # examples/
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))  # repo root

from rwe import RWEB, FeedbackGraph                          # noqa: E402  recommender + graph
from rwe.data import train_test_split                        # noqa: E402  existing split
from rwe.ideology import IdeologyModel                       # noqa: E402  existing ideal-point fit
from rwe.mind import load_mind, MINDData                     # noqa: E402  existing MIND ingest
from rwe.experiment import compare                           # noqa: E402  existing eval harness
import eval_mind as em                                       # noqa: E402  REUSED (factory + wilcoxon)


# --------------------------------------------------------------------------- #
# Instrumentation (offline, read-only)
# --------------------------------------------------------------------------- #
def _peak_rss_mb() -> float:
    """Process peak resident set size in MB (Linux ru_maxrss is KB) — the number that says
    whether the dense O(users×items) fit blew memory."""
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def _timed(fn):
    """Run fn(); return (result, seconds, tracemalloc_peak_MB). ru_maxrss (C-level numpy) is read
    separately via _peak_rss_mb; tracemalloc captures the Python-level incremental peak."""
    tracemalloc.start()
    t0 = time.perf_counter()
    out = fn()
    secs = time.perf_counter() - t0
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return out, round(secs, 4), round(peak / 1e6, 2)


# --------------------------------------------------------------------------- #
# Graph / convergence / stability / axis diagnostics (new OFFLINE utilities)
# --------------------------------------------------------------------------- #
def graph_diagnostics(g: FeedbackGraph) -> dict:
    """Structure + degree/component/popularity DISTRIBUTIONS (not just means)."""
    n_comp, labels = connected_components(g.A_G, directed=False)
    _, comp_sizes = np.unique(labels, return_counts=True)
    ud, idg = np.asarray(g.user_degrees), np.asarray(g.item_degrees)

    def _pctl(a):
        return {p: float(np.percentile(a, p)) for p in (50, 90, 99)} if a.size else {}

    def _gini(a):
        a = np.sort(np.asarray(a, float))
        if a.sum() <= 0:
            return 0.0
        i = np.arange(1, a.size + 1)
        return float((2 * np.sum(i * a) / (a.size * a.sum())) - (a.size + 1) / a.size)

    top1 = int(np.ceil(0.01 * g.n)) or 1
    head_share = float(np.sort(idg)[::-1][:top1].sum() / idg.sum()) if idg.sum() else 0.0
    return {
        "users": int(g.m), "items": int(g.n), "nodes": int(g.N), "edges": int(g.A.nnz),
        "density": float(g.A.nnz / (g.m * g.n)) if g.m and g.n else 0.0,
        "connected_components": int(n_comp),
        "largest_component_frac": float(comp_sizes.max() / g.N) if g.N else 0.0,
        "component_size_hist": {"max": int(comp_sizes.max()), "singletons": int((comp_sizes == 1).sum())},
        "isolated_items": int((idg == 0).sum()), "isolated_users": int((ud == 0).sum()),
        "avg_user_degree": float(ud.mean()) if g.m else 0.0,
        "avg_item_degree": float(idg.mean()) if g.n else 0.0,
        "user_degree_pctl": _pctl(ud), "item_degree_pctl": _pctl(idg),
        "item_degree_gini": _gini(idg), "top1pct_item_click_share": head_share,
    }


def convergence_diagnostics(matrix, seed: int, iters: int, restarts: int, max_cells: float) -> dict:
    """Objective trace + monotonicity + per-restart final-objective spread (fit stability signal)."""
    m, n = matrix.shape
    if m * n > max_cells:
        return {"skipped": f"cells {m*n:.3g} > max_cells {max_cells:.3g}"}
    finals = []
    for k in range(max(1, restarts)):
        r = IdeologyModel(n_iter=iters, seed=seed + k).fit(sp.csr_matrix(matrix), restarts=1)
        finals.append(float(r.history[-1]) if r.history else float("nan"))
    best = IdeologyModel(n_iter=iters, seed=seed).fit(sp.csr_matrix(matrix), restarts=restarts)
    hist = list(map(float, best.history or []))
    return {
        "iters_logged": len(hist), "objective_first": hist[0] if hist else None,
        "objective_last": hist[-1] if hist else None,
        "objective_gain": (hist[-1] - hist[0]) if len(hist) >= 2 else None,
        "monotone_nondecreasing": bool(np.all(np.diff(hist) >= -1e-9)) if len(hist) >= 2 else None,
        "restart_final_spread": float(np.nanmax(finals) - np.nanmin(finals)) if finals else None,
        "restart_finals": [round(x, 4) for x in finals],
    }


def stability_diagnostics(matrix, seeds: int, iters: int, restarts: int, k: int,
                          max_cells: float) -> dict:
    """Refit under `seeds` seeds; report SIGN-INVARIANT agreement — |Spearman| of item positions
    and mean top-k Jaccard of RWE-B recommendations (recs depend on relative distance, so they are
    orientation-free). Directly measures decision-gate metric 7."""
    m, n = matrix.shape
    if m * n > max_cells or seeds < 2:
        return {"skipped": f"cells {m*n:.3g} > max_cells {max_cells:.3g} or seeds<2"}
    g = FeedbackGraph(matrix)
    pos, recs = [], []
    for sd in range(seeds):
        f = IdeologyModel(n_iter=iters, seed=sd).fit(sp.csr_matrix(matrix), restarts=restarts)
        pos.append(f.phi)
        recs.append(RWEB(g, f.theta, f.phi, epsilon=0.9).recommend(
            np.arange(m), top_k=min(k, n), exclude_seen=True))
    corrs = [abs(spearmanr(pos[i], pos[j]).correlation)
             for i in range(seeds) for j in range(i + 1, seeds)]
    jac = []
    for i in range(seeds):
        for j in range(i + 1, seeds):
            for u in range(m):
                a, b = set(recs[i][u][recs[i][u] >= 0]), set(recs[j][u][recs[j][u] >= 0])
                if a or b:
                    jac.append(len(a & b) / len(a | b))
    return {"seeds": seeds, "abs_spearman_positions_mean": float(np.nanmean(corrs)) if corrs else None,
            "topk_jaccard_mean": float(np.mean(jac)) if jac else None}


def axis_proxy(item_pos, political_mask, categories) -> dict:
    """lean_corr is structurally None on label-free MIND, so probe what the axis DOES separate:
    |point-biserial| with the political flag, and the fraction of position variance explained by
    category (eta^2). High category-eta^2 => the axis is TOPICAL, not (necessarily) ideological."""
    pos = np.asarray(item_pos, float)
    finite = np.isfinite(pos)
    out = {"lean_corr": None, "note": "no outlet labels on MIND; lean_corr uncomputable"}
    pm = np.asarray(political_mask, bool)[finite] if political_mask is not None else None
    p = pos[finite]
    if pm is not None and pm.any() and (~pm).any() and p.std() > 0:
        out["abs_corr_with_political_flag"] = float(abs(np.corrcoef(p, pm.astype(float))[0, 1]))
    cats = np.asarray(categories, object)[finite] if categories is not None else None
    if cats is not None and p.std() > 0:
        grand = p.mean()
        ss_between = sum((p[cats == c].mean() - grand) ** 2 * (cats == c).sum()
                         for c in np.unique(cats) if (cats == c).any())
        out["category_eta_sq"] = float(ss_between / ((p - grand) ** 2).sum())
    return out


# --------------------------------------------------------------------------- #
# Ingest + scalability pre-flight (reuse load_mind / political_subset / sample_users)
# --------------------------------------------------------------------------- #
def ingest(fixture: str, political_only: bool, min_user: int, min_item: int,
           sample_users: "int | None", seed: int) -> MINDData:
    """Reuse the existing filtering strategy: k-core in load_mind, then political_subset (no lean
    required — MIND has none), then sample_users to cap the dense fit."""
    d = load_mind(fixture, min_user_clicks=min_user, min_item_clicks=min_item)
    if political_only:
        d = d.political_subset(require_lean=False)
    if sample_users:
        d = d.sample_users(sample_users, seed=seed)
    return d


def preflight(mind: MINDData, max_cells: float) -> dict:
    """Size the dense fit BEFORE running it: cells vs max_cells and the estimated dense-matrix
    memory (IdeologyModel densifies the m×n click matrix; intermediates ~5×)."""
    m, n = mind.dataset.matrix.shape
    cells = m * n
    est_gb = 8 * cells * 5 / 1e9        # float64 * cells * ~5 dense intermediates
    return {"users": int(m), "items": int(n), "cells": float(cells), "max_cells": float(max_cells),
            "fits_under_max_cells": bool(cells <= max_cells),
            "est_dense_fit_gb": round(est_gb, 3),
            "advice": "" if cells <= max_cells else
                      "apply --political-only / --min-*-clicks / --sample-users to shrink the fit"}


# --------------------------------------------------------------------------- #
# Leak-free evaluation — refit ideology per TRAIN split, reuse eval_mind harness
# --------------------------------------------------------------------------- #
def _factory_args(a) -> types.SimpleNamespace:
    return types.SimpleNamespace(itemknn_k=a.itemknn_k, rp3_beta=a.rp3_beta, rwed_beta=a.rwed_beta,
                                 rwed_v=a.rwed_v, epsilon=a.epsilon,
                                 rweb_max_distance=a.rweb_max_distance, no_bprmf=a.no_bprmf)


def leakfree_eval(dataset, seeds: int, iters: int, restarts: int, a, max_cells: float) -> dict:
    """For each seed: split -> **refit ideology on the TRAIN matrix only** -> build the full
    eval_mind baseline set on the train graph -> score against held-out test. Aggregate mean/std
    across seeds and run eval_mind's paired Wilcoxon vs the P3 reference. No held-out click ever
    informs the positions (leakage eliminated)."""
    import pandas as pd
    fa = _factory_args(a)
    tables, skipped = [], 0
    for sd in range(seeds):
        train, test_pos = train_test_split(dataset, test_frac=a.test_frac, seed=sd)
        if train.shape[0] * train.shape[1] > max_cells:
            skipped += 1
            continue
        gtr = FeedbackGraph(train)
        fit = IdeologyModel(n_iter=iters, seed=sd).fit(train, restarts=restarts)   # TRAIN-ONLY refit
        recs = em._recommenders(gtr, fit.theta, fit.phi, fa, sd)
        tables.append(compare(recs, gtr, test_pos, top_k=a.top_k, diversity_k=min(a.diversity_k, gtr.n),
                              item_positions=fit.phi, user_positions=fit.theta, n_users_total=gtr.m))
    if not tables:
        return {"skipped_all": True, "reason": f"every split exceeded max_cells {max_cells:.3g}"}
    methods, cols = list(tables[0].index), list(tables[0].columns)
    arr = np.stack([t.loc[methods, cols].to_numpy(float) for t in tables])
    import warnings
    with warnings.catch_warnings():                          # a metric may be all-NaN for a method
        warnings.simplefilter("ignore", RuntimeWarning)
        mean = pd.DataFrame(np.nanmean(arr, axis=0), index=methods, columns=cols)
    pvals = None
    if seeds >= 2 and "P3" in methods:                       # reuse eval_mind's significance
        try:
            pvals = em._wilcoxon_vs_ref(arr, methods, cols, "P3", cols)
        except Exception as e:
            pvals = {"error": str(e)}
    return {"seeds_used": len(tables), "seeds_skipped_over_max_cells": skipped,
            "leak_free": True, "positions": "refit on each training split",
            "mean_table": json.loads(mean.to_json()),
            "wilcoxon_vs_P3": (json.loads(json.dumps(pvals, default=lambda o: getattr(o, "tolist", lambda: str(o))()))
                               if pvals is not None else None)}


# --------------------------------------------------------------------------- #
def run(a) -> dict:
    os.makedirs(a.out_dir, exist_ok=True)
    report = {"gate": "G1 satisfied; this run PREPARES Phase 2 (no MIND-full execution)",
              "seed": a.seed, "timings_sec": {}, "tracemalloc_peak_mb": {}}

    # --- B: behavioral graph (fixture / MIND), with the existing filtering strategy ---------
    (dB), tB, mB = _timed(lambda: ingest(a.fixture, a.political_only, a.min_user_clicks,
                                          a.min_item_clicks, a.sample_users, a.seed))
    report["timings_sec"]["ingest"] = tB
    pf = preflight(dB, a.max_cells)
    report["preflight"] = pf

    if a.preflight:                                          # size-only: no fit, no eval
        report["peak_rss_mb"] = round(_peak_rss_mb(), 1)
        _write(a.out_dir, report, history=None)
        return report

    conv, tC, mC = _timed(lambda: convergence_diagnostics(
        dB.dataset.matrix, a.seed, a.iters, a.restarts, a.max_cells))
    stab, tS, mS = _timed(lambda: stability_diagnostics(
        dB.dataset.matrix, max(a.seeds, 2), a.iters, a.restarts, a.top_k, a.max_cells))
    # a single full-matrix fit only for the axis-proxy / artifact (diagnostic, not for scoring)
    fitB = dB.fit_ideology(n_iter=a.iters, seed=a.seed, restarts=a.restarts, max_cells=a.max_cells) \
        if pf["fits_under_max_cells"] else None
    gB = FeedbackGraph(dB.dataset.matrix)
    structB = graph_diagnostics(gB)
    proxy = axis_proxy(fitB.item_positions, dB.political, dB.categories) if fitB is not None else \
        {"skipped": "fit exceeded max_cells"}
    evalB, tE, mE = _timed(lambda: leakfree_eval(dB.dataset, a.seeds, a.iters, a.restarts, a, a.max_cells))
    report["timings_sec"].update(convergence=tC, stability=tS, leakfree_eval=tE)
    report["tracemalloc_peak_mb"].update(convergence=mC, stability=mS, leakfree_eval=mE)
    report["behavioral"] = {"structure": structB, "convergence": conv, "stability": stab,
                            "axis_proxy": proxy, "leakfree_eval": evalB}

    # --- A: synthetic comparator, SAME leak-free path (fitted-vs-fitted, symmetric) ---------
    if not a.no_synthetic:
        (dA_gA), tA, mA = _timed(lambda: _synth(a.syn_users, a.syn_items, a.seed))
        dA, gA = dA_gA
        report["timings_sec"]["synthetic_build"] = tA
        evalA, tEA, mEA = _timed(lambda: leakfree_eval(dA.dataset, a.seeds, a.iters, a.restarts, a, a.max_cells))
        report["timings_sec"]["synthetic_leakfree_eval"] = tEA
        report["synthetic"] = {"structure": graph_diagnostics(gA), "leakfree_eval": evalA,
                               "note": "gold labels available but eval refits per split for a "
                                       "symmetric, leak-free graph-quality comparison"}

    # --- artifact (click matrix + a diagnostic full-fit position set; graph derived) --------
    if fitB is not None:
        MINDData(dataset=dB.dataset, categories=dB.categories, subcategories=dB.subcategories,
                 titles=dB.titles, outlets=dB.outlets, political=dB.political,
                 item_positions=fitB.item_positions, user_positions=fitB.user_positions
                 ).save(os.path.join(a.out_dir, "w8a_behavioral.npz"))
        report["artifact"] = {"npz": os.path.join(a.out_dir, "w8a_behavioral.npz"),
                              "contains": ["click matrix", "diagnostic full-fit positions",
                                           "FeedbackGraph = A^G derived from the matrix"]}
    report["peak_rss_mb"] = round(_peak_rss_mb(), 1)
    _write(a.out_dir, report, history=(conv.get("restart_finals") if isinstance(conv, dict) else None))
    return report


def _synth(n_users, max_items, seed):
    import simulate_users as su
    _, _, _, _, mind, _, _ = su.run(su.SimConfig(n_users=n_users, max_items=max_items, seed=seed))
    return mind, FeedbackGraph(mind.dataset.matrix)


def _write(out_dir, report, history):
    with open(os.path.join(out_dir, "w8a_report.json"), "w") as f:
        json.dump(report, f, indent=2)


def _fingerprint(a) -> tuple:
    """Determinism fingerprint of the leak-free behavioral pipeline (fit-on-train, seed 0)."""
    d = ingest(a.fixture, a.political_only, a.min_user_clicks, a.min_item_clicks, a.sample_users, a.seed)
    train, _ = train_test_split(d.dataset, test_frac=a.test_frac, seed=0)
    fit = IdeologyModel(n_iter=a.iters, seed=0).fit(train, restarts=a.restarts)
    g = FeedbackGraph(train)
    return (np.round(fit.phi, 6).tolist(), int(g.A.nnz),
            int(connected_components(g.A_G, directed=False)[0]))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fixture", default="tests/fixtures/mind_demo")
    ap.add_argument("--out-dir", default="/tmp/w8a_phase2")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--seeds", type=int, default=1, help="eval seeds (set >=7 for MIND full)")
    ap.add_argument("--iters", type=int, default=300)
    ap.add_argument("--restarts", type=int, default=3)
    ap.add_argument("--test-frac", type=float, default=0.3)
    ap.add_argument("--top-k", type=int, default=3, help="ranking cutoff (raise for MIND full)")
    ap.add_argument("--diversity-k", type=int, default=20)
    # scalability / filtering (reuse the existing strategy)
    ap.add_argument("--political-only", action="store_true")
    ap.add_argument("--min-user-clicks", type=int, default=1)
    ap.add_argument("--min-item-clicks", type=int, default=1)
    ap.add_argument("--sample-users", type=int, default=None)
    ap.add_argument("--max-cells", type=float, default=5e7, help="library dense-fit guard")
    ap.add_argument("--preflight", action="store_true", help="size the fit only; no fit, no eval")
    # eval_mind baseline factory args (mirrors eval_mind so _recommenders works)
    ap.add_argument("--epsilon", type=float, default=0.9)
    ap.add_argument("--rweb-max-distance", type=float, default=None)
    ap.add_argument("--rwed-beta", type=float, default=0.5)
    ap.add_argument("--rwed-v", type=float, default=1.0)
    ap.add_argument("--rp3-beta", type=float, default=0.5)
    ap.add_argument("--itemknn-k", type=int, default=20)
    ap.add_argument("--no-bprmf", action="store_true", default=True,
                    help="skip BPRMF (default on; enable at MIND-full scale with --bprmf)")
    ap.add_argument("--bprmf", dest="no_bprmf", action="store_false")
    ap.add_argument("--no-synthetic", action="store_true")
    ap.add_argument("--syn-users", type=int, default=120)
    ap.add_argument("--syn-items", type=int, default=300)
    ap.add_argument("--det-check", action="store_true")
    a = ap.parse_args()

    if a.det_check:
        ok = _fingerprint(a) == _fingerprint(a)
        print("DETERMINISM (leak-free fit-on-train):", "PASS" if ok else "FAIL")
        raise SystemExit(0 if ok else 1)

    print(json.dumps(run(a), indent=2))


if __name__ == "__main__":
    main()
