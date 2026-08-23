"""story_intelligence.py — deterministic intelligence computed ON TOP of Story objects.

A strict **consumer** of the Story Service: it reads a Story (from ``story_service.get_story``) and the
reader's existing reads, and derives freshness, lifecycle, momentum, coverage statistics, an expanded
timeline, "new since your last visit", and informational alerts. It computes everything from the
Story's existing fields + the browser-extension reads — no clustering, no recommender, no AI, no new
read tracking.

Dependency graph (never the reverse): ``FeedArticle → Story Service → Story Intelligence``. This
module imports ``story_service`` (only in the orchestrator); ``story_service`` must never import this.

The computations are decomposed into focused, independently-testable functions:
``compute_freshness``, ``compute_momentum``, ``compute_lifecycle``, ``compute_coverage_statistics``,
``compute_timeline``, ``compute_new_since_last_visit``, ``compute_alerts`` — assembled by
``compute_summary`` (the lightweight card badge) and ``compute_intelligence`` (the full response).
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Optional

_BUCKET_ORDER = ("left", "center", "right")


# --------------------------------------------------------------------------- #
# Config + small helpers
# --------------------------------------------------------------------------- #
def _int_env(name: str, default: int) -> int:
    v = os.environ.get(name)
    try:
        return int(v) if v not in (None, "") else default
    except ValueError:
        return default


def _milestones_env(name: str, default) -> tuple:
    v = os.environ.get(name)
    if not v:
        return tuple(default)
    vals = tuple(sorted({int(x) for x in v.split(",") if x.strip().lstrip("-").isdigit()}))
    return vals or tuple(default)


def thresholds_from_env() -> dict:
    """Configurable freshness / lifecycle / momentum thresholds (hours) + milestone article counts."""
    return {
        # Default 6 (was 3): the Breaking audit measured cross-outlet confirmation latency at
        # 3–6h on this catalog's cadence — textbook breaking stories (a sinking bulk carrier at
        # 7 outlets, a fatal collision at 6) missed the badge because outlet #2 landed after 3h.
        "breakingHours": _int_env("RWE_STORY_BREAKING_HOURS", 6),
        "developingHours": _int_env("RWE_STORY_DEVELOPING_HOURS", 12),
        "activeHours": _int_env("RWE_STORY_ACTIVE_HOURS", 48),
        "coolingHours": _int_env("RWE_STORY_COOLING_HOURS", 168),
        "momentumWindowHours": _int_env("RWE_STORY_MOMENTUM_WINDOW_HOURS", 24),
        # Recalibration (2026-08-23 production audit): "Developing" additionally requires the
        # CLUSTER to be young, or a genuine recent burst — otherwise a week-old story kept
        # "Developing" forever by one syndicated drip per day (35 of 114 Developing/Breaking
        # badges in the audited window sat on 3-day-plus clusters).
        "developingMaxStoryAgeHours": _int_env("RWE_STORY_DEVELOPING_MAX_AGE_HOURS", 72),
        "developingMinRecent": _int_env("RWE_STORY_DEVELOPING_MIN_RECENT", 3),
        # Breaking's own audit (same day, the V4 recalibration): the badge's cluster floor reads
        # the NOTIFIER's env var deliberately — `story_events.min_publishers` wrote down why a
        # single-source "breaking" is a rumour, and one knob moving both surfaces is the point.
        # No import in either direction; the shared name is the coupling.
        "breakingMinPublishers": _int_env("RWE_BREAKING_MIN_PUBLISHERS", 3),
        "milestones": _milestones_env("RWE_STORY_MILESTONES", (2, 5, 10, 20)),
    }


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(iso) -> Optional[datetime]:
    s = (iso or "").strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _hours_since(iso, now: datetime) -> Optional[float]:
    dt = _parse(iso)
    return None if dt is None else (now - dt).total_seconds() / 3600.0


def _coverage_sorted(story: dict) -> list:
    """The story's coverage articles, oldest → newest (deterministic; url breaks ties)."""
    return sorted(story.get("coverage") or [],
                  key=lambda c: (c.get("publishedAt") or "", c.get("url") or ""))


