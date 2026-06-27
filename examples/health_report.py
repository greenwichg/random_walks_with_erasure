"""Personalized Information Health Report (v1) — a descriptive consumption profile.

Aggregates each user's *clicked* articles (their row of the click matrix) into a
reading-diet profile, using only metadata already in an ingested MIND ``.npz``
(``categories``, ``outlets``, ``item_positions`` lean, ``political`` mask) -- no
new models.  See ``docs/HEALTH_REPORT_PLAN.md`` for the feasibility scope.

    python examples/health_report.py --npz mind_text.npz --sample 3

==========================  v1 metric spec (frozen)  ==========================
For a user u, over their clicked items (shares sum to 1):

* Topic diversity  = normalized Shannon entropy of the category distribution
      H_norm = ( -Σ_c p_c·ln p_c ) / ln(C)
  where p_c = user's share of category c and C = #distinct categories in the
  *catalog* (fixed denominator -> comparable across users).  0 = one topic,
  1 = uniform over every catalog topic.
* Source concentration  = Herfindahl  HHI = Σ_o s_o²  over publisher shares s_o
  (empty/unknown outlets excluded); effective #sources = 1/HHI (inverse Simpson);
  top-N share = sum of the N largest s_o (the "X% from N publishers" line).
* Viewpoint (political subset only: political & known lean):
      left/centre/right = share with pos < -τ / |pos| ≤ τ / pos > τ   (τ = 0.5);
      cross-cutting share = share on the *opposite* side of the user's own mean;
      echo = 1 - 2·min(L,R)/(L+R)   in [0,1]   (0 = balanced L/R, 1 = one-sided).
* Scores reported as **population percentiles** over users above the click floor
  (higher = more diverse / more cross-cutting / less echo).  The composite
  "Overall" is an *unweighted, illustrative* average of the v1 percentiles only.

Boundaries:  topic & source over ALL clicks;  viewpoint/echo over the political
subset.  Users with < ``--min-clicks`` clicks get no scores; viewpoint needs
≥ ``--min-political`` political clicks.  Reporting-ratio and emotional exposure
are deliberately **out of v1** (need new classifiers -- see the plan).
==============================================================================
"""

import argparse

import numpy as np
import scipy.sparse as sp
from scipy.stats import rankdata

from rwe.mind import MINDData

LEAN_TAU = 0.5     # |pos| <= tau is "centre"
CENTER = 0.0


# --------------------------------------------------------------------------- #
# Pure metric helpers (unit-tested)
# --------------------------------------------------------------------------- #
def shares(counts) -> np.ndarray:
    counts = np.asarray(counts, dtype=float)
    total = counts.sum()
    return counts / total if total > 0 else counts


def normalized_entropy(share_vec, n_categories: int) -> float:
    """Shannon entropy of ``share_vec`` divided by ``ln(n_categories)`` -> [0, 1]."""
    p = np.asarray(share_vec, dtype=float)
    p = p[p > 0]
    if p.size == 0 or n_categories <= 1:
        return float("nan")
    return float(-(p * np.log(p)).sum() / np.log(n_categories))


def hhi(share_vec) -> float:
    """Herfindahl concentration Σ s² (1 = one source, →0 = many)."""
    p = np.asarray(share_vec, dtype=float)
    return float((p * p).sum())


def effective_number(share_vec) -> float:
    """Inverse-Simpson effective count = 1 / HHI."""
    h = hhi(share_vec)
    return float(1.0 / h) if h > 0 else float("nan")


def top_n_share(share_vec, n: int) -> float:
    p = np.sort(np.asarray(share_vec, dtype=float))[::-1]
    return float(p[:n].sum())


def viewpoint_shares(positions, tau: float = LEAN_TAU):
    """(left, centre, right) fractions of political items by lean band."""
    p = np.asarray(positions, dtype=float)
    p = p[np.isfinite(p)]
    if p.size == 0:
        return float("nan"), float("nan"), float("nan")
    left = float(np.mean(p < -tau))
    right = float(np.mean(p > tau))
    return left, 1.0 - left - right, right


def cross_cutting_share(positions, center: float = CENTER) -> float:
    """Share of political items on the *opposite* side of the user's own mean."""
    p = np.asarray(positions, dtype=float)
    p = p[np.isfinite(p)]
    if p.size == 0:
        return float("nan")
    mu = p.mean()
    if mu == center:
        return float("nan")
    opposite = (p - center) * np.sign(mu - center) < 0
    return float(np.mean(opposite))


def echo_score(left: float, right: float) -> float:
    """0 (balanced L/R) .. 1 (one-sided) = 1 - 2·min(L,R)/(L+R)."""
    nonc = left + right
    if nonc <= 0:
        return float("nan")
    return float(max(0.0, 1.0 - 2.0 * (min(left, right) / nonc)))


