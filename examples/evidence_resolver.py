"""Evidence Resolver (Commit 21a.3) — ONE human explanation per recommendation, chosen from
computed evidence by strict priority. Story matching is merely today's Priority 1: the resolver
is the product's single explanation vocabulary, reusable by any surface (Recommendations now;
Discover / notifications / digests later) — the internal explain endpoint stays the developer
source of truth underneath it.

Contract (structured, so tooling never parses prose):

    {"type": "story_match", "priority": 1, "variant": "same_event",
     "readerFact":   {"key": "read_story_from",    "params": {"publisher": "Fox News"}},
     "contribution": {"key": "covered_same_story", "params": {"publisher": "CNN"}},
     "message": "You already read this story from Fox News. Here's how CNN covered the same story.",
     "evidence": {...the computed facts that license the sentence...}}

Commit 23: ``readerFact`` / ``contribution`` are SEMANTIC parts (key + params, never localized
prose) — the UI renders them via catalog templates (``rec.reader.*`` / ``rec.contribution.*``),
exactly the Commit 20 pattern. ``message`` stays byte-identical: it remains the validated whole,
the ``reason`` mirror, and the drawer sentence. Types without a truthful reader fact carry no
``readerFact`` (long_tail — see the branch comment — and coverage_breadth; a balanced-profile
bridge degrades the same way), so the structure can never over-claim.

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

#: Commit 23 — the reader-side "meaningfully off-center" bar for the SEMANTIC political profile,
#: deliberately the same 0.5 the cross-cutting gate applies to the article (api_server._cross_of),
#: so "leans" means one thing product-wide. Numeric thresholding stays HERE; presentation only
#: ever sees the semantic value.
_READER_LEAN_TAU = 0.5


def _reader_political_profile(ctx: dict) -> str:
    """``balanced`` / ``leans_left`` / ``leans_right`` from the context's reader mean lean.
    Missing or near-center means (|mean| < tau) are ``balanced`` — the no-claim default, so an
    older caller that doesn't supply the mean can never cause an over-claim."""
    try:
        mean = float(ctx.get("reader_mean_lean"))
    except (TypeError, ValueError):
        return "balanced"
    if mean <= -_READER_LEAN_TAU:
        return "leans_left"
    if mean >= _READER_LEAN_TAU:
        return "leans_right"
    return "balanced"


def _hours_after(later: str, earlier: str) -> Optional[float]:
    """Hours by which ``later`` postdates ``earlier``; None when either timestamp is unusable."""
    from datetime import datetime
    try:
        a = datetime.fromisoformat(str(later).replace("Z", "+00:00"))
        b = datetime.fromisoformat(str(earlier).replace("Z", "+00:00"))
        return (a - b).total_seconds() / 3600.0
    except Exception:
        return None


def _share_percent(share) -> Optional[int]:
    """A 0..1 share as a whole display percent, or ``None`` when the share is missing, junk, or
    would round to 0% (a "0% of your reading" claim is worse than the plain fact)."""
    try:
        v = float(share)
    except (TypeError, ValueError):
        return None
    if not (0.0 <= v <= 1.0):
        return None
    pct = round(100.0 * v)
    return int(pct) if pct >= 1 else None


def _topic_shares(ctx: dict) -> dict:
    ts = ctx.get("topic_shares")
    return ts if isinstance(ts, dict) else {}


