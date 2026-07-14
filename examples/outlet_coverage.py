#!/usr/bin/env python3
"""Outlet coverage — a READ-ONLY diagnostic for registry maintenance (W4 observability).

Scans the stored catalog and reports which outlets the registry doesn't know (``scored.lean`` is
``NaN``). Those articles ingest and are searchable, but they never become recommendation candidates
— they are dropped at ``simulate_users.catalog_from_qbias`` and again at
``rwe.mind.recommender_inputs`` (NaN item position). So this tool answers one operational question:
*which unknown outlets, ranked by article volume, should we add to ``outlet_registry.csv`` next to
recover the most recommendable articles?*

It never mutates the store, the registry, or anything else, and it does not touch the recommender,
``evaluate()``, the report contract, or recommendation behaviour — it only reads and counts.

Usage:
    python examples/outlet_coverage.py [--db URL] [--top N] [--json]
    python examples/outlet_coverage.py --lint            # well-formedness check on the registry CSV
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import outlet_registry as orr   # noqa: E402
import store as store_mod       # noqa: E402


def _is_unknown(scored: dict) -> bool:
    """True when the article's outlet has no registry lean — the exact signal the recommender drops
    on (``item_positions`` NaN). Missing / non-numeric lean counts as unknown."""
    try:
        return not math.isfinite(float((scored or {}).get("lean")))
    except (TypeError, ValueError):
        return True


def scan(store) -> dict:
    """Read-only pass over the catalog → coverage summary + unknown outlets ranked by frequency.

    Returns ``{total, known, unknown, unknownPct, registryOutlets, outlets: [{outlet, count,
    example}]}`` where ``outlets`` is descending by ``count`` (ties broken by name, so the output is
    deterministic)."""
    articles = store.list_feed_articles(limit=10_000_000)
    tally: dict = {}                              # outlet label -> {count, example}
    unknown = 0
    for a in articles:
        sc = a.get("scored") or {}
        if not _is_unknown(sc):
            continue
        unknown += 1
        label = (a.get("publisher") or sc.get("outlet") or "(unresolved)").strip() or "(unresolved)"
        rec = tally.setdefault(label, {"outlet": label, "count": 0, "example": a.get("url")})
        rec["count"] += 1
    total = len(articles)
    ranked = sorted(tally.values(), key=lambda r: (-r["count"], r["outlet"]))
    return {"total": total, "known": total - unknown, "unknown": unknown,
            "unknownPct": round(100.0 * unknown / total, 2) if total else 0.0,
            "registryOutlets": len(orr.default_registry()), "outlets": ranked}


def _render(summary: dict, top: int) -> str:
    lines = [
        f"Outlet coverage — {summary['total']} catalog article(s), "
        f"{summary['registryOutlets']} outlets in the registry",
        f"  known outlet (recommendable) : {summary['known']}",
        f"  unknown outlet (excluded)    : {summary['unknown']}  ({summary['unknownPct']}%)",
    ]
    outlets = summary["outlets"]
    if not outlets:
        lines.append("\n  ✓ every catalog article resolves to a known outlet — full coverage.")
        return "\n".join(lines)
    lines.append(f"\n  {summary['unknown']} article(s) are excluded from the recommendation corpus "
                 "due to unresolved outlets.")
    lines.append(f"  Top {min(top, len(outlets))} unknown outlets by article volume "
                 "(add these to outlet_registry.csv first):\n")
    w = max(len(str(o["count"])) for o in outlets[:top])
    for o in outlets[:top]:
        lines.append(f"    {o['count']:>{w}}  {o['outlet']}")
        if o.get("example"):
            lines.append(f"    {'':>{w}}  e.g. {o['example']}")
    return "\n".join(lines)


def _lint(as_json: bool) -> int:
    issues = orr.lint_registry()
    errors = [i for i in issues if i.get("severity") == "error"]
    if as_json:
        print(json.dumps({"issues": issues, "errorCount": len(errors)}, indent=2))
    elif not issues:
        print("registry lint: OK — no issues.")
    else:
        print(f"registry lint: {len(issues)} issue(s) ({len(errors)} error(s)):")
        for i in issues:
            print(f"  [{i['severity']}] {i['code']}: {i['message']}")
    return 1 if errors else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Read-only outlet-coverage diagnostic for registry maintenance.")
    ap.add_argument("--db", default=None, help="database URL (default: RWE_DB_URL or the repo file)")
    ap.add_argument("--top", type=int, default=20, help="how many unknown outlets to list (default 20)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--lint", action="store_true",
                    help="instead of scanning the catalog, lint outlet_registry.csv and exit nonzero on errors")
    args = ap.parse_args(argv)

    if args.lint:
        return _lint(args.json)

    summary = scan(store_mod.Store(args.db))
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print(_render(summary, args.top))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
