"""Article-analysis reader enrichment (A3.1) — layer reader-relative sections onto an anonymous
analysis WITHOUT touching the analyzer.

``article_analyzer.analyze`` stays deterministic, anonymous, and zero-write; this module consumes
its output and, for a signed-in **measured** reader, fills the contract slots the analyzer pins
null:

  * ``explanation``    — the Evidence Resolver's ONE licensed explanation for the analyzed article,
                         computed exactly as ``/api/recommendations`` computes a card's explanation
                         (same ``evidence_resolver.resolve`` over the same ``explanation_context``),
                         so the vocabulary never disagrees with the product's recommendations;
  * ``recommendation`` — a STABLE reader-relative *verdict* (``wouldBroaden`` + a closed set of
                         ``reasons`` + ``blindSpotTopic``), assembled from existing signals
                         (cross-cutting via ``_cross_of``, outlet familiarity from the context,
                         blind-spot topics from the reader's stored report, story following from
                         the resolver) — **no new recommendation scoring, and no ranking**: an
                         analyzed article is often not even a recommendable catalog node.

A3.3a adds ``recommendation.nextArticle`` (flat additive inside the verdict): the reader's "read
next" pick. Two selection paths, each routed to the system that can license its claim:

  * ``source: "story"`` — a sibling of the analyzed article's own Story-Service cluster (the same
    clusters the analysis's story section displays). Selection **bypasses the recommendation
    engine by design** (approved A3.3 refinement): the claim is "same story, different publisher",
    which the Story Service licenses — the engine's rank adds nothing and its feed-admission gate
    would starve the path. Deterministic rules, not scoring: different publisher, unread, the
    analyzed article excluded, prefer a different lean bucket than the analyzed article's (only
    when both buckets are known), newest ``publishedAt`` first, canonical-URL tie-break.
  * ``source: "feed"`` — when no story sibling exists: the reader's REAL served feed's top pick,
    ``personalizer.recommendations(uid, None, reader_params)[0]`` verbatim — only the engine can
    license "from your recommendations". Lazy: the feed is only computed when the story path
    yields nothing.

The pick's ``explanation`` is whatever ``evidence_resolver.resolve`` licenses for it (the same
vocabulary as every card); the story relation travels as ``source`` provenance, never as a resolver
claim. No recommendation-engine metadata is fabricated for story picks (the object carries only
``source`` / ``article`` / ``explanation``).

A4.1 fills the third reserved slot, ``personal`` — the reader's STANDING relative to the analyzed
article: facts read verbatim from the same measured context, never scores or projections (the
``healthImpact`` lesson — nothing here is derived arithmetic over denominators the context doesn't
carry):

  * ``alreadyRead`` — the analyzed canonical URL is in the reader's history (and when);
  * ``publisher``   — the reader's familiarity band for the outlet (the SAME lookup the verdict's
                      new/rarely-read reasons use);
  * ``topic``       — the topic + the reader's measured share of it (only when their report claims
                      one) + the stored report's blind-spot ``gap`` when the verdict flagged it;
  * ``viewpoint``   — the article's lean bucket vs the reader's measured viewpoint shares;
                      ``addsMissing`` is claimed only when the article is political, its bucket is
                      known, and the reader's share of that bucket is exactly zero — a set-
                      membership fact, not a forecast;
  * ``story``       — the reader's own coverage of the analyzed article's cluster, from the SAME
                      memoized story index (no extra store pass): members read, buckets covered,
                      and the bucket this article would add. Present only when they have actually
                      read part of the story.

One pass, one vocabulary: the reads map, familiarity band, and blind-spot mapping are computed once
in ``enrich`` and shared by the verdict, the story-sibling pick, and ``personal``, so the sections
can never disagree with each other.

Read-only. It reads the reader's (cached) measured context and stored report; it creates no reads,
recommendation events, or feedback. Anonymous / non-measured callers get null sections, so the
anonymous analysis stays byte-identical to A2.
"""
from __future__ import annotations

import pathlib
import sys
from collections.abc import Mapping
from typing import Callable, Optional

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import api_server as engine          # noqa: E402  _cross_of — the ONE cross-cutting gate (shared)
import discover                      # noqa: E402  feed_article_to_article — the canonical Article shape
import evidence_resolver as er       # noqa: E402  resolve / story_index — the explanation vocabulary

