"""story_events.py — turn a story becoming *Breaking* into a notification event, exactly once.

This is the PRODUCER side of the notification platform: the one place that decides a global
occurrence has happened. It sits where `/api/stories` sits in the dependency graph — a consumer of
both :mod:`story_service` (which builds stories and deliberately knows nothing about Story
Intelligence) and :mod:`story_intelligence` (which scores them and knows nothing about storage). It
is its own module for that reason: neither of those may import the other, and neither should learn
about notifications.

**The level/edge problem, which is the whole reason this file exists.**
``story_intelligence.compute_freshness`` returns a *band*, and a band is a LEVEL computed from a
rolling window. A story crosses into ``"Breaking"``, drops out as the window slides past its burst,
and crosses back in when a second wave of coverage lands — so a notification written as
``if band == "Breaking"`` fires again and again for one story. What a reader should be told about is
the EDGE: the first moment it became true.

Nothing here remembers that edge. ``store.record_notification_event`` does, in a row protected by
``UNIQUE(source_type, source_id)``, and its return value *is* the edge — ``True`` the first time,
``False`` for every later cycle and every concurrent one. So this module holds no state, needs no
"already announced" set, and is safe to run from more than one process.

**Where it runs.** From the ingest poller's cycle, after the story cache is warmed — a background
process, never a request. Emitting from `/api/stories` would make a GET write, which is the pattern
this design moved away from.

**Off by default.** ``RWE_BREAKING_NOTIFICATIONS`` must be truthy or :func:`detect_breaking_stories`
returns immediately. Turning it on is an operational act, reversible with a restart.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import story_intelligence
import story_service


#: The category every event from this producer carries — the axis reader preferences gate on
#: (``notifications.categories.breaking``).
CATEGORY = "breaking"

#: ``source_type`` for the event's identity. The pair ``(SOURCE_TYPE, story_id)`` is what UNIQUE
#: protects, so a different producer may reuse a story id under its own type without colliding.
SOURCE_TYPE = "story_breaking"


def enabled() -> bool:
    """Whether breaking-story detection runs at all. Default OFF: this is the only part of Phase A
    that changes what a reader sees, so switching it on is a deliberate, reversible act rather than a
    consequence of deploying."""
    raw = os.environ.get("RWE_BREAKING_NOTIFICATIONS", "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def min_publishers() -> int:
    """How many distinct outlets must already be covering a story before it is worth interrupting a
    reader for (default 3).

    A quality bar, not a performance one. A single-source "breaking story" is a rumour, and telling
    readers about one costs more credibility than staying silent costs attention — which matters
    more than usual in a product whose premise is a healthier information diet."""
    raw = os.environ.get("RWE_BREAKING_MIN_PUBLISHERS", "")
    return int(raw) if raw.strip().isdigit() and int(raw) > 0 else 3


def ttl_hours() -> int:
    """How long a breaking event stays worth delivering (default 6h).

    Two jobs. It stops a reader who has not opened the app for days being told that something is
    breaking when it resolved overnight. And it is what makes the per-day cap a cap rather than a
    queue: a story the cap held back yesterday expires instead of arriving tomorrow as news."""
    raw = os.environ.get("RWE_BREAKING_TTL_HOURS", "")
    return int(raw) if raw.strip().isdigit() and int(raw) > 0 else 6


def _payload(story: dict, freshness: dict) -> dict:
    """What the notification will show. Deliberately small: a title, the story to open, and the two
    facts that justify the interruption. No coverage list, no scores — the story page has those."""
    return {
        "storyId": story.get("id"),
        "title": story.get("title") or "",
        "publisherCount": int(story.get("publisherCount") or 0),
        "band": freshness.get("band"),
        "topic": story.get("topic") or "",
    }


def detect_breaking_stories(store_, *, now: "datetime | None" = None, log=None,
                            limit: int = 60) -> int:
    """Record one event per story that has *just* become Breaking. Returns how many were new.

    Idempotent and stateless: re-running over the same stories records nothing, because the second
    call gets ``False`` from the store. Safe to call on every poll cycle, which is the point — the
    cycle is the only clock this needs.

    Never raises. A failure here must cost breaking notifications and nothing else: the caller is the
    ingest loop, and ingestion is far more important than an alert about it."""
    if not enabled():
        return 0
    now = now or datetime.now(timezone.utc)
    expires_at = (now + timedelta(hours=ttl_hours())).isoformat()
    threshold = min_publishers()
    created = 0

    try:
        # `sort="new"` and a bounded limit: a story that just broke is by definition recent, so
        # scanning the whole catalogue would cost the poll cycle time for stories that cannot
        # qualify. The cache was warmed immediately before this call, so this is a cache read.
        stories = (story_service.list_stories(store_, sort="new", limit=limit) or {}).get("stories") or []
    except Exception as exc:                     # noqa: BLE001 — see the docstring
        if log:
            log("breaking_detect_failed", error=f"{type(exc).__name__}: {exc}")
        return 0

    for story in stories:
        try:
            if int(story.get("publisherCount") or 0) < threshold:
                continue                          # below the quality bar — not worth interrupting for
            freshness = story_intelligence.compute_freshness(
                story, now=now, th=story_intelligence.thresholds_from_env())
            if freshness.get("band") != "Breaking":
                continue                          # the LEVEL is not true right now
            story_id = str(story.get("id") or "")
            if not story_id:
                continue
            # ...and this is the EDGE: True only on the first cycle that ever saw it.
            if store_.record_notification_event(
                    SOURCE_TYPE, story_id, category=CATEGORY,
                    payload=_payload(story, freshness),
                    occurred_at=now.isoformat(), expires_at=expires_at):
                created += 1
                if log:
                    log("breaking_story_detected", storyId=story_id,
                        publisherCount=story.get("publisherCount"),
                        topic=story.get("topic") or "")
        except Exception as exc:                  # noqa: BLE001 — one bad story is not the batch
            if log:
                log("breaking_detect_story_failed", error=f"{type(exc).__name__}: {exc}")
            continue

    return created
