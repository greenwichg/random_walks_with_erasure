"""corpus.py — the clustering corpus boundary.

**M1 of `docs/SCALE_ROADMAP.md`.** The clustering corpus stops being "whatever ``_fetch`` returned"
and becomes an explicitly *selected* projection, with a name, a policy, and a budget that says so
out loud when it binds.

## The two things this closes

**1. A silent truncation that turns more sources into fewer stories.**

``story_service._fetch`` bounds the candidate set by a 6-day time window *and* by
``RWE_STORIES_MAX_SCAN`` rows, newest-first. Its own docstring records what happened the last time
that row cap bound, at 2,000:

    "every provider added shrank the hours those 2000 rows covered, so integrating more sources
    produced FEWER stories (measured: a 12.5-hour effective window against a 6-day clustering
    threshold, 89 stories from a 12,790-article catalog)"

The cap was raised to 60,000, which at today's ~4,650 articles/day covers 12.9 days and never
binds. At 150k/day it covers **9.6 hours**; at 500k/day, **2.9 hours**. The defect is not that a
bound exists — it is that hitting it emits nothing, and its only symptom is fewer stories, which
reads as a clustering regression rather than a bound being hit.

``search_feed_articles`` has always returned ``(rows, total)`` and ``_fetch`` has always discarded
the total. **The evidence was already in the caller's hands and thrown away**, which is the same
shape as `PERFORMANCE.md`'s retention finding: "a ``deleted: 0`` line and a ``74,500 ms`` line
describe the same event. Only one of them was ever printed."

**2. There was no name for "the articles that are allowed to form stories."**

`CORPUS_ARCHITECTURE.md` defines ① Full/Searchable, ② Recommendation and ③ Reads, and has Stories
reading ① *directly*. So the clustering corpus was whatever the fetch happened to return, and there
was nowhere to stand to say "this outlet is searchable but does not form stories" — which is
precisely what shadow ingest, promotion and retirement all need. This module is that place: a new
projection ②′, the same shape of boundary as ②, with the same kind of guardrail test.

## Tiers

``A``       forms and votes in stories. Bounded — see :func:`tier_a_budget`.
``B``       searchable and attributable; **never enters the story builder**.
``shadow``  stored and attributed, surfaced nowhere, pending evaluation.

**Tier is a property of the OUTLET, not of the article** — "does this publisher form stories" is a
fact about a publisher. Deriving the article's tier from its resolved outlet means there is no
migration, no backfill and no possibility of two articles from one outlet disagreeing; and a
demotion (A→B when an outlet turns out to be a syndicator) takes effect on the next build over the
outlet's whole history, which is what a demotion should mean.

The source of truth is an env list today, matching ``RWE_CATALOG_BLOCKED_OUTLETS``. That is the
right home for M1 and the wrong home for 50,000 outlets; :func:`tier_of` is the seam, so moving the
declaration to a registry column or its own table later changes no caller.

## Off is byte-identical, structurally

With neither tier list set, :func:`select` performs **no registry resolution and no per-row work**,
and returns the list it was handed. Not "returns an equal list" — the same object. The budget
report still runs, because that half is the defect fix and it must not be switchable off by
accident. Same discipline as ``RWE_FEED_SCHEDULER``: the feature is off, the instrument is not.
"""

from __future__ import annotations

import functools
import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

import obs_metrics
import outlet_registry
from outlet_registry import default_registry

_logger = logging.getLogger("hidden_view.corpus")

#: The tiers, in order of decreasing privilege. ``A`` is the default for everything, which is what
#: makes turning this on a no-op — see the module docstring.
TIERS = ("A", "B", "shadow")

#: What an outlet is when nothing says otherwise. Grandfathering, deliberately: every outlet the
#: catalog already carries is in the clustering corpus today, and M1's job is to install the
#: boundary, not to move anyone across it. Moving an outlet is a measured decision with its own
#: counterfactual, exactly like every clustering knob in this repo.
DEFAULT_TIER = "A"

#: Articles allowed in the 6-day Tier A window before the build stops fitting its poll cycle.
#:
#: Derived in `docs/SCALE_ROADMAP.md` from the live pipeline profile — ``_fetch`` 2,319 ms linear,
#: ``cluster`` 1,942 ms at exponent 2.05, ``_merge_duplicates`` 1,451 ms quadratic in CLUSTER count,
#: at 22,493 articles. Holding the build to ~60 s (25% of a 600 s cycle's 240 sustainable
#: vCPU-seconds on a t3.medium) puts the ceiling near 83,000 articles, about 3x today's corpus.
#:
#: This is a WARNING threshold, not a gate. Nothing is dropped for exceeding it; the operator is
#: told, because crossing it is the signal that Tier A needs trimming (M2) or that incremental
#: clustering has become necessary (M10). A conservative value is the safe error direction for a
#: warning, and the fit it comes from overstates the measured build by ~35% at k=1.
DEFAULT_TIER_A_BUDGET = 83_000

