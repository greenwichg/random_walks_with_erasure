"""Ingest the **Reddit Politosphere** into an RWE-ready ``.npz`` (user×subreddit
endorsement matrix + ideal-point ideology).

Politosphere (Hofmann et al., ICWSM 2022; https://zenodo.org/records/5851729) is a
pseudonymized resource of 605 political subreddits (2008--2019), derived from
Pushshift. Each comment carries an ``author`` (pseudonym) and a ``subreddit``, so
the comments give a bipartite **user×subreddit endorsement graph** -- exactly the
input the ideal-point model (:class:`rwe.IdeologyModel`) needs to place users *and*
subreddits on a shared latent left--right axis **from behaviour alone** (no text
classifier, no outlet labels, no Twitter API). This is the *elite-endorsement*
setup of the original RWE paper, on a public, downloadable dataset -- and unlike the
MIND text-lean axis it has a **clean ideological ground truth**: known subreddit
leans (``examples/data/subreddit_lean.csv``).

The comment files are ``comments_YYYY-MM.bz2`` (bz2-compressed JSON lines, fields as
in Pushshift). Download a slice from the Politosphere release, then::

    python examples/ingest_politosphere.py --comments-dir politosphere/ --ideology \\
        --min-user-clicks 5 --min-item-clicks 20 --sample-users 15000 \\
        --out politosphere.npz

    python examples/eval_mind.py --npz politosphere.npz --no-bprmf      # RQ2 + RQ3
    python examples/plot_axis.py --npz politosphere.npz --out axis.png  # users+items

``--lean-csv`` (default: the bundled ``subreddit_lean.csv``) seeds known subreddit
leans so ``--ideology`` *orients* the learned axis to them and reports ``lean_corr``
(a validation number); items output is stored in the :class:`~rwe.mind.MINDData`
container (``item_ids`` = subreddits), so the whole MIND eval pipeline just works.

**Scale note.** The de-dup holds one set of subreddit-ids per author, so memory grows
with the number of users; subset to a few years / months (or pre-filter subreddits
with the dataset's own ``load_comments.py``) for a first run. ``--min-*-clicks`` and
``--sample-users`` keep the final matrix (and the dense ideal-point fit) tractable.
"""

import argparse
import bz2
import glob
import json
from pathlib import Path

import numpy as np

from rwe.data import from_interactions
from rwe.mind import MINDData, _filter_min

_SKIP_AUTHORS = frozenset({"[deleted]", "[removed]", "", "AutoModerator"})
_DEFAULT_LEAN = Path(__file__).resolve().parent / "data" / "subreddit_lean.csv"


def _comment_files(comments_dir, pattern):
    files = sorted(glob.glob(str(Path(comments_dir) / pattern)))
    if not files:
        raise FileNotFoundError(
            f"no files matched {Path(comments_dir) / pattern}; point --comments-dir "
            "at a folder of comments_YYYY-MM.bz2 (or .json) files from Politosphere.")
    return files


