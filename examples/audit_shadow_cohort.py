"""audit_shadow_cohort.py — Stage 4 run: what is the shadow lane worth?

**M8 of `docs/SCALE_ROADMAP.md`.** Read-only: no writes, no ingestion, no network, no curation. It
reads the lane M5 built, measures it with `source_evaluation.py`, and prints a verdict per outlet.
Nothing here acts on a verdict — acting is M9.

## The problem this run exists to solve

M5's shadow lane is *stored and surfaced nowhere*, which is what makes it safe to point at 50,000
unvetted sources. It also makes the metric every earlier audit leaned on unavailable: **story
participation is structurally zero for a shadow outlet, forever**, because shadow rows never reach
the builder. `audit_source_cohort.py` cannot be pointed at shadow — it would rank every outlet at 0%
and read as though it had measured something.

So this run asks the counterfactual instead: *would this article have joined a story, had it been
allowed to?* — answered with the clusterer's own pair rule (`clustering.pair_admits`), not a second
implementation of it.

## Two modes, and the second is the one you can run today

``(default)``   evaluate the outlets actually in ``RWE_CORPUS_SHADOW``. Reads them with
                ``include_shadow=True``, the flag that exists for exactly this caller.

``--as-if``     evaluate outlets we ALREADY carry in Tier A, as though they were in shadow. The
                Tier A story set is **rebuilt without them** first, so the index they are scored
                against does not contain their own articles.

``--as-if`` is not a toy. It is the same de-risking order `audit_source_cohort.py` used: exercise
the evaluation stage on real data with zero crawl, zero ToS exposure and zero new code in the
serving path, *before* pointing it at a genuinely new source. It also answers a question worth
asking on its own — what would we lose if this outlet were demoted? — using the harness that will
later judge the outlets we have not met yet.

## The trap this script refuses to fall into

If the assignment index contains the cohort's own coverage, every article attaches to itself and the
rate is ~100% **by construction**. In shadow mode that cannot happen (shadow never enters the
build). In ``--as-if`` mode it is one forgotten rebuild away, so :func:`main` asserts it: no cohort
member may appear in the story set it is scored against, and the run refuses to report if one does.
This audit series has now shipped three key-convention bugs that each produced confident, wrong
numbers; a guard is cheaper than a fourth.

    dc run --rm -T api python examples/audit_shadow_cohort.py --db "$RWE_DB_URL"
    dc run --rm -T api python examples/audit_shadow_cohort.py --db "$RWE_DB_URL" \\
        --as-if "sportskeeda.com,newsbytesapp.com"
"""

from __future__ import annotations

import argparse
import os
from collections import Counter, defaultdict

import audit_clustering_change as ach
import clustering
import corpus
import discover
import outlet_registry
import source_evaluation as se
import story_service
import store as store_mod
from pagination import OffsetPagination


def _identity(reg, row) -> str:
    """The registry's canonical name for a row's publisher, else the raw name lower-cased.

    Same expression `audit_source_cohort._identity` uses. Shadow outlets are mostly untracked, so
    the fallback is the common path here rather than the exception."""
    pub = (row.get("publisher") or "").strip()
    o = reg.resolve(pub) if pub else None
    return o.canonical if o else pub.lower()


def _host(row) -> str:
    return outlet_registry._host_of(row.get("canonicalUrl") or row.get("url") or "")


def _member_key(row) -> str:
    """The key a story's coverage entry carries for this row — ``discover._absolute_url`` of the
    DISPLAY url. See `audit_source_cohort.member_key`: looking up ``canonicalUrl`` here misses on
    most rows and reported participation 20x low on production."""
    return discover._absolute_url(row.get("url") or row.get("canonicalUrl"))


def carrier_index(*row_groups) -> dict:
    """Title-token set → the distinct publishers carrying it, across EVERY group given.

    Built over the cohort **and** the Tier A corpus together, deliberately. Syndication asks whether
    a headline also runs under another publisher, and for a shadow outlet the other publisher is
    almost always a Tier A masthead it is republishing. Counting carriers within the cohort alone
    would score a lone republisher at 0% syndication — the outlet the ceiling exists to catch."""
    carriers = defaultdict(set)
    for rows in row_groups:
        for r in rows:
            toks = clustering.title_tokens(r.get("title") or "")
            if toks:
                carriers[toks].add((r.get("publisher") or "").strip().lower())
    return carriers


