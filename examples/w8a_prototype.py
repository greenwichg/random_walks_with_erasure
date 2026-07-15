"""W8A Phase-1 — smallest offline prototype proving the behavioral-graph pipeline runs
end-to-end. **Offline research tool. Imports nothing from the serving path; nothing in the
serving path imports it. No production/API/report-contract/explainability code is touched.**

It reuses only existing repository components — `rwe.mind.load_mind` / `fit_ideology`,
`rwe.ideology.IdeologyModel`, `rwe.graph.FeedbackGraph`, `rwe.data.train_test_split`,
`rwe.experiment.compare` (the existing eval harness), `rwe.metrics`, and the existing
recommenders (`P3`, `RWED`, `RWEB`). The only new code is offline *evaluation utilities*
(graph connectivity/degree via `scipy.sparse.csgraph`), permitted by the Phase-1 brief.

Pipeline (B = behavioral):  load_mind(fixture) -> fit_ideology -> FeedbackGraph
                            -> train_test_split -> experiment.compare  -> artifact + report
Comparator (A = synthetic): simulate_users.run -> FeedbackGraph -> same battery.

Per docs/W8_EVALUATION_AND_DECISION_GATE.md, the demo fixture proves **G1 (runs +
deterministic) only** — NO statistical claim — and the A/B comparison is presented
side-by-side, each graph against its OWN held-out split (never cross-dataset).

    python examples/w8a_prototype.py --fixture tests/fixtures/mind_demo --out-dir /tmp/w8a
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys

import numpy as np
import scipy.sparse as sp
from scipy.sparse.csgraph import connected_components

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))        # examples/
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))  # repo root

from rwe import RWEB, RWED, P3, FeedbackGraph                 # noqa: E402  existing recommenders + graph
from rwe.data import train_test_split                         # noqa: E402  existing split
from rwe.ideology import IdeologyModel                        # noqa: E402  existing ideal-point fit
from rwe.mind import load_mind, MINDData                      # noqa: E402  existing MIND ingest
from rwe.experiment import compare                            # noqa: E402  existing eval harness


# --------------------------------------------------------------------------- #
# New OFFLINE evaluation utilities (permitted by the Phase-1 brief). Read-only.
# --------------------------------------------------------------------------- #
def graph_structure(g: FeedbackGraph) -> dict:
    """Structural descriptors of a FeedbackGraph — size, connectivity, degree, density.
    Connectivity is over the bipartite adjacency A^G (rwe/graph.py); the rest reuse the
    graph's own degree vectors."""
    n_comp, labels = connected_components(g.A_G, directed=False)
    _, sizes = np.unique(labels, return_counts=True)
    ud, idg = g.user_degrees, g.item_degrees
    return {
        "users": int(g.m), "items": int(g.n), "nodes": int(g.N), "edges": int(g.A.nnz),
        "density": float(g.A.nnz / (g.m * g.n)) if g.m and g.n else 0.0,
        "connected_components": int(n_comp),
        "largest_component_frac": float(sizes.max() / g.N) if g.N else 0.0,
        "isolated_items": int((idg == 0).sum()), "isolated_users": int((ud == 0).sum()),
        "avg_user_degree": float(ud.mean()) if g.m else 0.0,
        "avg_item_degree": float(idg.mean()) if g.n else 0.0,
        "median_item_degree": float(np.median(idg)) if g.n else 0.0,
    }


def convergence_trace(matrix, seed: int, n_iter: int, restarts: int) -> dict:
    """Ideology-fit convergence via the existing IdeologyModel.fit — reports the objective
    trace (IdeologyResult.history) and whether it is monotone non-decreasing (ascent)."""
    res = IdeologyModel(n_iter=n_iter, seed=seed).fit(sp.csr_matrix(matrix), restarts=restarts)
    hist = list(map(float, res.history or []))
    improved = (hist[-1] - hist[0]) if len(hist) >= 2 else float("nan")
    monotone = bool(np.all(np.diff(hist) >= -1e-9)) if len(hist) >= 2 else False
    return {"iters_logged": len(hist), "objective_first": hist[0] if hist else None,
            "objective_last": hist[-1] if hist else None, "objective_gain": improved,
            "monotone_nondecreasing": monotone, "history": hist}


def diversity_all_users(rec, g: FeedbackGraph, item_pos, k: int = 20) -> dict:
    """Diversity descriptors over ALL users (not test-gated), so they stay meaningful even when
    the held-out split is tiny. Pure reuse of rwe.metrics."""
    from rwe import metrics
    R = rec.recommend(np.arange(g.m), top_k=min(k, g.n), exclude_seen=True)
    out = {"gini_diversity": float(metrics.gini_diversity(R, g.n)),
           "catalog_coverage": float(metrics.catalog_coverage(R, g.n)),
           "personalization": float(metrics.personalization(R, g.n))}
    if item_pos is not None and np.isfinite(np.asarray(item_pos, float)).any():
        out["rec_range"] = float(metrics.rec_range_at_k(R, item_pos))
    return out


# --------------------------------------------------------------------------- #
# Graph builders (A synthetic / B behavioral) — reuse only
# --------------------------------------------------------------------------- #
def behavioral_from_fixture(fixture: str, seed: int, iters: int, restarts: int):
    """B: ingest the MIND fixture, fit ideology from CLICKS ONLY (no outlets), build the graph."""
    d = load_mind(fixture, min_user_clicks=1, min_item_clicks=1)
    fit = d.fit_ideology(n_iter=iters, seed=seed, restarts=restarts, max_cells=1e7)
    d = d.with_ideology(fit)
    g = FeedbackGraph(d.dataset.matrix)
    return d, g, fit


