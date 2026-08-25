"""audit_outlet_resolution.py — what a URL fallback in outlet resolution would buy, and cost.

Read-only. No network, no writes, no curation.

## The gap

``ingest.Scorer._resolve_outlet`` resolves like this::

    out = self.registry.resolve(raw.outlet or raw.url)

``raw.outlet or raw.url`` consults the URL only when the outlet name is **absent** — never when it
is present and *fails*. Every ingestion adapter supplies a publisher name, so in practice the URL
is never tried: a name the registry does not know falls straight through to ``(name, NaN)`` and the
article is unrated, even when its host is already in the registry's domain index.

Measured on the live catalog 2026-08-25: **431 outlet identities carrying 1,615 articles (5.8% of
the window) sit on a host a tracked outlet already owns.** They have no lean, no factuality, no
country and no scope, and no amount of curation would change that — the rows already exist.

The candidate is one line::

    out = self.registry.resolve(raw.outlet) or self.registry.resolve(raw.url)

Name-first ordering is PRESERVED, and that is what makes it safe rather than clever: an AP story
republished on cnn.com resolves to AP by name today and still would. The fallback fires only where
resolution currently gives up.

## What this instrument measures

Attribution is **strictly additive** — an article can gain an outlet, never change or lose one
(asserted below, not assumed). So the benefit is easy. The COST is the part that needs measuring,
and it is not obvious:

**Re-attribution can destroy stories.** ``min_publishers`` counts DISTINCT publishers. If two
unresolved name-forms in one story both resolve to the same canonical outlet, a 2-article /
2-publisher story becomes 2-article / 1-publisher and is dropped entirely. That is the same shape
as the research/forum removal, which cost 24 news articles their coverage to remove six.

So the run reports both sides:

* articles gaining attribution, split by whether the lean is RATED or locality-only NaN — only a
  rated one can vote, so only a rated one can move a blindspot claim;
* which outlets the newly-attributed articles land on, with the name strings that failed, so
  **mis-attribution is read rather than assumed**;
* stories, largest cluster, covered articles, and **blindspot claims** before and after;
* stories lost and news articles that lost their story — the ``min_publishers`` collapse.

    python examples/audit_outlet_resolution.py --db "$RWE_DB_URL"
"""

from __future__ import annotations

import argparse
import copy
import math
import os
from collections import defaultdict

import audit_clustering_change as ach
import outlet_registry
import story_service
import store as store_mod


def current_outlet(reg, publisher: str, url: str):
    """Exactly ``ingest.Scorer._resolve_outlet``'s rule — the URL only when the name is ABSENT."""
    return reg.resolve(publisher or url)


def candidate_outlet(reg, publisher: str, url: str):
    """Name first, URL as a FALLBACK when the name fails to resolve."""
    return (reg.resolve(publisher) if publisher else None) or (reg.resolve(url) if url else None)


def _lean_or_none(outlet):
    """A lean the row can carry. NaN (locality-only) becomes None, matching what the store holds
    and what ``discover._num_or_none`` would produce — a locality-only outlet is identified but
    still unrated, so it gains a NAME and no vote."""
    if outlet is None:
        return None
    return None if math.isnan(outlet.lean) else float(outlet.lean)


def reattribute(rows: list, reg) -> "tuple[list, list]":
    """``(patched_rows, changes)``. Each change is
    ``(publisher_string, host, new_canonical, rated)``."""
    out, changes = [], []
    for r in rows:
        pub = (r.get("publisher") or "").strip()
        url = r.get("canonicalUrl") or r.get("url") or ""
        cur = current_outlet(reg, pub, url)
        cand = candidate_outlet(reg, pub, url)
        if cur is not None or cand is None:
            out.append(r)                       # unchanged: already resolved, or still unknown
            continue
        # Strictly additive by construction — assert it rather than trust the reasoning.
        assert cur is None, "resolution must never CHANGE an already-resolved outlet"
        patched = copy.deepcopy(r)
        patched["publisher"] = cand.canonical
        scored = dict(patched.get("scored") or {})
        scored["lean"] = _lean_or_none(cand)
        scored["outlet"] = cand.canonical
        patched["scored"] = scored
        out.append(patched)
        changes.append((pub, outlet_registry._host_of(url), cand.canonical,
                        not math.isnan(cand.lean)))
    return out, changes


