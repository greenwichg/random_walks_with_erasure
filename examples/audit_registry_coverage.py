"""audit_registry_coverage.py — what is left to curate, and what curating it would buy.

Three existing audits each answer part of this and none answers it whole. ``outlet_coverage``
ranks unknown outlets by article volume but counts raw NAME STRINGS, so one masthead arriving as
``Yahoo.Com``, ``Finance.Yahoo.Com`` and ``Yahoo! News`` is three entries.
``audit_publisher_identity`` groups those correctly but says nothing about ratings.
``audit_cluster_trust`` reports the unlock worklist but only for outlets appearing in stories that
are exactly one rating short, which is the right worklist and the wrong denominator for "how much
is left".

This joins them. Everything below is counted **per OUTLET IDENTITY**, never per name string, and
every unresolved outlet is placed in exactly one bucket:

* **untracked** — no registry row, and its brand word is unambiguous. A curator can add it.
* **ambiguous** — no registry row, and its bare name is carried by more than one domain in this
  catalog (``The Local``, ``RTL``). Deliberately unplaced: guessing would merge two outlets. Only a
  human can settle it, and the fix is a row per edition, not a rating.
* **tracked, deliberately unrated** — a row EXISTS and the lean is blank on purpose. Three reasons,
  reported separately because they are not the same backlog:
  ``wire`` (machine-generated market-data copy — no editorial stance to rate),
  ``locality-only`` (no public rater covers it), and
  ``low-credibility`` (rated, but the rater called the source Questionable, so the lean is recorded
  and not voted).

Two rankings, because they answer different questions. **Article volume** says how much of the feed
an outlet accounts for. **Unlocks** says how many coverage-gap claims a single row would enable —
stories that are exactly one rating short. They disagree often: an outlet with 300 articles spread
across stories that already carry four rated publishers unlocks nothing.

    python examples/audit_registry_coverage.py
    python examples/audit_registry_coverage.py --top 40
    python examples/audit_registry_coverage.py --json
"""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter

import discover
import outlet_registry
import publisher_identity
import story_service
import store as store_mod

#: Buckets, in report order. Every unresolved or unrated identity lands in exactly one.
UNTRACKED = "untracked"
AMBIGUOUS = "ambiguous"
LOCALITY_ONLY = "locality-only"
LOW_CREDIBILITY = "low-credibility"

_REASON = {
    UNTRACKED: "no registry row — a curator can add one",
    AMBIGUOUS: "no row, and the bare name is carried by more than one domain here",
    LOCALITY_ONLY: "row exists, lean deliberately blank — no public rater covers it",
    LOW_CREDIBILITY: "rated, but the rater called it Questionable — lean recorded, not voted",
    # `kind` values are buckets in their own right: a source that is not a newsroom is not a
    # curation gap, and listing it as one would keep a permanently-blank row on a worklist forever.
    "wire": "machine-generated market-data / press-release copy — no editorial stance to rate",
    "aggregator": "republishes other outlets — its coverage is already in the cluster",
    "research": "a journal or preprint server — MBFC rates these Pro-Science, off the left/right axis",
    "forum": "user-generated posts, not reporting",
    "org": "an organisation publishing its own announcements",
}
#: Report order — real work first, then decisions already taken.
_ORDER = (UNTRACKED, AMBIGUOUS, LOW_CREDIBILITY, LOCALITY_ONLY,
          "aggregator", "wire", "research", "forum", "org")


def _votes(cov: dict) -> bool:
    """Whether this coverage row's lean COUNTS toward the rated-publisher floor.

    Must match ``story_service._votes`` or the worklist measures a rule production does not apply.
    A story's ``coverage`` rows carry ``leanBucket`` but not the low-credibility flag, so an audit
    reading the bucket alone counts TASS as a rated publisher and reports a story as fully-supported
    that the engine treats as one short. Asked of the registry directly, which is where the engine
    resolves it too, and gated by the same switch."""
    if not cov.get("leanBucket"):
        return False
    if not story_service.credibility_gate():
        return True
    return not outlet_registry.is_low_credibility(cov["publisher"])


def _is_ambiguous(forms, contested: set) -> bool:
    """Whether this identity is unplaced because its brand word is contested.

    Mirrors ``audit_publisher_identity`` exactly — a BARE name (not a host form) whose label is
    carried by more than one domain in this catalog. Asked of every form in the group, because an
    identity can arrive as both a bare name and a hostname and only the bare one can be ambiguous."""
    return any(not outlet_registry._looks_like_host(f)
               and outlet_registry._name_key(f) in contested for f in forms)


