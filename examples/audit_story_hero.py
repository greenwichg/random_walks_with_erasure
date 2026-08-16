"""audit_story_hero.py — why story cards show publisher branding instead of a story photo.

READ-ONLY instrument. One fetch, one production-parity story build, no writes, no downloads, no
image inspection of any kind — every number here comes from metadata the catalog already stores.

The defect this measures (``media.pick_story_hero``): the hero is taken from the REPRESENTATIVE
member unconditionally, and the representative is the *earliest-published* article
(``story_service._build_story``). A 29-source story therefore shows whichever image the fastest
filer attached, not the best of 29 — and the fastest filer is systematically the outlet that
republishes wire copy within minutes under a generic house graphic. Nothing anywhere rejects a
branding image, and the fallback ranking that would run if the representative had none is
``_area(width, height)``, which is 0 for every image except ``media:content``/``media:thumbnail``
(no other producer supplies dimensions).

Three questions, in the order the fix depends on them:

1. **PROVENANCE** — where do today's heroes come from? Representative vs. anything else, and
   which ingestion source supplied them. If heroes are overwhelmingly representative-sourced,
   defect 1 is confirmed on production data rather than inferred from code.
2. **REUSE** — which image URLs appear across MANY DIFFERENT STORIES? An image on twenty stories
   is by definition not about any of them. This is the publisher-agnostic branding detector (the
   image analogue of ``ENTITY_MERGE_MAX_STORY_DF``), and this table is what sets its threshold —
   measured, not guessed.
3. **IMPACT** — under a rank-don't-defer hero (L1) plus a reuse reject (L2), how many stories
   change hero, and how many fall back to the imageless coverage-figure card? The fallback is a
   designed state (``story-card.tsx`` draws the distribution in the image slot), so losing a
   branding hero is a fix, not a regression — but the SIZE of that population is a product fact
   and belongs in the decision.

The candidate ranking IS the shipped rule (``media.hero_rank`` / ``media.pick_story_hero(...,
ranked=True)``, adopted 2026-08-16 behind ``RWE_STORY_HERO_GUARD`` — docs/STORY_HERO_IMAGES.md):
the impact section literally executes the production selector at each threshold, so the
instrument cannot drift from what ships. With the guard ON in the measured environment, the
shipped-threshold row (marked) should therefore read ~0 changes — that IS the post-deploy
verification. Nothing here changes clustering, media selection, or any stored row.

**Story members are resolved through a URL index, not through ``story["coverage"]``.** A coverage
entry carries publisher/headline/lean/url and NO media fields (``story_service._coverage``), and
its ``url`` is the DISPLAY url while rows are keyed canonically — an instrument that reads media
off coverage measures its own join and reports zeros. That trap has now cost two instruments in
this repo; both keys index the same row here.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import media                 # noqa: E402 — _abs/_area, the same helpers production selects with
import story_service         # noqa: E402
import store as store_mod    # noqa: E402


# The identity/rank/selection helpers are IMPORTED from media, not redefined: `norm_image` and
# the candidate rank started life here, and shipping them moved them to `media.image_identity` /
# `media.hero_rank` / `media.pick_story_hero(ranked=True)`. A local copy would let the instrument
# and the product quietly diverge — the exact failure mode the reuse threshold was measured to
# prevent in images.
norm_image = media.image_identity


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=None)
    ap.add_argument("--top", type=int, default=20, help="rows in the reuse table")
    ap.add_argument("--examples", type=int, default=8,
                    help="story-hero examples to print for the biggest reused assets")
    args = ap.parse_args(argv)

    st = store_mod.Store(args.db)
    rows = story_service._fetch(st)
    stories = story_service.build_stories(rows, entities=story_service._entities_for(st, rows))

    # Coverage carries no media; resolve each member back to its row through BOTH url forms.
    by_url: dict = {}
    for r in rows:
        for key in (r.get("canonicalUrl"), r.get("url")):
            if key and key not in by_url:
                by_url[key] = r

    def members_of(story: dict) -> list:
        seen, out = set(), []
        for c in story["coverage"]:
            r = by_url.get(c.get("url"))
            if r is not None and id(r) not in seen:
                seen.add(id(r))
                out.append(r)
        return out

    unresolved = sum(1 for s in stories for c in s["coverage"] if c.get("url") not in by_url)
    if unresolved:
        print(f"WARNING: {unresolved:,} coverage entries did not resolve to a row — the media "
              f"numbers below undercount by that much")

    # ---- reuse index over the WHOLE window, not just story members: a house asset appears on
    # plenty of articles that never cleared min_publishers, and undercounting it would understate
    # exactly the thing being measured.
    art_count: dict = {}
    art_pubs: dict = {}
    for r in rows:
        key = norm_image(r.get("image"))
        if not key:
            continue
        art_count[key] = art_count.get(key, 0) + 1
        art_pubs.setdefault(key, set()).add(r.get("publisher") or "?")
    story_ids: dict = {}
    for st_ in stories:
        for key in {norm_image(m.get("image")) for m in members_of(st_)}:
            if key:
                story_ids.setdefault(key, set()).add(st_["id"])

    with_image_rows = sum(1 for r in rows if media._abs(r.get("image")))
    heroed = [s for s in stories if s.get("image")]
    guard = story_service.hero_guard()
    print(f"hero guard           : {'ON — the heroes below are ranked-mode' if guard else 'off — legacy representative-first heroes'} "
          f"(RWE_STORY_HERO_GUARD)")
    print(f"window articles      : {len(rows):,}  (with an image: {with_image_rows:,}, "
          f"{with_image_rows / max(1, len(rows)):.1%})")
    print(f"stories              : {len(stories):,}  (with a hero: {len(heroed):,}, "
          f"{len(heroed) / max(1, len(stories)):.1%})")
    print(f"distinct image URLs  : {len(art_count):,}")

    # ---- 1. PROVENANCE ------------------------------------------------------------------------
    rep_sourced = other_sourced = 0
    src_hist: dict = {}
    hero_key_of: dict = {}
    for st_ in stories:
        hero = media._abs(st_.get("image"))
        if not hero:
            continue
        members = members_of(st_)
        if not members:
            continue
        rep = min(members, key=lambda m: (m.get("publishedAt") or "~",
                                          m.get("canonicalUrl") or m.get("url") or ""))
        if media._abs(rep.get("image")) == hero:
            rep_sourced += 1
        else:
            other_sourced += 1
        owner = next((m for m in members if media._abs(m.get("image")) == hero), None)
        src = store_mod.normalize_image_source((owner or {}).get("imageSource"))
        src_hist[src] = src_hist.get(src, 0) + 1
        hero_key_of[st_["id"]] = norm_image(hero)
    print(f"\n-- 1. hero provenance --")
    print(f"  from the REPRESENTATIVE (earliest filer): {rep_sourced:,} "
          f"({rep_sourced / max(1, len(heroed)):.1%})")
    print(f"  from any other member                   : {other_sourced:,}")
    print(f"  ingestion source: " + ", ".join(f"{k} {v:,}" for k, v in
                                              sorted(src_hist.items(), key=lambda kv: -kv[1])))

    # ---- 2. REUSE -----------------------------------------------------------------------------
    print(f"\n-- 2. most-reused image URLs (story-df is the branding signal) --")
    print(f"{'stories':>7} {'arts':>6} {'pubs':>5}  url")
    ranked = sorted(art_count, key=lambda k: (-len(story_ids.get(k, ())), -art_count[k]))
    for key in ranked[:args.top]:
        sd = len(story_ids.get(key, ()))
        print(f"{sd:>7} {art_count[key]:>6} {len(art_pubs[key]):>5}  {key[:96]}")
        if sd >= 2 and args.examples:
            pubs = ", ".join(sorted(art_pubs[key])[:3])
            print(f"{'':>20}  publishers: {pubs}")

    # ---- 2b. ARTICLE SURFACES -----------------------------------------------------------------
    # Discover/Search/Saved/Recommendations serve each row's image RAW — the story-hero guard
    # never touches them. `imageSuspect` (feed_article_to_article) carries the suspect tier to
    # those surfaces as data; this section is its production measurement: how many article images
    # the verdict flags, and which assets those are. Post-deploy, the flagged share is the share
    # of Discover cards that will render text-first instead of fronting furniture.
    sus_rows = [r for r in rows if media._abs(r.get("image")) and media.hero_suspect(
        r.get("image"), r.get("imageWidth"), r.get("imageHeight"))]
    print(f"\n-- 2b. article-surface suspects (imageSuspect on raw article images) --")
    print(f"  with an image: {with_image_rows:,}; suspect: {len(sus_rows):,} "
          f"({len(sus_rows) / max(1, with_image_rows):.1%})")
    sus_ids: dict = {}
    for r in sus_rows:
        k = norm_image(r.get("image"))
        if k:
            sus_ids[k] = sus_ids.get(k, 0) + 1
    for k in sorted(sus_ids, key=lambda k: -sus_ids[k])[:8]:
        print(f"  {sus_ids[k]:>5} arts  {k[:96]}")

    # ---- 3. IMPACT ----------------------------------------------------------------------------
    # Each row RUNS the shipped selector (media.pick_story_hero(ranked=True) — reuse reject +
    # suspect tier + evidence rank) with that row's reject set, against whatever heroes the
    # measured environment currently serves. The marked row is the production threshold; with the
    # guard ON it should read ~0, and that reading is the post-deploy verification.
    shipped_t = media.HERO_MAX_CLUSTER_REUSE + 1
    print(f"\n-- 3. impact by reuse threshold (reject an image on >= T distinct stories) --")
    print(f"{'T':>3} {'urls rejected':>14} {'heroes rejected':>16} {'hero changes':>13} "
          f"{'-> no hero':>11}")
    for t in (2, 3, 4, 6, 10, 20):
        rejected_keys = frozenset(k for k, sids in story_ids.items() if len(sids) >= t)
        hero_rejected = sum(1 for sid, k in hero_key_of.items() if k in rejected_keys)
        changed = lost = 0
        for st_ in stories:
            members = members_of(st_)
            if not members:
                continue
            rep = min(members, key=lambda m: (m.get("publishedAt") or "~",
                                              m.get("canonicalUrl") or m.get("url") or ""))
            new_hero = (media.pick_story_hero(members, representative=rep, ranked=True,
                                              rejected=rejected_keys) or {}).get("image") or ""
            old_hero = media._abs(st_.get("image"))
            if not new_hero and old_hero:
                lost += 1
            elif new_hero and new_hero != old_hero:
                changed += 1
        mark = f"  <- shipped (HERO_MAX_CLUSTER_REUSE={media.HERO_MAX_CLUSTER_REUSE})" \
            if t == shipped_t else ""
        print(f"{t:>3} {len(rejected_keys):>14,} {hero_rejected:>16,} {changed:>13,} "
              f"{lost:>11,}{mark}")

    # ---- named exhibits: the stories whose hero is a reused asset today ------------------------
    worst = [k for k in ranked if len(story_ids.get(k, ())) >= 2][:args.examples]
    if worst:
        print(f"\n-- stories currently heroed by a multi-story asset (the reported symptom) --")
        shown = 0
        for st_ in stories:
            k = hero_key_of.get(st_["id"])
            if k in worst:
                print(f"  {st_['totalCoverage']:>3} arts / {st_['publisherCount']:>2} pubs  "
                      f"{st_['title'][:60]}")
                print(f"       {k[:100]}")
                shown += 1
                if shown >= args.examples:
                    break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
