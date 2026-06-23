"""Generate the diagrams embedded in GUIDE.md.

Produces three PNGs in ``docs/images/``:

* ``bipartite_graph.png`` -- the user-item feedback graph (GUIDE section 3.2)
* ``rwe_flow.png``        -- the random-walk-with-erasure mechanism (section 3.4)
* ``ideology_scale.png``  -- the left-right ideology scale + bridging (section 3.5)

Run with::

    python docs/make_diagrams.py

The script is deterministic, so re-running it reproduces identical figures.
"""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless / no display
import matplotlib.pyplot as plt
from matplotlib.patches import Circle, FancyArrowPatch, FancyBboxPatch

OUT = Path(__file__).resolve().parent / "images"

# Consistent palette.
USER_C = "#4C72B0"     # blue
ITEM_C = "#DD8452"     # amber
KEEP_C = "#55A868"     # green  (kept / recommended)
ERASE_C = "#C44E52"    # red    (erased / suppressed)
INK = "#2b2b2b"
MUTE = "#8a8a8a"


# --------------------------------------------------------------------------- #
# 1. Bipartite feedback graph
# --------------------------------------------------------------------------- #
def bipartite_graph():
    users = ["Alice", "Bob", "Carol"]
    items = ["Article 1", "Article 2", "Article 3", "Article 4"]
    edges = [(0, 0), (0, 1), (1, 1), (1, 2), (2, 2), (2, 3)]  # who liked what

    fig, ax = plt.subplots(figsize=(7.5, 4.6))
    ux, ix = 0.0, 3.0
    uy = {i: y for i, y in enumerate([3.2, 2.0, 0.8])}
    iy = {j: y for j, y in enumerate([3.6, 2.6, 1.6, 0.6])}

    for u, i in edges:
        ax.plot([ux, ix], [uy[u], iy[i]], color=MUTE, lw=1.6, zorder=1)

    for u, name in enumerate(users):
        ax.add_patch(Circle((ux, uy[u]), 0.22, color=USER_C, zorder=3))
        ax.text(ux - 0.4, uy[u], name, ha="right", va="center",
                fontsize=11, color=INK)
    for j, name in enumerate(items):
        ax.add_patch(FancyBboxPatch((ix - 0.22, iy[j] - 0.16), 0.44, 0.32,
                                    boxstyle="round,pad=0.02", color=ITEM_C, zorder=3))
        ax.text(ix + 0.4, iy[j], name, ha="left", va="center",
                fontsize=11, color=INK)

    ax.text(ux, 4.2, "USERS", ha="center", fontsize=12, weight="bold", color=USER_C)
    ax.text(ix, 4.2, "ITEMS", ha="center", fontsize=12, weight="bold", color=ITEM_C)
    ax.text(1.5, -0.25, "a line = “this user liked this item” (implicit feedback)",
            ha="center", fontsize=10, style="italic", color=MUTE)
    ax.set_title("The bipartite feedback graph", fontsize=14, weight="bold", color=INK)

    ax.set_xlim(-1.6, 4.6)
    ax.set_ylim(-0.6, 4.6)
    ax.axis("off")
    _save(fig, "bipartite_graph.png")


