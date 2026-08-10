"""audit_factuality_coverage.py — how much of the feed carries a factuality verdict, and what
sourcing the rest would be worth. READ-ONLY.

Phase 0 of the factuality work: the one number the rest of the plan depends on. It answers a
different question from ``audit_registry_coverage.py``, which is about LEAN and about coverage-gap
unlocks. Factuality unlocks nothing — no blindspot claim rests on it — so ranking by unlocks would
report zero for everything and say nothing. What matters here is how much of what a reader actually
sees could carry a label, so the weight is ARTICLE VOLUME.

Counted **per outlet identity**, never per name string, via the same ``publisher_identity`` grouping
the pipeline uses — otherwise one masthead arriving as ``Yahoo.Com``, ``Finance.Yahoo.Com`` and
``Yahoo! News`` is three rows and the percentages are wrong. That bug is why the sibling audit
exists in the shape it does.

Three totals travel together because they are easy to confuse and the differences are the point:

* **registry** — a property of the FILE. How many curated rows carry a ``credibility`` value.
* **window** — a property of the FEED. What share of articles come from an outlet that has one.
* **free** — already sourced, not yet written. During the lean tranches the rater's factuality
  verdict was read off the same MBFC page and recorded in the registry's own comments, but never
  entered in the column. Those need no new research and no rater access, so they are the honest
  "achievable today" figure and are reported separately from anything requiring a lookup.

Clustering is NOT run by default: factuality is per outlet, so article volume answers the question
without paying for a story build on a production box. ``--stories`` adds the story-level view (how
many stories have at least one publisher with no verdict) for anyone who wants it.

    python examples/audit_factuality_coverage.py
    python examples/audit_factuality_coverage.py --top 40
    python examples/audit_factuality_coverage.py --stories
    python examples/audit_factuality_coverage.py --json

WRITES NOTHING. Every call is a read: ``story_service._fetch`` for the corpus, the registry file,
and ``publisher_identity`` grouping, which are all pure. Safe to run against production.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import pathlib
import re

import outlet_registry
import publisher_identity
import story_service
import store as store_mod

#: MBFC's published ladder, worst to best. The registry's own column is the 3-level
#: ``high|medium|low`` summary; this is the vocabulary the COMMENTS record, and the mismatch
#: between them is a finding rather than a bug to paper over here — this probe reports what it
#: reads and does not map one onto the other.
_MBFC_ORDER = ("very low", "low", "mixed", "mostly factual", "high", "very high")

#: ``#   Outlet Name  : … factual High`` — the shape the lean tranches recorded verdicts in.
_NOTE = re.compile(r"^#\s{2,}([A-Z][^:]{2,40}?)\s*:.*?factual(?:ity)?\s+([A-Za-z ]+)", re.IGNORECASE)


def sourced_but_unwritten(path: "str | None" = None) -> dict:
    """Outlets whose factuality a curator already read at the rater and left in a comment.

    Parsed from the registry file rather than restated here, so the number cannot drift from the
    evidence. Only rows that EXIST and whose ``credibility`` cell is still blank are returned —
    a verdict already in the column is not free work, it is done work."""
    p = pathlib.Path(path or outlet_registry._DATA)
    raw = p.read_text(encoding="utf-8")
    noted = {}
    for line in raw.splitlines():
        m = _NOTE.match(line)
        if m:
            noted[m.group(1).strip()] = m.group(2).strip().rstrip(";,.").strip().lower()

    body = [ln for ln in raw.splitlines() if not ln.lstrip().startswith("#")]
    rows = {r["canonical"]: r for r in csv.DictReader(body) if (r.get("canonical") or "").strip()}
    return {name: verdict for name, verdict in noted.items()
            if name in rows and not (rows[name].get("credibility") or "").strip()}


def analyse(rows: list, *, free: dict, stories: "list | None" = None) -> dict:
    """Per-identity factuality coverage over the article window."""
    names: dict = {}
    for r in rows:
        p = (r.get("publisher") or "").strip()
        if p:
            names[p] = names.get(p, 0) + 1

    # Resolved over the WHOLE window exactly as the pipeline does — whether a bare name may join a
    # domain is a property of the catalog, not of one row.
    keys = publisher_identity.groups(sorted(names))

    groups: dict = {}
    for name, n in names.items():
        g = groups.setdefault(keys[name], {"articles": 0, "forms": {}, "outlet": None})
        g["articles"] += n
        g["forms"][name] = g["forms"].get(name, 0) + n
        if g["outlet"] is None:
            g["outlet"] = outlet_registry.resolve(name)

    out = []
    for key, g in groups.items():
        o = g["outlet"]
        label = max(g["forms"].items(), key=lambda kv: (kv[1], kv[0]))[0]
        cred = (getattr(o, "credibility", None) or "").strip().lower() if o else ""
        canonical = o.canonical if o else None
        out.append({
            "identity": key,
            "label": label,
            "canonical": canonical,
            "articles": g["articles"],
            "factuality": cred or None,
            # Distinguishes the two kinds of blank, which are different work:
            #   registered   — a row exists, only the verdict is missing (a lookup).
            #   unregistered — no row at all (identity curation first, then a lookup).
            "registered": o is not None,
            "hasLean": bool(o is not None and not math.isnan(o.lean)),
            "freeVerdict": free.get(canonical) if canonical else None,
        })

    total_articles = sum(r["articles"] for r in out)
    rated = [r for r in out if r["factuality"]]
    unrated = [r for r in out if not r["factuality"]]
    free_hits = [r for r in unrated if r["freeVerdict"]]

    by_value: dict = {}
    for r in rated:
        b = by_value.setdefault(r["factuality"], {"outlets": 0, "articles": 0})
        b["outlets"] += 1
        b["articles"] += r["articles"]

    registry = outlet_registry.default_registry().outlets()
    res = {
        "registryRows": len(registry),
        "registryWithFactuality": sum(1 for o in registry
                                      if (getattr(o, "credibility", None) or "").strip()),
        "registryWithLean": sum(1 for o in registry if not math.isnan(o.lean)),
        "names": len(names),
        "identities": len(groups),
        "articles": total_articles,
        "ratedOutlets": len(rated),
        "ratedArticles": sum(r["articles"] for r in rated),
        "unratedOutlets": len(unrated),
        "unratedArticles": sum(r["articles"] for r in unrated),
        "freeOutletsInWindow": len(free_hits),
        "freeArticles": sum(r["articles"] for r in free_hits),
        "freeTotalInFile": len(free),
        "byValue": by_value,
        "outlets": out,
    }
    if stories is not None:
        with_gap = 0
        for s in stories:
            pubs = {keys.get(c["publisher"], c["publisher"]) for c in s["coverage"]}
            lookup = {r["identity"]: r["factuality"] for r in out}
            if any(not lookup.get(p) for p in pubs):
                with_gap += 1
        res["stories"] = len(stories)
        res["storiesWithAnUnratedPublisher"] = with_gap
    return res


def _pct(n: int, d: int) -> str:
    return f"{(100.0 * n / d):.1f}%" if d else "—"


def _table(rows: list, top: int) -> str:
    rows = sorted(rows, key=lambda r: (-r["articles"], r["label"]))[:top]
    if not rows:
        return "    (none)"
    w = max(len(r["label"]) for r in rows)
    out = []
    for r in rows:
        tag = "row exists" if r["registered"] else "NO ROW"
        lean = "lean set" if r["hasLean"] else "no lean"
        freev = f"  <- already sourced: {r['freeVerdict']}" if r["freeVerdict"] else ""
        out.append(f"    {r['label']:<{w}}  {r['articles']:>6} articles   {tag}, {lean}{freev}")
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=None)
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--stories", action="store_true",
                    help="also build stories (slower) to report story-level gaps")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    store_ = store_mod.Store(args.db)
    rows = story_service._fetch(store_)
    stories = story_service.build_stories(rows) if args.stories else None
    res = analyse(rows, free=sourced_but_unwritten(), stories=stories)

    if args.json:
        print(json.dumps(res, indent=2, sort_keys=True))
        return 0

    print("FACTUALITY COVERAGE — read-only\n")
    print(f"REGISTRY (the file) : {res['registryRows']:,} rows")
    print(f"  with a factuality : {res['registryWithFactuality']:,}"
          f"  ({_pct(res['registryWithFactuality'], res['registryRows'])})")
    print(f"  with a lean       : {res['registryWithLean']:,}"
          f"  ({_pct(res['registryWithLean'], res['registryRows'])})   <- for contrast\n")

    print(f"WINDOW   (the feed) : {res['articles']:,} articles from "
          f"{res['identities']:,} outlet identities ({res['names']:,} name forms)")
    print(f"  ARTICLES with a factuality verdict : {res['ratedArticles']:,}"
          f"  ({_pct(res['ratedArticles'], res['articles'])})   <- THE BASELINE")
    print(f"  ARTICLES without                   : {res['unratedArticles']:,}"
          f"  ({_pct(res['unratedArticles'], res['articles'])})")
    print(f"  outlets with / without             : {res['ratedOutlets']:,} / {res['unratedOutlets']:,}")
    if "stories" in res:
        print(f"  stories with >=1 unrated publisher : {res['storiesWithAnUnratedPublisher']:,}"
              f" of {res['stories']:,}"
              f"  ({_pct(res['storiesWithAnUnratedPublisher'], res['stories'])})")

    if res["byValue"]:
        print("\n  verdicts present, by article volume:")
        for v in sorted(res["byValue"], key=lambda x: _MBFC_ORDER.index(x)
                        if x in _MBFC_ORDER else -1):
            d = res["byValue"][v]
            print(f"    {v:<16} {d['outlets']:>4} outlets  {d['articles']:>7} articles")

    print(f"\n=== FREE: already sourced at the rater, never written to the column ===")
    print(f"    {res['freeTotalInFile']:,} such verdicts sit in the registry's comments; "
          f"{res['freeOutletsInWindow']:,} of those outlets publish in this window,")
    print(f"    covering {res['freeArticles']:,} articles "
          f"({_pct(res['freeArticles'], res['articles'])} of the feed).")
    print(f"    Writing them needs NO rater access and NO new research.")
    print(f"    Baseline would move "
          f"{_pct(res['ratedArticles'], res['articles'])} -> "
          f"{_pct(res['ratedArticles'] + res['freeArticles'], res['articles'])}.")

    unrated = [r for r in res["outlets"] if not r["factuality"]]
    print(f"\n=== WORKLIST: unrated outlets by ARTICLE VOLUME (top {args.top}) ===")
    print("    'row exists' needs only a lookup; 'NO ROW' needs identity curation first.")
    print(_table(unrated, args.top))

    lookup_only = [r for r in unrated if r["registered"] and not r["freeVerdict"]]
    print(f"\n=== …of which need ONLY a rater lookup (a row already exists) ===")
    print(_table(lookup_only, args.top))
    print(f"\n    {len(lookup_only):,} outlets, {sum(r['articles'] for r in lookup_only):,} articles "
          f"({_pct(sum(r['articles'] for r in lookup_only), res['articles'])} of the feed).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