#: Outlets assigned to a tier other than the default, comma-separated. Each entry is resolved
#: through the registry FIRST, so a value moves the outlet's IDENTITY — every alias and every domain
#: the registry knows for it — rather than the one string somebody happened to type. An entry the
#: registry does not know is treated as a domain and matched subdomain-tolerantly.
_TIER_B_ENV = "RWE_CORPUS_TIER_B"
_SHADOW_ENV = "RWE_CORPUS_SHADOW"
_BUDGET_ENV = "RWE_CORPUS_TIER_A_BUDGET"


# --------------------------------------------------------------------------- #
# Policy
# --------------------------------------------------------------------------- #
def _setting(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def tier_a_budget() -> int:
    try:
        v = int(_setting(_BUDGET_ENV))
        return v if v > 0 else DEFAULT_TIER_A_BUDGET
    except (TypeError, ValueError):
        return DEFAULT_TIER_A_BUDGET


def enabled() -> bool:
    """Whether any outlet has been assigned away from :data:`DEFAULT_TIER`.

    False is the shipped state and means :func:`select` short-circuits the tier filter entirely.
    The budget report runs either way."""
    return bool(_setting(_TIER_B_ENV) or _setting(_SHADOW_ENV))


@functools.lru_cache(maxsize=8)
def _index(setting: str) -> tuple:
    """One setting string -> ``(canonical names, hosts)``.

    Keyed on the setting string, so an operator or a test changing the env re-parses instead of
    being served a memo of the old value. Bounded at 8 because the key space is operator-controlled
    and small.

    (``ingest._blocked_index`` is the same shape and predates this. Converging them is a real
    tidy-up and a separate change: that one sits in the ingest hot path with its own measurement
    history, and folding it into a new module during M1 would put an unmeasured edit under a
    byte-identical bar that is about something else.)"""
    canonicals, hosts = set(), set()
    for entry in setting.split(","):
        entry = entry.strip()
        if not entry:
            continue
        outlet = default_registry().resolve(entry)
        if outlet is not None:
            canonicals.add(outlet.canonical)
        elif "." in entry:                      # unknown to the registry -> treat it as a domain
            host = outlet_registry._host_of(entry)
            if host:
                hosts.add(host)
    return frozenset(canonicals), frozenset(hosts)


def tier_index() -> dict:
    """What the current settings were understood to mean, per tier.

    Exposed for the same reason ``ingest.blocked_catalog_index`` is: a misspelling, or an
    unregistered outlet named rather than domained, silently matches nothing — and a tier list that
    quietly does nothing is the worst way to find that out."""
    return {"B": _index(_setting(_TIER_B_ENV)), "shadow": _index(_setting(_SHADOW_ENV))}


def _matches(index: tuple, publisher: "str | None", url: "str | None") -> bool:
    """Two-sided identity match, the same rule ``ingest.is_blocked_from_catalog`` uses and for the
    same measured reason: 499 of 671 obituary articles arrive under the parent MASTHEAD's name with
    an ``obits.*`` URL, so resolving only the name lets them through under an identity that is not
    theirs. ``story_service`` has always tested wire membership two-sided
    (``is_wire(publisher) or is_wire_url(url)``); this matches."""
    canonicals, hosts = index
    if canonicals:
        reg = default_registry()
        for text in (publisher, url):
            if not text:
                continue
            outlet = reg.resolve(text)
            if outlet is not None and outlet.canonical in canonicals:
                return True
    if hosts:
        host = outlet_registry._host_of(url or "")
        if host and any(host == h or host.endswith("." + h) for h in hosts):
            return True
    return False


def _tier_with(idx: dict, publisher: "str | None", url: "str | None") -> str:
    """The rule, against an index the caller already holds.

    Split out from :func:`tier_of` so the row loop in :func:`select` reads the environment and
    builds the index ONCE rather than twice per article. That is the same waste the registry memo
    fixed — 60,400 resolve calls over 400 distinct strings, 10% of a whole build — and it is easier
    to not introduce than to find later.

    ``shadow`` is tested before ``B`` so an outlet named in both lands in the more restrictive one:
    a conflicting configuration should fail toward less exposure, not more."""
    if _matches(idx["shadow"], publisher, url):
        return "shadow"
    if _matches(idx["B"], publisher, url):
        return "B"
    return DEFAULT_TIER


def tier_of(publisher: "str | None", url: "str | None" = None) -> str:
    """This article's tier, derived from its outlet's identity."""
    if not enabled():
        return DEFAULT_TIER
    return _tier_with(tier_index(), publisher, url)


# --------------------------------------------------------------------------- #
# The selector
# --------------------------------------------------------------------------- #
def _parse(value) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _hours(a: Optional[datetime], b: Optional[datetime]) -> Optional[float]:
    if a is None or b is None:
        return None
    return round((a - b).total_seconds() / 3600.0, 2)


def _default_log(level: int, event: str, **fields) -> None:
    _logger.log(level, json.dumps({"event": event, **fields}, default=str))


def select(rows: list, *, total: "int | None" = None, cap: "int | None" = None,
           window_start: "str | None" = None, log=None,
           report_out: "dict | None" = None) -> list:
    """The clustering corpus: the Tier A rows of ``rows``, with a report of what bound.

    ``rows`` is the SQL slice as returned by ``store.search_feed_articles`` — already time-windowed
    and already truncated at ``cap`` rows, newest-first. ``total`` is that call's pre-pagination
    count, which is what makes the truncation detectable at all.

    Returns the kept rows. Fills ``report_out`` when given, matching the ``veto_stats`` /
    ``band_out`` sink convention ``build_stories`` already uses, so the caller stays a one-liner.

    **The order is honest about what it can and cannot do.** The positional cap is applied in SQL,
    upstream of the tier filter, so once Tier B has members their rows still count against the cap
    before this function ever sees them. Pushing the tier predicate into SQL is M2; ``report_out``
    carries ``capBoundBeforeTier`` so nobody has to infer it.

    Two conditions are reported LOUDLY, at WARNING, because both are silent today:

    * ``clustering_corpus_cap_bound`` — the row cap truncated the requested time window. The report
      names the window actually achieved, in hours, against the one asked for.
    * ``clustering_corpus_over_budget`` — Tier A is past :func:`tier_a_budget`, the size at which
      the build stops fitting its poll cycle. Nothing is dropped; the operator is told.
    """
    emit = log or _default_log
    cap = cap or 0
    kept = rows
    dropped = {"B": 0, "shadow": 0}

    if enabled() and rows:
        idx = tier_index()                      # once for the whole corpus, not once per row
        kept = []
        for r in rows:
            t = _tier_with(idx, r.get("publisher"), r.get("canonicalUrl") or r.get("url"))
            if t == DEFAULT_TIER:
                kept.append(r)
            else:
                dropped[t] = dropped.get(t, 0) + 1
        excluded = len(rows) - len(kept)
        if excluded:
            obs_metrics.incr("clustering_corpus_excluded_total", excluded)

    newest = _parse(rows[0].get("publishedAt")) if rows else None
    oldest = _parse(rows[-1].get("publishedAt")) if rows else None
    requested = _parse(window_start)
    budget = tier_a_budget()
    # `total` is the count BEFORE pagination, so `total > cap` is the truncation, exactly. It is
    # not inferred from `len(rows) == cap`, which would also fire on a window that happens to hold
    # exactly `cap` rows and would report a breach that did not occur.
    cap_bound = bool(cap and total is not None and total > cap)

    report = {
        "window": total,                       # rows the time window matched, before the cap
        "scanned": len(rows),                  # rows the cap let through
        "kept": len(kept),                     # the clustering corpus
        "droppedTierB": dropped["B"],
        "droppedShadow": dropped["shadow"],
        "tiering": enabled(),
        "cap": cap or None,
        "budget": budget,
        "capBound": cap_bound,
        "overBudget": len(kept) > budget,
        # Which of the two bounds is the operative one. Today the "memory backstop" (60,000) sits
        # BELOW the CPU budget (83,000), so the backstop is the binding constraint and the budget
        # warning cannot fire — worth printing rather than leaving to be discovered.
        "binding": ("cap" if cap and cap < budget else "budget"),
        "capBoundBeforeTier": cap_bound and enabled(),
        "requestedFrom": window_start,
        "effectiveFrom": oldest.isoformat() if oldest else None,
        "requestedWindowHours": _hours(newest, requested),
        "effectiveWindowHours": _hours(newest, oldest),
    }

    if cap_bound:
        obs_metrics.incr("clustering_corpus_cap_bound_total")
        emit(logging.WARNING, "clustering_corpus_cap_bound",
             window=total, cap=cap, dropped=total - cap,
             requestedFrom=window_start, effectiveFrom=report["effectiveFrom"],
             requestedWindowHours=report["requestedWindowHours"],
             effectiveWindowHours=report["effectiveWindowHours"],
             capBoundBeforeTier=report["capBoundBeforeTier"],
             detail=("the row cap truncated the clustering window; story yield now tracks "
                     "ingestion RATE, so adding sources will produce FEWER stories"))
    if report["overBudget"]:
        obs_metrics.incr("clustering_corpus_over_budget_total")
        emit(logging.WARNING, "clustering_corpus_over_budget",
             kept=len(kept), budget=budget,
             detail=("Tier A is past the size at which the story build fits its poll cycle; "
                     "trim Tier A (M2) or make the build incremental (M10)"))

    if report_out is not None:
        report_out.clear()
        report_out.update(report)
    return kept