# --------------------------------------------------------------------------- #
# 2. Random walk with erasure (the "tax" mechanism)
# --------------------------------------------------------------------------- #
def rwe_flow():
    fig, ax = plt.subplots(figsize=(11.4, 5.8))

    # Origin user.
    sx, sy = 1.05, 3.0
    ax.add_patch(Circle((sx, sy), 0.5, color=USER_C, zorder=5))
    ax.text(sx, sy, "S\n(you)", ha="center", va="center", color="white",
            fontsize=11, weight="bold", zorder=6)

    # Two representative landing pages with different tax (erasure) levels.
    # Each erased arrow leaves the page's *outer* corner and bows outward (top
    # up, bottom down), well clear of the incoming walk arrow and the box.
    items = [
        dict(y=4.7, q=0.9, title="Popular / same-side page",
             note="high tax  q = 0.9", color=ERASE_C, side=+1, rad=0.5),
        dict(y=1.3, q=0.2, title="Niche / opposite-side bridge",
             note="low tax  q = 0.2", color=KEEP_C, side=-1, rad=-0.5),
    ]
    bxl, bxr = 5.0, 7.4          # page box left / right (more gap from S)
    rec_l, rec_r = 9.5, 11.2     # recommendation box left / right

    def lw(frac):               # capped thickness encoding (no blobs)
        return 1.2 + 3.2 * frac

    # Recommendation zone.
    ax.add_patch(FancyBboxPatch((rec_l, 0.55), rec_r - rec_l, 4.9,
                                boxstyle="round,pad=0.05", fc="#eef5ee",
                                ec=KEEP_C, lw=1.5, zorder=1))
    ax.text((rec_l + rec_r) / 2, 5.0, "Recommend-\nation list", ha="center",
            va="center", fontsize=10, color=KEEP_C, weight="bold")

    for it in items:
        iy, side = it["y"], it["side"]
        ax.add_patch(FancyBboxPatch((bxl, iy - 0.45), bxr - bxl, 0.9,
                                    boxstyle="round,pad=0.03", fc="white",
                                    ec=it["color"], lw=2, zorder=3))
        ax.text((bxl + bxr) / 2, iy + 0.13, it["title"], ha="center",
                va="center", fontsize=9.5, color=INK)
        ax.text((bxl + bxr) / 2, iy - 0.22, it["note"], ha="center",
                va="center", fontsize=8.5, color=it["color"], style="italic")

        # forward walk (solid black): S -> page LEFT-CENTER (one clean arrow)
        ax.add_patch(FancyArrowPatch((sx + 0.46, sy + 0.18 * side),
                                     (bxl - 0.06, iy), arrowstyle="-|>",
                                     mutation_scale=15, color=INK, lw=1.6, zorder=2))

        # kept (green): page -> rec list, thickness ~ kept fraction (1-q)
        ax.add_patch(FancyArrowPatch((bxr + 0.06, iy), (rec_l - 0.06, iy),
                                     arrowstyle="-|>", mutation_scale=15,
                                     color=KEEP_C, lw=lw(1.0 - it["q"]), zorder=2))
        ax.text((bxr + rec_l) / 2, iy + 0.32, "(1−q)·p kept", ha="center",
                fontsize=8.2, color=KEEP_C)

        # erased (red dashed): page OUTER corner -> S, bowed outward,
        # thickness ~ erased fraction q
        ax.add_patch(FancyArrowPatch((bxl + 0.15, iy + 0.42 * side),
                                     (sx + 0.12, sy + 0.42 * side),
                                     arrowstyle="-|>", mutation_scale=13,
                                     color=ERASE_C, lw=lw(it["q"]),
                                     ls=(0, (6, 4)),
                                     connectionstyle=f"arc3,rad={it['rad']}",
                                     zorder=2))

    ax.text(2.95, 3.0, "walk · 3 hops · prob p", ha="center", va="center",
            fontsize=8.5, color=INK, rotation=0)
    ax.text(3.0, 6.05, "erased  q·p  →  back to S", ha="center", fontsize=8.5,
            color=ERASE_C, style="italic")
    ax.text(5.6, -0.55,
            "score(item) = p(1−q) / (1 − Σ pq)        "
            "high tax → suppressed,    low tax → recommended",
            ha="center", fontsize=10.5, color=INK)
    ax.set_title("Random Walk with Erasure: a “tax” that steers recommendations",
                 fontsize=13.5, weight="bold", color=INK)

    ax.set_xlim(0, 11.6)
    ax.set_ylim(-0.85, 6.4)
    ax.axis("off")
    _save(fig, "rwe_flow.png")


# --------------------------------------------------------------------------- #
# 3. Ideology scale + bridging (reproduces the paper's Figure 1)
# --------------------------------------------------------------------------- #
def ideology_scale():
    fig, ax = plt.subplots(figsize=(9.0, 3.0))
    ax.axhline(0, color=INK, lw=1.5, zorder=1)
    for x in range(-2, 3):
        ax.plot([x, x], [-0.06, 0.06], color=INK, lw=1.2)
        ax.text(x, -0.22, str(x), ha="center", fontsize=10, color=INK)

    users = [("u1", -1.0, USER_C), ("u2", 0.8, ERASE_C),
             ("u3", 1.0, ERASE_C), ("u4", 1.2, ERASE_C)]
    for name, x, c in users:
        ax.add_patch(Circle((x, 0), 0.07, color=c, zorder=4))
        ax.text(x, 0.2, name, ha="center", fontsize=11, color=c, weight="bold")

    ax.text(-1.0, 0.6, "left-leaning", ha="center", fontsize=10, color=USER_C)
    ax.text(1.0, 0.6, "right-leaning", ha="center", fontsize=10, color=ERASE_C)
    ax.text(0, 0.42, "center", ha="center", fontsize=9, color=MUTE)

    # Bridging arc: from left user u1 across the center to the opposite side.
    ax.add_patch(FancyArrowPatch((-1.0, 0.12), (0.8, 0.12),
                                 arrowstyle="-|>", mutation_scale=14,
                                 color=KEEP_C, lw=2,
                                 connectionstyle="arc3,rad=-0.5", zorder=3))
    ax.text(-0.1, 0.92, "RWE-B surfaces reachable opposite-side “bridges”",
            ha="center", fontsize=9.5, color=KEEP_C, style="italic")

    ax.set_title("One-dimensional ideology scale (paper's Figure 1)",
                 fontsize=13, weight="bold", color=INK)
    ax.set_xlim(-2.4, 2.4)
    ax.set_ylim(-0.5, 1.2)
    ax.axis("off")
    _save(fig, "ideology_scale.png")


