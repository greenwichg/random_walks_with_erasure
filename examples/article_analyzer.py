"""Article Analyzer — the analysis service (milestone A1: anonymous scope, no route).

A user pastes a news-article URL; this module produces the Information Health analysis of
that ARTICLE — its scored attributes and its story context — by orchestrating the existing
pipeline. Approved architecture (2026-07 review, rev. 2 lifecycle):

    * CATALOG-FIRST: the canonical URL is resolved against the FeedArticle catalog; a hit
      reuses the stored article + its scoring verbatim (`store.get_feed_article` +
      `discover.feed_article_to_article` — the one Article shape product-wide).
    * FETCHLESS: a miss is scored IN MEMORY from the URL plus whatever metadata the client
      supplies (`ingest.Scorer` + the baseline enricher via `rss_ingest.make_scorer` — the
      same scoring configuration as ingestion). No server-side fetch exists (the og-metadata
      fetcher is the separately-flagged A6 milestone, deliberately out of the first release).
    * ZERO-WRITE: analysis never touches the database — not even the scored-article cache
      (`score_with_cache` is deliberately NOT used; the scorer is deterministic, so a later
      Mark-as-Read that echoes the same metadata scores identically by construction). The
      lifecycle invariant — analysis alone must never influence Information Health,
      recommendations, analytics, or the recommendation graph — is therefore structural.
    * HONEST STORY CLAIMS: a catalog article's cluster membership comes from the SAME stories
      the Stories page serves; a non-catalog article never claims membership — at most a
      ``similarStory`` advisory (best title-token Jaccard >= the production clustering
      threshold), because `evidence_resolver.validate()`'s story gates require real
      membership.

ANALYSIS CONTRACT v1 (A1 sections; stable, additive evolution — breaking changes bump
``analysisVersion``):

    {
      "analysisVersion": 1,
      "input":   {"url", "canonicalUrl"},
      "status":  "analyzed" | "invalid_url",
      "source":  "catalog" | "scored_url_only" | null,   # provenance of the facts below
      "article": {...the canonical Article shape...} | null,       # catalog hits only
      "scoring": {"outlet", "lean": float|null, "leanBucket": "left|center|right"|null,
                  "topic", "political": bool, "emotion": {label: share}|null,
                  "register": float|null, "confidence": float|null} | null,
      "story":   {"matched": true, "storyId", "articleCount", "publisherCount",
                  "distribution": {left, center, right},   # the Story's own normalized
                  "missingViewpoints": [bucket]}           # publisher SHARES (sum to 1)
               | {"matched": false, "similarStory": {"storyId", "similarity"} | null}
               | null,
      "recommendation": null,          # A2 — reader-relative eligibility (signed-in)
      "personal":       null,          # A3 — the what-if projection (signed-in, measured)
      "explanation":    null,          # A2 — resolver vocabulary over the analysis facts
      "notes": [str],
    }

Every value is JSON-safe (NaN -> null) and the analysis carries no timestamps of its own —
deterministic per (store snapshot, url, metadata)."""
from __future__ import annotations

import math
import pathlib
import sys
from typing import Optional

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import api_server as engine            # noqa: E402  _lean_bucket / _prettify (serializer helpers)
import clustering                      # noqa: E402  title_tokens / jaccard (production thresholds)
import coverage_comparison             # noqa: E402  L0 cluster comparison (deterministic, no text)
import discover                        # noqa: E402  feed_article_to_article (the Article shape)
import ingest                          # noqa: E402  normalize/validate/canonicalize + RawRead
import rss_ingest                      # noqa: E402  make_scorer — the ONE scoring configuration
import story_service                   # noqa: E402  the stories the product actually serves

ANALYSIS_VERSION = 1

_BUCKETS = ("left", "center", "right")


def _num_or_none(v) -> Optional[float]:
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def _json_safe(obj):
    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    return obj


def _scoring_block(outlet, lean, topic, political, emotion, register, confidence) -> dict:
    lean = _num_or_none(lean)
    return {"outlet": outlet or "", "lean": lean,
            "leanBucket": engine._lean_bucket(lean) if lean is not None else None,
            "topic": engine._prettify(topic) if topic else "",
            "political": bool(political),
            "emotion": dict(emotion) if isinstance(emotion, dict) else None,
            "register": _num_or_none(register),
            "confidence": _num_or_none(confidence)}


def _story_block(store_, canon: str, title: str, out: Optional[dict] = None) -> Optional[dict]:
    """Membership from the Story Service's own clusters for catalog articles; at most a
    ``similarStory`` advisory (never membership) for anything else.

    ``out``, when given, receives ``{"story": <the matched Story>, "member": <its coverage row>}``
    on a membership hit. Coverage Comparison needs the cluster this call already found, and
    resolving it twice would mean a second story view build on a zero-write endpoint."""
    try:
        # The cached default view, not a fresh per-request clustering — the same fix, for the same
        # measured reason, as publisher co-coverage (root-cause report, 2026-08-02). Bonus: the ids
        # are the stabilized ones /api/stories serves, so a membership link now lands on the same
        # story id the story page itself uses.
        #
        # build_inline: /api/analyze's zero-write contract. The default path's boot-window kick
        # spawns a refresh that eventually writes the cache and the identity ledger — writes this
        # endpoint has contracted never to cause. The inline build is read-only and uncached.
        stories = story_service.default_story_view(store_, build_inline=True)
    except Exception:
        return None
    best, best_sim = None, 0.0
    probe = clustering.title_tokens(title or "")
    for s in stories:
        for m in s.get("coverage") or []:
            key = str(m.get("id") or m.get("url") or "")
            if key and ingest.canonical_url(key) == canon:
                # the Story's own normalized per-bucket publisher SHARES (sum to 1)
                dist = {b: round(float((s.get("distribution") or {}).get(b) or 0.0), 3)
                        for b in _BUCKETS}
                if out is not None:
                    out["story"], out["member"] = s, m
                return {"matched": True, "storyId": s["id"],
                        "articleCount": s.get("totalCoverage"),
                        "publisherCount": s.get("publisherCount"),
                        "distribution": dist,
                        "missingViewpoints": [b for b in _BUCKETS if dist[b] == 0.0]}
            if probe:
                sim = clustering.jaccard(probe, clustering.title_tokens(m.get("headline") or ""))
                if sim > best_sim:
                    best, best_sim = s, sim
    if best is not None and best_sim >= clustering.DEFAULT_SIM:
        return {"matched": False,
                "similarStory": {"storyId": best["id"], "similarity": round(best_sim, 3)}}
    return {"matched": False, "similarStory": None}


