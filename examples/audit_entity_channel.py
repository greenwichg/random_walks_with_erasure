"""audit_entity_channel.py — X6 Phase 0: can entity/geo evidence separate good merges from bad?

READ-ONLY instrument. It measures whether a SECOND sparse representation per article — the
X5b entity names (``store.entities_for_urls``, ``entity_noise``-filtered) plus the X4 event
countries — carries enough signal to corroborate clustering GROWTH decisions, before any rule
is wired anywhere. The idea under test is the one transferable piece of X's SimClusters tweet
similarity (docs pending; see the X6 report): represent an item in a second, lower-dimensional
sparse space and let agreement there corroborate — never replace — the lexical decision.

Nothing is persisted, no configuration is read beyond what a production build reads, and no
candidate rule exists yet. The instrument DOES run ``story_service.build_stories`` twice in
memory — once with the production environment's knobs (the baseline the pairs come from) and
once at the library fallbacks (single linkage, no repair/merge/veto: the documented 787-article
counterfactual, ``story_service.link_quorum``) — both results are measured and discarded.
Run it from a container that carries the deploy environment (``dc run --rm -T api …``) or the
baseline it prints is fiction — the same warning ``audit_clustering_change.py`` carries.

Populations measured (all sampling deterministic — lowest indices first, fixed caps, no RNG):

  (a) intra-story pairs   — member pairs of the shipped build's stories: the pairs any veto
                            must NOT break. Good-edge loss is measured here.
  (b1) recorded exhibits  — the six mis-cluster pairs recorded 2026-08-17 (Toronto/Palomar
                            remains, Lexington/Portland shootings, the athletics first-gold
                            fixture, the Antam gold-price fixture, the Garmin cross-language
                            merge, the eclipse-angle merge), re-found in the current window by
                            title signature; absent signatures are reported, never fabricated.
  (b2) counterfactual bridges — cross pairs inside the single-linkage build's largest cluster
                            whose two sides sit in DIFFERENT shipped stories: the edges the
                            production quorum breaks and single linkage welds.
  (c) lexical near-misses — pairs sharing >= min_shared tokens with Jaccard in
                            [sim - 0.08, sim), inside the time window: what a looser lexical
                            gate would admit. Context for a future recall question, not part
                            of the kill test.

Channel score per pair: shared non-noise entity names (the X5b currency), entity-set Jaccard,
and the geo relation (shared / disjoint / unknown, X4's vocabulary). Veto rules swept:

  E(k)     — both sides carry >= 1 name AND fewer than k are shared  (fail-open on coverage)
  G        — both sides carry >= 1 event country AND the sets are disjoint
  E(k)|G   — either fires;   E(k)&G — both fire

**Pre-registered kill criterion (X6 Phase 0, fixed before any number was seen): if no rule
holds good-edge loss on population (a) under 2% while catching a non-trivial share of (b2),
X6 stops here and Phase 1 is not built.** The verdict line applies the bar mechanically; the
exhibits sections exist so the loss and the misses get hand-read, not just counted.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from bisect import bisect_right
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import clustering                # noqa: E402
import discover                  # noqa: E402
import outlet_registry           # noqa: E402
import story_service             # noqa: E402
import store as store_mod        # noqa: E402

# Deterministic sampling caps — census where cheap, lowest-index sample where quadratic.
PAIRS_PER_STORY = 45             # 10 members' cross-pairs; census below that
BRIDGE_PAIR_CAP = 400
BRIDGE_PER_STORY = 3             # blob members sampled per constituent shipped story
NEAR_MISS_CAP = 2000
NEAR_BAND = 0.08                 # near-miss band: [sim - NEAR_BAND, sim)

#: The recorded mis-cluster exhibits (2026-08-17), as title signatures: (label, side A terms,
#: side B terms). Terms of length <= 3 match on word boundaries so "uk" cannot match "ukraine".
EXHIBITS = (
    ("remains: Palomar vs Toronto", ("human remains", "palomar"), ("human remains", "scarborough")),
    ("shooting: Lexington vs Portland", ("shooting", "lexington"), ("shooting", "portland")),
    ("athletics first-gold fixture", ("first", "gold", "armbruster"), ("first", "gold", "english")),
    ("Antam gold-price fixture", ("harga emas antam", "naik"), ("harga emas antam", "anjlok")),
    ("Garmin cross-language", ("garmin", "cirqa", "band"), ("garmin", "cirqa", "ring")),
    ("eclipse angle", ("solar eclipse", "uk"), ("solar eclipse", "netherlands")),
)


def _match(title: str, terms) -> bool:
    t = (title or "").lower()
    for term in terms:
        if len(term) <= 3:
            if not re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", t):
                return False
        elif term not in t:
            return False
    return True


def names_of(art: dict, entities: dict) -> frozenset:
    """The article's non-noise entity names — exactly X5b's per-member set
    (story_service._merge_by_entities.profile), minus the vote floor, which is a cluster-level
    concept a pair cannot have."""
    ents = entities.get(art.get("id") or art.get("url")) or {}
    return frozenset(name for kind in ("person", "org") for name in ents.get(kind, ())
                     if name and not story_service.entity_noise(name))


def countries_of(art: dict) -> frozenset:
    return frozenset(str(c).upper() for c in (art.get("eventCountries") or ()) if c)


def pair_score(a: dict, b: dict, entities: dict) -> dict:
    na, nb = names_of(a, entities), names_of(b, entities)
    ca, cb = countries_of(a), countries_of(b)
    shared = na & nb
    geo = ("unknown" if not ca or not cb else ("shared" if ca & cb else "disjoint"))
    return {
        "names": len(shared), "shared": sorted(shared)[:4],
        "covered": bool(na) and bool(nb),
        "ejaccard": (len(shared) / len(na | nb)) if (na and nb and (na | nb)) else 0.0,
        "geo": geo,
    }


def veto(s: dict, rule: str, k: int) -> bool:
    e = s["covered"] and s["names"] < k
    g = s["geo"] == "disjoint"
    return {"E": e, "G": g, "E|G": e or g, "E&G": e and g}[rule]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=None)
    ap.add_argument("--examples", type=int, default=6, help="exhibit pairs printed per section")
    args = ap.parse_args(argv)

    # The audit-environment check audit_clustering_change taught: a container without the deploy
    # knobs would replay single linkage while labelling it the baseline.
    tags = {"quorum": story_service.link_quorum(), "repair": story_service.repair_quorum(),
            "merge": story_service.merge_similarity(), "veto": story_service.geo_veto() or "off"}
    print(f"environment          : quorum {tags['quorum']}  repair {tags['repair']}  "
          f"merge {tags['merge']}  geo-veto {tags['veto']}")
    if tags["quorum"] <= 0.0:
        print("  !! quorum 0.0 — this is NOT the production environment; baseline below is "
              "single linkage. Run via `dc run --rm -T api …`.")

    st = store_mod.Store(args.db)
    rows = story_service._fetch(st)
    ents = st.entities_for_urls([r.get("canonicalUrl") for r in rows])
    print(f"window articles      : {len(rows):,}   entity side-table rows resolve for "
          f"{len(ents):,} urls")

    # The serialized article list, exclusion-mirrored — the exact population the builder
    # clusters (story_service.build_stories applies the same three tests, same order).
    arts = [discover.feed_article_to_article(r) for r in rows]
    if story_service.exclude_wire():
        arts = [a for a in arts if not (outlet_registry.is_wire(a.get("publisher"))
                                        or outlet_registry.is_wire_url(a.get("url")))]
    if story_service.exclude_aggregator():
        arts = [a for a in arts if not outlet_registry.is_aggregator(a.get("publisher"))]

    # -- 1. channel coverage ------------------------------------------------------------- #
    with_names = sum(1 for a in arts if names_of(a, ents))
    with_geo = sum(1 for a in arts if countries_of(a))
    with_both = sum(1 for a in arts if names_of(a, ents) and countries_of(a))
    n_arts = max(1, len(arts))
    print(f"\n-- 1. channel coverage (over {len(arts):,} post-exclusion articles) --")
    print(f"  >=1 non-noise entity name : {with_names:>6,}  ({with_names / n_arts:.1%})")
    print(f"  >=1 event country         : {with_geo:>6,}  ({with_geo / n_arts:.1%})")
    print(f"  both channels             : {with_both:>6,}  ({with_both / n_arts:.1%})")

    # -- 2. the two in-memory builds ------------------------------------------------------ #
    baseline = story_service.build_stories(rows, entities=story_service._entities_for(st, rows))
    counterfactual = story_service.build_stories(rows, quorum=0.0, repair=0.0, merge=0.0,
                                                 veto="", entity_merge=0)

    by_url: dict = {}
    for a in arts:
        for key in (a.get("id"), a.get("url")):
            if key and key not in by_url:
                by_url[key] = a

    def members_of(story) -> list:
        seen, out = set(), []
        for c in story["coverage"]:
            a = by_url.get(c.get("url"))
            if a is not None and id(a) not in seen:
                seen.add(id(a))
                out.append(a)
        return out

    # (a) intra-story pairs — census under the cap, lowest-index sample above it.
    good_pairs, unresolved = [], 0
    for s in baseline:
        mem = members_of(s)
        if len(mem) < 2:
            unresolved += 1
            continue
        cap_members = mem[:10]                     # 10 members -> at most 45 cross pairs
        taken = 0
        for i in range(len(cap_members)):
            for j in range(i + 1, len(cap_members)):
                good_pairs.append((cap_members[i], cap_members[j], s["title"]))
                taken += 1
                if taken >= PAIRS_PER_STORY:
                    break
            if taken >= PAIRS_PER_STORY:
                break
    print(f"\n-- 2. populations --")
    print(f"  (a) intra-story pairs      : {len(good_pairs):,} from {len(baseline):,} stories"
          f"  (join-unresolved stories: {unresolved})")

    # (b1) recorded exhibits, re-found by signature.
    exhibit_pairs = []
    for label, ta, tb in EXHIBITS:
        a = next((x for x in arts if _match(x.get("headline"), ta)), None)
        b = next((x for x in arts if _match(x.get("headline"), tb)), None)
        if a is not None and b is not None and a is not b:
            exhibit_pairs.append((a, b, label))
    found = {lbl for _, _, lbl in exhibit_pairs}
    print(f"  (b1) recorded exhibits     : {len(exhibit_pairs)} of {len(EXHIBITS)} re-found"
          + ("" if len(found) == len(EXHIBITS) else
             f"  (absent: {', '.join(l for l, _, _ in EXHIBITS if l not in found)})"))

    # (b2) counterfactual bridges — the blob's cross-shipped-story pairs.
    story_of: dict = {}
    for si, s in enumerate(baseline):
        for m in members_of(s):
            story_of[id(m)] = si
    blob = max((members_of(s) for s in counterfactual), key=len, default=[])
    per_story: dict = {}
    reps = []
    for m in blob:
        si = story_of.get(id(m))
        if si is None:
            continue
        if per_story.get(si, 0) < BRIDGE_PER_STORY:
            per_story[si] = per_story.get(si, 0) + 1
            reps.append((si, m))
    bridge_pairs = []
    for x in range(len(reps)):
        for y in range(x + 1, len(reps)):
            if reps[x][0] != reps[y][0]:
                bridge_pairs.append((reps[x][1], reps[y][1], "bridge"))
                if len(bridge_pairs) >= BRIDGE_PAIR_CAP:
                    break
        if len(bridge_pairs) >= BRIDGE_PAIR_CAP:
            break
    print(f"  (b2) counterfactual blob   : {len(blob):,} articles spanning "
          f"{len(per_story):,} shipped stories -> {len(bridge_pairs):,} bridge pairs sampled")

    # (c) lexical near-misses — the production pairwise gate, band just below it.
    cap = story_service.desc_tokens()
    sim = clustering.DEFAULT_SIM
    min_shared = (story_service.desc_min_shared() if cap > 0
                  else story_service.min_shared_tokens())
    floor = story_service.min_title_tokens()
    toks = [story_service.article_tokens(a, cap) for a in arts]
    times = [clustering.parse_time(a["publishedAt"]) for a in arts]
    weights = clustering.idf_weights(toks) if story_service.use_idf() else None
    postings: dict = {}
    for i, t in enumerate(toks):
        for tok in t:
            postings.setdefault(tok, []).append(i)
    near = []
    lo = max(0.0, sim - NEAR_BAND)
    for i in range(len(arts)):
        if len(near) >= NEAR_MISS_CAP:
            break
        if len(toks[i]) < floor:
            continue
        # The same bisect-past-i + C-level tally the production candidate walk uses
        # (clustering.cluster) — the naive per-posting loop is the measured 49%-of-build shape.
        shared_counts: Counter = Counter()
        for tok in toks[i]:
            plist = postings[tok]
            tail = plist[bisect_right(plist, i):]
            if tail:
                shared_counts.update(tail)
        for j, overlap in shared_counts.items():
            if overlap < min_shared or len(toks[j]) < floor:
                continue
            score = clustering.weighted_jaccard(toks[i], toks[j], weights)
            if lo <= score < sim and clustering.within_window(times[i], times[j],
                                                              clustering.DEFAULT_WINDOW_DAYS):
                near.append((arts[i], arts[j], f"j={score:.2f}"))
                if len(near) >= NEAR_MISS_CAP:
                    break
    print(f"  (c) near-misses [{lo:.2f}, {sim:.2f}) : {len(near):,} pairs (cap {NEAR_MISS_CAP:,})")

    # -- 3. distributions ----------------------------------------------------------------- #
    pops = (("(a) intra-story", [pair_score(a, b, ents) for a, b, _ in good_pairs]),
            ("(b1) exhibits", [pair_score(a, b, ents) for a, b, _ in exhibit_pairs]),
            ("(b2) bridges", [pair_score(a, b, ents) for a, b, _ in bridge_pairs]),
            ("(c) near-miss", [pair_score(a, b, ents) for a, b, _ in near]))
    print(f"\n-- 3. channel score distributions --")
    print(f"  {'population':<16}{'pairs':>7}{'cov%':>7}{'names 0/1/2/3+':>18}"
          f"{'geo sh/dis/unk':>17}{'ejac p50':>9}")
    for label, scores in pops:
        n = max(1, len(scores))
        hist = [sum(1 for s in scores if s["names"] == 0),
                sum(1 for s in scores if s["names"] == 1),
                sum(1 for s in scores if s["names"] == 2),
                sum(1 for s in scores if s["names"] >= 3)]
        geo = [sum(1 for s in scores if s["geo"] == g) for g in ("shared", "disjoint", "unknown")]
        ej = sorted(s["ejaccard"] for s in scores)
        cov = sum(1 for s in scores if s["covered"])
        print(f"  {label:<16}{len(scores):>7,}{cov / n:>7.0%}"
              f"{'/'.join(str(h) for h in hist):>18}"
              f"{'/'.join(str(g) for g in geo):>17}"
              f"{(ej[len(ej) // 2] if ej else 0.0):>9.2f}")

    # -- 4. the kill test ------------------------------------------------------------------ #
    print(f"\n-- 4. veto-rule sweep (goodLoss bar: < 2.0%; kill criterion pre-registered) --")
    print(f"  {'rule':<8}{'goodLoss(a)':>12}{'catch(b1)':>11}{'catch(b2)':>11}{'block(c)':>10}")
    best = None
    scores_by_pop = dict(pops)
    for rule in ("E", "E|G", "E&G", "G"):
        for k in ((1, 2, 3) if rule != "G" else (1,)):
            name = f"{rule}({k})" if rule != "G" else "G"
            rates = {}
            for label, scores in pops:
                n = max(1, len(scores))
                rates[label] = sum(1 for s in scores if veto(s, rule, k)) / n
            ok = rates["(a) intra-story"] < 0.02
            print(f"  {name:<8}{rates['(a) intra-story']:>12.2%}{rates['(b1) exhibits']:>11.0%}"
                  f"{rates['(b2) bridges']:>11.0%}{rates['(c) near-miss']:>10.0%}"
                  + ("   <- meets good-loss bar" if ok else ""))
            if ok and (best is None or rates["(b2) bridges"] > best[1]):
                best = (name, rates["(b2) bridges"], rule, k)
    if best and best[1] > 0.0:
        print(f"\n  VERDICT: PASS — {best[0]} holds good-loss < 2% and catches "
              f"{best[1]:.0%} of counterfactual bridges. Phase 1 is justified.")
    else:
        print(f"\n  VERDICT: KILL — no rule separates the populations within the "
              f"pre-registered bar. X6 stops here; Phase 1 is not built.")

    # -- 5. exhibits ----------------------------------------------------------------------- #
    def show(title, pairs):
        print(f"\n-- {title} --")
        for a, b, note in pairs[: args.examples]:
            s = pair_score(a, b, ents)
            print(f"  [{str(note)[:40]}] names={s['names']} {s['shared']} geo={s['geo']}")
            print(f"    A: {(a.get('headline') or '')[:76]}")
            print(f"    B: {(b.get('headline') or '')[:76]}")

    show("exhibit pairs, scored", exhibit_pairs)
    show("counterfactual bridge pairs (first sampled)", bridge_pairs)
    if best:
        rule, k = best[2], best[3]
        lost = [(a, b, t) for (a, b, t) in good_pairs
                if veto(pair_score(a, b, ents), rule, k)]
        missed = [(a, b, t) for (a, b, t) in bridge_pairs
                  if not veto(pair_score(a, b, ents), rule, k)]
        show(f"good pairs the best rule {best[0]} would veto (the loss, hand-read these)", lost)
        show(f"bridges the best rule {best[0]} misses", missed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