def _count_between(cov: list, now: datetime, lo_h: float, hi_h: float) -> int:
    n = 0
    for c in cov:
        h = _hours_since(c.get("publishedAt"), now)
        if h is not None and lo_h < h <= hi_h:
            n += 1
    return n


# --------------------------------------------------------------------------- #
# Freshness / momentum / lifecycle
# --------------------------------------------------------------------------- #
def compute_freshness(story: dict, *, now: datetime, th: dict) -> dict:
    """A freshness band (Breaking / Developing / Active / Cooling / Archived) + a 0–100 score, from the
    latest publication's age, recent velocity, and a burst check.

    Recalibrated against the 2026-08-23 production audit (1,582 stories; 377 badges contradicted
    by their own coverage — docs figure lives with the band audit), and, same day, Breaking's own
    audit (the V4 recalibration):

    * **Breaking = confirmed, multi-outlet, now.** Three conditions: newest article within
      ``breakingHours`` (default 6 — cross-outlet confirmation latency measured at 3–6h, which
      the old 3h window sat inside: real stories at 6–7 outlets missed the badge while sports
      micro-clusters wore it); at least two DISTINCT PUBLISHERS in that window (an outlet
      republishing itself carried 2 of 8 live badges, one of them a 14-publisher story whose
      "burst" was one outlet's double copy); and the cluster at ``breakingMinPublishers`` — the
      NOTIFIER's own floor and rationale ("a single-source breaking story is a rumour"), which
      the badge contradicted: 62% of live badges failed the product's recorded bar, while all
      146 notifications ever sent had ≥9 publishers at detection. Re-ignition is deliberately
      preserved: an old cluster with a fresh multi-outlet wave breaks again.

    * **Future-dated timestamps are metadata, not news.** Member ages < 0 are ignored everywhere
      here — a stale cluster with one future-stamped copy was measured pinning itself
      ``Developing, score 100, age −6h``. A ``latest`` that is itself future falls back to the
      newest REAL member; a story with no non-future timestamp is Archived, never "fresh".
    * **Active requires activity.** 342 of 522 Active badges (65.5%) sat on stories with zero
      articles in 24h; every one was momentum-Declining. A story inside the Active age window
      with an empty 24h window is Cooling. This also removes the "Active + Updated 2d ago"
      contradiction structurally: Active now implies an article within ``momentumWindowHours``,
      and the card's rounded timestamp cannot reach "2 days" from there.
    * **Developing requires youth or a genuine burst.** 35 Developing/Breaking badges sat on
      3-day-plus clusters kept "unfolding" by one syndicated drip per day. A cluster older than
      ``developingMaxStoryAgeHours`` keeps Developing only with ``developingMinRecent``+ articles
      in the 24h window (a real second wave, e.g. a re-ignited trade story); otherwise it falls
      through to the Active/Cooling rules like any other ongoing story."""
    cov = _coverage_sorted(story)
    aged = [(h, c.get("publisher") or "")
            for c in cov
            if (h := _hours_since(c.get("publishedAt"), now)) is not None and h >= 0.0]
    ages = [h for h, _ in aged]
    recent = sum(1 for h in ages if h <= th["momentumWindowHours"])
    burst_pubs = len({p for h, p in aged if h <= th["breakingHours"] and p})
    oldest = max(ages) if ages else None
    pubs_total = story.get("publisherCount")
    if pubs_total is None:                     # lightweight dicts: derive from the coverage itself
        pubs_total = len({p for _, p in aged if p})
    age = _hours_since(story.get("latest") or story.get("updatedAt"), now)
    if age is None or age < 0.0:
        age = min(ages) if ages else None      # future/absent `latest` → newest real member
    if age is None:
        return {"band": "Archived", "score": 0, "latestAgeHours": None, "recentArticles": recent}
    recency = 100.0 * (0.5 ** (age / max(1.0, th["activeHours"])))
    score = int(round(min(100.0, recency + min(20, recent * 5))))
    young = oldest is None or oldest <= th["developingMaxStoryAgeHours"]
    if (age <= th["breakingHours"] and burst_pubs >= 2
            and int(pubs_total) >= th["breakingMinPublishers"]):
        band = "Breaking"
    elif age <= th["developingHours"] and (young or recent >= th["developingMinRecent"]):
        band = "Developing"
    elif age <= th["activeHours"] and (recent > 0 or not cov):
        # `not cov`: a lightweight story dict with no coverage list cannot prove OR disprove
        # activity — degrade to the age-only rule rather than silently demoting on missing data.
        band = "Active"
    elif age <= th["coolingHours"]:
        band = "Cooling"
    else:
        band = "Archived"
    return {"band": band, "score": score, "latestAgeHours": round(age, 2), "recentArticles": recent}


