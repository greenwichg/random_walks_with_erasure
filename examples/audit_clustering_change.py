"""audit_clustering_change.py — measure a clustering-threshold change against the LIVE catalog.

Clustering thresholds are an empirical question about the real headline mix, not something to
settle on hand-picked examples. This runs the current window's articles through two parameter sets
and reports what actually moves: story count, the clusters that split, and the boilerplate titles
that stop merging.

The BEFORE side defaults to whatever the code is configured with — i.e. **production**. That
matters more than it sounds. It used to default to "no admission gates" (the pre-2026-07-27
behaviour: ratio only), which was right while the gates were the change under test and became
wrong the moment they shipped: every later measurement then charged the already-paid cost of the
admission gates to whatever new change was being tried. A link-quorum run scored 13.7% dropped
coverage that way, most of it not the quorum's doing. Pass ``--before-min-shared 1
--before-min-tokens 1`` to get the historical comparison back.

    python examples/audit_clustering_change.py                       # before/after summary
    python examples/audit_clustering_change.py --min-shared 4        # try a candidate value
    python examples/audit_clustering_change.py --show 20             # list the biggest splits
    python examples/audit_clustering_change.py --link-quorum 0.3     # cluster-aware linkage
    python examples/audit_clustering_change.py --min-support 2       # merge support breadth
    python examples/audit_clustering_change.py --entity-veto         # X5c entity disagreement
    python examples/audit_clustering_change.py --desc-tokens 12      # cluster on title + dek

It prints a VERDICT against bars fixed in advance (``--max-dropped``), because the previous
tightening change looked good on its headline numbers and cost 10.5% of covered articles — a
number nobody would have accepted if asked first.

**The VERDICT line is a COST check, not the whole criterion.** The bar registered on
``story_service.link_quorum`` reads "largest cluster well down, droppedOut ≤ 5%, no story-count
fall", and this line only tests the middle clause. Two candidates have now printed ADOPT and been
rejected on the rest of it — X6 (recorded in ``docs/STORY_TEMPLATE_GATE.md``) and the
support-breadth ``groups`` scope, which printed ADOPT at 1.9% while ``--pieces`` showed the splits
were same-event fragmentation ("US national debt passes $40tn" severed from "US debt tops $40
trillion"). **Read ``--pieces N`` before adopting anything that splits clusters.** A rising story
count is fragments as often as it is events, and only the pieces distinguish them.

**Measuring a rule that has already been adopted.** Flags like ``--entity-veto`` set the AFTER
side only; the BEFORE side resolves from the environment, so once a rule is a compose default a
bare run compares it against itself and honestly reports 0/0/0. Turn the baseline off for that
container instead::

    dc run --rm -T -e RWE_STORY_ENTITY_VETO=0 api python \
        examples/audit_clustering_change.py --entity-veto --pieces 5

**Reading a ``--desc-tokens`` run.** This one is an INSTRUMENT, not a candidate — the shared-token
floor it pairs with cannot separate paraphrases from wire templates, and
``story_service.desc_tokens`` records the measurement. What the run is for is sizing the two
populations on the real catalog: how much genuine paraphrase recall is being left on the table
against how many template collisions it would cost. That ratio is what decides whether the real
fix is worth building.

Two consequences for reading the output. It moves in BOTH directions at once — deks add linkage,
the raised floor removes it — so the splitting bars apply (dropped coverage is the real risk, and
the IDF revert is why) but the story-count rule can fire for the legitimate reason that two
paraphrase clusters became one event. And the VERDICT line is close to meaningless here: use
``--pieces`` and read the joins, because only the titles distinguish "same event, different words"
from "same template, different event", which is exactly the distinction the aggregates cannot make.
"""

from __future__ import annotations

import argparse
import os

import clustering
import story_service
import store as store_mod

#: Reject above this share of covered articles falling out of stories entirely. Set from the IDF
#: experiment, which measured 10.5% and was reverted.
MAX_DROPPED = 0.05

#: The engine's compose service defines every one of these with a default
#: (deploy/docker-compose.yml), so a container that carries the deploy environment ALWAYS has
#: them set. A container where NONE is present is running the library fallbacks — single linkage,
#: no repair pass, no duplicate merge — which stopped being production on 2026-08-03. The
#: distinction earned this guard: a backup-profile container (no ``environment:`` block) ran this
#: audit on 2026-08-16, reported a 787-article mega-cluster under a "[PRODUCTION BASELINE]" tag,
#: and the number stood as the production baseline until the missing quorum/repair/merge tags gave
#: it away. ``RWE_STORIES_SCAN_DAYS`` is deliberately absent from the list — the test suite sets
#: it, and a fixture window override is not evidence of the deploy env.
_DEPLOY_CLUSTER_ENV = (
    "RWE_CLUSTER_LINK_QUORUM", "RWE_STORY_REPAIR_QUORUM", "RWE_STORY_MERGE_SIM",
    "RWE_CLUSTER_MIN_SHARED", "RWE_CLUSTER_MIN_TOKENS", "RWE_CLUSTER_IDF",
    "RWE_STORY_EXCLUDE_WIRE", "RWE_STORY_EXCLUDE_AGGREGATOR",
    "RWE_CLUSTER_GEO_VETO", "RWE_STORY_ENTITY_MERGE",     # X4 + X5b, adopted 2026-08-16
    "RWE_CLUSTER_TEMPLATE_LEXICONS",                      # lexicon set, adopted 2026-08-24
    "RWE_CLUSTER_MIN_SUPPORT", "RWE_CLUSTER_SUPPORT_SCOPE",   # merge support breadth
    "RWE_STORY_ENTITY_VETO",                              # X5c entity disagreement
)