def synthetic_graph(n_users: int, max_items: int, seed: int):
    """A: the existing synthetic simulator (production's Stage-0 substrate), native gold labels."""
    import simulate_users as su
    cfg = su.SimConfig(n_users=n_users, max_items=max_items, seed=seed)
    _, _, _, _, mind, _, _ = su.run(cfg)                      # qbias=None -> synthetic_catalog
    g = FeedbackGraph(mind.dataset.matrix)
    return mind, g


def within_dataset_table(g: FeedbackGraph, item_pos, user_pos, seed: int) -> "dict":
    """Reuse the existing harness (rwe.experiment.compare) on this graph's OWN held-out split.
    Each recommender is one of the product's existing classes. Returns {metric: {rec: value}}."""
    ds_like = _DatasetLike(g.A)
    train_matrix, test_pos = train_test_split(ds_like, test_frac=0.3, min_interactions=3, seed=seed)
    gtr = FeedbackGraph(train_matrix)
    recs = {"P3": P3(gtr), "RWE-D": RWED(gtr, beta=0.5),
            "RWE-B": RWEB(gtr, user_pos, item_pos, epsilon=0.9)}
    df = compare(recs, gtr, test_pos, top_k=3, diversity_k=min(20, g.n),
                 item_positions=item_pos, user_positions=user_pos)
    n_eval = int(sum(1 for t in test_pos if len(t) > 0))
    return {"n_eval_users": n_eval, "table": json.loads(df.to_json())}


class _DatasetLike:
    """Minimal shim so train_test_split (which needs .matrix/.user_ids/.item_ids) accepts a graph's
    binarized matrix directly — avoids re-plumbing ids for the split. Reuse, not a new algorithm."""
    def __init__(self, A):
        A = sp.csr_matrix(A)
        self.matrix = A
        self.user_ids = np.arange(A.shape[0])
        self.item_ids = np.arange(A.shape[1])


# --------------------------------------------------------------------------- #
def run(fixture: str, out_dir: str, seed: int, iters: int, restarts: int,
        syn_users: int, syn_items: int) -> dict:
    os.makedirs(out_dir, exist_ok=True)

    # B — behavioral graph from the fixture
    dB, gB, fitB = behavioral_from_fixture(fixture, seed, iters, restarts)
    conv = convergence_trace(dB.dataset.matrix, seed, iters, restarts)
    structB = graph_structure(gB)
    recB = RWEB(gB, fitB.user_positions, fitB.item_positions, epsilon=0.9)
    divB = diversity_all_users(recB, gB, fitB.item_positions)
    tblB = within_dataset_table(gB, fitB.item_positions, fitB.user_positions, seed)

    # A — synthetic comparator (native gold labels)
    dA, gA = synthetic_graph(syn_users, syn_items, seed)
    structA = graph_structure(gA)
    recA = RWEB(gA, dA.user_positions, dA.item_positions, epsilon=0.9)
    divA = diversity_all_users(recA, gA, dA.item_positions)
    tblA = within_dataset_table(gA, dA.item_positions, dA.user_positions, seed)

    # Offline artifact: click matrix + fitted positions (+ graph is a pure function of the matrix)
    artifact_npz = os.path.join(out_dir, "w8a_behavioral.npz")
    dB.save(artifact_npz)

    report = {
        "gate": "G1 (runs + deterministic); demo fixture => NO statistical claim",
        "seed": seed,
        "behavioral": {"structure": structB, "convergence": {k: v for k, v in conv.items()
                                                              if k != "history"},
                       "diversity_all_users": divB, "within_dataset": tblB,
                       "fitted_item_positions": [round(float(x), 4) for x in fitB.item_positions],
                       "lean_corr": fitB.lean_corr},
        "synthetic": {"structure": structA, "diversity_all_users": divA, "within_dataset": tblA},
        "artifact": {"npz": artifact_npz,
                     "contains": ["click matrix (dataset.matrix)", "fitted item/user positions",
                                  "FeedbackGraph is A^G = derived from dataset.matrix"]},
    }
    with open(os.path.join(out_dir, "w8a_report.json"), "w") as f:
        json.dump({**report, "behavioral_convergence_history": conv["history"]}, f, indent=2)
    return report


def _fingerprint(fixture, seed, iters, restarts) -> tuple:
    """Numeric fingerprint of the behavioral pipeline for the determinism check."""
    d, g, fit = behavioral_from_fixture(fixture, seed, iters, restarts)
    return (np.round(fit.item_positions, 6).tolist(), np.round(fit.user_positions, 6).tolist(),
            int(g.A.nnz), int(connected_components(g.A_G, directed=False)[0]))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fixture", default="tests/fixtures/mind_demo")
    ap.add_argument("--out-dir", default="/tmp/w8a_phase1")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--iters", type=int, default=300)
    ap.add_argument("--restarts", type=int, default=3)
    ap.add_argument("--syn-users", type=int, default=120)
    ap.add_argument("--syn-items", type=int, default=300)
    ap.add_argument("--det-check", action="store_true",
                    help="run the behavioral pipeline twice and assert identical outputs (G1)")
    args = ap.parse_args()

    if args.det_check:
        a = _fingerprint(args.fixture, args.seed, args.iters, args.restarts)
        b = _fingerprint(args.fixture, args.seed, args.iters, args.restarts)
        print("DETERMINISM:", "PASS (identical across two runs)" if a == b else "FAIL")
        raise SystemExit(0 if a == b else 1)

    rep = run(args.fixture, args.out_dir, args.seed, args.iters, args.restarts,
              args.syn_users, args.syn_items)
    print(json.dumps(rep, indent=2))


if __name__ == "__main__":
    main()
