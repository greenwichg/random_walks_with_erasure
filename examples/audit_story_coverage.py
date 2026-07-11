#!/usr/bin/env python3
"""Story-coverage audit — answer "why is no card a Story Match?" for a live store, mechanically.

Story Match (P1) appears on a card only when ALL of these hold, and this audit checks each link
in order and names the first one that breaks:

  1. the catalog contains articles at all (clusters are built from ``feed_articles`` only);
  2. an article the reader READ belongs to a validated story cluster;
  3. that cluster contains an UNREAD sibling from a DIFFERENT publisher (P1's licensing gate);
  4. the sibling survives recommendation-candidate composition (the C4 freshness window);
  5. the sibling is actually SERVED (ranking + slice admission) — a sibling that exists but
     ranks below the cutoff is a coverage/ranking outcome, not an explanation-selection one:
     whenever a story sibling IS served, story_match outranks bridge by priority (the
     ``story_over_bridge`` golden pins this).

Read-only end to end. ``--serve`` additionally builds the same corpus + recommender stack the
API serves from (honouring RWE_RECS_SOURCE / RWE_FEED_* env exactly like the server), reads the
C5 ``storyMatch`` diagnostic for every served card, and runs the exclusion query for each
available-but-unserved sibling.

    python examples/audit_story_coverage.py                    # audit the default DB
    python examples/audit_story_coverage.py --db sqlite:///... --user 1 --serve
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # import sibling modules

import corpus_health
import evidence_resolver as er
import store as store_mod


def audit(st, user_id: int) -> dict:
    """The coverage half (steps 1–4): per-read story membership + sibling availability."""
    catalog_n = st.count_feed_articles()
    index = er.story_index(st)
    stories = {}
    for v in index.values():
        stories.setdefault(v["storyId"], v["coverage"])
    multi_pub = {sid: cov for sid, cov in stories.items()
                 if len({m.get("publisher") for m in cov}) >= 2}

    reads = st.get_reads(user_id)
    read_urls = {er._canon(str(r.get("article_id") or "")) for r in reads}
    fresh_urls = {a.get("canonicalUrl")
                  for a in corpus_health.fresh_articles(st.list_feed_articles(limit=1_000_000),
                                                        exempt=read_urls)}

    per_read = []
    for r in reads:
        url = er._canon(str(r.get("article_id") or ""))
        publisher = str(r.get("outlet") or "")
        story = index.get(url)
        if story is None:
            per_read.append({"url": url, "verdict": "read_not_in_any_cluster"})
            continue
        cov = story["coverage"]
        siblings = [m for m in cov
                    if m.get("url") and er._canon(str(m["url"])) not in read_urls
                    and str(m.get("publisher") or "") != publisher]
        if len({m.get("publisher") for m in cov}) < 2:
            per_read.append({"url": url, "storyId": story["storyId"],
                             "verdict": "cluster_single_publisher",
                             "clusterSize": len(cov)})
        elif not siblings:
            per_read.append({"url": url, "storyId": story["storyId"],
                             "verdict": "all_siblings_read", "clusterSize": len(cov)})
        else:
            fresh = [m for m in siblings if er._canon(str(m["url"])) in fresh_urls]
            per_read.append({"url": url, "storyId": story["storyId"],
                             "verdict": "sibling_available" if fresh else "siblings_all_stale",
                             "clusterSize": len(cov),
                             "siblings": [{"url": m["url"],
                                           "publisher": m.get("publisher"),
                                           "headline": m.get("headline"),
                                           "fresh": er._canon(str(m["url"])) in fresh_urls}
                                          for m in siblings]})

    verdicts = Counter(p["verdict"] for p in per_read)
    with_sibling = verdicts.get("sibling_available", 0)
    return {"catalogArticles": catalog_n,
            "storyClusters": len(stories),
            "multiPublisherClusters": len(multi_pub),
            "reads": len(reads),
            "readsInClusters": sum(1 for p in per_read if "storyId" in p),
            "perRead": per_read,
            "verdicts": dict(verdicts),
            # the headline coverage metric: share of the reading history with at least one
            # UNREAD, different-publisher, candidate-eligible (fresh) same-story sibling
            "siblingCoverage": {"withSibling": with_sibling, "reads": len(reads),
                                "percent": round(100.0 * with_sibling / len(reads), 1)
                                if reads else 0.0},
            "storyMatchPossible": with_sibling > 0}


def serve_and_diagnose(st, user_id: int) -> dict:
    """The serving half (step 5): build the SAME stack the API serves from (env-driven), read the
    C5 storyMatch diagnostic per served card, and explain each available-but-unserved sibling."""
    from types import SimpleNamespace
    import api_server as engine
    import feed_source
    import personalize

    def _int_env(name):
        v = os.environ.get(name)
        return int(v) if v and v.isdigit() else None

    ns = SimpleNamespace(profile=None, npz=None, qbias=None, register_csv=None, emotion_csv=None,
                         behaviors=None, lean_tau=None, domain=None,
                         n_users=_int_env("RWE_N_USERS"), max_items=_int_env("RWE_MAX_ITEMS"),
                         seed=_int_env("RWE_SEED") or 0)
    feed_csv = feed_source.prepare(st) if feed_source.enabled() else None
    if feed_csv:
        os.environ["RWE_QBIAS"] = feed_csv
        os.environ["RWE_PROFILE"] = "qbias"
    be = engine.Backend(engine.resolve_profile(ns))
    if feed_csv:
        be.attach_url_resolver(feed_source.load_url_map(feed_csv))
    pers = personalize.Personalizer(be, st, persist=False)
    out = {"feedCorpus": bool(feed_csv), "measured": pers.has_measured(user_id)}
    if not out["measured"]:
        out["note"] = "reader below the measured threshold — served from the demo path"
        return out
    diag = pers.explain(user_id)
    recs = diag.get("recommendations") or []
    out["served"] = len(recs)
    out["storyMatchReasons"] = dict(Counter(
        (d.get("storyMatch") or {}).get("reason") for d in recs))
    out["servedStoryMatches"] = [d["headline"] for d in recs
                                 if (d.get("storyMatch") or {}).get("matched")]
    # each available sibling that was NOT served: the truthful exclusion verdict
    coverage = audit(st, user_id)
    exclusions = []
    served_urls = {er._canon(str(d.get("url") or "")) for d in recs}
    for p in coverage["perRead"]:
        for m in p.get("siblings") or []:
            if not m["fresh"] or er._canon(str(m["url"])) in served_urls:
                continue
            ex = pers.explain(user_id, article=str(m["url"])).get("exclusion") or {}
            exclusions.append({"sibling": m["url"], "publisher": m["publisher"],
                               "verdict": ex.get("verdict"), "detail": ex.get("detail")})
    out["unservedSiblings"] = exclusions
    return out


def sibling_report(st, user_id: int) -> None:
    """The full per-read sibling report: every Reading History article with >= 1 same-story
    sibling in the catalog — the sibling(s), the shared validated cluster, whether each sibling
    was recommended, and if not the exact reason it was excluded. Reasons map to the engine's
    truthful taxonomy:

      freshness         outside the RWE_FEED_MAX_AGE_DAYS candidate window (C4) — never a candidate
      already read      you read the sibling too — a reader's own reads are never re-recommended
      ranking cutoff    ranked by every strategy but outside each served slice (per-strategy ranks
                        shown; a non-political sibling is additionally inadmissible to the
                        political-only Bridging slice — noted as political gating)
      not in graph      in the catalog but not a recommendable node (e.g. unresolved outlet lean)

    Two requested reasons cannot exclude an article outright, so they never appear: first-seen
    DEDUPLICATION only reassigns which strategy serves a column (the article is still served),
    and ANOTHER STRATEGY WINNING is what "ranking cutoff" shows per strategy. A SERVED sibling
    always explains as story_match — priority over bridge is pinned by the story_over_bridge
    golden — so "served but explained differently" is not a possible outcome."""
    cov = audit(st, user_id)
    with_siblings = [p for p in cov["perRead"]
                     if p["verdict"] in ("sibling_available", "siblings_all_stale")]
    sc = cov["siblingCoverage"]
    print(f"reads: {cov['reads']}   catalog: {cov['catalogArticles']}   "
          f"story clusters: {cov['storyClusters']} ({cov['multiPublisherClusters']} multi-publisher)")
    print(f"sibling coverage: {sc['withSibling']}/{sc['reads']} reads "
          f"({sc['percent']}%) have >= 1 unread same-story sibling available as a candidate")
    if not with_siblings:
        also_read = cov["verdicts"].get("all_siblings_read", 0)
        extra = (f" ({also_read} read(s) whose only cross-publisher coverage you ALREADY read)"
                 if also_read else "")
        print("\nNo article in this reading history has an unread same-story sibling in the "
              "catalog — the current corpus lacks cross-publisher coverage for those "
              f"stories{extra}. Story Match is impossible from this data; this is corpus "
              "coverage, not recommendation logic.")
        return

    s = serve_and_diagnose(st, user_id)
    measured = bool(s.get("measured")) and "served" in s
    # serve_and_diagnose lists an exclusion for every FRESH sibling that was NOT served, so a
    # fresh sibling absent from that list was served (and a served sibling explains story_match).
    excl_by_url = {x["sibling"]: x for x in s.get("unservedSiblings", [])} if measured else {}
    pol_of = {a["canonicalUrl"]: (a.get("scored") or {}).get("political")
              for a in st.list_feed_articles(limit=1_000_000)}

    for p in with_siblings:
        art = st.get_feed_article(p["url"]) or {}
        print(f"\nREAD: {art.get('title') or p['url']}")
        print(f"      {p['url']}")
        print(f"      validated story cluster: {p['storyId']} ({p['clusterSize']} members)")
        for m in p.get("siblings") or []:
            curl = er._canon(str(m["url"]))
            print(f"  SIBLING: {m['headline']}  ({m['publisher']})")
            print(f"      {m['url']}")
            print(f"      same validated cluster: yes ({p['storyId']})")
            if not m["fresh"]:
                print("      recommended: NO — excluded by FRESHNESS "
                      "(outside the RWE_FEED_MAX_AGE_DAYS candidate window)")
                continue
            if not measured:
                print("      recommended: n/a — reader below the measured threshold "
                      "(served from the demo path, which has no personal history)")
                continue
            ex = excl_by_url.get(str(m["url"]))
            if ex is None or ex.get("verdict") == "recommended":
                print("      recommended: YES — served, explains as story_match")
                continue
            verdict = ex.get("verdict")
            if verdict == "seen_excluded":
                print("      recommended: NO — ALREADY READ (a reader's own reads are never "
                      "re-recommended)")
            elif verdict == "below_cutoff":
                gate = ("; political gating: NOT admissible to the Bridging slice "
                        "(article is non-political)" if pol_of.get(curl) is False else "")
                print(f"      recommended: NO — RANKING CUTOFF ({ex.get('detail')}{gate})")
            else:
                print(f"      recommended: NO — {verdict}: {ex.get('detail')}")
    if measured and s.get("servedStoryMatches"):
        print(f"\nserved story_match cards this cycle: {s['servedStoryMatches']}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=None, help="database URL (default: RWE_DB_URL or the repo file)")
    ap.add_argument("--user", type=int, default=1, help="engine user id (default 1)")
    ap.add_argument("--serve", action="store_true",
                    help="also build the serving stack: per-card storyMatch diagnostics + "
                         "exclusion verdicts for unserved siblings")
    ap.add_argument("--report", action="store_true",
                    help="the per-read sibling report: every read with same-story siblings, "
                         "each sibling's cluster + served/excluded verdict with the exact reason")
    args = ap.parse_args()

    if args.report:
        st = store_mod.Store(args.db)
        print(f"store: {st.url}")
        sibling_report(st, args.user)
        return 0

    st = store_mod.Store(args.db)
    cov = audit(st, args.user)
    sc = cov["siblingCoverage"]
    print(f"store: {st.url}")
    print(f"catalog articles: {cov['catalogArticles']}   story clusters: {cov['storyClusters']}"
          f"   multi-publisher clusters: {cov['multiPublisherClusters']}")
    print(f"reads (user {args.user}): {cov['reads']}   in any cluster: {cov['readsInClusters']}")
    print(f"sibling coverage: {sc['withSibling']}/{sc['reads']} reads ({sc['percent']}%) "
          f"have >= 1 unread same-story sibling available as a candidate")
    print(f"per-read verdicts: {cov['verdicts'] or '-'}")
    for p in cov["perRead"]:
        if p["verdict"] in ("sibling_available", "siblings_all_stale"):
            print(f"  {p['verdict']}: read {p['url'][:70]}")
            for m in p.get("siblings") or []:
                print(f"      sibling [{'fresh' if m['fresh'] else 'STALE'}] "
                      f"{m['publisher']}: {str(m['headline'])[:60]}")
    print("story_match possible from current data:", cov["storyMatchPossible"])

    if args.serve:
        s = serve_and_diagnose(st, args.user)
        print(f"\nserving stack: feedCorpus={s.get('feedCorpus')} measured={s.get('measured')}")
        if "served" in s:
            print(f"served: {s['served']}   per-card storyMatch reasons: {s['storyMatchReasons']}")
            if s["servedStoryMatches"]:
                print("served story_match cards:", s["servedStoryMatches"])
            for x in s["unservedSiblings"]:
                print(f"  unserved sibling {x['publisher']}: {x['verdict']} — {x['detail']}")
        elif "note" in s:
            print(s["note"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