def deploy_env_present() -> bool:
    """Whether this environment shows any sign of the deploy's clustering configuration.

    PRESENCE is the test, not truthiness: compose gives each variable in the tuple a concrete
    default, so even one set — whatever its value, including empty — is evidence the env plumbing
    ran. All eight absent means the baseline about to be printed is the library fallbacks, and
    the output must not call that production."""
    return any(os.environ.get(k) is not None for k in _DEPLOY_CLUSTER_ENV)


def build(rows: list, *, min_shared: int, min_tokens: int, idf: bool = False,
          quorum=None, support=None, repair=None, merge=None, desc=None, veto=None,
          veto_stats=None,
          entity_merge=None, ent_veto=None, entities=None, lexicons=None, hyphen=None, uni=None,
          s_scope=None,
          derived=None, derived_df=None, derived_days=None) -> list:
    """``None`` means "whatever production is configured with" — ``build_stories`` resolves it.

    These defaulted to 0.0, which silently made the BEFORE side something production is not. It is
    the same defect as the admission-gate baseline, and it survived that fix because only the two
    original knobs were corrected: a ``--merge-sim`` run then compared *unrepaired* against
    *unrepaired + merge*, where the duplicate clusters the merge exists to join are still fused
    inside the mega-cluster and there is by construction nothing for it to do."""
    return story_service.build_stories(rows, min_shared=min_shared, min_tokens=min_tokens, idf=idf,
                                       quorum=quorum, support=support, s_scope=s_scope,
                                       repair=repair, merge=merge, desc=desc,
                                       veto=veto, veto_stats=veto_stats,
                                       uni=uni,
                                       entity_merge=entity_merge, ent_veto=ent_veto,
                                       entities=entities,
                                       lexicons=lexicons, hyphen=hyphen,
                                       derived=derived, derived_df=derived_df,
                                       derived_days=derived_days)


def _exhibit_outcomes(rows: list, a_member: dict, b_member: dict) -> list:
    """The rubric's ratified exhibit pairs, resolved against THIS window and both builds.

    Each row: (label, truth, before_same_story, after_same_story) — the booleans None when a
    side's article is no longer in the window (aged out; reported, never fabricated). "Same
    story" is membership in one admitted story; a pair split across stories and a pair where a
    side made no story at all both read as separated, which is the direction that matters for a
    different_event exhibit and is marked distinctly in the printout for a same_event one."""
    try:
        import audit_verifier_band as band
    except Exception:
        return []
    out = []
    for label, ta, tb, truth, _rule in band.V1_EXHIBITS:
        ua = ub = None
        for r in rows:
            t = r.get("title") or r.get("headline") or ""
            u = r.get("canonicalUrl") or r.get("url") or ""
            if not u:
                continue
            if ua is None and band._sig_match(t, ta):
                ua = u
            elif ub is None and band._sig_match(t, tb):
                ub = u
            if ua and ub:
                break
        if not (ua and ub):
            out.append((label, truth, None, None))
            continue
        before_same = a_member.get(ua) is not None and a_member.get(ua) == a_member.get(ub)
        after_same = b_member.get(ua) is not None and b_member.get(ua) == b_member.get(ub)
        out.append((label, truth, before_same, after_same))
    return out


def index_by_member(stories: list) -> dict:
    """article id -> the story id it landed in, so membership can be diffed."""
    out = {}
    for s in stories:
        for c in s["coverage"]:
            out[c["url"]] = s["id"]
    return out


def _coherence_stats(stories: list) -> dict:
    """geoCoherence over the clusters that carry one. The scored subset is a minority of the
    catalog (three located members are required), so these are reported WITH their denominator —
    a mean over 91 of 925 stories is not a statement about the catalog."""
    # ACTIONABLE scores only — the same bar _cluster_trust uses. Counting a 0.50 backed by two
    # located members would report movement in a signal the product does not act on.
    scored = [s["geoCoherence"] for s in stories
              if s.get("geoCoherence") is not None
              and (s.get("locatedMembers") or 0) >= story_service.MIN_LOCATED_FOR_TRUST]
    floor = story_service.coherence_floor()
    return {
        "scored": len(scored),
        "bad": len([c for c in scored if c < floor]),
        "mean": round(sum(scored) / len(scored), 3) if scored else None,
    }


