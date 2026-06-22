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
    fig, ax = plt.subplots(figsize=(10.5, 5.6))

    # Origin user.
    sx, sy = 1.2, 3.0
    ax.add_patch(Circle((sx, sy), 0.5, color=USER_C, zorder=5))
    ax.text(sx, sy, "S\n(you)", ha="center", va="center", color="white",
            fontsize=11, weight="bold", zorder=6)

    # Two representative landing pages with different tax (erasure) levels.
    # rad sign routes each erase-arrow outward (top bows up, bottom bows down)
    # so the red return arrows stay clear of the black forward arrows.
    items = [
        dict(y=4.6, q=0.9, title="Popular / same-side page",
             note="high tax  q = 0.9", color=ERASE_C, rad=0.42),
        dict(y=1.4, q=0.2, title="Niche / opposite-side bridge",
             note="low tax  q = 0.2", color=KEEP_C, rad=-0.42),
    ]
    bxl, bxr = 4.1, 6.4          # page box left / right
    rec_l, rec_r = 8.9, 10.7     # recommendation box left / right

    # Recommendation zone.
    ax.add_patch(FancyBboxPatch((rec_l, 0.7), rec_r - rec_l, 4.6,
                                boxstyle="round,pad=0.05", fc="#eef5ee",
                                ec=KEEP_C, lw=1.5, zorder=1))
    ax.text((rec_l + rec_r) / 2, 4.95, "Recommend-\nation list", ha="center",
            va="center", fontsize=10, color=KEEP_C, weight="bold")

    for it in items:
        iy = it["y"]
        ax.add_patch(FancyBboxPatch((bxl, iy - 0.45), bxr - bxl, 0.9,
                                    boxstyle="round,pad=0.03", fc="white",
                                    ec=it["color"], lw=2, zorder=3))
        ax.text((bxl + bxr) / 2, iy + 0.13, it["title"], ha="center",
                va="center", fontsize=9.5, color=INK)
        ax.text((bxl + bxr) / 2, iy - 0.22, it["note"], ha="center",
                va="center", fontsize=8.5, color=it["color"], style="italic")

        # forward walk (solid black): S -> page upper-left corner
        ax.add_patch(FancyArrowPatch((sx + 0.45, sy + (0.3 if iy > sy else -0.3)),
                                     (bxl - 0.05, iy + 0.2),
                                     arrowstyle="-|>", mutation_scale=15,
                                     color=INK, lw=1.7, zorder=2))

        # kept (green): page -> rec list, thickness ~ kept fraction (1-q)
        keep = 1.0 - it["q"]
        ax.add_patch(FancyArrowPatch((bxr + 0.05, iy), (rec_l - 0.05, iy),
                                     arrowstyle="-|>", mutation_scale=15,
                                     color=KEEP_C, lw=1.0 + 5 * keep, zorder=2))
        ax.text((bxr + rec_l) / 2, iy + 0.3, "(1−q)·p kept", ha="center",
                fontsize=8.2, color=KEEP_C)

        # erased (red dashed): page lower-left -> S, bowed outward,
        # thickness ~ erased fraction q
        ax.add_patch(FancyArrowPatch((bxl - 0.05, iy - 0.2),
                                     (sx + 0.05, sy + (0.45 if iy > sy else -0.45)),
                                     arrowstyle="-|>", mutation_scale=14,
                                     color=ERASE_C, lw=1.0 + 5 * it["q"],
                                     ls=(0, (6, 4)),
                                     connectionstyle=f"arc3,rad={it['rad']}",
                                     zorder=2))

    ax.text(2.95, 3.0, "walk\n(3 hops)\nprob p", ha="center", va="center",
            fontsize=8.5, color=INK)
    ax.text(3.2, 0.15, "erased mass  q·p  returns to S and re-walks",
            ha="center", fontsize=9, color=ERASE_C, style="italic")
    ax.text(5.3, -0.5,
            "score(item) = p(1−q) / (1 − Σ pq)        "
            "high tax → suppressed,    low tax → recommended",
            ha="center", fontsize=10.5, color=INK)
    ax.set_title("Random Walk with Erasure: a “tax” that steers recommendations",
                 fontsize=13.5, weight="bold", color=INK)

    ax.set_xlim(0, 11)
    ax.set_ylim(-0.8, 6.0)
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
