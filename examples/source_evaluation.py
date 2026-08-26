"""source_evaluation.py — Stage 4 of the source pipeline: what a shadow outlet is worth.

**M8 of `docs/SCALE_ROADMAP.md`.** Pure policy and pure measurement: no store, no network, no
environment, no writes. It takes catalog rows and a built story set and returns numbers and a
recommended verdict. Nothing here acts on that verdict — acting is M9.

## The problem M8 has to solve, which M5 created

A shadow outlet is, by definition, excluded from the story builder. So the metric the cohort audit
leans on — *story participation*, the share of an outlet's articles that reach an admitted story —
**cannot be measured for it at all**. It is structurally zero, for every shadow outlet, forever.

That is not a defect in shadow; it is what shadow is for. But it means evaluation has to ask a
counterfactual question instead of an observational one:

> **Would this article have joined a story, had it been allowed to?**

That is the same question the clusterer answers, so it is answered with the clusterer's own rule —
``clustering.pair_admits``, extracted from ``cluster``'s inner ``pair_ok`` precisely so there is one
definition of "same event" rather than two that drift. A second implementation here would be the
fourth key-convention drift this audit series has had to correct.

## What it measures

``observed_days``     how long the catalog has been seeing the outlet, from ``createdAt`` — NOT
                      ``publishedAt``, because backfilling providers insert articles published days
                      earlier and that would measure the news cycle rather than our observation.
``freshness_hours``   median lag from publication to fetch: an outlet that only carries stale items
                      adds coverage nobody can act on.
``assignment_rate``   the share of its articles that WOULD attach to an existing Tier A story.
``syndication``       share of headlines that also run under another publisher.
``host_stability``    share of articles on the outlet's own main host.

## What it deliberately does NOT gate on

``assignment_rate`` is **reported and never gated**, because no bar for it has been measured. The
temptation is to pick one — "promote above 20%" — and this audit series has now had two invented
thresholds die against data (participation as a quality proxy, then peer count as its excuse). A
third guess would be a worse mistake for having watched the first two fail.

Tier A promotion likewise cannot be decided here: it needs the clustering counterfactual on the
production bars, which is a whole-corpus measurement, not a per-outlet one. :func:`evaluate` says
``TIER A CANDIDATE`` and names the run that would settle it.
"""

from __future__ import annotations

import statistics
from datetime import datetime, timezone
from typing import Optional

import clustering

#: Days of observation before a verdict means anything. The roadmap's Stage 3 minimum: long enough
#: to measure a publish RATE rather than a burst, and to catch an outlet that files heavily for a
#: week and then stops.
OBSERVATION_DAYS = 14

#: Articles in the window below which an outlet is not worth a verdict — the measured floor from the
#: offline validation prefilter: 3,442 of 4,083 identities sat below it with a MEDIAN of one article.
VOLUME_FLOOR = 10

#: Share of headlines that may also run under another publisher before the outlet reads as a
#: republisher. Carried from the cohort evaluation, where it correctly caught `the brunswick news`
#: at 50% and `Brisbane Times` at 42% while flagging no genuine newsroom.
SYNDICATION_CEILING = 0.35

#: Share of articles that must sit on the outlet's own main host. Below this we cannot say who
#: published them — `iHeartRadio` measured 6%, meaning 94% of its articles were on other domains.
HOST_STABILITY_FLOOR = 0.5


def _parse(value) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def observed_days(rows: list, *, now: Optional[datetime] = None) -> Optional[float]:
    """How long the catalog has been seeing this outlet, in days, or ``None`` when undatable.

    From ``createdAt`` — when the row landed — never ``publishedAt``. GDELT and other backfilling
    providers insert articles published days earlier, so a published-at span measures the news cycle
    rather than our observation of the source. Same distinction `capacity_report.ingestion_rate`
    makes, and for the same reason."""
    seen = [d for d in (_parse(r.get("createdAt")) for r in rows) if d]
    if not seen:
        return None
    return round(((now or datetime.now(timezone.utc)) - min(seen)).total_seconds() / 86400.0, 2)