def percentiles(values) -> np.ndarray:
    """Percentile rank (0-100) of each finite entry within the finite entries."""
    v = np.asarray(values, dtype=float)
    out = np.full(v.shape, np.nan)
    ok = np.isfinite(v)
    k = int(ok.sum())
    if k == 1:
        out[ok] = 50.0
    elif k > 1:
        out[ok] = (rankdata(v[ok], method="average") - 1) / (k - 1) * 100.0
    return out


# --------------------------------------------------------------------------- #
# Population aggregation
# --------------------------------------------------------------------------- #
def _onehot(labels):
    uniq, inv = np.unique(labels, return_inverse=True)
    n = len(labels)
    oh = sp.csr_matrix((np.ones(n), (np.arange(n), inv)), shape=(n, len(uniq)))
    return oh, uniq


def _row_shares(M):
    tot = M.sum(axis=1, keepdims=True)
    return np.divide(M, tot, where=tot > 0, out=np.zeros_like(M, dtype=float))


def compute(mind: MINDData, min_clicks: int = 5, min_political: int = 3,
            top_n: int = 4) -> dict:
    """Per-user raw metrics + population percentiles + the aux matrices."""
    A = mind.dataset.matrix.tocsr().astype(float)
    n_users = A.shape[0]
    cats = np.asarray(mind.categories)
    outs = np.asarray(mind.outlets)
    pos = np.asarray(mind.item_positions, dtype=float)
    pol = np.asarray(mind.political, dtype=bool)
    n_clicks = np.asarray(A.sum(axis=1)).ravel()

    # Topic & source distributions per user (vectorised matrix products).
    cat_oh, cat_u = _onehot(cats)
    out_oh, out_u = _onehot(outs)
    UC = np.asarray((A @ cat_oh).todense())            # users x categories
    UO = np.asarray((A @ out_oh).todense())            # users x outlets
    if "" in set(out_u):                               # drop the unknown-outlet bucket
        keep = out_u != ""
        UO, out_u = UO[:, keep], out_u[keep]

    n_cat = len(cat_u)
    SC, SO = _row_shares(UC), _row_shares(UO)
    with np.errstate(divide="ignore", invalid="ignore"):
        topic = -np.nansum(np.where(SC > 0, SC * np.log(SC), 0.0), axis=1) / np.log(n_cat)
    hhi_u = (SO * SO).sum(axis=1)
    eff_src = np.divide(1.0, hhi_u, where=hhi_u > 0,
                        out=np.full_like(hhi_u, np.nan, dtype=float))
    topn = np.sort(SO, axis=1)[:, ::-1][:, :top_n].sum(axis=1)

    enough = n_clicks >= min_clicks
    for arr in (topic, eff_src, topn):
        arr[~enough] = np.nan

    # Viewpoint metrics over each user's political clicks.
    cross = np.full(n_users, np.nan)
    echo = np.full(n_users, np.nan)
    mean_lean = np.full(n_users, np.nan)
    n_pol = np.zeros(n_users, dtype=int)
    for u in range(n_users):
        items = A.indices[A.indptr[u]:A.indptr[u + 1]]
        pp = pos[items][pol[items] & np.isfinite(pos[items])]
        n_pol[u] = pp.size
        if pp.size < min_political:
            continue
        L, _, R = viewpoint_shares(pp)
        cross[u] = cross_cutting_share(pp)
        echo[u] = echo_score(L, R)
        mean_lean[u] = float(pp.mean())

    return dict(
        n_clicks=n_clicks, n_pol=n_pol,
        topic=topic, eff_src=eff_src, topn=topn, cross=cross, echo=echo,
        mean_lean=mean_lean,
        topic_pct=percentiles(topic), source_pct=percentiles(eff_src),
        viewpoint_pct=percentiles(cross), echo_pct=percentiles(-echo),  # less echo = higher
        UC=UC, UO=UO, cat_u=cat_u, out_u=out_u,
        catalog_cat_share=shares(np.bincount(_onehot(cats)[0].indices, minlength=n_cat)),
        top_n=top_n,
    )