def quadrant_scatter():
    """Result II: user position (x) vs mean recommended position (y), per user.

    Generated from real recommender output: a baseline keeps users in their own
    quadrant (dots track the diagonal), while RWE-B pulls them toward the centre
    / opposite side (dots flatten toward y=0).
    """
    import numpy as np
    from rwe import FeedbackGraph, P3, RWEB, data, metrics

    d = data.synthetic_political(n_users=300, n_items=100, seed=2)
    g = FeedbackGraph(d["matrix"])
    upos, ipos = d["user_positions"], d["item_positions"]
    users = np.arange(g.m)
    panels = [
        ("Baseline (P3)", P3(g).recommend(users, top_k=10),
         "recommendations stay on the\nuser's own side (along y = x)"),
        ("RWE-B (bridging)", RWEB(g, upos, ipos, epsilon=0.8).recommend(users, top_k=10),
         "recommendations pulled toward\nthe centre / opposite side"),
    ]

    fig, axes = plt.subplots(1, 2, figsize=(10.5, 5.2), sharex=True, sharey=True)
    colors = [USER_C if u < 0 else ERASE_C for u in upos]
    for ax, (title, recs, note) in zip(axes, panels):
        y = metrics.mean_recommended_position(recs, ipos)
        ax.axhline(0, color=MUTE, lw=1)
        ax.axvline(0, color=MUTE, lw=1)
        ax.plot([-2.2, 2.2], [-2.2, 2.2], ls="--", color="#c2c2c2", lw=1, zorder=1)
        ax.scatter(upos, y, c=colors, s=20, alpha=0.7, zorder=3, edgecolors="none")
        ax.set_title(title, fontsize=12, weight="bold", color=INK)
        ax.set_xlabel("user ideological position", fontsize=10, color=INK)
        ax.set_xlim(-2.3, 2.3)
        ax.set_ylim(-2.3, 2.3)
        ax.set_aspect("equal")
        ax.text(0, -2.05, note, ha="center", va="bottom", fontsize=8.5,
                style="italic", color=MUTE)
    axes[0].set_ylabel("mean position of\nrecommended items", fontsize=10, color=INK)
    fig.suptitle("Result II: where recommendations sit vs the user's own side",
                 fontsize=13.5, weight="bold", color=INK)
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    _save(fig, "quadrant_scatter.png")


