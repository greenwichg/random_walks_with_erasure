"""select_asif_population.py — pick the ``--as-if`` cohort by rule, not by hand.

Read-only: no writes, no ingestion, no network, no curation. It reads the Tier A window,
applies a **pre-registered** eligibility rule, and prints the outlets that pass plus the
exact `audit_shadow_cohort.py --as-if` command to run next.

## Why this exists

`audit_shadow_cohort.py --as-if` answers the Tier B question offline — *would these
articles have joined a story, had they been allowed to?* — but it takes the cohort as a
literal list of names. Choosing that list by hand is the whole experiment's weak point:
a population picked after glancing at the data can be picked to produce a result, and
nothing in the output would show it. So the rule lives here, in code, ahead of the run.

**The 8 outlets already carrying Tier B verdicts are the wrong population**, which is the
finding that made this script necessary. Six of them are republishers, and the measured
benefit of demoting them was removing 86 title-identical double-counts. Attaching them
back restores exactly the harm the demotion bought — a test guaranteed to look bad for
reasons that have nothing to do with whether Tier B attachment works. What is wanted is a
*legitimate low-volume outlet*: the thing the 50k tail is actually made of.

## The rule

1. **In Tier A** — `story_service._fetch` already excludes Tier B and shadow in SQL. A row
   here that is not Tier A means that exclusion did not hold, and the run aborts rather
   than filtering it away: it would invalidate the premise, not just this cohort.
2. **``MIN_ARTICLES <= articles <= MAX_ARTICLES``** in the 6-day window — enough rows for a
   non-degenerate attach rate, few enough that removing the outlet cannot reshape the story
   set it is then scored against. Low volume *is* the point.
3. **``syndication < source_evaluation.SYNDICATION_CEILING``** — the republisher filter, at
   the policy module's own constant rather than a second number chosen here.
4. **``hostStability >= MIN_HOST_STABILITY``**, over the outlet's OWN hosts — the other
   demotion cause in `source_evaluation.evaluate`. Aggregator hosts are excluded from the
   numerator and kept in the denominator, so an outlet reaching us half through Google News
   scores 50% rather than 100% on a domain that is not its own.
5. **Top host looks like a domain** — kills feed-title artifacts ("google news") without
   filtering on registry membership. Requiring TRACKED would bias the population toward
   majors having a quiet week, which is not the tail this stands in for; tracked/untracked
   is reported as a split instead, so a difference between the two strata is visible as a
   finding rather than hidden as a filter.
6. **A share cap** on the cohort as a whole — see :func:`subsample`. The rule above is about
   which outlets are *suitable*; the cap is about how many can be removed from the corpus at
   once without the rebuilt story set ceasing to resemble production.

## What it emits, and why that spelling

The **raw publisher strings, lower-cased** — every spelling of the identity present in the
window — not the registry canonical. Two reasons, and the second is the load-bearing one:

* an identity can arrive under several publisher strings, and naming one captures only
  that spelling's rows;
* `audit_shadow_cohort.measure` lower-cases what the caller typed, so before the fix in
  this same commit the canonical branch could never match for the 571 of 573 registry
  canonicals that carry capitals. Emitting raw strings means the command this script
  prints is correct against a **currently deployed** image as well as a rebuilt one.

    dc run --rm -T api python examples/select_asif_population.py --db "$RWE_DB_URL"

To run the experiment itself, prefer the one-command form — it calls :func:`cohort_names`
directly, so the list never crosses a shell:

    dc run --rm -T api python examples/audit_shadow_cohort.py --db "$RWE_DB_URL" --as-if-select

This script is then the way to *inspect* the cohort and the exclusion census before or after
that run, rather than a step the run depends on.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from collections import Counter, defaultdict

# Piped in over stdin, so there is no ``__file__`` to hang a path off — that is the point:
# it runs inside the image WITHOUT being baked into it, so the selection rule can change
# without a deploy. The api image is WORKDIR /app with examples/ beside it.
for _p in ("/app/examples", os.path.join(os.getcwd(), "examples"), os.getcwd()):
    if os.path.isfile(os.path.join(_p, "audit_shadow_cohort.py")) and _p not in sys.path:
        sys.path.insert(0, _p)
        break

import audit_shadow_cohort as asc      # noqa: E402
import clustering                      # noqa: E402
import corpus                          # noqa: E402
import outlet_registry                 # noqa: E402
import source_discovery                # noqa: E402
import source_evaluation as se         # noqa: E402
import story_service                   # noqa: E402
import store as store_mod              # noqa: E402

#: Pre-registered before the first run. Changing one is a decision to record, not a knob to
#: turn until the answer improves.
MIN_ARTICLES = 3
MAX_ARTICLES = 20
MAX_SYNDICATION = se.SYNDICATION_CEILING
MIN_HOST_STABILITY = 0.9

#: Cap on the cohort's share of Tier A articles. ``--as-if`` rebuilds the corpus WITHOUT the
#: cohort, so the cohort is also the perturbation — see :func:`subsample`. The first
#: production run qualified 1,058 outlets carrying 21.9% of Tier A, which is far too much to
#: remove and still call the rebuilt story set "production".
MAX_COHORT_SHARE = 0.05


def looks_like_domain(host: str) -> bool:
    """A weak sanity test, not a quality bar. `_host` reads the row's URL, so a real row
    yields a real host; a feed-title artifact yields ``""`` or something with a space."""
    h = (host or "").strip()
    return bool(h) and "." in h and " " not in h and not h.startswith(".")


def profile(tier_a: list, reg) -> list:
    """One dict per outlet identity, carrying every field the rule reads.

    Syndication uses `audit_shadow_cohort.carrier_index` rather than a local duplicate-title
    count, so "syndicated" means here exactly what it means in the audit this feeds."""
    carriers = asc.carrier_index(tier_a)
    resolve = corpus.tier_resolver()        # once per pass, not once per outlet — see `tier_resolver`
    by_id = defaultdict(list)
    for r in tier_a:
        by_id[asc._identity(reg, r)].append(r)

    out = []
    for key, arts in by_id.items():
        # The outlet's OWN hosts. An article ingested through Google News RSS carries
        # `news.google.com`, so counting raw hosts gave several unrelated outlets -- Barron's,
        # Charlotte Observer, Daily Beast -- a top host of `news.google.com` at 100% stability:
        # a filter meant to catch scattered rows passing on a domain that is not the outlet's.
        # `publisher_metadata` already learned this ("an aggregator's domain says who delivered
        # the article, not who wrote it"), so the proxy rule comes from `source_discovery`
        # rather than from a third list here.
        all_hosts = [asc._host(a) for a in arts if asc._host(a)]
        hosts = Counter(h for h in all_hosts if not source_discovery.is_proxy_host(h, reg))
        dup = sum(1 for a in arts
                  if (t := clustering.title_tokens(a.get("title") or "")) and len(carriers[t]) > 1)
        out.append({
            "key": key,
            "articles": len(arts),
            "syndication": dup / max(1, len(arts)),
            "hostStability": (hosts.most_common(1)[0][1] / max(1, len(arts))) if hosts else 0.0,
            "topHost": hosts.most_common(1)[0][0] if hosts else "",
            "ownHosts": len(hosts),
            "proxied": len(all_hosts) - sum(hosts.values()),
            "tracked": reg.resolve(key) is not None,
            "tier": resolve(arts[0].get("publisher"), arts[0].get("url")),
            # Every spelling in the window — see the module docstring on why raw strings.
            "spellings": sorted({(a.get("publisher") or "").strip().lower()
                                 for a in arts if (a.get("publisher") or "").strip()}),
        })
    return out


#: ``(label, predicate)`` per filter, so the run can print each one's own kill count and a
#: surprising eligible count can be read rather than guessed at.
FILTERS = (
    (f"articles < {MIN_ARTICLES}", lambda r: r["articles"] < MIN_ARTICLES),
    (f"articles > {MAX_ARTICLES}", lambda r: r["articles"] > MAX_ARTICLES),
    (f"syndication >= {MAX_SYNDICATION:.0%}", lambda r: r["syndication"] >= MAX_SYNDICATION),
    (f"hostStability < {MIN_HOST_STABILITY:.0%}",
     lambda r: r["hostStability"] < MIN_HOST_STABILITY),
    ("every row aggregator-proxied", lambda r: r["ownHosts"] == 0),
    ("host not a domain", lambda r: not looks_like_domain(r["topHost"])),
    ("a spelling contains a comma", lambda r: any("," in s for s in r["spellings"])),
)


def eligible(rows: list) -> list:
    """The outlets passing every filter, ordered by name — an ordering that cannot correlate
    with the outcome, unlike ranking by volume or by anything the audit will measure."""
    keep = [r for r in rows if not any(fn(r) for _label, fn in FILTERS)]
    return sorted(keep, key=lambda r: r["key"])


def _draw_order(key: str) -> str:
    """A stable pseudo-random order over outlet names, for :func:`subsample`.

    **Name order is neutral for listing and wrong for truncating.** Taking a prefix of an
    alphabetical list does not take a sample of the population — it takes everything whose
    name begins with a digit or a Latin letter early in the alphabet, and drops the Cyrillic,
    Greek, Arabic and CJK names entirely. On this corpus that is a language filter wearing a
    sampling filter's clothes, and the resulting attach rate would describe one part of the
    catalogue while claiming to describe the tail.

    Hashing the identity is deterministic (same cohort every run, so the experiment is
    repeatable), independent of every quantity the audit measures, and blind to script."""
    return hashlib.sha1(key.encode("utf-8")).hexdigest()


def subsample(picked: list, corpus_articles: int, share: float) -> list:
    """The largest hash-ordered prefix of ``picked`` whose articles stay within ``share``.

    **Why a cap at all.** ``--as-if`` rebuilds Tier A *without* the cohort, so the cohort is
    also the perturbation. Remove a fifth of the corpus and the story set the cohort is then
    scored against is not the production one: stories carried by two cohort outlets vanish
    entirely (``min_publishers = 2``), and their articles cannot attach to a story that no
    longer exists. A low attach rate would then be unreadable — the corpus was gutted, or
    Tier B recovers nothing, and the run cannot tell those apart. Capping the share keeps the
    rebuilt story set close enough to production that the number means what it says.

    Returned in name order, like :func:`eligible`; only the *selection* uses hash order."""
    budget = share * max(1, corpus_articles)
    total, taken = 0, []
    for r in sorted(picked, key=lambda r: _draw_order(r["key"])):
        if total + r["articles"] > budget:
            continue          # skip, don't stop: a big outlet must not truncate the draw
        taken.append(r)
        total += r["articles"]
    return sorted(taken, key=lambda r: r["key"])


def cohort_names(tier_a: list, reg, *, share: float = MAX_COHORT_SHARE) -> set:
    """The cohort as a set of ``--as-if`` names, from rows the caller already fetched.

    **The seam that removes the copy-paste step.** Printing a 254-name list for a human to
    paste into a second command is a step that can go wrong, and did: two production runs were
    spent on a placeholder string that reached the shell verbatim. Both times the audit's
    unmatched-name guard caught it and refused to report — but a guard firing twice on the
    same cause is an argument for removing the cause. `audit_shadow_cohort --as-if-select`
    calls this instead, so the names never leave the process.

    Takes ``tier_a`` rather than a store so the corpus is fetched once, by the caller."""
    return {s for r in subsample(eligible(profile(tier_a, reg)), len(tier_a), share)
            for s in r["spellings"]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=os.environ.get("RWE_DB_URL"))
    ap.add_argument("--share", type=float, default=MAX_COHORT_SHARE,
                    help="cap the cohort at this share of Tier A articles — see subsample()")
    args = ap.parse_args(argv)

    st = store_mod.Store(args.db)
    reg = outlet_registry.default_registry()

    tier_a = story_service._fetch(st)
    rows = profile(tier_a, reg)

    off_tier = [r for r in rows if r["tier"] != "A"]
    if off_tier:
        print(f"*** {len(off_tier)} NON-TIER-A OUTLET(S) IN THE TIER A FETCH — the SQL "
              f"exclusion did not hold; selection aborted")
        for r in off_tier[:10]:
            print(f"    {r['key']}  tier={r['tier']}")
        return 2

    qualified = eligible(rows)
    q_total = sum(r["articles"] for r in qualified)
    picked = subsample(qualified, len(tier_a), args.share)
    total = sum(r["articles"] for r in picked)
    tracked = sum(1 for r in picked if r["tracked"])

    print(f"Tier A window : {len(tier_a):,} articles across {len(rows):,} outlets")
    print(f"rule          : {MIN_ARTICLES}-{MAX_ARTICLES} articles, syndication < "
          f"{MAX_SYNDICATION:.0%}, hostStability >= {MIN_HOST_STABILITY:.0%}, host is a domain")
    print(f"qualified     : {len(qualified):,} outlets, {q_total:,} articles "
          f"({q_total / max(1, len(tier_a)):.1%} of Tier A)")
    print(f"cohort        : {len(picked):,} outlets, {total:,} articles "
          f"({total / max(1, len(tier_a)):.1%} of Tier A, cap {args.share:.0%}) — "
          f"{tracked} registry-tracked, {len(picked) - tracked} untracked")
    print(f"                hash-ordered draw over the qualified set — deterministic, and "
          f"blind to script in a way an alphabetical prefix is not")

    print("\nwhy outlets were excluded (each test applied alone):")
    for label, fn in FILTERS:
        print(f"  {label:<28} {sum(1 for r in rows if fn(r)):>5}")

    print(f"\n{'outlet':<40} {'arts':>5} {'synd':>6} {'stab':>6}  {'reg':<4} host")
    for r in picked[:60]:
        print(f"{r['key']:<40} {r['articles']:>5} {r['syndication']:>6.0%} "
              f"{r['hostStability']:>6.0%}  {'yes' if r['tracked'] else 'no':<4} {r['topHost']}")
    if len(picked) > 60:
        print(f"... and {len(picked) - 60} more")

    # An empty list must NOT print a runnable command. `--as-if ""` parses to an empty set,
    # which falls back to the DEFAULT shadow-lane run — a different question whose output
    # looks like an answer to this one. Same shape as the placeholder that printed
    # INCOMPLETE: a cohort that cannot be evaluated must not produce a command to evaluate it.
    if not picked:
        print("\n*** EMPTY COHORT — nothing to run. Either no outlet met the rule, or --share "
              "is smaller than the smallest qualifying outlet. Widen the band or raise the cap "
              "deliberately and say so; do not run --as-if with an empty list, it silently "
              "becomes the default shadow-lane run.")
        return 1

    names = ",".join(s for r in picked for s in r["spellings"])
    print("\n--- run this next ---")
    print('dc run --rm -T api python examples/audit_shadow_cohort.py --db "$RWE_DB_URL" \\')
    print(f'    --as-if "{names}"')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
