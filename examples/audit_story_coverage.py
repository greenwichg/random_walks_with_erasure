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
    # the user-facing feed: engine strategies + resolved explanation types (same post-pass
    # the API runs, so this breakdown matches the cards a reader actually sees)
    er._INDEX_CACHE.update(key=None, index=None)
    idx = er.story_index(st)
    ctx = pers.explanation_context(user_id)
    served = pers.recommendations(user_id)
    out["byStrategy"] = dict(Counter(str(r.get("strategy")) for r in served))
    out["byExplanation"] = dict(Counter(er.resolve(r, ctx, idx).get("type") for r in served))
    # each available sibling that was NOT served: the truthful exclusion verdict + raw ranks
    coverage = audit(st, user_id)
    pol_of = {a["canonicalUrl"]: (a.get("scored") or {}).get("political")
              for a in st.list_feed_articles(limit=1_000_000)}
    exclusions = []
    served_urls = {er._canon(str(d.get("url") or "")) for d in recs}
    for p in coverage["perRead"]:
        for m in p.get("siblings") or []:
            if not m["fresh"] or er._canon(str(m["url"])) in served_urls:
                continue
            ex = pers.explain(user_id, article=str(m["url"])).get("exclusion") or {}
            exclusions.append({"sibling": m["url"], "publisher": m["publisher"],
                               "headline": m.get("headline"),
                               "anchor": p["url"], "storyId": p.get("storyId"),
                               "verdict": ex.get("verdict"), "detail": ex.get("detail"),
                               "byStrategy": ex.get("byStrategy") or {},
                               "political": pol_of.get(er._canon(str(m["url"])))})
    out["unservedSiblings"] = exclusions
    return out


#: Explanation-type display labels shared by the CLI printer and the Colab notebook.
LABELS = {"bridge": "Bridge", "story_match": "Story Match", "long_tail": "Discovery",
          "new_publisher": "New Publisher", "topic_continuity": "Topic Continuity",
          "coverage_breadth": "Coverage Breadth"}


