"""shape.py — the ``/v1`` wire shape: identity on every object, licence withholding, versions.

Three rules, applied at the serializer and nowhere else (the same discipline as
``RWE_PUBLIC_FACTUALITY``: a disabled deployment transmits nothing, not a hidden field):

1. **Licence class decides fields.** An article whose class is outside the caller's plan keeps its
   identity, publisher, time and story membership — ours — and loses the provider's delivery
   (headline, snippet, link, image), listed under ``withheld``. ``reader_private`` rows and
   provisional rows never appear at all.
2. **Third-party ratings are a deployment switch.** AllSides-derived lean (and everything
   derived from it: the story distribution, the blindspot side, the low-credibility list) and the
   MBFC verdicts ship on ``/v1`` only when ``RWE_PLATFORM_PUBLISH_RATINGS=1`` — the operator holds
   that licence or does not. Wikipedia text (CC BY-SA) likewise, ``RWE_PLATFORM_PUBLISH_WIKIPEDIA``.
3. **Every response says what it is.** ``meta.versions`` names the scorer, the build, its config
   hash and the registry snapshot the answer reflects; ``meta.asOf`` says when.

Bodies are never on the wire (the consumer serializer already drops them); snippets are clamped.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import identity
import ingest
import licence
import story_service

DESCRIPTION_MAX = 300
ARTICLE_RATING_FIELDS = ("lean", "leanBucket", "publisherLean")
STORY_RATING_FIELDS = ("distribution", "blindspotSide", "blindspotWithheld",
                       "lowCredibilityPublishers")
_INTERNAL = ("sourceType",)


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def publish_ratings() -> bool:
    return _flag("RWE_PLATFORM_PUBLISH_RATINGS")


def publish_wikipedia() -> bool:
    return _flag("RWE_PLATFORM_PUBLISH_WIKIPEDIA")


def clamp(text, n: int = DESCRIPTION_MAX) -> str:
    s = (text or "").strip()
    if len(s) <= n:
        return s
    cut = s[:n].rsplit(" ", 1)[0].rstrip(" ,;:-")
    return (cut or s[:n]) + "…"


def versions() -> dict:
    return {"scorer": ingest.SCORER_VERSION, "build": story_service.BUILD_VERSION,
            "buildConfig": story_service.build_config_hash(),
            "registry": identity.registry_version(),
            "publisherIdScheme": identity.PUBLISHER_ID_SCHEME}


def envelope(data, *, request_id: "str | None" = None, **extra) -> dict:
    meta = {"requestId": request_id, "asOf": datetime.now(timezone.utc).isoformat(),
            "versions": versions(), "ratingsPublished": publish_ratings()}
    meta.update({k: v for k, v in extra.items() if v is not None})
    return {"data": data, "meta": meta}


def _drop_none(d: dict) -> dict:
    return {k: v for k, v in d.items() if v is not None}


def article(a: dict, meta: "dict | None", allowed) -> "dict | None":
    """One consumer Article dict -> its ``/v1`` shape, or ``None`` when it must not appear."""
    meta = meta or {}
    cls = meta.get("licenceClass") or licence.UNKNOWN
    if cls == licence.READER_PRIVATE or meta.get("articleState") == "provisional":
        return None
    out = {k: v for k, v in a.items() if k not in _INTERNAL}
    canonical = out.pop("id", None)
    out["articleId"] = meta.get("articleId")
    out["publisherId"] = meta.get("publisherId") or identity.publisher_id_for(a.get("publisher"))
    out["description"] = clamp(out.get("description"))
    withheld = set()
    reduced = licence.withheld_fields(cls, allowed)
    for f in reduced:
        if out.get(f) not in (None, False, ""):
            withheld.add(f)
        out[f] = None
    if not reduced:
        out["canonicalUrl"] = canonical
    if not publish_ratings():
        for f in ARTICLE_RATING_FIELDS:
            if out.get(f) is not None:
                withheld.add("lean")
            out[f] = None
    out["licence"] = {"class": cls,
                      "attribution": licence.attribution_for(meta.get("channels") or ())}
    if withheld:
        out["withheld"] = sorted(withheld)
    return _drop_none(out)


def _coverage_row(c: dict, meta: dict, allowed) -> "dict | None":
    cls = meta.get("licenceClass") or licence.UNKNOWN
    if cls == licence.READER_PRIVATE or meta.get("articleState") == "provisional":
        return None
    row = {"articleId": meta.get("articleId"), "publisher": c.get("publisher"),
           "publisherId": meta.get("publisherId") or identity.publisher_id_for(c.get("publisher")),
           "publishedAt": c.get("publishedAt"), "attached": bool(c.get("tierB")),
           "register": c.get("register"), "ownership": c.get("ownership"),
           "licence": {"class": cls}}
    withheld = licence.withheld_fields(cls, allowed)
    if not withheld:
        row["headline"], row["url"] = c.get("headline"), c.get("url")
    else:
        row["withheld"] = ["headline", "url"]
    if publish_ratings():
        row["lean"], row["leanBucket"] = c.get("lean"), c.get("leanBucket")
        row["factuality"] = c.get("factuality")
    return _drop_none(row)


def story(s: dict, meta_by_url: dict, allowed, *, with_coverage: bool = True) -> dict:
    """One built Story dict -> its ``/v1`` shape. The title and summary are a MEMBER's words
    (the representative's headline and dek), so they follow that member's licence class: when the
    representative is outside the plan, the title comes from the earliest member that is inside
    it, and the summary is withheld."""
    cov = list(s.get("coverage") or ())
    rep = min(cov, key=lambda c: (c.get("publishedAt") or "~", c.get("url") or "")) if cov else None
    rep_meta = meta_by_url.get(rep.get("url")) if rep else {}
    rep_ok = rep is None or not licence.withheld_fields(
        (rep_meta or {}).get("licenceClass") or licence.UNKNOWN, allowed)
    withheld = set()
    title, summary = s.get("title"), clamp(s.get("summary"))
    if not rep_ok:
        summary = None
        withheld.add("summary")
        title = None
        for c in sorted(cov, key=lambda c: (c.get("publishedAt") or "~", c.get("url") or "")):
            m = meta_by_url.get(c.get("url")) or {}
            if not licence.withheld_fields(m.get("licenceClass") or licence.UNKNOWN, allowed):
                title = c.get("headline")
                break
        if title is None:
            withheld.add("title")
    out = {
        "storyId": s["id"], "title": title, "summary": summary, "topic": s.get("topic"),
        "updatedAt": s.get("updatedAt"), "earliest": s.get("earliest"), "latest": s.get("latest"),
        "timeSpanHours": s.get("timeSpanHours"), "totalCoverage": s.get("totalCoverage"),
        "publisherCount": s.get("publisherCount"), "publishers": s.get("publishers"),
        "publisherIds": sorted({pid for pid in (identity.publisher_id_for(p)
                                                for p in (s.get("publishers") or ())) if pid}),
        "publisherDiversity": s.get("publisherDiversity"),
        "attachedCoverage": s.get("attachedCoverage"),
        "countries": s.get("countries"), "primaryCountry": s.get("primaryCountry"),
        "eventCountries": s.get("eventCountries"),
        "publisherCountries": s.get("publisherCountries"),
        "clusterTrust": s.get("clusterTrust"), "geoCoherence": s.get("geoCoherence"),
        "freshness": s.get("freshness"), "lifecycle": s.get("lifecycle"),
        "image": s.get("image"), "imageSource": s.get("imageSource"),
        "imageAttribution": s.get("imageAttribution"),
        "tags": [{"name": t.get("name"), "label": t.get("label"), "source": t.get("source"),
                  "score": t.get("score")} for t in (s.get("tags") or ()) if isinstance(t, dict)],
        "factualityPublished": s.get("factualityPublished"),
    }
    if publish_ratings():
        for f in STORY_RATING_FIELDS:
            out[f] = s.get(f)
    else:
        withheld.update(("distribution", "blindspotSide", "lowCredibilityPublishers"))
    if with_coverage:
        rows = [_coverage_row(c, meta_by_url.get(c.get("url")) or {}, allowed) for c in cov]
        out["coverage"] = [r for r in rows if r is not None]
    if withheld:
        out["withheld"] = sorted(withheld)
    return _drop_none(out)


def publisher(row: dict, hosts: list, profile: "dict | None") -> dict:
    """A ``publishers`` row (+ hosts, + the counted profile) -> its ``/v1`` shape."""
    out = {"publisherId": row["publisherId"], "name": row["name"],
           "registered": row["registered"], "country": row.get("country"),
           "region": row.get("region"), "city": row.get("city"), "scope": row.get("scope"),
           "kind": row.get("kind"), "tier": row.get("tier"), "hosts": hosts,
           "ownership": row.get("ownership"), "ownershipSource": row.get("ownershipSource"),
           "ownershipAsOf": row.get("ownershipAsOf"), "ownershipOwner": row.get("ownershipOwner"),
           "articles": {"total": row.get("articles"), "firstSeen": row.get("firstSeen"),
                        "lastSeen": row.get("lastSeen")},
           "registryVersion": row.get("registryVersion")}
    withheld = set()
    if publish_ratings():
        out["lean"], out["leanSource"] = row.get("lean"), row.get("leanSource")
        out["credibility"] = row.get("credibility")
        if row.get("factuality"):
            out["factuality"] = {"value": row.get("factuality"),
                                 "source": row.get("factualitySource"),
                                 "asOf": row.get("factualityAsOf")}
    else:
        withheld.update(("lean", "factuality", "credibility"))
    if profile:
        out["topics"] = profile.get("topics")
        out["languages"] = profile.get("languages")
        out["eventCountries"] = profile.get("eventCountries")
        out["topicGaps"] = profile.get("topicGaps")
        out["coCoverage"] = profile.get("coCoverage")
        about = profile.get("about") or {}
        if about:
            kept = {k: v for k, v in about.items() if k != "description"}
            if publish_wikipedia():
                kept["description"] = about.get("description")
            else:
                withheld.add("about.description")
            out["about"] = kept or None
    if withheld:
        out["withheld"] = sorted(withheld)
    return _drop_none(out)


__all__ = ["DESCRIPTION_MAX", "publish_ratings", "publish_wikipedia", "clamp", "versions",
           "envelope", "article", "story", "publisher"]
