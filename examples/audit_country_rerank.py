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

To measure what ONE REAL READER is served, find their id and probe the personalized feed — the
augmented corpus they actually get, not the base corpus's demo row (`--user` is a corpus row
index, which is a different thing and belongs to nobody):

    dc exec -T api python examples/audit_country_rerank.py --list-users
    dc exec -T api python examples/audit_country_rerank.py --serve-diff --engine-user 3 --countries IN
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np               # noqa: E402
import feed_source                # noqa: E402
import story_service              # noqa: E402
import store as store_mod         # noqa: E402


def coverage(rows, source: str) -> dict:
    """Country coverage over catalog rows.

    Two different things, deliberately kept apart, because reporting one under the other's name
    misled a production verification: `ev`/`pub`/`located` are the PROVENANCE of the legacy
    single-label resolver (does any label exist, and did it come from the event or the outlet),
    while `matched` and `per_country` count what the feed ACTUALLY matches on — `source`, whose
    default is `content`. Under `content` a Delhi outlet's article about Washington is not India
    supply, so the two counts differ by thousands of articles and only one of them predicts what
    a reader is served."""
    n = len(rows)
    ev = pub = matched = 0
    per_country: Counter = Counter()
    for r in rows:
        has_event = any(str(c).strip() for c in (r.get("eventCountries") or ()))
        if feed_source.article_country(r):
            ev, pub = (ev + 1, pub) if has_event else (ev, pub + 1)
        cs = feed_source.article_countries(r, source)
        if cs:
            matched += 1
            for c in cs:
                per_country[c] += 1
    return {"n": n, "event": ev, "publisher": pub, "located": ev + pub,
            "matched": matched, "per_country": per_country}


def n_country_in(arts, want: str, country_of: dict) -> int:
    """Raw count of served cards matching `want` — the series to read, since the share's
    denominator (cards with a KNOWN country) varies run to run and can make an unchanged count
    look like a decline."""
    return sum(1 for a in arts if want in country_of.get(str(a["id"]), ()))