def _read_comments(files, limit=None):
    """Yield ``(author, subreddit)`` from bz2/plain JSON-lines comment files."""
    n = 0
    for path in files:
        opener = bz2.open if str(path).endswith(".bz2") else open
        with opener(path, "rt", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                a, s = obj.get("author"), obj.get("subreddit")
                if not a or not s or a in _SKIP_AUTHORS:
                    continue
                yield a, s
                n += 1
                if limit and n >= limit:
                    return


def _build_inputs(comment_iter):
    """Stream comments -> de-dup to one row per (author, subreddit) -> parallel
    ``(users, items)`` name arrays for :func:`from_interactions`."""
    sid, s_names, user_subs = {}, [], {}
    for a, s in comment_iter:
        i = sid.get(s)
        if i is None:
            i = sid[s] = len(s_names)
            s_names.append(s)
        user_subs.setdefault(a, set()).add(i)
    users, items = [], []
    for a, subs in user_subs.items():
        for i in subs:
            users.append(a)
            items.append(s_names[i])
    return np.array(users, dtype=object), np.array(items, dtype=object)


def load_subreddit_lean(path) -> dict:
    """``subreddit,lean`` table -> ``{subreddit_lower: float}`` (skips ``#``/header)."""
    table = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or line.lower().startswith("subreddit"):
                continue
            parts = line.split("\t") if "\t" in line else line.split(",")
            if len(parts) >= 2:
                try:
                    table[parts[0].strip().lower()] = float(parts[1])
                except ValueError:
                    pass
    return table


def build_mind(users, items, lean=None) -> MINDData:
    """Parallel ``(users, items)`` -> a :class:`MINDData` (items = subreddits)."""
    ds = from_interactions(users, items)
    ds.matrix.data[:] = 1.0                       # binary endorsement
    names = np.asarray(ds.item_ids)
    n = len(names)
    if lean:
        pos = np.array([lean.get(str(s).lower(), np.nan) for s in names], dtype=float)
    else:
        pos = np.full(n, np.nan)
    return MINDData(
        dataset=ds,
        categories=np.array(["political"] * n, dtype=object),
        subcategories=names.astype(object),
        titles=np.array([f"r/{s}" for s in names], dtype=object),
        outlets=np.array([""] * n, dtype=object),
        political=np.ones(n, dtype=bool),
        item_positions=pos,
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--comments-dir", required=True,
                    help="directory with Politosphere comments_YYYY-MM.bz2 files")
    ap.add_argument("--pattern", default="comments_*.bz2",
                    help="glob for the comment files (default comments_*.bz2)")
    ap.add_argument("--out", default="politosphere.npz")
    ap.add_argument("--lean-csv", default=str(_DEFAULT_LEAN),
                    help="subreddit,lean table to seed/validate the axis "
                         "(default: bundled subreddit_lean.csv; '' to skip)")
    ap.add_argument("--min-user-clicks", type=int, default=5,
                    help="min distinct subreddits per user")
    ap.add_argument("--min-item-clicks", type=int, default=20,
                    help="min distinct users per subreddit")
    ap.add_argument("--sample-users", type=int, default=None,
                    help="randomly keep this many users (caps the dense --ideology fit)")
    ap.add_argument("--ideology", action="store_true",
                    help="estimate user+subreddit positions via IdeologyModel "
                         "(ideal-point; recommended -- the whole reason for this dataset)")
    ap.add_argument("--ideology-iters", type=int, default=300)
    ap.add_argument("--max-cells", type=float, default=5e7)
    ap.add_argument("--limit", type=int, default=None, help="read only N comments (debug)")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    files = _comment_files(args.comments_dir, args.pattern)
    print(f"reading {len(files)} comment file(s) from {args.comments_dir} ...")
    users, items = _build_inputs(_read_comments(files, limit=args.limit))
    print(f"  {users.size} unique (user, subreddit) endorsements")
    users, items = _filter_min(users, items, args.min_user_clicks, args.min_item_clicks)
    if users.size == 0:
        raise ValueError("no endorsements left after filtering; lower the thresholds.")

    lean = load_subreddit_lean(args.lean_csv) if args.lean_csv else None
    d = build_mind(users, items, lean=lean)
    if args.sample_users:
        d = d.sample_users(args.sample_users, seed=args.seed)
    print("Ingested Politosphere:")
    print(json.dumps(d.summary(), indent=2))

    if args.ideology:
        fit = d.fit_ideology(n_iter=args.ideology_iters, seed=args.seed,
                             max_cells=args.max_cells)
        d = d.with_ideology(fit)
        print(f"\nFit ideology (ideal-point): users={d.n_users}, items={d.n_items}, "
              f"lean_corr={fit.lean_corr}  "
              "(near ±1 = the learned axis matches the labeled subreddit leans)")
    elif lean and d.summary()["items_with_lean"] == 0:
        print("\n[note] No labeled subreddits matched item_ids; check name casing in "
              "--lean-csv, or pass --ideology to position from behaviour.")

    d.save(args.out)
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
