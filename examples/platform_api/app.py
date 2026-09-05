"""app.py — mount ``/v1`` into the engine, or run it standalone.

``mount(app, get_store)`` is what ``api_fastapi`` calls when ``RWE_PLATFORM_API=1``; it is
idempotent. ``create_app(store)`` builds a self-contained FastAPI app over one store — the shape
a separate ``platform`` process would run, and what the tests drive.
"""

from __future__ import annotations

import uuid
from typing import Callable, Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from platform_api.auth import PlatformError
from platform_api.routes import DESCRIPTION, TITLE, VERSION, build_router

_MARK = "_platform_api_mounted"


def _handler_for(get_request_id: "Callable[[], str] | None"):
    async def on_platform_error(request: Request, exc: PlatformError):
        rid: Optional[str] = None
        if get_request_id is not None:
            try:
                rid = get_request_id()
            except Exception:                # noqa: BLE001
                rid = None
        rid = rid or request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        body = {"error": {"code": exc.code, "message": str(exc.detail), "requestId": rid}}
        return JSONResponse(status_code=exc.status_code, content=body,
                            headers=dict(exc.headers or {}))
    return on_platform_error


def mount(app: FastAPI, get_store: Callable, *,
          get_request_id: "Callable[[], str] | None" = None) -> bool:
    """Include the router + its error handler once. Returns ``False`` when already mounted."""
    if getattr(app.state, _MARK, False):
        return False
    app.include_router(build_router(get_store, get_request_id=get_request_id))
    app.add_exception_handler(PlatformError, _handler_for(get_request_id))
    # Starlette copies the handler table into its exception middleware when the middleware stack
    # is BUILT (first request). Mounting after that point — tests, a runtime opt-in — would leave
    # PlatformError rendered by the engine's generic handler (`unauthorized` for `unauthenticated`,
    # `http_error` for every 403). Dropping the built stack makes the next request rebuild it with
    # this handler in place; at import time it is already None, so this is a no-op there.
    app.middleware_stack = None
    setattr(app.state, _MARK, True)
    return True


def create_app(store_) -> FastAPI:
    """A standalone platform app over ``store_`` — one uvicorn away from its own process."""
    app = FastAPI(title=TITLE, version=VERSION, description=DESCRIPTION,
                  summary="The commercial front door over the Hidden View news-intelligence engine.",
                  docs_url=None, redoc_url=None, openapi_url=None)   # /v1/docs + /v1/openapi.json
    mount(app, lambda: store_)
    return app


__all__ = ["mount", "create_app"]