#: Largest cluster a merge may produce. Above this it is rebuilding the blob.
MERGE_MAX_LARGEST = 120

#: How far mean coherence may fall before a merge is rejected.
#:
#: Not zero, because a merge MOVES THE DENOMINATOR. Combining two clusters pools their located
#: members, which can lift a pair over ``MIN_LOCATED_FOR_TRUST`` and add a cluster to the scored
#: set that was not in it before — measured, 67 scored clusters became 68. A mean over a changed
#: denominator is not a like-for-like comparison, and a single new entry below the mean moves it by
#: roughly 1/68 of the difference, about 0.002. Rejecting on that is rejecting on arithmetic.
#:
#: 0.01 absorbs about five such entries while still catching a real degradation. The rule that
#: actually detects a bad merge is the bad-cluster COUNT, which has a fixed meaning regardless of
#: how many clusters are scored.
MERGE_MEAN_TOLERANCE = 0.01


def verdict(res: dict, *, max_dropped: float = MAX_DROPPED, merging: bool = False) -> dict:
    """Adopt / reject against the bars, computed rather than eyeballed.

    **The bars depend on which direction the change moves**, and applying the wrong set would
    reject a good change on principle. A SPLIT is judged on coverage retained; a MERGE drops no
    articles at all and is judged on whether it rebuilt something it should not have.

    Splitting rules, both learned from measurements already taken:

    * dropped coverage over ``max_dropped`` — the IDF experiment's failure mode.
    * story count FALLING — the ``min_publishers`` cliff. Splitting a 4-article/2-publisher cluster
      into 2+2 can leave two single-publisher fragments, and both are then dropped. Oversplitting
      does not merely shrink stories, it deletes them, and a raw article count hides that.

    Merging rules — the cliff rule is dropped because a falling story count is the POINT (45
    duplicate stories becoming 22 events), and a merge cannot strand a single-publisher fragment:

    * any dropped coverage at all. A merge that loses articles has a bug, not a trade-off.
    * a largest cluster over ``MERGE_MAX_LARGEST`` — the runaway that started all of this.
    * the independent signal getting worse: more bad clusters, or mean coherence falling by more
      than ``MERGE_MEAN_TOLERANCE``. The COUNT is the rule that bites — it has a fixed meaning
      whatever the scored set does. The mean needs the tolerance because a merge pools located
      members and can lift a pair into the scored set, moving the denominator under the average.
    """
    covered = res["beforeCovered"] or 1
    dropped = res["droppedOut"] / covered
    fails = []
    if merging:
        if res["droppedOut"]:
            fails.append(f"a merge dropped {res['droppedOut']:,} articles — merges add coverage, "
                         f"they never lose it, so this is a bug")
        if res["afterLargest"] > MERGE_MAX_LARGEST:
            fails.append(f"largest cluster {res['afterLargest']} > {MERGE_MAX_LARGEST} "
                         f"(rebuilding the blob)")
        before, after = res["beforeCoherence"], res["afterCoherence"]
        if after["bad"] > before["bad"]:
            fails.append(f"bad clusters rose {before['bad']} -> {after['bad']} "
                         f"(the independent signal says the merge is wrong)")
        if (before["mean"] is not None and after["mean"] is not None
                and after["mean"] < before["mean"] - MERGE_MEAN_TOLERANCE):
            fails.append(f"mean coherence fell {before['mean']} -> {after['mean']} "
                         f"(beyond the {MERGE_MEAN_TOLERANCE} denominator tolerance)")
        return {"droppedShare": dropped, "fails": fails, "adopt": not fails}
    if dropped > max_dropped:
        fails.append(f"dropped {dropped:.1%} of covered articles (bar {max_dropped:.0%})")
    if res["afterStories"] < res["beforeStories"]:
        fails.append(f"story count fell {res['beforeStories']:,} -> {res['afterStories']:,} "
                     f"(min_publishers cliff)")
    return {"droppedShare": dropped, "fails": fails, "adopt": not fails}