def in_share_str(arts, want: str, country_of: dict) -> str:
    """Share of the CARDS WHOSE COUNTRY IS KNOWN that match `want`, plus the raw card count —
    the denominator is stated because an unlocated card is not evidence either way."""
    known = [a for a in arts if country_of.get(str(a["id"]))]
    if not known:
        return "n/a (0 known)"
    hit = sum(1 for a in known if want in country_of[str(a["id"])])
    return f"{hit / len(known):.0%} ({hit}/{len(known)})"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--countries", default="",
                    help="comma-separated ISO codes to probe (default: the catalog's top 3)")
    ap.add_argument("--user", type=int, default=0,
                    help="CORPUS ROW to probe (default: demo). Not an account id — see "
                         "--engine-user, which is what a real reader has.")
    ap.add_argument("--engine-user", type=int, default=0,
                    help="a REAL reader's engine user id: probes the personalized feed they are "
                         "actually served (augmented corpus), not the base corpus's demo reader")
    ap.add_argument("--list-users", action="store_true",
                    help="list engine users with their read counts, then exit")
    ap.add_argument("--limit", type=int, default=100000)
    ap.add_argument("--sources", default="",
                    help="compare what COUNTS as belonging to a country: "
                         "event,mention,content,publisher,union. `content` is event|mention — "
                         "what the article is ABOUT, never the outlet's home.")
    ap.add_argument("--backfill-check", action="store_true",
                    help="for a thin country: verify the feed is not short, the Bridging "
                         "allocation is preserved, backfill comes from the normal ranking, and "
                         "every card is labelled matched-vs-backfill")
    ap.add_argument("--modes", default="",
                    help="compare country ordering modes head to head, e.g. boost,first — "
                         "reports country cards AND whether Bridging keeps serving cross-cutting "
                         "articles, which is what a country-dominated feed puts at risk")
    ap.add_argument("--boost-sweep", default="",
                    help="comma-separated country-boost anchors to measure (e.g. 8,12,16,20). "
                         "Reports the country gain AND the interest dilution at each, so the "
                         "anchor is chosen from numbers the way the Interest curve was retuned.")
    ap.add_argument("--serve-diff", action="store_true",
                    help="also build a Backend from the live catalog and diff the served feed "
                         "with/without each country (costs one corpus build)")
    args = ap.parse_args(argv)

    st = store_mod.Store(None)

    if args.list_users:
        from store import User
        from sqlalchemy import select
        with st.session() as sess:
            users = sess.execute(
                select(User.id, User.email, User.display_name).order_by(User.id)).all()
        print(f"-- engine users ({len(users)}) --")
        for uid, email, name in users:
            print(f"  --engine-user {uid:<5} reads={st.count_reads(uid):<5} "
                  f"{email or name or '(no profile)'}")
        print("\n  A reader below the measured-reads threshold is served the demo path, so their "
              "feed is not personalized and probing it measures the demo reader.")
        return 0

    rows = story_service._fetch(st)
    active = feed_source.country_source()
    cov = coverage(rows[: args.limit], active)
    n = max(1, cov["n"])
    print(f"-- 1. catalog coverage ({cov['n']} articles) --")
    print(f"  MATCHING SOURCE    : {active}  (RWE_REC_COUNTRY_SOURCE)")
    print(f"  matched by it      : {cov['matched']} ({cov['matched'] / n:.1%})"
          f"  <-- the supply the feed can actually draw on")
    print(f"  any label at all   : {cov['located']} ({cov['located'] / n:.1%})   [legacy resolver]")
    print(f"    via event geography: {cov['event']} ({cov['event'] / n:.1%})")
    print(f"    via publisher home : {cov['publisher']} ({cov['publisher'] / n:.1%})")
    if active in ("publisher", "union"):
        print(f"  NOTE: `{active}` counts the OUTLET's home country, so a Delhi paper's article "
              f"about Washington reads as India news. That is provenance, not subject — say it "
              f"that way in the UI copy, or set RWE_REC_COUNTRY_SOURCE=content.")

    print(f"\n-- 2. per-country supply under `{active}` (top 12) --")
    for c, k in cov["per_country"].most_common(12):
        print(f"  {c}  {k:>5} articles ({k / n:.1%})")
    print(f"  Counted under the ACTIVE matching source, not the legacy label: reporting the "
          f"publisher-inclusive count here makes a thin country look well supplied and turns an "
          f"honest backfill into an apparent ranking bug.")

    # -- 2b. what counts as belonging ------------------------------------------------------- #
    sources = [x.strip().lower() for x in args.sources.split(",") if x.strip()]
    if sources:
        per: dict = {src: Counter() for src in sources}
        for r in rows[: args.limit]:
            for src in sources:
                for c in feed_source.article_countries(r, src):
                    per[src][c] += 1
        want_list = [c.strip().upper() for c in args.countries.split(",") if c.strip()]
        if not want_list:
            want_list = [c for c, _ in per[sources[0]].most_common(8)]
        print(f"\n-- 2b. supply by definition ({cov['n']} articles) --")
        print(f"  {'country':>8}  " + "  ".join(f"{src:>10}" for src in sources))
        for c in want_list:
            print(f"  {c:>8}  " + "  ".join(f"{per[src].get(c, 0):>10}" for src in sources))
        print(f"  {'TOTAL':>8}  " + "  ".join(
            f"{sum(1 for r in rows[: args.limit] if feed_source.article_countries(r, src)):>10}"
            for src in sources))
        print(f"\n  Supply bounds what a country CAN fill, but predicts it poorly — measured "
              f"2026-08-19 on a 14-card personalized feed under content matching: MY with 113 "
              f"eligible articles filled 6 slots, IN with 374 filled 11, GB with 1,148 filled 9. "
              f"A ~200 'cannot fill a feed' rule of thumb was stated here earlier and the numbers "
              f"refuted it: the reader's own ranking dominates, so read this table as an upper "
              f"bound and the served diff (section 3b) as the answer.")

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

    if args.engine_user:
        # The feed a REAL reader is served comes from their augmented corpus (personalize.py),
        # not the base corpus's demo row — probing `be.recommendations` for them would measure a
        # simulated reader and call it theirs.
        import personalize
        pers = personalize.Personalizer(be, st, persist=False)
        if not pers.has_measured(args.engine_user):
            print(f"\n-- 3. served diff -- user {args.engine_user} is below the measured-reads "
                  f"threshold, so they are served the demo path, not a personalized feed. "
                  f"Nothing reader-specific to measure yet.")
            return 1
        serve_full = lambda p: pers.recommendations(args.engine_user, None, p)
        who = f"engine user {args.engine_user} (personalized)"
    else:
        u = args.user or be.demo_user
        serve_full = lambda p: be.recommendations(u, None, p)
        who = f"corpus row {u}"
    serve = lambda p: [r["article"] for r in serve_full(p)]

    if not getattr(be, "country_by_id", None):
        print(f"\n-- 3. served diff -- the Backend has NO country map attached: the catalog CSV "
              f"predates the country column, so the nudge is inert. Rebuild the corpus.")
        return 1

    base = serve(None)
    base_ids = [a["id"] for a in base]
    country_of = {str(k): frozenset(v) if not isinstance(v, str) else frozenset({v})
                  for k, v in be.country_by_id.items()}

    def share(arts, want):
        known = [a for a in arts if country_of.get(str(a["id"]))]
        if not known:
            return 0.0, 0
        hit = sum(1 for a in known if want in country_of[str(a["id"])])
        return hit / len(known), len(known)

    print(f"\n-- 3. served diff ({who}; {len(base)} cards) --")
    for want in probes:
        picked = serve({"country": want})
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
                  f"either they rank far outside the {engine._COUNTRY_BOOST:g}x reach, or "
                  f"they are excluded upstream (already-read, admission, publisher cap).")
    print(f"\n  Global re-check: {'identical' if [a['id'] for a in base] == base_ids else 'DRIFTED'}"
          f" — an unmoved control must serve the unmoved feed.")

    # -- 3b. mode comparison ----------------------------------------------------------------- #
    modes = [m.strip().lower() for m in args.modes.split(",") if m.strip()]
    if modes:
        def bridge_stats(recs):
            """Bridging is the slice whose whole job is opposing perspectives. A country-dominated
            feed can starve it, and that — not a short feed — is what `first` actually risks."""
            br = [r for r in recs if r.get("strategy") == "rwe-b"]
            return len(br), sum(1 for r in br if r.get("crossCutting"))

        print(f"\n-- 3b. ordering modes ({who}) --")
        print(f"  {'mode':>8}  {'country cards':>13}  {'slots moved':>11}  "
              f"{'bridging':>9}  {'cross-cutting':>13}")
        for want in probes:
            base_full = serve_full(None)
            b_n, b_x = bridge_stats(base_full)
            print(f"  {want}:")
            print(f"  {'global':>8}  {n_country_in(base, want, country_of):>13}  {'0':>11}  "
                  f"{b_n:>9}  {b_x:>13}")
            for m in modes:
                recs = serve_full({"country": want, "countryMode": m})
                arts = [r["article"] for r in recs]
                n_br, n_x = bridge_stats(recs)
                moved = sum(1 for x, y in zip(base_ids, [a["id"] for a in arts]) if x != y)
                warn = "   <-- BRIDGING EMPTY" if n_br and not n_x else ""
                print(f"  {m:>8}  {n_country_in(arts, want, country_of):>13}  {moved:>11}  "
                      f"{n_br:>9}  {n_x:>13}{warn}")
        print(f"  A cross-cutting count that falls to zero means the country preference has "
              f"starved the slice that exists to show opposing perspectives — the one real cost "
              f"of `first`, and the reason it is env-reversible.")

    # -- 3c. backfill quality --------------------------------------------------------------- #
    if args.backfill_check:
        base_full = serve_full(None)
        base_pubs = {(r["article"].get("publisher") or "") for r in base_full}
        base_ids_set = {r["article"]["id"] for r in base_full}
        b_plan = Counter(r.get("strategy") for r in base_full)
        b_cross = sum(1 for r in base_full if r.get("crossCutting"))
        b_cross_b = sum(1 for r in base_full
                        if r.get("strategy") == "rwe-b" and r.get("crossCutting"))
        print(f"\n-- 3c. backfill quality ({who}) --")
        print(f"  Global baseline: {len(base_full)} cards, {len(base_pubs)} publishers, "
              f"plan {dict(b_plan)}, cross-cutting {b_cross_b} in bridging / {b_cross} overall")
        for want in probes:
            recs = serve_full({"country": want})
            arts = [r["article"] for r in recs]
            matched = [r for r in recs if r.get("countryMatch")]
            fill = [r for r in recs if not r.get("countryMatch")]
            pubs = {(a.get("publisher") or "") for a in arts}
            plan_now = Counter(r.get("strategy") for r in recs)
            cross = sum(1 for r in recs if r.get("crossCutting"))
            # rwe-b is the slice whose CONTRACT is opposing perspectives. A card in another slice
            # that happens to be cross-cutting is incidental and free to change — reordering the
            # non-bridging slices is the whole point of a country preference. Judging the total
            # conflates the two and cries wolf; this instrument did exactly that until the
            # interaction audit split them (2026-08-19).
            cross_b = sum(1 for r in recs if r.get("strategy") == "rwe-b" and r.get("crossCutting"))
            # WHERE the matched cards land. Two countries with very different supply both
            # returning exactly 8 matched + 6 backfill, with the backfill equal to the rwe-b
            # budget, is the shape of a partition that reaches some slices and not others —
            # supply alone would not land on the same number twice. Per-strategy counts settle
            # it by measurement; the totals cannot.
            by_slice = Counter(r.get("strategy") for r in matched)
            bits, starved, full = [], [], []
            for s, tot in plan_now.most_common():
                got = by_slice.get(s, 0)
                bits.append(f"{s} {got}/{tot}")
                if tot and not got:
                    starved.append(s)
                elif tot and got >= tot:
                    full.append(s)
            # A slice at zero while the OTHERS saturate localises the ceiling to that slice's
            # admitted pool — rwe-b admits only political articles that cross the reader's lean,
            # so a country card must clear that before it can appear there. Without the
            # saturated-elsewhere condition this reads as "the partition never reached the
            # slice", which a run showing 1/6 disproves; say only what the counts support.
            note = ""
            if starved and full:
                note = (f"   <-- {','.join(starved)} took none while {','.join(full)} filled: "
                        f"read that slice's admission rule before blaming supply")
            elif starved:
                note = f"   <-- {','.join(starved)} took none"
            # Backfill should be the reader's ORDINARY recommendations, not scraped from the
            # bottom of the ranking. Overlap with the Global feed is the evidence for that.
            overlap = sum(1 for r in fill if r["article"]["id"] in base_ids_set)
            short = len(recs) < len(base_full)
            print(f"\n  {want}: {len(recs)} cards = {len(matched)} matched + {len(fill)} backfill"
                  f"{'   <-- SHORT FEED' if short else ''}")
            print(f"    plan          : {dict(plan_now)}"
                  f"{'   <-- BRIDGING ALLOCATION CHANGED' if plan_now.get('rwe-b') != b_plan.get('rwe-b') else '   (bridging held)'}")
            print(f"    cross-cutting : bridging {cross_b} (global {b_cross_b})"
                  f"{'   <-- DIVERSITY LOST' if cross_b < b_cross_b else ''}"
                  f";  all slices {cross} (global {b_cross})"
                  f"{'  — incidental, outside the contract' if cross < b_cross else ''}")
            print(f"    matched where : {', '.join(bits)}{note}")
            print(f"    publishers    : {len(pubs)} distinct (global {len(base_pubs)})")
            print(f"    backfill also in the Global feed: {overlap}/{len(fill)}"
                  f"{'   <-- backfill is NOT the normal feed; investigate' if fill and overlap == 0 else ''}")
            print(f"    every card labelled: "
                  f"{all('countryMatch' in r for r in recs)}")
        print(f"\n  Three things must hold for a thin country: the feed is NOT short, the "
              f"bridging allocation is unchanged, and every card says whether it matched — an "
              f"unlabelled backfill lets a country with 100 articles look like it filled a feed.")

    # -- 4. the boost sweep ----------------------------------------------------------------- #
    anchors = [float(x) for x in args.boost_sweep.replace(" ", "").split(",") if x]
    if not anchors:
        return 0

    # The dilution arm needs a topic with real supply, so it uses the catalog's commonest one at
    # slider 10 — the strongest interest a reader can express. Measuring the country boost alone
    # would show only the benefit; the cost is that both preferences multiply into ONE sort key,
    # so every increment of country boost buys country cards partly at the interest's expense.
    cats = [str(c).strip().lower() for c in np.asarray(be.mind.categories)]
    topic = Counter(c for c in cats if c).most_common(1)[0][0]
    # The served payload carries the PRETTIFIED label ("technology" -> "Technology", and
    # "arts_culture" -> "Arts Culture"), so match on that rather than on the raw category —
    # lower-casing the served value back would silently miss every underscored taxonomy label.
    label = engine._prettify(topic)
    n_topic_in = lambda arts: sum(1 for a in arts if a.get("topic") == label)
    interest_only = serve({"interests": {topic: 10}})
    ref_topic = n_topic_in(interest_only)

    for want in probes:
        print(f"\n-- 4. boost sweep for {want} (interest arm: '{label}' at slider 10) --")
        print(f"  {'boost':>7}  {'country cards':>13}  {'share':>12}  {'slots moved':>11}  "
              f"{'topic cards':>11}  {'vs interest-only':>16}")
        print(f"  {'—':>7}  {n_country_in(base, want, country_of):>13}  "
              f"{in_share_str(base, want, country_of):>12}  {'0':>11}  "
              f"{ref_topic:>11}  {'(reference)':>16}")
        for b in anchors:
            both = serve({"country": want, "countryBoost": b, "interests": {topic: 10}})
            only = serve({"country": want, "countryBoost": b})
            moved = sum(1 for x, y in zip(base_ids, [a["id"] for a in only]) if x != y)
            n_topic = n_topic_in(both)
            print(f"  {b:>6.0f}x  {n_country_in(only, want, country_of):>13}  "
                  f"{in_share_str(only, want, country_of):>12}  {moved:>11}  "
                  f"{n_topic:>11}  {n_topic - ref_topic:>+16d}")
        print(f"  (card COUNT is the honest series: the share's denominator is the cards whose "
              f"country is known, which itself moves between runs.)")
    print(f"\n  Read the last column as the cost: how many '{label}' cards a reader with that "
          f"slider at 10 loses once the country boost competes with it. A boost that buys country "
          f"cards by emptying the interest arm has made the eight sliders decorative.")
    print(f"  The shipped anchor is {engine._COUNTRY_BOOST:g}x; changing it means editing "
          f"_COUNTRY_BOOST, not passing countryBoost — that key is measurement-only and no reader "
          f"can set it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
