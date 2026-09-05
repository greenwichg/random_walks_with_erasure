"""story_history.py — durable events: what each served build said, recorded as DELTAS.

The story builder is a pure function and stays one (``story_service.build_stories``); the id
ledger (``story_member``) remembers only the id each url was LAST served under. Between them a
story that leaves the window leaves no trace, a merge or a split is invisible after the fact, and
"what did we say about this story yesterday" is unanswerable. This module records, once per served
unfiltered build:

* ``story_builds``      — one row: when, which algorithm version, under which configuration;
* ``stories``           — the durable event row, created on first serve, closed when it stops
                          being served, ``merged`` (with its successor) when the ledger handed
                          most of its members to another id, ``origin_id`` when it split off one;
* ``story_snapshots``   — the story as served, written ONLY when its served fields changed
                          (a fingerprint decides), so the table grows with news, not with polling;
* ``story_membership``  — joins and leaves per url, with the build they happened at.

It is called from ``story_service._cached_build`` AFTER identity, tags and Tier-B attachment, on
the unfiltered build only, single-flight under the build lock, and it fails soft: a history
write that raises is logged and the build is served exactly as before. Nothing on the consumer
path reads these tables. ``RWE_STORY_HISTORY=0`` turns it off.

Design: docs/NEWS_INTELLIGENCE_INFRASTRUCTURE.md §D.3 / §E.2.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from collections import Counter
from datetime import datetime, timezone
from typing import Optional

import identity

#: The share of a departing story's members a destination must have absorbed for the departure
#: to count as a MERGE into it (and, mirrored, of a new story's members one origin must have
#: supplied for the arrival to count as a SPLIT). The same 0.5 the id ledger's carry-over uses.
MERGE_SHARE = 0.5

log = logging.getLogger("story_history")


def enabled() -> bool:
    """ON — ``RWE_STORY_HISTORY=0`` disables recording (serving is unaffected either way)."""
    return os.environ.get("RWE_STORY_HISTORY", "").strip().lower() not in {"0", "false", "no", "off"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def snapshot_of(story: dict) -> dict:
    """The snapshot columns for a served story (everything but build id / time / fingerprint)."""
    dist = story.get("distribution") or {}
    tags = [t.get("name") for t in (story.get("tags") or []) if isinstance(t, dict) and t.get("name")]
    geo = story.get("geoCoherence")
    return {
        "story_id": story["id"],
        "title": story.get("title") or "",
        "summary": story.get("summary") or "",
        "topic": story.get("topic") or "",
        "total_coverage": int(story.get("totalCoverage") or 0),
        "publisher_count": int(story.get("publisherCount") or 0),
        "attached_coverage": int(story.get("attachedCoverage") or 0),
        "distribution": json.dumps({k: dist.get(k, 0) for k in ("left", "center", "right")}),
        "blindspot_side": story.get("blindspotSide"),
        "blindspot_withheld": bool(story.get("blindspotWithheld")),
        "cluster_trust": story.get("clusterTrust"),
        "geo_coherence": round(float(geo), 4) if isinstance(geo, (int, float)) else None,
        "countries": json.dumps(list(story.get("countries") or [])),
        "primary_country": story.get("primaryCountry"),
        "earliest": story.get("earliest") or "",
        "latest": story.get("latest") or "",
        "image": story.get("image"),
        "publishers": json.dumps(sorted(story.get("publishers") or [])),
        "tags": json.dumps(tags),
    }


def fingerprint(snapshot: dict) -> str:
    """A hash of the served fields — the change detector. Same fields, same hash, no new row."""
    keyed = {k: v for k, v in snapshot.items() if k != "story_id"}
    return hashlib.sha1(json.dumps(keyed, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _representative_url(story: dict) -> str:
    cov = story.get("coverage") or []
    if not cov:
        return ""
    # `_build_story` titles the event by its EARLIEST member; the coverage list is newest first.
    oldest = min(cov, key=lambda c: (c.get("publishedAt") or "~", c.get("url") or ""))
    return oldest.get("url") or ""


def record_build(store_, stories: list, *, build_version: str, config_hash: str,
                 registry_version: "str | None" = None, built_at: "str | None" = None,
                 resolve_ids=None) -> Optional[dict]:
    """Record one served unfiltered build. Returns the build's counters, or ``None`` when off.

    Pure bookkeeping over the served list: it never changes a story, an id, or the order. Every
    write lands in one transaction (``store.apply_story_history``), so a crash mid-build leaves
    the previous history intact rather than a half-recorded build."""
    if not enabled():
        return None
    t0 = time.perf_counter()
    built_at = built_at or _now_iso()

    # -- the current membership, keyed exactly as the id ledger keys it (coverage url) -------- #
    current: dict = {}
    for s in stories:
        for c in s.get("coverage") or ():
            url = c.get("url")
            if url and url not in current:
                current[url] = (s["id"], bool(c.get("tierB")), c)
    served_ids = [s["id"] for s in stories]
    served_set = set(served_ids)

    open_members = store_.story_history_open()             # url -> {id, storyId, attached}
    records = store_.story_records(served_ids)             # existing rows for served ids
    active_ids = store_.open_story_ids()

    # -- joins, leaves, and where members MOVED (the merge/split evidence) ------------------ #
    joins: list = []
    leaves: list = []
    moved: dict = {}          # old story -> Counter(new story)
    departed: Counter = Counter()   # old story -> members that left it (moved or aged out)
    arrivals: dict = {}       # new story -> Counter(old story) over its members
    for url, (sid, attached, c) in current.items():
        prev = open_members.get(url)
        if prev is not None and prev["storyId"] == sid and prev["attached"] == attached:
            continue
        if prev is not None:
            leaves.append(prev["id"])
            departed[prev["storyId"]] += 1
            moved.setdefault(prev["storyId"], Counter())[sid] += 1
            arrivals.setdefault(sid, Counter())[prev["storyId"]] += 1
        joins.append({"story_id": sid, "url": url, "attached": attached,
                      "publisher": c.get("publisher") or "",
                      "publisher_id": identity.publisher_id_for(c.get("publisher")),
                      "article_id": None})
    for url, prev in open_members.items():
        if url not in current:
            leaves.append(prev["id"])
            departed[prev["storyId"]] += 1
    # Article ids for the JOINING urls only (the steady state is a few dozen per build), resolved
    # through the alias table so the original publisher URL the coverage carries maps to the id.
    if joins and resolve_ids is not None:
        try:
            ids = resolve_ids([j["url"] for j in joins]) or {}
        except Exception:                   # identity is additive; a lookup fault loses nothing
            ids = {}
        for j in joins:
            j["article_id"] = ids.get(j["url"])

    # -- story lifecycle ----------------------------------------------------------------- #
    new_stories: list = []
    for s in stories:
        if s["id"] in records:
            continue
        origin = None
        src = arrivals.get(s["id"])
        if src:
            old, n = src.most_common(1)[0]
            members = len([1 for u, (sid, _a, _c) in current.items() if sid == s["id"]])
            if old in served_set and members and n / members >= MERGE_SHARE:
                origin = old
        new_stories.append({"storyId": s["id"], "title": s.get("title") or "",
                            "topic": s.get("topic") or "", "representativeUrl": _representative_url(s),
                            "originId": origin})
    reopened = [sid for sid in served_ids if sid in records and records[sid]["status"] != "active"]
    status_updates: list = []
    for sid in sorted(active_ids - served_set):
        successor = None
        dest = moved.get(sid)
        if dest and departed[sid]:
            to, n = dest.most_common(1)[0]
            if to in served_set and n / departed[sid] >= MERGE_SHARE:
                successor = to
        status_updates.append({"storyId": sid, "status": "merged" if successor else "closed",
                               "successorId": successor})

    # -- snapshots, only where something changed ----------------------------------------- #
    last = store_.last_story_fingerprints(served_ids)
    snapshots: list = []
    for s in stories:
        snap = snapshot_of(s)
        fp = fingerprint(snap)
        if last.get(s["id"]) == fp:
            continue
        snapshots.append(dict(snap, fingerprint=fp))

    # -- write ---------------------------------------------------------------------------- #
    try:
        catalog_rows, catalog_newest = store_.catalog_fingerprint()
    except Exception:                       # a store without the fingerprint is still recordable
        catalog_rows, catalog_newest = None, None
    build_id = store_.record_story_build(built_at=built_at, build_version=build_version,
                                         config_hash=config_hash, registry_version=registry_version,
                                         catalog_rows=catalog_rows, catalog_newest=catalog_newest,
                                         stories=len(stories))
    stats = {"stories": len(stories), "new_stories": len(new_stories),
             "closed_stories": len(status_updates), "changed": len(snapshots),
             "joins": len(joins), "leaves": len(leaves)}
    store_.apply_story_history(build_id=build_id, built_at=built_at, new_stories=new_stories,
                               reopened=reopened, status_updates=status_updates,
                               touched=served_ids, snapshots=snapshots, joins=joins,
                               leaves=leaves,
                               stats=dict(stats, ms=round((time.perf_counter() - t0) * 1000.0, 1)))
    out = dict(stats, buildId=build_id, builtAt=built_at,
               ms=round((time.perf_counter() - t0) * 1000.0, 1))
    log.info(json.dumps({"event": "story_history", **out}))
    return out


def history_for(store_, story_id: str, *, limit: int = 200) -> Optional[dict]:
    """A story's durable record + its snapshots (oldest first) + membership joins/leaves."""
    return store_.story_history(story_id, limit=limit)


