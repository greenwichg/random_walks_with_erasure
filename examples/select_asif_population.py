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
4. **``hostStability >= MIN_HOST_STABILITY``** — the other demotion cause in
   `source_evaluation.evaluate`.
5. **Top host looks like a domain** — kills feed-title artifacts ("google news") without
   filtering on registry membership. Requiring TRACKED would bias the population toward
   majors having a quiet week, which is not the tail this stands in for; tracked/untracked
   is reported as a split instead, so a difference between the two strata is visible as a
   finding rather than hidden as a filter.

## What it emits, and why that spelling

The **raw publisher strings, lower-cased** — every spelling of the identity present in the
window — not the registry canonical. Two reasons, and the second is the load-bearing one:

* an identity can arrive under several publisher strings, and naming one captures only
  that spelling's rows;
* `audit_shadow_cohort.measure` lower-cases what the caller typed, so before the fix in
  this same commit the canonical branch could never match for the 571 of 573 registry
  canonicals that carry capitals. Emitting raw strings means the command this script
  prints is correct against a **currently deployed** image as well as a rebuilt one.

    dc run --rm -T api python - < examples/select_asif_population.py --db "$RWE_DB_URL"
"""

from __future__ import annotations

import argparse
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
import source_evaluation as se         # noqa: E402
import story_service                   # noqa: E402
import store as store_mod              # noqa: E402

#: Pre-registered before the first run. Changing one is a decision to record, not a knob to
#: turn until the answer improves.
MIN_ARTICLES = 3
MAX_ARTICLES = 20
MAX_SYNDICATION = se.SYNDICATION_CEILING
MIN_HOST_STABILITY = 0.9

#: Above this share of Tier A, removing the cohort perturbs the story set it is scored
#: against, and the attach rate stops being a clean read.
MAX_COHORT_SHARE = 0.10


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
    by_id = defaultdict(list)
    for r in tier_a:
        by_id[asc._identity(reg, r)].append(r)

    out = []
    for key, arts in by_id.items():
        hosts = Counter(asc._host(a) for a in arts if asc._host(a))
        dup = sum(1 for a in arts
                  if (t := clustering.title_tokens(a.get("title") or "")) and len(carriers[t]) > 1)
        out.append({
            "key": key,
            "articles": len(arts),
            "syndication": dup / max(1, len(arts)),
            "hostStability": (hosts.most_common(1)[0][1] / max(1, len(arts))) if hosts else 0.0,
            "topHost": hosts.most_common(1)[0][0] if hosts else "",
            "tracked": reg.resolve(key) is not None,
            "tier": corpus.tier_of(arts[0].get("publisher"), arts[0].get("url")),
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
    ("host not a domain", lambda r: not looks_like_domain(r["topHost"])),
    ("a spelling contains a comma", lambda r: any("," in s for s in r["spellings"])),
)


def eligible(rows: list) -> list:
    """The outlets passing every filter, ordered by name — an ordering that cannot correlate
    with the outcome, unlike ranking by volume or by anything the audit will measure."""
    keep = [r for r in rows if not any(fn(r) for _label, fn in FILTERS)]
    return sorted(keep, key=lambda r: r["key"])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=os.environ.get("RWE_DB_URL"))
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

    picked = eligible(rows)
    total = sum(r["articles"] for r in picked)
    tracked = sum(1 for r in picked if r["tracked"])

    print(f"Tier A window : {len(tier_a):,} articles across {len(rows):,} outlets")
    print(f"rule          : {MIN_ARTICLES}-{MAX_ARTICLES} articles, syndication < "
          f"{MAX_SYNDICATION:.0%}, hostStability >= {MIN_HOST_STABILITY:.0%}, host is a domain")
    print(f"eligible      : {len(picked)} outlets, {total:,} articles "
          f"({total / max(1, len(tier_a)):.1%} of Tier A) — "
          f"{tracked} registry-tracked, {len(picked) - tracked} untracked")

    print("\nwhy outlets were excluded (each test applied alone):")
    for label, fn in FILTERS:
        print(f"  {label:<28} {sum(1 for r in rows if fn(r)):>5}")

    if total > MAX_COHORT_SHARE * max(1, len(tier_a)):
        print(f"\n*** the cohort is over {MAX_COHORT_SHARE:.0%} of Tier A — removing it perturbs "
              f"the story set it is scored against; lower MAX_ARTICLES before trusting the result")

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
        print("\n*** NO OUTLET MET THE RULE — nothing to run. Widen the band or relax a filter "
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