def compute_momentum(story: dict, *, now: datetime, th: dict) -> dict:
    """Coverage momentum (Growing / Stable / Declining) from the recent window vs the window before it,
    plus whether new publishers joined recently. Deterministic — no AI."""
    cov = _coverage_sorted(story)
    w = th["momentumWindowHours"]
    # Future-dated timestamps are ignored here for the same reason as in compute_freshness: a
    # future-stamped copy was measured making a stale story "Growing" (and its publisher "new").
    recent = _count_between(cov, now, 0.0, w)
    prior = _count_between(cov, now, w, 2 * w)
    first_seen: dict = {}
    for c in cov:
        p = c.get("publisher")
        if p and p not in first_seen:
            first_seen[p] = c.get("publishedAt")
    new_pubs = sum(1 for d in first_seen.values()
                   if (h := _hours_since(d, now)) is not None and 0.0 <= h <= w)
    if recent > prior or new_pubs > 0:
        state = "Growing"
    elif recent < prior or recent == 0:
        state = "Declining"
    else:
        state = "Stable"
    return {"state": state, "recentArticles": recent, "priorArticles": prior, "newPublishers": new_pubs}


def compute_lifecycle(story: dict, freshness: dict, momentum: dict, *, now: datetime, th: dict) -> str:
    """Deterministic lifecycle state (Breaking / Developing / Mature / Archived) from age + momentum."""
    age = freshness.get("latestAgeHours")
    if age is None or age > th["coolingHours"]:
        return "Archived"
    if age <= th["breakingHours"] and momentum["state"] == "Growing":
        return "Breaking"
    if age <= th["developingHours"] and momentum["state"] in ("Growing", "Stable"):
        return "Developing"
    return "Mature"


# --------------------------------------------------------------------------- #
# Coverage statistics + expanded timeline
# --------------------------------------------------------------------------- #
def compute_coverage_statistics(story: dict, *, now: datetime, th: dict) -> dict:
    """Publisher diversity + coverage growth / velocity / duration + political & publisher distribution.
    No opinion scores — coverage geometry only. Reuses the Story's own metrics where present."""
    cov = story.get("coverage") or []
    pub_dist: dict = {}
    for c in cov:
        p = c.get("publisher") or "Unknown"
        pub_dist[p] = pub_dist.get(p, 0) + 1
    duration_h = story.get("timeSpanHours") or 0.0
    total = story.get("totalCoverage") or len(cov)
    velocity_per_day = round(total / (duration_h / 24.0), 2) if duration_h and duration_h > 0 else float(total)
    w = th["momentumWindowHours"]
    recent = _count_between(cov, now, -1e9, w)
    prior = _count_between(cov, now, w, 2 * w)
    ratio = round((recent - prior) / prior, 2) if prior else (float(recent) if recent else 0.0)
    return {
        "publisherDiversity": story.get("publisherDiversity"),
        "publisherCount": story.get("publisherCount"),
        "articleCount": total,
        "coverageDurationHours": duration_h,
        "coverageVelocityPerDay": velocity_per_day,
        "coverageGrowth": {"recent": recent, "prior": prior, "delta": recent - prior, "ratio": ratio},
        "politicalDistribution": story.get("distribution"),
        "publisherDistribution": dict(sorted(pub_dist.items(), key=lambda kv: (-kv[1], kv[0]))),
    }


