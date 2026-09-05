"""routes.py — the ``/v1`` router: thin, metered wrappers over the existing services.

Every handler: authenticate (``auth``) -> rate + quota (``metering``) -> scope -> call the SAME
service the consumer route calls -> shape (``shape``) -> envelope. The route class records one
usage row per request, whatever the outcome, and stamps the limit headers.
"""

from __future__ import annotations

import time
from typing import Callable, Optional

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.routing import APIRoute
from starlette.exceptions import HTTPException as StarletteHTTPException

import identity
import publisher_service
import search
import story_intelligence
import story_service
from platform_api import auth, metering, shape
from platform_api.auth import PlatformError, Principal

MAX_LIMIT = 100


def _cursor(raw: Optional[str]) -> int:
    if raw is None or not str(raw).strip():
        return 0
    if not str(raw).strip().isdigit():
        raise PlatformError(400, "invalid_cursor", "cursor must be the value a previous page returned")
    return int(raw)


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

    # ---- routes ----------------------------------------------------------------------- #
    @router.get("/health", summary="Platform liveness + the versions in force")
    def health(request: Request) -> dict:
        return shape.envelope({"status": "ok"}, request_id=rid(request))

    @router.get("/articles", summary="Search the catalogue")
    def articles(request: Request, p: Principal = Depends(scoped("articles:read")),
                 q: Optional[str] = Query(None, max_length=200),
                 publisher_id: Optional[str] = None, publisher: Optional[str] = None,
                 topic: Optional[str] = None, country: Optional[str] = None,
                 from_: Optional[str] = Query(None, alias="from"), to: Optional[str] = None,
                 sort: str = "newest", limit: int = Query(30, ge=1, le=MAX_LIMIT),
                 cursor: Optional[str] = None) -> dict:
        st = get_store()
        offset = _cursor(cursor)
        # Provisional (extension-born, uncorroborated) rows are excluded in SQL, not after the
        # page is cut: they are reader-private, and a page that drops them after fetching would
        # be short by exactly the rows it must not show.
        res = search.search(st, query=q, publisher=publisher_name(st, publisher_id, publisher),
                            topic=topic, country=country, date_from=from_, date_to=to,
                            sort=sort, limit=limit, offset=offset, include_provisional=False)
        metas = meta_for(st, [a["id"] for a in res["results"]], with_channels=True)
        items = [shape.article(a, metas.get(a["id"]), p.licence_classes) for a in res["results"]]
        items = [a for a in items if a is not None]
        nxt = str(offset + limit) if res.get("hasMore") else None
        return shape.envelope(items, request_id=rid(request),
                              page={"limit": limit, "cursor": str(offset), "nextCursor": nxt,
                                    "total": res.get("total")})

    @router.get("/articles/by-url", summary="One article by any URL form ever observed")
    def article_by_url(request: Request, url: str = Query(..., max_length=2048),
                       p: Principal = Depends(scoped("articles:read"))) -> dict:
        return _one_article(request, url, p)

    @router.get("/articles/{ref:path}", summary="One article by id")
    def article_by_id(request: Request, ref: str,
                      p: Principal = Depends(scoped("articles:read"))) -> dict:
        return _one_article(request, ref, p)

    def _one_article(request: Request, ref: str, p: Principal) -> dict:
        st = get_store()
        row = st.resolve_article(ref)
        if row is None:
            raise PlatformError(404, "not_found", "No article matches that reference.")
        import discover                       # the shared Article serializer (consumer path)
        a = discover.feed_article_to_article(row)
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

    @router.get("/stories", summary="News events, clustered — filtered and paged")
    def stories(request: Request, p: Principal = Depends(scoped("stories:read")),
                topic: Optional[str] = None, publisher_id: Optional[str] = None,
                publisher: Optional[str] = None, country: Optional[str] = None,
                tag: Optional[str] = None, type: Optional[str] = None,
                lean: Optional[str] = None, blindspot: Optional[str] = None,
                from_: Optional[str] = Query(None, alias="from"), to: Optional[str] = None,
                sort: str = "top", limit: int = Query(30, ge=1, le=MAX_LIMIT),
                cursor: Optional[str] = None) -> dict:
        st = get_store()
        if (lean or blindspot) and not shape.publish_ratings():
            raise PlatformError(403, "ratings_not_published",
                                "This deployment does not publish third-party ratings, so "
                                "lean / blindspot filters are unavailable.")
        offset = _cursor(cursor)
        res = story_service.list_stories(st, topic=topic,
                                         publisher=publisher_name(st, publisher_id, publisher),
                                         lean=lean, country=country, blindspot=blindspot,
                                         story_type=type, tag=tag, date_from=from_, date_to=to,
                                         sort=sort, limit=limit, offset=offset)
        for s in res.get("stories", []):
            s.update(story_intelligence.compute_summary(s))
        urls = [c.get("url") for s in res["stories"] for c in (s.get("coverage") or ())]
        metas = meta_for(st, urls)
        items = [shape.story(s, metas, p.licence_classes, with_coverage=False)
                 for s in res["stories"]]
        nxt = str(offset + limit) if res.get("hasMore") else None
        return shape.envelope(items, request_id=rid(request),
                              page={"limit": limit, "cursor": str(offset), "nextCursor": nxt,
                                    "total": res.get("total")})

    def _story_or_404(st, story_id: str) -> dict:
        s = story_service.get_story(st, story_id)
        if s is None:
            raise PlatformError(404, "not_found", "No live story has that id.")
        return s

    @router.get("/stories/{story_id}", summary="One story with its coverage")
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

    @router.get("/stories/{story_id}/history", summary="How the story was served over time")
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
        for m in h["membership"]:
            meta = metas.get(m["url"]) or {}
            cls = meta.get("licenceClass") or "unknown"
            if not m.get("articleId"):
                m["articleId"] = meta.get("articleId")
            m["licence"] = {"class": cls}
            if cls == "reader_private" or (
                    cls not in p.licence_classes):
                m.pop("url", None)
        return shape.envelope(h, request_id=rid(request))

    @router.get("/publishers", summary="Resolve a publisher by name or list the busiest")
    def publishers(request: Request, p: Principal = Depends(scoped("publishers:read")),
                   name: Optional[str] = None, limit: int = Query(30, ge=1, le=MAX_LIMIT),
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
        rows, total = st.list_publishers(limit=limit, offset=offset)
        items = [shape.publisher(r, [], None) for r in rows]
        nxt = str(offset + limit) if offset + limit < total else None
        return shape.envelope(items, request_id=rid(request),
                              page={"limit": limit, "cursor": str(offset), "nextCursor": nxt,
                                    "total": total})

    @router.get("/publishers/{publisher_id}", summary="One publisher: curated facts + counted profile")
    def publisher(request: Request, publisher_id: str,
                  p: Principal = Depends(scoped("publishers:read"))) -> dict:
        st = get_store()
        row = st.publisher_by_id(publisher_id)
        if row is None:
            raise PlatformError(404, "not_found", "Unknown publisher id.")
        profile = None
        try:
            profile = publisher_service.get_publisher(st, row["name"], recent_limit=0)
        except Exception:                    # noqa: BLE001 — the curated row stands alone
            profile = None
        return shape.envelope(shape.publisher(row, st.publisher_hosts(publisher_id), profile),
                              request_id=rid(request))

    @router.get("/usage", summary="The tenant's own meter")
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


__all__ = ["MAX_LIMIT", "build_router"]
