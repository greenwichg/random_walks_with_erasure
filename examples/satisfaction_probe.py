"""Feasibility probe: is a *measured* opposite-perspective engagement signal
recoverable from real Reddit (Politosphere) comments, on the **validated** axis?

The satisfaction-driven adaptive recommender (:mod:`rwe.satisfaction`) is currently
driven by a *simulated* dwell score. This probe asks whether the real comment stream
carries a usable substitute. For each user we find their **cross-cutting** comments
(in subreddits on the *opposite* side of the validated left--right axis from their own
side) and measure the engagement quality the simulation only guesses at:

* **reception** -- mean comment ``score`` (upvotes). Upvoted cross-cutting comments
  are a *welcomed bridge*; heavily downvoted ones are a *flame war* -- the key
  confound this probe exists to rule out.
* **depth** -- fraction of cross-cutting comments that are *replies* (``parent_id``
  begins ``t1_``), i.e. a back-and-forth rather than a drive-by.
* **return** -- number of distinct months the user keeps coming back to the other
  side (``created_utc``).

It prints a population diagnostic comparing cross-cutting vs same-side engagement and
reports which signals are even available (Politosphere's pseudonymization may have
dropped some Pushshift fields). It does **not** wire anything into the recommender --
that is the follow-up *iff* the probe looks sensible.

The "side" of each subreddit comes from the validated ideal-point axis stored in the
ingested ``.npz`` (``examples/ingest_politosphere.py --ideology``); only subreddits
present there (with a position) are considered, so the whole probe rides the same
validated axis as RQ3.

Usage::

    python examples/satisfaction_probe.py --comments-dir politosphere/ \\
        --npz politosphere_mi200.npz --out satisfaction_probe.csv
"""

from __future__ import annotations

import argparse
import bz2
import datetime as _dt
import json
from pathlib import Path

import numpy as np

from rwe.mind import MINDData

_SKIP_AUTHORS = frozenset({"[deleted]", "[removed]", "", "AutoModerator"})


def _comment_files(comments_dir, pattern):
    import glob
    files = sorted(glob.glob(str(Path(comments_dir) / pattern)))
    if not files:
        raise FileNotFoundError(
            f"no files matched {Path(comments_dir) / pattern}; point --comments-dir at "
            "a folder of comments_YYYY-MM.bz2 files from Politosphere.")
    return files


def _month_code(created_utc) -> int:
    """``created_utc`` (unix seconds, int/str) -> ``year*12 + month`` or ``-1``."""
    try:
        d = _dt.datetime.fromtimestamp(int(created_utc), _dt.timezone.utc)
        return d.year * 12 + (d.month - 1)
    except (TypeError, ValueError, OSError, OverflowError):
        return -1