def identity_first_seen(st, reg, identities) -> tuple:
    """``(first_seen_by_identity, catalog_articles_by_identity)`` — both over the WHOLE catalog.

    Resolves the identity's spellings from the **catalog**, not from the fetched rows. An outlet can
    arrive under several publisher strings, and asking only the ones that appear in the last 6 days
    makes its "first seen" move whenever a variant falls out of the window — a measurement that
    drifts for a reason having nothing to do with the outlet.

    ``catalog_articles`` comes back with it because the two answer the question the other raises: a
    first-seen that advances between runs means rows left, and that is only visible if the whole-
    catalog count is on screen next to the windowed one."""
    names = defaultdict(list)
    for row in st.catalog_publishers():
        pub = (row.get("publisher") or "").strip()
        if not pub:
            continue
        ident = _identity(reg, {"publisher": pub})
        if ident in identities:
            names[ident].append(row)

    stamps = st.publisher_first_seen({r["publisher"] for rows in names.values() for r in rows})
    first, counts = {}, {}
    for ident, rows in names.items():
        got = [stamps[k] for k in ((r["publisher"] or "").strip().lower() for r in rows)
               if k in stamps]
        if got:
            first[ident] = min(got)
        counts[ident] = sum(int(r.get("articles") or 0) for r in rows)
    return first, counts


def outlet_stats(rows: list, reg, carriers: dict, index: tuple, *, now=None,
                 first_seen: "dict | None" = None,
                 catalog_articles: "dict | None" = None) -> dict:
    """Per outlet identity: every measurement `source_evaluation.evaluate` reads, plus the two it
    reports and never gates on.

    ``first_seen`` maps an outlet **IDENTITY** to the catalog-wide ``MIN(created_at)``, and passing
    it is what makes ``observedDays`` mean the outlet's history rather than the fetch window.
    Omitting it falls back to scanning the rows, which is correct only when the caller holds the
    outlet's whole history — see :func:`observation_is_window_bound` for why the runner never does.

    **Keyed on identity rather than on the windowed publisher strings, and that distinction is the
    bug it fixes.** The first version gathered the strings to look up from the rows it had, so an
    outlet arriving under several spellings only contributed the spellings that happened to appear
    in the last 6 days. A variant falling out of the window would move the outlet's "first seen"
    without anything about its history changing — the same shape as deriving the span from the
    window itself, one level down. :func:`identity_first_seen` asks the catalog which strings belong
    to the identity instead.

    ``catalog_articles`` is the outlet's row count over the WHOLE catalog, printed beside the
    window count so retention erosion is visible rather than inferred."""
    by_id = defaultdict(list)
    for r in rows:
        by_id[_identity(reg, r)].append(r)

    out = {}
    for key, arts in by_id.items():
        o = reg.resolve(key)
        hosts = Counter(_host(a) for a in arts if _host(a))
        dup = sum(1 for a in arts
                  if (t := clustering.title_tokens(a.get("title") or "")) and len(carriers[t]) > 1)
        assign = se.assignment_rate(arts, index)
        since = (first_seen or {}).get(key)
        out[key] = {
            "articles": len(arts),
            "catalogArticles": (catalog_articles or {}).get(key),
            "observedDays": se.observed_days(arts, now=now, since=since),
            "firstSeen": since,
            "freshnessHours": se.freshness_hours(arts),
            "syndication": dup / max(1, len(arts)),
            "hosts": len(hosts),
            "topHost": hosts.most_common(1)[0][0] if hosts else "",
            "hostStability": (hosts.most_common(1)[0][1] / max(1, len(arts))) if hosts else 0.0,
            "assignmentRate": assign["rate"],
            "assignmentStories": assign["stories"],
            "attached": assign["attached"],
            "tracked": o is not None,
            "rated": bool(o is not None and o.lean == o.lean),      # NaN != NaN
            "kind": (o.kind if o else None),
            "canonical": (o.canonical if o else key),
            # Where the outlet is TODAY, so a verdict can be read as a direction rather than as an
            # instruction whose sign depends on an assumption the reader may not share.
            "tier": corpus.tier_of(arts[0].get("publisher"), arts[0].get("url")),
        }
    return out


#: Where each verdict wants the outlet to end up. ``None`` = nowhere; the verdict is not an
#: instruction. Kept beside :func:`direction` rather than inside `source_evaluation` because the
#: policy module is deliberately tier-blind — it scores an outlet, it does not know where one is.
_TARGET_TIER = {"PROMOTE TO TIER B": "B", "TIER A CANDIDATE": "A", "REJECT": "shadow"}