def _classify(outlet, ambiguous_here: bool) -> "str | None":
    """The bucket for one identity, or ``None`` when it is fully tracked and rated.

    Order matters. ``low-credibility`` is checked before the lean, because such a row DOES carry a
    lean — it simply does not vote it, and reporting it as rated would overstate the sample a
    coverage-gap claim rests on."""
    if outlet is None:
        return AMBIGUOUS if ambiguous_here else UNTRACKED
    if outlet.kind:
        return outlet.kind
    if outlet.credibility == "low":
        return LOW_CREDIBILITY
    if math.isnan(outlet.lean):
        return LOCALITY_ONLY
    return None


def identity_by_url(rows: list, keys: dict) -> dict:
    """``coverage url -> outlet identity``, joined on the ARTICLE rather than on the name.

    **The unlocks metric was blind to most of the backlog it exists to measure, and this is why.**
    ``analyse`` used to key its per-story sets on ``keys.get(c["publisher"])``, but ``keys`` is
    built from the RAW row publisher while ``discover.feed_article_to_article`` puts
    ``engine._prettify(outlet)`` into the coverage. For a registry-resolved outlet those agree —
    ``NPR`` prettifies to ``NPR`` — so the join looked fine. For an **untracked** outlet arriving as
    a bare host it does not: ``gamma.example`` becomes ``Gamma.Example``, misses the map, falls back
    to the prettified string, and never matches the ``d:gamma.example`` identity the buckets are
    keyed on. Proven on a fixture: 3 of 3 untracked outlets MISS, 2 of 2 registry outlets hit.

    So every untracked outlet whose name is a host form — ``sportskeeda.com``, ``decider.com``,
    every local-TV call sign — scored zero unlocks by construction, and only the ones that happen to
    be prettify-stable (``BelTA``, ``NL Times``, ``PerthNow``) were ever counted.

    Joining on the URL removes the whole class: it is one key, taken from the rows the stories were
    built from, and no display transformation touches it. Same fix, same reason, as
    ``audit_source_cohort.member_key``."""
    out = {}
    for r in rows:
        p = (r.get("publisher") or "").strip()
        u = discover._absolute_url(r.get("url") or r.get("canonicalUrl"))
        if p and u:
            out[u] = keys.get(p, p)
    return out


def analyse(rows: list, stories: list, *, min_rated: int) -> dict:
    """Per-identity coverage, buckets, article volume and unlock estimates."""
    names: dict = {}
    for r in rows:
        p = (r.get("publisher") or "").strip()
        if p:
            names[p] = names.get(p, 0) + 1
    # Resolved over the WHOLE build, exactly as the pipeline does: whether a bare name may join a
    # domain depends on how many domains carry that label, which is a property of the catalog.
    keys = publisher_identity.groups(sorted(names))
    contested = publisher_identity.ambiguous_labels(sorted(names))
    by_url = identity_by_url(rows, keys)

    groups: dict = {}
    for name, n in names.items():
        g = groups.setdefault(keys[name], {"articles": 0, "forms": {}, "outlet": None})
        g["articles"] += n
        g["forms"][name] = g["forms"].get(name, 0) + n
        if g["outlet"] is None:
            g["outlet"] = outlet_registry.resolve(name)

    # Unlocks, in identity space. A story that is exactly one rating short is the only one a SINGLE
    # registry row can convert; two or three short need coordinated curation and are counted apart.
    unlocks: dict = {}
    assists: dict = {}
    unmatched = 0
    for idx, s in enumerate(stories):
        outlets = {by_url[c["url"]] for c in s["coverage"] if c["url"] in by_url}
        rated = {by_url[c["url"]] for c in s["coverage"]
                 if c["url"] in by_url and _votes(c)}
        unmatched += sum(1 for c in s["coverage"] if c["url"] not in by_url)
        if len(outlets) < min_rated or len(rated) >= min_rated:
            continue
        target = unlocks if (min_rated - len(rated)) == 1 else assists
        for k in outlets - rated:
            target.setdefault(k, set()).add(idx)

    out = []
    for key, g in groups.items():
        label = max(g["forms"].items(), key=lambda kv: (kv[1], kv[0]))[0]
        bucket = _classify(g["outlet"], _is_ambiguous(g["forms"], contested))
        if bucket is None:
            continue
        out.append({
            "identity": key, "label": label, "bucket": bucket,
            "canonical": g["outlet"].canonical if g["outlet"] else None,
            "articles": g["articles"], "forms": sorted(g["forms"]),
            "unlocks": len(unlocks.get(key, ())), "assists": len(assists.get(key, ())),
        })

    by_bucket: dict = {}
    for r in out:
        b = by_bucket.setdefault(r["bucket"], {"outlets": 0, "articles": 0, "unlocks": 0})
        b["outlets"] += 1
        b["articles"] += r["articles"]
        b["unlocks"] += r["unlocks"]

    # Registry-side totals travel WITH the catalog-side ones, because the two are easy to confuse
    # and the difference is the whole point: `registryRated` is a property of the file,
    # `ratedInWindow` is a property of the feed. A reader who sees only the second reasonably
    # assumes it is the first — which is exactly what happened the first time this ran.
    registry = outlet_registry.default_registry().outlets()
    return {
        "registryRows": len(registry),
        "registryRated": sum(1 for o in registry if not math.isnan(o.lean)),
        "names": len(names), "identities": len(groups),
        "ratedInWindow": len(groups) - len(out),
        "stories": len(stories), "articles": sum(names.values()),
        "buckets": by_bucket, "outlets": out,
        # Every coverage row must join to an identity. A miss means the key convention drifted
        # again -- the defect this instrument shipped with for its whole life -- so it is counted
        # and `main` refuses to report on it rather than printing a silent undercount.
        "unmatchedCoverage": unmatched,
        # Kept so a caller can size a COHORT without re-deriving the join. `main` pops it before
        # --json: nobody reading the report wants a per-URL map inlined.
        "byUrl": by_url,
    }