def user_report(pop: dict, mind: MINDData, u: int) -> dict:
    """Assemble the detailed report dict for one user."""
    UC, UO, cat_u, out_u = pop["UC"], pop["UO"], pop["cat_u"], pop["out_u"]
    p_c = shares(UC[u])
    s_o = shares(UO[u])
    q_c = pop["catalog_cat_share"]

    top_cats = sorted(((cat_u[i], p_c[i]) for i in np.argsort(-p_c)[:3] if p_c[i] > 0),
                      key=lambda x: -x[1])
    gaps = sorted(((cat_u[i], p_c[i], q_c[i]) for i in range(len(cat_u))
                   if q_c[i] > 0.02 and p_c[i] < 0.5 * q_c[i]),
                  key=lambda x: -(x[2] - x[1]))
    top_pubs = [(out_u[i], s_o[i]) for i in np.argsort(-s_o)[:pop["top_n"]] if s_o[i] > 0]

    def _sc(arr):
        v = pop[arr][u]
        return None if not np.isfinite(v) else round(float(v))

    scores = {"Topic Diversity": _sc("topic_pct"),
              "Source Diversity": _sc("source_pct"),
              "Viewpoint Balance": _sc("viewpoint_pct"),
              "Echo Chamber Score": _sc("echo_pct")}
    have = [v for v in scores.values() if v is not None]
    overall = round(float(np.mean(have))) if have else None

    return dict(
        user=int(u), n_clicks=int(pop["n_clicks"][u]), n_political=int(pop["n_pol"][u]),
        scores=scores, overall=overall,
        top_categories=top_cats, blind_spots=gaps[:2], top_publishers=top_pubs,
        top_n_share=float(pop["topn"][u]) if np.isfinite(pop["topn"][u]) else None,
        effective_sources=float(pop["eff_src"][u]) if np.isfinite(pop["eff_src"][u]) else None,
        distinct_outlets=int((UO[u] > 0).sum()),
        viewpoint=viewpoint_shares(_political_positions(mind, u)),
        mean_lean=float(pop["mean_lean"][u]) if np.isfinite(pop["mean_lean"][u]) else None,
    )


def _political_positions(mind: MINDData, u: int) -> np.ndarray:
    A = mind.dataset.matrix.tocsr()
    pos = np.asarray(mind.item_positions, dtype=float)
    pol = np.asarray(mind.political, dtype=bool)
    items = A.indices[A.indptr[u]:A.indptr[u + 1]]
    return pos[items][pol[items] & np.isfinite(pos[items])]


def format_report(rep: dict) -> str:
    """Render the report dict as the INFORMATION HEALTH REPORT text block."""
    L = ["INFORMATION HEALTH REPORT", "=" * 32,
         f"user #{rep['user']}   ({rep['n_clicks']} articles read, "
         f"{rep['n_political']} political)\n"]
    if rep["overall"] is not None:
        L.append(f"Overall Score: {rep['overall']}/100   "
                 "(illustrative unweighted avg of v1 dimensions)\n")
    for name, v in rep["scores"].items():
        L.append(f"{name}: {v}/100" if v is not None else f"{name}: n/a")
    L.append("Reporting Ratio: n/a (v2)")
    L.append("Emotional Balance: n/a (v2)\n")

    if rep["top_n_share"] is not None and rep["top_publishers"]:
        L.append("Biggest Insight:")
        L.append(f"  {rep['top_n_share'] * 100:.0f}% of your reading came from your "
                 f"top {len(rep['top_publishers'])} publishers "
                 f"(you read {rep['distinct_outlets']} distinct sources).\n")
    if rep["blind_spots"]:
        cat, us, cs = rep["blind_spots"][0]
        L.append("Blind Spot:")
        L.append(f"  You read little '{cat}' news — {us * 100:.0f}% of your reading "
                 f"vs {cs * 100:.0f}% of the catalog.\n")
    if rep["top_categories"]:
        L.append("Top topics:  " + ", ".join(f"{c} {s * 100:.0f}%"
                                              for c, s in rep["top_categories"]))
    lo, ce, ri = rep["viewpoint"]
    if np.isfinite(lo):
        L.append(f"Viewpoint mix: left {lo * 100:.0f}% · centre {ce * 100:.0f}% · "
                 f"right {ri * 100:.0f}%")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--npz", required=True, help="ingested MIND .npz (from ingest_mind.py)")
    ap.add_argument("--user", type=int, default=None, help="report a single user index")
    ap.add_argument("--sample", type=int, default=3, help="report this many random users")
    ap.add_argument("--min-clicks", type=int, default=5)
    ap.add_argument("--min-political", type=int, default=3)
    ap.add_argument("--top-n", type=int, default=4, help="publishers in the concentration line")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    mind = MINDData.load(args.npz)
    pop = compute(mind, min_clicks=args.min_clicks, min_political=args.min_political,
                  top_n=args.top_n)
    eligible = np.flatnonzero(pop["n_clicks"] >= args.min_clicks)
    print(f"users={mind.n_users}  items={mind.n_items}  eligible(>= {args.min_clicks} "
          f"clicks)={eligible.size}\n")
    if eligible.size == 0:
        print("no users meet the click floor."); return

    if args.user is not None:
        users = [args.user]
    else:
        rng = np.random.default_rng(args.seed)
        users = rng.choice(eligible, size=min(args.sample, eligible.size), replace=False)
    for u in users:
        print(format_report(user_report(pop, mind, int(u))))
        print("\n" + "-" * 60 + "\n")


if __name__ == "__main__":
    main()
