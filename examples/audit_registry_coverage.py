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

import outlet_registry
import publisher_identity
import story_service
import store as store_mod

#: Buckets, in report order. Every unresolved or unrated identity lands in exactly one.
UNTRACKED = "untracked"
AMBIGUOUS = "ambiguous"
WIRE = "wire"
LOCALITY_ONLY = "locality-only"
LOW_CREDIBILITY = "low-credibility"

_REASON = {
    UNTRACKED: "no registry row — a curator can add one",
    AMBIGUOUS: "no row, and the bare name is carried by more than one domain here",
    WIRE: "machine-generated market-data / press-release copy — no editorial stance to rate",
    LOCALITY_ONLY: "row exists, lean deliberately blank — no public rater covers it",
    LOW_CREDIBILITY: "rated, but the rater called it Questionable — lean recorded, not voted",
}


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
    if outlet.kind == "wire":
        return WIRE
    if outlet.credibility == "low":
        return LOW_CREDIBILITY
    if math.isnan(outlet.lean):
        return LOCALITY_ONLY
    return None


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
    for idx, s in enumerate(stories):
        outlets = {keys.get(c["publisher"], c["publisher"]) for c in s["coverage"]}
        rated = {keys.get(c["publisher"], c["publisher"])
                 for c in s["coverage"] if _votes(c)}
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

    return {
        "names": len(names), "identities": len(groups),
        "tracked_and_rated": len(groups) - len(out),
        "stories": len(stories), "articles": sum(names.values()),
        "buckets": by_bucket, "outlets": out,
    }


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
    args = ap.parse_args(argv)

    store_ = store_mod.Store(args.db)
    rows = story_service._fetch(store_)
    stories = story_service.build_stories(rows)
    res = analyse(rows, stories, min_rated=story_service.min_rated_for_blindspot())

    if args.json:
        print(json.dumps(res, indent=2, sort_keys=True))
        return 0

    print(f"clustering window: {res['articles']:,} articles, {res['stories']:,} stories")
    print(f"publisher names  : {res['names']:,}")
    print(f"outlet identities: {res['identities']:,}   "
          f"({res['names'] - res['identities']:,} names are another name's alias)")
    print(f"  fully tracked and rated : {res['tracked_and_rated']:,}")
    print(f"  NOT rated               : {sum(b['outlets'] for b in res['buckets'].values()):,}\n")

    print(f"{'outlets':>8} {'articles':>9} {'unlocks':>8}  bucket")
    for b in (UNTRACKED, AMBIGUOUS, LOW_CREDIBILITY, LOCALITY_ONLY, WIRE):
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

    for bucket in (AMBIGUOUS, LOW_CREDIBILITY, LOCALITY_ONLY):
        rows_b = [r for r in res["outlets"] if r["bucket"] == bucket]
        if not rows_b:
            continue
        print(f"\n=== {bucket.upper()} — {_REASON[bucket]} ===")
        print(_table(rows_b, lambda r: (-r["articles"], r["label"]), args.top))

    total_unlocks = sum(r["unlocks"] for r in untracked)
    print(f"\nWORKLIST: {len(untracked):,} untracked outlets, "
          f"{sum(1 for r in untracked if r['unlocks']):,} of which sit in a one-short story "
          f"and are worth {total_unlocks:,} claims between them.")
    print("Everything in the other buckets is a decision already taken, not a backlog.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
