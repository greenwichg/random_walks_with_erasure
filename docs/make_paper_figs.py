"""Generate the paper figures (docs/images/paper_*.png) from the 7-seed MIND results.

Numbers are the mean ± std reported in docs/RESULTS.md (produced by
examples/eval_mind.py --seeds 7 on MIND-small with text-lean positions). Hard-coded
here so the figures regenerate without re-running the Colab pipeline.

    python docs/make_paper_figs.py
"""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

OUT = Path(__file__).resolve().parent / "images"

USER_C = "#4C72B0"     # blue
ITEM_C = "#DD8452"     # amber
KEEP_C = "#55A868"     # green
ERASE_C = "#C44E52"    # red
INK = "#2b2b2b"
MUTE = "#8a8a8a"


def _save(fig, name):
    OUT.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT / name, dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print("wrote", OUT / name)


# --------------------------------------------------------------------------- #
# Figure 1 — the bounded-bridging sweep (headline result)
# --------------------------------------------------------------------------- #
def sweep():
    lab = ["∞", "2", "1.5", "1", "0.5"]
    x = np.arange(len(lab))
    uw_shift = [1.044, .742, .551, .404, .343]
    uw_shift_e = [.007, .006, .004, .004, .005]
    uw_recs = [.768, .475, .334, .268, .276]
    uw_recs_e = [.007, .006, .004, .005, .006]
    auc = [.753, .756, .760, .766, .769]
    hr = [.139, .151, .162, .180, .194]
    hr_e = [.004, .004, .003, .004, .003]
    P3 = dict(uw_shift=.339, uw_recs=.278, auc=.771, hr=.196)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11.0, 4.3))

    ax1.axvspan(0.7, 2.3, color=KEEP_C, alpha=0.10)         # moderated window d≈1.5–2
    ax1.text(1.5, 1.06, "moderated\nbridging", ha="center", va="top",
             fontsize=9, color="#2E6B3E")
    ax1.errorbar(x, uw_shift, yerr=uw_shift_e, marker="o", color=USER_C, lw=2.2,
                 capsize=3, label="UW-shift (bridging strength)")
    ax1.errorbar(x, uw_recs, yerr=uw_recs_e, marker="s", color=ERASE_C, lw=2.2,
                 capsize=3, label="UW-recs (distance from centre)")
    ax1.axhline(P3["uw_shift"], ls=":", color=USER_C, alpha=0.7)
    ax1.axhline(P3["uw_recs"], ls=":", color=ERASE_C, alpha=0.7)
    ax1.text(4.05, P3["uw_recs"], "P3", color=MUTE, fontsize=8, va="center")
    ax1.set_title("Bridging vs the bound", fontsize=12.5, weight="bold", color=INK)
    ax1.set_ylabel("UW measure", fontsize=11, color=INK)
    ax1.legend(fontsize=9, loc="upper right")

    ax2.errorbar(x, auc, yerr=[.002] * 5, marker="o", color=INK, lw=2.2,
                 capsize=3, label="AUC")
    ax2.errorbar(x, hr, yerr=hr_e, marker="s", color=ITEM_C, lw=2.2,
                 capsize=3, label="HR@10")
    ax2.axhline(P3["auc"], ls=":", color=INK, alpha=0.7)
    ax2.axhline(P3["hr"], ls=":", color=ITEM_C, alpha=0.7)
    ax2.text(4.05, P3["auc"], "P3", color=MUTE, fontsize=8, va="center")
    ax2.set_title("Accuracy vs the bound", fontsize=12.5, weight="bold", color=INK)
    ax2.set_ylabel("accuracy", fontsize=11, color=INK)
    ax2.legend(fontsize=9, loc="center right")

    for ax in (ax1, ax2):
        ax.set_xticks(x)
        ax.set_xticklabels(lab)
        ax.set_xlabel("“not too far” bound  d   (loose → tight)", fontsize=11, color=INK)
        for sp in ("top", "right"):
            ax.spines[sp].set_visible(False)
    fig.suptitle("Bounded bridging: tightening d pulls recommendations toward the "
                 "centre while accuracy rises (MIND, 7 seeds, mean ± std)",
                 fontsize=12, weight="bold", color=INK)
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    _save(fig, "paper_sweep.png")


# --------------------------------------------------------------------------- #
# Figure 2 — accuracy vs long-tail (RQ1) and vs bridging (RQ2)
# --------------------------------------------------------------------------- #
def tradeoff():
    methods = ["ItemKNN", "P3", "RP3-β", "RWE-D", "RWE-B"]
    colors = [MUTE, INK, ITEM_C, KEEP_C, USER_C]
    auc = [.719, .771, .744, .743, .753]
    auc_e = [.002, .001, .002, .002, .002]
    gini = [.406, .151, .683, .708, .168]
    gini_e = [.004, .001, .003, .003, .001]
    uwsh = [.379, .339, .401, .405, 1.044]
    uwsh_e = [.010, .005, .005, .005, .007]

    fig, (axA, axB) = plt.subplots(1, 2, figsize=(11.0, 4.3))

    def panel(ax, xv, xe, xlabel, title, dxy):
        for m, a, ae, v, ve, c, (dx, dy) in zip(methods, auc, auc_e, xv, xe, colors, dxy):
            ax.errorbar(v, a, xerr=ve, yerr=ae, marker="o", ms=9, color=c,
                        capsize=2, lw=1.4, zorder=3)
            ax.annotate(m, (v, a), textcoords="offset points", xytext=(dx, dy),
                        fontsize=9.5, color=c, weight="bold")
        ax.set_xlabel(xlabel, fontsize=11, color=INK)
        ax.set_ylabel("accuracy (AUC)", fontsize=11, color=INK)
        ax.set_title(title, fontsize=12.5, weight="bold", color=INK)
        for sp in ("top", "right"):
            ax.spines[sp].set_visible(False)

    panel(axA, gini, gini_e, "long-tail diversity (Gini@20)  →",
          "RQ1: long-tail (RWE-D wins)",
          [(8, -4), (-4, 8), (8, 4), (6, -12), (8, 2)])
    panel(axB, uwsh, uwsh_e, "ideological bridging (UW-shift)  →",
          "RQ2: bridging (RWE-B wins)",
          [(8, -4), (8, 4), (8, 4), (8, -10), (-10, -14)])
    fig.tight_layout()
    _save(fig, "paper_tradeoff.png")


if __name__ == "__main__":
    sweep()
    tradeoff()
