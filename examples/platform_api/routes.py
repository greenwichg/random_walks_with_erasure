"""routes.py — the ``/v1`` router: thin, metered wrappers over the existing services.

Every handler: authenticate (``auth``) -> rate + quota (``metering``) -> scope -> call the SAME
service the consumer route calls -> shape (``shape``) -> envelope. The route class records one
usage row per request, whatever the outcome, and stamps the limit headers.

Surface (scope in brackets; ``/health``, ``/openapi.json`` and ``/docs`` need no key)::

    GET /v1/me                                       any key — the key, its plan and month
    GET /v1/articles                                 [articles:read]
    GET /v1/articles/by-url?url=                     [articles:read]
    GET /v1/articles/{article_id}                    [articles:read]
    GET /v1/articles/{article_id}/entities           [articles:read]
    GET /v1/entities?name=&kind=                     [articles:read]
    GET /v1/countries                                [articles:read]
    GET /v1/stories                                  [stories:read]
    GET /v1/stories/{story_id}                       [stories:read]
    GET /v1/stories/{story_id}/similar               [stories:read]
    GET /v1/stories/{story_id}/intelligence          [stories:read]
    GET /v1/stories/{story_id}/coverage-comparison   [stories:read]
    GET /v1/stories/{story_id}/history               [stories:history]
    GET /v1/tags                                     [stories:read]
    GET /v1/tags/{tag}                               [stories:read]
    GET /v1/publishers                               [publishers:read]
    GET /v1/publishers/by-host?host=                 [publishers:read]
    GET /v1/publishers/{publisher_id}                [publishers:read]
    GET /v1/publishers/{publisher_id}/articles       [articles:read]
    GET /v1/publishers/{publisher_id}/stories        [stories:read]
    GET /v1/outlets/search?q=                        [publishers:read]
    GET /v1/usage                                    [usage:read]
"""

from __future__ import annotations

import sqlite3
import time
from typing import Callable, Optional

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.routing import APIRoute
from starlette.exceptions import HTTPException as StarletteHTTPException

import coverage_comparison
import identity
import ingest
import licence
import location
import outlet_search
import publisher_service
import search
import story_intelligence
import story_service
from platform_api import auth, metering, shape
from platform_api.auth import PlatformError, Principal

MAX_LIMIT = 100
TITLE = "Hidden View Platform API"
VERSION = "1.0.0"
#: Entity kinds the provider extracted (GDELT GKG) and the one we extract ourselves (``span``).
PROVIDER_ENTITY_KINDS = ("person", "org")
ENTITY_KINDS = PROVIDER_ENTITY_KINDS + ("span",)
#: Paths that answer without a key (and are therefore never metered).
PUBLIC_PATHS = frozenset({"/v1/health", "/v1/openapi.json", "/v1/docs"})

DESCRIPTION = """\
The commercial front door over the Hidden View news-intelligence engine: the same articles,
publishers, story clusters and story intelligence the consumer product serves, under durable ids,
provenance and licence-class enforcement, with per-key rate limits and per-tenant monthly quotas.

**Authentication.** Every endpoint except `/v1/health`, `/v1/openapi.json` and `/v1/docs` needs a
platform key, sent as `Authorization: Bearer hv_live_…` or `X-API-Key: hv_live_…`. Keys carry a
plan (scopes, licence classes, rate, quota); `GET /v1/me` reports the caller's own.

**Envelope.** Every answer is `{"data": …, "meta": {…}}`. `meta` carries `requestId`, `asOf`,
`versions` (scorer, build, buildConfig, registry, publisherIdScheme), `ratingsPublished`, and on
lists `page` (`limit`, `cursor`, `nextCursor`, `total`). Errors are
`{"error": {"code", "message", "requestId"}}` with stable codes.

**Licence classes.** An article whose class is outside the key's plan keeps its identity,
publisher, time, topic and story membership and loses the provider's delivery (`headline`,
`description`, `url`, image), listed under `withheld`. Reader-private rows never appear.
Third-party ratings (lean, blindspot, factuality) ship only on deployments that publish them.
"""


def _cursor(raw: Optional[str]) -> int:
    if raw is None or not str(raw).strip():
        return 0
    if not str(raw).strip().isdigit():
        raise PlatformError(400, "invalid_cursor", "cursor must be the value a previous page returned")
    return int(raw)


def _hidden(row: dict) -> bool:
    """A catalogue row the platform never serves: reader-private or provisional."""
    return ((row.get("licenceClass") or licence.UNKNOWN) == licence.READER_PRIVATE
            or row.get("articleState") == "provisional")


def _article_sort(sort: Optional[str], q: Optional[str]) -> str:
    """With a query the natural order is relevance; without one there is no relevance to sort by."""
    if sort:
        return "newest" if (sort == "relevance" and not q) else sort
    return "relevance" if q else "newest"


