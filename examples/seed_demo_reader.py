#!/usr/bin/env python3
"""Seed the persisted demo account with reads from the local catalog — no engine required.

Creates the ``dev`` / ``demo@infodiet.local`` account (the one the app's "Continue as demo
reader" button signs into, and that ``rec_sandbox.py --reader demo`` resolves to) and records a
handful of reads drawn from the ingested catalog, so the measured / signed-in views have data.
This is the offline, store-level equivalent of the deploy notebook's cell-5 pre-load — useful
when you provisioned the demo in one environment (e.g. Colab) but want it in another (e.g. a
local checkout) without standing up the HTTP engine.

Read composition mirrors the notebook: a left-leaning diet (4 left + 2 centre + 2 right) so
cross-cutting bridges point right, and each read lands on a real catalog article's canonical URL
so it connects to the recommendation graph (bridging-ready).

By default it is narrow and idempotent: ``upsert_user_by_identity`` and ``add_read`` both dedup,
so re-running adds nothing new (the demo's reading history stays stable). It only ever writes the
demo account and its reads — never the catalog, another user, or any project code.

To make the reading history *change*, the reads must be cleared and re-picked — that is what
``--reset`` and ``--random`` are for.

Usage
-----
    python examples/seed_demo_reader.py                      # default DB, deterministic 8 reads
    python examples/seed_demo_reader.py sqlite:///data/ih_beta.db   # an explicit store URL
    python examples/seed_demo_reader.py --reset --random     # a DIFFERENT history every run
    python examples/seed_demo_reader.py --reset --seed 7      # a different but REPRODUCIBLE history
    python examples/seed_demo_reader.py --reset --random --count 12   # 12 reads instead of 8

Flags
-----
    --reset        clear the demo account's existing reads before seeding (so the history changes
                   instead of being a no-op). Only the demo user's reads are removed.
    --random       pick a different left-leaning set each run (shuffles the catalog first).
    --seed N       like --random but reproducible: the same N always picks the same set.
    --count N       how many reads to seed (default 8).

With no DB argument it targets ``store.default_db_url()`` (the same absolute file the engine writes
to), so it is immune to the working-directory ambiguity of a relative ``sqlite:///data/...`` URL.
"""
import argparse
import pathlib
import random
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # so `import store` works anywhere
import sqlalchemy as _sa                                           # noqa: E402
import store as store_mod                                          # noqa: E402

PROVIDER = "dev"
ACCOUNT = "demo@infodiet.local"


def _iso(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def _clear_reads(store, uid: int) -> int:
    """Delete every read for ``uid`` (the demo user only) and return how many were removed.
    Read-management, not a catalog/user change — targeted by user_id, nothing else is touched."""
    with store.session() as s:
        n = s.execute(_sa.text("SELECT COUNT(*) FROM reads WHERE user_id=:u"), {"u": uid}).scalar()
        s.execute(_sa.text("DELETE FROM reads WHERE user_id=:u"), {"u": uid})
    return int(n or 0)


def seed(store, target_reads: int = 8, rng: "random.Random | None" = None,
         reset: bool = False) -> dict:
    """Create the demo account (if absent) and top it up toward ``target_reads`` catalog reads.

    Returns ``{userId, catalog, picked, added, cleared, totalReads}``.

    - Default (``rng=None, reset=False``): deterministic and idempotent — the same left-leaning
      diet (4 left / 2 centre / 2 right) every run, and a second call adds ``0``.
    - ``rng`` (from ``--random`` / ``--seed``): shuffles the catalog first, so a *different* set
      fills each lean bucket — the reading history changes per run.
    - ``reset=True`` (``--reset``): clears the demo's existing reads first, so the new picks
      replace the old history instead of deduping against it.

    Reads are keyed by each article's canonical URL, so they attach to the recommendation graph
    exactly as a real read would."""
    uid = store.upsert_user_by_identity(PROVIDER, ACCOUNT, email=ACCOUNT,
                                        display_name="Demo Reader").id
    cleared = _clear_reads(store, uid) if reset else 0
    catalog = store.list_feed_articles(limit=200)
    if rng is not None:
        catalog = list(catalog)
        rng.shuffle(catalog)                 # vary which article fills each lean bucket
    seen: set = set()
    picks: list = []

    def take(pred, n):
        got = 0
        for a in catalog:
            if got >= n:
                break
            sc = a.get("scored") or {}
            cu = a.get("canonicalUrl")
            lean = sc.get("lean")
            if cu and cu not in seen and lean is not None and pred(float(lean)):
                seen.add(cu)
                picks.append(a)
                got += 1

    left = max(target_reads // 2, 1)         # keep the left-leaning composition at any --count
    take(lambda l: l < -0.5, left)           # ~half left
    take(lambda l: -0.5 <= l <= 0.5, max((target_reads - left) // 2, 1))   # ~quarter centre
    take(lambda l: l > 0.5, max((target_reads - left) // 2, 1))            # ~quarter right
    take(lambda l: True, target_reads - len(picks))   # top up if a bucket ran dry

    added = 0
    for i, a in enumerate(picks):
        sc = a["scored"]
        canonical = sc.get("article_id") or a["canonicalUrl"]
        if store.add_read(uid, canonical, sc, _iso(1 + i), read_source="seed"):
            added += 1
    return {"userId": uid, "catalog": len(catalog), "picked": len(picks),
            "added": added, "cleared": cleared, "totalReads": store.count_reads(uid)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="seed_demo_reader",
                                 description="Seed / re-seed the persisted demo reader's reads.")
    ap.add_argument("db", nargs="?", default=None,
                    help="store URL (default: the engine's default DB, an absolute path)")
    ap.add_argument("--reset", action="store_true",
                    help="clear the demo's existing reads first (so the history actually changes)")
    ap.add_argument("--random", dest="randomize", action="store_true",
                    help="pick a different left-leaning set each run")
    ap.add_argument("--seed", type=int, default=None,
                    help="reproducible randomization (same seed -> same set)")
    ap.add_argument("--count", type=int, default=8, help="how many reads to seed (default 8)")
    args = ap.parse_args(argv)

    store = store_mod.Store(args.db)
    print("database        :", store.engine.url.database or store.engine.url)
    if store.count_feed_articles() == 0:
        print("catalog is EMPTY — ingest first, e.g.:")
        print("  python examples/rss_ingest.py run --feeds deploy/rss_feeds.example.txt")
        print("then re-run this script.")
        return 1

    rng = (random.Random(args.seed) if args.seed is not None
           else random.Random() if args.randomize else None)
    r = seed(store, target_reads=args.count, rng=rng, reset=args.reset)

    print(f"demo account    : user_id={r['userId']}  ({PROVIDER} / {ACCOUNT})")
    if r["cleared"]:
        print(f"reset           : cleared {r['cleared']} old read(s)")
    mode = ("random" if (args.randomize and args.seed is None)
            else f"seed={args.seed}" if args.seed is not None else "deterministic")
    print(f"reads           : +{r['added']} new (from {r['picked']} picked of "
          f"{r['catalog']} catalog, {mode}) -> total {r['totalReads']}")
    if not args.reset and args.randomize:
        print("hint            : add --reset to REPLACE the old history instead of adding to it.")
    if r["totalReads"] < 5:
        print("note            : fewer than 5 reads — the measured report needs >=5; "
              "ingest more articles and re-run.")
    print(f"done            : rec_sandbox.py --reader demo now resolves user {r['userId']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