def _claims(stories: list) -> "tuple[int, int]":
    """``(asserted, withheld)``. The story dict's field is ``blindspotSide`` — a probe keyed on a
    plausible-looking ``blindspot`` returns 0 on BOTH sides and reports a real effect as no
    effect, which is the failure mode this whole audit series keeps finding in its instruments."""
    return (sum(1 for s in stories if s.get("blindspotSide")),
            sum(1 for s in stories if s.get("blindspotWithheld")))


def _rated_members(stories: list) -> int:
    return sum(1 for s in stories for c in s["coverage"] if c.get("leanBucket"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=os.environ.get("RWE_DB_URL"))
    ap.add_argument("--show", type=int, default=20, help="newly-attributed outlets to list")
    args = ap.parse_args(argv)

    st = store_mod.Store(args.db)
    reg = outlet_registry.default_registry()
    rows = story_service._fetch(st)
    patched, changes = reattribute(rows, reg)

    rated = [c for c in changes if c[3]]
    print(f"window                 : {len(rows):,} articles")
    print(f"articles gaining an outlet: {len(changes):,} "
          f"({len(changes) / max(1, len(rows)) * 100:.1f}% of the window)")
    print(f"  of which RATED (can vote): {len(rated):,}")
    print(f"  locality-only (named, still unrated): {len(changes) - len(rated):,}")

    by_target = defaultdict(lambda: [0, set()])
    for pub, host, canon, is_rated in changes:
        by_target[canon][0] += 1
        by_target[canon][1].add(f"{pub or '(no name)'} @ {host}")
    print(f"\n=== where the newly-attributed articles land (top {args.show}) ===")
    print("    READ THIS: a host shared by many publishers is where mis-attribution would happen.")
    print(f"  {'arts':>6}  outlet  <- failing name @ host")
    for canon, (n, names) in sorted(by_target.items(), key=lambda kv: -kv[1][0])[:args.show]:
        print(f"  {n:>6}  {canon[:34]:<34} <- {sorted(names)[0][:58]}")
        for extra in sorted(names)[1:3]:
            print(f"  {'':>6}  {'':<34}    {extra[:58]}")

    base = story_service.build_stories(rows)
    after = story_service.build_stories(patched)
    mb, ma = ach.index_by_member(base), ach.index_by_member(after)
    cov_b = sum(len(s["coverage"]) for s in base)
    cov_a = sum(len(s["coverage"]) for s in after)

    print("\n=== the cost side: does re-attribution destroy stories? ===")
    print(f"stories                : {len(base):,} -> {len(after):,}")
    print(f"largest cluster        : {max((len(s['coverage']) for s in base), default=0)} -> "
          f"{max((len(s['coverage']) for s in after), default=0)}")
    print(f"covered articles       : {cov_b:,} -> {cov_a:,}")
    print(f"articles that LOST their story: {sum(1 for u in mb if u not in ma):,}"
          f"   <- the min_publishers collapse")
    print(f"articles that changed story   : "
          f"{sum(1 for u, s in mb.items() if ma.get(u) and ma[u] != s):,}")

    cb, wb = _claims(base)
    ca, wa = _claims(after)
    print("\n=== the benefit side ===")
    print(f"rated story members    : {_rated_members(base):,} -> {_rated_members(after):,}")
    print(f"BLINDSPOT CLAIMS       : {cb:,} -> {ca:,}"
          f"   <- the metric that decides this")
    print(f"  withheld (low trust) : {wb:,} -> {wa:,}")

    bb, ab = ach._coherence_stats(base), ach._coherence_stats(after)
    print(f"independent signal     : {bb['bad']}/{bb['scored']} bad (mean {bb['mean']}) -> "
          f"{ab['bad']}/{ab['scored']} bad (mean {ab['mean']})")

    print("\n=== ratified exhibits ===")
    for label, truth, b, a in ach._exhibit_outcomes(rows, mb, ma):
        def fmt(v):
            return "not in window" if v is None else ("together" if v else "separated")
        print(f"  {label:<22} {truth:<16} {fmt(b):<14} -> {fmt(a):<14}"
              f"{'' if b == a else '   <-- MOVED'}")

    print("\nNOTE: the production scored-article cache keys on canonical URL, so shipping this")
    print("would re-attribute NEW articles only until the cache is invalidated. This run patches")
    print("the window directly and therefore shows the FULL steady-state effect, not day one.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