# --------------------------------------------------------------------------- #
# Extension 1 (satisfaction.py): adaptive exposure calibrates the dose per user
# --------------------------------------------------------------------------- #
def adaptive_exposure():
    import numpy as np
    from rwe import (FeedbackGraph, RWEB, AdaptiveRWEB, WebGraph,
                     SatisfactionModel, data)

    d = data.synthetic_political(n_users=400, n_items=120, seed=1)
    g = FeedbackGraph(d["matrix"])
    upos, ipos = d["user_positions"], d["item_positions"]
    center = float(np.median(upos))
    users = np.arange(g.m)
    web = WebGraph(g, ipos)
    web.detect_communities(knn=5, seed=0)
    exposure = SatisfactionModel(web, upos, n_walks=25, walk_length=40,
                                 seed=0).exposure(users)
    med = np.median(exposure)
    masks = {"low-tolerance\nusers": exposure <= med, "high-tolerance\nusers": exposure > med}

    def opp(recs, mask):
        fr = []
        for r, u in zip(recs[mask], users[mask]):
            it = r[r >= 0]
            if it.size:
                fr.append((np.sign(ipos[it] - center) == -np.sign(upos[u] - center)).mean())
        return float(np.mean(fr))

    fixed = RWEB(g, upos, ipos, epsilon=0.9).recommend(users, top_k=10)
    adapt = AdaptiveRWEB(g, upos, ipos, exposure=exposure).recommend(users, top_k=10)
    labels = list(masks)
    fixed_v = [opp(fixed, masks[k]) for k in labels]
    adapt_v = [opp(adapt, masks[k]) for k in labels]

    fig, ax = plt.subplots(figsize=(8.4, 4.7))
    x = np.arange(2)
    w = 0.36
    ax.bar(x - w / 2, fixed_v, w, color=ERASE_C, label="fixed RWE-B (ε=0.9)")
    ax.bar(x + w / 2, adapt_v, w, color=KEEP_C, label="AdaptiveRWEB (satisfaction-driven)")
    for xi, v in zip(x - w / 2, fixed_v):
        ax.text(xi, v + 0.02, f"{v:.2f}", ha="center", fontsize=10, color=ERASE_C)
    for xi, v in zip(x + w / 2, adapt_v):
        ax.text(xi, v + 0.02, f"{v:.2f}", ha="center", fontsize=10, color=KEEP_C)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=11, color=INK)
    ax.set_ylabel("opposite-content fraction in top-10", fontsize=11, color=INK)
    ax.set_ylim(0, 1.32)
    ax.legend(fontsize=9, loc="upper center", ncol=2, frameon=False)
    ax.set_title("Adaptive exposure calibrates the dose per user",
                 fontsize=13, weight="bold", color=INK)
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    ax.text(0.5, -0.22, "fixed bridging flips everyone; adaptive protects low-tolerance "
            "users while bridging high-tolerance ones", transform=ax.transAxes,
            ha="center", fontsize=8.5, style="italic", color=MUTE)
    ax.text(0.5, -0.31, "synthetic data; planted ideological positions (illustrative)",
            transform=ax.transAxes, ha="center", fontsize=8, color="#9a9a9a")
    _save(fig, "adaptive_exposure.png")


# --------------------------------------------------------------------------- #
# Extension 2 (agent_sim.py): satisfaction falls as confirmation bias rises
# --------------------------------------------------------------------------- #
def alpha_sweep_curve():
    import numpy as np
    from rwe.agent_sim import (make_synthetic_web_graph, detect_communities,
                               assign_community_ideology, alpha_sweep)

    G, latent = make_synthetic_web_graph(block_ideologies=(-1.5, 0.0, 1.5),
                                         block_size=30, seed=0)
    nc = detect_communities(G, method="louvain", seed=0)
    node_ideo, _ = assign_community_ideology(latent, nc)
    alphas = [-1.0, -0.5, 0.0, 0.5, 1.0, 2.0]
    df = alpha_sweep(G, node_ideo, nc, alphas=alphas, positions=[-1.5, 1.5],
                     n_trials=300, max_steps=200, seed=0)

    # Average the (ideologically symmetric) left and right agents into one
    # representative curve -- their gap is a random-graph artifact, not a real
    # left/right effect, so collapsing it keeps the monotonic-decrease message.
    avg = (df["u=-1.5"].values + df["u=+1.5"].values) / 2.0

    fig, ax = plt.subplots(figsize=(8.4, 4.7))
    ax.plot(alphas, avg, "-o", color=USER_C, lw=2.2,
            label="partisan agent (mean of left & right)")
    ax.axvline(0, color=MUTE, lw=1, ls=":")
    ax.text(-0.55, ax.get_ylim()[1] * 0.92, "rabbit hole", ha="center",
            fontsize=9, color=MUTE, style="italic")
    ax.text(1.0, ax.get_ylim()[1] * 0.92, "confirmation bias", ha="center",
            fontsize=9, color=MUTE, style="italic")
    ax.set_xlabel("α   (transition-policy bias)", fontsize=11, color=INK)
    ax.set_ylabel("mean satisfaction score", fontsize=11, color=INK)
    ax.set_title("Satisfaction falls monotonically with confirmation bias",
                 fontsize=13, weight="bold", color=INK)
    ax.legend(fontsize=10)
    ax.text(0.5, -0.20, "synthetic stochastic-block-model web graph; planted "
            "communities (illustrative)", transform=ax.transAxes, ha="center",
            fontsize=8, color="#9a9a9a")
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    _save(fig, "alpha_sweep.png")


