"""audit_story_geography.py — rank clusters by INCIDENT-location coherence.

Coherence is the share of a story's located members whose incident countries include the story's
consensus country (``story_service._geo_coherence``). It is a member-AGREEMENT measure on the
event dimension only — the publisher's home country never participates, so a US outlet reporting
from India counts as India.

Its practical value turned out to be finding **false merges**, not geography errors. A cluster whose
members were located in a dozen unrelated countries is not a multi-country story; it is several
stories that shared title tokens. Measured in production: a 105-publisher cluster titled "Thune on
Trump's Canada tariffs" spanning CN CU DJ GB IL IR OM PH SA SG US YE — which ``publisherDiversity``
scored 0.53 and called healthy. Both columns are printed here so the two can be compared.

A LEGITIMATE multi-country story scores high: an explainer citing fires in AU/ES/FR/GB/SK/US is
coherent as long as its members agree on the lead country, however many others each adds.

    python examples/audit_story_geography.py                  # worst 25, ≥3 located members
    python examples/audit_story_geography.py --min-located 5 --limit 50
    python examples/audit_story_geography.py --json
"""

from __future__ import annotations

import argparse
import json

import story_service
import store as store_mod


def rank(store_, *, min_located: int = 3) -> list:
    """Stories with enough located members to judge, worst coherence first.

    Stories nobody located are EXCLUDED, not scored zero — no evidence is not incoherence, and
    burying real findings under unlocated noise would make the report useless."""
    rows = []
    for s in story_service.cluster_from_store(store_):
        if s.get("geoCoherence") is None or s.get("locatedMembers", 0) < min_located:
            continue
        rows.append({
            "coherence": s["geoCoherence"],
            "located": s["locatedMembers"],
            "articles": s["totalCoverage"],
            "publishers": s["publisherCount"],
            "diversity": s["publisherDiversity"],
            "consensus": s["countries"],
            "votes": s["countryVotes"],
            "id": s["id"],
            "title": s["title"],
        })
    rows.sort(key=lambda r: (r["coherence"], -r["publishers"]))
    return rows


def summarize(rows: list) -> dict:
    if not rows:
        return {"scored": 0}
    cs = [r["coherence"] for r in rows]
    return {
        "scored": len(rows),
        "meanCoherence": round(sum(cs) / len(cs), 3),
        "below0_5": sum(1 for c in cs if c < 0.5),
        "below0_3": sum(1 for c in cs if c < 0.3),
        "perfect": sum(1 for c in cs if c >= 0.999),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=None, help="RWE_DB_URL override")
    ap.add_argument("--min-located", type=int, default=3,
                    help="ignore stories with fewer located members (default 3)")
    ap.add_argument("--limit", type=int, default=25, help="rows to print (default 25)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args(argv)

    rows = rank(store_mod.Store(args.db), min_located=args.min_located)
    stats = summarize(rows)
    if args.json:
        print(json.dumps({"summary": stats, "stories": rows[: args.limit]}, indent=2, default=str))
        return 0

    print(f"scored {stats.get('scored', 0)} stories (>= {args.min_located} located members)")
    if not rows:
        print("nothing to score — incident locations are too sparse in this window")
        return 0
    print(f"  mean coherence {stats['meanCoherence']}  |  <0.5: {stats['below0_5']}  "
          f"<0.3: {stats['below0_3']}  |  perfect: {stats['perfect']}")
    print(f"\n{'coh':>5} {'div':>5} {'loc':>4} {'arts':>5} {'pubs':>5}  consensus  title")
    for r in rows[: args.limit]:
        votes = ",".join(f"{c}:{n}" for c, n in list(r["votes"].items())[:6])
        print(f"{r['coherence']:>5.2f} {r['diversity']:>5.2f} {r['located']:>4} "
              f"{r['articles']:>5} {r['publishers']:>5}  {','.join(r['consensus']):<9}  "
              f"{r['title'][:58]}")
        print(f"{'':>27}votes: {votes}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
