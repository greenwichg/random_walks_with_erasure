#!/usr/bin/env python3
"""One-shot topic reclassification — re-run every stored article through the canonical classifier.

Commit 3 made ``ingest.classify_topic`` the ONE place a topic is ever assigned. Articles scored
before it existed froze their category at first scoring (the scored-article cache is per canonical
URL), so a plainly political story ingested under a generic feed label can still carry ``""`` — or
a junk label — in ``scored_articles``, ``reads``, and ``feed_articles``. This migration rewrites
those stored ``scored`` documents in place, immediately, instead of waiting for a lazy re-ingest.

For every row the stored category is treated as a source-category *hint* (a canonical label is
idempotent; a junk label falls through), joined with the catalog's title/description for the same
canonical URL, and reclassified. The ``political`` flag is re-derived the same way the scorer does
— but it is never downgraded: an article already marked political stays political.

Deterministic, offline, idempotent (a second run changes nothing).

    python examples/migrate_topics.py             # migrate RWE_DB_URL / the default repo DB
    python examples/migrate_topics.py --dry-run   # report what would change; write nothing
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter

from sqlalchemy import select

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # import sibling modules
import ingest
import store as store_mod


def _reclassify(scored: dict, *, url: str, title: str = "", description: str = "") -> "tuple[dict, bool]":
    """Reclassify one stored ``scored`` document. Returns ``(new_scored, changed)`` — the input
    dict is never mutated. The old category is passed as the source-category hint; ``political``
    only ever ratchets upward (True stays True)."""
    old_cat = str(scored.get("category") or "")
    new_cat = ingest.classify_topic(url=url, source_category=old_cat,
                                    title=title or str(scored.get("title") or ""),
                                    description=description)
    old_pol = scored.get("political")
    new_pol = bool(old_pol) or ingest.looks_political(url, new_cat)
    if new_cat == old_cat and bool(old_pol) == new_pol and old_pol is not None:
        return scored, False
    out = dict(scored)
    out["category"] = new_cat
    out["political"] = new_pol
    return out, True


def _catalog_context(st: "store_mod.Store") -> dict:
    """Title/description/original-URL context per canonical URL from the catalog — the richest
    text available for reclassifying cache/read rows that carry only the scored JSON."""
    with st.session() as s:
        return {r.canonical_url: (r.title or "", r.description or "", r.url or "")
                for r in s.scalars(select(store_mod.FeedArticle)).all()}


def migrate(st: "store_mod.Store", dry_run: bool = False) -> dict:
    """Reclassify ``scored_articles``, ``reads``, and ``feed_articles`` through
    ``ingest.classify_topic``. Returns per-table stats: row/changed counts and the before/after
    category distributions. ``dry_run=True`` computes the same stats but writes nothing."""
    extra = _catalog_context(st)
    stats: dict = {}

    def _run(table_name, rows, key_of, text_of, apply_change):
        before, after = Counter(), Counter()
        changed = 0
        for r in rows:
            scored = dict(json.loads(r.scored))
            url = key_of(r)
            title, desc, real_url = extra.get(url, ("", "", ""))
            t_override, d_override = text_of(r)
            new, ch = _reclassify(scored, url=(real_url or url),
                                  title=(t_override or title),
                                  description=(d_override or desc))
            before[str(scored.get("category") or "")] += 1
            after[str(new.get("category") or "")] += 1
            if ch:
                changed += 1
                if not dry_run:
                    apply_change(r, new)
        stats[table_name] = {"rows": len(rows), "changed": changed,
                             "before": dict(before), "after": dict(after)}

    with st.session() as s:
        _run("scored_articles",
             s.scalars(select(store_mod.ScoredArticle)).all(),
             key_of=lambda r: r.url,
             text_of=lambda r: ("", ""),
             apply_change=lambda r, new: setattr(r, "scored", store_mod._dumps_scored(new)))
    with st.session() as s:
        _run("reads",
             s.scalars(select(store_mod.Read)).all(),
             key_of=lambda r: r.canonical_url,
             text_of=lambda r: ("", ""),
             apply_change=lambda r, new: setattr(r, "scored", store_mod._dumps_scored(new)))
    with st.session() as s:
        _run("feed_articles",
             s.scalars(select(store_mod.FeedArticle)).all(),
             key_of=lambda r: r.canonical_url,
             text_of=lambda r: (r.title or "", r.description or ""),
             apply_change=lambda r, new: setattr(r, "scored", store_mod._dumps_scored(new)))
    return stats


def _fmt_dist(dist: dict) -> str:
    parts = [f"{(k or '(uncategorized)')}: {v}"
             for k, v in sorted(dist.items(), key=lambda kv: (-kv[1], kv[0]))]
    return ", ".join(parts) or "-"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=None, help="database URL (default: RWE_DB_URL or the repo file)")
    ap.add_argument("--dry-run", action="store_true", help="report changes without writing")
    args = ap.parse_args()

    st = store_mod.Store(args.db)
    stats = migrate(st, dry_run=args.dry_run)
    mode = "DRY RUN — nothing written" if args.dry_run else "migrated"
    print(f"topic reclassification ({mode}): {st.url}")
    for table, t in stats.items():
        print(f"  {table}: {t['changed']}/{t['rows']} rows reclassified")
        print(f"    before: {_fmt_dist(t['before'])}")
        print(f"    after:  {_fmt_dist(t['after'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
