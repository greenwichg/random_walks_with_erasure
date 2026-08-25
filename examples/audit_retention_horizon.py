"""audit_retention_horizon.py — how deep is the searchable archive, and how deep at 10x ingestion?

Read-only. No writes, no deletes, no network. The production side of M2's second half
(`docs/SCALE_ROADMAP.md`, break #2).

## The question

``RWE_RETENTION_MAX_COUNT`` is a **count**, and a count cap is an age cap whose length nobody chose.
150,000 rows is ~32 days at today's ~4,650 articles/day, **one day at 150k/day** and **seven hours
at 500k/day**. `CORPUS_ARCHITECTURE.md` makes ① responsible for being *complete and findable*, so
the same unchanged setting quietly turns the searchable archive into a few hours as source coverage
grows — and nothing about it looks different from the outside. Ingestion goes up, depth goes down,
no error is raised.

This prints the horizon the current policy actually buys, at the measured rate and at multiples of
it, so the number is chosen rather than inherited. It also prints the tier split, which is what a
per-tier age rule would act on.

**It deletes nothing and recommends nothing automatically.** Retention is the one path in this
system that destroys data, so the instrument that reasons about it reports and stops.

    dc run --rm -T api python examples/audit_retention_horizon.py --db "$RWE_DB_URL"
"""

from __future__ import annotations

import argparse
import os
from collections import Counter

import capacity_report
import clustering
import corpus
import retention_policy
import store as store_mod

#: Ingestion multiples to project. 1x is measured; the rest are the roadmap's scenarios — 150k/day
#: is 50,000 sources at a conservative 3 items/day, 500k/day at 10.
MULTIPLES = (1, 2, 5, 10, 50, 100)


def _fmt_days(d: float) -> str:
    if d >= 2:
        return f"{d:,.1f} days"
    return f"{d * 24:,.1f} hours"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=os.environ.get("RWE_DB_URL"))
    ap.add_argument("--window", type=int, default=14, help="days of history for the rate estimate")
    args = ap.parse_args(argv)

    st = store_mod.Store(args.db)
    policy = retention_policy.load()
    with st.session() as s:
        rate = capacity_report.ingestion_rate(s, days=args.window)
    catalog = st.count_feed_articles()

    mean = rate.get("meanPerDay") or 0.0
    print("=== ingestion [M] ===")
    print(f"  catalog            : {catalog:,} rows")
    if not mean:
        print("  rate               : UNMEASURABLE (no rows in the window) — nothing below is derivable")
        return 1
    print(f"  measured rate      : {mean:,.0f} articles/day "
          f"({rate.get('fullDays')} full days of a {args.window}-day window)")
    if not rate.get("reliable"):
        print("  *** the first and last buckets are PARTIAL days and could not be trimmed, so this")
        print("      mean is diluted and every horizon below is optimistic.")

    print("\n=== the policy in force [M] ===")
    cap = f"{policy.article_max_count:,} rows" if policy.article_max_count else "off"
    print(f"  {'RWE_RETENTION_MAX_COUNT':<34} : {cap}")
    for label, value in (("RWE_RETENTION_MAX_AGE_DAYS", policy.article_max_age_days),
                         ("  ...        _TIER_B", policy.article_max_age_days_tier_b),
                         ("  ...        _SHADOW", policy.article_max_age_days_shadow)):
        print(f"  {label:<34} : {str(value) + ' days' if value else 'off'}")

    if not policy.article_max_count:
        print("\n  No count cap is set, so there is no count-shaped horizon to project. The catalog")
        print("  grows until the disk decides — see examples/capacity_report.py for that side.")
    else:
        print("\n=== what the COUNT cap actually buys [D] ===")
        print("  A count cap is an age cap whose length nobody chose. The SAME setting means:")
        print(f"\n  {'ingestion':>10}  {'articles/day':>13}   {policy.article_max_count:,} rows is...")
        floor = clustering.DEFAULT_WINDOW_DAYS
        for m in MULTIPLES:
            per_day = mean * m
            days = policy.article_max_count / per_day
            flag = ""
            if days < floor:
                flag = f"   <- SHALLOWER than the {floor:g}-day clustering window"
            elif days < floor * 2:
                flag = "   <- within 2x of the clustering window"
            print(f"  {str(m) + 'x':>10}  {per_day:>13,.0f}   {_fmt_days(days):>12}{flag}")

        equiv = policy.article_max_count / mean
        print(f"\n  An age policy of ~{equiv:,.0f} days is what the cap buys TODAY, and unlike the cap")
        print(f"  it does not move when ingestion does. That is the whole argument for age over count:")
        print(f"  the number an operator sets stays the number the readers get.")

    # ---------------------------------------------------------------- tiers
    print("\n=== the tier split [M] — what a per-tier age rule would act on ===")
    if not corpus.enabled():
        print("  Tiering is not configured, so every row is Tier A and a per-tier age has nothing")
        print("  to separate. RWE_RETENTION_MAX_AGE_DAYS_TIER_B would prune nothing today.")
    else:
        counts: Counter = Counter()
        for a in st.list_feed_articles(limit=10_000_000):
            counts[corpus.tier_of(a.get("publisher"), a.get("canonicalUrl") or a.get("url"))] += 1
        for tier in corpus.TIERS:
            n = counts.get(tier, 0)
            print(f"  tier {tier:<7}: {n:>9,}  ({n / max(1, catalog) * 100:5.1f}% of the catalog)"
                  f"   age rule: {policy.age_days_for_tier(tier) or 'off'}")

    print("\nNOTE: retention DELETES. Nothing here changed a setting or removed a row, and the")
    print("floors in corpus_health (per-bucket, publishers, fresh, total) still hold whatever age")
    print("policy is configured — a prune can never breach them, per-tier or global.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