def analyze(store_, url: str, metadata: Optional[dict] = None) -> dict:
    """Analyze one article URL. ``store_`` is only read; ``metadata`` is optional
    client-supplied page context (the ``ReadInput`` fields: title, description, outlet,
    category, subtitle, political, publishedAt) that improves fetchless scoring exactly as
    the extension's page capture does. Returns ANALYSIS CONTRACT v1."""
    md = dict(metadata or {})
    norm = ingest.normalize_url(str(url or ""))
    report = {"analysisVersion": ANALYSIS_VERSION,
              "input": {"url": norm, "canonicalUrl": None},
              "status": "analyzed", "source": None, "article": None, "scoring": None,
              "story": None, "coverageComparison": None,
              "recommendation": None, "personal": None, "explanation": None,
              # Article Insights (docs/ARTICLE_INSIGHTS.md): pinned null HERE, filled by the
              # endpoint from the insights cache — the same shape as the A3 enrichment sections,
              # so wire-vs-service byte parity holds for un-enriched requests.
              "insights": None,
              "notes": []}
    if not ingest.has_host(norm):
        report["status"] = "invalid_url"
        report["notes"].append("not a fetchable URL (no plausible host)")
        return _json_safe(report)
    canon = ingest.canonical_url(norm)
    report["input"]["canonicalUrl"] = canon

    row = store_.get_feed_article(canon) if store_ is not None else None
    if row:
        report["source"] = "catalog"
        report["article"] = discover.feed_article_to_article(row)
        sc = row.get("scored") or {}
        report["scoring"] = _scoring_block(
            row.get("publisher") or sc.get("outlet"), sc.get("lean"), sc.get("category"),
            sc.get("political"), sc.get("emotion"), sc.get("register"), sc.get("selective"))
        title = report["article"].get("headline") or ""
    else:
        # Fetchless in-memory scoring — the SAME deterministic scorer as ingestion; NEVER
        # score_with_cache (analysis must write nothing, not even the scoring cache).
        scorer = rss_ingest.make_scorer()
        sr = scorer.score(ingest.RawRead(
            url=norm, title=str(md.get("title") or ""),
            outlet=str(md.get("outlet") or ""), category=str(md.get("category") or ""),
            political=md.get("political"), subtitle=str(md.get("subtitle") or ""),
            description=str(md.get("description") or "")))
        report["source"] = "scored_url_only"
        report["scoring"] = _scoring_block(
            sr.outlet, sr.lean, sr.category, sr.political,
            getattr(sr, "emotion", None), getattr(sr, "register", None),
            getattr(sr, "confidence", None))
        title = sr.title or str(md.get("title") or "")
        if not md.get("title"):
            report["notes"].append("no page metadata supplied — scoring degrades to "
                                   "URL-level signals (outlet, path section)")
        if report["scoring"]["lean"] is None:
            report["notes"].append("outlet not in the registry — political lean unknown "
                                   "(excluded from lean-based claims, as everywhere)")

    matched: dict = {}
    report["story"] = _story_block(store_, canon, title, out=matched)
    # Coverage Comparison (docs/COVERAGE_COMPARISON_DESIGN.md), tier L0: counted facts about the
    # cluster this article is already known to belong to. Pure computation over the story just
    # resolved plus the article's own located facts — no text is examined, nothing is written, and
    # a gated cluster yields an explicit "unavailable + reason" the UI renders as nothing.
    if matched.get("story") is not None:
        try:
            m = matched.get("member") or {}
            countries = store_.article_event_countries(canon) if store_ is not None else []
            report["coverageComparison"] = coverage_comparison.compare(
                {"publisher": m.get("publisher") or (report["scoring"] or {}).get("outlet"),
                 "url": m.get("url") or canon,
                 "leanBucket": m.get("leanBucket") or (report["scoring"] or {}).get("leanBucket"),
                 "register": m.get("register")},
                matched["story"], target_countries=countries, member=m)
        except Exception:
            # Never fail an analysis over a comparison — but never fail SILENTLY either. The
            # first production article hit a shape assumption in this module, and because the
            # catch-all swallowed it the card simply never rendered anywhere, with nothing to
            # see. A counter makes a systematic failure visible in OBS1 instead of invisible.
            report["coverageComparison"] = None
            try:
                import obs_metrics
                obs_metrics.incr("coverage_comparison_error_total")
            except Exception:
                pass
    return _json_safe(report)