def direction(verdict: str, current_tier: str) -> str:
    """How a verdict reads for an outlet ALREADY IN ``current_tier``: a move up, down, or nowhere.

    **The first production run of the fixed harness is why this exists.** `sportskeeda.com` is in
    **Tier A** today, by grandfathering, and the run printed ``PROMOTE TO TIER B``. For an outlet
    already in Tier A that is a **demotion** wearing the word "promote".

    The vocabulary was written for the shadow lane, where every move is upward — shadow is the
    bottom, so "promote to Tier B" can only mean one thing. `--as-if` breaks that assumption: it
    evaluates outlets we already carry, and against Tier A the same phrase points the other way.
    No number is wrong; the *word* is, and a reader acting on it would move an outlet the opposite
    of what the evidence supports.

    So the direction is computed against where the outlet actually is, and printed beside the
    verdict rather than folded into it — `source_evaluation.evaluate` stays tier-blind and its
    tests stay unchanged."""
    target = _TARGET_TIER.get(verdict)
    if target is None:
        return ""                                       # INSUFFICIENT * — not an instruction
    if target == current_tier:
        return "no change"
    order = {"shadow": 0, "B": 1, "A": 2}
    if order.get(target, 0) > order.get(current_tier, 0):
        return f"UP from {current_tier}"
    return f"*** DOWN from {current_tier} — this is a DEMOTION ***"


def observation_is_window_bound(table: dict, window_days: float) -> bool:
    """Does EVERY outlet's observation span sit at or below the fetch window?

    The signature of the defect the first production run shipped with. ``observedDays`` was derived
    from the fetched rows, and the fetch is bounded to `story_service.scan_days()` — 6 days — so no
    outlet could ever clear the 14-day gate and `INSUFFICIENT DATA` was the only verdict the harness
    could reach. It printed one clean, plausible table and told us nothing.

    A gate that cannot fire is worse than no gate: it reads as a measurement. This audit series has
    now found the same shape in its own instruments three times, so the runner checks rather than
    trusts. False positives are possible and cheap — a genuinely new cohort really is younger than
    the window, and the message says so."""
    spans = [v["observedDays"] for v in table.values() if v["observedDays"] is not None]
    return bool(spans) and max(spans) <= window_days + 0.01


def self_scored(cohort: list, stories: list) -> int:
    """How many of the cohort's own articles are IN the story set they are scored against.

    Must be zero. Otherwise every one of them attaches to itself and the assignment rate is ~100%
    **by construction** — a number that looks like a strong result and measures nothing. Shadow mode
    cannot hit this (shadow rows never enter a build); ``--as-if`` is one forgotten rebuild away."""
    member = ach.index_by_member(stories)
    return sum(1 for r in cohort if _member_key(r) in member)


