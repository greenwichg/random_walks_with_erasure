"""platform_validate.py — exercise the ``/v1`` platform surface against a real catalogue and report.

The battery a customer would run before trusting the API, and the one the operator runs after
enabling it: every major capability, the exposure invariants (what must NEVER leave: bodies,
reader-private and provisional rows, a restricted provider's delivery, unlicensed ratings), the
response-quality measurements (relevance, completeness, clustering, publisher coverage) and
per-endpoint latency. Read-only against the catalogue; the only writes are the meter's own rows
and, in local mode, the temporary tenant + keys it mints and revokes.

Two ways to point it at an engine::

    # a running engine (production: run INSIDE the api container, the engine has no host port)
    RWE_PLATFORM_KEY=hv_live_… [RWE_PLATFORM_KEY_DEV=hv_live_…] \\
        python examples/platform_validate.py --base-url http://127.0.0.1:8000 --json /app/data/platform_validation.json

    # a database file, standalone (a copy of production, a local catalogue): temp keys are minted and revoked
    python examples/platform_validate.py --db sqlite:////path/ih_beta.db [--backfill] --json out.json

Keys are read from the environment (never an argument, never printed). ``RWE_PLATFORM_KEY`` is an
``internal``-plan key (every scope, every licensable class); ``RWE_PLATFORM_KEY_DEV`` a
``developer``-plan key, which is what the withholding checks need — without it those checks run
against the internal key and are reported as SKIPPED, not passed.

Exit 0 when nothing FAILED (WARNs allowed), 1 otherwise. ``--json`` writes every check, every
measurement and the trimmed samples for the report.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

RATING_KEYS = ("lean", "leanBucket", "leanSource", "distribution", "blindspotSide", "factuality",
               "credibility", "missingViewpoints", "publisherLean", "lowCredibility")
RESTRICTED_DELIVERY = ("headline", "description", "url", "canonicalUrl", "image", "imageWidth",
                       "imageHeight", "imageMimeType", "imageSource", "imageAttribution")
WORD = re.compile(r"[a-z0-9]{3,}")


# ---- transport -------------------------------------------------------------------------- #
class Resp:
    __slots__ = ("status", "json", "ms", "headers", "text")

    def __init__(self, status: int, body: Any, ms: float, headers: dict, text: str = ""):
        self.status, self.json, self.ms, self.headers, self.text = status, body, ms, headers, text


class HttpClient:
    def __init__(self, base_url: str, timeout: float = 30.0):
        self.base = base_url.rstrip("/")
        self.timeout = timeout

    def get(self, path: str, params: Optional[dict] = None, headers: Optional[dict] = None) -> Resp:
        url = self.base + path
        if params:
            url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        req = urllib.request.Request(url, headers=dict(headers or {}))
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                raw, status, hdrs = r.read(), r.status, dict(r.headers.items())
        except urllib.error.HTTPError as e:
            raw, status, hdrs = e.read(), e.code, dict(e.headers.items())
        ms = (time.perf_counter() - t0) * 1000.0
        text = raw.decode("utf-8", "replace")
        try:
            body = json.loads(text) if text else None
        except ValueError:
            body = None
        return Resp(status, body, ms, {k.lower(): v for k, v in hdrs.items()}, text)


class LocalClient:
    """The same interface over a FastAPI app, in-process (``--db``)."""

    def __init__(self, app):
        from fastapi.testclient import TestClient
        self.c = TestClient(app)

    def get(self, path: str, params: Optional[dict] = None, headers: Optional[dict] = None) -> Resp:
        t0 = time.perf_counter()
        r = self.c.get(path, params={k: v for k, v in (params or {}).items() if v is not None},
                       headers=dict(headers or {}))
        ms = (time.perf_counter() - t0) * 1000.0
        try:
            body = r.json()
        except ValueError:
            body = None
        return Resp(r.status_code, body, ms, {k.lower(): v for k, v in r.headers.items()}, r.text)


# ---- the battery ------------------------------------------------------------------------ #
def _walk(obj: Any, path: str = "$"):
    """Every (path, key, value) of a JSON document — list items included (key = index), so a row
    inside ``data[]`` is seen as a value in its own right, not only through its fields."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield path, k, v
            yield from _walk(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield path, i, v
            yield from _walk(v, f"{path}[{i}]")


def _tokens(s: str) -> set:
    return set(WORD.findall((s or "").lower()))


def _pct(x: float) -> str:
    return f"{100.0 * x:.0f}%"


def _p(values: list, q: float) -> float:
    if not values:
        return 0.0
    v = sorted(values)
    k = min(len(v) - 1, max(0, int(round(q * (len(v) - 1)))))
    return round(v[k], 1)


class Battery:
    def __init__(self, client, *, key: str, key_dev: Optional[str], repeat: int = 3,
                 hidden_refs: Optional[dict] = None, log: Callable[[str], None] = print):
        self.client = client
        self.h_int = {"Authorization": f"Bearer {key}"}
        self.h_dev = {"X-API-Key": key_dev} if key_dev else None
        self.repeat = max(1, repeat)
        # canonical urls / article ids the platform must never show (local mode knows them)
        self.hidden = hidden_refs or {"urls": set(), "ids": set()}
        self.log = log
        self.checks: list = []
        self.metrics: dict = {}
        self.samples: dict = {}
        self.latency: dict = {}
        self.payloads: list = []        # (label, key_kind, body) — for the exposure sweep
        self.sent: dict = {"int": 0, "dev": 0}
        self.ratings_published: Optional[bool] = None

    # -- bookkeeping ----------------------------------------------------------------------- #
    def check(self, name: str, ok: Optional[bool], detail: str = "", *, warn: bool = False) -> bool:
        level = "SKIP" if ok is None else ("PASS" if ok else ("WARN" if warn else "FAIL"))
        self.checks.append({"name": name, "level": level, "detail": detail})
        self.log(f"  [{level:4}] {name}" + (f" — {detail}" if detail else ""))
        return bool(ok)

    def get(self, label: str, path: str, params: Optional[dict] = None, *, dev: bool = False,
            headers: Optional[dict] = None, record: bool = True) -> Resp:
        h = headers if headers is not None else (self.h_dev if dev and self.h_dev else self.h_int)
        r = self.client.get(path, params, h)
        if headers is None:
            self.sent["dev" if (dev and self.h_dev) else "int"] += 1
        if record:
            self.latency.setdefault(label, []).append(round(r.ms, 1))
            if isinstance(r.json, dict):
                self.payloads.append((label, "dev" if (dev and self.h_dev) else "int", r.json))
                meta = r.json.get("meta") or {}
                if "ratingsPublished" in meta and self.ratings_published is None:
                    self.ratings_published = bool(meta["ratingsPublished"])
        return r

    def sample(self, name: str, value: Any, limit: int = 1400) -> None:
        text = json.dumps(value, ensure_ascii=False, default=str)
        self.samples[name] = json.loads(text) if len(text) <= limit else text[:limit] + " …"

    @staticmethod
    def data(r: Resp):
        return (r.json or {}).get("data") if isinstance(r.json, dict) else None

    @staticmethod
    def err(r: Resp) -> str:
        try:
            return r.json["error"]["code"]
        except Exception:                    # noqa: BLE001
            return f"http {r.status}"

    # -- sections -------------------------------------------------------------------------- #
    def run(self) -> dict:
        self.log("== /v1 platform validation ==")
        self.s_health()
        self.s_me()
        sid, story = self.s_stories()
        self.sid = sid
        first_article = None
        if sid:
            first_article = self.s_story_detail(sid, story)
            self.s_similar(sid)
            self.s_intelligence(sid)
            self.s_comparison(sid, first_article)
            self.s_history(sid)
        top_tag = self.s_tags(sid)
        self.s_entities(first_article)
        self.s_search(story, top_tag)
        self.s_publishers(first_article, story)
        self.s_filters(story)
        self.s_outlets(story)
        self.s_auth_refusals(sid)
        self.s_exposure()
        self.s_latency(sid, first_article)
        self.s_metering()
        return self.report()

    def s_health(self):
        self.log("-- health --")
        r = self.get("health", "/v1/health", headers={})
        v = ((r.json or {}).get("meta") or {}).get("versions") or {}
        self.check("health answers without a key", r.status == 200 and (self.data(r) or {}).get("status") == "ok",
                   f"http {r.status}")
        self.check("versions stamped", bool(v.get("scorer") and v.get("build") and v.get("registry")),
                   json.dumps(v))
        self.metrics["versions"] = v
        d = self.data(r) or {}
        # Enrichment is counted off the request path: the first health call after a start hands
        # back null and starts the count. Give it a moment rather than reporting a cold engine.
        for _ in range(6):
            if d.get("enrichment"):
                break
            time.sleep(2)
            d = self.data(self.get("health", "/v1/health", headers={}, record=False)) or {}
        enr = (d.get("enrichment") or {}).get("recent") or {}
        self.check("enrichment coverage published", bool(enr) and "entityCoverage" in enr,
                   f"last {enr.get('days')}d: entities {enr.get('entityCoverage')} spans {enr.get('spanCoverage')} geo {enr.get('geoCoverage')} over {enr.get('articles')} articles")
        # The story recorder fails soft; health carries its last outcome so a build that served
        # but never recorded is diagnosed here, with the error, not inferred from an empty history.
        h = d.get("history") or {}
        self.check("story recorder healthy (no failed write since start)", "history" in d and not h.get("lastError"),
                   f"stories={h.get('stories')} snapshots={h.get('story_snapshots')} membership={h.get('story_membership')} "
                   f"errors={h.get('errors')} lastError={h.get('lastError')}")
        self.check("story history holds rows", int(h.get("stories") or 0) > 0,
                   f"stories={h.get('stories')} lastOk={h.get('lastOk')}", warn=True)
        self.metrics["historyRecorder"] = h
        self.check("provider entities reach ≥20% of recent articles", (enr.get("entityCoverage") or 0) >= 0.2,
                   f"{enr.get('entityCoverage')}", warn=True)
        self.check("event geography reaches ≥20% of recent articles", (enr.get("geoCoverage") or 0) >= 0.2,
                   f"{enr.get('geoCoverage')}", warn=True)
        self.check("a story build is recorded", bool(d.get("lastBuildAt")), f"lastBuildAt={d.get('lastBuildAt')}", warn=True)
        self.check("search index ready and in step", bool((d.get("searchIndex") or {}).get("ready"))
                   and (d.get("searchIndex") or {}).get("indexed") == (d.get("searchIndex") or {}).get("catalogue"),
                   json.dumps(d.get("searchIndex")))
        self.metrics["enrichment"] = d.get("enrichment")
        self.metrics["lastBuildAt"] = d.get("lastBuildAt")
        # documentation, not data: kept out of the exposure sweep (its `description` keys are prose)
        d = self.get("docs", "/v1/openapi.json", headers={}, record=False)
        self.check("openapi schema served", d.status == 200 and isinstance(d.json, dict)
                   and len((d.json or {}).get("paths") or {}) >= 20,
                   f"{len(((d.json or {}).get('paths') or {}))} paths")

    def s_me(self):
        self.log("-- me --")
        r = self.get("me", "/v1/me")
        d = self.data(r) or {}
        # the meter's reading BEFORE this run's traffic (the /v1/me call above is its first row)
        self.meter_start = int(((d.get("usage") or {}).get("requests") or 0)) - 1
        self.check("internal key resolves", r.status == 200 and d.get("plan") == "internal",
                   f"plan={d.get('plan')} scopes={len(d.get('scopes') or [])} classes={d.get('licenceClasses')}")
        self.check("published switches reported", isinstance(d.get("published"), dict),
                   json.dumps(d.get("published")))
        self.metrics["published"] = d.get("published")
        self.sample("me", d)
        if self.h_dev:
            r2 = self.get("me", "/v1/me", dev=True)
            d2 = self.data(r2) or {}
            self.check("developer key resolves", r2.status == 200 and d2.get("plan") == "developer",
                       f"plan={d2.get('plan')} classes={d2.get('licenceClasses')}")
        else:
            self.check("developer key resolves", None, "RWE_PLATFORM_KEY_DEV not set — withholding checks run on the internal key")

    def s_stories(self):
        self.log("-- stories (the served build) --")
        r = self.get("stories", "/v1/stories", {"limit": 50})
        items = self.data(r) or []
        meta = (r.json or {}).get("meta") or {}
        page = meta.get("page") or {}
        self.check("stories list answers", r.status == 200 and isinstance(items, list),
                   f"{len(items)} on page, total={page.get('total')}")
        self.check("build time stamped (meta.asOf) and served fresh", bool(meta.get("asOf")) and meta.get("stale") is False,
                   f"asOf={meta.get('asOf')} stale={meta.get('stale')}", warn=True)
        self.metrics["view"] = {"asOf": meta.get("asOf"), "stale": meta.get("stale")}
        if not items:
            self.check("catalogue holds clustered stories", False, "no stories — an empty or single-source catalogue")
            return None, None
        self.check("default listing serves only trusted clusters", all((s.get("clusterTrust") or "ok") == "ok" for s in items),
                   f"minTrust={meta.get('minTrust')} trusts={sorted({str(s.get('clusterTrust')) for s in items})}")
        r_any = self.get("stories", "/v1/stories", {"limit": 50, "min_trust": "any"})
        any_total = (((r_any.json or {}).get("meta") or {}).get("page") or {}).get("total") or 0
        self.check("min_trust=any widens the listing or equals it", any_total >= (page.get("total") or 0),
                   f"any={any_total} ok={page.get('total')}")
        self.metrics["trustFilter"] = {"ok": page.get("total"), "any": any_total}
        n = len(items)
        pubs = [int(s.get("publisherCount") or 0) for s in items]
        with_summary = sum(1 for s in items if s.get("summary"))
        with_tags = sum(1 for s in items if s.get("tags"))
        with_countries = sum(1 for s in items if s.get("countries"))
        with_topic = sum(1 for s in items if s.get("topic"))
        withheld_title = sum(1 for s in items if "title" in (s.get("withheld") or []))
        trust = {}
        for s in items:
            trust[str(s.get("clusterTrust"))] = trust.get(str(s.get("clusterTrust")), 0) + 1
        spans = [float(s.get("timeSpanHours") or 0) for s in items]
        sizes = [int(s.get("totalCoverage") or 0) for s in items]
        covered = sum(sizes)
        # Clustering shape: a window whose largest story swallows most of the coverage, or whose
        # stories pack many articles per publisher, is over-merged (a syndication wire, a template
        # genre, or a too-loose gate) — the failure a customer sees first.
        largest_share = round(max(sizes) / covered, 3) if covered else 0.0
        per_pub = [s / p for s, p in zip(sizes, pubs) if p]
        self.metrics["stories"] = {
            "onPage": n, "total": page.get("total"), "articlesCovered": covered,
            "publishersMean": round(statistics.mean(pubs), 2) if pubs else 0,
            "publishersMedian": statistics.median(pubs) if pubs else 0,
            "share3PlusPublishers": round(sum(1 for p in pubs if p >= 3) / n, 3),
            "sizeMedian": statistics.median(sizes) if sizes else 0, "sizeMax": max(sizes) if sizes else 0,
            "largestStoryShare": largest_share,
            "articlesPerPublisherMax": round(max(per_pub), 2) if per_pub else 0,
            "shareWithSummary": round(with_summary / n, 3), "shareWithTags": round(with_tags / n, 3),
            "shareWithCountries": round(with_countries / n, 3), "shareWithTopic": round(with_topic / n, 3),
            "titleWithheld": withheld_title, "clusterTrust": trust,
            "timeSpanHoursMedian": statistics.median(spans) if spans else 0,
        }
        self.check("no story swallows the window", largest_share < 0.4 or n < 3,
                   f"largest story holds {_pct(largest_share)} of the covered articles", warn=True)
        self.check("no story stacks many articles per publisher", (max(per_pub) if per_pub else 0) <= 3,
                   f"max {round(max(per_pub), 1) if per_pub else 0} articles per publisher in one story", warn=True)
        self.check("every story has ≥2 publishers", all(p >= 2 for p in pubs), f"min={min(pubs)}")
        self.check("story completeness: title, publishers, ids", all(
            s.get("title") and s.get("storyId", "").startswith("st_") and s.get("publisherIds")
            for s in items), f"{n} checked")
        self.check("summaries present", with_summary / n >= 0.8, _pct(with_summary / n), warn=True)
        self.check("tags present", with_tags / n >= 0.5, _pct(with_tags / n), warn=True)
        self.check("event countries present", with_countries / n >= 0.3, _pct(with_countries / n), warn=True)
        self.check("no story lost its title to withholding", withheld_title == 0, f"{withheld_title} withheld", warn=True)
        # Every served id, once, over the WHOLE listing (the largest page the API allows, at most
        # 100 pages). A duplicate id is a dead link for one of the two stories — and it failed the
        # history record on every production build until 2026-09-05. Walking the pages also
        # proves the cursor reaches the total the first page announced.
        ids: list = []
        cursor = None
        pages = 0
        while pages < 100:
            params = {"limit": 100}
            if cursor:
                params["cursor"] = cursor
            rw = self.get("stories.walk", "/v1/stories", params)
            rows = self.data(rw) or []
            ids.extend(s.get("storyId") for s in rows)
            pages += 1
            cursor = (((rw.json or {}).get("meta") or {}).get("page") or {}).get("nextCursor")
            if not rows or not cursor:
                break
        dup = len(ids) - len(set(ids))
        self.check("every served story id is unique", dup == 0,
                   f"{len(ids)} ids over {pages} page(s), {dup} duplicated")
        self.check("pagination walks the whole listing", len(ids) == (page.get("total") or 0),
                   f"walked {len(ids)} vs total {page.get('total')}", warn=True)
        self.metrics["stories"].update(idsWalked=len(ids), duplicateIds=dup, pagesWalked=pages)
        top = items[0]
        self.sample("story.listing[0]", top)
        return top["storyId"], top

    def s_story_detail(self, sid: str, story: dict):
        self.log("-- one story + coverage --")
        r = self.get("story", f"/v1/stories/{sid}")
        d = self.data(r) or {}
        cov = d.get("coverage") or []
        self.check("story detail answers", r.status == 200 and d.get("storyId") == sid, f"{len(cov)} coverage rows")
        omitted = int(d.get("coverageOmitted") or 0)
        self.check("coverage + coverageOmitted equals totalCoverage", len(cov) + omitted == d.get("totalCoverage"),
                   f"{len(cov)} listed + {omitted} omitted vs {d.get('totalCoverage')} (cap {d.get('coveragePerPublisher', 'none hit')})")
        per_pub: dict = {}
        for c in cov:
            per_pub[c.get("publisher")] = per_pub.get(c.get("publisher"), 0) + 1
        cap = d.get("coveragePerPublisher")
        self.check("no publisher exceeds the coverage cap", cap is None or max(per_pub.values()) <= cap,
                   f"max per publisher {max(per_pub.values()) if per_pub else 0}")
        etag = r.headers.get("etag")
        r304 = self.get("story", f"/v1/stories/{sid}", headers=dict(self.h_int, **{"If-None-Match": etag or ""}), record=False)
        self.sent["int"] += 1
        self.check("story carries an ETag and honours If-None-Match", bool(etag) and r304.status == 304,
                   f"ETag={etag} revalidation -> {r304.status}")
        self.check("publishers equal coverage publishers",
                   sorted(set(c.get("publisher") for c in cov if c.get("publisher"))) == sorted(d.get("publishers") or []),
                   f"{len(d.get('publishers') or [])} publishers")
        self.check("every coverage row carries identity + licence", all(
            c.get("articleId", "").startswith("ar_") and c.get("publisherId", "").startswith("pub_")
            and (c.get("licence") or {}).get("class") for c in cov), "articleId, publisherId, licence.class")
        self.check("delivery present exactly when the class allows", all(
            ("url" in c) == (not c.get("withheld")) for c in cov), "url ⇔ not withheld")
        self.check("freshness + lifecycle attached", bool(d.get("freshness")) and bool(d.get("lifecycle")),
                   f"lifecycle={(d.get('lifecycle') or {}).get('stage') if isinstance(d.get('lifecycle'), dict) else d.get('lifecycle')}")
        classes = {}
        for c in cov:
            k = (c.get("licence") or {}).get("class")
            classes[k] = classes.get(k, 0) + 1
        self.metrics["coverageClasses"] = classes
        self.sample("story.detail", {k: v for k, v in d.items() if k != "coverage"} | {"coverage": cov[:4]})
        first = next((c for c in cov if c.get("url")), None) or (cov[0] if cov else None)
        return first

    def s_similar(self, sid: str):
        self.log("-- similar stories --")
        r = self.get("similar", f"/v1/stories/{sid}/similar", {"limit": 5})
        items = self.data(r) or []
        self.check("similar answers", r.status == 200 and isinstance(items, list), f"{len(items)} related")
        self.check("similar never returns the story itself", all(s.get("storyId") != sid for s in items))
        self.metrics["similarCount"] = len(items)
        if items:
            self.sample("similar[0]", items[0])

    def s_intelligence(self, sid: str):
        self.log("-- story intelligence --")
        r = self.get("intelligence", f"/v1/stories/{sid}/intelligence")
        d = self.data(r) or {}
        self.check("intelligence answers", r.status == 200 and bool(d), ", ".join(sorted(d)[:8]))
        self.check("freshness / momentum / lifecycle present",
                   all(k in d for k in ("freshness", "lifecycle")) and any(k in d for k in ("momentum", "velocity", "alerts")),
                   ", ".join(sorted(d)))
        self.check("no reader-relative field", "newSinceLastVisit" not in d)
        self.sample("intelligence", d)

    def s_comparison(self, sid: str, member: Optional[dict]):
        self.log("-- coverage comparison --")
        if not member or not member.get("articleId"):
            self.check("coverage comparison", None, "no servable member to compare")
            return
        r = self.get("comparison", f"/v1/stories/{sid}/coverage-comparison", {"article_id": member["articleId"]})
        d = self.data(r) or {}
        self.check("comparison answers", r.status == 200 and "available" in d,
                   f"available={d.get('available')} reason={d.get('reason')}")
        self.metrics["comparison"] = {"available": d.get("available"), "reason": d.get("reason"),
                                      "outlets": d.get("outlets"), "findings":
                                      len(d.get("reportedElsewhere") or []) + len(d.get("uniqueHere") or [])}
        if d.get("available"):
            ev = [e for f in (d.get("reportedElsewhere") or []) + (d.get("uniqueHere") or []) for e in f.get("evidence") or []]
            self.check("evidence rows carry identity + licence", all(
                e.get("publisherId") and (e.get("licence") or {}).get("class") for e in ev), f"{len(ev)} evidence rows")
            self.check("evidence delivery follows the class", all(("url" in e) == (not e.get("withheld")) for e in ev))
            if self.ratings_published is False:
                self.check("viewpoint findings withheld without ratings",
                           "missingViewpoints" not in d and "missingViewpoints" in (d.get("withheld") or []))
        self.sample("comparison", d)

    def s_history(self, sid: str):
        self.log("-- story history --")
        r = self.get("history", f"/v1/stories/{sid}/history")
        d = self.data(r) or {}
        self.check("history answers", r.status == 200 and d.get("story"),
                   f"snapshots={len(d.get('snapshots') or [])} membership={len(d.get('membership') or [])}")
        self.check("at least one snapshot recorded", len(d.get("snapshots") or []) >= 1, warn=True)
        if self.ratings_published is False and d.get("snapshots"):
            self.check("snapshot distribution withheld without ratings",
                       all(s.get("distribution") is None for s in d["snapshots"]))
        self.metrics["history"] = {"snapshots": len(d.get("snapshots") or []), "membership": len(d.get("membership") or [])}

    def s_tags(self, sid: Optional[str]) -> Optional[dict]:
        self.log("-- tags --")
        r = self.get("tags", "/v1/tags", {"limit": 20})
        items = self.data(r) or []
        self.check("tags vocabulary answers", r.status == 200 and isinstance(items, list),
                   f"{len(items)} on page, total={(((r.json or {}).get('meta') or {}).get('page') or {}).get('total')}")
        self.metrics["tags"] = {"total": (((r.json or {}).get("meta") or {}).get("page") or {}).get("total"),
                                "top": items[:5]}
        if not items:
            self.check("tag retrieval", None, "no tags in the projection")
            return None
        # the first non-topic tag when there is one: an entity or subject, not a shelf
        top = next((t for t in items if t.get("stories", 0) >= 2), items[0])
        r2 = self.get("tag", f"/v1/tags/{urllib.parse.quote(top['tag'])}", {"limit": 20})
        stories = self.data(r2) or []
        self.check("tag retrieval answers", r2.status == 200 and len(stories) >= 1,
                   f"'{top['tag']}' -> {len(stories)} stories (vocabulary says {top['stories']})")
        carried = sum(1 for s in stories if any(t.get("name") == top["tag"] for t in (s.get("tags") or [])))
        tok = _tokens(top["tag"])
        mentions = sum(1 for s in stories if tok & _tokens((s.get("title") or "") + " " + (s.get("summary") or "")))
        self.metrics["tagRelevance"] = {"tag": top["tag"], "returned": len(stories), "carryTag": carried,
                                        "mentionInText": mentions}
        self.check("tag stories carry the tag or mention it", not stories or (carried + mentions) / len(stories) >= 0.8,
                   f"carry={carried} mention={mentions} of {len(stories)}", warn=True)
        r3 = self.get("stories", "/v1/stories", {"tag": top["tag"], "limit": 20})
        self.metrics["tagFilterCount"] = len(self.data(r3) or [])
        self.sample("tags.top", items[:5])
        return top

    def s_entities(self, member: Optional[dict]):
        self.log("-- entities --")
        if not member or not member.get("articleId"):
            self.check("entities", None, "no article to read entities from")
            return
        r = self.get("article.entities", f"/v1/articles/{member['articleId']}/entities")
        d = self.data(r) or {}
        ents = d.get("entities") or {}
        names = [n for k in ("person", "org") for n in (ents.get(k) or [])]
        self.check("per-article entities answer", r.status == 200 and "entities" in d,
                   f"person={len(ents.get('person') or [])} org={len(ents.get('org') or [])}")
        self.check("GDELT attribution carried", d.get("attribution") == ["GDELT Project (gdeltproject.org)"])
        if not names:
            self.check("entity lookup", None, "this article carries no provider entities (enrichment off, or not yet backfilled)")
            self.metrics["entities"] = {"onFirstArticle": 0}
            return
        name = names[0]
        r2 = self.get("entities", "/v1/entities", {"name": name, "limit": 20})
        arts = self.data(r2) or []
        total = (((r2.json or {}).get("meta") or {}).get("page") or {}).get("total")
        self.check("entity lookup answers", r2.status == 200 and len(arts) >= 1, f"'{name}' -> {len(arts)} articles (total {total})")
        # relevance: the first three results really carry the name
        hits = 0
        for a in arts[:3]:
            e = self.data(self.get("article.entities", f"/v1/articles/{a['articleId']}/entities")) or {}
            if name in [n for k in ("person", "org") for n in ((e.get("entities") or {}).get(k) or [])]:
                hits += 1
        self.check("entity results carry the entity", hits == min(3, len(arts)), f"{hits}/{min(3, len(arts))} verified")
        self.metrics["entities"] = {"onFirstArticle": len(names), "lookup": name, "articles": total}
        self.sample("entities", {"name": name, "articles": arts[:3]})

    def s_search(self, story: Optional[dict], top_tag: Optional[dict]):
        self.log("-- article search --")
        # "Find articles about X": X is drawn from the top story's own title (its two longest
        # words), which is what a customer types. A topic-shelf tag ("Arts") is NOT used: the
        # catalogue search also matches the category, and text precision would misjudge it.
        q = None
        if story:
            words = [w for w in _tokens(story.get("title") or "") if len(w) >= 5]
            q = " ".join(sorted(words, key=len, reverse=True)[:2])
        if not q and top_tag:
            q = top_tag.get("label") or top_tag.get("tag")
        if not q:
            self.check("article search", None, "nothing to search for")
            return
        r = self.get("articles.search", "/v1/articles", {"q": q, "limit": 20})
        arts = self.data(r) or []
        meta = (r.json or {}).get("meta") or {}
        total = (meta.get("page") or {}).get("total")
        self.check("search answers", r.status == 200 and isinstance(arts, list), f"q='{q}' -> {len(arts)} (total {total})")
        # Recall on the easiest possible query: two words from a headline the catalogue holds,
        # in the order they were NOT written. Term search must find it; a phrase match cannot.
        words = q.split()
        reversed_q = " ".join(reversed(words)) if len(words) > 1 else q
        r2 = self.get("articles.search", "/v1/articles", {"q": reversed_q, "limit": 20})
        total2 = (((r2.json or {}).get("meta") or {}).get("page") or {}).get("total")
        self.check("search finds the top story's own words, in any order", (total or 0) > 0 and (total2 or 0) > 0,
                   f"'{q}' -> {total}; '{reversed_q}' -> {total2}")
        self.check("query terms echoed and relevance is the default sort",
                   meta.get("sort") == "relevance" and (meta.get("query") or {}).get("terms"),
                   f"sort={meta.get('sort')} terms={(meta.get('query') or {}).get('terms')}")
        qt = _tokens(q)
        rel = [a for a in arts if qt & _tokens((a.get("headline") or "") + " " + (a.get("description") or ""))]
        visible = [a for a in arts if "headline" in a]
        first = visible[0] if visible else None
        top_hit = bool(first) and bool(qt & _tokens((first.get("headline") or "") + " " + (first.get("description") or "")))
        self.metrics["searchRelevance"] = {"q": q, "returned": len(arts), "total": total, "reversedTotal": total2,
                                           "withVisibleText": len(visible), "matchingAnyToken": len(rel),
                                           "precisionOverVisible": round(len(rel) / len(visible), 3) if visible else None,
                                           "topResultMatches": top_hit}
        if visible:
            self.check("search precision (query token in visible text)", len(rel) / len(visible) >= 0.7,
                       f"{len(rel)}/{len(visible)}", warn=True)
            self.check("top result carries the query", top_hit, (first.get("headline") or "")[:80])
        r3 = self.get("articles.search", "/v1/articles", {"q": q, "sort": "newest", "limit": 20})
        times = [a.get("publishedAt") or "" for a in (self.data(r3) or [])]
        self.check("sort=newest orders newest first", times == sorted(times, reverse=True), f"{len(times)} rows")
        r4 = self.get("stories.search", "/v1/stories", {"q": q, "limit": 10})
        found = self.data(r4) or []
        s_total = (((r4.json or {}).get("meta") or {}).get("page") or {}).get("total")
        self.check("story search finds the story the words came from", r4.status == 200 and (s_total or 0) >= 1
                   and any(s.get("storyId") == getattr(self, "sid", None) for s in found),
                   f"q='{q}' -> {s_total} stories")
        self.metrics["storySearch"] = {"q": q, "total": s_total, "topIsSource": bool(found) and found[0].get("storyId") == getattr(self, "sid", None)}
        self.check("every article carries ids + licence", all(
            a.get("articleId", "").startswith("ar_") and a.get("publisherId", "").startswith("pub_")
            and (a.get("licence") or {}).get("class") for a in arts))
        r2 = self.get("articles", "/v1/articles", {"limit": 50})
        arts2 = self.data(r2) or []
        classes = {}
        for a in arts2:
            k = (a.get("licence") or {}).get("class")
            classes[k] = classes.get(k, 0) + 1
        self.metrics["articleClasses"] = classes
        self.sample("articles.search", arts[:3])

    def s_publishers(self, member: Optional[dict], story: Optional[dict]):
        self.log("-- publishers --")
        r = self.get("publishers", "/v1/publishers", {"limit": 10})
        pubs = self.data(r) or []
        total = (((r.json or {}).get("meta") or {}).get("page") or {}).get("total")
        self.check("publishers list answers", r.status == 200 and len(pubs) >= 1, f"{len(pubs)} on page, total={total}")
        self.metrics["publishers"] = {"total": total, "top": [(p.get("name"), (p.get("articles") or {}).get("total")) for p in pubs[:5]]}
        if not pubs:
            return
        top = pubs[0]
        pid = top["publisherId"]
        r2 = self.get("publishers.q", "/v1/publishers", {"q": top["name"][:12]})
        self.check("publisher name search finds it", any(p["publisherId"] == pid for p in (self.data(r2) or [])),
                   f"q='{top['name'][:12]}'")
        host = None
        if member and member.get("url"):
            host = urllib.parse.urlsplit(member["url"]).hostname
        if host:
            r3 = self.get("publishers.by-host", "/v1/publishers/by-host", {"host": host})
            d3 = self.data(r3) or {}
            self.check("host resolves to the article's publisher", r3.status == 200
                       and d3.get("publisherId") == member.get("publisherId"), f"{host} -> {d3.get('name')}")
        r4 = self.get("publisher", f"/v1/publishers/{pid}")
        d4 = self.data(r4) or {}
        self.check("publisher profile answers", r4.status == 200 and d4.get("name") == top["name"],
                   f"topics={len(d4.get('topics') or [])} hosts={len(d4.get('hosts') or [])} articles={(d4.get('articles') or {}).get('total')}")
        r5 = self.get("publisher.articles", f"/v1/publishers/{pid}/articles", {"limit": 20})
        a5 = self.data(r5) or []
        self.check("publisher articles all belong to it", r5.status == 200 and a5
                   and all(a.get("publisherId") == pid for a in a5), f"{len(a5)} rows")
        r6 = self.get("publisher.stories", f"/v1/publishers/{pid}/stories", {"limit": 20})
        s6 = self.data(r6) or []
        self.check("publisher stories all carry it", r6.status == 200
                   and all(pid in (s.get("publisherIds") or []) for s in s6), f"{len(s6)} stories")
        r7 = self.get("stories", "/v1/stories", {"publisher_id": pid, "limit": 20})
        self.check("stories?publisher_id agrees with /publishers/{id}/stories",
                   [s["storyId"] for s in (self.data(r7) or [])] == [s["storyId"] for s in s6])
        if story:
            r8 = self.get("stories", "/v1/stories", {"topic": story.get("topic"), "limit": 20}) if story.get("topic") else None
            if r8 is not None:
                s8 = self.data(r8) or []
                self.check("topic filter is exact", all(s.get("topic") == story.get("topic") for s in s8),
                           f"topic='{story.get('topic')}' -> {len(s8)}")
        self.sample("publisher", {k: v for k, v in d4.items() if k not in ("coCoverage",)})

    def s_filters(self, story: Optional[dict]):
        self.log("-- country filters --")
        r = self.get("countries", "/v1/countries")
        rows = self.data(r) or []
        self.check("countries answer", r.status == 200 and isinstance(rows, list), f"{len(rows)} countries")
        self.metrics["countries"] = {"count": len(rows), "top": rows[:5]}
        if not rows:
            self.check("country filter", None, "no event geography in the catalogue (enrichment off, or not backfilled)")
            return
        cc = rows[0]["country"]
        r2 = self.get("stories", "/v1/stories", {"country": cc, "limit": 20})
        s2 = self.data(r2) or []
        self.check("stories?country= is exact", all(cc in (s.get("countries") or []) for s in s2), f"{cc} -> {len(s2)} stories")
        r3 = self.get("articles", "/v1/articles", {"country": cc, "limit": 20})
        a3 = self.data(r3) or []
        self.check("articles?country= answers", r3.status == 200, f"{cc} -> {len(a3)} articles")

    def s_outlets(self, story: Optional[dict]):
        self.log("-- outlet search --")
        r = self.get("outlets", "/v1/outlets/search", {"q": "local news websites in Kenya", "count": 5})
        if r.status == 503:
            self.check("outlet index available", False, self.err(r), warn=True)
            return
        rows = self.data(r) or []
        self.check("outlet search answers", r.status == 200 and isinstance(rows, list),
                   f"{len(rows)} hits, plan={((r.json or {}).get('meta') or {}).get('query')}")
        name = (story or {}).get("publishers", ["BBC"])[0] if story else "BBC"
        r2 = self.get("outlets", "/v1/outlets/search", {"q": name, "count": 5})
        rows2 = self.data(r2) or []
        self.check("outlet search by name", r2.status == 200, f"'{name}' -> {len(rows2)} hits, tracked={sum(1 for x in rows2 if x.get('tracked'))}", warn=True)
        self.metrics["outlets"] = {"kenya": len(rows), "byName": len(rows2)}
        self.sample("outlets", rows[:3])

    def s_auth_refusals(self, sid: Optional[str]):
        self.log("-- refusals --")
        r = self.get("refusal", "/v1/articles", headers={}, record=False)
        self.check("no key -> 401 unauthenticated", r.status == 401 and self.err(r) == "unauthenticated")
        key = self.h_int["Authorization"].split(" ", 1)[1]
        r = self.get("refusal", "/v1/articles", {"api_key": key}, headers={}, record=False)
        self.check("query-string key is refused", r.status == 401)
        r = self.get("refusal", "/v1/articles", headers={"Authorization": "Bearer hv_live_not_a_key"}, record=False)
        self.check("unknown key -> 401", r.status == 401 and self.err(r) == "unauthenticated")
        r = self.get("refusal", "/v1/articles", {"cursor": "abc"}, record=False)
        self.check("bad cursor -> 400 invalid_cursor", r.status == 400 and self.err(r) == "invalid_cursor")
        r = self.get("refusal", "/v1/stories/st_0000000000000000", record=False)
        self.check("unknown story -> 404 not_found", r.status == 404 and self.err(r) == "not_found")
        if self.ratings_published is False:
            r = self.get("refusal", "/v1/stories", {"lean": "left"}, record=False)
            self.check("lean filter refused while ratings unpublished", r.status == 403 and self.err(r) == "ratings_not_published")
        if self.h_dev and sid:
            r = self.get("refusal", f"/v1/stories/{sid}/history", dev=True, record=False)
            self.check("developer key lacks stories:history -> 403", r.status == 403 and self.err(r) == "forbidden_scope")

    def s_exposure(self):
        self.log("-- exposure invariants (every payload collected so far) --")
        if self.h_dev:
            # The developer key must SEE restricted rows (identity, publisher, time) and never their
            # delivery — so fetch the rows most likely to carry one with that key before the sweep.
            self.get("articles", "/v1/articles", {"limit": 50}, dev=True)
            self.get("stories", "/v1/stories", {"limit": 20}, dev=True)
            sid = getattr(self, "sid", None)
            if sid:
                d = self.data(self.get("story", f"/v1/stories/{sid}", dev=True)) or {}
                member = next((c for c in d.get("coverage") or () if c.get("articleId")), None)
                if member:
                    self.get("comparison", f"/v1/stories/{sid}/coverage-comparison",
                             {"article_id": member["articleId"]}, dev=True)
        # what a developer key received, plus everything the internal key received for the class-free rules
        bodies = [(l, k, b) for l, k, b in self.payloads]
        leaks = {"body": 0, "readerPrivate": 0, "provisional": 0, "hiddenRef": 0, "ratings": 0,
                 "wikipedia": 0, "restrictedDelivery": 0, "longSnippet": 0}
        ratings_off = self.ratings_published is False
        wiki_off = not ((self.metrics.get("published") or {}).get("wikipedia"))
        dev_seen = 0
        for label, kind, body in bodies:
            for path, k, v in _walk(body):
                if k == "body":
                    leaks["body"] += 1
                if k == "class" and v == "reader_private":
                    leaks["readerPrivate"] += 1
                if k == "articleState" and v == "provisional":
                    leaks["provisional"] += 1
                if k in ("url", "canonicalUrl") and isinstance(v, str) and v in self.hidden["urls"]:
                    leaks["hiddenRef"] += 1
                if k == "articleId" and isinstance(v, str) and v in self.hidden["ids"]:
                    leaks["hiddenRef"] += 1
                if ratings_off and k in RATING_KEYS and v not in (None, [], {}):
                    leaks["ratings"] += 1
                if wiki_off and k == "about" and isinstance(v, dict) and v.get("description"):
                    leaks["wikipedia"] += 1
                if k in ("description", "summary") and isinstance(v, str) and len(v) > 320:
                    leaks["longSnippet"] += 1
                if kind == "dev" and isinstance(v, dict) and (v.get("licence") or {}).get("class") not in (None, "metadata_public"):
                    dev_seen += 1
                    if any(f in v for f in RESTRICTED_DELIVERY):
                        leaks["restrictedDelivery"] += 1
        self.metrics["exposure"] = dict(leaks, payloads=len(bodies), restrictedRowsSeenByDeveloperKey=dev_seen)
        self.check("no article body anywhere", leaks["body"] == 0, f"{len(bodies)} payloads swept")
        self.check("no reader-private row", leaks["readerPrivate"] == 0)
        self.check("no provisional row", leaks["provisional"] == 0)
        self.check("no known-hidden reference", leaks["hiddenRef"] == 0,
                   f"{len(self.hidden['urls'])} hidden urls / {len(self.hidden['ids'])} ids known" if self.hidden["urls"] or self.hidden["ids"] else "no hidden set supplied (local mode supplies one)")
        self.check("no third-party rating while unpublished", None if not ratings_off else leaks["ratings"] == 0,
                   "ratings are published on this deployment" if not ratings_off else f"{leaks['ratings']} rating values")
        self.check("no Wikipedia text while unpublished", None if not wiki_off else leaks["wikipedia"] == 0)
        self.check("snippets clamped", leaks["longSnippet"] == 0, f"{leaks['longSnippet']} over 320 chars")
        if self.h_dev:
            self.check("developer key never receives a restricted row's delivery", leaks["restrictedDelivery"] == 0,
                       f"{dev_seen} restricted rows seen by the developer key")
        else:
            self.check("developer key never receives a restricted row's delivery", None, "no developer key")

    def s_latency(self, sid: Optional[str], member: Optional[dict]):
        self.log(f"-- latency ({self.repeat} repeats per endpoint) --")
        targets = [("stories", "/v1/stories", {"limit": 30}), ("articles", "/v1/articles", {"limit": 30}),
                   ("articles.search", "/v1/articles", {"q": "government", "limit": 30}),
                   ("publishers", "/v1/publishers", {"limit": 30}), ("tags", "/v1/tags", {"limit": 50}),
                   ("countries", "/v1/countries", None), ("me", "/v1/me", None)]
        if sid:
            targets += [("story", f"/v1/stories/{sid}", None), ("similar", f"/v1/stories/{sid}/similar", None),
                        ("intelligence", f"/v1/stories/{sid}/intelligence", None),
                        ("history", f"/v1/stories/{sid}/history", None)]
            if member and member.get("articleId"):
                targets.append(("comparison", f"/v1/stories/{sid}/coverage-comparison", {"article_id": member["articleId"]}))
                targets.append(("article", f"/v1/articles/{member['articleId']}", None))
        for label, path, params in targets:
            for _ in range(self.repeat):
                self.get(label, path, params)
        table = {}
        for label, ms in self.latency.items():
            table[label] = {"n": len(ms), "p50": _p(ms, 0.5), "p95": _p(ms, 0.95), "max": max(ms)}
        self.metrics["latencyMs"] = table
        slow = {k: v for k, v in table.items() if v["p95"] > 1500}
        self.check("p95 under 1.5 s on every endpoint", not slow, ", ".join(f"{k}={v['p95']}" for k, v in slow.items()) or "all under", warn=True)
        for label, v in sorted(table.items()):
            self.log(f"    {label:18} n={v['n']:2} p50={v['p50']:7.1f} p95={v['p95']:7.1f} max={v['max']:7.1f} ms")

    def s_metering(self):
        self.log("-- metering --")
        r = self.get("usage", "/v1/usage", record=False)
        d = self.data(r) or {}
        self.check("usage answers", r.status == 200 and "units" in d, f"units={d.get('units')} requests={d.get('requests')}")
        units_hdr = r.headers.get("x-usage-month")
        self.check("X-Usage-Month header stamped", bool(units_hdr), f"X-Usage-Month={units_hdr} X-RateLimit-Limit={r.headers.get('x-ratelimit-limit')}")
        # Every request this run sent with a key of this tenant — including the refusals and the
        # /v1/usage read itself — is one row on the meter. The reading is compared as a DELTA from
        # the first request of the run, so an earlier run on the same tenant does not mask a loss.
        # Metering is fail-soft by design (the answer stands, the row is lost), so a deficit is a
        # WARN naming the count: rows the invoice would not see.
        sent = self.sent["int"] + self.sent["dev"]
        seen = int(d.get("requests") or 0) - int(getattr(self, "meter_start", 0) or 0)
        self.check("meter counts this run's requests", seen >= sent, f"metered {seen} of {sent} sent this run",
                   warn=True)
        self.metrics["metering"] = {"units": d.get("units"), "requests": d.get("requests"),
                                    "sentThisRun": sent, "meteredThisRun": seen,
                                    "endpointsMetered": len({x.get("endpoint") for x in (d.get("daily") or [])})}
        rl = self.get("usage.requests", "/v1/usage/requests", {"limit": 5}, record=False)
        rows = self.data(rl) or []
        self.check("per-request log answers", rl.status == 200 and rows
                   and all(x.get("endpoint") and x.get("status") and x.get("ts") for x in rows),
                   f"{len(rows)} newest rows, e.g. {rows[0].get('endpoint') if rows else None} -> {rows[0].get('status') if rows else None}")
        self.check("a 304 is logged as a request with no unit", any(x.get("status") == 304 and not x.get("units") for x in rows)
                   or not any(x.get("status") == 304 for x in rows), "304 rows carry units=0" if rows else "")

    # -- report ---------------------------------------------------------------------------- #
    def report(self) -> dict:
        levels = {"PASS": 0, "WARN": 0, "FAIL": 0, "SKIP": 0}
        for c in self.checks:
            levels[c["level"]] += 1
        self.log(f"== validation: {levels['PASS']} PASS, {levels['WARN']} WARN, {levels['FAIL']} FAIL, {levels['SKIP']} SKIP ==")
        return {"summary": levels, "checks": self.checks, "metrics": self.metrics, "samples": self.samples,
                "asOf": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


# ---- local mode ------------------------------------------------------------------------- #
def run_local(db_url: str, *, backfill: bool, repeat: int, log=print) -> dict:
    os.environ["RWE_PLATFORM_API"] = "1"
    import identity
    import identity_backfill
    import store as store_mod
    import story_service
    from platform_api import app as platform_app
    st = store_mod.Store(db_url)
    if backfill:
        log("-- identity backfill (dry run, then real) --")
        dry = identity_backfill.run(st, dry_run=True, log=lambda s: None)
        log(f"  dry-run: {json.dumps(dry)}")
        real = identity_backfill.run(st, log=lambda s: None)
        log(f"  applied: {json.dumps(real)}")
        after = identity_backfill.run(st, dry_run=True, log=lambda s: None)
        log(f"  after:   missingArticleId={after['missingArticleId']} missingPublisherId={after['missingPublisherId']} missingLicence={after['missingLicence']}")
    else:
        identity.sync_publishers(st)
    story_service.clear_cache()
    hidden = {"urls": set(), "ids": set()}
    try:
        for row in st.hidden_article_refs():
            hidden["urls"].update(u for u in (row.get("url"), row.get("canonicalUrl")) if u)
            if row.get("articleId"):
                hidden["ids"].add(row["articleId"])
    except Exception:                        # noqa: BLE001 — the sweep still runs on the generic rules
        pass
    st.platform_create_tenant("platform-validate", "platform_validate.py (temporary)", kind="internal")
    key, meta = st.platform_mint_key(tenant_id="platform-validate", plan="internal", label="validation")
    key_dev, meta_dev = st.platform_mint_key(tenant_id="platform-validate", plan="developer", label="validation")
    try:
        client = LocalClient(platform_app.create_app(st))
        out = Battery(client, key=key, key_dev=key_dev, repeat=repeat, hidden_refs=hidden, log=log).run()
    finally:
        st.platform_revoke_key(meta["keyId"])
        st.platform_revoke_key(meta_dev["keyId"])
    try:
        import obs_metrics
        errs = (obs_metrics.snapshot().get("counters") or {}).get("platform_metering_errors_total")
        out["metrics"]["metering"]["recordErrors"] = int(errs or 0)
    except Exception:                        # noqa: BLE001
        pass
    out["mode"] = {"db": re.sub(r"//[^/]*@", "//…@", db_url), "backfill": backfill,
                   "hiddenRows": len(hidden["urls"])}
    return out


def run_remote(base_url: str, *, repeat: int, log=print) -> dict:
    key = os.environ.get("RWE_PLATFORM_KEY", "").strip()
    if not key:
        raise SystemExit("RWE_PLATFORM_KEY is not set (an internal-plan key; never pass it as an argument)")
    key_dev = os.environ.get("RWE_PLATFORM_KEY_DEV", "").strip() or None
    out = Battery(HttpClient(base_url), key=key, key_dev=key_dev, repeat=repeat, log=log).run()
    out["mode"] = {"baseUrl": base_url, "developerKey": bool(key_dev)}
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--base-url", help="a running engine, e.g. http://127.0.0.1:8000 (keys from the environment)")
    g.add_argument("--db", help="a SQLAlchemy URL to validate standalone (temporary keys, revoked at the end)")
    ap.add_argument("--backfill", action="store_true", help="(--db) run the identity backfill first")
    ap.add_argument("--repeat", type=int, default=3, help="latency repeats per endpoint")
    ap.add_argument("--json", default=None, help="write the full report here")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)
    log = (lambda s: None) if args.quiet else print
    if args.db:
        out = run_local(args.db, backfill=args.backfill, repeat=args.repeat, log=log)
    else:
        out = run_remote(args.base_url, repeat=args.repeat, log=log)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=1, ensure_ascii=False, default=str)
        log(f"report written to {args.json}")
    return 0 if out["summary"]["FAIL"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