def full_report(st, user_id: int) -> dict:
    """THE report computation — a single structured document consumed unchanged by the CLI
    printer (:func:`print_report`) and the Colab notebook, so both surfaces read one source and
    every metric is byte-for-byte identical across them.

    Covers: coverage (reads / clusters / Story Coverage Rate), the served feed (total, by
    engine strategy, by explanation type, Story Match cards), Story Conversion Rate, per-read
    sibling verdicts, the missed-opportunity list (closest-to-serving first, with per-strategy
    ranks), and the health verdict. Sibling exclusion reasons map to the engine's truthful
    taxonomy:

      freshness         outside the RWE_FEED_MAX_AGE_DAYS candidate window (C4) — never a candidate
      already read      you read the sibling too — a reader's own reads are never re-recommended
      ranking cutoff    ranked by every strategy but outside each served slice (per-strategy ranks
                        carried; a non-political sibling is additionally inadmissible to the
                        political-only Bridging slice — flagged as political gating)
      not in graph      in the catalog but not a recommendable node (e.g. unresolved outlet lean)

    First-seen DEDUPLICATION and ANOTHER STRATEGY WINNING can never exclude an article outright
    (dedup only reassigns the serving strategy; strategy competition IS the per-strategy ranking
    cutoff), and a SERVED sibling always explains as story_match (the story_over_bridge golden
    pins the priority). Read-only end to end."""
    cov = audit(st, user_id)
    sc = cov["siblingCoverage"]
    stale_only = cov["verdicts"].get("siblings_all_stale", 0)
    with_siblings = [p for p in cov["perRead"]
                     if p["verdict"] in ("sibling_available", "siblings_all_stale")]
    doc = {"store": st.url, "user": user_id, "coverage": cov, "staleOnly": stale_only,
           "coverageRatePercent": sc["percent"], "measured": None, "feed": None,
           "conversion": None, "perRead": [], "missed": [], "verdict": None,
           "retentionNote": None, "noCoverage": None}

    if not with_siblings:
        also_read = cov["verdicts"].get("all_siblings_read", 0)
        doc["noCoverage"] = {"alsoRead": also_read}
        if cov["reads"] == 0:
            doc["verdict"] = {"code": "insufficient_data",
                              "message": "INSUFFICIENT DATA — no reads in this history."}
        else:
            doc["verdict"] = {"code": "coverage",
                              "message": ("PRIMARILY LIMITED BY CORPUS COVERAGE — "
                                          f"{cov['catalogArticles']} catalog articles, "
                                          f"{cov['multiPublisherClusters']} multi-publisher "
                                          "clusters, 0 live story opportunities.")}
        return doc

    s = serve_and_diagnose(st, user_id)
    measured = bool(s.get("measured")) and "served" in s
    doc["measured"] = measured
    # serve_and_diagnose lists an exclusion for every FRESH sibling that was NOT served, so a
    # fresh sibling absent from that list was served (and a served sibling explains story_match).
    excl_by_url = {x["sibling"]: x for x in s.get("unservedSiblings", [])} if measured else {}
    pol_of = {a["canonicalUrl"]: (a.get("scored") or {}).get("political")
              for a in st.list_feed_articles(limit=1_000_000)}

    story_cards = converted = 0
    live = sc["withSibling"]
    if measured:
        story_cards = len(s.get("servedStoryMatches") or [])
        # an opportunity converts when at least one of ITS fresh siblings is NOT in the
        # unserved list (serve_and_diagnose lists every fresh-but-unserved sibling)
        unserved_by_anchor: dict = {}
        for x in s.get("unservedSiblings", []):
            unserved_by_anchor.setdefault(x["anchor"], set()).add(er._canon(str(x["sibling"])))
        for p in cov["perRead"]:
            if p["verdict"] != "sibling_available":
                continue
            fresh = {er._canon(str(m["url"])) for m in p["siblings"] if m["fresh"]}
            if fresh - unserved_by_anchor.get(p["url"], set()):
                converted += 1
        doc["feed"] = {"served": s["served"], "byStrategy": s.get("byStrategy") or {},
                       "byExplanation": s.get("byExplanation") or {},
                       "storyMatchCards": story_cards,
                       "servedStoryMatches": s.get("servedStoryMatches") or []}
    doc["conversion"] = {"live": live, "converted": converted,
                         "ratePercent": (round(100.0 * converted / live, 1) if live else None)}

    for p in with_siblings:
        art = st.get_feed_article(p["url"]) or {}
        entry = {"url": p["url"], "title": art.get("title") or p["url"],
                 "storyId": p["storyId"], "clusterSize": p["clusterSize"], "siblings": []}
        for m in p.get("siblings") or []:
            curl = er._canon(str(m["url"]))
            sib = {"url": m["url"], "publisher": m["publisher"], "headline": m["headline"],
                   "fresh": bool(m["fresh"]), "detail": None, "politicalGate": False}
            if not m["fresh"]:
                sib["outcome"] = "freshness"
            elif not measured:
                sib["outcome"] = "not_measured"
            else:
                ex = excl_by_url.get(str(m["url"]))
                if ex is None or ex.get("verdict") == "recommended":
                    sib["outcome"] = "served"
                elif ex.get("verdict") == "seen_excluded":
                    sib["outcome"] = "already_read"
                elif ex.get("verdict") == "below_cutoff":
                    sib["outcome"] = "ranking_cutoff"
                    sib["detail"] = ex.get("detail")
                    sib["politicalGate"] = pol_of.get(curl) is False
                else:
                    sib["outcome"] = str(ex.get("verdict"))
                    sib["detail"] = ex.get("detail")
            entry["siblings"].append(sib)
        doc["perRead"].append(entry)

    # ---- top missed opportunities (closest-to-serving first) --------------------------------
    missed = []
    if measured:
        for x in s.get("unservedSiblings", []):
            gaps = []
            for strat, k in (("rwe-b", 6), ("rwe-d", 4), ("adaptive", 4)):
                by = (x.get("byStrategy") or {}).get(strat) or {}
                r = by.get("rank")
                if r is None or (strat == "rwe-b" and x.get("political") is False):
                    continue                     # non-political: rwe-b slice inadmissible
                gaps.append({"gap": r - k, "strategy": strat, "rank": r, "cutoff": k})
            reason = ("RANKING CUTOFF" if x.get("verdict") == "below_cutoff"
                      else str(x.get("verdict") or "unknown").upper())
            missed.append({"gap": (min(g["gap"] for g in gaps) if gaps else 10**9),
                           "ranks": sorted(gaps, key=lambda g: g["gap"]), "reason": reason,
                           "headline": x.get("headline"), "sibling": x["sibling"],
                           "publisher": x.get("publisher"), "anchor": x.get("anchor"),
                           "storyId": x.get("storyId")})
    for p in cov["perRead"]:
        if p["verdict"] == "siblings_all_stale":
            for m in p.get("siblings") or []:
                missed.append({"gap": 10**9, "ranks": [], "reason": "FRESHNESS",
                               "headline": m.get("headline"), "sibling": m["url"],
                               "publisher": m["publisher"], "anchor": p["url"],
                               "storyId": p.get("storyId")})
    doc["missed"] = sorted(missed, key=lambda d: d["gap"])

    # ---- health verdict ----------------------------------------------------------------------
    if not measured:
        doc["verdict"] = {"code": "insufficient_data",
                          "message": ("INSUFFICIENT DATA — reader below the measured threshold; "
                                      "the personal feed does not serve yet.")}
        return doc
    ranking_lost = live - converted
    if live == 0 and stale_only > 0:
        doc["verdict"] = {"code": "freshness",
                          "message": (f"PRIMARILY LIMITED BY FRESHNESS — every existing sibling "
                                      f"({stale_only} read(s)) has aged past the candidate window.")}
    elif live == 0:
        doc["verdict"] = {"code": "coverage",
                          "message": ("PRIMARILY LIMITED BY CORPUS COVERAGE — no live story "
                                      f"opportunities ({cov['multiPublisherClusters']} "
                                      "multi-publisher clusters).")}
    elif converted / live >= 0.8:
        doc["verdict"] = {"code": "none",
                          "message": (f"NO SIGNIFICANT LIMITATION — {converted}/{live} live "
                                      "opportunities served as Story Match.")}
    elif ranking_lost >= max(stale_only, 1):
        doc["verdict"] = {"code": "ranking",
                          "message": (f"PRIMARILY LIMITED BY RANKING — {ranking_lost}/{live} live "
                                      "opportunities exist but their siblings rank below every "
                                      f"served slice (freshness-lost: {stale_only}).")}
    else:
        doc["verdict"] = {"code": "freshness",
                          "message": (f"PRIMARILY LIMITED BY FRESHNESS — {stale_only} "
                                      f"opportunities aged out vs {ranking_lost} ranking-lost "
                                      f"(served {converted}/{live}).")}
    ret_age = os.environ.get("RWE_RETENTION_MAX_AGE_DAYS")
    if ret_age and ret_age.isdigit() and int(ret_age) > 0:
        doc["retentionNote"] = (f"retention is ENABLED ({ret_age}d) — read anchors older than "
                                "that are deleted with no read exemption, which dissolves their "
                                "clusters (see the lifecycle audit); anchors within ~7d of the "
                                "limit put their opportunities at risk.")
    return doc