def _query_terms(q: str) -> list:
    """The words a query was matched on — echoed in ``meta.query`` so a caller can see what a
    punctuation-heavy or operator-looking query became."""
    import store as store_mod
    expr = store_mod.Store.fts_match_expression(q) or ""
    return [t.strip('"') for t in expr.split(" ") if t]


def _page_meta(limit: int, offset: int, total: Optional[int], has_more: Optional[bool] = None) -> dict:
    more = has_more if has_more is not None else (total is not None and offset + limit < total)
    return {"limit": limit, "cursor": str(offset), "nextCursor": str(offset + limit) if more else None,
            "total": total}


def build_router(get_store: Callable, *, get_request_id: "Callable[[], str] | None" = None) -> APIRouter:
    """The router, bound to a store getter (the engine's ``_require_store`` or a fixed store)."""

    def rid(request: Request) -> Optional[str]:
        if get_request_id is not None:
            try:
                return get_request_id()
            except Exception:                # noqa: BLE001
                pass
        return request.headers.get("x-request-id")

    class MeteredRoute(APIRoute):
        """Meter every request that authenticated, whatever it answered."""

        def get_route_handler(self):
            original = super().get_route_handler()
            path = self.path

            async def handler(request: Request) -> Response:
                t0 = time.perf_counter()
                status = 500
                try:
                    resp = await original(request)
                    status = resp.status_code
                    usage = getattr(request.state, "platform_usage", None)
                    p = getattr(request.state, "principal", None)
                    if p is not None:
                        resp.headers["X-RateLimit-Limit"] = str(p.rate_per_min)
                        if usage:
                            resp.headers["X-Usage-Month"] = str(usage["used"] + 1)
                            resp.headers["X-Usage-Limit"] = str(usage["limit"])
                    return resp
                except StarletteHTTPException as exc:
                    status = exc.status_code
                    raise
                finally:
                    p = getattr(request.state, "principal", None)
                    if p is not None:
                        metering.record(get_store(), p, endpoint=path, status=status,
                                        latency_ms=round((time.perf_counter() - t0) * 1000.0, 2),
                                        request_id=rid(request))

            return handler

    router = APIRouter(prefix="/v1", tags=["platform"], route_class=MeteredRoute)

    def principal_dep(request: Request) -> Principal:
        st = get_store()
        p = auth.authenticate(st, request)
        request.state.principal = p
        ok, retry = metering.check_rate(p)
        if not ok:
            raise PlatformError(429, "rate_limited", "Too many requests for this key.",
                                headers={"Retry-After": str(retry)})
        usage = metering.check_quota(st, p)
        request.state.platform_usage = usage
        if usage["exceeded"]:
            raise PlatformError(429, "quota_exceeded",
                                f"Monthly quota of {usage['limit']} units reached.")
        return p

    def scoped(scope: str):
        def dep(p: Principal = Depends(principal_dep)) -> Principal:
            auth.require_scope(p, scope)
            return p
        return dep

    # ---- helpers ---------------------------------------------------------------------- #
    def meta_for(st, urls, *, with_channels: bool = False) -> dict:
        try:
            metas = st.article_meta_for_urls(urls)
        except Exception:                    # noqa: BLE001 — identity is additive
            return {}
        if with_channels and metas:
            try:
                prov = st.provenance_for_urls({m["canonicalUrl"] for m in metas.values()})
            except Exception:                # noqa: BLE001
                prov = {}
            for m in metas.values():
                m["channels"] = [x["channel"] for x in prov.get(m["canonicalUrl"], ())]
        return metas

    def publisher_name(st, publisher_id: Optional[str], publisher: Optional[str]) -> Optional[str]:
        if publisher_id:
            row = st.publisher_by_id(publisher_id)
            if row is None:
                raise PlatformError(404, "not_found", "Unknown publisher id.")
            return row["name"]
        return publisher or None

    def _publisher_or_404(st, publisher_id: str) -> dict:
        row = st.publisher_by_id(publisher_id)
        if row is None:
            raise PlatformError(404, "not_found", "Unknown publisher id.")
        return row

    def _article_view(row: dict) -> dict:
        import discover                       # the shared Article serializer (consumer path)
        return discover.feed_article_to_article(row)

    def _article_items(st, results: list, p: Principal) -> list:
        """Consumer Article dicts (``search.search`` results or serialised rows) -> the page."""
        metas = meta_for(st, [a["id"] for a in results], with_channels=True)
        items = [shape.article(a, metas.get(a["id"]), p.licence_classes) for a in results]
        return [a for a in items if a is not None]

    def _article_page(request: Request, st, res: dict, p: Principal, *, limit: int, offset: int,
                      q: Optional[str] = None) -> dict:
        items = _article_items(st, res["results"], p)
        extra = {"sort": res.get("sort")}
        if q:
            extra["query"] = {"q": q, "terms": _query_terms(q)}
        return shape.envelope(items, request_id=rid(request),
                              page=_page_meta(limit, offset, res.get("total"), res.get("hasMore")),
                              **extra)

    def _story_page(request: Request, st, res: dict, p: Principal, *, limit: int, offset: int,
                    **extra) -> dict:
        for s in res.get("stories", []):
            s.update(story_intelligence.compute_summary(s))
        urls = [c.get("url") for s in res["stories"] for c in (s.get("coverage") or ())]
        metas = meta_for(st, urls)
        items = [shape.story(s, metas, p.licence_classes, with_coverage=False)
                 for s in res["stories"]]
        return shape.envelope(items, request_id=rid(request),
                              page=_page_meta(limit, offset, res.get("total"), res.get("hasMore")),
                              **extra)

    def _story_or_404(st, story_id: str) -> dict:
        s = story_service.get_story(st, story_id)
        if s is None:
            raise PlatformError(404, "not_found", "No live story has that id.")
        return s

    def _row_or_404(st, ref: str) -> dict:
        row = st.resolve_article(ref)
        if row is None or _hidden(row):
            raise PlatformError(404, "not_found", "No article matches that reference.")
        return row

    def _ratings_or_403(*wanted) -> None:
        if any(wanted) and not shape.publish_ratings():
            raise PlatformError(403, "ratings_not_published",
                                "This deployment does not publish third-party ratings, so "
                                "lean / blindspot filters are unavailable.")

    # ---- public: liveness + documentation ------------------------------------------------ #
    @router.get("/health", summary="Platform liveness + the versions in force",
                description="No key needed. `meta.versions` names the scorer, build, build "
                            "configuration and registry snapshot every other answer reflects.")
    def health(request: Request) -> dict:
        return shape.envelope({"status": "ok"}, request_id=rid(request))

    _schema: dict = {}

    def openapi_schema() -> dict:
        if not _schema:
            schema = get_openapi(title=TITLE, version=VERSION, description=DESCRIPTION,
                                 routes=router.routes)
            comps = schema.setdefault("components", {})
            comps["securitySchemes"] = {
                "bearerAuth": {"type": "http", "scheme": "bearer", "bearerFormat": "hv_live_…"},
                "apiKeyAuth": {"type": "apiKey", "in": "header", "name": "X-API-Key"}}
            for path, ops in (schema.get("paths") or {}).items():
                if path in PUBLIC_PATHS:
                    continue
                for op in ops.values():
                    if isinstance(op, dict):
                        op["security"] = [{"bearerAuth": []}, {"apiKeyAuth": []}]
            _schema.update(schema)
        return _schema

    @router.get("/openapi.json", include_in_schema=False)
    def openapi_json() -> JSONResponse:
        return JSONResponse(openapi_schema())

    @router.get("/docs", include_in_schema=False)
    def docs() -> HTMLResponse:
        return get_swagger_ui_html(openapi_url="/v1/openapi.json", title=f"{TITLE} — docs")

    # ---- the caller ------------------------------------------------------------------- #
    @router.get("/me", summary="The calling key: tenant, plan, scopes, classes, limits, month-to-date",
                description="Any valid key. What this key may read and how much of the month's "
                            "quota its tenant has used — the numbers behind the `X-RateLimit-*` "
                            "and `X-Usage-*` headers.")
    def me(request: Request, p: Principal = Depends(principal_dep)) -> dict:
        st = get_store()
        tenant = st.platform_tenant(p.tenant_id) or {}
        month = metering.month_of()
        totals = st.platform_usage_month(p.tenant_id, month)
        return shape.envelope({
            "tenantId": p.tenant_id, "tenantName": tenant.get("name"), "tenantKind": p.tenant_kind,
            "keyId": p.key_id, "plan": p.plan, "scopes": sorted(p.scopes),
            "licenceClasses": sorted(p.licence_classes),
            "limits": {"ratePerMin": p.rate_per_min, "quotaMonth": p.quota_month},
            "usage": {"month": month, "units": totals["units"], "requests": totals["requests"]},
            "published": {"ratings": shape.publish_ratings(),
                          "wikipedia": shape.publish_wikipedia()},
        }, request_id=rid(request))

    # ---- articles --------------------------------------------------------------------- #
    @router.get("/articles", summary="Search the catalogue",
                description="Term search and filters over the article catalogue. `q` is words, "
                            "any order, all required, stemmed (`resign` finds `resigns`), matched "
                            "in the headline, snippet, publisher name and category; a trailing "
                            "`*` keeps a prefix. With `q` the default `sort` is `relevance` "
                            "(bm25, headline weighted), else `newest`; `oldest` and `publisher` "
                            "are also accepted. Provisional (uncorroborated, reader-private) rows "
                            "are excluded in SQL. Page with `cursor` = the previous `nextCursor`.")
    def articles(request: Request, p: Principal = Depends(scoped("articles:read")),
                 q: Optional[str] = Query(None, max_length=200),
                 publisher_id: Optional[str] = None, publisher: Optional[str] = None,
                 topic: Optional[str] = None, country: Optional[str] = None,
                 from_: Optional[str] = Query(None, alias="from"), to: Optional[str] = None,
                 sort: Optional[str] = Query(None, pattern="^(relevance|newest|oldest|publisher)$"),
                 limit: int = Query(30, ge=1, le=MAX_LIMIT),
                 cursor: Optional[str] = None) -> dict:
        st = get_store()
        offset = _cursor(cursor)
        # Provisional (extension-born, uncorroborated) rows are excluded in SQL, not after the
        # page is cut: they are reader-private, and a page that drops them after fetching would
        # be short by exactly the rows it must not show.
        res = search.search(st, query=q, publisher=publisher_name(st, publisher_id, publisher),
                            topic=topic, country=country, date_from=from_, date_to=to,
                            sort=_article_sort(sort, q), limit=limit, offset=offset,
                            include_provisional=False, terms=True)
        return _article_page(request, st, res, p, limit=limit, offset=offset, q=q)

    @router.get("/articles/by-url", summary="One article by any URL form ever observed",
                description="Resolves the raw, canonical or any aliased URL form to the article.")
    def article_by_url(request: Request, url: str = Query(..., max_length=2048),
                       p: Principal = Depends(scoped("articles:read"))) -> dict:
        return _one_article(request, url, p)

    @router.get("/articles/{article_id}/entities", summary="Named entities on one article",
                description="Provider-extracted `person` / `org` names (GDELT GKG; attribution "
                            "in `attribution`) by default; `kind=span` returns the capitalised "
                            "spans we extract from the headline ourselves. Names are stored "
                            "normalised (lower-cased).")
    def article_entities(request: Request, article_id: str,
                         kind: Optional[str] = Query(None, pattern="^(person|org|span)$"),
                         p: Principal = Depends(scoped("articles:read"))) -> dict:
        st = get_store()
        row = _row_or_404(st, article_id)
        kinds = (kind,) if kind else PROVIDER_ENTITY_KINDS
        found = st.entities_for_urls([row["canonicalUrl"]], kinds=kinds).get(row["canonicalUrl"], {})
        provider = [k for k in kinds if k in PROVIDER_ENTITY_KINDS]
        return shape.envelope({
            "articleId": row.get("articleId"), "publisherId": row.get("publisherId"),
            "entities": {k: list(found.get(k) or ()) for k in kinds},
            "attribution": licence.attribution_for(("gdelt",)) if provider else [],
        }, request_id=rid(request))

    @router.get("/articles/{ref:path}", summary="One article by id",
                description="`ar_…` id (or a URL). Carries the story it sits in now and the "
                            "channels it was observed through.")
    def article_by_id(request: Request, ref: str,
                      p: Principal = Depends(scoped("articles:read"))) -> dict:
        return _one_article(request, ref, p)

    def _one_article(request: Request, ref: str, p: Principal) -> dict:
        st = get_store()
        row = st.resolve_article(ref)
        if row is None:
            raise PlatformError(404, "not_found", "No article matches that reference.")
        a = _article_view(row)
        prov = st.article_provenance(row["canonicalUrl"])
        meta = {"articleId": row.get("articleId"), "publisherId": row.get("publisherId"),
                "licenceClass": row.get("licenceClass"), "articleState": row.get("articleState"),
                "channels": [x["channel"] for x in prov]}
        out = shape.article(a, meta, p.licence_classes)
        if out is None:
            raise PlatformError(404, "not_found", "No article matches that reference.")
        # The story this article sits in NOW, from the id ledger the served build writes. The
        # default build is cached (and warmed by the poller); serving it here only ensures the
        # ledger reflects the current catalogue on a cold engine.
        try:
            story_service.list_stories(st, limit=1)
            ledger = st.story_ids_for_urls([row.get("url"), row["canonicalUrl"]])
        except Exception:                    # noqa: BLE001 — membership is additive
            ledger = {}
        sid = ledger.get(row.get("url")) or ledger.get(row["canonicalUrl"])
        if sid:
            out["storyId"] = sid
        out["provenance"] = [{"channel": x["channel"], "firstObservedAt": x["firstObservedAt"],
                              "lastObservedAt": x["lastObservedAt"],
                              "licenceClass": x["licenceClass"]} for x in prov]
        return shape.envelope(out, request_id=rid(request))

    @router.get("/entities", summary="Articles carrying a named entity",
                description="The catalogue rows on which `name` was extracted as a `person` or "
                            "`org` (provider-extracted; `kind=span` for our own headline spans), "
                            "newest first. `name` is matched normalised (case- and "
                            "whitespace-insensitive).")
    def entities(request: Request, name: str = Query(..., min_length=1, max_length=200),
                 kind: Optional[str] = Query(None, pattern="^(person|org|span)$"),
                 limit: int = Query(30, ge=1, le=MAX_LIMIT), cursor: Optional[str] = None,
                 p: Principal = Depends(scoped("articles:read"))) -> dict:
        st = get_store()
        offset = _cursor(cursor)
        kinds = (kind,) if kind else PROVIDER_ENTITY_KINDS
        rows, total = st.articles_for_entity(name, kinds=kinds, limit=limit, offset=offset)
        items = _article_items(st, [_article_view(r) for r in rows], p)
        return shape.envelope(items, request_id=rid(request),
                              page=_page_meta(limit, offset, total),
                              entity={"name": " ".join(name.split()).lower(), "kinds": list(kinds)})

    @router.get("/countries", summary="Where the catalogue's events happen",
                description="Per-country article and publisher counts over the EVENT geography "
                            "(provider-extracted locations), most-covered first. A country "
                            "counts the events that happened there, never a publisher's home.")
    def countries(request: Request, p: Principal = Depends(scoped("articles:read"))) -> dict:
        st = get_store()
        rows = st.feed_article_country_facets(include_provisional=False)
        items = [{"country": r["country"], "name": location.country_name(r["country"]) or None,
                  "articles": r["articles"], "publishers": r["publishers"]} for r in rows]
        return shape.envelope(items, request_id=rid(request), total=len(items))

    # ---- stories ---------------------------------------------------------------------- #
    @router.get("/stories", summary="News events, clustered — filtered and paged",
                description="The served story build: one row per event with its publishers, "
                            "countries, freshness, lifecycle and tags. `q` finds the events whose "
                            "member articles match the words (any order, all required, stemmed); "
                            "under the default `sort=top` the best-matched events lead. "
                            "`lean` / `blindspot` filters exist only where ratings are published.")
    def stories(request: Request, p: Principal = Depends(scoped("stories:read")),
                q: Optional[str] = Query(None, max_length=200),
                topic: Optional[str] = None, publisher_id: Optional[str] = None,
                publisher: Optional[str] = None, country: Optional[str] = None,
                tag: Optional[str] = None, type: Optional[str] = None,
                lean: Optional[str] = None, blindspot: Optional[str] = None,
                from_: Optional[str] = Query(None, alias="from"), to: Optional[str] = None,
                sort: str = "top", limit: int = Query(30, ge=1, le=MAX_LIMIT),
                cursor: Optional[str] = None) -> dict:
        st = get_store()
        _ratings_or_403(lean, blindspot)
        offset = _cursor(cursor)
        res = story_service.list_stories(st, topic=topic,
                                         publisher=publisher_name(st, publisher_id, publisher),
                                         lean=lean, country=country, blindspot=blindspot,
                                         story_type=type, tag=tag, date_from=from_, date_to=to,
                                         sort=sort, limit=limit, offset=offset, query=q)
        extra = {"query": {"q": q, "terms": _query_terms(q)}} if q else {}
        return _story_page(request, st, res, p, limit=limit, offset=offset, **extra)

    @router.get("/stories/{story_id}", summary="One story with its coverage",
                description="The event, every member article (identity, publisher, time, "
                            "attachment) and — for members inside the plan's licence classes — "
                            "their headline and link.")
    def story(request: Request, story_id: str,
              p: Principal = Depends(scoped("stories:read"))) -> dict:
        st = get_store()
        s = _story_or_404(st, story_id)
        s = dict(s, **story_intelligence.compute_summary(s))
        metas = meta_for(st, [c.get("url") for c in (s.get("coverage") or ())])
        return shape.envelope(shape.story(s, metas, p.licence_classes), request_id=rid(request))

    @router.get("/stories/{story_id}/similar", summary="Stories about the same or a related event")
    def similar(request: Request, story_id: str, limit: int = Query(10, ge=1, le=25),
                p: Principal = Depends(scoped("stories:read"))) -> dict:
        st = get_store()
        found = story_service.similar_stories(st, story_id, limit=limit)
        if found is None:
            raise PlatformError(404, "not_found", "No live story has that id.")
        metas = meta_for(st, [c.get("url") for s in found for c in (s.get("coverage") or ())])
        items = [shape.story(s, metas, p.licence_classes, with_coverage=False) for s in found]
        return shape.envelope(items, request_id=rid(request), total=len(items))

    @router.get("/stories/{story_id}/intelligence", summary="Freshness, momentum, lifecycle, alerts")
    def intelligence(request: Request, story_id: str,
                     p: Principal = Depends(scoped("stories:read"))) -> dict:
        st = get_store()
        s = _story_or_404(st, story_id)
        intel = story_intelligence.compute_intelligence(s, reads=None)
        intel.pop("newSinceLastVisit", None)              # reader-relative: not on the platform
        return shape.envelope(intel, request_id=rid(request))

    @router.get("/stories/{story_id}/coverage-comparison",
                summary="One member article against the rest of its story's coverage",
                description="Counted facts (never text interpretation): who else carried the "
                            "event, event geography the wider coverage records, register mix, "
                            "timing and what only this article brings. Name the member with "
                            "`article_id` or `url`. A gated cluster answers "
                            "`{available: false, reason}`; evidence links follow each member's "
                            "licence class; viewpoint findings need published ratings.")
    def comparison(request: Request, story_id: str, article_id: Optional[str] = None,
                   url: Optional[str] = Query(None, max_length=2048),
                   p: Principal = Depends(scoped("stories:read"))) -> dict:
        st = get_store()
        ref = (article_id or url or "").strip()
        if not ref:
            raise PlatformError(400, "invalid_request",
                                "Name the member article: article_id= or url=.")
        s = _story_or_404(st, story_id)
        row = _row_or_404(st, ref)
        canon = row["canonicalUrl"]
        # The comparison runs over the coverage the platform serves: a hidden member (reader-
        # private, provisional) is not counted and cannot be picked as evidence — the same set
        # `/v1/stories/{id}` lists. Members outside the plan's classes stay: they are counted,
        # and their links are withheld in the shaping.
        metas = meta_for(st, [c.get("url") for c in (s.get("coverage") or ())])
        visible = shape.visible_coverage(s, metas)
        member = next((m for m in visible
                       if ingest.canonical_url(str(m.get("id") or m.get("url") or "")) == canon), None)
        if member is None:
            raise PlatformError(404, "not_found", "That article is not in this story's coverage.")
        a = _article_view(row)
        countries_ = st.article_event_countries(canon)
        # The analyzer's exact call (article_analyzer.analyze): the member's own facts first, the
        # serialised row's as the fallback, the article's provider-extracted countries passed in.
        result = coverage_comparison.compare(
            {"publisher": member.get("publisher") or a.get("publisher"),
             "url": member.get("url") or canon,
             "leanBucket": member.get("leanBucket") or a.get("leanBucket"),
             "register": member.get("register")},
            dict(s, coverage=visible, totalCoverage=len(visible)),
            target_countries=countries_, member=member)
        out = shape.comparison(result or {}, metas, p.licence_classes)
        out["storyId"], out["articleId"] = story_id, row.get("articleId")
        return shape.envelope(out, request_id=rid(request))

    @router.get("/stories/{story_id}/history", summary="How the story was served over time",
                description="The persisted record: every snapshot the served build changed, and "
                            "each member's join / leave. Enterprise scope.")
    def history(request: Request, story_id: str, limit: int = Query(200, ge=1, le=1000),
                p: Principal = Depends(scoped("stories:history"))) -> dict:
        st = get_store()
        h = st.story_history(story_id, limit=limit)
        if h is None:
            raise PlatformError(404, "not_found", "No recorded story has that id.")
        if not shape.publish_ratings():
            for snap in h["snapshots"]:
                snap["distribution"] = None
                snap["blindspotSide"] = None
                snap.pop("blindspotWithheld", None)
            h["withheld"] = ["snapshots.distribution", "snapshots.blindspotSide"]
        urls = [m["url"] for m in h["membership"]]
        metas = meta_for(st, urls)
        kept = []
        for m in h["membership"]:
            meta = metas.get(m["url"]) or {}
            # A hidden member (reader-private, provisional) is not in the history either: the
            # ledger recorded its join, but its existence is the reader's, not ours to serve.
            # (Found by platform_validate.py's exposure sweep before the first customer did.)
            if shape.hidden(meta):
                continue
            cls = meta.get("licenceClass") or "unknown"
            if not m.get("articleId"):
                m["articleId"] = meta.get("articleId")
            m["licence"] = {"class": cls}
            if cls not in p.licence_classes:
                m.pop("url", None)
            kept.append(m)
        h["membership"] = kept
        return shape.envelope(h, request_id=rid(request))

    # ---- tags ------------------------------------------------------------------------- #
    @router.get("/tags", summary="The story-tag vocabulary of the live window, most-carried first",
                description="Every tag the served build recorded for a story (named entities, "
                            "subjects and the topic shelf), with how many stories carry it. "
                            "`q` filters by substring; `min_stories` drops the long tail. Feed "
                            "a tag to `/v1/tags/{tag}` (every story recorded under it) or "
                            "`/v1/stories?tag=` (the consumer's discovery filter, which omits "
                            "tags reaching a single story).")
    def tags(request: Request, p: Principal = Depends(scoped("stories:read")),
             q: Optional[str] = Query(None, max_length=100), min_stories: int = Query(1, ge=1),
             limit: int = Query(50, ge=1, le=MAX_LIMIT), cursor: Optional[str] = None) -> dict:
        st = get_store()
        offset = _cursor(cursor)
        story_service.list_stories(st, limit=1)           # the served build writes the projection
        needle = (q or "").strip().lower()
        items = [r for r in st.tag_vocabulary()
                 if r["stories"] >= min_stories
                 and (not needle or needle in r["tag"] or needle in r["label"].lower())]
        return shape.envelope(items[offset:offset + limit], request_id=rid(request),
                              page=_page_meta(limit, offset, len(items)))

    @router.get("/tags/{tag}", summary="Stories recorded under one tag",
                description="The stories the projection holds for the tag, strongest "
                            "association first, as `/v1/stories` rows. 404 when no live story "
                            "carries it.")
    def tag_stories(request: Request, tag: str,
                    limit: int = Query(30, ge=1, le=MAX_LIMIT), cursor: Optional[str] = None,
                    p: Principal = Depends(scoped("stories:read"))) -> dict:
        st = get_store()
        offset = _cursor(cursor)
        name = " ".join(tag.strip().lower().split())
        story_service.list_stories(st, limit=1)           # same warm as /v1/tags
        ids = st.stories_for_tag(name, limit=1000)
        found = [s for s in (story_service.get_story(st, sid) for sid in ids) if s is not None]
        if not found:
            raise PlatformError(404, "not_found", "No live story carries that tag.")
        res = {"stories": found[offset:offset + limit], "total": len(found),
               "hasMore": offset + limit < len(found)}
        return _story_page(request, st, res, p, limit=limit, offset=offset, tag=name)

    # ---- publishers ------------------------------------------------------------------- #
    @router.get("/publishers", summary="Find publishers: by name, host, place, kind — or the busiest",
                description="`name` resolves one outlet through the registry (any name form or "
                            "host). Otherwise a filtered listing, busiest first: `q` (substring "
                            "of the display name or a host), `country` (ISO-3166 alpha-2), "
                            "`scope`, `kind`, `registered`.")
    def publishers(request: Request, p: Principal = Depends(scoped("publishers:read")),
                   name: Optional[str] = None, q: Optional[str] = Query(None, max_length=100),
                   country: Optional[str] = Query(None, max_length=2), scope: Optional[str] = None,
                   kind: Optional[str] = None, registered: Optional[bool] = None,
                   limit: int = Query(30, ge=1, le=MAX_LIMIT),
                   cursor: Optional[str] = None) -> dict:
        st = get_store()
        if name:
            pid = identity.publisher_id_for(name)
            row = st.publisher_by_id(pid) if pid else None
            if row is None:
                raise PlatformError(404, "not_found", "No publisher matches that name.")
            return shape.envelope([shape.publisher(row, st.publisher_hosts(pid), None)],
                                  request_id=rid(request), total=1)
        offset = _cursor(cursor)
        rows, total = st.list_publishers(limit=limit, offset=offset, registered=registered,
                                         country=country, scope=scope, kind=kind, q=q)
        items = [shape.publisher(r, [], None) for r in rows]
        return shape.envelope(items, request_id=rid(request),
                              page=_page_meta(limit, offset, total))

    @router.get("/publishers/by-host", summary="Resolve a hostname (or URL) to its publisher",
                description="`www.` / `m.` / `amp.` prefixes are stripped; an unknown host that "
                            "the registry can place still resolves.")
    def publisher_by_host(request: Request, host: str = Query(..., min_length=1, max_length=2048),
                          p: Principal = Depends(scoped("publishers:read"))) -> dict:
        st = get_store()
        h = outlet_search.canonical_host(host)
        row = st.publisher_for_host(h) if h else None
        if row is None:
            pid = identity.publisher_id_for(h or host)
            row = st.publisher_by_id(pid) if pid else None
        if row is None:
            raise PlatformError(404, "not_found", "No publisher is known for that host.")
        return shape.envelope(shape.publisher(row, st.publisher_hosts(row["publisherId"]), None),
                              request_id=rid(request), host=h or host)

    @router.get("/publishers/{publisher_id}", summary="One publisher: curated facts + counted profile",
                description="Registry facts (country, scope, kind, ownership), hosts, catalogue "
                            "counts, and the counted profile (topics, languages, event "
                            "countries, co-coverage).")
    def publisher(request: Request, publisher_id: str,
                  p: Principal = Depends(scoped("publishers:read"))) -> dict:
        st = get_store()
        row = _publisher_or_404(st, publisher_id)
        profile = None
        try:
            profile = publisher_service.get_publisher(st, row["name"], recent_limit=0)
        except Exception:                    # noqa: BLE001 — the curated row stands alone
            profile = None
        return shape.envelope(shape.publisher(row, st.publisher_hosts(publisher_id), profile),
                              request_id=rid(request))

    @router.get("/publishers/{publisher_id}/articles", summary="One publisher's articles",
                description="`/v1/articles` scoped to the publisher.")
    def publisher_articles(request: Request, publisher_id: str,
                           q: Optional[str] = Query(None, max_length=200),
                           topic: Optional[str] = None,
                           from_: Optional[str] = Query(None, alias="from"), to: Optional[str] = None,
                           sort: Optional[str] = Query(None, pattern="^(relevance|newest|oldest|publisher)$"),
                           limit: int = Query(30, ge=1, le=MAX_LIMIT),
                           cursor: Optional[str] = None,
                           p: Principal = Depends(scoped("articles:read"))) -> dict:
        st = get_store()
        row = _publisher_or_404(st, publisher_id)
        offset = _cursor(cursor)
        res = search.search(st, query=q, publisher=row["name"], topic=topic, date_from=from_,
                            date_to=to, sort=_article_sort(sort, q), limit=limit, offset=offset,
                            include_provisional=False, terms=True)
        return _article_page(request, st, res, p, limit=limit, offset=offset, q=q)

    @router.get("/publishers/{publisher_id}/stories", summary="Stories one publisher covered",
                description="`/v1/stories` scoped to events with coverage from the publisher.")
    def publisher_stories(request: Request, publisher_id: str,
                          q: Optional[str] = Query(None, max_length=200), topic: Optional[str] = None,
                          country: Optional[str] = None,
                          from_: Optional[str] = Query(None, alias="from"), to: Optional[str] = None,
                          sort: str = "top", limit: int = Query(30, ge=1, le=MAX_LIMIT),
                          cursor: Optional[str] = None,
                          p: Principal = Depends(scoped("stories:read"))) -> dict:
        st = get_store()
        row = _publisher_or_404(st, publisher_id)
        offset = _cursor(cursor)
        res = story_service.list_stories(st, publisher=row["name"], topic=topic, country=country,
                                         date_from=from_, date_to=to, sort=sort, limit=limit,
                                         offset=offset, query=q)
        extra = {"query": {"q": q, "terms": _query_terms(q)}} if q else {}
        return _story_page(request, st, res, p, limit=limit, offset=offset, **extra)

    # ---- outlets (the index, not the catalogue) --------------------------------------- #
    @router.get("/outlets/search", summary="Find news outlets by place, language or name",
                description="The outlet index the source-discovery pipeline builds (Wikidata, "
                            "Wikipedia, Common Crawl, observed feeds): outlets that may not be "
                            "in the catalogue yet. Queries like `local news websites in Kenya` "
                            "or `Swahili language news site Kenya` are planned as geography; "
                            "anything else is full-text. Internal index only — no paid upstream "
                            "is spent on a platform request.")
    def outlets_search(request: Request, q: str = Query(..., min_length=1, max_length=200),
                       count: int = Query(10, ge=1, le=50),
                       p: Principal = Depends(scoped("publishers:read"))) -> dict:
        st = get_store()
        try:
            con = outlet_search.open_index()
        except (sqlite3.Error, OSError):
            raise PlatformError(503, "search_unavailable",
                                "The outlet index is not available on this deployment.")
        try:
            plan = outlet_search.plan_query(q)
            rows = outlet_search.query_index(con, plan, count=count,
                                             feedback=outlet_search.feedback_weights(st))
        except sqlite3.Error:
            raise PlatformError(503, "search_unavailable",
                                "The outlet index is not available on this deployment.")
        finally:
            con.close()
        items = []
        for r in rows:
            pid = None
            if r.get("tracked"):
                known = st.publisher_for_host(r["host"])
                pid = (known or {}).get("publisherId") or identity.publisher_id_for(r["host"])
            items.append(shape.outlet(r, pid))
        return shape.envelope(items, request_id=rid(request), total=len(items), query=plan)

    # ---- the meter -------------------------------------------------------------------- #
    @router.get("/usage", summary="The tenant's own meter",
                description="Units and requests this month (or `month=YYYY-MM`), per day, key "
                            "and endpoint. Refused requests count as requests, never as units.")
    def usage(request: Request, p: Principal = Depends(scoped("usage:read")),
              month: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}$")) -> dict:
        st = get_store()
        m = month or metering.month_of()
        totals = st.platform_usage_month(p.tenant_id, m)
        return shape.envelope({"tenantId": p.tenant_id, "month": m, "plan": p.plan,
                               "quotaMonth": p.quota_month, "ratePerMin": p.rate_per_min,
                               "units": totals["units"], "requests": totals["requests"],
                               "daily": st.platform_usage(p.tenant_id, since_day=f"{m}-01")},
                              request_id=rid(request))

    return router


__all__ = ["MAX_LIMIT", "TITLE", "VERSION", "PUBLIC_PATHS", "build_router"]