def cohort_unlocks(stories: list, by_url: dict, cohort: set, *, min_rated: int) -> dict:
    """What a SET of registry rows buys when they land together.

    ``unlocks`` in :func:`analyse` is measured per outlet **in isolation**: it counts only stories
    exactly one rating short, because that is the only kind one row can convert alone. Summing it
    over a cohort therefore UNDERSTATES the cohort — a story two short with two untracked members is
    converted by rating both, and neither one is credited with it.

    This measures the thing a curation batch actually delivers: how many stories reach
    ``min_rated`` when every member of ``cohort`` is treated as rated at once.

    ``shortfall`` is the other half, and it is the number that stops a batch being sized by hope:
    for the stories the cohort touches and still cannot convert, how many MORE ratings each would
    need. A large tail at 2+ means the batch is the wrong shape, not too small."""
    gained, naive, shortfall = [], 0, Counter()
    for idx, s in enumerate(stories):
        outlets = {by_url[c["url"]] for c in s["coverage"] if c["url"] in by_url}
        rated = {by_url[c["url"]] for c in s["coverage"]
                 if c["url"] in by_url and _votes(c)}
        if len(rated) >= min_rated:
            continue                                  # already claims; nothing to buy
        touching = outlets & cohort
        if not touching:
            continue
        if (min_rated - len(rated)) == 1 and touching:
            naive += 1                                # what sum(unlocks) would have counted
        after = rated | touching
        if len(after) >= min_rated:
            gained.append((idx, s.get("title", ""), len(rated), len(touching)))
        else:
            shortfall[min_rated - len(after)] += 1
    return {"cohort": len(cohort), "joint": len(gained), "naiveSum": naive,
            "coordinationBonus": len(gained) - naive,
            "shortfall": dict(sorted(shortfall.items())), "examples": gained[:10]}