def print_report(doc: dict) -> None:
    """Render a :func:`full_report` document as the CLI text report — formatting ONLY; every
    number and sentence comes from the document, so the CLI and the notebook cannot drift."""
    cov = doc["coverage"]
    sc = cov["siblingCoverage"]
    print(f"reads: {cov['reads']}   catalog: {cov['catalogArticles']}   "
          f"story clusters: {cov['storyClusters']} ({cov['multiPublisherClusters']} multi-publisher)")
    print(f"sibling coverage: {sc['withSibling']}/{sc['reads']} reads "
          f"({sc['percent']}%) have >= 1 unread same-story sibling available as a candidate")
    print(f"Story Coverage Rate: {sc['percent']}%")

    if doc["noCoverage"] is not None:
        also_read = doc["noCoverage"]["alsoRead"]
        extra = (f" ({also_read} read(s) whose only cross-publisher coverage you ALREADY read)"
                 if also_read else "")
        print("\nNo article in this reading history has an unread same-story sibling in the "
              "catalog — the current corpus lacks cross-publisher coverage for those "
              f"stories{extra}. Story Match is impossible from this data; this is corpus "
              "coverage, not recommendation logic.")
        print("\n==== Health summary ====")
        print(f"verdict: {doc['verdict']['message']}")
        return

    feed, conv = doc["feed"], doc["conversion"]
    if doc["measured"]:
        print("\n==== Recommendation feed ====")
        print(f"total recommendations served: {feed['served']}")
        print(f"by engine strategy: {feed['byStrategy']}")
        pretty = {f"{LABELS.get(k, k)}": v for k, v in feed["byExplanation"].items()}
        print(f"by explanation type: {pretty}")
        print(f"Story Match cards served: {feed['storyMatchCards']}")
        print("\n==== Story conversion ====")
        if conv["live"]:
            print(f"live story opportunities: {conv['live']}   opportunities with a served "
                  f"sibling: {conv['converted']}")
            print(f"Story Conversion Rate: {conv['converted']}/{conv['live']} "
                  f"({conv['ratePercent']}%)")
        else:
            print("live story opportunities: 0   Story Conversion Rate: n/a")

    for p in doc["perRead"]:
        print(f"\nREAD: {p['title']}")
        print(f"      {p['url']}")
        print(f"      validated story cluster: {p['storyId']} ({p['clusterSize']} members)")
        for m in p["siblings"]:
            print(f"  SIBLING: {m['headline']}  ({m['publisher']})")
            print(f"      {m['url']}")
            print(f"      same validated cluster: yes ({p['storyId']})")
            out = m["outcome"]
            if out == "freshness":
                print("      recommended: NO — excluded by FRESHNESS "
                      "(outside the RWE_FEED_MAX_AGE_DAYS candidate window)")
            elif out == "not_measured":
                print("      recommended: n/a — reader below the measured threshold "
                      "(served from the demo path, which has no personal history)")
            elif out == "served":
                print("      recommended: YES — served, explains as story_match")
            elif out == "already_read":
                print("      recommended: NO — ALREADY READ (a reader's own reads are never "
                      "re-recommended)")
            elif out == "ranking_cutoff":
                gate = ("; political gating: NOT admissible to the Bridging slice "
                        "(article is non-political)" if m["politicalGate"] else "")
                print(f"      recommended: NO — RANKING CUTOFF ({m['detail']}{gate})")
            else:
                print(f"      recommended: NO — {out}: {m['detail']}")
    if doc["measured"] and feed["servedStoryMatches"]:
        print(f"\nserved story_match cards this cycle: {feed['servedStoryMatches']}")

    if doc["missed"]:
        print("\n==== Top missed Story Match opportunities ====")
        for i, x in enumerate(doc["missed"][:10], start=1):
            ranks = ", ".join(f"{g['strategy']} #{g['rank']}/(top {g['cutoff']})"
                              for g in x["ranks"]) or "-"
            print(f"{i:>2}. {str(x.get('headline') or x['sibling'])[:70]}  ({x.get('publisher')})")
            print(f"      story {x.get('storyId')}  anchor read: {x.get('anchor')}")
            print(f"      reason: {x['reason']}   ranks: {ranks}")

    print("\n==== Health summary ====")
    print(f"verdict: {doc['verdict']['message']}")
    if doc["retentionNote"]:
        print(f"note: {doc['retentionNote']}")


