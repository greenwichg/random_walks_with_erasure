#!/usr/bin/env python3
"""Freshness shadow (C4.2) — before/after candidacy diff for the URL-date signal. READ-ONLY.

For every catalog article it computes candidacy **without** the URL-date signal (the pre-fix gate:
``publishedAt`` → first-seen ``createdAt`` → ``fetchedAt``) and **with** it, under the same window and
the same ``now``, then reports exactly which articles change:

* ``EXCLUDED`` — kept before, dropped now: the fix's target (archived URL-dated pages the old gate
  let through because the feed left them undated or re-dated them recent).
* ``RESCUED``  — dropped before, kept now: a genuinely-recent URL whose feed handed a wrong old date.

It never writes, never rebuilds a corpus, never touches ranking — it only re-runs the shared
:func:`corpus_health.fresh_articles` filter twice and diffs the two candidate sets.

    python examples/freshness_shadow.py               # live RWE_DB_URL / repo DB
    python examples/freshness_shadow.py --db sqlite:///beta.db
    python examples/freshness_shadow.py --demo        # built-in representative sample (deterministic)
    python examples/freshness_shadow.py --window 60   # override the age window (days)
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # import sibling modules
import corpus_health as ch


# A representative, deterministic sample mirroring the five required scenarios plus the two archived
# CNN URLs reported in the field (docs/FRESHNESS_ROOT_CAUSE_AUDIT.md). "now" is fixed for --demo so
# the output is byte-stable.
_DEMO_NOW = datetime(2026, 7, 15, 12, 0, 0, tzinfo=timezone.utc)


def _demo_articles() -> list:
    def art(url, *, pub=None, created=0.0, fetched=0.0):
        def iso(d):
            return (_DEMO_NOW - timedelta(days=d)).isoformat()
        return {"canonicalUrl": url, "url": url, "publisher": "Demo",
                "scored": {"outlet": "Demo", "lean": 0.0, "political": True},
                "publishedAt": iso(pub) if pub is not None else None,
                "createdAt": iso(created), "fetchedAt": iso(fetched)}
    return [
        # scenario                                    url                                                          feed date
        art("https://edition.cnn.com/2023/04/18/opinions/2024-presidential-election-alternative-voters-lieberman", pub=None),   # archived opinion, UNDATED -> today's createdAt
        art("https://edition.cnn.com/europe/live-news/russia-ukraine-war-news-04-18-23/index.html", pub=1),                      # archived live blog, RE-DATED recent
        art("https://ex.com/explainer/how-primaries-work", pub=None, created=1),                                                # undated RSS, no URL date (evergreen)
        art("https://ex.com/guides/media-literacy", pub=None, created=3),                                                       # dateless evergreen
        art("https://edition.cnn.com/2026/07/10/politics/new-story/index.html", pub=5),                                         # newly published (current URL date)
        art("https://ex.com/plain/ancient-but-honest", pub=90),                                                                 # plainly stale, correctly dated (already excluded both ways)
    ]


def _reason(a: dict) -> str:
    d = ch._url_date(a.get("canonicalUrl") or "") or ch._url_date(a.get("url") or "")
    if d is not None:
        return f"url-date {d.date().isoformat()}"
    dt = ch._published(a, ch._CANDIDACY_TIME_KEYS)
    return f"fallback   {dt.date().isoformat()}" if dt else "no date signal"


def shadow(articles: list, *, now: datetime, window: float) -> dict:
    """Diff the two candidate sets. Pure; returns the excluded/rescued/unchanged canonical URLs."""
    old = {a["canonicalUrl"] for a in ch.fresh_articles(articles, now=now, max_age_days=window,
                                                         url_date=False)}
    new = {a["canonicalUrl"] for a in ch.fresh_articles(articles, now=now, max_age_days=window,
                                                         url_date=True)}
    return {"total": len(articles), "old_kept": old, "new_kept": new,
            "excluded": sorted(old - new), "rescued": sorted(new - old)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=None, help="database URL (default: RWE_DB_URL or the repo file)")
    ap.add_argument("--demo", action="store_true", help="use the built-in representative sample")
    ap.add_argument("--window", type=float, default=None,
                    help="age window in days (default: RWE_FEED_MAX_AGE_DAYS / 60)")
    args = ap.parse_args()

    if args.demo:
        articles, now, src = _demo_articles(), _DEMO_NOW, "built-in demo sample"
    else:
        import store as store_mod
        st = store_mod.Store(args.db)
        articles, now, src = st.list_feed_articles(limit=10_000_000), datetime.now(timezone.utc), st.url
    window = args.window if args.window is not None else (ch.feed_max_age_days() or 60.0)

    by_url = {a["canonicalUrl"]: a for a in articles}
    res = shadow(articles, now=now, window=window)

    print(f"freshness shadow (C4.2 URL-date signal): {src}")
    print(f"  articles={res['total']}  window={window:g}d  now={now.date().isoformat()}")
    print(f"  candidates: before={len(res['old_kept'])}  after={len(res['new_kept'])}  "
          f"EXCLUDED={len(res['excluded'])}  RESCUED={len(res['rescued'])}")
    for label, urls in (("EXCLUDED (kept before, dropped now)", res["excluded"]),
                        ("RESCUED  (dropped before, kept now)", res["rescued"])):
        if urls:
            print(f"\n  {label}:")
            for u in urls:
                print(f"    - {u}\n        {_reason(by_url[u])}")
    if not res["excluded"] and not res["rescued"]:
        print("\n  no candidacy changes (no URL carried a date-provenance mismatch)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
