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

Narrow and idempotent: ``upsert_user_by_identity`` and ``add_read`` both dedup, so re-running
adds nothing new. It only ever writes the demo account and its reads — never the catalog, never
any other user, never any project code.

Usage
-----
    python examples/seed_demo_reader.py                              # the engine's default DB
    python examples/seed_demo_reader.py sqlite:///data/ih_beta.db    # an explicit store URL

With no argument it targets ``store.default_db_url()`` (the same absolute file the engine writes
to), so it is immune to the working-directory ambiguity of a relative ``sqlite:///data/...`` URL.
"""
import pathlib
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # so `import store` works anywhere
import store as store_mod                                          # noqa: E402

PROVIDER = "dev"
ACCOUNT = "demo@infodiet.local"


def _iso(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def seed(store, target_reads: int = 8) -> dict:
    """Create the demo account (if absent) and top it up toward ``target_reads`` catalog reads.

    Returns a summary dict ``{userId, catalog, picked, added, totalReads}``. Idempotent: only NEW
    ``(user, canonical_url)`` pairs are recorded, so a second call adds ``0``. Reads are chosen as
    a left-leaning diet across the political spectrum and keyed by each article's canonical URL, so
    they attach to the recommendation graph exactly as a real read would."""
    uid = store.upsert_user_by_identity(PROVIDER, ACCOUNT, email=ACCOUNT,
                                        display_name="Demo Reader").id
    catalog = store.list_feed_articles(limit=200)
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

    take(lambda l: l < -0.5, 4)              # 4 left
    take(lambda l: -0.5 <= l <= 0.5, 2)      # 2 centre
    take(lambda l: l > 0.5, 2)               # 2 right
    take(lambda l: True, target_reads - len(picks))   # top up if a bucket ran dry

    added = 0
    for i, a in enumerate(picks):
        sc = a["scored"]
        canonical = sc.get("article_id") or a["canonicalUrl"]
        if store.add_read(uid, canonical, sc, _iso(1 + i), read_source="seed"):
            added += 1
    return {"userId": uid, "catalog": len(catalog), "picked": len(picks),
            "added": added, "totalReads": store.count_reads(uid)}


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    db = argv[0] if argv else None            # None -> store.default_db_url() (absolute; cwd-proof)
    store = store_mod.Store(db)
    print("database        :", store.engine.url.database or store.engine.url)
    if store.count_feed_articles() == 0:
        print("catalog is EMPTY — ingest first, e.g.:")
        print("  python examples/rss_ingest.py run --feeds deploy/rss_feeds.example.txt")
        print("then re-run this script.")
        return 1
    r = seed(store)
    print(f"demo account    : user_id={r['userId']}  ({PROVIDER} / {ACCOUNT})")
    print(f"reads           : +{r['added']} new (from {r['picked']} picked of "
          f"{r['catalog']} catalog) -> total {r['totalReads']}")
    if r["totalReads"] < 5:
        print("note            : fewer than 5 reads — the measured report needs >=5; "
              "ingest more articles and re-run.")
    print(f"done            : rec_sandbox.py --reader demo now resolves user {r['userId']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