def sibling_report(st, user_id: int) -> None:
    """Compute + print the full report (the CLI path) — a thin wrapper over
    :func:`full_report` / :func:`print_report`."""
    print_report(full_report(st, user_id))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=None, help="database URL (default: RWE_DB_URL or the repo file)")
    ap.add_argument("--user", type=int, default=1, help="engine user id (default 1)")
    ap.add_argument("--serve", action="store_true",
                    help="also build the serving stack: per-card storyMatch diagnostics + "
                         "exclusion verdicts for unserved siblings")
    ap.add_argument("--report", action="store_true",
                    help="the full report: feed breakdown (total + by strategy/explanation), "
                         "Story Coverage + Conversion rates, per-read sibling verdicts, top-10 "
                         "missed opportunities with reasons + per-strategy ranks, and a health "
                         "verdict (coverage / ranking / freshness / none)")
    ap.add_argument("--list-users", action="store_true",
                    help="list the store's users (id, email, reads) and exit — to pick --user")
    args = ap.parse_args()

    if args.list_users:
        st = store_mod.Store(args.db)
        print(f"store: {st.url}")
        from sqlalchemy import func, select
        with st.session() as s:
            rows = s.execute(
                select(store_mod.User.id, store_mod.User.email, store_mod.User.display_name,
                       func.count(store_mod.Read.id))
                .outerjoin(store_mod.Read, store_mod.Read.user_id == store_mod.User.id)
                .group_by(store_mod.User.id).order_by(store_mod.User.id)).all()
        for uid, email, name, n in rows:
            print(f"  --user {uid}   {email or '-':<32} {name or '-':<20} {n} reads")
        return 0

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