# --------------------------------------------------------------------------- #
# Polarization outcome: does opposite-view exposure help or hurt? (opinion_dynamics.py)
# --------------------------------------------------------------------------- #
def opinion_dynamics():
    import numpy as np
    from rwe import opinion_dynamics as od

    theta0 = od.initial_population(n_users=400, seed=0)
    hist = od.compare_policies(theta0, n_rounds=40, seed=0)
    rounds = list(range(len(next(iter(hist.values())))))
    colors = {"echo chamber": "#b08a3e", "naive opposite-blast": ERASE_C,
              "RWE-B bridging": KEEP_C, "adaptive (satisfaction)": USER_C}
    styles = {"echo chamber": "--", "naive opposite-blast": "-",
              "RWE-B bridging": "-", "adaptive (satisfaction)": "--"}

    fig, ax = plt.subplots(figsize=(8.8, 4.9))
    for name, h in hist.items():
        ax.plot(rounds, h, styles[name], color=colors[name], lw=2.3, label=name)
    ax.axhline(hist["RWE-B bridging"][0], color=MUTE, lw=1, ls=":")
    ax.text(len(rounds) * 0.62, hist["RWE-B bridging"][0] + 0.05, "start",
            fontsize=8.5, color=MUTE)
    ax.set_xlabel("round (repeated exposure)", fontsize=11, color=INK)
    ax.set_ylabel("population polarization (std of positions)", fontsize=11, color=INK)
    ax.set_title("Bounded bridging lowers polarization; naive opposite-blast raises it",
                 fontsize=12.5, weight="bold", color=INK)
    ax.legend(fontsize=9, loc="center right", framealpha=0.95)
    ax.annotate("diverging\n(polarizing)", xy=(len(rounds) * 0.5, 2.2),
                fontsize=9, color=ERASE_C, ha="center", style="italic")
    ax.annotate("converging\n(depolarizing)", xy=(len(rounds) * 0.5, 0.45),
                fontsize=9, color="#2E6B3E", ha="center", style="italic")
    ax.text(0.5, -0.20, "stylized assimilation–contrast model (Social Judgment "
            "Theory); illustrative direction, not magnitude", transform=ax.transAxes,
            ha="center", fontsize=8, color="#9a9a9a")
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    _save(fig, "opinion_dynamics.png")


# --------------------------------------------------------------------------- #
# Closed-loop guardrails: detect backfire (drift / engagement) and cut the dose
# --------------------------------------------------------------------------- #
def guardrails_diagram():
    import numpy as np  # noqa: F401
    from rwe import guardrails as gr, opinion_dynamics as od

    theta0 = od.initial_population(n_users=400, seed=0)
    res = gr.compare_guardrails(theta0, n_rounds=40)
    rounds = list(range(len(next(iter(res.values()))["polarization"])))
    colors = {"no guardrail": ERASE_C, "backfire monitor (drift)": KEEP_C,
              "engagement early-warning": USER_C}

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11.2, 4.7))
    for name, r in res.items():
        ax1.plot(rounds, r["polarization"], color=colors[name], lw=2.3, label=name)
        ax2.plot(rounds, r["dose"], color=colors[name], lw=2.3, label=name)
    ax1.set_title("Polarization outcome", fontsize=12, weight="bold", color=INK)
    ax1.set_xlabel("round", fontsize=10, color=INK)
    ax1.set_ylabel("polarization (std of positions)", fontsize=10, color=INK)
    ax1.legend(fontsize=8.3, loc="center right")
    ax2.set_title("Dose (the guardrail's action)", fontsize=12, weight="bold", color=INK)
    ax2.set_xlabel("round", fontsize=10, color=INK)
    ax2.set_ylabel("mean opposite-content dose", fontsize=10, color=INK)
    for ax in (ax1, ax2):
        for sp in ("top", "right"):
            ax.spines[sp].set_visible(False)
    fig.suptitle("Closed-loop guardrails detect backfire and cut the dose",
                 fontsize=13.5, weight="bold", color=INK)
    fig.text(0.5, 0.005, "an un-guarded aggressive dose backfires (red); the "
             "guardrails pull the dose into the safe, depolarizing zone — stylized model",
             ha="center", fontsize=8, color="#9a9a9a")
    fig.tight_layout(rect=(0, 0.03, 1, 0.93))
    _save(fig, "guardrails.png")


def _save(fig, name):
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / name
    fig.savefig(path, dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"wrote {path}")


if __name__ == "__main__":
    bipartite_graph()
    rwe_flow()
    ideology_scale()
    quadrant_scatter()
    adaptive_exposure()
    alpha_sweep_curve()
    opinion_dynamics()
    guardrails_diagram()
