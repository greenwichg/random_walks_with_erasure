"""audit_coverage_comparison.py — readiness + reach of Coverage Comparison, on the live catalog.

Two questions, one read-only pass over the stories the product actually serves:

**1. Does L0 reach anything?** For every clustered article, would a comparison render, and if not,
which gate refused it? A feature that silently renders nothing on 95% of articles is not shipped,
it is merely deployed — this counts that honestly.

**2. Is the catalog ready for L1–L3?** The design (docs/COVERAGE_COMPARISON_DESIGN.md §2) gates
the text-derived tiers behind a measurement this repo did not have: how much of the catalog
carries a ``body`` at all, how long those bodies are, how many clusters have enough *comparable*
members, and how multilingual clusters are. Those numbers decide whether salient-term deltas,
figure discrepancies and quoted-voice comparison are worth building — and they are printed here
rather than assumed.

    python examples/audit_coverage_comparison.py                 # both sections
    python examples/audit_coverage_comparison.py --show 15       # sample the refusals
    python examples/audit_coverage_comparison.py --readiness     # just the L1-L3 readiness

Read-only: it opens the store, builds the same default story view the product serves, and writes
nothing.
"""

from __future__ import annotations

import argparse
import statistics
from collections import Counter

import coverage_comparison as cc
import ingest                       # canonical_url — coverage carries the PUBLISHER url
import story_service
import store as store_mod


def _canon(m: dict) -> str:
    """The catalog key for a coverage member. ``m['url']`` is the publisher's own URL; the
    catalog (and the event-location side table) are keyed by the CANONICAL url, exactly as
    ``article_analyzer._story_block`` resolves it."""
    return ingest.canonical_url(str(m.get("id") or m.get("url") or ""))


def _bodies(store_, urls: "set[str]") -> dict:
    """canonical_url -> len(body or ''), for the sampled members only."""
    from sqlalchemy import select
    if not urls:
        return {}
    out: dict = {}
    with store_._Session() as s:
        rows = s.execute(select(store_mod.FeedArticle.canonical_url, store_mod.FeedArticle.body,
                                store_mod.FeedArticle.description, store_mod.FeedArticle.language)
                         .where(store_mod.FeedArticle.canonical_url.in_(list(urls)))).all()
    for canon, body, desc, lang in rows:
        out[canon] = {"body": len(body or ""), "desc": len(desc or ""), "lang": (lang or "")[:2]}
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=None)
    ap.add_argument("--show", type=int, default=8, help="sample rows per section")
    ap.add_argument("--readiness", action="store_true", help="skip the L0 reach section")
    args = ap.parse_args(argv)

    st = store_mod.Store(args.db)
    stories = story_service.default_story_view(st, build_inline=True)
    print(f"stories in the served view : {len(stories):,}")
    print(f"L0 enabled                 : {cc.enabled()}   min_publishers={cc.min_publishers()}")

    members_total = sum(len(s.get("coverage") or []) for s in stories)
    print(f"clustered articles         : {members_total:,}")

    if not args.readiness:
        # ---- 1. L0 reach: what renders, and what refuses -------------------------------------
        reasons: Counter = Counter()
        findings: Counter = Counter()
        unique_kinds: Counter = Counter()
        rendered, examples = 0, []
        for s in stories:
            for m in s.get("coverage") or []:
                art = {"publisher": m.get("publisher"), "url": m.get("url"),
                       "leanBucket": m.get("leanBucket"), "register": m.get("register")}
                try:
                    countries = st.article_event_countries(_canon(m))
                except Exception:
                    countries = []
                out = cc.compare(art, s, target_countries=countries, member=m)
                if not out.get("available"):
                    reasons[out.get("reason")] += 1
                    continue
                rendered += 1
                for f in out["reportedElsewhere"]:
                    findings[f["key"]] += 1
                for f in out["uniqueHere"]:
                    unique_kinds[f["key"]] += 1
                if len(examples) < args.show and out["uniqueHere"]:
                    examples.append((m.get("publisher"), out["outlets"], out["timing"],
                                     [f["key"] for f in out["uniqueHere"]],
                                     (m.get("headline") or "")[:56]))
        share = rendered / members_total if members_total else 0.0
        print("\n== L0 reach ==")
        print(f"  renders a comparison   : {rendered:,} / {members_total:,} ({share:.1%})")
        print("  refusals by reason     :")
        for r, n in reasons.most_common():
            print(f"     {str(r):20} {n:>7,}  ({n / max(1, members_total):.1%})")
        print("  findings emitted       :")
        for k, n in findings.most_common(10):
            print(f"     {k:24} {n:>7,}")
        print("  'unique to this article':")
        for k, n in unique_kinds.most_common(10):
            print(f"     {k:24} {n:>7,}")
        if examples:
            print("  samples (article with at least one unique finding):")
            for pub, outlets, timing, keys, head in examples:
                pos = f"{timing['position']}/{timing['of']}" if timing else "-"
                print(f"     {str(pub)[:18]:18} outlets={outlets:>3} pos={pos:>6} {keys} {head}")

    # ---- 2. Readiness for the text tiers ----------------------------------------------------
    print("\n== L1-L3 readiness (design §2: measure before building) ==")
    urls = {_canon(m) for s in stories for m in (s.get("coverage") or []) if m.get("url")}
    info = _bodies(st, urls)
    if not info:
        print("  no member rows resolved — cannot assess readiness")
        return 0
    with_body = [v for v in info.values() if v["body"] > 0]
    bodies = sorted(v["body"] for v in with_body)
    descs = sorted(v["desc"] for v in info.values())
    print(f"  members resolved       : {len(info):,}")
    print(f"  with a body            : {len(with_body):,} ({len(with_body) / len(info):.1%})")
    if bodies:
        print(f"  body length            : p10 {bodies[len(bodies) // 10]:,}  "
              f"median {statistics.median(bodies):,.0f}  p90 {bodies[len(bodies) * 9 // 10]:,}")
    print(f"  description length     : median {statistics.median(descs):,.0f} chars")

    comparable, multilingual, trusted = 0, 0, 0
    for s in stories:
        mem = [m for m in (s.get("coverage") or []) if m.get("url")]
        pubs = len({m.get("publisher") for m in mem if m.get("publisher")})
        if pubs < cc.min_publishers() or (s.get("clusterTrust") or "ok") == "low":
            continue
        trusted += 1
        bodied = [m for m in mem if info.get(_canon(m), {}).get("body", 0) >= 400]
        if len(bodied) >= 3:
            comparable += 1
        langs = {info.get(_canon(m), {}).get("lang") for m in mem}
        langs.discard("")
        if len(langs) > 1:
            multilingual += 1
    print(f"  clusters past L0 gates : {trusted:,}")
    print(f"  …with >=3 bodied (400+) members  : {comparable:,} "
          f"({comparable / max(1, trusted):.1%})  <- the L2/L3 addressable set")
    print(f"  …multilingual                    : {multilingual:,} "
          f"({multilingual / max(1, trusted):.1%})")
    print("\n  Read: if the bodied share is small, L1 (title+description terms) is the only text")
    print("  tier the catalog can support today, and L2/L3 should wait for richer ingestion.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