def _lean_shares(ctx: dict) -> "dict | None":
    """The context's political-lean shares, rounded to a stable 3 decimals — or ``None`` when
    absent/malformed (so no share claim is ever minted from junk)."""
    ls = ctx.get("lean_shares")
    if not isinstance(ls, dict):
        return None
    try:
        out = {k: round(float(ls[k]), 3) for k in ("left", "center", "right")}
    except (KeyError, TypeError, ValueError):
        return None
    if any(not (0.0 <= v <= 1.0) for v in out.values()):
        return None
    return out

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
    import obs_metrics
    import story_service
    t0 = time.perf_counter()
    key = (int(store_.count_feed_articles()), int(time.time() // _INDEX_TTL_S))
    if _INDEX_CACHE["key"] == key and _INDEX_CACHE["index"] is not None:
        try:
            obs_metrics.incr("rec_story_index_hit_total")
            obs_metrics.observe("rec_story_index_hit_ms", (time.perf_counter() - t0) * 1000.0)
        except Exception:                       # instrumentation must never break resolution
            pass
        return _INDEX_CACHE["index"]
    index: dict = {}
    # The cached default view (see story_service.default_story_view): an index rebuild no longer
    # pays a full clustering on the request thread — it reads the build the poller already warmed.
    #
    # "Reads the build the poller already warmed" is the intended case, not a guaranteed one:
    # `default_story_view` falls back to a read-only INLINE build whenever the peek misses, and
    # that fallback is a full clustering on this request thread. The two cost ~2 ms and ~600 ms at
    # 2k articles, so which one production actually takes decides the whole latency picture —
    # hence timed apart rather than assumed.
    t_view = time.perf_counter()
    view = story_service.default_story_view(store_)
    view_ms = (time.perf_counter() - t_view) * 1000.0
    for s in view:
        for m in s.get("coverage") or []:
            if m.get("url"):
                index[_canon(str(m["url"]))] = {"storyId": s["id"], "coverage": s["coverage"]}
    _INDEX_CACHE.update(key=key, index=index)
    # The KEY carries the catalog row count, so under continuous ingestion this misses whenever a
    # single article lands — measuring the miss rate and its cost is the point of these counters,
    # not an assumption that it is cheap.
    try:
        obs_metrics.incr("rec_story_index_miss_total")
        obs_metrics.observe("rec_story_index_build_ms", (time.perf_counter() - t0) * 1000.0)
        obs_metrics.observe("rec_story_index_view_ms", view_ms)
    except Exception:
        pass
    return index


# ---------------------------------------------------------------- resolution
def resolve(rec: dict, ctx: dict, index: Optional[dict] = None) -> dict:
    """Choose the one explanation for an already-serialized recommendation payload.

    ``ctx`` is per-reader context the caller could actually compute (surfaces without a
    signed-in reader simply fall through the history-based priorities):
      reads          [{"url": canonical, "publisher": str, "publishedAt": str|None}], oldest first
      familiarity    callable(publisher) -> {"reads", "share", "band"}   (21a computation)
      top_topics     [str] — the reader's top reading categories
      topic_shares   {topic: share 0..1} — the reader's measured topic shares (C6; the SAME
                     ``hr.user_report`` numbers the explain drawer shows). When present, a
                     topic_continuity readerFact upgrades to the concrete
                     "{topic} represents {percent}% of your recent reading" — never estimated,
                     never claimed when the share is missing
      lean_shares    {"left","center","right": share 0..1} over the reader's POLITICAL reads
                     (C6; the report's own confidence-weighted viewpoint computation). When
                     present, an off-center bridge readerFact upgrades to
                     "{percent}% of your political reading leans {side}"
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
                reader_fact = {"key": "following_story", "params": {"n": len(mine)}}
                contribution = {"key": "story_coverage", "params": {"publisher": publisher}}
            elif gap_h is not None and gap_h >= _FOLLOW_UP_MIN_HOURS:
                variant, message = "follow_up", (
                    f"You already read the earlier coverage from {read_pub}. "
                    f"Here's {publisher}'s latest update.")
                reader_fact = {"key": "read_story_from", "params": {"publisher": read_pub}}
                contribution = {"key": "story_update", "params": {"publisher": publisher}}
            else:
                variant, message = "same_event", (
                    f"You already read this story from {read_pub}. "
                    f"Here's how {publisher} covered the same story.")
                reader_fact = {"key": "read_story_from", "params": {"publisher": read_pub}}
                contribution = {"key": "covered_same_story", "params": {"publisher": publisher}}
            return {"type": "story_match", "priority": 1, "variant": variant, "message": message,
                    "readerFact": reader_fact, "contribution": contribution,
                    # storyId + readUrl (+ headline) make the claim directly traceable: WHICH
                    # validated story cluster licensed it, and WHICH earlier read matched.
                    "evidence": {"storyId": story["storyId"], "readUrl": read["url"],
                                 "readHeadline": member.get("headline") or None,
                                 "readPublisher": read_pub, "recPublisher": publisher,
                                 "storyReads": len(mine),
                                 "readPublishedAt": read_at or None,
                                 "recPublishedAt": rec_at or None}}

    # -- P2 · bridge (a cross-cutting read is a stronger, more personal reason than a merely
    #                 shared topic — so it outranks topic_continuity as of 21a.4) -----------
    # Commit R1 defense in depth: the serializer's cross gate already requires the article be
    # political, but a direct caller could still hand us crossCutting=True on a non-political
    # payload — "another political perspective" is never licensed for one. (An absent flag is
    # legacy/unknown and does not block; the serializer no longer emits such payloads.)
    if bool(rec.get("crossCutting")) and art.get("political") is not False:
        # Commit 23: the SEMANTIC reader profile licenses the reader-first parts. Only a reader
        # who is meaningfully off-center gets "your reading has leaned {side}"; a balanced (or
        # unknown-mean) reader degrades to the article-centric message — honesty over symmetry.
        profile = _reader_political_profile(ctx)
        out = {"type": "bridge", "priority": 2,
               "message": "This article offers another political perspective.",
               "evidence": {"crossCutting": True, "articleLean": art.get("lean"),
                            "articlePolitical": art.get("political"),
                            "readerPoliticalProfile": profile}}
        if profile in ("leans_left", "leans_right"):
            side = profile.split("_")[1]
            # C6: prefer the CONCRETE measured fact over the generic label — "74% of your
            # political reading leans left" — but only when the context carries the reader's
            # real lean shares (the report's viewpoint computation). Never estimated.
            shares = _lean_shares(ctx)
            pct = _share_percent(shares.get(side)) if shares else None
            if pct is not None:
                out["evidence"]["readerLeanShares"] = shares
                out["readerFact"] = {"key": f"political_lean_{side}_share",
                                     "params": {"percent": pct}}
            else:
                out["readerFact"] = {"key": f"political_lean_{side}", "params": {}}
            out["contribution"] = {"key": "other_side_perspective", "params": {}}
        return out

    # -- P3 · new_publisher --------------------------------------------------
    fam_of: Optional[Callable] = ctx.get("familiarity")
    fam = fam_of(publisher) if (fam_of is not None and publisher) else None
    if fam and fam.get("band") in ("never", "rarely"):
        first = (f"You've never read {publisher} before." if fam["band"] == "never"
                 else f"You rarely read {publisher}.")
        reader_fact = ({"key": "never_read_publisher", "params": {"publisher": publisher}}
                       if fam["band"] == "never" else
                       {"key": "rarely_read_publisher",
                        "params": {"publisher": publisher, "n": int(fam.get("reads") or 0)}})
        return {"type": "new_publisher", "priority": 3,
                "message": f"{first} This broadens your source diversity.",
                "readerFact": reader_fact,
                "contribution": {"key": "add_new_publisher", "params": {}},
                "evidence": dict(fam, publisher=publisher)}

    # -- P4 · topic_continuity (fallback only — reached only after story, cross-cutting, and
    #    publisher reasons are exhausted, so a shared topic never displaces a provable one) --
    tops = [str(t) for t in (ctx.get("top_topics") or [])]
    if topic and topic in tops:
        cross = bool(rec.get("crossCutting"))
        second = ("Here's another perspective." if cross
                  else "Here's more coverage from another outlet.")
        out = {"type": "topic_continuity", "priority": 4,
               "message": f"You've been reading about {topic.lower()}. {second}",
               "readerFact": {"key": "top_topic", "params": {"topic": topic}},
               "contribution": {"key": "more_topic_coverage", "params": {}},
               "evidence": {"topic": topic, "topTopics": tops, "crossCutting": cross}}
        # C6: prefer the CONCRETE measured fact — "Politics represents 42% of your recent
        # reading" — when the context carries the reader's real topic shares (the same
        # hr.user_report numbers the explain drawer shows). Missing share → the plain fact.
        pct = _share_percent(_topic_shares(ctx).get(topic))
        if pct is not None:
            out["evidence"]["topicShare"] = round(float(_topic_shares(ctx)[topic]), 3)
            out["readerFact"] = {"key": "top_topic_share",
                                 "params": {"topic": topic, "percent": pct}}
        return out

    # -- P5 · long_tail --------------------------------------------------------
    if str(rec.get("strategy") or "") == "rwe-d":
        # Commit 23 documented exception: NO reader fact exists here by construction — when P5
        # fires, new_publisher (P3) did not, so the reader is NOT unfamiliar with this publisher.
        # Long tail therefore leads with its contribution (an item fact RWE-D licenses).
        return {"type": "long_tail", "priority": 5,
                "message": "This article introduces a less frequently recommended source.",
                "contribution": {"key": "rare_in_feeds", "params": {}},
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
        # Commit R1: "another political perspective" requires a political article. An absent flag
        # is legacy/unknown (older payloads) — only an explicit False is an over-claim.
        if art.get("political") is False:
            fails.append("bridge claims a political perspective on a non-political article")
    elif etype == "long_tail":
        if str(rec.get("strategy")) != "rwe-d":
            fails.append("recommendation was not chosen by rwe-d")
    # coverage_breadth: claim-free by construction — nothing to verify.

    # ---- Commit 23 · structured parts are SHOWN sentences too — validate each individually.
    # Additive: every whole-message check above is unchanged; these gates prove the semantic
    # readerFact/contribution objects are licensed by the same evidence, so the pipeline keeps
    # validating exactly what users see after the UI switches to the structured fields.
    rf = explanation.get("readerFact")
    co = explanation.get("contribution")

    def _part(part, name):
        """Shape check; returns (key, params) or records a failure."""
        if part is None:
            return None, {}
        if not isinstance(part, dict) or not isinstance(part.get("key"), str):
            fails.append(f"{name} is not a structured part")
            return None, {}
        return part["key"], dict(part.get("params") or {})

    rf_key, rf_p = _part(rf, "readerFact")
    co_key, co_p = _part(co, "contribution")

    if etype == "story_match":
        if rf_key not in ("read_story_from", "following_story"):
            fails.append(f"story readerFact key {rf_key!r} not allowed")
        elif rf_key == "read_story_from" and rf_p.get("publisher") != ev.get("readPublisher"):
            fails.append("story readerFact cites a publisher the evidence does not")
        elif rf_key == "following_story":
            if int(rf_p.get("n") or 0) != int(ev.get("storyReads") or 0) or int(ev.get("storyReads") or 0) < 2:
                fails.append("following_story count does not match storyReads evidence")
        want_co = {"same_event": "covered_same_story", "follow_up": "story_update",
                   "following": "story_coverage"}.get(str(explanation.get("variant")))
        if co_key != want_co:
            fails.append(f"story contribution key {co_key!r} not allowed for the variant")
        elif co_p.get("publisher") != ev.get("recPublisher"):
            fails.append("story contribution cites a publisher the evidence does not")
    elif etype == "new_publisher":
        want_rf = "never_read_publisher" if ev.get("band") == "never" else "rarely_read_publisher"
        if rf_key != want_rf:
            fails.append(f"new_publisher readerFact key {rf_key!r} does not match band")
        else:
            if rf_p.get("publisher") != publisher:
                fails.append("new_publisher readerFact cites the wrong publisher")
            if rf_key == "rarely_read_publisher" and int(rf_p.get("n") or -1) != int(ev.get("reads") or 0):
                fails.append("rarely_read_publisher count does not match evidence reads")
        if co_key != "add_new_publisher":
            fails.append(f"new_publisher contribution key {co_key!r} not allowed")
    elif etype == "topic_continuity":
        if rf_key not in ("top_topic", "top_topic_share") \
                or rf_p.get("topic") != str(art.get("topic") or ""):
            fails.append("topic readerFact does not cite the article's topic")
        elif rf_key == "top_topic_share":
            # C6: the concrete percent must be the reader's REAL measured share, from the ctx
            want_pct = _share_percent(_topic_shares(ctx).get(str(art.get("topic") or "")))
            if want_pct is None:
                fails.append("top_topic_share claimed but the context carries no such share")
            elif int(rf_p.get("percent") or -1) != want_pct:
                fails.append("top_topic_share percent does not match the reader's measured share")
            if ev.get("topicShare") is None:
                fails.append("top_topic_share readerFact without topicShare evidence")
        if co_key != "more_topic_coverage":
            fails.append(f"topic contribution key {co_key!r} not allowed")
    elif etype == "bridge":
        profile = ev.get("readerPoliticalProfile")
        if profile not in ("balanced", "leans_left", "leans_right"):
            fails.append(f"bridge evidence has no valid readerPoliticalProfile: {profile!r}")
        elif "reader_mean_lean" in ctx and _reader_political_profile(ctx) != profile:
            fails.append("readerPoliticalProfile does not match the recomputed profile")
        if profile == "balanced":
            if rf is not None or co is not None:
                fails.append("balanced bridge must not claim reader-first parts")
        else:
            side = str(profile).split("_")[-1]
            if rf_key == f"political_lean_{side}_share":
                # C6: the concrete percent must be the reader's REAL lean share, from the ctx
                shares = _lean_shares(ctx)
                want_pct = _share_percent((shares or {}).get(side))
                if want_pct is None:
                    fails.append("political lean share claimed but the context carries no shares")
                elif int(rf_p.get("percent") or -1) != want_pct:
                    fails.append("political lean share percent does not match the reader's shares")
                if ev.get("readerLeanShares") != shares:
                    fails.append("readerLeanShares evidence does not match the context shares")
            elif rf_key != f"political_lean_{side}":
                fails.append(f"bridge readerFact {rf_key!r} does not match profile {profile!r}")
            if co_key != "other_side_perspective":
                fails.append(f"bridge contribution key {co_key!r} not allowed")
    elif etype == "long_tail":
        if rf is not None:
            fails.append("long_tail must not claim a reader fact (documented exception)")
        if co_key != "rare_in_feeds":
            fails.append(f"long_tail contribution key {co_key!r} not allowed")
    elif etype == "coverage_breadth":
        if rf is not None or co is not None:
            fails.append("coverage_breadth is claim-free and must carry no parts")
    return fails
