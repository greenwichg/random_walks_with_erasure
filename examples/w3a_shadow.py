"""W3A shadow / migration diagnostic — compare the OLD substring political mask against the NEW
`classify_topic` delegation over a real catalog, and report the delta. READ-ONLY: it mutates
nothing (no store, no article, no recommender); it only classifies and counts.

Because the political flag is computed at scoring time and is **not** persisted
(`store.py` has no political column), there is no stored data to migrate — a re-ingest / rebuild
simply recomputes the sharper mask. This tool is the *shadow* the design doc asks for: it shows
what would change, before anything does.

    python examples/w3a_shadow.py                       # uses data/qbias_raw.csv if present
    python examples/w3a_shadow.py --csv data/qbias_raw.csv --limit 45000
"""

from __future__ import annotations

import argparse
import csv
import pathlib
import sys
from urllib.parse import urlsplit

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import ingest  # noqa: E402  (NEW mask: ingest.looks_political now delegates to classify_topic)

# ---- the OLD substring mask, reproduced verbatim for the before/after ---------------------- #
_OLD_HINTS = ("politic", "election", "/opinion")
_OLD_CAT_HINTS = ("politic", "election", "opinion")


def old_mask(url: str = "", category: str = "") -> bool:
    path = urlsplit(url).path.lower() if url else ""
    cat = (category or "").lower()
    return any(h in path for h in _OLD_HINTS) or any(h in cat for h in _OLD_CAT_HINTS)


def _rows_from_qbias(csv_path: str, limit: int):
    """(category, title, lean) per Qbias row with a finite lean. Feeds BOTH masks the SAME
    inputs the production path feeds (category = raw tags string, title = headline)."""
    from validate_qbias import _pick_col, label_to_pos, _HEADLINE_COLS, _BIAS_COLS
    import numpy as np
    out = []
    with open(csv_path, newline="", encoding="utf-8", errors="replace") as f:
        rd = csv.DictReader(f)
        hc = _pick_col(rd.fieldnames, _HEADLINE_COLS)
        bc = _pick_col(rd.fieldnames, _BIAS_COLS)
        tc = _pick_col(rd.fieldnames, ("tags", "topic", "topics", "tag"))
        for i, row in enumerate(rd):
            if limit and i >= limit:
                break
            lean = label_to_pos(row.get(bc, ""))
            if not np.isfinite(lean):
                continue
            out.append((str(row.get(tc) or ""), (row.get(hc) or "").strip(), float(lean)))
    return out


def _synthetic_rows():
    """Fallback corpus when no Qbias CSV is present: the documented adversarial cases so the
    shadow still demonstrates the FP/FN deltas deterministically."""
    return [  # (category, title, lean)
        ("Sports", "Team selection announced for the final", 0.2),
        ("Science", "Natural selection in the Galapagos", -0.1),
        ("Business", "A guide to stock selection", 0.5),
        ("Entertainment", "The selection of Oscar nominees", -0.3),
        ("opinion", "Why the Lakers should trade their star", 0.1),
        ("congress", "Congress passes the spending bill", -0.6),
        ("white house", "White House responds to the ruling", 1.2),
        ("U.S. news", "Senate votes on immigration reform", -0.8),
        ("opinion", "Congress must act on the border", 1.5),
        ("Politics", "Election results certified", -1.0),
    ]


def run(csv_path: "str | None", limit: int) -> str:
    if csv_path and pathlib.Path(csv_path).exists():
        rows, corpus = _rows_from_qbias(csv_path, limit), csv_path
    else:
        rows, corpus = _synthetic_rows(), "(built-in adversarial cases — no Qbias CSV found)"

    old = [old_mask(category=c) for c, _, _ in rows]
    new = [ingest.looks_political(category=c, title=t) for c, t, _ in rows]

    old_n, new_n = sum(old), sum(new)
    added = [(c, t) for (c, t, _), o, n in zip(rows, old, new) if n and not o]      # FN fixed
    removed = [(c, t) for (c, t, _), o, n in zip(rows, old, new) if o and not n]    # FP fixed

    def _bridge_pool(mask):  # political items available as opposite-side bridges, per reader side
        left = sum(1 for (_, _, ln), m in zip(rows, mask) if m and ln < 0)   # bridges for right readers
        right = sum(1 for (_, _, ln), m in zip(rows, mask) if m and ln > 0)  # bridges for left readers
        return left, right

    old_l, old_r = _bridge_pool(old)
    new_l, new_r = _bridge_pool(new)

    def _ex(pairs, k=6):
        return "; ".join(f"[{c}] {t[:48]}" for c, t in sorted(set(pairs))[:k]) or "(none)"

    L = [
        "W3A political-mask shadow — OLD substring test  vs  NEW classify_topic delegation",
        "=" * 82,
        f"corpus: {corpus}",
        f"items scored: {len(rows):,}",
        "",
        f"POLITICAL ARTICLES:   OLD {old_n:,}  ->  NEW {new_n:,}   (delta {new_n - old_n:+,})",
        f"  false positives removed (old political -> now not): {len(removed):,}",
        f"    e.g. {_ex(removed)}",
        f"  false negatives added   (old not -> now political): {len(added):,}",
        f"    e.g. {_ex(added)}",
        "",
        "BRIDGE CANDIDATES (opposite-side political items a reader could be bridged to):",
        f"  for RIGHT-leaning readers (left-lean political items):  OLD {old_l:,}  ->  NEW {new_l:,}   ({new_l - old_l:+,})",
        f"  for LEFT-leaning readers  (right-lean political items):  OLD {old_r:,}  ->  NEW {new_r:,}   ({new_r - old_r:+,})",
        "",
        "READ: the mask sharpens — spurious 'selection'/opinion items leave the political set,",
        "genuine institutional-politics items (congress / white house / senate) enter it. The",
        "bridge pool shifts with it. Political flag is not persisted, so a rebuild recomputes it;",
        "no stored-data migration is required.",
    ]
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", default="data/qbias_raw.csv", help="catalog CSV (Qbias); falls back to built-ins")
    ap.add_argument("--limit", type=int, default=45000, help="cap rows scanned")
    args = ap.parse_args()
    print(run(args.csv, args.limit))


if __name__ == "__main__":
    main()
