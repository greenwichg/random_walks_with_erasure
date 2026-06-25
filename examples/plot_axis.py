"""Plot where users and items actually sit on the left<->right ideology scale.

Loads an ingested MIND ``.npz`` (from ``ingest_mind.py`` -- ideally the text-lean
one, ``--positions-csv lean.csv``) and draws the **populated** ideology axis:
stacked histograms of item positions (articles) and user positions (``theta``) on
one shared scale, coloured by side, so the left-leaning users/items land on the
left and the rest on the right.  It also prints the most-left and most-right
example headlines so you can sanity-check the orientation by eye.

    python examples/plot_axis.py --npz mind_text.npz --out axis.png

The split point is ``--center`` (default 0, the text-lean centre); pass
``--center median`` to split at the population median instead.
"""

import argparse
from pathlib import Path

import numpy as np

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from rwe.mind import MINDData

USER_C = "#4C72B0"   # blue  (users)
ITEM_C = "#DD8452"   # amber (items)
LEFT_C = "#4C72B0"   # blue  (left side)
RIGHT_C = "#C44E52"  # red   (right side)
INK = "#2b2b2b"
MUTE = "#8a8a8a"


def plot_axis(item_pos, user_pos, center: float = 0.0, out: str = "axis.png",
              title: str | None = None) -> dict:
    """Draw users + items on one left<->right scale; return the side counts."""
    item_pos = np.asarray(item_pos, dtype=float)
    user_pos = np.asarray(user_pos, dtype=float)
    item_pos = item_pos[np.isfinite(item_pos)]
    user_pos = user_pos[np.isfinite(user_pos)]
    if item_pos.size == 0 or user_pos.size == 0:
        raise ValueError("need at least one finite item and user position to plot")

    lo = min(item_pos.min(), user_pos.min())
    hi = max(item_pos.max(), user_pos.max())
    pad = 0.05 * (hi - lo + 1e-9)
    bins = np.linspace(lo - pad, hi + pad, 41)

    fig, (ax_i, ax_u) = plt.subplots(2, 1, figsize=(9.0, 5.4), sharex=True)
    rows = [(ax_i, item_pos, "items\n(articles)"),
            (ax_u, user_pos, "users\n(θ = mean lean of clicks)")]
    for ax, pos, ylab in rows:
        left, right = pos[pos < center], pos[pos >= center]
        ax.hist(left, bins=bins, color=LEFT_C, alpha=0.85,
                label=f"left  ({100 * left.size / pos.size:.0f}%)")
        ax.hist(right, bins=bins, color=RIGHT_C, alpha=0.85,
                label=f"right ({100 * right.size / pos.size:.0f}%)")
        ax.axvline(center, color=INK, lw=1.3, ls="--")
        ax.set_ylabel(ylab, fontsize=10, color=INK)
        ax.legend(fontsize=9, loc="upper right", frameon=False)
        for sp in ("top", "right"):
            ax.spines[sp].set_visible(False)
    ax_i.text(center, ax_i.get_ylim()[1] * 0.98, " centre", ha="left", va="top",
              fontsize=8.5, color=MUTE)
    ax_u.set_xlabel("←  left        ideological position (text lean)        right  →",
                    fontsize=11, color=INK)
    ax_i.set_title(title or "Users and items on the left↔right scale",
                   fontsize=13, weight="bold", color=INK)
    fig.tight_layout()
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return dict(n_items=int(item_pos.size), n_users=int(user_pos.size),
                item_left=float(np.mean(item_pos < center)),
                user_left=float(np.mean(user_pos < center)))


def _example_headlines(titles, positions, k: int = 5):
    """(most-left, most-right) ``(position, title)`` lists for a quick eyeball."""
    titles = list(titles)
    positions = np.asarray(positions, dtype=float)
    ok = np.flatnonzero(np.isfinite(positions) & np.array(
        [bool(t and t.strip()) for t in titles]))
    if ok.size == 0:
        return [], []
    order = ok[np.argsort(positions[ok])]
    left = [(float(positions[i]), titles[i]) for i in order[:k]]
    right = [(float(positions[i]), titles[i]) for i in order[-k:][::-1]]
    return left, right


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--npz", required=True, help="ingested MIND .npz (from ingest_mind.py)")
    ap.add_argument("--out", default="axis.png")
    ap.add_argument("--center", default="0",
                    help="split point: a number (default 0) or 'median'")
    args = ap.parse_args()

    d = MINDData.load(args.npz)
    item_pos = np.asarray(d.item_positions, dtype=float)
    user_pos = (d.user_positions if d.user_positions is not None
                else d.user_positions_from_clicks(fill=np.nan))
    user_pos = np.asarray(user_pos, dtype=float)

    if args.center == "median":
        center = float(np.nanmedian(item_pos))
    else:
        center = float(args.center)

    stats = plot_axis(item_pos, user_pos, center=center, out=args.out,
                      title="Users and items on the left↔right scale (MIND text-lean axis)")
    print(f"wrote {args.out}")
    print(f"  items: {stats['n_items']}  ({stats['item_left'] * 100:.0f}% left / "
          f"{(1 - stats['item_left']) * 100:.0f}% right of centre={center:g})")
    print(f"  users: {stats['n_users']}  ({stats['user_left'] * 100:.0f}% left / "
          f"{(1 - stats['user_left']) * 100:.0f}% right)")

    if getattr(d, "titles", None) is not None and len(d.titles):
        left, right = _example_headlines(d.titles, item_pos)
        if left:
            print("\nMost LEFT-scored articles:")
            for p, t in left:
                print(f"  {p:+.2f}  {t[:80]}")
            print("Most RIGHT-scored articles:")
            for p, t in right:
                print(f"  {p:+.2f}  {t[:80]}")


if __name__ == "__main__":
    main()
