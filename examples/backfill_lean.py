"""backfill_lean.py — re-apply the curated registry lean to articles already in the catalog.

An article's lean is written into its ``scored`` JSON at INGEST time, and ``feed_article_to_article``
reads it from there. So editing ``outlet_registry.csv`` changes nothing about articles that are
already stored: a newly rated outlet keeps casting no vote until its next article arrives, and the
old ones age out of the six-day window instead of being corrected.

That is not a theory. Ratings were added for six outlets and coverage-gap claims moved 61 → 62,
while the audit went on listing ``Dailymail.Com``, ``Winnipegfreepress.Com``, ``Inquirer.Com`` and
``Variety.Com`` as unrated — all of them rated an hour earlier. The registry was right and the
catalog had not heard.

This rewrites the stored lean from the registry, for articles whose outlet resolves to a rated row
and whose stored lean disagrees. It is deliberately narrow:

* **Only the lean fields.** Everything else in ``scored`` — category, register, emotion,
  confidence — is left byte-identical. Those were measured per article; the lean is a property of
  the outlet and is the only thing the registry owns.
* **Never invents a rating.** An outlet that resolves to an unrated row (NaN lean) is skipped, not
  written as centre. An outlet that resolves to nothing is skipped. Absence stays absence (L2.2).
* **Idempotent.** Re-running changes nothing once applied, so it is safe on a schedule or after
  every curation pass.

    python examples/backfill_lean.py --dry-run          # what would change, and for whom
    python examples/backfill_lean.py                    # apply
"""

from __future__ import annotations

import argparse
import json
import math

import outlet_registry
import store as store_mod


def planned_lean(publisher: str, stored) -> "float | None":
    """The lean this article SHOULD carry, or ``None`` if nothing should change.

    Returns ``None`` both when the registry has no opinion and when it already agrees — the caller
    cannot tell those apart and does not need to, since neither is a write."""
    outlet = outlet_registry.resolve(publisher)
    if outlet is None or math.isnan(outlet.lean):
        return None                                   # unknown or deliberately unrated
    try:
        current = float(stored) if stored is not None else None
    except (TypeError, ValueError):
        current = None
    if current is not None and abs(current - outlet.lean) < 1e-9:
        return None                                   # already correct: idempotent
    return outlet.lean


def plan(rows: list) -> list:
    """``(canonical_url, publisher, old, new)`` for every article whose stored lean is stale."""
    out = []
    for r in rows:
        try:
            scored = json.loads(r["scored"]) if isinstance(r["scored"], str) else dict(r["scored"])
        except (TypeError, ValueError):
            continue
        new = planned_lean(r["publisher"], scored.get("lean"))
        if new is not None:
            out.append((r["canonicalUrl"], r["publisher"], scored.get("lean"), new))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--show", type=int, default=25)
    args = ap.parse_args(argv)

    store_ = store_mod.Store(args.db)
    rows = store_.all_feed_articles_for_lean_backfill()
    changes = plan(rows)

    by_pub: dict = {}
    for _, pub, old, new in changes:
        by_pub.setdefault((pub, old, new), 0)
        by_pub[(pub, old, new)] += 1

    print(f"catalog: {len(rows):,} articles")
    print(f"stale leans: {len(changes):,} across {len({c[1] for c in changes}):,} publishers\n")
    print(f"{'articles':>9} {'was':>6} {'now':>6}  publisher")
    for (pub, old, new), n in sorted(by_pub.items(), key=lambda kv: -kv[1])[:args.show]:
        print(f"{n:>9} {str(old):>6} {new:>6.1f}  {pub}")

    if args.dry_run:
        print("\n(dry run — nothing written)")
        return 0
    written = store_.apply_lean_backfill([(u, new) for u, _, _, new in changes])
    print(f"\nwrote {written:,} articles. Re-run to confirm it reports zero — it is idempotent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