def _hours_between(earliest: str, latest: str) -> Optional[float]:
    try:
        a = datetime.fromisoformat(str(earliest).replace("Z", "+00:00"))
        b = datetime.fromisoformat(str(latest).replace("Z", "+00:00"))
        return round(abs((b - a).total_seconds()) / 3600.0, 2)
    except (TypeError, ValueError):
        return None


def persisted_view(store_) -> "tuple[list, str | None]":
    """The last recorded build, re-materialised as served stories — ``(stories, builtAt)``.

    Every ACTIVE story row, its latest snapshot (title, summary, topic, spectrum, trust, geography,
    tags, image, time span) and its OPEN membership, joined back to the catalogue rows the members
    are (bodies never loaded) and serialised by the one Article serializer the consumer path uses
    (``discover.feed_article_to_article``), so a coverage row here is the same shape a built story
    carries. What a snapshot does not hold (``publisherDiversity``, tag scores) is absent, never
    invented. Members whose catalogue row is gone (retention) are dropped and the counts recomputed
    over what is left, so the story is consistent with itself."""
    import discover
    import ingest
    import story_tags
    rows = store_.persisted_story_view_rows()
    if not rows:
        return [], None
    members = rows["membership"]
    canon_of = {}
    for ms in members.values():
        for m in ms:
            canon_of[m["url"]] = ingest.canonical_url(m["url"])
    articles = store_.feed_rows_for_urls(canon_of.values())
    stories = []
    for snap in rows["stories"]:
        coverage = []
        for m in members.get(snap["storyId"], ()):
            row = articles.get(canon_of.get(m["url"]))
            if row is None:
                continue
            a = discover.feed_article_to_article(row)
            a["url"] = m["url"] or a.get("url")
            if m.get("attached"):
                a["tierB"] = True
            coverage.append(a)
        if not coverage:
            continue
        coverage.sort(key=lambda c: (c.get("publishedAt") or "", c.get("url") or ""), reverse=True)
        publishers = sorted({c.get("publisher") for c in coverage if c.get("publisher")})
        times = [c.get("publishedAt") for c in coverage if c.get("publishedAt")]
        earliest = min(times) if times else (snap.get("earliest") or None)
        latest = max(times) if times else (snap.get("latest") or None)
        stories.append({
            "id": snap["storyId"], "title": snap.get("title") or "", "summary": snap.get("summary") or "",
            "topic": snap.get("topic") or "", "coverage": coverage,
            "totalCoverage": len(coverage), "publisherCount": len(publishers), "publishers": publishers,
            "attachedCoverage": sum(1 for c in coverage if c.get("tierB")),
            "distribution": snap.get("distribution") or {}, "blindspotSide": snap.get("blindspotSide"),
            "blindspotWithheld": bool(snap.get("blindspotWithheld")),
            "clusterTrust": snap.get("clusterTrust"), "geoCoherence": snap.get("geoCoherence"),
            "countries": list(snap.get("countries") or []), "primaryCountry": snap.get("primaryCountry"),
            "earliest": earliest, "latest": latest, "updatedAt": latest,
            "timeSpanHours": _hours_between(earliest, latest) if earliest and latest else None,
            "image": snap.get("image"),
            "tags": [{"name": t, "label": story_tags.label_for(t)} for t in (snap.get("tags") or []) if t],
            "persisted": True,
        })
    # The build's own order is not recorded; biggest and freshest first is what it serves.
    stories.sort(key=lambda s: (s["publisherCount"], s["totalCoverage"], s.get("latest") or ""), reverse=True)
    return stories, rows.get("builtAt")


__all__ = ["MERGE_SHARE", "enabled", "snapshot_of", "fingerprint", "record_build", "history_for",
           "persisted_view"]