def skeletons(rows: list, untracked: list, wanted: list) -> None:
    """Ready-to-paste registry rows for a cohort, with the LEAN DELIBERATELY BLANK.

    The tedious half of curating a row is gathering every name form and host the catalog carries for
    one outlet — ``ign.com`` arrives under three, ``The Hankyoreh`` under four — and getting that
    wrong splits one masthead into several identities, which is the defect
    ``audit_publisher_identity`` exists to prevent. That half is derivable from the catalog and is
    filled in here.

    The half that is NOT derivable is the rating, and this function will not invent it. Every lean in
    ``outlet_registry.csv`` is a transcribed label from a published rater, carried with
    ``factuality_source`` and the ``factuality_asof`` date it was READ — because, in that file's
    words, "an unattributed rating is indistinguishable from a guess". A lean written from a model's
    impression of an outlet, stamped ``mbfc``, would be a false provenance claim about a named news
    organisation sitting in a data file that the product treats as fact.

    So the skeleton stops at the catalog's own knowledge and leaves four fields for the curator:
    the lean, the locality (the rater's page states it), the factuality label, and the read date."""
    by_label = {r["label"]: r for r in untracked}
    hosts_of: dict = {}
    for r in rows:
        p = (r.get("publisher") or "").strip()
        h = outlet_registry._host_of(r.get("canonicalUrl") or r.get("url") or "")
        if p and h:
            hosts_of.setdefault(p, set()).add(h)

    print("\n=== REGISTRY ROW SKELETONS — lean intentionally blank ===")
    print("    canonical,lean,aliases,country,region,city,scope,kind,credibility,"
          "factuality,factuality_source,factuality_asof")
    print("    Fill: lean (rater's label), country/region/city/scope, factuality, source, asof.")
    print("    A row whose rater publishes NO lean must stay blank — it becomes locality-only, and")
    print("    a locality-only row unlocks nothing. Record those under CHECKED AND NOT REGISTERED")
    print("    in the CSV so the next pass does not re-search them.\n")
    for label in wanted:
        r = by_label.get(label)
        if r is None:
            print(f"    # {label}: not an untracked outlet in this window — nothing to add")
            continue
        aliases = sorted({f for f in r["forms"]} | set().union(
            *[hosts_of.get(f, set()) for f in r["forms"]] or [set()]))
        aliases = [a for a in aliases if a != label]
        print(f"    # {label}: {r['articles']} articles, {r['unlocks']} unlock(s), "
              f"{r['assists']} assist(s)")
        print(f"    {label},,{'|'.join(aliases)},,,,,,,,,")


