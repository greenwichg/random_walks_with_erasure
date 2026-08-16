"""audit_story_summary.py — what the story summary actually serves, measured before any fix.

READ-ONLY instrument (Phase 1 of the summary plan). One fetch, one production-parity story
build, no writes, no configuration reads beyond what the build itself does. It measures the
REGISTERED criteria for the summary defect — the served summary is ``rep["description"]``
verbatim (story_service._build_story), the representative is the earliest filer, and one
provider (Google News) stores a coverage DIGEST as its description — so every number here is a
baseline the adoption bars get set against, before ``pick_story_summary`` exists.

Registered criteria measured:

  * digest-rate      — summaries naming >= 2 OTHER cluster members' publishers (the GN digest
                       signature, detected from the story's own evidence; word-boundary matched,
                       publishers with names < 4 chars skipped — a substring match on "Time"
                       would convict the word "sometimes").
  * echo-rate        — >= 80% of the story title's tokens (len >= 3) appear in the summary's
                       first 160 chars, with at least 4 such tokens — the dek that restates the
                       headline.
  * url-leak count   — bare domains in the summary text.
  * fallback rate    — stories serving the counted fallback (recomputed: the representative's
                       description is empty; never string-matched against the fallback copy).
  * length           — distribution of served summary lengths (the digest class lives far above
                       the readable band).
  * determinism      — the same rows build to the same summaries, twice.
  * provenance       — which ingestion source supplied each served summary (the GN share is the
                       receipt for the ingest-side half of the plan).

Plus one rule-free bar-setting fact: the per-story CANDIDATE POOL (members with any
description at all) — the ceiling any selection rule can reach, reported so the fallback-rate
bar is set against reality rather than hope.

**Story members are resolved through a URL index under BOTH url forms** (coverage carries
display urls and no media/source fields; rows are keyed canonically). That join has now cost
this repo multiple instruments — see audit_story_hero.py — and is inherited here deliberately.

Post-adoption note: provenance/fallback attribute the WINNER of the shipped rule
(story_service._pick_summary replayed on the same serialized articles); the registered
criteria still judge the served summary text, so baseline and post-fix runs compare
one-to-one.
"""

from __future__ import annotations

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import discover              # noqa: E402
import story_service         # noqa: E402
import store as store_mod    # noqa: E402