def _read_shadow(st, *, window_start, cap) -> list:
    """The shadow lane. ``include_shadow=True`` is the whole reason that flag exists — every other
    caller gets the default, which hides these rows from readers."""
    rows, _total = st.search_feed_articles(
        date_from=window_start, sort="newest", include_shadow=True,
        pagination=OffsetPagination.from_params(cap, 0, max_limit=cap))
    return [r for r in rows if corpus.is_shadow(r.get("publisher"), r.get("url"))]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=os.environ.get("RWE_DB_URL"))
    ap.add_argument("--as-if", default="",
                    help="comma-separated outlets to evaluate AS IF shadow; the Tier A story set "
                         "is rebuilt without them first")
    ap.add_argument("--show", type=int, default=30, help="outlets to list")
    args = ap.parse_args(argv)

    st = store_mod.Store(args.db)
    reg = outlet_registry.default_registry()

    window_start = story_service._window_start()
    tier_a = story_service._fetch(st)
    ents = story_service._entities_for(st, tier_a)
    verdicts_in, _band = story_service._event_inputs(st)

    as_if = {p.strip().lower() for p in args.as_if.split(",") if p.strip()}
    if as_if:
        # Rebuild WITHOUT the cohort. Filtering the rows directly rather than through the SQL
        # prefilter, for the reason `audit_source_cohort` gives: the cap is not binding, so the two
        # are equivalent for the build, and this keeps the audit off the query path entirely.
        def _names(r):
            return {_identity(reg, r), (r.get("publisher") or "").strip().lower()}
        cohort = [r for r in tier_a if _names(r) & as_if]
        keep = [r for r in tier_a if not (_names(r) & as_if)]
        # Which of the names given actually matched. A name that matched nothing is a typo or an
        # identity mismatch, and evaluating 1 of 2 named outlets while reporting neither fact is
        # the silent-partial-result failure this audit series keeps correcting.
        matched = set().union(*(_names(r) for r in cohort)) & as_if if cohort else set()
        unmatched = sorted(as_if - matched)
        stories = story_service.build_stories(keep, entities=ents, event_verdicts=verdicts_in)
        mode = f"--as-if: {len(matched)} of {len(as_if)} named outlets, rebuilt without them"
        peers = keep
    else:
        unmatched = []
        cohort = _read_shadow(st, window_start=window_start,
                              cap=story_service.max_scan_default())
        keep = tier_a
        stories = story_service.build_stories(tier_a, entities=ents, event_verdicts=verdicts_in)
        mode = "shadow lane (RWE_CORPUS_SHADOW)"
        peers = tier_a

    print(f"window        : from {window_start}")
    print(f"mode          : {mode}")
    print(f"Tier A built  : {len(keep):,} articles -> {len(stories):,} stories "
          f"({sum(len(s['coverage']) for s in stories):,} covered)")
    print(f"cohort        : {len(cohort):,} articles")
    if unmatched:
        print(f"\n*** {len(unmatched)} NAMED OUTLET(S) MATCHED NOTHING: {', '.join(unmatched)}")
        # Which of the two causes it is, rather than leaving the reader to guess: the catalog knows
        # whether it has EVER held this publisher string, and that separates "the name is wrong"
        # from "the outlet went quiet".
        ever = st.publisher_first_seen(set(unmatched))
        for name in unmatched:
            if name in ever:
                print(f"    {name:<30} IN THE CATALOG since {ever[name]} — published nothing in "
                      f"this window")
            else:
                print(f"    {name:<30} NOT IN THE CATALOG under this exact string — the name is "
                      f"wrong, or it resolves to a registry canonical")
        print("    Everything below describes ONLY the outlets that matched.")

    if not cohort:
        # An empty table is not a finding, and printing one would read as "nothing here is worth
        # promoting". Say which of the two empty states this is.
        print("\nVERDICT: INCOMPLETE — nothing to evaluate.")
        if not as_if:
            shadow = corpus.shadow_exclusions()
            print(f"  RWE_CORPUS_SHADOW names {len(shadow)} outlet(s); "
                  f"{'none of them published in this window' if shadow else 'it is unset'}.")
            print("  The harness is built and tested; it has no subject yet. Either put outlets in")
            print("  shadow, or exercise it on outlets we already carry with --as-if.")
        else:
            print("  None of the named outlets published in this window. Check the names against")
            print("  audit_source_cohort.py's table — identity is the registry canonical, or the")
            print("  raw publisher string lower-cased when untracked.")
        return 0

    # ------------------------------------------------------------------ the self-scoring guard
    #
    # If the cohort's own coverage is in the index it is scored against, every article attaches to
    # itself and the rate is ~100% by construction. Shadow mode cannot hit this; --as-if is one
    # forgotten rebuild away from it. Three key-convention bugs in this audit series each produced
    # confident wrong numbers, so this is checked rather than reasoned about.
    mine = self_scored(cohort, stories)
    if mine:
        print(f"\n*** SELF-SCORING: {mine:,} of the cohort's own articles are IN the story")
        print("    set they are being scored against. Every assignment rate would be ~100% by")
        print("    construction. Refusing to report.")
        return 1

    # ------------------------------------------------------------------ observation, unwindowed
    #
    # MUST come from the catalog, not from `cohort`. The rows above were fetched through a 6-day
    # window, so a span derived from them cannot exceed 6 days and the 14-day gate could never be
    # satisfied by anything — the defect the first production run of this script shipped with.
    # Resolved by IDENTITY from the catalog, not from the windowed rows' publisher strings: an
    # outlet with several spellings would otherwise contribute only the ones the last 6 days
    # happened to contain, and its history would move when a variant aged out.
    identities = {_identity(reg, r) for r in cohort}
    first_seen, catalog_articles = identity_first_seen(st, reg, identities)

    carriers = carrier_index(peers, cohort)
    index = se.assignment_index(stories)
    table = outlet_stats(cohort, reg, carriers, index, first_seen=first_seen,
                         catalog_articles=catalog_articles)

    print(f"outlets       : {len(table):,}   "
          f"tracked {sum(1 for v in table.values() if v['tracked']):,}   "
          f"rated {sum(1 for v in table.values() if v['rated']):,}   "
          f"[membership guard passed: 0 self-scored]")

    scan = story_service.scan_days()
    if observation_is_window_bound(table, scan):
        print(f"\n*** OBSERVATION LOOKS WINDOW-BOUND: no outlet exceeds {scan:g}d, the fetch window.")
        print("    That is the signature of observedDays being derived from the fetched rows rather")
        print(f"    than the catalog, in which case NOTHING can ever clear the "
              f"{se.OBSERVATION_DAYS}d gate and")
        print("    INSUFFICIENT DATA is the only verdict this run can reach. If the cohort really")
        print("    is newer than the window this is a true reading — check `first_seen` below.")

    # ------------------------------------------------------------------ the table
    print(f"\n=== the cohort, ranked by articles that WOULD attach to a story ===")
    print("    `would` is the counterfactual, answered with clustering.pair_admits — the")
    print("    clusterer's own pair rule, so this cannot drift from what a build would do.")
    print("    ATTACH IS REPORTED AND NEVER GATED. No bar for it has been measured, and two")
    print("    invented thresholds have already died against data in this series.")
    print("    The verdict is read AGAINST WHERE THE OUTLET IS TODAY (`now` column). The verdict")
    print("    vocabulary was written for the shadow lane, where every move is upward; --as-if")
    print("    evaluates outlets we already carry, so `PROMOTE TO TIER B` against a Tier A outlet")
    print("    is a DEMOTION. The direction is spelled out rather than left to the word.")
    print(f"\n  {'arts':>6} {'obs_d':>6} {'attach':>7} {'story':>6} {'synd':>6} {'host':>6} "
          f"{'fresh_h':>8} {'now':>7}  outlet")
    for key, s in sorted(table.items(), key=lambda kv: -kv[1]["attached"])[:args.show]:
        v, _why = se.evaluate(s)
        obs = f"{s['observedDays']:.1f}" if s["observedDays"] is not None else "?"
        fresh = f"{s['freshnessHours']:.1f}" if s["freshnessHours"] is not None else "?"
        d = direction(v, s["tier"])
        print(f"  {s['articles']:>6} {obs:>6} {s['attached']:>7} {s['assignmentStories']:>6} "
              f"{s['syndication']:>5.0%} {s['hostStability']:>5.0%} {fresh:>8} "
              f"{s['tier']:>7}  {s['canonical'][:30]:<30} {v}"
              f"{('   [' + d + ']') if d else ''}")

    # ------------------------------------------------------------------ verdicts
    census = Counter(se.evaluate(s)[0] for s in table.values())
    print(f"\n=== verdicts ===")
    for name, n in census.most_common():
        arts = sum(s["articles"] for s in table.values() if se.evaluate(s)[0] == name)
        print(f"  {n:>5} outlets  {arts:>7,} articles  {name}")

    print(f"\n=== the reasons (read these) ===")
    for key, s in sorted(table.items(), key=lambda kv: -kv[1]["articles"])[:args.show]:
        v, why = se.evaluate(s)
        print(f"  {s['canonical'][:30]:<30} {v:<20} {why}")

    print(f"\n=== first seen (catalog-wide MIN(created_at), NOT the fetch window) ===")
    floor = st.catalog_first_seen()
    print(f"    retention floor (oldest surviving row in the catalog): {floor or '(empty)'}")
    print("    An outlet whose first-seen sits AT the floor has not been observed for that long —")
    print("    it has merely not been trimmed yet, and its true first-seen is unknowable from what")
    print("    we still hold. Reading a floor-pinned span as an observation would be the same")
    print("    error as reading the fetch window as one.")
    print("    `catalog` is the outlet's row count over the WHOLE catalog, beside the window count.")
    print("    Run this twice: if first-seen advances while `catalog` falls, retention is eroding")
    print("    the outlet's history and the span is shrinking for a reason that is not the outlet.")
    print(f"\n  {'window':>7} {'catalog':>8}  first seen                   outlet")
    for key, s in sorted(table.items(), key=lambda kv: (kv[1]["firstSeen"] or "9")):
        pinned = ""
        if floor and s["firstSeen"] and se.days_since(s["firstSeen"]) is not None:
            gap = (se.days_since(floor) or 0) - (se.days_since(s["firstSeen"]) or 0)
            pinned = "  <- AT THE FLOOR, span is a lower bound" if gap < 1.0 else ""
        cat = f"{s['catalogArticles']:,}" if s["catalogArticles"] is not None else "?"
        print(f"  {s['articles']:>7,} {cat:>8}  {(s['firstSeen'] or '(unknown)'):<28} "
              f"{s['canonical'][:30]}{pinned}")

    print(f"\n=== what this run does NOT decide ===")
    print("  * Tier A promotion. A TIER A CANDIDATE needs the clustering counterfactual on the")
    print("    production bars — a whole-corpus measurement, not a per-outlet one. Run")
    print("    audit_corpus_boundary.py / audit_clustering_change.py before promoting anything.")
    print("  * Anything at all, automatically. Every verdict here is evidence. Acting on one —")
    print("    moving an outlet between lanes, retiring it — is M9, and it is not built.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