def read_engagement(files, sub_pos: dict, limit=None):
    """Stream comments in *positioned* subreddits, keeping the engagement fields.

    ``sub_pos`` maps subreddit name -> validated-axis position. Returns parallel
    arrays ``(author, pos, score, month, is_reply)`` plus a ``fields`` dict marking
    which optional Pushshift fields were actually present (so the caller can report
    what is measurable)."""
    authors, pos, score, month, is_reply = [], [], [], [], []
    seen = {"score": 0, "created_utc": 0, "parent_id": 0}
    n = 0
    for path in files:
        opener = bz2.open if str(path).endswith(".bz2") else open
        with opener(path, "rt", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except json.JSONDecodeError:
                    continue
                a, s = o.get("author"), o.get("subreddit")
                if not a or not s or a in _SKIP_AUTHORS or s not in sub_pos:
                    continue
                sc, cu, pid = o.get("score"), o.get("created_utc"), o.get("parent_id")
                seen["score"] += sc is not None
                seen["created_utc"] += cu is not None
                seen["parent_id"] += pid is not None
                authors.append(a)
                pos.append(sub_pos[s])
                score.append(np.nan if sc is None else float(sc))
                month.append(_month_code(cu))
                is_reply.append(bool(pid) and str(pid).startswith("t1_"))
                n += 1
                if limit and n >= limit:
                    break
        if limit and n >= limit:
            break
    fields = {k: (v > 0) for k, v in seen.items()}
    return (np.asarray(authors, dtype=object), np.asarray(pos, dtype=float),
            np.asarray(score, dtype=float), np.asarray(month, dtype=np.int64),
            np.asarray(is_reply, dtype=bool), fields)


def _distinct_months(codes, months, n_users) -> np.ndarray:
    """Distinct (user, month) count per user, vectorised (ignores month ``-1``)."""
    ok = months >= 0
    if not ok.any():
        return np.zeros(n_users, dtype=float)
    key = codes[ok].astype(np.int64) * 100_000 + months[ok]
    u = np.unique(key) // 100_000
    return np.bincount(u, minlength=n_users).astype(float)


def probe(authors, pos, score, month, is_reply, *, sub_tau=0.5, user_tau=0.3):
    """Classify each comment as same-/cross-side of the user's own side and aggregate
    per-user engagement. Returns a dict of per-user arrays + population summary."""
    users, codes = np.unique(authors, return_inverse=True)
    n_users = users.size
    cnt = np.bincount(codes, minlength=n_users).astype(float)
    user_mean = np.bincount(codes, weights=pos, minlength=n_users) / np.maximum(cnt, 1)
    user_side = np.where(np.abs(user_mean) >= user_tau, np.sign(user_mean), 0)

    comm_side = np.where(np.abs(pos) >= sub_tau, np.sign(pos), 0)
    us = user_side[codes]
    cross = (comm_side != 0) & (us != 0) & (comm_side == -us)
    same = (comm_side != 0) & (us != 0) & (comm_side == us)

    def agg(mask):
        c = np.bincount(codes[mask], minlength=n_users).astype(float)
        sc = score[mask]
        fin = np.isfinite(sc)
        ssum = np.bincount(codes[mask][fin], weights=sc[fin], minlength=n_users)
        sfin = np.bincount(codes[mask][fin], minlength=n_users).astype(float)
        spos = np.bincount(codes[mask][fin], weights=(sc[fin] > 0).astype(float),
                           minlength=n_users)
        rep = np.bincount(codes[mask], weights=is_reply[mask].astype(float),
                          minlength=n_users)
        mon = _distinct_months(codes[mask], month[mask], n_users)
        return dict(n=c, mean_score=np.divide(ssum, sfin, out=np.full(n_users, np.nan),
                                              where=sfin > 0),
                    upvoted_frac=np.divide(spos, sfin, out=np.full(n_users, np.nan),
                                           where=sfin > 0),
                    reply_frac=np.divide(rep, c, out=np.full(n_users, np.nan),
                                         where=c > 0),
                    months=mon)

    X, S = agg(cross), agg(same)
    has_side = user_side != 0
    has_cross = X["n"] > 0
    summary = dict(
        n_users=int(n_users), n_sided=int(has_side.sum()),
        n_with_cross=int(has_cross.sum()),
        cross_share_of_sided=float(has_cross.sum() / max(has_side.sum(), 1)),
        # the flame-war check: of *all* cross-cutting comments, what fraction upvoted?
        cross_comments=int(cross.sum()), same_comments=int(same.sum()),
        cross_upvoted_frac=_frac_pos(score[cross]),
        same_upvoted_frac=_frac_pos(score[same]),
        cross_median_score=_median(score[cross]),
        same_median_score=_median(score[same]),
        cross_reply_frac=float(np.nanmean(is_reply[cross])) if cross.any() else float("nan"),
        same_reply_frac=float(np.nanmean(is_reply[same])) if same.any() else float("nan"),
        cross_return_median=_median(X["months"][has_cross]),
        same_return_median=_median(S["months"][S["n"] > 0]),
    )
    return dict(users=users, user_side=user_side, cross=X, same=S, summary=summary)


def _frac_pos(a):
    a = a[np.isfinite(a)]
    return float((a > 0).mean()) if a.size else float("nan")


def _median(a):
    a = np.asarray(a, dtype=float)
    a = a[np.isfinite(a)]
    return float(np.median(a)) if a.size else float("nan")


def _verdict(s: dict) -> str:
    """A blunt heuristic read of whether the measured signal looks sensible."""
    cf, sf = s["cross_upvoted_frac"], s["same_upvoted_frac"]
    if not np.isfinite(cf):
        return ("NO `score` field -> reception not measurable. Return/depth may still "
                "be usable; otherwise the signal stays simulated.")
    if cf >= 0.5:
        return ("LOOKS SENSIBLE: most cross-cutting comments are upvoted -> welcomed "
                "bridges dominate, not flame wars. Worth promoting to a real metric.")
    if cf >= 0.33:
        return ("MIXED: a sizeable minority of cross-cutting comments are downvoted. "
                "Usable, but gate the satisfaction signal on score>0 (constructive only).")
    return ("CONFOUNDED: cross-cutting comments are mostly downvoted -> adversarial "
            "participation dominates. Reported as *why* real satisfaction is hard here.")


def format_summary(s: dict, fields: dict) -> str:
    L = ["SATISFACTION PROBE — measured opposite-side engagement (validated axis)",
         "=" * 68,
         f"fields present:  score={fields['score']}  created_utc={fields['created_utc']}"
         f"  parent_id={fields['parent_id']}",
         f"users with a clear side: {s['n_sided']}/{s['n_users']}   "
         f"of those, {s['cross_share_of_sided']*100:.0f}% have >=1 cross-cutting comment",
         f"cross-cutting comments: {s['cross_comments']:,}    same-side: {s['same_comments']:,}",
         "",
         "                          cross-cutting     same-side",
         f"  median comment score   {s['cross_median_score']:>10.2f}    {s['same_median_score']:>10.2f}",
         f"  upvoted (score>0)       {s['cross_upvoted_frac']*100:>9.0f}%    {s['same_upvoted_frac']*100:>9.0f}%",
         f"  reply (in-thread) frac  {s['cross_reply_frac']*100:>9.0f}%    {s['same_reply_frac']*100:>9.0f}%",
         f"  return (distinct months){s['cross_return_median']:>10.1f}    {s['same_return_median']:>10.1f}",
         "",
         "VERDICT: " + _verdict(s)]
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--comments-dir", required=True,
                    help="folder of Politosphere comments_YYYY-MM.bz2 files")
    ap.add_argument("--pattern", default="comments_*.bz2")
    ap.add_argument("--npz", required=True,
                    help="ingested .npz with the validated axis (item_positions)")
    ap.add_argument("--sub-tau", type=float, default=0.5,
                    help="|position| below this = centrist subreddit, skipped")
    ap.add_argument("--user-tau", type=float, default=0.3,
                    help="|mean position| below this = user has no clear side, skipped")
    ap.add_argument("--limit", type=int, default=None, help="cap comments (debug)")
    ap.add_argument("--out", default=None, help="optional per-user CSV")
    args = ap.parse_args()

    d = MINDData.load(args.npz)
    names = np.asarray(d.dataset.item_ids)
    pos = np.asarray(d.item_positions, dtype=float)
    sub_pos = {str(s): float(p) for s, p in zip(names, pos) if np.isfinite(p)}
    print(f"validated axis: {len(sub_pos)} positioned subreddits "
          f"(|pos|>= {args.sub_tau} treated as sided)")

    files = _comment_files(args.comments_dir, args.pattern)
    print(f"reading {len(files)} comment file(s) ...")
    authors, p, score, month, is_reply, fields = read_engagement(
        files, sub_pos, limit=args.limit)
    print(f"  {authors.size:,} comments in positioned subreddits")
    if authors.size == 0:
        print("no comments landed in positioned subreddits — wrong --npz/--comments-dir?")
        return

    res = probe(authors, p, score, month, is_reply,
                sub_tau=args.sub_tau, user_tau=args.user_tau)
    print("\n" + format_summary(res["summary"], fields))

    if args.out:
        import csv
        X, S = res["cross"], res["same"]
        with open(args.out, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["user", "side", "cross_n", "cross_mean_score",
                        "cross_upvoted_frac", "cross_reply_frac", "cross_months",
                        "same_n", "same_mean_score"])
            for i, u in enumerate(res["users"]):
                if res["user_side"][i] == 0:
                    continue
                w.writerow([u, int(res["user_side"][i]), int(X["n"][i]),
                            round(float(X["mean_score"][i]), 3) if np.isfinite(X["mean_score"][i]) else "",
                            round(float(X["upvoted_frac"][i]), 3) if np.isfinite(X["upvoted_frac"][i]) else "",
                            round(float(X["reply_frac"][i]), 3) if np.isfinite(X["reply_frac"][i]) else "",
                            int(X["months"][i]), int(S["n"][i]),
                            round(float(S["mean_score"][i]), 3) if np.isfinite(S["mean_score"][i]) else ""])
        print(f"\nwrote per-user CSV -> {args.out}")


if __name__ == "__main__":
    main()