# The detectors are the ONE shared implementation in story_service — the instrument measures
# with the exact functions the shipped rule judges with, so the two can never drift. (They
# moved there when pick_story_summary was adopted; the registered metrics are unchanged.)
_URL_RE = story_service._SUMMARY_URL_RE
digest_hits = story_service.summary_digest_hits
echo_share = story_service.summary_echo_share


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=None)
    ap.add_argument("--examples", type=int, default=5, help="exhibits per worst-case class")
    args = ap.parse_args(argv)

    st = store_mod.Store(args.db)
    rows = story_service._fetch(st)
    ents = story_service._entities_for(st, rows)
    stories = story_service.build_stories(rows, entities=ents)

    # determinism: the registered criterion, measured directly
    again = story_service.build_stories(rows, entities=ents)
    deterministic = [s["summary"] for s in stories] == [s["summary"] for s in again]

    by_url: dict = {}
    for r in rows:
        for key in (r.get("canonicalUrl"), r.get("url")):
            if key and key not in by_url:
                by_url[key] = r

    def members_of(story) -> list:
        seen, out = set(), []
        for c in story["coverage"]:
            r = by_url.get(c.get("url"))
            if r is not None and id(r) not in seen:
                seen.add(id(r))
                out.append(r)
        return out

    n = len(stories)
    fallback = 0
    src_hist: dict = {}
    digest = []          # (hits, story)
    echo = []            # (share, story)
    leaks = []           # (matches, story)
    lengths = []
    pool_hist = {0: 0, 1: 0, "2-4": 0, "5+": 0}

    for s in stories:
        members = members_of(s)
        summary = s.get("summary") or ""
        lengths.append(len(summary))

        with_desc = sum(1 for m in members if (m.get("description") or "").strip())
        pool_hist[0 if with_desc == 0 else 1 if with_desc == 1 else
                  "2-4" if with_desc <= 4 else "5+"] += 1

        # Replay the SERVED selection (story_service._pick_summary — the same rows, the same
        # article serialization, the same rule) to attribute the winner. Before the rule
        # shipped this instrument recomputed the representative instead; the registered
        # metrics below are unchanged either way — they judge the served summary text.
        arts = [discover.feed_article_to_article(m) for m in members]
        rep = min(arts, key=lambda a: (a.get("publishedAt") or "~", a.get("id") or "")) \
            if arts else None
        picked, winner = story_service._pick_summary(arts, rep) if arts else ("", None)
        if not picked:
            fallback += 1
            src_hist["<fallback>"] = src_hist.get("<fallback>", 0) + 1
            continue   # the counted fallback is clean by construction; criteria below judge deks

        src = (winner.get("sourceType") or "unknown") if winner else "unknown"
        src_hist[src] = src_hist.get(src, 0) + 1

        pubs = {a.get("publisher") for a in arts if a.get("publisher")}
        others = pubs - {winner.get("publisher")} if winner else pubs
        hits = digest_hits(summary, others)
        if len(hits) >= 2:
            digest.append((hits, s))
        share = echo_share(s.get("title") or "", summary)
        if share >= 0.8:
            echo.append((share, s))
        urls = _URL_RE.findall(summary)
        if urls:
            leaks.append((urls, s))

    served = n - fallback
    lengths.sort()
    pct = lambda p: lengths[min(len(lengths) - 1, int(p * len(lengths)))] if lengths else 0
    in_band = sum(1 for x in lengths if 60 <= x <= 320)
    over = sum(1 for x in lengths if x > 500)

    print(f"window articles      : {len(rows):,}")
    print(f"stories              : {n:,}  (dek-served: {served:,}; counted fallback: {fallback:,}"
          f" = {fallback / max(1, n):.1%})")

    print(f"\n-- 1. summary provenance (ingestion source of the serving description) --")
    for k, v in sorted(src_hist.items(), key=lambda kv: -kv[1]):
        print(f"  {k:<12} {v:>5,}  ({v / max(1, n):.1%})")

    print(f"\n-- 2. registered criteria --")
    print(f"  digest-rate  : {len(digest):,} / {n:,} ({len(digest) / max(1, n):.1%})"
          f"  — summaries naming >=2 other member publishers")
    print(f"  echo-rate    : {len(echo):,} / {n:,} ({len(echo) / max(1, n):.1%})"
          f"  — >=80% of title tokens in the summary head")
    print(f"  url-leaks    : {len(leaks):,} summaries containing bare domains")
    print(f"  fallback     : {fallback:,} ({fallback / max(1, n):.1%})")
    print(f"  length       : p10 {pct(0.10)}  p50 {pct(0.50)}  p90 {pct(0.90)} chars; "
          f"in 60-320: {in_band / max(1, n):.1%}; over 500: {over / max(1, n):.1%}")
    print(f"  determinism  : {'PASS — identical summaries on a second build' if deterministic else 'FAIL'}")

    print(f"\n-- 3. candidate pool (rule-free: members with ANY description, per story) --")
    for k in (0, 1, "2-4", "5+"):
        v = pool_hist[k]
        print(f"  {k!s:>4} deks: {v:>5,}  ({v / max(1, n):.1%})")

    def show(title, items, fmt):
        print(f"\n-- worst: {title} --")
        for item in items[: args.examples]:
            fmt(item)

    digest.sort(key=lambda t: -len(t[0]))
    show("digest-shaped summaries (most other-publisher names)", digest, lambda t: (
        print(f"  [{len(t[0])} pubs named: {', '.join(t[0][:4])}]  {t[1]['title'][:64]}"),
        print(f"    {t[1]['summary'][:180]}")))
    echo.sort(key=lambda t: -t[0])
    show("headline-echo summaries", echo, lambda t: (
        print(f"  [{t[0]:.0%} overlap]  {t[1]['title'][:64]}"),
        print(f"    {t[1]['summary'][:150]}")))
    show("url leaks", leaks, lambda t: (
        print(f"  [{', '.join(t[0][:3])}]  {t[1]['title'][:64]}"),
        print(f"    {t[1]['summary'][:150]}")))
    longest = sorted(stories, key=lambda s: -len(s.get("summary") or ""))
    show("longest summaries", longest, lambda s: (
        print(f"  [{len(s.get('summary') or '')} chars]  {s['title'][:64]}"),
        print(f"    {(s.get('summary') or '')[:180]}")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
