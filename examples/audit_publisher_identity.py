"""audit_publisher_identity.py — is one outlet being counted as several?

``publisherCount`` counts distinct ``publisher`` STRINGS. Feeds do not agree on how to name an
outlet, so the same masthead arrives as ``Sportskeeda.Com`` and ``Sportskeeda``, or as
``Dailymail.Com`` where the registry knows ``Daily Mail``. Every such pair inflates the count.

That is not a cosmetic problem, because ``publisherCount`` is load-bearing in four places:

* ``min_publishers = 2`` decides whether a cluster becomes a story at all — so **one outlet under
  two name forms can be admitted as if two outlets had covered it**;
* the default ranking sorts on it, so a fragmented outlet lifts a story up the page;
* ``publisherDiversity`` is ``publishers / articles``;
* the ``>= 3 rated publishers`` floor for a coverage-gap claim.

The identity key resolves through the registry first — a curated alias is the authority — and falls
back to a public-suffix-aware brand label for names the registry has never heard of, so
``Thewest.Com.Au`` and ``The West Australian`` land together without either being curated yet.

    python examples/audit_publisher_identity.py
    python examples/audit_publisher_identity.py --show 30
"""

from __future__ import annotations

import argparse

import outlet_registry
import publisher_identity
import story_service
import store as store_mod


def identity_groups(names) -> dict:
    """The pipeline's own rule — see :mod:`publisher_identity`. Imported rather than reimplemented
    so this audit cannot drift from what production actually does."""
    return publisher_identity.groups(names)


def identity_key(name: str) -> str:
    """Single-name convenience. Ambiguity is a property of the SET, so prefer
    :func:`identity_groups` wherever more than one name is in play."""
    return identity_groups([name])[name]


def analyse(stories: list, *, min_publishers: int = 2) -> dict:
    """Collisions, and what collapsing them would do to the stories that exist."""
    names: dict = {}
    for s in stories:
        for c in s["coverage"]:
            names.setdefault(c["publisher"], 0)
            names[c["publisher"]] += 1

    keys = identity_groups(list(names))
    groups: dict = {}
    for name, n in names.items():
        groups.setdefault(keys[name], []).append((name, n))
    collisions = {k: sorted(v, key=lambda t: -t[1]) for k, v in groups.items() if len(v) > 1}

    shrunk, fake = [], []
    for s in stories:
        raw = {c["publisher"] for c in s["coverage"]}
        true = {keys[p] for p in raw}
        if len(true) < len(raw):
            row = {"title": s["title"], "articles": s["totalCoverage"],
                   "was": len(raw), "now": len(true),
                   "names": sorted(raw)}
            shrunk.append(row)
            if len(true) < min_publishers:
                fake.append(row)

    # A collision where one form resolves and another does not is a MISSING ALIAS — the rating
    # already exists and the row just needs the form the feed actually sends. Cheapest possible fix.
    missing_alias = []
    for key, members in collisions.items():
        resolved = [n for n, _ in members if outlet_registry.resolve(n)]
        unresolved = [n for n, _ in members if not outlet_registry.resolve(n)]
        if resolved and unresolved:
            canonical = outlet_registry.resolve(resolved[0]).canonical
            missing_alias.append({"canonical": canonical, "add": unresolved,
                                  "articles": sum(n for nm, n in members if nm in unresolved)})

    return {
        "names": len(names),
        "identities": len(groups),
        "collisions": sorted(collisions.items(), key=lambda kv: -sum(n for _, n in kv[1])),
        "shrunk": sorted(shrunk, key=lambda r: -r["articles"]),
        "fake": sorted(fake, key=lambda r: -r["articles"]),
        "missingAlias": sorted(missing_alias, key=lambda m: -m["articles"]),
        "stories": len(stories),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=None)
    ap.add_argument("--show", type=int, default=20)
    args = ap.parse_args(argv)

    store_ = store_mod.Store(args.db)
    stories = story_service.build_stories(story_service._fetch(store_))
    res = analyse(stories)

    print(f"catalog: {res['stories']:,} stories")
    print(f"publisher names : {res['names']:,}")
    print(f"real identities : {res['identities']:,}   "
          f"({res['names'] - res['identities']:,} names are duplicates of another)")

    print(f"\nstories whose publisherCount is inflated: {len(res['shrunk']):,}")
    print(f"{'arts':>5} {'was':>4} {'now':>4}  title")
    for r in res["shrunk"][:args.show]:
        print(f"{r['articles']:>5} {r['was']:>4} {r['now']:>4}  {r['title'][:56]}")

    print(f"\n*** stories that are ONE outlet wearing two names: {len(res['fake']):,} ***")
    print("    These cleared min_publishers only because the same masthead was counted twice.")
    for r in res["fake"][:args.show]:
        print(f"{r['articles']:>5} {r['was']:>4} {r['now']:>4}  {r['title'][:44]}  {r['names']}")

    print(f"\nmissing registry aliases (the rating already exists): {len(res['missingAlias']):,}")
    print(f"{'arts':>5}  canonical -> add these forms")
    for m in res["missingAlias"][:args.show]:
        print(f"{m['articles']:>5}  {m['canonical']} -> {'|'.join(m['add'])}")

    print(f"\nbiggest name collisions overall")
    print(f"{'arts':>5}  forms")
    for key, members in res["collisions"][:args.show]:
        total = sum(n for _, n in members)
        print(f"{total:>5}  {' | '.join(f'{n} ({c})' for n, c in members)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