def _table(rows: list, key, top: int) -> str:
    lines = [f"{'arts':>6} {'unlk':>5} {'asst':>5}  outlet"]
    for r in sorted(rows, key=key)[:top]:
        forms = f"   [{len(r['forms'])} name forms]" if len(r["forms"]) > 1 else ""
        lines.append(f"{r['articles']:>6} {r['unlocks']:>5} {r['assists']:>5}  {r['label']}{forms}")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=None)
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--cohort", default="",
                    help="comma-separated outlet labels to size TOGETHER (joint unlocks)")
    ap.add_argument("--cohort-top", type=int, default=20,
                    help="also size the top N untracked outlets by curation value (default %(default)s)")
    ap.add_argument("--skeletons", action="store_true",
                    help="emit registry-row skeletons for --cohort: every field this catalog can "
                         "establish, and a BLANK lean for the curator to source")
    args = ap.parse_args(argv)

    store_ = store_mod.Store(args.db)
    rows = story_service._fetch(store_)
    stories = story_service.build_stories(rows)
    res = analyse(rows, stories, min_rated=story_service.min_rated_for_blindspot())

    by_url = res.pop("byUrl")
    # Every coverage row is an article from the same window, so every one must join to an identity.
    # A miss means the key convention drifted -- which is the defect this instrument shipped with
    # from the start, invisible because a partial join still prints plausible numbers.
    if res["unmatchedCoverage"]:
        print(f"*** JOIN BROKEN: {res['unmatchedCoverage']:,} coverage rows did not resolve to an "
              f"outlet identity.")
        print("    Every unlocks figure would understate. See identity_by_url(). Refusing to report.")
        return 1
    if args.json:
        print(json.dumps(res, indent=2, sort_keys=True))
        return 0

    unseen = res["registryRated"] - res["ratedInWindow"]
    print(f"REGISTRY (the file)  : {res['registryRows']:,} rows, {res['registryRated']:,} rated")
    print(f"WINDOW   (the feed)  : {res['articles']:,} articles, {res['stories']:,} stories")
    print(f"  publisher names    : {res['names']:,}")
    print(f"  outlet identities  : {res['identities']:,}   "
          f"({res['names'] - res['identities']:,} names are another name's alias)")
    print(f"    rated            : {res['ratedInWindow']:,}"
          + (f"   ({unseen:,} rated registry outlets published nothing in this window)"
             if unseen > 0 else ""))
    print(f"    NOT rated        : {sum(b['outlets'] for b in res['buckets'].values()):,}\n")

    print(f"{'outlets':>8} {'articles':>9} {'unlocks':>8}  bucket")
    for b in _ORDER:
        d = res["buckets"].get(b)
        if not d:
            continue
        print(f"{d['outlets']:>8} {d['articles']:>9} {d['unlocks']:>8}  {b}")
        print(f"{'':>27}  {_REASON[b]}")

    untracked = [r for r in res["outlets"] if r["bucket"] == UNTRACKED]
    print(f"\n=== UNTRACKED, by ARTICLE VOLUME — how much of the feed they account for ===")
    print(_table(untracked, lambda r: (-r["articles"], r["label"]), args.top))

    print(f"\n=== UNTRACKED, by UNLOCKS — what ONE row would actually buy ===")
    print("    A story exactly one rating short is the only kind a single row converts.")
    workable = [r for r in untracked if r["unlocks"]]
    print(_table(workable, lambda r: (-r["unlocks"], -r["articles"], r["label"]), args.top)
          if workable else "    (none — no untracked outlet sits in a one-short story)")

    for bucket in (b for b in _ORDER if b != UNTRACKED):
        rows_b = [r for r in res["outlets"] if r["bucket"] == bucket]
        if not rows_b:
            continue
        print(f"\n=== {bucket.upper()} — {_REASON[bucket]} ===")
        print(_table(rows_b, lambda r: (-r["articles"], r["label"]), args.top))

    # ------------------------------------------------------------------ cohort sizing
    #
    # Everything above prices ONE row at a time. A curation batch is not one row at a time, and the
    # difference is not a rounding error: a story two ratings short with two untracked members is
    # converted by rating both, and the per-outlet `unlocks` column credits that story to neither.
    min_rated = story_service.min_rated_for_blindspot()
    by_value = sorted(untracked,
                      key=lambda r: (-r["unlocks"], -r["assists"], -r["articles"], r["label"]))

    def report(title: str, labels: list):
        ids = {r["identity"] for r in untracked if r["label"] in set(labels)}
        missing = set(labels) - {r["label"] for r in untracked}
        c = cohort_unlocks(stories, by_url, ids, min_rated=min_rated)
        print(f"\n=== COHORT: {title} ===")
        if missing:
            print(f"  NOT FOUND as untracked outlets (ignored): {', '.join(sorted(missing))}")
        print(f"  outlets in cohort        : {c['cohort']}")
        print(f"  sum of per-outlet unlocks: {c['naiveSum']}   <- what the column above implies")
        print(f"  JOINT unlocks            : {c['joint']}   <- what the batch actually buys")
        print(f"  coordination bonus       : {c['coordinationBonus']:+d}"
              f"   <- stories only a BATCH converts")
        if c["shortfall"]:
            tail = "  ".join(f"{n} stories still {k} short" for k, n in c["shortfall"].items())
            print(f"  touched but not converted: {tail}")
        for _idx, title_, was, add in c["examples"][:5]:
            print(f"     +1  had {was} rated, cohort adds {add}   {title_[:56]}")

    report(f"top {args.cohort_top} untracked by curation value",
           [r["label"] for r in by_value[:args.cohort_top]])
    if args.cohort:
        wanted = [s.strip() for s in args.cohort.split(",") if s.strip()]
        report("explicit --cohort", wanted)
        if args.skeletons:
            skeletons(rows, untracked, wanted)

    # The ceiling. If curating EVERY untracked outlet converts n stories, no subset converts more,
    # and the whole programme is bounded by that one number. Part 2 priced the backlog at 13 by
    # summing per-outlet unlocks; this is the same question asked without that understatement.
    every = cohort_unlocks(stories, by_url, {r["identity"] for r in untracked},
                           min_rated=min_rated)
    print(f"\n=== THE CEILING: curate ALL {len(untracked):,} untracked outlets ===")
    print(f"  JOINT unlocks : {every['joint']}   of {res['stories']:,} stories "
          f"({every['joint'] / max(1, res['stories']) * 100:.1f}%)")
    print(f"  vs summing per-outlet unlocks: {every['naiveSum']}"
          f"   (understates by {every['coordinationBonus']:+d})")
    if every["shortfall"]:
        print("  stories the untracked tail touches and STILL cannot convert: "
              + "  ".join(f"{n} at {k} short" for k, n in every["shortfall"].items()))
    print("  No subset of the untracked backlog buys more than this. It is the budget for the")
    print("  entire curation programme, and every batch is a fraction of it.")

    total_unlocks = sum(r["unlocks"] for r in untracked)
    print(f"\nWORKLIST: {len(untracked):,} untracked outlets, "
          f"{sum(1 for r in untracked if r['unlocks']):,} of which sit in a one-short story "
          f"and are worth {total_unlocks:,} claims between them.")
    print("Everything in the other buckets is a decision already taken, not a backlog.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