def compute_timeline(story: dict, *, th: Optional[dict] = None) -> list:
    """Expand the Story into chronological timeline events: first report, publisher joins, **perspective
    expansions** (coverage broadening to a new political side), article-count milestones, and the latest
    update. Derived from ``coverage`` — the Story's own 2-point timeline is left untouched."""
    th = th or thresholds_from_env()
    cov = _coverage_sorted(story)
    milestones = set(th["milestones"])
    events: list = []
    seen_pubs: set = set()
    seen_buckets: set = set()
    n = len(cov)
    for i, c in enumerate(cov):
        date = c.get("publishedAt") or ""
        pub = c.get("publisher") or "Unknown"
        bucket = c.get("leanBucket")
        if i == 0:
            events.append({"date": date, "type": "first_report", "label": f"First report — {pub}", "publisher": pub})
        elif pub not in seen_pubs:
            events.append({"date": date, "type": "publisher_join", "label": f"{pub} joined the coverage", "publisher": pub})
        seen_pubs.add(pub)
        if bucket:
            if bucket not in seen_buckets and len(seen_buckets) >= 1:   # broadening to a NEW perspective
                events.append({"date": date, "type": "perspective_expansion",
                               "label": f"Coverage expands to the {bucket}", "perspective": bucket})
            seen_buckets.add(bucket)
        count = i + 1
        if count in milestones:
            events.append({"date": date, "type": "milestone", "label": f"{count} articles covering the event", "count": count})
        if i == n - 1 and n > 1:
            events.append({"date": date, "type": "latest", "label": f"Latest update — {pub}", "publisher": pub})
    return events


# --------------------------------------------------------------------------- #
# New since last visit (reuses the browser-extension reads only)
# --------------------------------------------------------------------------- #
def compute_new_since_last_visit(story: dict, reads: Optional[list], *, now: Optional[datetime] = None) -> dict:
    """What's new in this Story since the reader last read one of its articles. Baseline (``lastVisited``)
    = the reader's most recent ``observedAt`` among reads whose canonical URL belongs to this Story; new
    = coverage published after it that the reader hasn't read. ``lastUpdated`` = the Story's latest
    publication (so the UI can explain the comparison). Empty on a first visit / anonymous request.
    Reuses the existing reads — no new tracking."""
    last_updated = story.get("latest") or story.get("updatedAt")
    empty = {"lastVisited": None, "lastUpdated": last_updated, "count": 0,
             "articles": [], "publishers": [], "perspectives": []}
    if not reads:
        return empty
    import ingest    # normalize_url — the SAME canonicalisation the read path keys on
    cov = _coverage_sorted(story)
    canon: dict = {}
    for c in cov:
        u = c.get("url") or ""
        for k in {u, (ingest.normalize_url(u) if u else "")}:
            if k:
                canon.setdefault(k, c)
    read_canon = {r.get("canonicalUrl") for r in reads}
    story_reads = [r for r in reads if r.get("canonicalUrl") in canon and r.get("observedAt")]
    if not story_reads:
        return empty
    baseline = max(r["observedAt"] for r in story_reads)

    def _already_read(c):
        u = c.get("url") or ""
        return u in read_canon or (ingest.normalize_url(u) if u else u) in read_canon

    new_arts = [c for c in cov
                if (c.get("publishedAt") or "") > baseline and not _already_read(c)]
    seen_pubs = {c.get("publisher") for c in cov if (c.get("publishedAt") or "") <= baseline}
    seen_buckets = {c.get("leanBucket") for c in cov if (c.get("publishedAt") or "") <= baseline}
    new_pubs = sorted({c.get("publisher") for c in new_arts} - seen_pubs)
    new_persp = sorted({c.get("leanBucket") for c in new_arts if c.get("leanBucket")} - seen_buckets,
                       key=lambda b: _BUCKET_ORDER.index(b) if b in _BUCKET_ORDER else 9)
    return {
        "lastVisited": baseline, "lastUpdated": last_updated, "count": len(new_arts),
        "articles": [{"publisher": c.get("publisher"), "headline": c.get("headline"), "url": c.get("url"),
                      "leanBucket": c.get("leanBucket"), "publishedAt": c.get("publishedAt")} for c in new_arts],
        "publishers": new_pubs, "perspectives": new_persp,
    }


