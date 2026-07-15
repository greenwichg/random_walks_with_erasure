"""Close the loop: drive ``AdaptiveRWEB`` from the **measured** per-user reception (the
satisfaction probe), not a simulated browsing walk.

The pipeline: ``satisfaction_probe.csv`` gives, per user, how *welcomed* their real
cross-cutting comments were (``cross_welcomed_frac`` = upvoted AND not controversial, or
``cross_upvoted_frac`` on older CSVs, in [0,1]). That is a measured
*tolerance* signal — we map it straight to ``AdaptiveRWEB``'s per-user ``exposure``, so a
user the data says tolerates cross-cutting (their bridges got upvoted) receives a stronger
bridging dose, and a user who got downvoted gets a gentler one. The dose is now
personalised from **real engagement**, not simulation.

The demonstration is *redistribution*, not a free lunch: against a uniform RWE-B set to the
**same average dose**, the adaptive policy gives the low-tolerance users *less* opposite
content (sparing them) and the high-tolerance users *more*. Same caveat as the probe: the
measured signal exists only for the ~9% who ever cross over, and is self-selected.

    python examples/satisfaction_probe.py --comments-dir politosphere --npz NPZ \\
        --out satisfaction_probe.csv
    python examples/adaptive_satisfaction.py --npz NPZ --probe-csv satisfaction_probe.csv
"""

from __future__ import annotations

import argparse
import csv

import numpy as np

from rwe import FeedbackGraph, RWEB
from rwe.mind import MINDData
from rwe.satisfaction import AdaptiveRWEB


def read_probe_csv(path: str) -> dict:
    """Per-user probe CSV -> ``{user_name: row_dict}``."""
    with open(path, newline="", encoding="utf-8") as f:
        return {r["user"]: r for r in csv.DictReader(f)}


def measured_exposure(rows: dict, user_ids, min_cross: int = 1, default: float = 0.5):
    """Per-user exposure in [0,1] aligned to ``user_ids``: a user's measured reception of
    their cross-cutting comments when they have >= ``min_cross`` of them, else the neutral
    ``default`` (no signal). Prefers the **hardened** ``cross_welcomed_frac`` (upvoted AND
    not controversiality-flagged) the probe now writes, falling back to
    ``cross_upvoted_frac`` for older CSVs. Returns ``(exposure, measured_mask)``."""
    ids = [str(u) for u in user_ids]
    exp = np.full(len(ids), float(default), dtype=float)
    measured = np.zeros(len(ids), dtype=bool)
    for i, u in enumerate(ids):
        r = rows.get(u)
        if not r:
            continue
        uf = r.get("cross_welcomed_frac") or r.get("cross_upvoted_frac", "")
        try:
            if int(r.get("cross_n", 0)) >= min_cross and uf not in ("", None):
                exp[i], measured[i] = float(uf), True
        except (TypeError, ValueError):
            continue
    return exp, measured


def shrunk_exposure(rate, n, kappa: float = 10.0, prior: float = 0.5) -> float:
    """Bayesian shrinkage of a measured cross-cutting reception ``rate`` (openedCross/shownCross)
    toward the neutral ``prior`` (W2): ``(n·rate + kappa·prior)/(n + kappa)``, clipped to ``[0, 1]``.

    ``n`` = shownCross is the observation count; ``kappa`` is the prior's pseudo-impression weight
    (Beta(kappa/2, kappa/2) centred at ``prior``). ``n == 0`` or a null ``rate`` returns ``prior``
    exactly, so a new / no-signal reader is byte-identical to the neutral default. **Driven by the
    RATE, never the raw opened count** — showing more cross-cutting (a bigger bridge budget) raises
    ``n`` but not ``E[rate]``, so it cannot inflate the estimate (W2 feedback-loop audit)."""
    if rate is None or n is None:
        return float(prior)
    n = max(0.0, float(n))
    x = (n * float(rate) + float(kappa) * float(prior)) / (n + float(kappa))
    return float(min(1.0, max(0.0, x)))


def opposite_reach(model, users, theta, item_pos, k: int = 10) -> np.ndarray:
    """Per-user **rank-weighted** reach into opposite-side territory in the top-``k``
    (DCG-style: opposite content ranked *higher* scores more). A plain opposite-*fraction*
    saturates -- RWE-B already recommends mostly opposite items -- so the dose changes the
    *ordering*/*depth* more than the count; this metric captures that. NaN if no valid recs."""
    users = np.asarray(users, dtype=int)
    R = model.recommend(users, top_k=k)                  # (len, k) item indices, -1 padded
    ip = np.asarray(item_pos, dtype=float)
    w = 1.0 / np.log2(np.arange(k) + 2.0)                # rank discount
    out = np.full(users.shape[0], np.nan)
    for row_i, (recs, u) in enumerate(zip(R, users)):
        valid = recs >= 0
        if not valid.any():
            continue
        side = np.sign(theta[u]) or 1.0
        reach = np.clip(-side * ip[recs[valid]], 0.0, None)   # depth into opposite territory
        ww = w[valid]
        out[row_i] = float(np.sum(ww * reach) / np.sum(ww))
    return out