#: The closed ``reasons`` vocabulary, in a stable presentation order. A verdict never emits anything
#: outside this set, so a surface can map each reason to copy without guessing.
_REASON_ORDER = ("following_story", "cross_cutting", "blind_spot",
                 "new_publisher", "rarely_read_publisher", "familiar_topic")
#: The subset that actually *broadens* a reading diet (drives ``wouldBroaden``). "following_story"
#: and "familiar_topic" explain relevance without claiming the article broadens anything.
_BROADENING = frozenset({"cross_cutting", "blind_spot", "new_publisher", "rarely_read_publisher"})

_NULL = {"explanation": None, "recommendation": None, "personal": None}

#: Stable presentation order for lean buckets (matches the analyzer's vocabulary).
_BUCKET_ORDER = ("left", "center", "right")


def _num(v, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _sign(v: float) -> float:
    return 1.0 if v > 0 else (-1.0 if v < 0 else 0.0)


def _rec_article(analysis: dict) -> dict:
    """The synthetic recommendation ``article`` the resolver consumes, from the analysis facts.

    ``lean`` / ``political`` / ``topic`` come from ``scoring`` — the flat projection present on
    EVERY analyzed article and the authoritative source for the cross-cutting gate (the catalog
    ``article`` shape from ``discover.feed_article_to_article`` omits ``political``). ``url`` /
    ``publisher`` / ``publishedAt`` prefer the richer catalog ``article`` and fall back to the
    ``scoring`` outlet + the canonical input URL for a scored-url-only miss (``lean`` may be
    ``None`` — then nothing cross-cutting is ever claimed)."""
    art = analysis.get("article") if isinstance(analysis.get("article"), dict) else {}
    sc = analysis.get("scoring") or {}
    canon = (analysis.get("input") or {}).get("canonicalUrl")
    url = art.get("url") or art.get("id") or canon
    return {"url": url, "id": art.get("id") or url,
            "publisher": art.get("publisher") or sc.get("outlet") or "",
            "topic": sc.get("topic") or art.get("topic") or "",
            "lean": sc.get("lean", art.get("lean")),
            "political": sc.get("political", art.get("political")),
            "publishedAt": art.get("publishedAt")}


def _familiarity_band(ctx: dict, publisher: str) -> Optional[str]:
    """The reader's three-tier familiarity band for ``publisher`` (``never`` / ``rarely`` /
    ``familiar``) from the context's own lookup — or ``None`` when it can't be computed."""
    fam = ctx.get("familiarity")
    if not callable(fam) or not publisher:
        return None
    try:
        return fam(publisher).get("band")
    except Exception:
        return None


def _is_num(v) -> bool:
    """A real, non-NaN number (an unknown outlet's stored lean can be NaN — never a bucket)."""
    return isinstance(v, (int, float)) and v == v


def _blind_spot_gaps(blind_spot_topics) -> dict:
    """Normalise the blind-spot input to ``{topic: gap|None}`` — a mapping (the stored report's
    topic -> gap, the production shape) or a bare sequence of topics (gap unknown) both work, so
    the ONE normalisation feeds the verdict (membership) and ``personal`` (membership + gap)."""
    if isinstance(blind_spot_topics, Mapping):
        return {str(t): (float(g) if _is_num(g) else None) for t, g in blind_spot_topics.items()}
    return {str(t): None for t in (blind_spot_topics or ())}


def _resolver_rec(article: dict, ctx: dict) -> dict:
    """A synthetic resolver ``rec`` for a candidate next-article: the resolver article vocabulary
    plus the cross-cutting flag via the ONE shared gate (same construction as the analyzed
    article's own enrichment)."""
    lean, political = article.get("lean"), bool(article.get("political"))
    user_side = _sign(_num(ctx.get("reader_mean_lean")))
    cross = bool(political and _is_num(lean) and engine._cross_of(user_side, float(lean), political))
    return {"article": article, "crossCutting": cross}


def _select_story_sibling(analysis: dict, ctx: dict, store, index: Optional[dict],
                          read: "Mapping | set") -> "dict | None":
    """The deterministic story-relative pick: a sibling of the analyzed article's own cluster —
    different publisher, unread, the analyzed article excluded; prefer a different lean bucket than
    the analyzed article's (only when both buckets are known — no preference is ever derived from
    an unknown lean), newest first, canonical-URL tie-break. Selection runs on the Story Service's
    own coverage (the index); only the WINNER is hydrated from the store. ``read`` is the reader's
    canonical-URL membership (built ONCE in ``enrich`` and shared with ``_personal``). Returns
    ``{"source": "story", "article", "explanation"}`` or ``None``. Bypasses the recommendation
    engine by design (the claim is story membership, not rank)."""
    if store is None:
        return None
    canon = (analysis.get("input") or {}).get("canonicalUrl")
    entry = (index or {}).get(canon) if canon else None
    if not entry:
        return None
    my_pub = engine._prettify(str((analysis.get("article") or {}).get("publisher") or ""))
    my_bucket = (analysis.get("scoring") or {}).get("leanBucket")

    cands = []
    for m in entry.get("coverage") or []:
        u = m.get("url")
        if not u:
            continue
        c = er._canon(str(u))
        if c == canon or c in read:
            continue
        pub = engine._prettify(str(m.get("publisher") or ""))
        if not pub or pub == my_pub:
            continue
        cands.append({"canon": c, "publisher": pub,
                      "bucket": m.get("leanBucket") if isinstance(m.get("leanBucket"), str) else None,
                      "publishedAt": str(m.get("publishedAt") or "")})
    if not cands:
        return None
    # Deterministic, order-free selection (stable sorts, least- to most-significant rule):
    cands.sort(key=lambda c: c["canon"])                        # 3. canonical-URL tie-break
    cands.sort(key=lambda c: c["publishedAt"], reverse=True)    # 2. newest first ("" sorts last)
    if my_bucket:                                               # 1. cross-viewpoint preference
        cands.sort(key=lambda c: 0 if (c["bucket"] and c["bucket"] != my_bucket) else 1)
    pick = cands[0]

    row = store.get_feed_article(pick["canon"])
    if not row:
        return None
    article = discover.feed_article_to_article(row)             # the canonical Article shape
    sc = row.get("scored") or {}
    rec_article = {"url": article.get("url") or article.get("id"), "id": article.get("id"),
                   "publisher": article.get("publisher") or "", "topic": article.get("topic") or "",
                   "lean": sc.get("lean"), "political": sc.get("political"),
                   "publishedAt": article.get("publishedAt")}
    try:
        explanation = er.resolve(_resolver_rec(rec_article, ctx), ctx, index)
    except Exception:
        explanation = None
    return {"source": "story", "article": article, "explanation": explanation}


def _feed_next(personalizer, store, uid: int, ctx: dict, index: Optional[dict]) -> "dict | None":
    """The engine-licensed fallback: the reader's REAL served feed's top pick, with their own
    slider-mapped params (so the analyzer never shows a feed pick their Recommendations page
    wouldn't). The pick is ``recommendations(...)[0]`` verbatim; its explanation is resolved with
    the SAME resolver + context the feed endpoint's post-pass uses. Best-effort: any failure
    yields ``None``, never an error."""
    try:
        params = None
        if store is not None:
            try:
                params = engine.rec_params_from_settings(store.get_settings(uid))
            except Exception:
                params = None
        recs = personalizer.recommendations(uid, None, params) or []
    except Exception:
        return None
    if not recs:
        return None
    top = recs[0]
    article = top.get("article") or {}
    if not article:
        return None
    try:
        explanation = er.resolve(top, ctx, index)
    except Exception:
        explanation = None
    # Only source/article/explanation travel — feed-serving metadata (strategy, reason, ...) is
    # the feed's provenance, not this contract's.
    return {"source": "feed", "article": article, "explanation": explanation}


def _verdict(article: dict, explanation: dict, ctx: dict, cross_cutting: bool,
             blind_gaps: dict, band: Optional[str]) -> dict:
    """The stable reader-relative verdict, assembled from existing signals only. ``blind_gaps`` and
    ``band`` are the enrich-level shared computations (one pass — ``personal`` reads the same
    values, so the sections can never disagree)."""
    topic = str(article.get("topic") or "")
    blind_topic = topic if topic and topic in blind_gaps else None

    reasons = set()
    if (explanation or {}).get("type") == "story_match":
        reasons.add("following_story")
    if cross_cutting:
        reasons.add("cross_cutting")
    if blind_topic:
        reasons.add("blind_spot")
    if band == "never":
        reasons.add("new_publisher")
    elif band == "rarely":
        reasons.add("rarely_read_publisher")
    # A top reading topic explains relevance (never a broadening claim), and only when it isn't
    # already the more important blind-spot signal.
    if topic and not blind_topic and topic in {str(t) for t in (ctx.get("top_topics") or [])}:
        reasons.add("familiar_topic")

    ordered = [r for r in _REASON_ORDER if r in reasons]
    return {"wouldBroaden": any(r in _BROADENING for r in ordered),
            "reasons": ordered, "blindSpotTopic": blind_topic}


def _personal(analysis: dict, ctx: dict, article: dict, band: Optional[str],
              reads_by_url: dict, index: Optional[dict], blind_gaps: dict,
              recommendation: dict) -> dict:
    """A4.1 — the reader's standing relative to the analyzed article: a closed five-key object of
    facts read verbatim from the shared context / index / stored-report inputs. Every block nulls
    independently when its licensing data is absent (unknown outlet -> no ``publisher``; NaN-gated
    viewpoint shares -> no ``readerShares`` and never an ``addsMissing`` claim; no cluster reads ->
    no ``story``). No derived "after reading" arithmetic — projections are set-membership facts
    only. Consistency by construction: ``band`` is the verdict's band, blind-spot membership IS the
    verdict's ``blindSpotTopic``, and ``reads_by_url`` is the same map the sibling pick excluded
    against."""
    sc = analysis.get("scoring") or {}
    canon = (analysis.get("input") or {}).get("canonicalUrl")

    # alreadyRead — canonical identity, so a catalog hit and a URL-only miss both resolve.
    row = reads_by_url.get(canon) if canon else None
    already = ({"at": str(row["publishedAt"]) if row.get("publishedAt") else None}
               if row is not None else None)

    pub = str(article.get("publisher") or "")
    publisher = ({"name": pub, "band": band}
                 if pub and band in ("never", "rarely", "familiar") else None)

    topic = str(article.get("topic") or "")
    topic_block = None
    if topic:
        shares = ctx.get("topic_shares")
        share = shares.get(topic) if isinstance(shares, Mapping) else None
        blind = ({"gap": blind_gaps.get(topic)}
                 if recommendation.get("blindSpotTopic") == topic else None)
        topic_block = {"topic": topic,
                       "share": float(share) if _is_num(share) else None,
                       "blindSpot": blind}

    bucket = sc.get("leanBucket") if sc.get("leanBucket") in _BUCKET_ORDER else None
    viewpoint = None
    if bucket:
        ls = ctx.get("lean_shares")
        reader_shares = ({b: float(ls[b]) for b in _BUCKET_ORDER}
                         if isinstance(ls, Mapping) and all(_is_num(ls.get(b)) for b in _BUCKET_ORDER)
                         else None)
        adds_missing = bool(bool(article.get("political")) and reader_shares is not None
                            and reader_shares[bucket] == 0.0)
        viewpoint = {"articleBucket": bucket, "readerShares": reader_shares,
                     "addsMissing": adds_missing}

    story = None
    entry = (index or {}).get(canon) if canon else None
    if entry:
        buckets, count = set(), 0
        for m in entry.get("coverage") or []:
            u = m.get("url")
            if not u:
                continue
            c = er._canon(str(u))
            if c == canon or c not in reads_by_url:      # the analyzed article itself never counts
                continue
            count += 1
            if m.get("leanBucket") in _BUCKET_ORDER:
                buckets.add(m["leanBucket"])
        if count >= 1:                                   # a real relationship, or no story claim
            story = {"readCount": count,
                     "bucketsRead": [b for b in _BUCKET_ORDER if b in buckets],
                     "addsBucket": bucket if bucket and bucket not in buckets else None}

    return {"alreadyRead": already, "publisher": publisher, "topic": topic_block,
            "viewpoint": viewpoint, "story": story}


def enrich(analysis: dict, ctx: dict, *, index: Optional[dict] = None,
           blind_spot_topics=(), store=None,
           feed_next: "Callable[[], dict | None] | None" = None) -> dict:
    """The reader-enrichment assembler: ``{"explanation", "recommendation", "personal"}`` for an
    analyzed article given a measured reader's ``ctx`` (the Evidence Resolver's documented
    context). Total and deterministic over its inputs — an ``invalid_url`` analysis or an article
    with no resolvable URL yields null sections; it never raises and never writes.

    A3.3a: the verdict additionally carries ``nextArticle`` — the story-relative pick when the
    analyzed article sits in a cluster (``store`` supplies the winner's hydration; the engine is
    never consulted), else the value of the LAZY ``feed_next`` callable (the engine-licensed
    fallback, invoked only when the story path yields nothing), else ``None``.

    A4.1: ``personal`` — the reader's standing (see ``_personal``). The familiarity band, the
    blind-spot mapping, and the reader's canonical-URL reads map are computed HERE, once, and
    shared by the verdict, the sibling pick, and ``personal`` — single-pass consistency."""
    if not isinstance(analysis, dict) or analysis.get("status") != "analyzed":
        return dict(_NULL)
    article = _rec_article(analysis)
    if not article.get("url"):
        return dict(_NULL)

    band = _familiarity_band(ctx, str(article.get("publisher") or ""))
    blind_gaps = _blind_spot_gaps(blind_spot_topics)
    reads_by_url: dict = {}                       # canonical URL -> its (first) read row
    for r in (ctx.get("reads") or []):
        u = str(r.get("url") or "")
        if u and u not in reads_by_url:
            reads_by_url[u] = r

    rec = _resolver_rec(article, ctx)
    explanation = er.resolve(rec, ctx, index)
    recommendation = _verdict(article, explanation, ctx, rec["crossCutting"], blind_gaps, band)

    next_article = _select_story_sibling(analysis, ctx, store, index, reads_by_url)
    if next_article is None and feed_next is not None:
        try:
            next_article = feed_next()
        except Exception:
            next_article = None
    recommendation["nextArticle"] = next_article

    personal = _personal(analysis, ctx, article, band, reads_by_url, index, blind_gaps,
                         recommendation)
    return {"explanation": explanation, "recommendation": recommendation, "personal": personal}


def _blind_spot_topics(store, uid: int) -> dict:
    """The reader's blind spots from their latest stored report (read-only), as ``{topic: gap}``
    (gap ``None`` when the stored entry has no usable number — membership is the fact, the gap is
    enrichment). Defensive: any missing/odd shape yields ``{}`` so a ``blind_spot`` claim only ever
    comes from real data. Topics are already prettified in the stored report, matching the analysis
    ``scoring.topic``."""
    if store is None:
        return {}
    try:
        rep = store.latest_report(uid) or {}
        out = {}
        for b in rep.get("blindSpots") or []:
            t = b.get("topic") if isinstance(b, dict) else None
            if t and str(t).strip():
                g = b.get("gap")
                out[str(t)] = float(g) if _is_num(g) else None
        return out
    except Exception:
        return {}


def enrich_for_reader(personalizer, store, uid: "int | None", analysis: dict) -> dict:
    """The reader-relative sections for a signed-in **measured** reader, else null sections.

    Gated on ``has_measured`` (the same routing switch the report/recommendations use), so an
    anonymous or non-measured caller gets null ``explanation`` / ``recommendation`` / ``personal``
    sections and the base analysis stays byte-identical to A2. Best-effort: any failure degrades to
    null sections —
    enrichment never breaks the analysis. Read-only: it materialises only the reader's own (cached)
    measured context + report and creates no reads / recommendation events / feedback."""
    if uid is None or personalizer is None:
        return dict(_NULL)
    try:
        if not personalizer.has_measured(uid):
            return dict(_NULL)
        ctx = personalizer.explanation_context(uid)
        # build_inline: this runs inside /api/analyze, whose documented contract is that the
        # request writes nothing anywhere — the default path's boot-window kick spawns a refresh
        # that eventually writes the cache and identity ledger, which that contract forbids.
        index = er.story_index(store, build_inline=True) if store is not None else {}
        return enrich(analysis, ctx, index=index, blind_spot_topics=_blind_spot_topics(store, uid),
                      store=store,
                      feed_next=lambda: _feed_next(personalizer, store, uid, ctx, index))
    except Exception:
        return dict(_NULL)