def freshness_hours(rows: list) -> Optional[float]:
    """Median hours from publication to fetch, or ``None`` when no row carries both timestamps.

    ``None`` is returned rather than 0 so a missing signal is never read as a perfect one — the
    fail-honest rule this repo applies to every other absent measurement."""
    lags = []
    for r in rows:
        pub, got = _parse(r.get("publishedAt")), _parse(r.get("fetchedAt"))
        if pub and got:
            lags.append((got - pub).total_seconds() / 3600.0)
    return round(statistics.median(lags), 2) if lags else None


def assignment_index(stories: list) -> tuple:
    """``(members, postings)`` over Tier A story members — the structure assignment tests against.

    Blocked by an inverted token index, exactly as ``clustering.cluster`` blocks its own candidate
    generation, and for the same reason it is exact rather than approximate: ``jaccard(a,b) >= sim``
    for any ``sim > 0`` requires at least one shared token, so a member sharing none can never match
    and is safe to skip. Without it this is |shadow articles| x |story members| pair tests."""
    members, postings = [], {}
    for s in stories:
        for c in s.get("coverage", ()):
            toks = clustering.title_tokens(c.get("headline") or "")
            if len(toks) < clustering.MIN_TITLE_TOKENS:
                continue
            i = len(members)
            members.append((toks, _parse(c.get("publishedAt")), s.get("id")))
            for t in toks:
                postings.setdefault(t, []).append(i)
    return members, postings


def would_attach(title: str, published_at, index: tuple) -> Optional[str]:
    """The id of the story this article would join, or ``None``.

    Deterministic: candidates are visited in ascending member order, so an article matching members
    of several stories always reports the same one. A frozenset iteration here would make the answer
    vary between runs on identical input, which would quietly break every before/after comparison
    built on it."""
    members, postings = index
    toks = clustering.title_tokens(title or "")
    if len(toks) < clustering.MIN_TITLE_TOKENS:
        return None
    when = _parse(published_at)
    for i in sorted({i for t in toks for i in postings.get(t, ())}):
        mtoks, mtime, sid = members[i]
        if clustering.pair_admits(toks, mtoks, when, mtime):
            return sid
    return None


def assignment_rate(rows: list, index: tuple) -> dict:
    """``{articles, attached, rate, stories}`` — how much of an outlet's output would land.

    ``stories`` is the count of DISTINCT stories it would touch, which separates an outlet feeding
    one running story from one covering the spread."""
    hits = [would_attach(r.get("title"), r.get("publishedAt"), index) for r in rows]
    landed = [h for h in hits if h]
    return {"articles": len(rows), "attached": len(landed),
            "rate": round(len(landed) / max(1, len(rows)), 4),
            "stories": len(set(landed))}


def evaluate(stats: dict) -> "tuple[str, str]":
    """``(verdict, reason)`` for one shadow outlet. Evidence, never an action — M9 acts.

    Order matters: the disqualifying facts are read before the promoting ones, so an outlet that
    both republishes heavily AND would attach everywhere reads as a republisher rather than as a
    strong candidate. Its attachments are other publishers' coverage counted twice."""
    observed = stats.get("observedDays")
    if observed is not None and observed < OBSERVATION_DAYS:
        return "INSUFFICIENT DATA", (
            f"observed {observed:.1f}d of the {OBSERVATION_DAYS}d minimum — not a rejection, "
            f"a verdict that cannot be reached yet")
    if stats.get("articles", 0) < VOLUME_FLOOR:
        return "INSUFFICIENT VOLUME", (
            f"{stats.get('articles', 0)} articles, below the {VOLUME_FLOOR}-article floor")
    if stats.get("syndication", 0.0) > SYNDICATION_CEILING:
        return "REJECT", (f"{stats['syndication']:.0%} of headlines also run under another "
                          f"publisher — a republisher, and its attachments would double-count")
    if stats.get("hostStability", 1.0) < HOST_STABILITY_FLOOR:
        return "REJECT", (f"only {stats['hostStability']:.0%} of articles on its main host — "
                          f"we cannot say who published them")
    if stats.get("rated"):
        return "TIER A CANDIDATE", (
            f"carries a lean and would attach to {stats.get('assignmentStories', 0)} stories "
            f"({stats.get('assignmentRate', 0.0):.0%} of its articles) — run the clustering "
            f"counterfactual before promoting; this function cannot decide it")
    return "PROMOTE TO TIER B", (
        f"passes every automatic gate; unrated, so it is searchable and attributable and does not "
        f"vote. Tier B cannot alter the partition, which is why this needs no counterfactual")
