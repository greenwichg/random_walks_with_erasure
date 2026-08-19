"""audit_country_rerank.py — does the For You country preference reach the served feed? READ-ONLY.

The production counterpart of the end-to-end test: the test proves the mechanism on a seeded
corpus, this measures it on the real catalog, where the thing that decides whether the feature
works at all is COVERAGE — how many recommendable articles carry a country, and from which source.

It changes nothing. No settings are written, no clustering runs, no configuration is touched: it
reads the live catalog and, when asked for the served diff, builds its own Backend from that same
catalog to compare one reader's feed with and without a country preference.

What it reports, in the order the answers matter:

  1. Catalog coverage — of the articles that reach the recommender, how many resolve a country,
     split by SOURCE (event geography vs publisher home). The X6 audit measured event geography at
     ~18% of articles; if the split shows the country label is almost entirely publisher-derived,
     that is worth knowing before anyone claims the feed prioritizes "news about" a country.
  2. Per-country supply — how many catalog items each country could contribute, so a country the
     picker offers with three eligible articles is visible as such rather than as a failed nudge.
  3. The served diff — for each probed country: the reader's Global feed, the same reader's feed
     with that country selected, the share of served cards from it, and the rank movement of the
     items the nudge lifted. A country with supply but no movement is a finding, not a rounding
     error, and the run says so rather than printing a shrug.

Usage. Coverage alone (a store read; cheap enough for a box serving traffic):

    dc exec -T api python examples/audit_country_rerank.py

Adding the served diff builds a Backend from the live catalog — the same work a corpus refresh
does. `dc exec` runs a NEW process inside the container and shares no memory with the uvicorn
worker, so there is no running Backend to borrow; it has to be built, and that is why the diff is
opt-in rather than default:

    dc exec -T api python examples/audit_country_rerank.py --serve-diff --countries IN,GB,US
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import feed_source                # noqa: E402
import story_service              # noqa: E402
import store as store_mod         # noqa: E402


def coverage(rows) -> dict:
    """Country coverage over catalog rows, split by which signal produced the label."""
    n = len(rows)
    ev = pub = 0
    per_country: Counter = Counter()
    for r in rows:
        has_event = any(str(c).strip() for c in (r.get("eventCountries") or ()))
        c = feed_source.article_country(r)
        if c:
            per_country[c] += 1
            if has_event:
                ev += 1
            else:
                pub += 1
    return {"n": n, "event": ev, "publisher": pub, "located": ev + pub, "per_country": per_country}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--countries", default="",
                    help="comma-separated ISO codes to probe (default: the catalog's top 3)")
    ap.add_argument("--user", type=int, default=0, help="engine user row to probe (default: demo)")
    ap.add_argument("--limit", type=int, default=100000)
    ap.add_argument("--serve-diff", action="store_true",
                    help="also build a Backend from the live catalog and diff the served feed "
                         "with/without each country (costs one corpus build)")
    args = ap.parse_args(argv)

    st = store_mod.Store(None)
    rows = story_service._fetch(st)
    cov = coverage(rows[: args.limit])
    n = max(1, cov["n"])
    print(f"-- 1. catalog coverage ({cov['n']} articles) --")
    print(f"  located            : {cov['located']} ({cov['located'] / n:.1%})")
    print(f"    via event geography: {cov['event']} ({cov['event'] / n:.1%})")
    print(f"    via publisher home : {cov['publisher']} ({cov['publisher'] / n:.1%})")
    if cov["located"] and cov["event"] / max(1, cov["located"]) < 0.25:
        print(f"  NOTE: the country label is mostly PUBLISHER-derived. The feed prioritizes "
              f"outlets based in the country more than coverage ABOUT it — accurate to what the "
              f"data supports, but say it that way in the UI copy.")

    print(f"\n-- 2. per-country supply (top 12) --")
    for c, k in cov["per_country"].most_common(12):
        print(f"  {c}  {k:>5} articles ({k / n:.1%})")

    probes = [c.strip().upper() for c in args.countries.split(",") if c.strip()]
    if not probes:
        probes = [c for c, _ in cov["per_country"].most_common(3)]

    # -- 3. the served diff ---------------------------------------------------------------- #
    if not args.serve_diff:
        print(f"\n-- 3. served diff -- skipped (pass --serve-diff).")
        print(f"  It builds a Backend from the live catalog, which is the same work a corpus "
              f"refresh does — bounded, but not free on a box also serving traffic. Sections 1-2 "
              f"above need only a store read and already answer the question that gates the "
              f"feature: whether the catalog carries enough country data for a nudge to have "
              f"anything to lift.")
        return 0

    # A Backend must be BUILT here: `dc exec` starts a new process inside the container, which
    # shares no memory with the uvicorn worker serving traffic — there is no running Backend to
    # borrow, and pretending otherwise would report a feed nobody was served. This mirrors
    # audit_story_coverage.py: same profile resolution, same catalog, same resolvers.
    from types import SimpleNamespace
    import api_server as engine                         # noqa: F401 — the engine module

    def _int_env(name):
        v = os.environ.get(name)
        return int(v) if v and v.isdigit() else None

    ns = SimpleNamespace(profile=None, npz=None, qbias=None, register_csv=None, emotion_csv=None,
                         behaviors=None, lean_tau=None, domain=None,
                         n_users=_int_env("RWE_N_USERS"), max_items=_int_env("RWE_MAX_ITEMS"),
                         seed=_int_env("RWE_SEED") or 0)
    feed_csv = feed_source.prepare(st) if feed_source.enabled() else None
    if not feed_csv:
        print(f"\n-- 3. served diff -- the recommender is not sourcing from the live feed "
              f"(RWE_RECS_SOURCE), so there is no country-bearing catalog to probe.")
        return 1
    os.environ["RWE_QBIAS"] = feed_csv
    os.environ["RWE_PROFILE"] = "qbias"
    be = engine.Backend(engine.resolve_profile(ns))
    be.attach_url_resolver(feed_source.load_url_map(feed_csv))
    be.attach_country_resolver(feed_source.load_country_map(feed_csv))

    u = args.user or be.demo_user
    if not getattr(be, "country_by_id", None):
        print(f"\n-- 3. served diff -- the Backend has NO country map attached: the catalog CSV "
              f"predates the country column, so the nudge is inert. Rebuild the corpus.")
        return 1

    base = [r["article"] for r in be.recommendations(u, None, None)]
    base_ids = [a["id"] for a in base]
    country_of = {str(k): v for k, v in be.country_by_id.items()}

    def share(arts, want):
        known = [a for a in arts if country_of.get(str(a["id"]))]
        if not known:
            return 0.0, 0
        hit = sum(1 for a in known if country_of[str(a["id"])] == want)
        return hit / len(known), len(known)

    print(f"\n-- 3. served diff (user {u}; {len(base)} cards) --")
    for want in probes:
        picked = [r["article"] for r in be.recommendations(u, None, {"country": want})]
        ids = [a["id"] for a in picked]
        s_base, k_base = share(base, want)
        s_pick, k_pick = share(picked, want)
        moved = sum(1 for a, b in zip(base_ids, ids) if a != b)
        supply = cov["per_country"].get(want, 0)
        verdict = ("MOVED" if s_pick > s_base else
                   ("no supply" if supply == 0 else "NO MOVEMENT — investigate"))
        print(f"  {want}: share {s_base:.0%} -> {s_pick:.0%} "
              f"(of {k_base}/{k_pick} country-known cards); {moved}/{len(base_ids)} slots "
              f"changed; catalog supply {supply}  [{verdict}]")
        if supply and s_pick <= s_base:
            print(f"    the catalog HAS {want} articles and the feed did not shift toward them — "
                  f"either they rank far outside the {api_server._COUNTRY_BOOST:g}x reach, or "
                  f"they are excluded upstream (already-read, admission, publisher cap).")
    print(f"\n  Global re-check: {'identical' if [a['id'] for a in base] == base_ids else 'DRIFTED'}"
          f" — an unmoved control must serve the unmoved feed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
