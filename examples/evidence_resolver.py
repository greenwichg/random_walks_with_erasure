"""Evidence Resolver (Commit 21a.3) — ONE human explanation per recommendation, chosen from
computed evidence by strict priority. Story matching is merely today's Priority 1: the resolver
is the product's single explanation vocabulary, reusable by any surface (Recommendations now;
Discover / notifications / digests later) — the internal explain endpoint stays the developer
source of truth underneath it.

Contract (structured, so tooling never parses prose):

    {"type": "story_match", "priority": 1, "variant": "same_event",
     "message": "You already read this story from Fox News. Here's how CNN covered the same story.",
     "evidence": {...the computed facts that license the sentence...}}

Priorities (first match wins; explanations are NEVER combined). Ordered so the most personal,
most provable reason always wins — a same-story relationship, a cross-cutting read, or an
unfamiliar publisher outranks a mere shared topic (21a.4):

    1 story_match        you read another article of the SAME story, from a DIFFERENT publisher
                         (variants: following >= 2 story reads; follow_up rec newer than your
                         read; else same_event) — story membership from the Story Service's own
                         clusters (coverage carries the URLs), no re-clustering logic here
    2 bridge             the recommendation is cross-cutting (the 21a computation)
    3 new_publisher      you never/rarely read this outlet (the 21a familiarity computation)
    4 topic_continuity   the article's topic is one of your top reading topics — fallback only;
                         never displaces a provable story / publisher / cross-cutting reason
    5 long_tail          chosen by RWE-D (long-tail discovery)
    6 coverage_breadth   claim-free fallback so no priority is ever forced to over-claim

``validate()`` re-derives every gate from the evidence and the raw context — the automated
check that a shown sentence is licensed by real data (used by the tests today and the
Recommendation Validation Pipeline in 21d).

Read-only over the store + Story Service; never touches ranking, scoring, graphs, or clustering.
"""
from __future__ import annotations

import pathlib
import sys
import time
from typing import Callable, Optional

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from ingest import canonical_url as _canon  # noqa: E402

TYPES = ("story_match", "topic_continuity", "new_publisher", "bridge",
         "long_tail", "coverage_breadth")

#: "latest update" is only truthful when the recommendation is meaningfully newer than the
#: coverage the reader already saw — a same-news-cycle article is "the same story", not an update.
_FOLLOW_UP_MIN_HOURS = 6.0


def _hours_after(later: str, earlier: str) -> Optional[float]:
    """Hours by which ``later`` postdates ``earlier``; None when either timestamp is unusable."""
    from datetime import datetime
    try:
        a = datetime.fromisoformat(str(later).replace("Z", "+00:00"))
        b = datetime.fromisoformat(str(earlier).replace("Z", "+00:00"))
        return (a - b).total_seconds() / 3600.0
    except Exception:
        return None

# ---------------------------------------------------------------- story index (memoized)
#: Rebuild at most once a minute per catalog size — clustering 128 articles costs ~40 ms, and
#: the index only shifts when the catalog does (bounded staleness is stated, not hidden).
_INDEX_TTL_S = 60
_INDEX_CACHE: dict = {"key": None, "index": None}