# --------------------------------------------------------------------------- #
# Alerts (informational only — no notifications)
# --------------------------------------------------------------------------- #
def compute_alerts(story: dict, freshness: dict, lifecycle: str, momentum: dict, nsv: dict,
                   *, th: Optional[dict] = None) -> list:
    """Informational coverage alerts. Never a notification — purely descriptive flags for the UI."""
    alerts: list = []

    def add(t, msg, **ctx):
        alerts.append({"type": t, "message": msg, **ctx})

    if nsv.get("publishers"):
        add("new_publisher", f"New publisher(s) joined: {', '.join(nsv['publishers'])}", publishers=nsv["publishers"])
    if nsv.get("perspectives"):
        add("new_perspective", f"New perspective(s) since your last visit: {', '.join(nsv['perspectives'])}",
            perspectives=nsv["perspectives"])
    if freshness.get("band") == "Breaking":
        add("became_breaking", "This story is breaking — rapid new coverage across publishers.")
    if lifecycle == "Archived":
        add("became_archived", "This story has been archived — coverage has stopped.")
    if momentum.get("priorArticles", 0) > 0 and momentum.get("recentArticles", 0) >= 2 * momentum["priorArticles"]:
        add("coverage_doubled", "Coverage has more than doubled in the latest window.")
    return alerts


# --------------------------------------------------------------------------- #
# Assembled outputs + the Story-Service orchestrator
# --------------------------------------------------------------------------- #
def compute_summary(story: dict, *, now: Optional[datetime] = None, thresholds: Optional[dict] = None) -> dict:
    """The lightweight badge for the Story list/cards: freshness (band + score) + lifecycle. Cheap
    (no reads, no store) so ``/api/stories`` can attach it to every story without extra requests."""
    now = now or _now()
    th = thresholds or thresholds_from_env()
    freshness = compute_freshness(story, now=now, th=th)
    momentum = compute_momentum(story, now=now, th=th)
    lifecycle = compute_lifecycle(story, freshness, momentum, now=now, th=th)
    return {"freshness": {"band": freshness["band"], "score": freshness["score"]}, "lifecycle": lifecycle}


def compute_intelligence(story: dict, *, reads: Optional[list] = None, now: Optional[datetime] = None,
                         thresholds: Optional[dict] = None) -> dict:
    """The full Story Intelligence for one Story (+ optional reader reads)."""
    now = now or _now()
    th = thresholds or thresholds_from_env()
    t0 = time.perf_counter()
    freshness = compute_freshness(story, now=now, th=th)
    momentum = compute_momentum(story, now=now, th=th)
    lifecycle = compute_lifecycle(story, freshness, momentum, now=now, th=th)
    stats = compute_coverage_statistics(story, now=now, th=th)
    timeline = compute_timeline(story, th=th)
    nsv = compute_new_since_last_visit(story, reads, now=now)
    alerts = compute_alerts(story, freshness, lifecycle, momentum, nsv, th=th)
    return {
        "storyId": story.get("id"),
        "freshness": freshness,
        "lifecycle": lifecycle,
        "momentum": momentum,
        "coverageStatistics": stats,
        "timeline": timeline,
        "newSinceLastVisit": nsv,
        "alerts": alerts,
        "lastVisited": nsv.get("lastVisited"),
        "lastUpdated": nsv.get("lastUpdated"),
        "diagnostics": {"computeMs": round((time.perf_counter() - t0) * 1000.0, 3),
                        "coverageCount": len(story.get("coverage") or []),
                        "timelineEvents": len(timeline)},
    }


def intelligence_for(store_, story_id: str, *, reads: Optional[list] = None,
                     now: Optional[datetime] = None) -> Optional[dict]:
    """Fetch the Story from the Story Service and compute its intelligence. The ONE place this module
    consumes ``story_service`` — the dependency only ever points this way."""
    import story_service     # imported here so the module graph stays FeedArticle → Service → Intelligence
    story = story_service.get_story(store_, story_id)
    if story is None:
        return None
    return compute_intelligence(story, reads=reads, now=now)