def compare(store_, *, before: tuple, after: tuple, show: int = 10,
            before_idf: bool = False, after_idf: bool = False,
            before_quorum=None, after_quorum=None, after_support=None, after_scope=None,
            after_repair=None, after_merge=None, after_desc=None,
            after_veto=None, after_entity_merge=None, after_ent_veto=None,
            after_lexicons=None, after_hyphen=None, after_uni=None,
            after_derived=None, after_derived_df=None, after_derived_days=None) -> dict:
    rows = story_service._fetch(store_)
    # The entity mapping is fetched when EITHER a flag asks for the X5b pass OR production is
    # configured with it (adopted 2026-08-16) — and it is handed to BOTH sides, because the
    # BEFORE side's whole contract is "whatever production is configured with": resolving
    # entity_merge_min() to 2 while silently withholding the data it needs would make the
    # baseline something production is not, the exact defect this docstring's history keeps
    # re-finding one knob at a time.
    need_entities = (bool(after_entity_merge) or story_service.entity_merge_min() > 0
                     or bool(after_ent_veto) or story_service.entity_veto())
    entities = (store_.entities_for_urls([r.get("canonicalUrl") for r in rows])
                if need_entities else None)
    a = build(rows, min_shared=before[0], min_tokens=before[1], idf=before_idf,
              quorum=before_quorum, entities=entities)
    # Telemetry only when a veto/pass is explicitly under test — a None passthrough must stay
    # byte-identical to production, counting included.
    veto_stats = ({} if (after_veto or after_entity_merge or after_derived or after_ent_veto)
                  else None)
    b = build(rows, min_shared=after[0], min_tokens=after[1], idf=after_idf, quorum=after_quorum,
              support=after_support, s_scope=after_scope, repair=after_repair, merge=after_merge, desc=after_desc,
              veto=after_veto, veto_stats=veto_stats,
              entity_merge=after_entity_merge, ent_veto=after_ent_veto, entities=entities,
              lexicons=after_lexicons, hyphen=after_hyphen, uni=after_uni,
              derived=after_derived, derived_df=after_derived_df,
              derived_days=after_derived_days)
    if veto_stats is not None and "derivedBoilerplate" in veto_stats:
        print(f"derived boilerplate : {veto_stats['derivedBoilerplate']} tokens, "
              f"{veto_stats['derivedManualOverlap']} shared with the manual lexicons "
              f"(the self-check: the derivation should rediscover them)")

    a_by_id = {s["id"]: s for s in a}
    b_by_id = {s["id"]: s for s in b}
    a_member = index_by_member(a)
    b_member = index_by_member(b)
    exhibits = _exhibit_outcomes(rows, a_member, b_member)

    # A "merge" = a new story whose members came from more than one old story. The split table
    # cannot show this: under a pure merge every old story's members land in exactly ONE new story,
    # so `len(dests) == 1` and `clusters changed` reads 0 while 14 stories have in fact been joined.
    # The two operations need their own counters or a merge looks like a no-op.
    joins: dict = {}
    for url, new in b_member.items():
        old = a_member.get(url)
        if old is not None:
            joins.setdefault(new, set()).add(old)
    merged = [(nid, olds) for nid, olds in joins.items() if len(olds) > 1]
    merged.sort(key=lambda kv: -b_by_id[kv[0]]["totalCoverage"])

    # A "split" = an old story whose members now live in more than one story (or in none).
    fates: dict = {}
    for url, old in a_member.items():
        fates.setdefault(old, set()).add(b_member.get(url))
    split = [(sid, dests) for sid, dests in fates.items() if len(dests) > 1 or dests == {None}]
    split.sort(key=lambda kv: -a_by_id[kv[0]]["totalCoverage"])

    # Where the dropped articles CAME FROM. droppedOut on its own cannot tell a regression from a
    # fix: an article leaving a 101-article/4-publisher press-release template is the change working,
    # and an article leaving a 48-publisher wire story is the change costing real coverage. Both
    # decrement the same counter. Attributing each loss to the cluster it left makes the mix visible,
    # and articles-per-publisher is the tell — a template is one outlet repeating itself (25 articles
    # per publisher), a real story is many outlets covering one event (~1.4).
    lost: dict = {}
    for url, old in a_member.items():
        if url not in b_member:
            lost[old] = lost.get(old, 0) + 1
    dropped_from = sorted(lost.items(), key=lambda kv: -kv[1])

    return {
        "articles": len(rows),
        "vetoStats": veto_stats,
        "beforeStories": len(a),
        "afterStories": len(b),
        "beforeLargest": max((s["totalCoverage"] for s in a), default=0),
        "afterLargest": max((s["totalCoverage"] for s in b), default=0),
        "splitCount": len(split),
        "mergedCount": len(merged),
        "mergedFrom": [{
            "articles": b_by_id[nid]["totalCoverage"],
            "publishers": b_by_id[nid]["publisherCount"],
            "title": b_by_id[nid]["title"],
            "parts": sorted(({"articles": a_by_id[o]["totalCoverage"],
                              "publishers": a_by_id[o]["publisherCount"],
                              "title": a_by_id[o]["title"]} for o in olds),
                            key=lambda x: -x["articles"]),
        } for nid, olds in merged],
        # Whether the INDEPENDENT signal improved. A change that splits clusters without moving
        # this has rearranged the catalog rather than corrected it.
        "beforeCoherence": _coherence_stats(a),
        "afterCoherence": _coherence_stats(b),
        # Blindspot claims on IDENTICAL rows. The live catalog moved 57 -> 62 claims across a merge
        # deploy, but it also gained ~100 articles in the same interval, so that delta cannot be
        # attributed. Both sides here are built from one row set, which makes the attribution exact.
        "beforeClaims": len([s for s in a if s.get("blindspotSide")]),
        "afterClaims": len([s for s in b if s.get("blindspotSide")]),
        "exhibits": exhibits,
        # Coverage retention: a change that "improves" the numbers by quietly dropping articles out
        # of stories is not an improvement. droppedOut counts articles that were in a story and now
        # are in none.
        "beforeCovered": len(a_member),
        "afterCovered": len(b_member),
        "droppedOut": len([u for u in a_member if u not in b_member]),
        "newlyCovered": len([u for u in b_member if u not in a_member]),
        "droppedFrom": [{
            "lost": n,
            "articles": a_by_id[sid]["totalCoverage"],
            "publishers": a_by_id[sid]["publisherCount"],
            "perPublisher": a_by_id[sid]["totalCoverage"] / max(1, a_by_id[sid]["publisherCount"]),
            "title": a_by_id[sid]["title"],
        } for sid, n in dropped_from[:show]],
        "split": [{
            "articles": a_by_id[sid]["totalCoverage"],
            "publishers": a_by_id[sid]["publisherCount"],
            "pieces": len([d for d in dests if d]),
            "dissolved": None in dests,
            "title": a_by_id[sid]["title"],
        } for sid, dests in split[:show]],
        # The pieces themselves. No aggregate can answer the question that decides a split — are
        # these recognisably separate events, or is one story shredded into shards? — so the titles
        # have to be readable. Sorted biggest first; the long tail of 2-article pieces is the tell
        # for over-fragmentation.
        "splitInto": [{
            "title": a_by_id[sid]["title"],
            "articles": a_by_id[sid]["totalCoverage"],
            "publishers": a_by_id[sid]["publisherCount"],
            "pieces": sorted(
                ({"articles": b_by_id[d]["totalCoverage"],
                  "publishers": b_by_id[d]["publisherCount"],
                  "title": b_by_id[d]["title"]} for d in dests if d),
                key=lambda p: -p["articles"]),
        } for sid, dests in split],
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=None)
    ap.add_argument("--before-min-shared", type=int, default=None,
                    help="baseline gate (default: configured, i.e. production)")
    ap.add_argument("--before-min-tokens", type=int, default=None,
                    help="baseline gate (default: configured, i.e. production)")
    ap.add_argument("--min-shared", type=int, default=None, help="candidate (default: configured)")
    ap.add_argument("--min-tokens", type=int, default=None, help="candidate (default: configured)")
    ap.add_argument("--show", type=int, default=10)
    ap.add_argument("--idf", action="store_true",
                    help="score the AFTER side with rarity-weighted similarity")
    ap.add_argument("--link-quorum", type=float, default=None,
                    help="cluster-aware linkage on the AFTER side: fraction of cross-pairs that "
                         "must agree before two clusters merge (0 = single linkage)")
    ap.add_argument("--entity-veto", action="store_true",
                    help="X5c on the AFTER side: refuse a cluster merge when both sides carry a "
                         "corroborated entity consensus and those consensuses share no name")
    ap.add_argument("--support-scope", choices=("any", "groups"), default=None,
                    help="where --min-support applies: 'any' (every side of 2+, the measured "
                         "8.7%% variant) or 'groups' (only when BOTH sides are already 2+, "
                         "exempting the singleton-absorption growth that cost the 8.7%%)")
    ap.add_argument("--min-support", type=int, default=None,
                    help="merge support BREADTH on the AFTER side: distinct members each side "
                         "must contribute to the passing cross-pairs (1 = off). Orthogonal to "
                         "--link-quorum, which measures the passing FRACTION instead")
    ap.add_argument("--repair-quorum", type=float, default=None,
                    help="TARGETED linkage: re-split only the clusters the independent signal "
                         "condemns, leaving every other story untouched")
    ap.add_argument("--merge-sim", type=float, default=None,
                    help="second-pass duplicate merge: join clusters whose description-backed "
                         "profiles reach this weighted similarity (recall, not precision)")
    ap.add_argument("--desc-tokens", type=int, default=None,
                    help="add the first N description tokens to each article's clustering signal "
                         "(0 = headline only, as production is). Raises the shared-token floor to "
                         "--desc-min-shared unless --min-shared is given")
    ap.add_argument("--desc-min-shared", type=int, default=None,
                    help="the floor to pair with --desc-tokens (default: configured, %d)"
                         % story_service.desc_min_shared())
    ap.add_argument("--geo-veto", choices=story_service._GEO_VETO_MODES, default=None,
                    help="X4 entity-evidence veto on the AFTER side "
                         "(docs/STORY_ENTITY_EVIDENCE_PLAN.md): 'pair' vetoes every "
                         "lexically-matching pair whose event-country sets are both present and "
                         "disjoint; 'growth' gates only cluster merges past MIN_CHAINABLE, on "
                         "located-consensus disagreement. Fail-open on missing data")
    ap.add_argument("--entity-merge", type=int, default=None, metavar="N",
                    help="X5b entity-corroborated merge recall on the AFTER side: join stories "
                         "sharing >= N corroborated non-noise consensus names (2 is the designed "
                         "minimum — one name can be a responder agency). Complete linkage, the "
                         "X4 geo-consensus veto, the coherence guard, size cap and gap window "
                         "all apply. Requires backfilled article_entities; a MERGE-direction "
                         "change, judged by the merge bars")
    ap.add_argument("--template-lexicons", default=None, metavar="NAMES",
                    help="AFTER-side sole-boilerplate lexicons, comma-separated from "
                         f"{sorted(story_service.TEMPLATE_LEXICONS)} — e.g. "
                         "'announce,tracker,preview'. Same rule and hook as the adopted "
                         "announce gate; an edge must share >= 1 token outside the UNION. "
                         "Registered candidates: tracker (box-office/OTT day-counter chains), "
                         "preview (recurring fixture previews)")
    ap.add_argument("--derived-boilerplate", action="store_true",
                    help="AFTER side: derive the sole-boilerplate vocabulary from the window "
                         "itself (df + day-spread conditions; story_service.derived_boilerplate) "
                         "instead of relying only on the manual lexicons")
    ap.add_argument("--boilerplate-df", type=int, default=None, metavar="N",
                    help="derived-boilerplate df floor (default: configured, 25)")
    ap.add_argument("--boilerplate-days", type=int, default=None, metavar="N",
                    help="derived-boilerplate distinct-days floor (default: configured, 5)")
    ap.add_argument("--unicode-words", action="store_true",
                    help="CANDIDATE TOKENIZER: match \\w plus combining marks instead of "
                         "[a-z0-9], and emit character bigrams for scripts with no word separator "
                         "(CJK, Thai). Measured 2026-08-27: the shipped class yields ZERO tokens "
                         "for ko/ar/zh/ja/ru/ta/hi, and pair_admits rejects anything under "
                         "MIN_TITLE_TOKENS before any other test, so those articles cannot join a "
                         "story under any configuration. Does NOT fold diacritics — see "
                         "story_service.unicode_words.")
    ap.add_argument("--hyphen-compounds", action="store_true",
                    help="AFTER side: hyphenated compounds also contribute their joined token "
                         "('X-Men' carries 'xmen', not just the generic fragment 'men') — the "
                         "xmen-pair false-split defect. Additive only at the token level")
    ap.add_argument("--pieces", type=int, default=0,
                    help="print the resulting pieces for the N biggest split clusters — the read "
                         "that decides whether a split separated events or shredded a story")
    ap.add_argument("--piece-limit", type=int, default=25,
                    help="how many pieces to print per cluster")
    ap.add_argument("--max-dropped", type=float, default=MAX_DROPPED,
                    help="reject the change above this share of covered articles dropped")
    args = ap.parse_args(argv)

    configured = (story_service.min_shared_tokens(), story_service.min_title_tokens())
    cap = story_service.desc_tokens() if args.desc_tokens is None else args.desc_tokens
    # The dek changes what a shared token IS WORTH, so the floor moves with it — the same coupling
    # `build_stories` applies. It has to be re-derived here because this parser resolves every knob
    # to a concrete value before calling, so passing `configured[0]` would silently hand the
    # dek-enabled side the headline floor and measure a change nobody would ship.
    if args.min_shared is not None:
        after_shared = args.min_shared
    elif cap > 0:
        after_shared = (args.desc_min_shared if args.desc_min_shared is not None
                        else story_service.desc_min_shared())
    else:
        after_shared = configured[0]
    after = (after_shared,
             args.min_tokens if args.min_tokens is not None else configured[1])
    before = (args.before_min_shared if args.before_min_shared is not None else configured[0],
              args.before_min_tokens if args.before_min_tokens is not None else configured[1])
    lex_names = None
    if args.template_lexicons is not None:
        lex_names = tuple(n.strip().lower() for n in args.template_lexicons.split(",") if n.strip())
        unknown = [n for n in lex_names if n not in story_service.TEMPLATE_LEXICONS]
        if unknown:
            print(f"unknown lexicon(s): {', '.join(unknown)} — "
                  f"known: {', '.join(sorted(story_service.TEMPLATE_LEXICONS))}")
            return 2
    res = compare(store_mod.Store(args.db), before=before,
                  after=after, show=args.show, after_idf=args.idf,
                  after_quorum=args.link_quorum, after_support=args.min_support,
                  after_ent_veto=True if args.entity_veto else None,
                  after_scope=args.support_scope,
                  after_repair=args.repair_quorum,
                  after_merge=args.merge_sim, after_desc=cap, after_veto=args.geo_veto,
                  after_entity_merge=args.entity_merge,
                  after_lexicons=lex_names,
                  after_hyphen=True if args.hyphen_compounds else None,
                  after_uni=True if args.unicode_words else None,
                  after_derived=True if args.derived_boilerplate else None,
                  after_derived_df=args.boilerplate_df,
                  after_derived_days=args.boilerplate_days)

    def _tag(name, v):
        return f", {name} {v:g}" if v else ""
    # The before side is production, so name what production ALREADY has as well as the override —
    # otherwise a run that changes nothing looks like a run that was never configured.
    tag = ((", idf" if args.idf else "")
           + _tag("quorum", args.link_quorum if args.link_quorum is not None
                  else story_service.link_quorum())
           + _tag("support", args.min_support if args.min_support is not None
                  else story_service.min_support())
           + (f"/{args.support_scope or story_service.support_scope()}"
              if (args.min_support or story_service.min_support() > 1) else "")
           + _tag("repair", args.repair_quorum if args.repair_quorum is not None
                  else story_service.repair_quorum())
           + _tag("merge", args.merge_sim if args.merge_sim is not None
                  else story_service.merge_similarity())
           + _tag("dek", cap)
           + (f", veto {args.geo_veto or story_service.geo_veto()}"
              if (args.geo_veto or story_service.geo_veto()) else "")
           + (f", entity-merge {args.entity_merge or story_service.entity_merge_min()}"
              if (args.entity_merge or story_service.entity_merge_min()) else "")
           + (", entity-veto" if (args.entity_veto or story_service.entity_veto()) else "")
           + (f", lexicons {'+'.join(lex_names)}" if lex_names else "")
           + (", unicode-words" if args.unicode_words else "")
           + (", hyphen-compounds" if args.hyphen_compounds else "")
           + ((", derived-boilerplate"
               + (f"(df>={args.boilerplate_df})" if args.boilerplate_df else "")
               + (f"(days>={args.boilerplate_days})" if args.boilerplate_days else ""))
              if args.derived_boilerplate else ""))
    base_tag = (_tag("quorum", story_service.link_quorum())
                + _tag("support", story_service.min_support())
                + _tag("repair", story_service.repair_quorum())
                + _tag("merge", story_service.merge_similarity())
                + _tag("dek", story_service.desc_tokens())
                + (f", veto {story_service.geo_veto()}" if story_service.geo_veto() else "")
                + (f", entity-merge {story_service.entity_merge_min()}"
                   if story_service.entity_merge_min() else "")
                + (", entity-veto" if story_service.entity_veto() else ""))
    # "[PRODUCTION BASELINE]" is a claim about the ENVIRONMENT, not just about before == configured.
    # Every environment is self-consistent with its own defaults, so without this check the label
    # certifies any container as production — which is exactly how a backup-profile container's
    # single-linkage numbers got taken as the production baseline (2026-08-16).
    if not deploy_env_present():
        base_label = "   [LIBRARY FALLBACKS — no deploy env]"
        print("WARNING: none of the deploy's clustering variables are set in this environment\n"
              f"         ({', '.join(_DEPLOY_CLUSTER_ENV[:3])}, …).\n"
              "         The baseline below is the LIBRARY FALLBACKS — single linkage, no repair\n"
              "         pass, no duplicate merge — which production has not run since 2026-08-03.\n"
              "         For production numbers, run from a container that carries the api\n"
              "         service's environment:\n"
              "           cd /opt/ih && source deploy/ops/_compose.sh\n"
              "           dc run --rm -T api python examples/audit_clustering_change.py …\n")
    else:
        base_label = "   [PRODUCTION BASELINE]"
    print(f"articles in window : {res['articles']:,}")
    print(f"before  (shared>={before[0]}, tokens>={before[1]}{base_tag}): "
          f"{res['beforeStories']:,} stories, largest {res['beforeLargest']}"
          f"{base_label if before == configured else '   [not production]'}")
    print(f"after   (shared>={after[0]}, tokens>={after[1]}{tag}): "
          f"{res['afterStories']:,} stories, largest {res['afterLargest']}")
    print(f"clusters split     : {res['splitCount']:,}")
    print(f"clusters merged    : {res['mergedCount']:,}")
    print(f"articles in a story: {res['beforeCovered']:,} -> {res['afterCovered']:,} "
          f"(dropped out {res['droppedOut']:,}, newly covered {res['newlyCovered']:,})")

    if res.get("exhibits"):
        print("\nknown exhibits (truth from the ratified rubric; 'together' = one admitted story)")
        for label, truth, b_same, a_same in res["exhibits"]:
            if b_same is None:
                print(f"  {label:<22} {truth:<16} not in this window")
                continue
            want_together = truth == "same_event"
            word = lambda s: "together" if s else "separated"
            before_ok = b_same == want_together
            after_ok = a_same == want_together
            note = ("ok" if (before_ok and after_ok)
                    else "FIXED" if (not before_ok and after_ok)
                    else "REGRESSED" if (before_ok and not after_ok)
                    else "still wrong")
            print(f"  {label:<22} {truth:<16} {word(b_same):<10} -> {word(a_same):<10} [{note}]")

    print(f"blindspot claims   : {res['beforeClaims']:,} -> {res['afterClaims']:,} "
          f"(same rows, so this delta IS the change's doing)")

    bc, ac = res["beforeCoherence"], res["afterCoherence"]
    print(f"independent signal : {bc['bad']}/{bc['scored']} bad (mean {bc['mean']}) -> "
          f"{ac['bad']}/{ac['scored']} bad (mean {ac['mean']})")
    if res["vetoStats"] is not None:
        vs = res["vetoStats"]
        # `pairChecked` counts LEXICALLY-VIABLE pairs (the veto is consulted after the token
        # gates), so vetoed/bothLocated is the veto's real bite on edges that would otherwise
        # exist. Admission and quorum share one predicate by design, so they are one counter.
        print(f"geo-veto telemetry : pairs checked {vs.get('pairChecked', 0):,} "
              f"(both located {vs.get('pairBothLocated', 0):,}, "
              f"vetoed {vs.get('pairVetoed', 0):,}); "
              f"merges checked {vs.get('mergeChecked', 0):,} "
              f"(gated {vs.get('mergeGated', 0):,}, vetoed {vs.get('mergeVetoed', 0):,}); "
              f"dup-merge vetoed {vs.get('dupMergeVetoed', 0):,}")
        if args.entity_veto or story_service.entity_veto():
            # X5c's own counters, printed separately from the geo line above: the two channels
            # gate the same merges and a single combined number could not say which one bit.
            print(f"X5c telemetry      : merges checked "
                  f"{vs.get('entityMergeChecked', 0):,} "
                  f"(both had consensus {vs.get('entityMergeGated', 0):,}, "
                  f"vetoed {vs.get('entityMergeVetoed', 0):,}); "
                  f"dup-merge vetoed {vs.get('dupMergeEntityVetoed', 0):,}")
        if args.entity_merge:
            print(f"entity-merge       : candidates {vs.get('entityMergeCandidates', 0):,}, "
                  f"joined {vs.get('entityMergeJoined', 0):,}, "
                  f"unanchored {vs.get('entityMergeUnanchored', 0):,}, "
                  f"geo-vetoed {vs.get('entityMergeGeoVetoed', 0):,}, "
                  f"coherence-vetoed {vs.get('entityMergeCoherenceVetoed', 0):,}, "
                  f"size-capped {vs.get('entityMergeSizeCapped', 0):,}, "
                  f"gap-blocked {vs.get('entityMergeGapBlocked', 0):,}")
            if not vs.get("entityMergeCandidates"):
                print("                     NOTE: zero candidates — no two stories share the "
                      "minimum corroborated names, or article_entities is empty (run "
                      "gdelt_entity_backfill.py first)")
        if args.geo_veto and not any(vs.values()):
            print("                     NOTE: the veto was never consulted — either nothing "
                  "lexically matched or no event locations exist in this catalog")

    for grp in res["mergedFrom"][:args.pieces]:
        print(f"\n=== merged into: {grp['title'][:66]}")
        print(f"    {grp['articles']} articles / {grp['publishers']} publishers, from "
              f"{len(grp['parts'])} stories")
        print(f"    {'arts':>5} {'pubs':>5}  was")
        for part in grp["parts"]:
            print(f"    {part['articles']:>5} {part['publishers']:>5}  {part['title'][:62]}")

    for grp in res["splitInto"][:args.pieces]:
        kept = sum(p["articles"] for p in grp["pieces"])
        print(f"\n--- {grp['title'][:70]}")
        print(f"    {grp['articles']} articles / {grp['publishers']} publishers -> "
              f"{len(grp['pieces'])} pieces holding {kept} articles "
              f"({grp['articles'] - kept} dropped)")
        tail = len([p for p in grp["pieces"] if p["articles"] <= 2])
        print(f"    {tail} of those pieces are 2 articles or fewer")
        print(f"    {'arts':>5} {'pubs':>5}  title")
        for pc in grp["pieces"][:args.piece_limit]:
            print(f"    {pc['articles']:>5} {pc['publishers']:>5}  {pc['title'][:64]}")

    v = verdict(res, max_dropped=args.max_dropped,
                merging=bool(args.merge_sim or args.entity_merge))
    print(f"\nVERDICT: {'ADOPT' if v['adopt'] else 'REJECT'} "
          f"(dropped {v['droppedShare']:.1%} of covered articles)")
    for f in v["fails"]:
        print(f"  - {f}")
    if res["droppedFrom"]:
        print("\nwhere the dropped articles came from  (high a/p = one outlet repeating a template)")
        print(f"{'lost':>5} {'arts':>5} {'pubs':>5} {'a/p':>6}  title")
        for d in res["droppedFrom"]:
            print(f"{d['lost']:>5} {d['articles']:>5} {d['publishers']:>5} "
                  f"{d['perPublisher']:>6.1f}  {d['title'][:60]}")
    if res["split"]:
        print(f"\n{'arts':>5} {'pubs':>5} {'->':>4}  title")
        for s in res["split"]:
            dest = "gone" if s["dissolved"] and s["pieces"] == 0 else f"{s['pieces']}"
            print(f"{s['articles']:>5} {s['publishers']:>5} {dest:>4}  {s['title'][:64]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