def story_index(store_) -> dict:
    """``canonical url → {"storyId", "coverage"}`` from the Story Service's own clusters —
    Priority 1 consumes the SAME stories the Stories page shows (coverage carries the URLs)."""
    if store_ is None:
        return {}
    import story_service
    key = (int(store_.count_feed_articles()), int(time.time() // _INDEX_TTL_S))
    if _INDEX_CACHE["key"] == key and _INDEX_CACHE["index"] is not None:
        return _INDEX_CACHE["index"]
    index: dict = {}
    for s in story_service.cluster_from_store(store_):
        for m in s.get("coverage") or []:
            if m.get("url"):
                index[_canon(str(m["url"]))] = {"storyId": s["id"], "coverage": s["coverage"]}
    _INDEX_CACHE.update(key=key, index=index)
    return index


# ---------------------------------------------------------------- resolution
def resolve(rec: dict, ctx: dict, index: Optional[dict] = None) -> dict:
    """Choose the one explanation for an already-serialized recommendation payload.

    ``ctx`` is per-reader context the caller could actually compute (surfaces without a
    signed-in reader simply fall through the history-based priorities):
      reads          [{"url": canonical, "publisher": str, "publishedAt": str|None}], oldest first
      familiarity    callable(publisher) -> {"reads", "share", "band"}   (21a computation)
      top_topics     [str] — the reader's top reading categories
    """
    art = rec.get("article") or {}
    url = _canon(str(art.get("url") or art.get("id") or ""))
    publisher = str(art.get("publisher") or "")
    topic = str(art.get("topic") or "")
    reads = list(ctx.get("reads") or [])

    # -- P1 · story_match ---------------------------------------------------
    story = (index or {}).get(url)
    if story:
        members = {_canon(str(m["url"])): m for m in story["coverage"] if m.get("url")}
        mine = [r for r in reads if r.get("url") in members and r["url"] != url]
        qualifying = [r for r in mine if str(r.get("publisher") or "") != publisher]
        if qualifying:
            read = qualifying[-1]                      # oldest-first ⇒ last = most recent (Case 5)
            read_pub = str(read.get("publisher") or "your earlier read")
            member = members.get(read["url"]) or {}
            rec_at, read_at = str(art.get("publishedAt") or ""), str(member.get("publishedAt") or "")
            gap_h = _hours_after(rec_at, read_at) if rec_at and read_at else None
            if len(mine) >= 2:
                variant, message = "following", (
                    f"You've been following this story. Here's {publisher}'s coverage.")
            elif gap_h is not None and gap_h >= _FOLLOW_UP_MIN_HOURS:
                variant, message = "follow_up", (
                    f"You already read the earlier coverage from {read_pub}. "
                    f"Here's {publisher}'s latest update.")
            else:
                variant, message = "same_event", (
                    f"You already read this story from {read_pub}. "
                    f"Here's how {publisher} covered the same story.")
            return {"type": "story_match", "priority": 1, "variant": variant, "message": message,
                    "evidence": {"storyId": story["storyId"], "readUrl": read["url"],
                                 "readPublisher": read_pub, "recPublisher": publisher,
                                 "storyReads": len(mine),
                                 "readPublishedAt": read_at or None,
                                 "recPublishedAt": rec_at or None}}

    # -- P2 · bridge (a cross-cutting read is a stronger, more personal reason than a merely
    #                 shared topic — so it outranks topic_continuity as of 21a.4) -----------
    if bool(rec.get("crossCutting")):
        return {"type": "bridge", "priority": 2,
                "message": "This article offers another political perspective.",
                "evidence": {"crossCutting": True, "articleLean": art.get("lean")}}

    # -- P3 · new_publisher --------------------------------------------------
    fam_of: Optional[Callable] = ctx.get("familiarity")
    fam = fam_of(publisher) if (fam_of is not None and publisher) else None
    if fam and fam.get("band") in ("never", "rarely"):
        first = (f"You've never read {publisher} before." if fam["band"] == "never"
                 else f"You rarely read {publisher}.")
        return {"type": "new_publisher", "priority": 3,
                "message": f"{first} This broadens your source diversity.",
                "evidence": dict(fam, publisher=publisher)}

    # -- P4 · topic_continuity (fallback only — reached only after story, cross-cutting, and
    #    publisher reasons are exhausted, so a shared topic never displaces a provable one) --
    tops = [str(t) for t in (ctx.get("top_topics") or [])]
    if topic and topic in tops:
        cross = bool(rec.get("crossCutting"))
        second = ("Here's another perspective." if cross
                  else "Here's more coverage from another outlet.")
        return {"type": "topic_continuity", "priority": 4,
                "message": f"You've been reading about {topic.lower()}. {second}",
                "evidence": {"topic": topic, "topTopics": tops, "crossCutting": cross}}

    # -- P5 · long_tail --------------------------------------------------------
    if str(rec.get("strategy") or "") == "rwe-d":
        return {"type": "long_tail", "priority": 5,
                "message": "This article introduces a less frequently recommended source.",
                "evidence": {"strategy": "rwe-d"}}

    # -- P6 · coverage_breadth (claim-free — no priority may over-claim) --------
    return {"type": "coverage_breadth", "priority": 6,
            "message": (f"Broadens your {topic.lower()} coverage beyond your usual mix."
                        if topic else "Broadens your coverage beyond your usual mix."),
            "evidence": {"topic": topic or None}}


# ---------------------------------------------------------------- validation (the RVP hook)
def validate(explanation: dict, rec: dict, ctx: dict, index: Optional[dict] = None) -> list:
    """Re-derive every gate the explanation's type claims, from the raw context — returns the
    list of failures ([] = PASS). This is what makes a shown sentence *checkable*: the
    Recommendation Validation Pipeline (21d) and the regression tests consume it by ``type``
    instead of parsing prose."""
    fails: list = []
    etype = explanation.get("type")
    if etype not in TYPES:
        return [f"unknown explanation type: {etype!r}"]
    art = rec.get("article") or {}
    url = _canon(str(art.get("url") or art.get("id") or ""))
    publisher = str(art.get("publisher") or "")
    ev = explanation.get("evidence") or {}

    if etype == "story_match":
        story = (index or {}).get(url)
        if not story:
            fails.append("recommended article is not in any story")
        elif story["storyId"] != ev.get("storyId"):
            fails.append("story ids differ between evidence and index")
        else:
            members = {_canon(str(m["url"])) for m in story["coverage"] if m.get("url")}
            read_urls = {r.get("url") for r in ctx.get("reads") or []}
            if ev.get("readUrl") not in members:
                fails.append("cited read is not a member of the story")
            if ev.get("readUrl") not in read_urls:
                fails.append("cited read is not in the reader's history")
            if ev.get("readPublisher") == publisher:
                fails.append("publishers do not differ")
        allowed = {"same_event": "covered the same story",
                   "follow_up": "latest update",
                   "following": "following this story"}
        want = allowed.get(str(explanation.get("variant")))
        if not want or want not in str(explanation.get("message", "")):
            fails.append("sentence not allowed for the variant")
    elif etype == "topic_continuity":
        if str(art.get("topic") or "") not in (ctx.get("top_topics") or []):
            fails.append("topic is not among the reader's top topics")
    elif etype == "new_publisher":
        fam_of = ctx.get("familiarity")
        band = (fam_of(publisher) if fam_of else {}).get("band")
        if band not in ("never", "rarely"):
            fails.append(f"familiarity band is {band!r}, not never/rarely")
        if band and band != ev.get("band"):
            fails.append("evidence band does not match recomputed band")
    elif etype == "bridge":
        if not bool(rec.get("crossCutting")):
            fails.append("recommendation is not cross-cutting")
    elif etype == "long_tail":
        if str(rec.get("strategy")) != "rwe-d":
            fails.append("recommendation was not chosen by rwe-d")
    # coverage_breadth: claim-free by construction — nothing to verify.
    return fails