def _tercile_table(tol, adaptive_reach, uniform_reach) -> str:
    """Opposite-content reach by measured-tolerance tercile: uniform (flat) vs adaptive
    (should rise with tolerance -- the dose redistributes toward those who tolerate it)."""
    order = np.argsort(tol)
    thirds = np.array_split(order, 3)
    L = ["  measured tolerance   | adaptive reach | uniform reach  (n)",
         "  ---------------------+----------------+-------------------"]
    for name, g in zip(("low ", "mid ", "high"), thirds):
        a = np.nanmean(adaptive_reach[g])
        u = np.nanmean(uniform_reach[g])
        L.append(f"  {name} ({tol[g].min():.2f}-{tol[g].max():.2f})     "
                 f"|     {a:5.3f}      |     {u:5.3f}     ({g.size})")
    return "\n".join(L)


def run(npz: str, probe_csv: str, k: int = 10, sample: int = 4000,
        epsilon_low: float = 0.5, epsilon_high: float = 0.95, seed: int = 0) -> str:
    d = MINDData.load(npz)
    dataset, theta, item_pos = d.recommender_inputs()
    rows = read_probe_csv(probe_csv)
    exposure, measured = measured_exposure(rows, dataset.user_ids)

    g = FeedbackGraph(dataset.matrix)
    adaptive = AdaptiveRWEB(g, theta, item_pos, exposure,
                            epsilon_low=epsilon_low, epsilon_high=epsilon_high)
    uniform_eps = float(np.mean(adaptive.epsilon))       # same AVERAGE dose, flat across users
    uniform = RWEB(g, theta, item_pos, epsilon=uniform_eps)

    idx = np.flatnonzero(measured)                       # demo on users with a real signal
    if idx.size == 0:
        return "no users have a measured cross-cutting signal in this probe CSV."
    rng = np.random.default_rng(seed)
    if idx.size > sample:
        idx = rng.choice(idx, size=sample, replace=False)
    tol = exposure[idx]
    a_reach = opposite_reach(adaptive, idx, theta, item_pos, k)
    u_reach = opposite_reach(uniform, idx, theta, item_pos, k)

    from scipy.stats import spearmanr
    ok = np.isfinite(a_reach)
    rho = (spearmanr(tol[ok], a_reach[ok])[0]
           if ok.sum() >= 3 and np.ptp(tol[ok]) > 0 and np.ptp(a_reach[ok]) > 0
           else float("nan"))

    out = [
        "CLOSED-LOOP: measured reception -> AdaptiveRWEB exposure (real, not simulated)",
        "=" * 76,
        f"users: {dataset.n_users:,} total; {int(measured.sum()):,} "
        f"({100*measured.mean():.0f}%) have a measured cross-cutting signal "
        "(the rest get the neutral default).",
        f"measured exposure: min {exposure[measured].min():.2f}  "
        f"median {np.median(exposure[measured]):.2f}  max {exposure[measured].max():.2f}  "
        f"-> epsilon in [{epsilon_low + exposure[measured].min()*(epsilon_high-epsilon_low):.2f}, "
        f"{epsilon_low + exposure[measured].max()*(epsilon_high-epsilon_low):.2f}]",
        f"uniform baseline epsilon (same average dose) = {uniform_eps:.3f}",
        "",
        f"rank-weighted opposite-content reach in top-{k}, by measured tolerance "
        f"(demo on {idx.size:,} signalled users):",
        _tercile_table(tol, a_reach, u_reach),
        "",
        f"Spearman(measured tolerance, adaptive reach) = {rho:+.3f}  "
        "(positive = the dose lands where the data says it is tolerated)",
        "",
        "READ: vs a uniform recommender at the SAME average dose, the measured-adaptive "
        "policy gives low-tolerance users a gentler bridging dose and high-tolerance users "
        "a stronger one (adaptive reach rises across the terciles; uniform stays flat) -- "
        "personalisation from real engagement. CAVEATS: the signal is self-selected (only "
        f"the users who cross over carry it -- {100*measured.mean():.0f}% of these served "
        "users, an upper bound), and it is coarse per user (most have 1-2 cross-cutting "
        "comments, so the fraction is near 0 or 1; the policy mainly spares those whose "
        "bridges drew pushback). So this is a mechanism demonstration on real data, not a "
        "settled satisfaction gain.",
    ]
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--npz", required=True, help="validated-axis .npz (item_positions)")
    ap.add_argument("--probe-csv", required=True,
                    help="per-user CSV from satisfaction_probe.py --out")
    ap.add_argument("--k", type=int, default=10, help="top-k recommendations to score")
    ap.add_argument("--sample", type=int, default=4000, help="signalled users to demo on")
    ap.add_argument("--epsilon-low", type=float, default=0.5)
    ap.add_argument("--epsilon-high", type=float, default=0.95)
    args = ap.parse_args()
    print(run(args.npz, args.probe_csv, k=args.k, sample=args.sample,
              epsilon_low=args.epsilon_low, epsilon_high=args.epsilon_high))


if __name__ == "__main__":
    main()
