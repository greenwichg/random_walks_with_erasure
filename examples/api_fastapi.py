"""FastAPI serving layer for the Information Health engine — the production re-host.

This wraps the **existing** ``Backend`` serialisers (examples/api_server.py) verbatim behind
FastAPI: same paths, same query params, same JSON as the stdlib server, so the web frontend
and the contract tests are unchanged. FastAPI adds interactive OpenAPI docs (``/docs``) and a
single ``Backend`` built **once at startup** and reused across requests.

The algorithms, serialisers, JSON contract, and dataset-profile system are untouched — this
file only changes *how the responses are served*, not what they contain.

    python examples/api_fastapi.py --port 8000
    python examples/api_fastapi.py --profile mind --npz mind_full.npz
    RWE_PROFILE=mind RWE_NPZ=mind_full.npz python examples/api_fastapi.py   # config-only
    # interactive docs: http://127.0.0.1:8000/docs

Install the serving deps once:  pip install -e ".[serve]"
"""

from __future__ import annotations

import argparse
import contextvars
import dataclasses
import hashlib
import json
import math
import logging
import os
import re
import sys
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Literal, Optional
import urllib.parse
from urllib.parse import urlsplit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # import sibling api_server
import api_server as engine   # Backend, DatasetProfile, resolve_profile, BUILTIN_PROFILES
import store                  # beta persistence layer (users + identities)
import settings_service       # settings schema + normaliser (leaf); the canonical settings accessors
import story_continuation     # the post-read "compare this story" resolver (read-only, flag-gated)
import ingest                 # reading-event scorer + cache (Milestone C)
import enrich                 # headline enrichment (register + emotion) behind ingest.Enricher
import personalize            # per-user augmented Measured report / recs / coach
import evidence_resolver      # ONE human explanation per rec, chosen from evidence (21a.3)
import improvement_ledger     # improvement-recommendation lifecycle state machine (leaf, RC2.3)
import rec_context            # reader-state (feedback/repetition) params, flag-gated (X-audit Tier 1)
import improvement_ranking    # feedback-aware ranking/filtering of improvements (leaf, RC2.4)
import recommendation_eval     # deterministic evaluation + attribution of improvements (leaf, RC2.5)
import obs_metrics             # OBS1: in-process request/latency metrics (dependency-free, observational)
import error_reporting         # OBS1: vendor-agnostic exception-reporting abstraction (default: logging)
import product_analytics       # PA1: event taxonomy + funnel/metric/retention maths (pure, deterministic)
import coach_service          # Coach v2: intent-routed, tool-using coach (RWE_COACH_V2, M4)
import email_delivery          # the email channel's worker + unsubscribe (leaf-ish)
import notification_delivery   # orchestration: build context -> evaluate -> record notifications (N2)
import ratelimit              # dependency-free token-bucket rate limiter (Private Alpha hardening)
import reqlimits              # request-body size / batch-shape limits (Private Alpha hardening)
import feed_source            # optional: source the recommender catalog from the RSS FeedArticle store
import feed_service           # optional: background RSS polling that keeps the FeedArticle catalog fresh
import sources                # pluggable multi-source ingestion (RSS + NewsAPI + GDELT) via adapters
import rss_ingest             # FeedEntry + ingest_entries — the one producer path (Commit 18: + extension)
import corpus                 # the clustering-corpus tier boundary (M1) + M11 admission wiring
import corpus_validation      # corpus-eligibility gate (validation only; no activation / no hot swap)
import corpus_refresh         # atomic hot activation of a validated corpus (background Backend swap)
import discover               # Discover: product-layer exploration over the FeedArticle catalog
import search                 # live full-text + faceted search over the FeedArticle catalog (Commit 6)
import story_service          # the single owner of Story construction (Discover + Stories consume it)
import publisher_service      # Publisher Intelligence: counted catalog + curated registry profile
import story_intelligence     # deterministic intelligence computed ON TOP of Story objects (Commit 10)
import article_analyzer       # anonymous URL analysis (A1 service: catalog-first, fetchless, zero-write)
import analysis_enrichment    # A3: reader-relative explanation + recommendation layered on an analysis
import location               # Location Intelligence — canonical model + publisher scope vocabulary
import outlet_registry        # publisher locality registry (Local News v1 backing data)
import media                  # centralised media + publisher-logo selection (rec enrichment, Commit 9)

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.exceptions import HTTPException as StarletteHTTPException


# ------------------------------------------------------------------ #
# Structured logging — one JSON line per event, with a per-request id so a
# client error (X-Request-ID header / error.requestId) correlates to the log.
# ------------------------------------------------------------------ #
logger = logging.getLogger("ih.api")
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(_handler)
    logger.setLevel(os.environ.get("RWE_LOG_LEVEL", "INFO").upper())
    logger.propagate = False

_request_id: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


def _log(level: int, event: str, **fields) -> None:
    logger.log(level, json.dumps({"event": event, "requestId": _request_id.get(), **fields}))


def _obs_ms(name: str, t0: float) -> None:
    """Record ``now - t0`` (ms) under ``name``. Observational only, and guarded: a metrics failure
    must never surface as a failed request, so the caller's work is already done by the time we
    reach here and nothing below can change what it returns."""
    try:
        obs_metrics.observe(name, (time.perf_counter() - t0) * 1000.0)
    except Exception:
        pass


def _install_db_timing(st) -> None:
    """OBS1 — record each SQL statement's latency into ``obs_metrics`` via SQLAlchemy cursor events.
    Purely observational: the listeners only read the clock, never touch the statement, and are guarded
    so a metrics failure can never affect a query. Keeps ``store`` free of any observability dependency."""
    from sqlalchemy import event as _sa_event

    def _before(conn, cursor, statement, params, context, executemany):
        context._ih_t0 = time.perf_counter()

    def _after(conn, cursor, statement, params, context, executemany):
        t0 = getattr(context, "_ih_t0", None)
        if t0 is not None:
            obs_metrics.observe("db_query_ms", (time.perf_counter() - t0) * 1000.0)

    try:
        engine = getattr(st, "engine", None)
        if engine is not None and not getattr(engine, "_ih_db_timing", False):
            _sa_event.listen(engine, "before_cursor_execute", _before)
            _sa_event.listen(engine, "after_cursor_execute", _after)
            engine._ih_db_timing = True
    except Exception:               # observability must never block startup
        pass


def _int_env(name: str):
    v = os.environ.get(name)
    return int(v) if v and v.lstrip("-").isdigit() else None


def _production() -> bool:
    """Whether the engine is running in production mode (``RWE_ENV=production`` / ``prod``).

    The single cross-tier switch that turns on fail-closed authentication (the web tier reads the
    same variable) and the strict CORS default. Unset — local dev, the Colab demo, tests — keeps the
    zero-config behaviour that makes the app runnable with one command. Defined early because the
    CORS middleware (configured at import) consults it."""
    return os.environ.get("RWE_ENV", "").strip().lower() in {"production", "prod"}


# --- dev-only single reading identity -----------------------------------------
# In dev / Colab the browser extension and the web "demo reader" must resolve to the SAME engine
# user (so extension reads land where Reading History looks), and that must survive the container's
# ephemeral database being recreated. A fixed dev token — the default off production, overridable via
# RWE_DEV_TOKEN — always resolves to the demo reader below (the identity the web dev-login upserts),
# so the two never diverge and the token never goes stale on a restart. OFF in production unless
# RWE_DEV_TOKEN is explicitly set.
_DEV_DEMO_PROVIDER = "dev"
_DEV_DEMO_ACCOUNT = "demo@infodiet.local"
_DEFAULT_DEV_TOKEN = "infodiet-dev-demo-token"


def _dev_token() -> "str | None":
    """The fixed demo token that binds the extension to the web demo reader (dev only), or None."""
    explicit = (os.environ.get("RWE_DEV_TOKEN") or "").strip()
    if explicit:
        return explicit
    return None if _production() else _DEFAULT_DEV_TOKEN


def _ensure_demo_user() -> int:
    """The stable demo-reader engine uid (created on demand), matching the web dev-login identity —
    so the fixed dev token and the web session always name the same user, even after a DB reset."""
    u = _require_store().upsert_user_by_identity(
        _DEV_DEMO_PROVIDER, _DEV_DEMO_ACCOUNT, email=_DEV_DEMO_ACCOUNT, display_name="Demo Reader")
    return u.id


def _profile_for(feed_csv: "str | None" = None) -> "engine.DatasetProfile":
    """The dataset profile this process will serve.

    Deployment configuration comes from the environment (``RWE_PROFILE``, ``RWE_NPZ``, …), resolved
    by the same ``resolve_profile`` the CLI uses so the two behave identically.

    ``feed_csv`` is the live recommendation source's answer, when it produced one: the feed catalog
    IS the corpus, so it must win over a pre-set ``RWE_PROFILE`` (the docker/compose default is
    ``synthetic``) or the engine would build the pre-set corpus and silently ignore the feed — and no
    publisher URL would ever reach a recommendation. It wins by being passed HERE, on the argument
    that ``resolve_profile`` already ranks above the environment. It used to win by being written
    back into ``os.environ``, which reached the same build and also reconfigured every later reader
    in the process — invisible in production, where the app boots once, and a steady source of
    cross-test contamination in a suite that boots it dozens of times.
    """
    ns = SimpleNamespace(
        profile="qbias" if feed_csv else None, npz=None, qbias=feed_csv,
        register_csv=None, emotion_csv=None,
        behaviors=None, lean_tau=None, domain=None,
        n_users=_int_env("RWE_N_USERS"), max_items=_int_env("RWE_MAX_ITEMS"),
        seed=_int_env("RWE_SEED"),
    )
    return engine.resolve_profile(ns)


class _State:
    # `active` is the single atomically-swapped serving bundle (backend + personalizer + generation).
    # `backend` / `personalizer` are read-only views onto it, so existing accessors keep working while
    # a hot refresh replaces the pair together (never one without the other).
    active: "corpus_refresh.Active | None" = None
    store: "store.Store | None" = None
    scorer: "ingest.Scorer | None" = None
    limiter: "ratelimit.RateLimiter | None" = None
    poller: "feed_service.FeedPoller | None" = None
    refresh: "corpus_refresh.RefreshManager | None" = None
    event_judge: "object | None" = None   # event_identity.EventJudge (lazy import)
    # The designated read-only exhibit account (RWE_DEMO_ACCOUNT, "provider:accountId").
    # None = feature off; everything behaves exactly as before the demo account existed.
    demo_uid: "int | None" = None

    @property
    def backend(self) -> "engine.Backend | None":
        return self.active.backend if self.active is not None else None

    @property
    def personalizer(self) -> "personalize.Personalizer | None":
        return self.active.personalizer if self.active is not None else None


state = _State()


def _configure_recs_source(st) -> "str | None":
    """Opt-in live recommendation source. When ``RWE_RECS_SOURCE=feed`` and the RSS ``FeedArticle``
    catalog is large enough, export it to a qbias-format CSV and return that path; :func:`_profile_for`
    turns it into the corpus. Returns ``None`` — keep the existing corpus — when the source is
    disabled or the catalog is below ``RWE_FEED_MIN_ARTICLES``, so enabling the flag before any RSS
    ingest stays safe. No recommendation algorithm is affected; this only selects the article source.

    It answers a question and changes nothing. The CSV path used to be applied here, by writing
    ``RWE_QBIAS`` and ``RWE_PROFILE`` into the process environment; deciding and applying are now
    separate, so a caller can ask what the source would be without reconfiguring the process."""
    if not feed_source.enabled():
        return None
    feed_csv = feed_source.prepare(st)
    if not feed_csv:
        # Two gates can produce this, and `articles` alone distinguishes them: a count below the
        # threshold is a catalog that is too small, while a healthy count means the rows exist but
        # were filtered out of candidacy — almost always the RWE_FEED_MAX_AGE_DAYS window over a
        # catalog that has stopped being refreshed.
        _log(logging.WARNING, "recs_source_fallback", source="feed",
             reason="too few recommendation candidates: below RWE_FEED_MIN_ARTICLES, "
                    "or filtered out of candidacy (RWE_FEED_MAX_AGE_DAYS)",
             articles=st.count_feed_articles())
        return None
    _log(logging.INFO, "recs_source", source="feed", csv=feed_csv, profile="qbias",
         articles=st.count_feed_articles())
    return feed_csv


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Fail fast on a fatal misconfiguration (production mode without the internal secret) BEFORE
    # building anything — a mis-configured prod deploy must refuse to start, never come up fail-open.
    errors = _config_errors()
    if errors:
        for err in errors:
            _log(logging.CRITICAL, "config_error", error=err)
        raise RuntimeError("Refusing to start: " + " ".join(errors))
    # Build the engine (dataset + compute + recommender inputs) once, reuse per request.
    provider = os.environ.get("RWE_PROVIDER", "anthropic")
    st = store.Store()
    state.store = st
    _install_db_timing(st)          # OBS1: observational per-query latency (never alters queries)
    state.scorer = ingest.Scorer(enricher=enrich.make_enricher())   # baseline register+emotion
    state.limiter = ratelimit.RateLimiter()          # per-process token-bucket limiter
    # The read-only exhibit account (Option E): when RWE_DEMO_ACCOUNT=provider:accountId is set,
    # anonymous / below-threshold requests are served this account's MEASURED report once it is
    # seeded past the read threshold (see _serve/_report_for); its writes are locked at the
    # middleware. Upsert is idempotent — pre-seeding, the account exists empty and everything
    # falls back to the synthetic demo reader exactly as before.
    demo_account = os.environ.get("RWE_DEMO_ACCOUNT", "").strip()
    # Unconditional reset: a previous lifespan's exhibit uid must never leak into a process where
    # the flag is now unset — a stale demo_uid would write-lock (and exhibit-mark) whichever
    # ordinary user happens to hold that id in the new DB.
    state.demo_uid = None
    if demo_account and ":" in demo_account:
        provider_name, account_id = demo_account.split(":", 1)
        state.demo_uid = st.upsert_user_by_identity(provider_name, account_id,
                                                    display_name="Demo Reader").id
        _log(logging.INFO, "demo_account", uid=state.demo_uid, identity=demo_account)
    # Live recommendation source (opt-in): build the recommender's catalog from the RSS FeedArticle
    # store instead of the static qbias CSV / synthetic generator. Additive — the FeedArticle-derived
    # CSV is handed to the profile as the qbias corpus, so the ENGINE and the protected simulator are
    # unchanged and the recommender operates over live articles exactly as over qbias. Falls back
    # (keeps the existing corpus) when the catalog is too small.
    feed_csv = _configure_recs_source(st)
    be = engine.Backend(_profile_for(feed_csv), provider=provider)
    if feed_csv:
        # Map the corpus item ids (Q{i}) back to their FeedArticle publisher URLs, so recommendations
        # carry the real openable URL (the Honest URL Pass-through). Additive; no algorithm change.
        be.attach_url_resolver(feed_source.load_url_map(feed_csv))
        # Same CSV, same row indexing → the For You country preference's input.
        be.attach_country_resolver(feed_source.load_country_map(feed_csv))
        # Story membership → the per-story feed quota's input (Tier 1). Enrichment like the
        # country map: fail-soft, inert until RWE_REC_MAX_PER_STORY is set.
        try:
            be.attach_story_resolver(*feed_source.load_story_maps(st, feed_csv))
        except Exception as exc:                     # noqa: BLE001 — enrichment, never fatal
            _log(logging.WARNING, "story_map_unavailable", error=repr(exc))
    # The personalization layer: builds a real user's Measured report / recs / coach from an
    # augmented corpus once they've stored enough reads (cached per user + reading version).
    personalizer = personalize.Personalizer(be, st)
    # Seed the atomic serving bundle (generation 1) + the refresh manager. The boot Backend is built
    # exactly as before; the manager only ever REPLACES it later via a validated, atomic hot swap
    # (backend + personalizer together), so the running engine picks up new articles without a restart.
    state.refresh = corpus_refresh.RefreshManager(state, provider=provider, log=_log)
    source = "feed" if be.url_by_id else "static"
    sig = corpus_refresh.initial_signature(st) if feed_csv else "static"
    state.refresh.seed(be, personalizer, source, sig, len(be.mind.dataset.item_ids))
    _log(logging.INFO, "startup", profile=be.profile.name, demoUser=be.demo_user,
         eligibleReaders=int(len(be.eligible)), db=st.url,
         rateLimit=ratelimit.enabled(), production=_production())
    # Automatic multi-source polling + hot refresh (opt-in): keep the FeedArticle catalog fresh in the
    # background from every enabled source (RSS + NewsAPI + GDELT via the SourceRegistry), and — via the
    # poller's on_cycle seam — atomically activate a newly validated corpus so new articles become
    # recommendable with NO restart. Requires the live feed source (RWE_RECS_SOURCE=feed) and at least
    # one enabled adapter (RSS defaults to the existing RWE_FEED_POLL). Each adapter polls on its own
    # interval, isolated; retention/health/validation/hot-refresh are unchanged and owned by earlier
    # commits — this only consumes their outputs. FeedPoller is untouched (standalone CLI still uses it).
    # M11: the admission table is a second source of shadow tier assignments and of crawl configs.
    # Wired here, explicitly and once, rather than from `Store.__init__` — see
    # `corpus.wire_admissions`. It must come BEFORE the registry is built: `crawler.admitted_configs`
    # filters its rows through `corpus.is_shadow`, so an unwired corpus would report every admitted
    # host as Tier A and the crawl set would come back empty.
    # BOTH admitted tiers, or the one that is not wired serves as Tier A while the table says
    # otherwise — and `corpus.enabled()` reads both, so wiring only shadow on a Tier-B-only
    # deployment would short-circuit the tier filter entirely rather than fail loudly.
    corpus.wire_admissions(st.admitted_shadow_hosts)
    corpus.wire_tier_b_admissions(st.admitted_tier_b_hosts)
    registry = sources.default_registry(store_=st)
    for _w in sources.config_warnings(registry):     # e.g. RWE_NEWSAPI_ENABLED set but no API key
        _log(logging.WARNING, "source_misconfigured", detail=_w)
    if registry.enabled() and not feed_source.enabled():
        _log(logging.WARNING, "source_poller_inactive",
             detail="ingestion adapters are enabled but RWE_RECS_SOURCE != feed, so the poller will not start",
             adapters=[a.provider for a in registry.enabled()])
    if feed_source.enabled() and registry.enabled():
        state.refresh.polling_enabled = True
        state.poller = sources.MultiSourcePoller(state.store, state.scorer, registry=registry,
                                                 log=_log, on_cycle=state.refresh.on_poll_cycle,
                                                 dirty_check=state.refresh.is_catalog_dirty)
        state.poller.start()

    # Banded event-identity judge (event_identity): the out-of-band worker that drains the story
    # build's ambiguity-band queue through the Claude adapter and persists verdicts. Flag-gated
    # (RWE_EVENT_JUDGE, default off) and key-gated (ANTHROPIC_API_KEY — the coach narrative's
    # existing convention); without either, .start() is a no-op and clustering is byte-identical
    # to production. The build itself never waits on this thread.
    import event_identity
    state.event_judge = event_identity.EventJudge(st, log=_log)
    state.event_judge.start()

    # Push delivery (Phase B4). Registers the metric series so a counter that has never fired is a
    # visible zero rather than an absent one, and reports what the PREVIOUS process left behind — a
    # restart mid-fan-out leaves claimed-but-unresolved rows that the lease recovers silently fifteen
    # minutes later, which is exactly the kind of self-healing nobody notices until they need to
    # explain why notifications were late.
    try:
        import push_delivery
        push_delivery.startup(state.store, log=_log)
    except Exception as e:           # startup reporting must never keep the app from coming up
        _log(logging.WARNING, "push_startup_failed", error=f"{type(e).__name__}: {e}")

    yield

    # Push first: a fan-out in flight holds delivery claims, and stopping it before the poller means
    # the poller cannot start another one behind our back while we are waiting.
    try:
        import push_delivery
        push_delivery.shutdown(log=_log)
    except Exception:                # shutdown must never raise out of the lifespan
        pass
    if state.poller is not None:
        state.poller.stop()          # graceful: signal + join the current cycle
    if state.event_judge is not None:
        try:
            state.event_judge.stop()  # daemon thread; join is bounded
        except Exception:            # shutdown must never raise out of the lifespan
            pass
    # The coalescing story warmer outlives any single poll cycle by design, so it is stopped here
    # rather than by the poller. Ordered AFTER poller.stop() so a cycle finishing during shutdown
    # cannot queue a warm against a warmer that has already gone.
    try:
        story_service.shutdown_warmer()
    except Exception:                # shutdown must never raise out of the lifespan
        pass
    # And the build subprocess (P0-2′), after the warmer for the same reason: the warmer is the
    # main thing that submits to it, and stopping the pool first would turn the warmer's last
    # build into a broken-pool fallback on the GIL during shutdown.
    try:
        story_service.shutdown_build_pool()
    except Exception:                # shutdown must never raise out of the lifespan
        pass
    state.poller = None
    state.active = None
    state.refresh = None
    state.store = None
    state.scorer = None
    state.limiter = None


app = FastAPI(
    title="Information Health API",
    version="1.0.0",
    summary="Real Information Health engine — report, recommendations, and AI coach.",
    description=(
        "JSON over the deterministic Information Health Report (`health_report`), the RWE "
        "recommender family, and the grounded AI coach (`narrate_report`). Responses match "
        "the frontend domain contract (`web/types/domain.ts`). Every error uses one typed "
        "envelope: `{ \"error\": { \"code\", \"message\", \"requestId\" } }`."
    ),
    openapi_tags=[
        {"name": "report", "description": "The flagship Information Health Report for a reader."},
        {"name": "recommendations", "description": "RWE-B / RWE-D / Adaptive recommendations."},
        {"name": "coach", "description": "Grounded AI coach — greeting and replies."},
        {"name": "meta", "description": "Service health and readiness."},
    ],
    lifespan=lifespan,
)
def _cors_origins() -> "list[str]":
    """Browser origins allowed to call the engine cross-origin.

    ``RWE_CORS_ORIGINS`` (comma-separated) wins. Otherwise: permissive (``*``) in development, where
    the engine is often hit directly from a browser / the docs; **locked** (none) in production,
    where the engine is internal and the web tier calls it server-to-server (not subject to CORS).
    Read at import (when the middleware is configured), like a deployment setting."""
    raw = os.environ.get("RWE_CORS_ORIGINS")
    if raw is not None and raw.strip() != "":
        return [o.strip() for o in raw.split(",") if o.strip()]
    return [] if _production() else ["*"]


app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)


@app.middleware("http")
async def _observability(request: Request, call_next):
    """Tag each request with an id, apply rate limiting, time it, and emit one structured log line.

    The rate-limit check runs here (after the request id is set, before the handler) so a throttled
    request is denied without doing any work, yet is still logged and carries ``X-Request-ID`` and
    ``error.requestId`` exactly like every other response."""
    rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
    _request_id.set(rid)
    start = time.perf_counter()
    status = 500
    try:
        # Reject a too-large body (413) before buffering it; then apply the rate limiter (429). The
        # first non-None short-circuits the handler; otherwise run it. Headers are set uniformly below.
        resp = _body_limit_check(request) or _rate_limit_check(request) or _demo_write_check(request)
        if resp is None:
            resp = await call_next(request)
        status = resp.status_code
        resp.headers["X-Request-ID"] = rid
        # Response hardening for a JSON API: never sniff types, never leak the URL as a referrer, and
        # never cache per-user data. (CSP/frame headers live on the browser-facing web tier.)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path.startswith("/api/"):
            resp.headers.setdefault("Cache-Control", "no-store")
        return resp
    finally:
        duration_ms = round((time.perf_counter() - start) * 1000, 1)
        _log(logging.INFO if status < 500 else logging.ERROR, "request",
             method=request.method, path=request.url.path, status=status, durationMs=duration_ms)
        # OBS1: aggregate by the matched route TEMPLATE (not the raw path) so metric cardinality stays
        # bounded; unmatched requests (404) collapse to one series.
        route = request.scope.get("route")
        template = getattr(route, "path", None) or "unmatched"
        obs_metrics.record_request(request.method, template, status, duration_ms)


# ------------------------------------------------------------------ #
# One typed error envelope for every failure: {"error": {"code", "message"}}
# (matches the web proxy's shape in web/lib/backend.ts). Success bodies are
# unchanged, so the frontend and contract are unaffected.
# ------------------------------------------------------------------ #
class ErrorBody(BaseModel):
    code: str
    message: str
    requestId: str | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody


# Shared OpenAPI documentation of the typed error envelope (documents, does not enforce).
_ERR_RESPONSES: dict = {"default": {"model": ErrorResponse, "description": "Typed error envelope."}}


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message, "requestId": _request_id.get()}},
    )


_HTTP_CODES = {400: "bad_request", 401: "unauthorized", 404: "not_found",
               405: "method_not_allowed", 413: "payload_too_large", 422: "invalid_request",
               503: "engine_unavailable"}


@app.exception_handler(RequestValidationError)
async def _on_validation_error(request: Request, exc: RequestValidationError):
    # Push registrations are logged on rejection, and only push: a browser producing subscriptions the
    # engine will not accept is invisible from both ends otherwise — the reader sees "could not
    # enable" and the operator sees a bare 422 count. Field NAMES only; a validation error's context
    # echoes the submitted value, which for this route is an endpoint or a device key.
    if request.url.path.startswith("/api/me/push/"):
        fields = sorted({str(e.get("loc", ["?"])[-1]) for e in exc.errors()})
        _log(logging.WARNING, "push_subscription_rejected",
             path=request.url.path, fields=fields, errors=len(exc.errors()))
    return _error(422, "invalid_request", "One or more request parameters are invalid.")


@app.exception_handler(StarletteHTTPException)
async def _on_http_error(request: Request, exc: StarletteHTTPException):
    code = _HTTP_CODES.get(exc.status_code, "http_error")
    return _error(exc.status_code, code, str(exc.detail))


@app.exception_handler(Exception)
async def _on_unhandled_error(request: Request, exc: Exception):
    # Log the failure (type + path) for correlation; never leak internals to the client.
    _log(logging.ERROR, "unhandled_exception", path=request.url.path, error=type(exc).__name__)
    # OBS1: full capture through the vendor-agnostic reporter (traceback + correlation context). A later
    # deployment swaps the reporter for Sentry / App Insights / OTel without touching this handler.
    error_reporting.report_exception(exc, path=request.url.path, method=request.method,
                                     requestId=_request_id.get(), status=500)
    return _error(500, "internal_error", "An unexpected error occurred.")


# ------------------------------------------------------------------ #
# Response schemas for OpenAPI. These mirror the existing serialiser output
# (web/types/domain.ts) exactly; combined with response_model_exclude_none they
# document the contract without changing any response (the serialisers omit
# rather than null, and a strict HTTP-vs-serialiser equality test guards this).
# ------------------------------------------------------------------ #
class EmotionShareModel(BaseModel):
    fear: float
    outrage: float
    analysis: float
    positive: float
    neutral: float


class RawModel(BaseModel):
    value: float
    unit: str


class MeasurementCoverageModel(BaseModel):
    """The *scope* of a metric (ADR-001): of the reads eligible for this dimension, how many carried
    its signal. ``observed <= eligible``; ``basis`` names the eligibility population."""
    observed: int          # eligible reads that carried this dimension's signal
    eligible: int          # the honest denominator — reads the metric is about
    basis: str             # the eligibility population (e.g. "political_reads", "all_reads")


class MeasurementProvenanceModel(BaseModel):
    """Where a metric's value comes from (ADR-001). ``kind``: ``authoritative`` (looked up from a
    source of truth) | ``derived`` (inferred by a model). ``source`` names that source of truth / model."""
    kind: str              # "authoritative" | "derived"
    source: str            # e.g. "outlet_registry", "baseline_lexical"


class MeasurementModel(BaseModel):
    """Generic per-metric **Measurement metadata** (ADR-001): coverage (scope) + provenance, and an
    optional confidence (certainty). Coverage != confidence — coverage says how many reads carried the
    signal at all; confidence says how sure we are given the reads that did. ``confidence`` is omitted
    unless a value genuinely represents prediction uncertainty (the current Emotion outputs do not, so
    it is absent rather than a heuristic). Additive and optional — present only on the metrics that
    carry a measurement (Viewpoint / Emotion) of a Measured report; older clients ignore it."""
    dimension: str
    coverage: MeasurementCoverageModel
    provenance: MeasurementProvenanceModel
    confidence: Optional[float] = None


class MetricModel(BaseModel):
    key: str
    score: int
    delta: int
    band: str
    benchmark: Optional[int] = None
    raw: Optional[RawModel] = None
    # Availability (Metric Empty State): explicit backend signal for whether this metric could be
    # computed from the reader's activity. When False the UI shows a "not enough data yet" empty state
    # inside the card rather than hiding it or implying a real 0. Defaults keep older payloads valid.
    available: bool = True
    reason: Optional[str] = None          # e.g. "insufficient_data" (only set when not available)
    minimumActivity: Optional[int] = None # reads that typically unlock the metric (informational)
    # Measurement metadata (ADR-001): coverage + provenance for this metric, computed from the reader's
    # scored reads by the engine (measurement.py). Present only on the dimensions that carry one
    # (Viewpoint / Emotion) of a Measured report; additive — older clients ignore it. The coverage of an
    # *unavailable* metric is meaningful (it explains the empty state), so it can appear with available=False.
    measurement: Optional[MeasurementModel] = None


class ViewpointModel(BaseModel):
    left: float
    center: float
    right: float


class TopicSliceModel(BaseModel):
    topic: str
    share: float
    count: int


class SourceSliceModel(BaseModel):
    source: str
    share: float
    count: int
    lean: float


class BlindSpotModel(BaseModel):
    topic: str
    gap: float
    note: str


class EvidenceBasisModel(BaseModel):
    """One report field that fed an improvement's evidence — the traceability record (RC2.1).
    ``value`` is the exact number used (a 0–1 share, a metric score, or a count)."""
    field: str
    label: str
    value: float


class ToScoreModel(BaseModel):
    low: int
    high: int


class ImpactEstimateModel(BaseModel):
    """RC2.2 — a deterministic, evidence-based estimated impact **range** (percentile points) that
    replaces the old fixed ``+5``. ``method`` is ``simulated`` (the reader's own distribution perturbed
    by the suggested action and re-percentiled) or ``deficit`` (a coarse guide from the score's distance
    below the typical reader — graph metrics and estimate reports). ``fromScore``/``toScore`` show the
    percentile it would move from → to, and ``explanation`` states how it was derived."""
    low: int
    high: int
    method: str                 # "simulated" | "deficit"
    metric: str
    confidence: str             # "high" | "medium" | "low"
    fromScore: int
    toScore: ToScoreModel
    explanation: str


class ImprovementLifecycleStateModel(BaseModel):
    """RC2.3 — the signed-in reader's lifecycle state for one improvement recommendation. Every field
    but ``recKey``/``state`` is optional (a freshly-shown rec has no accepted/completed timestamps)."""
    recKey: str
    state: str                  # one of improvement_ledger.LIFECYCLE_STATES
    firstScore: Optional[int] = None
    currentScore: Optional[int] = None
    completedScore: Optional[int] = None
    generatedAt: Optional[str] = None
    shownAt: Optional[str] = None
    viewedAt: Optional[str] = None
    acceptedAt: Optional[str] = None
    dismissedAt: Optional[str] = None
    completedAt: Optional[str] = None
    expiredAt: Optional[str] = None
    supersededAt: Optional[str] = None
    supersededBy: Optional[str] = None


class RankingSignalModel(BaseModel):
    signal: str
    effect: str


class ImprovementRankingModel(BaseModel):
    """RC2.4 — why a recommendation was ordered / suppressed. ``visible`` False means the ranker filtered
    it out (``reason`` = completed | dismissed | overlaps:<family>); ``signals`` lists every applied
    factor, so nothing about the ranking is hidden."""
    rank: Optional[int] = None      # 1-based position among visible recs; None when suppressed
    visible: bool
    priority: float
    reason: Optional[str] = None    # suppression reason when not visible
    signals: list[RankingSignalModel] = []


class ImprovementModel(BaseModel):
    id: str
    title: str
    detail: str
    metric: str
    impact: int
    # RC2.1 — optional, user-specific evidence bound from fields already present in this report.
    # Additive: absent on older payloads, and omitted (response_model_exclude_none) whenever the
    # report lacked the grounded data to be specific — so the static title/detail still stand alone.
    trigger: Optional[str] = None
    evidence: Optional[str] = None
    suggestedAction: Optional[str] = None
    expectedBenefit: Optional[str] = None
    evidenceBasis: Optional[list[EvidenceBasisModel]] = None
    # RC2.2 — optional dynamic impact estimate; ``impact`` (above) stays as the band midpoint for
    # backward compatibility with consumers that read a single scalar.
    impactEstimate: Optional[ImpactEstimateModel] = None
    # RC2.3 — optional lifecycle state for the signed-in reader (absent for anonymous/demo reports and
    # older payloads). Additive; drives no selection/ordering.
    lifecycle: Optional[ImprovementLifecycleStateModel] = None
    # RC2.4 — optional feedback-aware ranking/suppression for the signed-in reader. Additive; a
    # consumer that ignores it sees every generated rec (as before), one that honours it renders only
    # `visible` recs in `rank` order.
    ranking: Optional[ImprovementRankingModel] = None


class CoverageModel(BaseModel):
    reads: int
    threshold: int
    sufficient: bool


class HealthReportModel(BaseModel):
    overall: int
    overallDelta: int
    band: str
    updatedAt: str
    metrics: list[MetricModel]
    viewpoint: ViewpointModel
    attention: EmotionShareModel
    topics: list[TopicSliceModel]
    sources: list[SourceSliceModel]
    blindSpots: list[BlindSpotModel]
    improvements: list[ImprovementModel]
    # article-level axis confidence — present on a measured report, omitted on an estimate
    axisConfidence: Optional[float] = None
    # mode + coverage make Estimate vs Measured explicit; an estimate omits axisConfidence
    mode: Optional[str] = None
    coverage: Optional[CoverageModel] = None
    # True when this report belongs to the EXHIBIT account and is being shown to someone else.
    #
    # `_report_for` falls back to the exhibit's report for a signed-in reader with no reads and no
    # onboarding. That report is genuinely `mode="measured"` with `coverage.reads=30` — because it
    # really is the exhibit's measurement — and nothing in the payload said whose it was. A brand new
    # beta tester therefore opened their Health Report and saw "Measured · based on 30 reads" over a
    # political distribution they had never produced: the product asserting a measurement about
    # somebody who had read nothing.
    #
    # Additive and optional, so `response_model_exclude_none=True` omits it entirely on a real
    # reader's report and no existing consumer or contract test sees a changed payload.
    sample: Optional[bool] = None
    # Note (ADR-001): per-metric *dimensional* coverage now lives on each metric's `measurement`
    # (MetricModel.measurement) — Viewpoint's coverage moved there and Emotion gained one — rather
    # than a single report-level `viewpointCoverage`. The `coverage` above stays volume-only
    # (reads-vs-threshold) and `axisConfidence` stays certainty (not scope).


class TrendPointModel(BaseModel):
    # a point on the health trend; extra per-metric scores are allowed (TrendPoint is open-ended).
    model_config = ConfigDict(extra="allow")
    date: str
    overall: int


class DashboardTodayModel(BaseModel):
    articlesRead: int
    avgReadingMinutes: int
    minutesRead: int                    # today's total estimated reading minutes
    politicalShare: float
    topTopics: list[str]
    # today-vs-goal progress from the reader's stored daily reading goal; omitted (None) for
    # anonymous/demo requests, which have no settings. Minutes are per-read estimates.
    goalMinutes: int | None = None
    goalMet: bool | None = None


class DashboardModel(BaseModel):
    overall: int
    overallDelta: int
    trend: list[TrendPointModel]
    today: DashboardTodayModel
    metrics: list[MetricModel]          # the report's metrics, reused verbatim (not re-derived)
    streakDays: int
    # Estimate vs Measured + coverage, lifted from the reader's report so the dashboard keeps the
    # onboarding context (progress toward the measured threshold). Omitted (None) only if a report
    # somehow lacks them; present for every real routing path.
    mode: Optional[str] = None
    coverage: Optional[CoverageModel] = None
    # Same marker as HealthReportModel.sample — the dashboard shows the same Measured chip, so it
    # would make the same false claim without it.
    sample: Optional[bool] = None


class EmotionPointModel(BaseModel):
    date: str
    fear: float
    outrage: float
    analysis: float
    positive: float
    neutral: float


class ReportingPointModel(BaseModel):
    date: str
    reporting: float
    opinion: float


class RecAcceptancePointModel(BaseModel):
    date: str
    accepted: int
    ignored: int


class AnalyticsModel(BaseModel):
    # Reading coverage toward the measured threshold, so Analytics carries the same Estimate-vs-Measured
    # context as the dashboard/report (trends grow as the reader builds their profile).
    coverage: Optional[CoverageModel] = None
    readingOverTime: list[TrendPointModel]
    topicDiversity: list[TrendPointModel]
    politicalDiversity: list[TrendPointModel]
    publisherDiversity: list[TrendPointModel]
    emotion: list[EmotionPointModel]
    reporting: list[ReportingPointModel]
    recommendationAcceptance: list[RecAcceptancePointModel]
    healthImprovement: list[TrendPointModel]


class AchievementModel(BaseModel):
    id: str
    title: str
    description: str
    icon: str
    unlocked: bool
    unlockedAt: str | None = None
    progress: float | None = None


class ProfileModel(BaseModel):
    name: str
    handle: str
    email: str
    avatarUrl: str | None = None
    joinedAt: str
    streakDays: int
    longestStreak: int
    scoreHistory: list[TrendPointModel]
    achievements: list[AchievementModel]     # empty until the feature exists (honest, not faked)
    savedCount: int                          # the single "Saved" counter (no separate bookmark)


class NotificationCategoryModel(BaseModel):
    """One category's per-channel switches. ``push`` is part of the contract from the start and is
    read by nothing yet — declaring it now is what lets a client store the shape before there is a
    push channel to honour it, so adding one later needs no settings migration."""
    inApp: bool
    push: bool


class NotificationDigestCategoryModel(NotificationCategoryModel):
    """Digests carry a third channel: email (``examples/email_delivery.py``).

    A subclass rather than a nullable ``email`` on every category, because the schema should say
    which categories actually have an email channel. Only digests can be mailed; declaring the leaf
    on ``breaking`` would advertise a switch that turns nothing on.

    **This declaration is load-bearing, not documentation.** ``response_model`` filters output to
    declared fields, so an undeclared leaf is stripped from every response *and* from every patch —
    the setting becomes neither readable nor settable, and the API answers 200 while discarding it.
    That is exactly what happened when the email channel first shipped: the toggle in Settings could
    not be turned on, because this class did not know the field existed."""
    email: bool


class NotificationCategoriesModel(BaseModel):
    """Notification preferences by CATEGORY (what it is about) x CHANNEL (how it arrives).

    Declared explicitly rather than as a free ``dict`` so the contract is visible in the OpenAPI
    schema and an unknown category cannot enter through the API — the same reason every other group
    here is a model. ``settings_service`` drops unknown keys independently; this is the outer gate."""
    breaking: NotificationCategoryModel
    digests: NotificationDigestCategoryModel
    recommendations: NotificationCategoryModel
    product: NotificationCategoryModel


class NotificationPrefsModel(BaseModel):
    recommendations: bool
    weeklyDigest: bool
    streakReminders: bool
    blindSpotAlerts: bool
    # The four booleans above are per-KIND toggles and stay authoritative for the kinds that already
    # ship; `categories` is the composable shape new kinds gate on. Both are live — see
    # `settings_service.DEFAULT_SETTINGS`. Without this field FastAPI's response_model would strip
    # the group from every response, leaving a preference the reader could never see or change.
    categories: NotificationCategoriesModel


# NOTE: a `privacy` group (shareAnonymizedMetrics / personalizedAds) was removed from the settings
# contract in S1.2 — neither field was consumed by any behavior. Legacy stored blobs and legacy
# PATCH payloads carrying those keys normalize away safely (dropped like any unknown key), so old
# data and old clients keep working without a migration.
class FollowedLocationModel(BaseModel):
    """One followed place (Location Intelligence Phase 1). Extends additively for future levels."""
    placeId: str
    level: str        # country | region | city


class InterestPrefsModel(BaseModel):
    """Interest Intensity — the eight per-interest sliders (1–10; 5 = neutral). Declared
    explicitly, like the notification matrix, so the contract is visible in the OpenAPI schema
    and an unknown interest cannot enter through the API; the keys are
    ``settings_service.INTEREST_KEYS`` and the slider→catalog-topic mapping is
    ``api_server._INTEREST_TOPICS``."""
    business: int
    technology: int
    science: int
    health: int
    climate: int
    sports: int
    entertainment: int
    artsCulture: int


class SettingsModel(BaseModel):
    theme: str
    language: str
    politicalOpenness: int
    recommendationStrength: int
    interests: InterestPrefsModel
    readingGoalMinutes: int
    weeklyReport: bool
    monthlyReport: bool
    notifications: NotificationPrefsModel
    # Location Intelligence Phase 1 — prepared contract, no UI yet.
    edition: str | None = None
    locations: list[FollowedLocationModel] = []
    # For You country preference (ISO alpha-2, null = Global). Independent of ``edition``: this
    # one prioritizes recommendations, that one scopes Local Pulse.
    recommendationCountry: str | None = None
    timeZone: str | None = None


# Update model — every field optional so any client can PATCH a subset; the engine merges it over
# the stored preferences and returns the full, normalised SettingsModel.
class NotificationCategoryUpdate(BaseModel):
    """A partial patch of one category — either channel alone, both, or neither."""
    inApp: bool | None = None
    push: bool | None = None


class NotificationDigestCategoryUpdate(NotificationCategoryUpdate):
    """Digests only — the inbound half of :class:`NotificationDigestCategoryModel`. Without it a
    patch of ``{"digests": {"email": true}}`` is accepted with 200 and thrown away."""
    email: bool | None = None


class NotificationCategoriesUpdate(BaseModel):
    breaking: NotificationCategoryUpdate | None = None
    digests: NotificationDigestCategoryUpdate | None = None
    recommendations: NotificationCategoryUpdate | None = None
    product: NotificationCategoryUpdate | None = None


class NotificationPrefsUpdate(BaseModel):
    recommendations: bool | None = None
    weeklyDigest: bool | None = None
    streakReminders: bool | None = None
    blindSpotAlerts: bool | None = None
    # `exclude_none=True` on the dump is recursive, so a patch of a single channel arrives at
    # `normalize_settings` as exactly that one leaf and merges without disturbing its siblings.
    categories: NotificationCategoriesUpdate | None = None


class InterestPrefsUpdate(BaseModel):
    """A partial Interest Intensity patch — any subset of the eight sliders; `exclude_none=True`
    on the dump keeps an untouched slider out of the merge entirely (per-leaf, like categories)."""
    business: int | None = None
    technology: int | None = None
    science: int | None = None
    health: int | None = None
    climate: int | None = None
    sports: int | None = None
    entertainment: int | None = None
    artsCulture: int | None = None


# A legacy client that still sends a `privacy` object is not an error: it's an undeclared field,
# ignored by Pydantic's default `extra="ignore"`, so the merge simply never sees it (see S1.2).
class SettingsUpdateModel(BaseModel):
    theme: str | None = None
    language: str | None = None
    politicalOpenness: int | None = None
    recommendationStrength: int | None = None
    interests: InterestPrefsUpdate | None = None
    readingGoalMinutes: int | None = None
    weeklyReport: bool | None = None
    monthlyReport: bool | None = None
    notifications: NotificationPrefsUpdate | None = None
    edition: str | None = None
    locations: list[FollowedLocationModel] | None = None
    recommendationCountry: str | None = None
    timeZone: str | None = None


class NotificationModel(BaseModel):
    """A materialised notification (from ``store.list_notifications``). ``payload`` is the kind's
    structured content; ``titleKey`` is an i18n key (rendering is the client's job)."""
    id: int
    kind: str
    titleKey: str
    payload: dict
    createdAt: str
    seenAt: str | None = None
    gatedBy: str


# --------------------------------------------------------------------------------------------- #
# Browser push (Phase B1) — subscription registration only. Nothing here sends anything; see
# docs/BROWSER_PUSH_ARCHITECTURE.md.
# --------------------------------------------------------------------------------------------- #
class PushConfigModel(BaseModel):
    """What a browser needs before it may subscribe. Served rather than baked into the web bundle so
    the key and the switch are read at call time — turning push off, or rotating the key pair, is a
    restart and not a rebuild."""
    enabled: bool
    #: The VAPID **public** key (base64url, uncompressed P-256 point). Public by construction: it is
    #: handed to `pushManager.subscribe` in the browser. The private half never leaves the engine.
    publicKey: str


class PushSubscriptionModel(BaseModel):
    """One registered device. Deliberately WITHOUT `p256dh`/`auth` — those are the device's address,
    the sender reads them from the row, and a response that carries them is one that can be logged."""
    id: int
    endpoint: str
    userAgent: str
    contentEncoding: str
    expiresAt: str | None = None
    categories: dict
    createdAt: str | None = None
    updatedAt: str | None = None


#: Why a subscription changed — a closed set, so a client cannot write arbitrary strings into the
#: operational log. ``repair_retire`` applies only to deletions (the endpoint a VAPID rotation
#: replaced), the rest only to registrations.
PushReason = Literal["user", "repair", "worker", "repair_retire"]


def _push_reason(value: str) -> str:
    """Clamp a deletion's ``reason`` query parameter to the closed set. A query parameter cannot be
    validated by the request model, and an unrecognised value is a log-injection vector rather than
    something worth rejecting a deletion over — so it degrades to ``user``."""
    return value if value in ("user", "repair", "worker", "repair_retire") else "user"


class PushSubscriptionCreate(BaseModel):
    """A browser's `PushSubscription`, flattened. Validated rather than trusted: this arrives from a
    client, the endpoint becomes a URL the engine will later POST to, and the keys become the
    encryption target — so a malformed one must be rejected here rather than discovered at send time,
    when the failure is asynchronous and looks like a delivery bug."""
    endpoint: str = Field(min_length=8, max_length=1024)
    p256dh: str = Field(min_length=8, max_length=255)
    auth: str = Field(min_length=4, max_length=255)
    contentEncoding: str = Field(default="aes128gcm", max_length=32)
    #: `PushSubscription.expirationTime` — epoch **milliseconds** per the DOM spec, or null (the usual
    #: case). Advisory only; a 410 from the push service is the authoritative end of a subscription.
    expirationTime: int | None = None
    userAgent: str = Field(default="", max_length=255)
    #: What caused this registration, for the operational log only — it changes no behaviour and is
    #: never stored. Without it every registration looks alike, and the question a key rotation makes
    #: urgent ("are devices actually repairing themselves?") has no answer in the logs.
    #: ``user`` a reader enabled it · ``repair`` the client found a retired VAPID key and re-subscribed
    #: · ``worker`` the browser rotated the subscription and the service worker re-registered it.
    reason: PushReason = "user"

    @field_validator("endpoint")
    @classmethod
    def _https_endpoint(cls, v: str) -> str:
        """HTTPS with a host, and nothing else. The engine will POST to whatever is stored here, so an
        unvalidated endpoint is a stored request-forgery target: `http://` would carry the encrypted
        payload in clear transport, and a hostless or non-URL value is a send that can only ever fail."""
        parts = urlsplit(v.strip())
        if parts.scheme != "https" or not parts.netloc:
            raise ValueError("endpoint must be an https:// URL")
        return v.strip()

    @field_validator("p256dh", "auth")
    @classmethod
    def _base64url(cls, v: str) -> str:
        """base64url, as `PushSubscription.getKey` produces. Charset-checked rather than decoded: the
        engine does not interpret these — it hands them to the encryption layer — so the useful test
        is that nothing arrived which could not have come from that API."""
        v = v.strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]+=*", v):
            raise ValueError("must be base64url")
        return v


class ArticleModel(BaseModel):
    # `register` shadows a BaseModel attribute, so hold it under an alias and serialise
    # it back to the wire key "register" (FastAPI responds by_alias).
    model_config = ConfigDict(populate_by_name=True)

    id: str
    headline: str
    publisher: str
    # Nullable like `lean` below (L2.2): a registry-unknown outlet has no house lean either.
    publisherLean: Optional[float] = None
    topic: str
    # the canonical publisher URL — present only when verified (live feed source / a real read),
    # omitted otherwise (response_model_exclude_none); the frontend opens it for the Read flow.
    url: Optional[str] = None
    # article-level political classification (Commit R1) — the flag behind the cross-cutting gate
    # and the bridge explanation; present when known, omitted when unknown (never fabricated).
    political: Optional[bool] = None
    # short summary — populated for Discover/Stories (from the feed); omitted for recommendations.
    description: Optional[str] = None
    # Nullable (L2.2): an outlet the registry doesn't know has an unknown political lean —
    # serialised null rather than a fabricated centre. That covers reading-history reads AND the
    # feed catalog (Discover/Search/Stories coverage — the GDELT long tail is mostly unrated); only
    # the recommendation path (corpus outlets, all rated) always fills both.
    # `response_model_exclude_none` omits the null fields on the wire.
    lean: Optional[float] = None
    leanBucket: Optional[str] = None
    # Same nullability family (L2.2): an unenriched article HAS no confidence / emotion /
    # register — null on the wire (omitted via exclude_none), never 0.7 / all-neutral /
    # "reporting" defaults. The recommendation path still fills its own values.
    confidence: Optional[float] = None
    emotion: Optional[EmotionShareModel] = None
    dominantEmotion: Optional[str] = None
    register_: Optional[str] = Field(None, alias="register")
    # Location Intelligence Phase 0 — canonical publisher-level location; omitted when unknown.
    country: Optional[str] = None
    language: Optional[str] = None
    publishedAt: str
    readingMinutes: int
    # Media + publisher logo (Commit 9; RSS/Atom media only). Null/omitted when absent — the card falls
    # back to the existing text-only layout. `response_model_exclude_none` drops the null fields.
    image: Optional[str] = None
    imageWidth: Optional[int] = None
    imageHeight: Optional[int] = None
    imageMimeType: Optional[str] = None
    imageSource: Optional[str] = None
    imageAttribution: Optional[str] = None
    # Branding verdict on the article's own image (media.hero_suspect — the story-hero guard's
    # suspect tier, serialized so article surfaces can prefer text-first over furniture). Data,
    # not enforcement: the URL still ships beside it.
    imageSuspect: Optional[bool] = None
    publisherLogo: Optional[str] = None
    publisherLogoDark: Optional[str] = None
    publisherLogoSource: Optional[str] = None
    # Ordered alternates to try when the one above fails to load. A Commons file can be renamed and
    # an Apple touch icon is a convention, not a guarantee — without the chain a single 404 drops
    # the outlet to a generic glyph even though it publishes a perfectly good icon one step down.
    publisherLogoFallbacks: Optional[list[str]] = None


class ExplanationPartModel(BaseModel):
    """One semantic explanation part (Commit 23): a catalog-template discriminator plus its
    evidence-derived params — never a localized sentence (the web localizes via the
    rec.reader.* / rec.contribution.* templates, the Commit 20 pattern)."""
    key: str
    params: dict | None = None


class ExplanationModel(BaseModel):
    """The Evidence Resolver's structured explanation (21a.3): the UI shows ``message``;
    tooling and the validation pipeline consume ``type``/``priority``/``evidence``.
    Commit 23 adds the semantic ``readerFact``/``contribution`` parts — additive; older
    clients keep reading ``message``, which is byte-identical."""
    type: str
    priority: int
    variant: str | None = None
    readerFact: ExplanationPartModel | None = None
    contribution: ExplanationPartModel | None = None
    message: str
    evidence: dict | None = None


class RecommendationModel(BaseModel):
    # Commit 21a: ``healthImpact`` removed — it was a stable hash, not a measurement, and every
    # surfaced signal must be traceable to real recommender evidence (the explain endpoint,
    # /api/internal/recommendations/explain, carries that evidence).
    # Commit 21a.3: ``reason`` mirrors ``explanation.message`` (the resolver's ONE human
    # sentence); the structured ``explanation`` is the contract tooling should prefer.
    article: ArticleModel
    reason: str
    strategy: str
    helpsMetric: str
    crossCutting: bool
    # True when this card matched the reader's selected For You country; False when it is
    # BACKFILL — an ordinary recommendation filling a slot the country could not. Absent
    # entirely when no country is selected, so the Global response is unchanged.
    countryMatch: bool | None = None
    explanation: ExplanationModel | None = None


class HistoryEntryModel(BaseModel):
    id: str
    article: ArticleModel
    readAt: str | None = None
    readingMinutes: int
    completed: bool
    readSource: str | None = None     # additive: app | extension | <import> (omitted when unknown)
    openedFrom: str | None = None     # additive: the in-app surface a read came from


# ---- Story Continuation (the post-read "compare this story" offer; docs/STORY_CONTINUATION_DESIGN.md)
class ContinuationOutletModel(BaseModel):
    """One side of the comparison. ``lean``/``leanBucket`` are Optional in the SHAPE but never
    absent in a served payload — the resolver's gate 5 requires both outlets rated, and a
    continuation with an unrated side could not state the symmetric sentence the copy rules
    (design §1.3) require. Optional here so the contract cannot be read as promising a rating
    the registry does not hold."""
    url: str
    publisher: str
    lean: Optional[float] = None
    leanBucket: Optional[str] = None


class ContinuationSiblingModel(ContinuationOutletModel):
    headline: str
    publishedAt: str


class ContinuationModel(BaseModel):
    """One continuation offer. ``null`` (not 404, not an empty object) is the ordinary answer for
    the overwhelming majority of reads — the gates are strict by design and the client renders
    nothing. ``anchor`` travels with ``sibling`` because the copy names BOTH outlets on the same
    axis; rating only the sibling would imply the reader's own article is neutral."""
    storyId: str
    storyTitle: str | None = None
    outlets: int                       # distinct OUTLETS on the story — "20 outlets covered this"
    anchor: ContinuationOutletModel
    sibling: ContinuationSiblingModel
    distance: float                    # |lean difference|, for the analytics decay curve
    candidateCount: int                # how many opposing siblings qualified, before ranking


# ---- Discover & Stories (FeedArticle-powered exploration; product layer) ----
class DiscoverResponseModel(BaseModel):
    articles: list[ArticleModel]
    topics: list[str]        # facet values for the topic filter
    publishers: list[str]    # facet values for the publisher filter
    # country -> located-article count over what Discover lists (event geography, non-provisional);
    # the picker's option list — countries with zero content are simply absent. Same semantics as
    # the Stories countryFacets: country-filter-independent, so the dropdown stays stable.
    countryFacets: "dict[str, int]" = {}


class SearchResponseModel(BaseModel):
    # Live catalog search (Commit 6). `queryMs` + `ftsAvailable` appear only in debug mode, so allow
    # extras. Results reuse the exact Article contract, so the Read flow is identical.
    model_config = ConfigDict(extra="allow")
    results: list[ArticleModel]
    total: int
    page: int
    pageSize: int
    hasMore: bool
    remainingPages: int
    sort: str


class StoryCoverageModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)   # `register` alias, as ArticleModel
    publisher: str
    headline: str
    # Nullable (L2.2) like ArticleModel: an unrated outlet's coverage row carries null, not centre,
    # and an unenriched article's register/emotion are null, not defaults.
    lean: Optional[float] = None
    leanBucket: Optional[str] = None
    register_: Optional[str] = Field(None, alias="register")
    emotion: Optional[EmotionShareModel] = None
    url: Optional[str] = None
    publishedAt: str
    # M4: True on a Tier B article ATTACHED to this story after the build — coverage that never
    # voted (no lean count, no membership, no id input). Absent (exclude_none) on every member
    # row, so the flag-off wire payload is byte-identical to before the field existed.
    tierB: Optional[bool] = None
    # Controlling-owner type of the outlet (registry OWNERSHIPS vocabulary). Absent when the
    # registry doesn't classify the outlet — unknown is unknown, never "other" (L2.2).
    ownership: Optional[str] = None


class TimelinePointModel(BaseModel):
    date: str
    label: str


class StoryModel(BaseModel):
    id: str
    title: str
    summary: str
    # Hero image contract (nullable) — selected from the cluster's RSS media (Commit 9). Omitted when
    # no article in the event carried an image.
    image: Optional[str] = None
    imageWidth: Optional[int] = None
    imageHeight: Optional[int] = None
    imageMimeType: Optional[str] = None
    imageSource: Optional[str] = None
    imageAttribution: Optional[str] = None
    topic: str
    updatedAt: str
    totalCoverage: int          # number of articles in the cluster
    publisherCount: int         # number of distinct publishers
    publishers: list[str] = []  # explicit publisher list
    publisherDiversity: Optional[float] = None   # distinct publishers / articles
    earliest: str
    latest: str
    firstPublished: Optional[str] = None
    latestUpdate: Optional[str] = None
    newest: Optional[str] = None
    oldest: Optional[str] = None
    timeSpanHours: Optional[float] = None
    distribution: ViewpointModel   # L/C/R over distinct publishers (coverage, not opinion)
    coverage: list[StoryCoverageModel]
    timeline: list[TimelinePointModel]
    blindspotSide: Optional[str] = None
    # Outlets that covered this story whose LEAN was recorded but not counted, because the registry
    # carries a `credibility = low` verdict for them (an MBFC Questionable / Low Credibility source).
    # Exposed rather than kept internal: the credibility column exists precisely so the caveat can
    # be shown instead of the outlet being silently dropped, and a field the UI cannot read would
    # rebuild the same invisibility one layer down. Empty for almost every story.
    lowCredibilityPublishers: list[str] = []
    # Story Intelligence summary (Commit 10) — attached by the API layer so cards can badge without an
    # extra request. story_service stays untouched. Omitted (exclude_none) if not computed.
    freshness: Optional[dict[str, Any]] = None    # {band, score}
    lifecycle: Optional[str] = None
    # M4: how many Tier B articles attached to this story (the marked tail of `coverage`).
    # Present only when > 0; every count above (totalCoverage, publisherCount, distribution) still
    # describes the Tier A cluster alone — attached coverage is an addendum, not a vote.
    attachedCoverage: Optional[int] = None


class StoryIntelligenceModel(BaseModel):
    # Full Story Intelligence (Commit 10). Nested shapes vary, so allow extras.
    model_config = ConfigDict(extra="allow")
    storyId: str
    freshness: dict[str, Any]
    lifecycle: str
    momentum: dict[str, Any]
    coverageStatistics: dict[str, Any]
    timeline: list[dict[str, Any]]
    newSinceLastVisit: dict[str, Any]
    alerts: list[dict[str, Any]]
    lastVisited: Optional[str] = None
    lastUpdated: Optional[str] = None
    diagnostics: dict[str, Any]


class StoriesResponseModel(BaseModel):
    # Paginated Story envelope (Commit 7). `clusterMs` + `diagnostics` appear only in debug mode, so
    # allow extras. Discover and Stories both consume this from the single Story Service.
    model_config = ConfigDict(extra="allow")
    stories: list[StoryModel]
    total: int
    page: int
    pageSize: int
    hasMore: bool
    remainingPages: int
    sort: str
    # Story counts per event country under the active topic/publisher/lean filters (computed
    # before the country filter + pagination) — the Stories country picker's source of truth,
    # so an offered country always returns ≥1 story.
    countryFacets: "dict[str, int]" = {}
    # Story counts per DETECTED coverage-gap side (blindspotSide), same faceting discipline —
    # the coverage-gaps picker offers only sides that return ≥1 story. Balanced-or-unknown
    # stories (blindspotSide null) are counted nowhere: a gap is a finding, never a default.
    blindspotFacets: "dict[str, int]" = {}
    # Story counts per curated SOURCE type (news / research / community), same faceting discipline
    # — what selecting that lens would return. Always carries all three keys, so an empty lens
    # reads as 0 rather than going missing. A story covered by both a journal and a newspaper
    # counts under BOTH, so these do not sum to `total`.
    typeFacets: "dict[str, int]" = {}


class CitationModel(BaseModel):
    metric: str
    value: int | float | str
    # Coach v2: the engine surface the number came from (absent on v1 replies)
    source: str | None = None


class CoachMessageModel(BaseModel):
    id: str
    role: str
    content: str
    createdAt: str
    citations: Optional[list[CitationModel]] = None
    suggestions: Optional[list[ArticleModel]] = None
    # Coach v2 (RWE_COACH_V2) — additive; absent (exclude_none) on the v1 path, so old
    # clients and the M0 characterization contract are untouched with the flag off.
    intent: Optional[str] = None
    resolution: Optional[str] = None
    followUps: Optional[list[str]] = None
    cards: Optional[list[RecommendationModel]] = None
    echo: Optional[dict] = None
    # Structured Weekly Review (COMPARE.weekly_review only): reads/outlets/topPublishers,
    # trend first->last per metric, goal minutes, stored goals — the dashboard-card form of the
    # same facts the prose cites. Additive + exclude_none: absent everywhere else.
    weeklyReview: Optional[dict] = None


class HealthStatusModel(BaseModel):
    ok: bool
    profile: str
    domain: str
    demoUser: int
    eligibleReaders: int
    narrative: bool
    dataset: dict[str, Any]
    # Recommendation-source diagnostic: is the live RSS feed driving recs (so they carry real
    # publisher URLs — the Honest URL Pass-through) or the static corpus (no URLs)? Lets an operator
    # verify the deployment's URL state with a single GET /api/health.
    recommendationSource: dict[str, Any]


# OBS1 — health split + metrics + client error sink.
class LivenessModel(BaseModel):
    status: str                      # "alive" — the process is up and serving


class ReadinessModel(BaseModel):
    status: str                      # "ready" | "starting"
    store: bool                      # the persistence layer is built
    backend: bool                    # the serving bundle (engine) is built


class MetricsSnapshotModel(BaseModel):
    model_config = ConfigDict(extra="allow")     # open-ended snapshot (counters + latency timers)
    uptimeSeconds: float


class ClientErrorRequest(BaseModel):
    message: str
    name: Optional[str] = None
    stack: Optional[str] = None
    digest: Optional[str] = None
    url: Optional[str] = None
    context: Optional[dict] = None


class ClientErrorAckModel(BaseModel):
    ok: bool


# PA1 — product analytics. One inbound event; the client always posts a batch (even of one). The
# authoritative identity/time (userId, serverTs, requestId) are stamped server-side, never trusted
# from the client, so only the descriptive fields appear here.
class AnalyticsEventIn(BaseModel):
    event: str
    props: Optional[dict] = None
    anonId: Optional[str] = None
    sessionId: Optional[str] = None
    clientTs: Optional[str] = None


class AnalyticsBatchIn(BaseModel):
    events: list[AnalyticsEventIn]


class AnalyticsAckModel(BaseModel):
    ok: bool
    accepted: int
    dropped: int


class AnalyticsResultModel(BaseModel):
    """Open-ended container for the internal analytics read-backs (funnel / metrics / retention /
    counts) — the shapes are defined by :mod:`product_analytics`."""
    model_config = ConfigDict(extra="allow")


class CoachRequest(BaseModel):
    message: str = ""
    user: str | None = None
    # Coach v2 (RWE_COACH_V2): the client-carried STRUCTURED conversation echo ({"v": 1, ...}).
    # Binding-only — it resolves references ("it", "the first one"); nothing in it is citable.
    echo: dict | None = None


class UpsertUserRequest(BaseModel):
    provider: str
    providerAccountId: str
    email: str | None = None
    displayName: str | None = None
    # False = do not overwrite an EXISTING user's profile with the values above; a first sighting is
    # still created with them. Sent by web-tier identity recovery, which resolves an id from a session
    # token that may be weeks old and must not write a stale profile over a newer one.
    #
    # Defaults True — today's behaviour — so a client that has never heard of this field is unchanged.
    # The model must also keep Pydantic's default extra="ignore": a NEWER web sending refreshProfile
    # to an OLDER engine has to be silently ignored, not rejected, or a rollback of this tier alone
    # would 422 every sign-in. test_internal_user_upsert_ignores_unknown_fields pins that.
    refreshProfile: bool = True


class UserModel(BaseModel):
    userId: int
    email: str | None = None
    displayName: str | None = None


class OutletModel(BaseModel):
    id: str
    name: str
    lean: float
    leanBucket: str
    articles: int


class CountryFacetModel(BaseModel):
    """One country's catalog + registry facts (Countries experience). ``articles``/``publishers``
    are counted from located catalog rows; ``registryPublishers`` from the curated registry — a
    registry country with no located articles yet still appears (zero counts, never hidden)."""
    country: str
    articles: int
    publishers: int
    registryPublishers: int


class ReaderGeographyModel(BaseModel):
    """Counted geographic facts about the signed-in reader's stored reads (Geographic Diversity
    readiness). Facts only — no 0-100 score; ``unknown`` buckets are explicit."""
    reads: int
    located: int
    countries: dict[str, int]
    languages: dict[str, int]
    scope: dict[str, int]


class PlacePublisherModel(BaseModel):
    """One publisher from the locality registry (Local News v1). Locality fields are curated
    facts; anything unknown is omitted (`response_model_exclude_none`), never guessed. A
    locality-only registry row (unrated) omits lean/leanBucket — L2.2, never a default."""
    name: str
    lean: Optional[float] = None
    leanBucket: Optional[str] = None
    country: str | None = None
    region: str | None = None
    city: str | None = None
    scope: str | None = None


# --- Publisher Intelligence (profile page) ------------------------------------------------------
class LabelCountModel(BaseModel):
    """One counted fact: a label (topic / ISO country / ISO language / host) + article count."""
    label: str
    count: int


class PublisherRegistryModel(BaseModel):
    """Curated registry locality — present only when the outlet is in the registry; unknown
    fields are omitted, never guessed (the registry discipline)."""
    country: Optional[str] = None
    region: Optional[str] = None
    city: Optional[str] = None
    scope: Optional[str] = None


class PublisherArticlesModel(BaseModel):
    """Counted catalog volume. ``perDay`` only over a real observed window (>= 1 day)."""
    total: int
    firstSeen: Optional[str] = None
    lastSeen: Optional[str] = None
    perDay: Optional[float] = None


class PublisherRegistersModel(BaseModel):
    """Reporting/opinion/mixed counts over the ``n`` articles that carry a register signal."""
    reporting: int
    opinion: int
    mixed: int
    n: int


class PublisherEmotionModel(BaseModel):
    """Mean emotion shares over the ``n`` articles that carry a real emotion vector."""
    fear: float
    outrage: float
    analysis: float
    positive: float
    neutral: float
    n: int


class TopicGapModel(BaseModel):
    """One under-covered topic: the catalog's counts/shares beside the publisher's — a counted
    comparison (publisher share < half the catalog's, or zero), never a score."""
    label: str
    publisherCount: int
    catalogCount: int
    publisherShare: float
    catalogShare: float


class CoPublisherModel(BaseModel):
    """One co-covering publisher and how many clustered stories they share."""
    publisher: str
    stories: int


class CoCoverageModel(BaseModel):
    """Counted story co-membership: how many clustered stories this publisher shares with at
    least one other outlet, and the outlets it shares them with most."""
    sharedStories: int
    publishers: list[CoPublisherModel]


class PublisherFactualityModel(BaseModel):
    """A third party's factuality verdict, and everything needed to attribute it.

    Every field is required because each one carries part of the honesty: `value` is the rater's
    own label on the rater's own scale (never paraphrased into our vocabulary), `source` is who
    said it, `asOf` is when it was read — raters revise and this registry has no refresh
    mechanism, so an undated verdict shown under a rater's name claims they still say it — and
    `ratingUrl` is where a reader can check the current one. A verdict that cannot supply all four
    is not shown at all."""
    value: str          # FACTUALITY: very_high | high | mostly_factual | mixed | low | very_low
    source: str         # FACTUALITY_SOURCES
    asOf: str           # ISO date the verdict was read
    ratingUrl: str      # the rater's own page/search for this outlet


class PublisherProfileModel(BaseModel):
    """The Publisher Intelligence profile: curated registry facts + counted catalog facts.
    ``rated=false`` means the registry doesn't rate this outlet — lean/leanBucket are null
    (L2.2: "Not rated", never a fabricated Center). Tone modules (registers/emotion) are
    omitted below their signal floor — omit, don't thin-render."""
    name: str
    rated: bool
    lean: Optional[float] = None
    leanBucket: Optional[str] = None
    registry: Optional[PublisherRegistryModel] = None
    # The rater's factuality verdict, carried with its provenance. Absent — not null, not a
    # placeholder level — when no verdict exists, which is the normal case: the registry rates a
    # minority of the outlets in the catalog, and an absent module is the same "unknown" the null
    # lean already speaks. `credibility` is deliberately NOT exposed here: it is the clustering
    # vote-gate's input on a different scale, and showing both would invite a reader to reconcile
    # two numbers that were never meant to agree.
    factuality: Optional[PublisherFactualityModel] = None
    # Whether THIS DEPLOYMENT publishes factuality at all (`RWE_PUBLIC_FACTUALITY`), as distinct
    # from whether THIS OUTLET has a verdict. The client needs both: without this flag an absent
    # `factuality` is ambiguous between "nobody rated them" and "the operator switched publication
    # off", and rendering "Not rated" for the second case would state something false about 123
    # outlets we hold verdicts for.
    factualityPublished: Optional[bool] = None
    site: Optional[str] = None
    articles: PublisherArticlesModel
    topics: list[LabelCountModel] = []
    languages: list[LabelCountModel] = []
    eventCountries: list[LabelCountModel] = []
    registers: Optional[PublisherRegistersModel] = None
    emotion: Optional[PublisherEmotionModel] = None
    # M2 — counted relationship modules, each omitted below its floor (publisher_service).
    topicGaps: Optional[list[TopicGapModel]] = None
    coCoverage: Optional[CoCoverageModel] = None
    recent: list[ArticleModel] = []
    publisherLogo: Optional[str] = None
    publisherLogoDark: Optional[str] = None
    publisherLogoSource: Optional[str] = None
    # Ordered alternates to try when the one above fails to load. A Commons file can be renamed and
    # an Apple touch icon is a convention, not a guarantee — without the chain a single 404 drops
    # the outlet to a generic glyph even though it publishes a perfectly good icon one step down.
    publisherLogoFallbacks: Optional[list[str]] = None


class EstimateRequest(BaseModel):
    outlets: list[str] = []


class OnboardingSaveRequest(BaseModel):
    outlets: list[str] = []


class MeModel(BaseModel):
    onboarding: Optional[dict] = None
    report: Optional[HealthReportModel] = None
    # Stored read count. The authenticated app shell gates on onboarding, and it must not bounce a
    # reader who already HAS reading — anyone past the threshold has onboarded in substance, whether
    # or not an `onboarding` row exists. Without this the gate would send established users back to
    # a funnel they no longer need. Kept on /api/me so the gate is ONE call.
    reads: Optional[int] = None


class AnalyzeMetadata(BaseModel):
    """Optional client-supplied page context for fetchless scoring — the documented metadata
    vocabulary of ``article_analyzer.analyze`` (a subset of :class:`ReadInput`'s fields)."""
    title: str | None = None
    description: str | None = None
    outlet: str | None = None
    category: str | None = None
    subtitle: str | None = None
    political: bool | None = None
    publishedAt: str | None = None    # accepted per the contract; unused by scoring today


class AnalyzeRequest(BaseModel):
    url: str
    metadata: AnalyzeMetadata | None = None


class AnalysisInputModel(BaseModel):
    """ANALYSIS CONTRACT v1 ``input`` — both keys always present (``canonicalUrl`` null when the
    URL is invalid)."""
    url: str
    canonicalUrl: Optional[str] = None


class AnalysisModel(BaseModel):
    """ANALYSIS CONTRACT v1, passed through verbatim. Serialised WITHOUT ``exclude_none``: the
    nulls are the contract (``recommendation``/``personal``/``explanation`` are pinned null until
    A3/A4, and an honest analysis reports ``lean: null`` rather than omitting it). The nested
    sections stay untyped dicts on purpose — ``article`` is the canonical Article shape owned by
    ``discover``; ``story`` is a discriminated union (membership vs advisory) whose variants have
    disjoint keys; and ``scoring``'s contract field ``register`` collides with a Pydantic
    ``BaseModel`` attribute, so typing it would need an alias workaround for zero contract gain.
    Typing any of them would risk injecting or dropping keys and breaking byte-level parity with
    the service (pinned by ``test_catalog_hit_parity_with_service``)."""
    analysisVersion: int
    input: AnalysisInputModel
    status: str                              # "analyzed" | "invalid_url"
    source: Optional[str] = None             # "catalog" | "scored_url_only" | null
    article: Optional[dict] = None
    scoring: Optional[dict] = None
    story: Optional[dict] = None
    #: Coverage Comparison L0 (docs/COVERAGE_COMPARISON_DESIGN.md) — counted facts about the
    #: article's story cluster, or an {"available": false, "reason": …} refusal. Untyped for the
    #: same reason as the sections above: the shape is owned by coverage_comparison.
    coverageComparison: Optional[dict] = None
    recommendation: Optional[dict] = None    # pinned null until A3-Enrich
    personal: Optional[dict] = None          # pinned null until A4
    explanation: Optional[dict] = None       # pinned null until A3-Enrich
    notes: list[str] = []


class ReadInput(BaseModel):
    url: str
    title: str | None = None
    outlet: str | None = None
    category: str | None = None
    political: bool | None = None
    observedAt: str | None = None
    subtitle: str | None = None       # optional richer text for enrichment
    description: str | None = None    # optional richer text for enrichment (og:description)
    readSource: str | None = None     # app | extension | <future import> (additive; metadata only)
    openedFrom: str | None = None     # in-app surface: recommendations/discover/stories/search/saved
    device: str | None = None         # optional client hint
    timeZone: str | None = None       # the reader's IANA zone at read time (see below)
    # Standard page metadata the extension collects (Commit 18) so an extension read can become a
    # first-class FeedArticle. All optional + additive; never trusted for canonicalization (the
    # engine canonicalizes the URL itself). ``language``/``author`` are accepted per the privacy
    # allowlist but not yet persisted (FeedArticle has no columns for them).
    image: str | None = None          # og:image
    publishedAt: str | None = None    # article:published_time
    siteName: str | None = None       # og:site_name (publisher hint)
    language: str | None = None       # <html lang>
    author: str | None = None         # meta[name=author]


class ReadsRequest(BaseModel):
    reads: list[ReadInput] = []


class IngestResultModel(BaseModel):
    accepted: int
    duplicates: int
    rejected: int
    totalReads: int
    threshold: int
    sufficient: bool


class RecOpenRequest(BaseModel):
    # the recommended article the reader opened; crossCutting is the rec's own flag (the web tier
    # forwards it from the rec payload) used only if the surfacing wasn't recorded first.
    articleId: str
    crossCutting: bool | None = None


class RecReceptionModel(BaseModel):
    shownCross: int
    openedCross: int
    rate: float | None = None
    threshold: int          # cross-cutting recs that must be surfaced before Open-Mindedness activates
    active: bool            # whether the reader now has enough reception for Open-Mindedness


# The canonical (snake_case) feedback signals — mirrors store.RECOMMENDATION_FEEDBACK_TYPES.
# The last five are the Tier-2 vocabulary (docs/X_ALGORITHM_AUDIT_AND_PROPOSAL.md, Phase 13.6).
RecFeedbackType = Literal["like", "dislike", "ignore", "read_later",
                          "another_viewpoint", "already_know", "too_repetitive",
                          "fewer_from_source", "more_topic"]


class RecFeedbackRequest(BaseModel):
    # the recommended article and the reader's explicit signal; an unknown feedback value is rejected
    # here (422) by the Literal, so the store only ever sees a canonical type.
    articleId: str
    feedback: RecFeedbackType


class RecFeedbackAckModel(BaseModel):
    ok: bool
    feedback: RecFeedbackType
    changed: bool           # True when newly recorded; False for an idempotent repeat of the same signal


class RecFeedbackRemoveRequest(BaseModel):
    # the undo behind the visible-consequence UI: one recorded signal, or (feedback omitted) every
    # signal the reader gave this article. Removing what was never recorded is ok:false-free — the
    # ack's `removed` count is the honest answer and 0 is a fine value.
    articleId: str
    feedback: Optional[RecFeedbackType] = None


class RecFeedbackRemoveAckModel(BaseModel):
    ok: bool
    removed: int            # rows deleted (0 = nothing was recorded for this article/type)


# The settings ledger's human-scale view (the effects redesign): signals grouped by the dimension
# they touch, derived engine-side from the SAME table ranking consumes (FEEDBACK_DIMENSIONS).
class FeedbackSignalModel(BaseModel):
    articleId: str
    feedback: RecFeedbackType
    createdAt: str


class FeedbackEffectGroupModel(BaseModel):
    name: str                       # publisher name / prettified topic
    direction: Literal["more", "less"]
    signals: list[FeedbackSignalModel]


class FeedbackArticleModel(BaseModel):
    articleId: str
    feedback: RecFeedbackType
    createdAt: str
    headline: Optional[str] = None  # humanized from the catalog; None once the article rotated out
    publisher: Optional[str] = None
    url: Optional[str] = None
    inCatalog: bool                 # False = an expired reference; the UI must say so, not show ids


class FeedbackEffectsModel(BaseModel):
    publishers: list[FeedbackEffectGroupModel]
    topics: list[FeedbackEffectGroupModel]
    articles: list[FeedbackArticleModel]


class RecFeedbackEntryModel(BaseModel):
    articleId: str
    feedback: RecFeedbackType
    createdAt: str
    updatedAt: str


# RC2.3 — the explicit reader lifecycle signals on an improvement recommendation.
ImprovementEventType = Literal["accept", "dismiss", "view"]
_IMPROVEMENT_EVENT_TO_STATE = {"accept": "accepted", "dismiss": "dismissed", "view": "viewed"}


class ImprovementEventAckModel(BaseModel):
    ok: bool
    recKey: str
    event: str               # the recorded lifecycle event (accepted | dismissed | viewed)
    created: bool            # True when the ledger row was newly created by this event


class ImprovementLifecycleEntryModel(BaseModel):
    recKey: str
    metric: str
    state: str
    firstScore: Optional[int] = None
    currentScore: Optional[int] = None
    completedScore: Optional[int] = None
    generatedAt: Optional[str] = None
    shownAt: Optional[str] = None
    viewedAt: Optional[str] = None
    acceptedAt: Optional[str] = None
    dismissedAt: Optional[str] = None
    completedAt: Optional[str] = None
    expiredAt: Optional[str] = None
    supersededAt: Optional[str] = None
    supersededBy: Optional[str] = None
    updatedAt: Optional[str] = None


# RC2.5 — recommendation evaluation & attribution (read-only projections).
class AttributionModel(BaseModel):
    recommendationAttributed: float
    organic: float
    populationDrift: float


class RecommendationEvalModel(BaseModel):
    recKey: str
    metric: str
    outcome: Optional[str] = None
    estimatedGain: Optional[int] = None
    realizedGain: Optional[int] = None
    attribution: AttributionModel
    attributionConfidence: str
    calibrationError: Optional[float] = None
    sustainedImprovement: Optional[bool] = None


class ReaderEvaluationModel(BaseModel):
    recommendations: list[RecommendationEvalModel]
    outcomes: dict[str, int]


class CohortRuleQualityModel(BaseModel):
    # ruleQuality is an open-ended {metric: {rates + calibration}} map — kept permissive so new quality
    # fields need no model change; the deterministic contract lives in recommendation_eval.rule_quality.
    model_config = ConfigDict(extra="allow")
    cohortSize: int
    ruleQuality: dict[str, dict]


class SaveArticleRequest(BaseModel):
    articleId: str
    article: dict = {}      # the Article snapshot the reader saw (rendered later in the saved list)


class SavedArticleModel(BaseModel):
    articleId: str
    article: dict
    savedAt: str | None = None


class SaveResultModel(BaseModel):
    articleId: str
    saved: bool             # the resulting state: true after a save, false after an unsave
    savedCount: int         # the reader's live saved total (drives the profile's Saved counter)


class CreateTokenRequest(BaseModel):
    label: str | None = None


class TokenMintModel(BaseModel):
    # the plaintext token is returned exactly once, at creation
    id: int
    token: str
    label: str | None = None
    createdAt: str | None = None


class TokenModel(BaseModel):
    id: int
    label: str | None = None
    createdAt: str | None = None
    lastUsedAt: str | None = None


class ResolveTokenRequest(BaseModel):
    token: str


class ResolveTokenModel(BaseModel):
    userId: int


class StorageStatusModel(BaseModel):
    # storage/durability diagnostics — the concrete fields depend on the backend, so allow extras.
    model_config = ConfigDict(extra="allow")
    url: str
    backend: str
    ephemeral: bool


class FeedHealthModel(BaseModel):
    # per-feed polling health + quality (observational). `status` is derived from the failure count.
    feedUrl: str
    name: Optional[str] = None
    status: str                 # healthy | degraded | unhealthy
    healthy: bool
    consecutiveFailures: int
    totalPolls: int
    totalOk: int
    totalFailed: int
    lastSuccessAt: Optional[str] = None
    lastFailureAt: Optional[str] = None
    lastError: Optional[str] = None
    lastLatencyMs: Optional[float] = None
    avgLatencyMs: Optional[float] = None
    newestPublished: Optional[str] = None
    oldestPublished: Optional[str] = None
    newestAgeDays: Optional[float] = None       # age (days) of the newest article; None if undated
    staleThresholdDays: Optional[int] = None    # RWE_FEED_STALE_DAYS in effect
    stale: Optional[bool] = None                # newest article older than the threshold (separate from status)
    imported: int
    duplicate: int
    rejected: int
    missingMetadata: int
    updatedAt: Optional[str] = None


class CorpusValidationModel(BaseModel):
    # Corpus-eligibility diagnostics (validation ONLY — this reports whether a candidate corpus WOULD
    # be eligible to activate; it never activates, rebuilds Backend, or hot-swaps). Nested dicts vary
    # in shape, so allow extras.
    model_config = ConfigDict(extra="allow")
    eligible: bool
    status: str                              # pass | fail | error
    generatedAt: str
    candidateSize: int
    metrics: dict[str, Any]
    publisherDistribution: dict[str, Any]
    politicalDistribution: dict[str, Any]
    freshness: dict[str, Any]
    healthyFeeds: int
    unhealthyFeeds: int
    failures: list[dict[str, Any]]
    warnings: list[dict[str, Any]]
    thresholds: dict[str, Any]


class RefreshStatusModel(BaseModel):
    # Hot-refresh / activation diagnostics (Commit 5). Reports the active corpus generation and the
    # last refresh outcome; it triggers nothing. Extra allowed for forward-compat.
    model_config = ConfigDict(extra="allow")
    state: str                          # idle | building | swapping | failed
    source: str                         # feed | static
    generation: int
    activeVersion: int
    activeSignature: Optional[str] = None
    candidateVersion: Optional[str] = None
    itemCount: int
    builtAt: Optional[str] = None
    refreshCount: int
    lastSuccessAt: Optional[str] = None
    lastFailedAt: Optional[str] = None
    lastValidationAt: Optional[str] = None
    lastError: Optional[str] = None
    lastFailures: list[str] = []
    buildMs: Optional[float] = None
    activationMs: Optional[float] = None
    pollingEnabled: bool


def _active() -> "corpus_refresh.Active":
    """The current serving bundle (backend + personalizer + generation), captured in one atomic read.
    A handler that needs a swap-consistent view for the whole request captures this ONCE and reads
    ``.backend`` / ``.personalizer`` off it, so a hot swap landing mid-request can't split a response
    across two generations."""
    a = state.active
    if a is None:
        raise HTTPException(status_code=503, detail="The engine is still starting up.")
    return a


def _require_backend() -> "engine.Backend":
    return _active().backend


def _resolve(user: str | None) -> int:
    return _require_backend().resolve_user({"user": [user]} if user is not None else {})


_REAL_USER_HEADER = "x-ih-user-id"
_AUTH_HEADER = "x-ih-auth"


def _internal_secret() -> "str | None":
    """The shared secret the web tier signs internal calls with, or None if unset.

    In local development (no production signal) the engine trusts the local caller so the app
    runs with zero extra configuration; production mode *requires* it (see :func:`_require_auth`
    and :func:`_config_errors`). Read per-request so it can be rotated without a restart."""
    return os.environ.get("RWE_INTERNAL_SECRET") or None


def _require_auth() -> bool:
    """Whether internal / per-user requests must be authenticated (fail closed).

    Follows production mode by default; ``RWE_REQUIRE_AUTH`` (``1``/``0``) can force it on or off
    independently — e.g. to exercise the closed path in a test, or harden a staging box that
    isn't formally ``RWE_ENV=production``."""
    override = os.environ.get("RWE_REQUIRE_AUTH")
    if override is not None and override.strip() != "":
        return override.strip().lower() in {"1", "true", "yes", "on"}
    return _production()


def _config_errors() -> "list[str]":
    """Fatal misconfigurations that must stop the engine from starting.

    The one that matters for fail-closed auth: production mode with no ``RWE_INTERNAL_SECRET``
    would leave the engine unable to authenticate the web tier — so rather than silently trust
    every caller (the audited account-takeover), the engine refuses to boot. Additive: this is
    empty in local development, so nothing changes there."""
    errors: list[str] = []
    if _require_auth() and _internal_secret() is None:
        errors.append(
            "Production mode is enabled (RWE_ENV=production or RWE_REQUIRE_AUTH=1) but "
            "RWE_INTERNAL_SECRET is not set. Without it the engine cannot authenticate the web "
            "tier and would have to trust any caller presenting an X-IH-User-Id header. Set "
            "RWE_INTERNAL_SECRET (identical on the web app and the engine), or unset production "
            "mode for local development."
        )
    db_url = os.environ.get("RWE_DB_URL") or store.default_db_url()
    if _production() and store.is_ephemeral_url(db_url):
        errors.append(
            "Production mode is enabled (RWE_ENV=production) but RWE_DB_URL points at ephemeral "
            "storage (an in-memory database, or a temp directory like /tmp) — every account, read, "
            "report, and token is lost on restart. Point RWE_DB_URL at a persistent file on a "
            "mounted volume (e.g. sqlite:////app/data/ih_beta.db) or a database server."
        )
    return errors


def _trusted(request: Request) -> bool:
    """Whether a request is from the trusted web tier.

    * A secret is configured   -> trusted only with the matching ``X-IH-Auth`` header.
    * No secret, dev mode      -> trusted (zero-config local development / the Colab demo).
    * No secret, auth required  -> **never** trusted (fail closed). Startup validation
      (:func:`_config_errors`) normally prevents this state; this is the runtime safety net so
      a mis-started prod process denies rather than exposes."""
    secret = _internal_secret()
    if secret is not None:
        return request.headers.get(_AUTH_HEADER) == secret
    return not _require_auth()


def _require_trusted(request: Request) -> None:
    """Reject an internal call that is not from the trusted web tier (fail closed in prod)."""
    if not _trusted(request):
        raise HTTPException(status_code=401, detail="Missing or invalid internal credentials.")


def _client_ip(request: Request) -> str:
    """Best-effort client IP for rate-limit keying: the first ``X-Forwarded-For`` hop (set by the
    web tier / load balancer) when present, else the socket peer. Behind the web proxy the real
    client IP arrives via XFF; a direct call uses the peer."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        first = xff.split(",")[0].strip()
        if first:
            return first
    return request.client.host if request.client else "unknown"


def _rate_identity(request: Request) -> str:
    """The rate-limit key subject: the authenticated user when the trusted web tier names one
    (``u:<id>``), else the client IP (``ip:<addr>``). Keying by user id keeps per-user limits
    correct even though every production request shares the web tier's socket address."""
    raw = request.headers.get(_REAL_USER_HEADER)
    if raw and raw.lstrip("-").isdigit() and _trusted(request):
        return f"u:{raw}"
    return f"ip:{_client_ip(request)}"


def _body_limit_check(request: Request) -> "JSONResponse | None":
    """Reject an oversized request body with a typed ``413`` *before* it is buffered or parsed —
    the memory-exhaustion defence. Uses the ``Content-Length`` header (all JSON clients set it), so
    the body is never read; the body is never logged. Returns ``None`` for exempt / read-only /
    no-body requests. A chunked upload without ``Content-Length`` is passed through here and bounded
    downstream by the per-scope model limits (see the deployment notes)."""
    limit = reqlimits.max_body_bytes(request.method, request.url.path, _production())
    if limit is None:
        return None
    cl = request.headers.get("content-length")
    if cl is not None and cl.isdigit() and int(cl) > limit:
        _log(logging.WARNING, "payload_too_large", method=request.method, path=request.url.path,
             contentLength=int(cl), limitBytes=limit)
        return _error(413, "payload_too_large",
                      "Request body is too large. Please reduce the payload and try again.")
    return None


#: Interaction telemetry fired by normal browsing — exempt from the 403 guard; these routes
#: no-op successfully for the exhibit account instead (a visitor must never see an error for
#: simply using the product). Future interaction telemetry endpoints belong in this set.
_DEMO_INTERACTION_PATHS = {"/api/me/recommendations/opened"}


def _is_demo_account(uid: "int | None") -> bool:
    """Whether ``uid`` is the designated read-only exhibit account (constant False when the
    ``RWE_DEMO_ACCOUNT`` feature is off)."""
    return state.demo_uid is not None and uid == state.demo_uid


def _demo_write_check(request: Request) -> "JSONResponse | None":
    """The exhibit account is immutable ONCE SEEDED: administrative mutations under ``/api/me/*``
    (reads, settings, saved, tokens, onboarding — and every future writer, by construction)
    return a typed 403 when the caller IS the measured demo account. While the account is still
    empty (below the read threshold) provisioning flows through the normal public pipeline —
    and the flip is one-way, because no route deletes reads. Interaction telemetry
    (``_DEMO_INTERACTION_PATHS``) is exempted here and no-ops successfully in its route.
    ONE enforcement site; no per-route logic anywhere."""
    if state.demo_uid is None or request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return None
    path = request.url.path
    if not path.startswith("/api/me/") or path in _DEMO_INTERACTION_PATHS:
        return None
    if not _is_demo_account(_real_uid(request)):
        return None
    if state.active is None or not state.personalizer.has_measured(state.demo_uid):
        return None                          # the pre-seed provisioning window
    _log(logging.WARNING, "demo_account_write_blocked", method=request.method, path=path)
    return _error(403, "demo_account_read_only",
                  "The demo account is read-only. Sign in with your own account to make changes.")


def _rate_limit_check(request: Request) -> "JSONResponse | None":
    """Apply the token-bucket limiter to a request. Returns a typed ``429`` (with ``Retry-After``)
    when the caller has exceeded the scope's rate, else ``None`` to let the request proceed. Never
    raises — a limiter fault must not take down the request path. Exempt paths (health/docs) and
    pre-flight are classified out by :func:`ratelimit.scope_for`."""
    limiter = state.limiter
    if limiter is None or not ratelimit.enabled():
        return None
    scope = ratelimit.scope_for(request.method, request.url.path)
    if scope is None:
        return None
    identity = _rate_identity(request)
    rate = ratelimit.rate_for(scope, production=_production())
    ok, retry_after = limiter.check(f"{scope}|{identity}", rate)
    if ok:
        return None
    _log(logging.WARNING, "rate_limited", scope=scope, identityKind=identity.split(":", 1)[0],
         method=request.method, path=request.url.path, limitPerMin=rate, retryAfter=retry_after)
    resp = _error(429, "rate_limited",
                  "Too many requests — please slow down and try again in a moment.")
    resp.headers["Retry-After"] = str(retry_after)
    return resp


def _require_store() -> "store.Store":
    if state.store is None:
        raise HTTPException(status_code=503, detail="The engine is still starting up.")
    return state.store


def _require_scorer() -> "ingest.Scorer":
    if state.scorer is None:
        raise HTTPException(status_code=503, detail="The engine is still starting up.")
    return state.scorer


def _require_personalizer() -> "personalize.Personalizer":
    return _active().personalizer


def _real_uid(request: Request) -> "int | None":
    """The authenticated real engine user id if this is a trusted signed-in reader, else None.

    A real user is named by the ``X-IH-User-Id`` header the web tier sets after Google sign-in;
    the header is honoured only with the internal secret (when configured) and when it maps to a
    known user. ``None`` means an anonymous / ``?user=`` request — the exact demo + contract
    behaviour, untouched."""
    raw = request.headers.get(_REAL_USER_HEADER)
    if (raw and raw.lstrip("-").isdigit() and _trusted(request) and state.store is not None
            and state.store.get_user(int(raw)) is not None):
        return int(raw)
    return None


def _anon_row(active: "corpus_refresh.Active", request: Request, user: str | None) -> int:
    """The base-corpus row for an anonymous request — the ``?user=`` selector, else the demo."""
    return active.backend.resolve_user({"user": [user]} if user is not None else {})


def _demo_personal(active: "corpus_refresh.Active") -> "int | None":
    """The exhibit account's uid when it can actually carry a Measured report (configured via
    ``RWE_DEMO_ACCOUNT`` and seeded past the read threshold), else ``None`` — the cold-start
    fallback stays the synthetic demo reader, byte-identical to the pre-feature behaviour."""
    uid = state.demo_uid
    if uid is not None and active.personalizer.has_measured(uid):
        return uid
    return None


def _serve(active: "corpus_refresh.Active", request: Request, user: str | None):
    """Routing for recommendations + coach (which have no Estimate form). Reads the single captured
    ``active`` bundle so the whole request stays on one corpus generation across a hot swap.

    Returns ``(kind, value, is_sample)``. ``("personal", uid, False)`` when the signed-in reader has
    crossed the read threshold — the request is served from their augmented corpus. Otherwise the
    seeded exhibit account (``_demo_personal``) is preferred — the same measured pipeline every real
    user gets — falling back to ``("row", row, …)``: the synthetic demo reader, or the ``?user=``
    selection, which always wins for an anonymous request (the row picker is a deliberate exhibit
    browser).

    ``is_sample`` is the same question ``_report_for`` answers: is this the requesting reader's own
    data? A recommendation is not a neutral article list — it carries a RATIONALE ("this offers
    another political perspective"), and that sentence is a claim about the reader's existing diet.
    Served to someone who has read nothing it is bridging away from a position they never held."""
    uid = _real_uid(request)
    if uid is not None and active.personalizer.has_measured(uid):
        return "personal", uid, False
    demo = _demo_personal(active)
    if uid is not None:
        # Signed in, but nothing of their own — whichever fallback is configured, it is not theirs.
        return (("personal", demo, True) if demo is not None
                else ("row", active.backend.demo_user, True))
    if user is None and demo is not None:
        return "personal", demo, True
    return "row", _anon_row(active, request, user), True


def _require_real_user(request: Request) -> int:
    """The authenticated engine user id from the trusted web tier (X-IH-User-Id + secret),
    or 401. The per-user ``/api/me`` endpoints act on a specific real account, so — unlike
    ``/api/report`` — they have no demo fallback."""
    raw = request.headers.get(_REAL_USER_HEADER)
    if not (raw and raw.lstrip("-").isdigit()) or not _trusted(request):
        raise HTTPException(status_code=401, detail="Authentication required.")
    uid = int(raw)
    if state.store is None or state.store.get_user(uid) is None:
        raise HTTPException(status_code=401, detail="Unknown user.")
    return uid


@app.get("/api/health", response_model=HealthStatusModel, tags=["meta"],
         summary="Service health and dataset summary", responses=_ERR_RESPONSES)
def health() -> dict:
    active = _active()
    be = active.backend
    h = be.health()
    # Surface whether recommendations are sourced from the live RSS feed (URLs present) or the static
    # corpus (no URLs), plus the active corpus generation (bumps on each hot refresh). `url_by_id` is
    # populated when the feed source is active; `generation` starts at 1 and increments per swap.
    feed_articles = state.store.count_feed_articles() if state.store is not None else 0
    h["recommendationSource"] = {
        "source": "feed" if be.url_by_id else "static",
        "generation": active.generation,
        "feedArticles": int(feed_articles),
        "resolvedUrls": len(be.url_by_id),
        "recsSourceEnv": os.environ.get("RWE_RECS_SOURCE", ""),
    }
    return h


@app.get("/api/health/live", response_model=LivenessModel, tags=["meta"],
         summary="Liveness probe — is the process up?")
def health_live() -> dict:
    """OBS1 **liveness**: the process is running and can serve. It does **no** dependency checks, so a
    liveness probe never restarts the process for a slow/absent dependency (that's readiness's job)."""
    return {"status": "alive"}


@app.get("/api/health/ready", response_model=ReadinessModel, tags=["meta"],
         summary="Readiness probe — are dependencies built?", responses=_ERR_RESPONSES)
def health_ready() -> JSONResponse:
    """OBS1 **readiness**: whether the store and the serving bundle (engine) are built. Returns **503**
    until both are ready, so a load balancer holds traffic during startup / a corpus (re)build."""
    store_ok = state.store is not None
    backend_ok = state.active is not None and getattr(state.active, "backend", None) is not None
    ready = store_ok and backend_ok
    body = {"status": "ready" if ready else "starting", "store": store_ok, "backend": backend_ok}
    return JSONResponse(status_code=200 if ready else 503, content=body)


@app.get("/api/metrics", response_model=MetricsSnapshotModel, tags=["meta"],
         summary="[internal] In-process application metrics snapshot", responses=_ERR_RESPONSES)
def metrics_snapshot(request: Request) -> dict:
    """OBS1 in-process metrics — request counts + latency (p50/p95/p99) by route, report-generation and
    DB-query timings, and uptime. **Internal-only**: served to the trusted web tier / an operator with
    the internal secret; any other caller gets 404 (it must not exist for the public, like the dev
    endpoints). No external monitoring dependency — a later phase drains this into Prometheus/OTel."""
    if not _trusted(request):
        raise HTTPException(status_code=404, detail="Not found.")
    return obs_metrics.snapshot()


# --------------------------------------------------------------------------------------- IH Search
def _search_api_keys() -> "frozenset[str]":
    return frozenset(k.strip() for k in os.environ.get("RWE_SEARCH_API_KEYS", "").split(",")
                     if k.strip())


def _search_authorized(api_key: str, request: Request) -> bool:
    """The facade's own key scheme — deliberately NOT `_trusted` alone, because the design review
    ruled the search API grows toward external consumers with their own keys. Accepted: any key in
    ``RWE_SEARCH_API_KEYS``, the internal secret (so the discovery cron works with zero new
    configuration), or — with neither configured — dev mode's local trust, mirroring `_trusted`."""
    keys = _search_api_keys()
    if api_key and api_key in keys:
        return True
    secret = _internal_secret()
    if secret is not None and (api_key == secret or request.headers.get(_AUTH_HEADER) == secret):
        return True
    return not keys and secret is None and not _require_auth()


def _serp_topup(query: str, need: int, st) -> "tuple[list, str | None]":
    """Up to ``need`` SerpAPI results — the blended half of the facade, budget-metered.

    Spends ONLY when the internal index fell short (`internal-first, external top-up`), against
    the same durable daily meter the discovery channel uses (one SerpAPI account, one budget —
    two meters would let the pair spend double what the operator authorised). The upstream key is
    ``RWE_SERPAPI_API_KEY``, its own env on purpose: after cutover `RWE_WEB_SEARCH_API_KEY` holds
    OUR key, and reading it here would send our internal key to SerpAPI. `retries=0` for the
    reason `source_web.search_adapter` documents: search providers bill errors as readily as
    successes. Returns (results, note); a failure is a note, never an exception — a thin page is
    a degraded answer, not an outage."""
    import source_web
    key = os.environ.get("RWE_SERPAPI_API_KEY", "").strip()
    if not key or os.environ.get("RWE_SEARCH_TOPUP", "1").strip().lower() in ("0", "false", "no"):
        return [], None
    budget = source_web.search_daily_budget()
    if st.web_search_spent() >= budget:
        obs_metrics.incr("search_api_topup_budget_exhausted_total")
        return [], f"top-up budget exhausted ({budget}/day)"
    url = ("https://serpapi.com/search.json?"
           + urllib.parse.urlencode({"q": query, "num": min(need, 20), "api_key": key}))
    try:
        st.note_web_search()                            # count BEFORE the request, like the channel
        payload = sources._get_json(url, retries=0)
    except Exception as exc:                            # noqa: BLE001 — degraded, not down
        obs_metrics.incr("search_api_topup_errors_total")
        return [], f"top-up failed: {type(exc).__name__}"
    out = [{"link": (r.get("link") or "").strip(), "title": (r.get("title") or "").strip(),
            "snippet": r.get("snippet") or ""}
           for r in (payload.get("organic_results") or []) if isinstance(r, dict)]
    obs_metrics.incr("search_api_topup_requests_total")
    obs_metrics.incr("search_api_topup_results_total", len(out))
    return [r for r in out if r["link"]], None


@app.get("/api/search.json", response_model=None, tags=["search"],
         summary="IH Search — SerpAPI-compatible outlet/web-source discovery")
def ih_search(request: Request, q: str = "", num: int = 10, api_key: str = "",
              engine: str = "google") -> "JSONResponse | dict":
    """The SerpAPI-shaped facade over the IH outlet index (`outlet_search.py`).

    The HARD contract (what `source_web.search_adapter` reads): 200 + `organic_results[]` of
    `{link, title}`; 401/400/429 otherwise. The envelope (`search_metadata` etc.) is served for
    third-party SerpAPI clients and costs nothing. Results are internal-first; a shortfall is
    topped up from SerpAPI under the shared daily budget, each row marked `ih_source` — interface
    compatibility, honestly labelled provenance."""
    t0 = time.perf_counter()
    obs_metrics.incr("search_api_requests_total")
    if not _search_authorized(api_key, request):
        return JSONResponse({"error": "Invalid API key. Your API key should be here: "
                                      "https://serpapi.com/manage-api-key"}, status_code=401)
    if not q.strip():
        return JSONResponse({"error": "Missing query `q` parameter."}, status_code=400)
    num = max(1, min(int(num or 10), 50))
    import outlet_search
    st = _require_store()
    con = outlet_search.open_index()
    try:
        plan = outlet_search.plan_query(q)
        rows = outlet_search.query_index(con, plan, count=num,
                                         feedback=outlet_search.feedback_weights(st))
    finally:
        con.close()
    results = [{"position": i + 1, "title": r["name"], "link": f"https://{r['host']}/",
                "source": r["domain"], "displayed_link": f"https://{r['host']}",
                "snippet": (f"{r['country'] or '??'}/{r['language'] or '??'} — evidence: "
                            f"{', '.join(r['evidence'])}"),
                "ih_source": "internal", "ih_score": r["score"], "ih_tracked": r["tracked"]}
               for i, r in enumerate(rows)]
    obs_metrics.incr("search_api_internal_results_total", len(results))
    note = None
    if len(results) < num:
        extra, note = _serp_topup(q, num - len(results), st)
        have = {outlet_search.registrable_domain(
            urlsplit(r["link"]).hostname or "") for r in results}
        for r in extra:
            dom = outlet_search.registrable_domain(urlsplit(r["link"]).hostname or "")
            if dom in have:
                continue
            have.add(dom)
            results.append({"position": len(results) + 1, "title": r["title"], "link": r["link"],
                            "source": dom, "snippet": r.get("snippet") or "",
                            "ih_source": "serpapi"})
            if len(results) >= num:
                break
    body = {
        "search_metadata": {"id": uuid.uuid4().hex, "status": "Success",
                            "created_at": datetime.now(timezone.utc).isoformat(),
                            "total_time_taken": round(time.perf_counter() - t0, 3),
                            **({"note": note} if note else {})},
        "search_parameters": {"engine": engine or "google", "q": q, "num": num},
        "search_information": {"total_results": len(results)},
        "organic_results": results,
    }
    return body


@app.post("/api/client-errors", response_model=ClientErrorAckModel, tags=["meta"],
          summary="Sink for frontend error reports (structured log / reporter)", responses=_ERR_RESPONSES)
def client_error(request: Request, req: ClientErrorRequest) -> dict:
    """OBS1 — the backend sink the web tier's error reporter posts to (the "custom backend" provider), so
    a browser crash lands in the same correlatable log stream + exception reporter as a server error.
    Best-effort and additive: fields are truncated, it's covered by the existing size + rate limits, and
    it never fails the caller. No auth (errors happen for anonymous visitors too)."""
    def _clip(s, n):
        return s[:n] if isinstance(s, str) else s
    obs_metrics.incr("client_errors_total")
    _log(logging.WARNING, "client_error", name=_clip(req.name, 120), message=_clip(req.message, 500),
         url=_clip(req.url, 500), digest=_clip(req.digest, 120))
    error_reporting.report_message("client_error", name=_clip(req.name, 120),
                                   message=_clip(req.message, 500), url=_clip(req.url, 500),
                                   digest=_clip(req.digest, 120), stack=_clip(req.stack, 4000),
                                   requestId=_request_id.get())
    return {"ok": True}


# ------------------------------------------------------------------ #
# PA1 — product analytics: the event sink + the internal read-back dashboard. Measurement only;
# no recommender/report/ranking/lifecycle/eval path reads these, and nothing here changes behavior.
# ------------------------------------------------------------------ #
_MAX_EVENTS_PER_BATCH = 50


@app.post("/api/events", response_model=AnalyticsAckModel, tags=["meta"],
          summary="Product-analytics event sink (PA1)", responses=_ERR_RESPONSES)
def analytics_events(request: Request, batch: AnalyticsBatchIn) -> dict:
    """PA1 — the sink the web tier's analytics beacon posts to. Each event is validated against the
    taxonomy allow-list (:mod:`product_analytics`); the **user id is resolved server-side** from the
    trusted web tier (never client-asserted); the authoritative ``server_ts`` + correlation
    ``request_id`` are stamped here. Best-effort and additive: unknown events are dropped, the batch is
    capped, and a storage failure never fails the caller. **No user auth** — anonymous (pre-account)
    events are exactly what the pre-activation funnel needs."""
    uid = _real_uid(request)
    now = datetime.now(timezone.utc).isoformat()
    rid = _request_id.get()
    accepted: list[dict] = []
    dropped = 0
    for ev in batch.events[:_MAX_EVENTS_PER_BATCH]:
        norm = product_analytics.normalize(ev.model_dump())
        if norm is None:
            dropped += 1
            continue
        norm["user_id"] = uid                    # authoritative: from X-IH-User-Id, not the client
        norm["server_ts"] = now
        norm["request_id"] = rid
        accepted.append(norm)
    dropped += max(0, len(batch.events) - _MAX_EVENTS_PER_BATCH)
    written = 0
    if accepted and state.store is not None:
        try:
            written = state.store.record_analytics_events(accepted)
        except Exception as exc:                 # analytics must never break a request
            error_reporting.report_exception(exc, where="analytics_events", requestId=rid)
            written = 0
    obs_metrics.incr("analytics_events_total", written)
    return {"ok": True, "accepted": written, "dropped": dropped}


def _analytics_rows(request: Request) -> list:
    """Guard + fetch for the internal analytics dashboard: **internal-only** (trusted web tier / an
    operator with the internal secret), 404 to anyone else — the exact posture of ``/api/metrics``."""
    if not _trusted(request):
        raise HTTPException(status_code=404, detail="Not found.")
    return state.store.list_analytics_events() if state.store is not None else []


@app.get("/api/analytics/funnel", response_model=AnalyticsResultModel, tags=["meta"],
         summary="[internal] Activation funnel + conversions", responses=_ERR_RESPONSES)
def analytics_funnel(request: Request) -> dict:
    """The ten-stage activation funnel with per-stage reachers, stage/overall conversion, and the top
    drop-off. Deterministic (pure :mod:`product_analytics`); internal-only."""
    return product_analytics.funnel(_analytics_rows(request))


@app.get("/api/analytics/metrics", response_model=AnalyticsResultModel, tags=["meta"],
         summary="[internal] Product metrics (activation, time-to-value, engagement, retention)",
         responses=_ERR_RESPONSES)
def analytics_product_metrics(request: Request) -> dict:
    return product_analytics.product_metrics(_analytics_rows(request))


@app.get("/api/analytics/retention", response_model=AnalyticsResultModel, tags=["meta"],
         summary="[internal] D1/D7 cohort retention", responses=_ERR_RESPONSES)
def analytics_retention(request: Request) -> dict:
    return product_analytics.retention(_analytics_rows(request))


@app.get("/api/analytics/events", response_model=AnalyticsResultModel, tags=["meta"],
         summary="[internal] Event counts by name", responses=_ERR_RESPONSES)
def analytics_event_counts(request: Request) -> dict:
    return product_analytics.event_counts(_analytics_rows(request))


@app.get("/api/report", response_model=HealthReportModel, response_model_exclude_none=True,
         tags=["report"], summary="Information Health Report for a reader", responses=_ERR_RESPONSES)
def report(request: Request,
           user: str | None = Query(None, description="reader id; defaults to the demo reader")) -> dict:
    """Route a report request:

    * **Measured** — a signed-in reader at/above the read threshold: their real report from the
      augmented corpus (`personalize`).
    * **Estimate** — a signed-in reader below the threshold who has onboarded: the Initial
      Information Health Estimate, recomputed server-side from their stored onboarding outlets.
    * **Demo** — a signed-in reader with no usable onboarding, or an anonymous / ``?user=``
      request: the reference reader (unchanged for the frontend and contract tests)."""
    with obs_metrics.timer("report_generate_ms"):     # OBS1: time generation (does not alter it)
        rep, is_exhibit, is_sample = _report_for(_active(), request, user)
    if is_sample:
        rep["sample"] = True
    # RC2.3 — for a signed-in reader viewing their OWN report, reconcile the improvement lifecycle
    # ledger and annotate each improvement with its state. The EXHIBIT report is excluded even when
    # a real uid is present (the exhibit's own request, or a below-threshold reader served the
    # exhibit): it is a frozen showcase — annotating would fork it per viewer AND write ledger rows
    # from exhibit traffic, both of which the demo-account contract forbids (anon == own, and
    # anonymous/viewer traffic never moves the exhibit). Never fails the request.
    uid = _real_uid(request)
    # Per-metric Measurement metadata (ADR-001) — coverage + provenance for Viewpoint / Emotion — is
    # now computed in the engine (measurement.py, via personalize) and attached onto each metric inside
    # `_report_for`; there is no separate report-level annotation to load reads again here.
    # `not is_sample` as well as `not is_exhibit`: a report that is not the reader's own must never
    # write rows into THEIR improvement ledger. The synthetic fallback used to pass this gate.
    if (uid is not None and not is_exhibit and not is_sample
            and rep.get("mode") in ("measured", "estimate") and rep.get("improvements")):
        _annotate_improvement_lifecycle(uid, rep)       # RC2.3: attach lifecycle state
        _rank_improvement_recommendations(uid, rep)     # RC2.4: reorder + suppress (filtering only)
    return rep


def _rank_improvement_recommendations(uid: int, rep: dict) -> None:
    """Feedback-aware ranking/filtering of the generated improvements (RC2.4). Reuses the lifecycle just
    attached plus one cheap feedback-count query; the pure ranking lives in :mod:`improvement_ranking`.
    Generation is untouched — this only reorders and suppresses. Never fails the request."""
    st = state.store
    if st is None:
        return
    try:
        counts = st.recommendation_feedback_counts(uid)
        scores = {m["key"]: m.get("score") for m in (rep.get("metrics") or [])
                  if m.get("available") and m.get("score") is not None}
        rep["improvements"] = improvement_ranking.rank(rep.get("improvements") or [], counts, scores)
    except Exception as exc:                # ranking is auxiliary — never break the report
        error_reporting.report_exception(exc, where="improvement_ranking", userId=uid,
                                         requestId=_request_id.get())


def _annotate_improvement_lifecycle(uid: int, rep: dict) -> None:
    """Reconcile the lifecycle ledger against the report's improvements and attach each rec's state.
    Store I/O only; the state machine itself lives in the pure :mod:`improvement_ledger` leaf."""
    st = state.store
    if st is None:
        return
    improvements = rep.get("improvements") or []
    try:
        ledger = {r["recKey"]: r for r in st.list_improvement_lifecycle(uid)}
        current = [{"recKey": imp["id"], "metric": imp["metric"]} for imp in improvements]
        scores = {m["key"]: m.get("score") for m in (rep.get("metrics") or [])
                  if m.get("available") and m.get("score") is not None}
        now = datetime.now(timezone.utc).isoformat()
        annotated, updates = improvement_ledger.reconcile(current, ledger, scores, now)
        if updates:
            st.save_improvement_lifecycle(uid, list(updates.values()))
        for imp in improvements:
            row = annotated.get(imp["id"])
            if row is not None:
                imp["lifecycle"] = improvement_ledger.public_view(row)
    except Exception as exc:                # lifecycle is auxiliary — never break the report
        error_reporting.report_exception(exc, where="improvement_lifecycle", userId=uid,
                                         requestId=_request_id.get())


def _report_for(active: "corpus_refresh.Active", request: Request,
                user: str | None) -> "tuple[dict, bool, bool]":
    """The report a reader would see — **Measured** (augmented corpus), **Estimate** (stored
    onboarding), or **Demo** (anonymous / no onboarding). Shared by ``GET /api/report`` and the
    dashboard so both speak the exact same report with no duplicated routing or serialisation. Serves
    the whole request from one captured ``active`` bundle (swap-consistent).

    Returns ``(report, is_exhibit, is_sample)`` — two flags, because they are two questions and
    conflating them is what let a fabricated report reach a real reader:

    * ``is_exhibit`` — this report belongs to the seeded demo-exhibit account. It is a frozen
      showcase, so per-reader lifecycle/ranking annotation never applies to it. TRUE even when the
      exhibit is viewing its own report.
    * ``is_sample`` — this report is NOT the requesting reader's own data. True for every fallback:
      the exhibit served to somebody else, the synthetic demo row served to an anonymous visitor,
      AND the synthetic demo row served to a signed-in reader with no reads and no onboarding.

    That last case is the one that shipped a bug. It returned ``is_exhibit=False`` — correctly, it
    is not the exhibit — and an earlier fix keyed the payload marker on ``is_exhibit`` alone, so the
    branch production actually runs (``RWE_DEMO_ACCOUNT`` unset, therefore ``_demo_personal`` is
    ``None``) stayed unmarked. A new beta reader saw "Measured · based on 24 reads" over a synthetic
    reader's politics, and the numbers CHANGED between page loads because the synthetic dataset is
    regenerated on every corpus rebuild."""
    be = active.backend
    uid = _real_uid(request)
    if uid is None:
        # anonymous: the seeded exhibit account's measured report when available (an explicit
        # ?user= selection always wins — the row picker is a deliberate exhibit browser)
        demo = _demo_personal(active) if user is None else None
        if demo is not None:
            return active.personalizer.report(demo), True, True
        return be.report(_anon_row(active, request, user)), False, True
    if active.personalizer.has_measured(uid):
        # The reader's OWN measurement — the only branch that is not a sample.
        return active.personalizer.report(uid), uid == getattr(state, "demo_uid", None), False
    outlets = _require_store().get_onboarding(uid)
    if outlets:
        try:
            rep = be.estimate(outlets)
            # The estimate is computed from outlets (0 reads back the SCORE), but a signed-in reader
            # may have partial reads on the way to Measured. Fill coverage.reads with their real stored
            # count so "N of 5 reads" progress is honest — metadata only, the estimate score is
            # untouched. (The anonymous POST /api/estimate path keeps reads=0: no user, no reads.)
            cov = rep.get("coverage")
            if isinstance(cov, dict):
                cnt = _require_store().count_reads(uid)
                cov["reads"] = cnt
                cov["sufficient"] = cnt >= engine.ESTIMATE_MIN_READS
            return rep, False, False     # an Estimate is theirs: computed from outlets THEY chose
        except ValueError:
            pass
    demo = _demo_personal(active)
    if demo is not None:
        return active.personalizer.report(demo), True, True
    # Synthetic fallback for a signed-in reader with nothing of their own. NOT the exhibit, and NOT
    # theirs — the second flag is the whole reason this tuple grew.
    return be.report(be.demo_user), False, True


@app.get("/api/dashboard", response_model=DashboardModel, response_model_exclude_none=True,
         tags=["report"], summary="Home dashboard summary for a reader", responses=_ERR_RESPONSES)
def dashboard(request: Request,
              user: str | None = Query(None, description="reader id; defaults to the demo reader")) -> dict:
    """The home dashboard, composed from data that already exists: the reader's report (overall +
    the eight metrics, reused verbatim), their saved health trend (report snapshots), and today's
    reading + streak (their stored reads). Same Measured/Estimate/Demo routing as ``/api/report`` —
    no new report serialisation, no algorithm."""
    active = _active()
    rep, _is_exhibit, is_sample = _report_for(active, request, user)
    if is_sample:
        rep["sample"] = True
    st, uid = _require_store(), _real_uid(request)
    reads = st.list_reads(uid) if uid is not None else []
    snaps = st.list_report_snapshots(uid) if uid is not None else []
    # A signed-in reader's stored daily reading goal drives the today-vs-goal progress (their
    # settings always normalise to a goal, so every real user gets one); anonymous/demo has none.
    goal = (settings_service.reading_goal_minutes(st, uid)
            if uid is not None else None)
    # The streak counts DAYS, and a day is local: the same settings read that carries the
    # goal carries the reader's zone (None for anonymous/demo -> UTC, as before).
    tz = settings_service.get(st, uid).get("timeZone") if uid is not None else None
    dash = active.backend.build_dashboard(rep, reads, snaps, goal_minutes=goal, time_zone=tz)
    if rep.get("sample"):
        dash["sample"] = True          # build_dashboard rebuilds the payload; carry the marker over
    return dash


@app.get("/api/outlets", response_model=list[OutletModel], tags=["meta"],
         summary="Publishers available for onboarding selection", responses=_ERR_RESPONSES)
def outlets() -> list:
    return _require_backend().outlets()


@app.get("/api/places/countries", response_model=list[CountryFacetModel], tags=["meta"],
         summary="Countries with located coverage and/or rated publishers", responses=_ERR_RESPONSES)
def place_countries() -> list:
    """The Countries experience's backing list: the union of countries seen in the located catalog
    and countries in the publisher registry, with counted facts for each. Registry-only countries
    carry zero article counts (honest zero, not omission)."""
    facets = {r["country"]: r for r in _require_store().feed_article_country_facets()}
    reg_counts: dict = {}
    for o in outlet_registry.default_registry().outlets():
        # RATED rows only: the web renders this count under "Rated publishers", and locality-only
        # (unrated, NaN-lean) registry rows exist now — counting them would make the label lie.
        if o.country and math.isfinite(o.lean):
            reg_counts[o.country] = reg_counts.get(o.country, 0) + 1
    out = []
    for c in sorted(set(facets) | set(reg_counts)):
        f = facets.get(c, {})
        out.append({"country": c, "articles": int(f.get("articles", 0)),
                    "publishers": int(f.get("publishers", 0)),
                    "registryPublishers": reg_counts.get(c, 0)})
    out.sort(key=lambda r: (-r["articles"], -r["registryPublishers"], r["country"]))
    return out


@app.get("/api/me/geography", response_model=ReaderGeographyModel, tags=["report"],
         summary="The signed-in reader's geographic reading facts (counted, not scored)",
         responses=_ERR_RESPONSES)
def my_geography(request: Request) -> dict:
    """Geographic Diversity readiness surfaced: countries and languages read plus local-vs-
    national exposure, all COUNTED from the reader's stored reads joined to the located catalog
    (location.reader_geography). Explicit unknown buckets; no score is derived here."""
    uid = _require_real_user(request)
    return location.reader_geography(_require_store(), uid)


@app.get("/api/places/publishers", response_model=list[PlacePublisherModel],
         response_model_exclude_none=True, tags=["meta"],
         summary="Publishers by locality (Local News v1)", responses=_ERR_RESPONSES)
def place_publishers(
    country: Optional[str] = Query(None, description="ISO 3166-1 alpha-2 home country"),
    region: Optional[str] = Query(None, description="state / province display name"),
    city: Optional[str] = Query(None, description="home city display name"),
    scope: Optional[str] = Query(None, description="international | national | regional | local | hyperlocal"),
) -> list:
    """Local News v1 — answers exactly one question from curated registry facts: *which publishers
    are local to the selected place?* Publisher locality only (no event locations, no coordinates,
    no inference); registry-backed, so it involves no recommendation engine and no catalog scan.
    Filters are conjunctive; text filters match case-insensitively; no filters = the full rated
    registry."""
    def _norm(v):
        return str(v).strip().lower() if v and str(v).strip() else None

    want_country = location.normalize_country(country) if country else None
    want_region, want_city, want_scope = _norm(region), _norm(city), _norm(scope)
    if scope and want_scope not in location.SCOPES:
        raise HTTPException(status_code=400,
                            detail=f"scope must be one of {', '.join(location.SCOPES)}.")
    out = []
    for o in outlet_registry.default_registry().outlets():
        if want_country and (o.country or "").upper() != want_country:
            continue
        if want_region and _norm(o.region) != want_region:
            continue
        if want_city and _norm(o.city) != want_city:
            continue
        if want_scope and _norm(o.scope) != want_scope:
            continue
        rated = math.isfinite(o.lean)
        out.append({"name": o.canonical, "lean": o.lean if rated else None,
                    "leanBucket": engine._lean_bucket(o.lean) if rated else None,
                    "country": o.country, "region": o.region, "city": o.city, "scope": o.scope})
    out.sort(key=lambda r: (r.get("country") or "~", r["name"].lower()))
    return out


@app.get("/api/publishers/{name}", response_model=PublisherProfileModel,
         response_model_exclude_none=True, tags=["meta"],
         summary="Publisher Intelligence profile (counted catalog + curated registry facts)",
         responses=_ERR_RESPONSES)
def publisher_profile(name: str) -> dict:
    """The profile of ONE publisher: curated registry identity/lean/locality (honest absence when
    the registry doesn't know the outlet — ``rated=false``, null lean, never a fabricated Center)
    plus counted catalog facts (volume/window, topics, languages, event countries, tone-with-n)
    and its recent articles via the same serializer Discover/Search use. ``name`` accepts the
    display name, the stored catalog name, or any registry alias/domain. 404 when neither the
    registry nor the catalog knows the name — a profile is never synthesised from nothing."""
    prof = publisher_service.get_publisher(_require_store(), name)
    if prof is None:
        raise HTTPException(status_code=404, detail="Publisher not found.")
    return prof


# ---- Discover & Stories: read-only exploration over the RSS FeedArticle catalog ---------------- #
# Additive product surface: reshapes the catalog into the existing Article/Story contracts and
# clusters it into events deterministically. No recommender, report, or protected module involved.
@app.get("/api/discover", response_model=DiscoverResponseModel, response_model_exclude_none=True,
         tags=["discover"], summary="Latest catalog articles + topic/publisher/lean filters",
         responses=_ERR_RESPONSES)
def discover_feed(
    topic: Optional[str] = Query(None, description="filter to a topic (facet value)"),
    publisher: Optional[str] = Query(None, description="filter to a publisher (facet value)"),
    lean: Optional[str] = Query(None, description="left | center | right"),
    country: Optional[str] = Query(None, description="articles about events in this ISO 3166-1 "
                                   "alpha-2 country (event geography; publisher home is provenance, not a filter)"),
    # Named `type` for the same reason as on /api/stories: FastAPI takes the query-string name from
    # the parameter name. It shadows the builtin for this function, which never calls it.
    type: Optional[str] = Query(None, description="news | research | community — articles from a "
                                                  "publisher CURATED as that type (outlet registry "
                                                  "`kind`); a publisher the registry does not carry "
                                                  "matches no type"),
    limit: int = Query(60, ge=1, le=200),
) -> dict:
    return discover.list_discover(_require_store(), topic=topic, publisher=publisher,
                                  lean=lean, country=country, story_type=type, limit=limit)


@app.get("/api/stories", response_model=StoriesResponseModel, response_model_exclude_none=True,
         tags=["discover"], summary="News events — FeedArticles clustered into Stories (filtered + paged)",
         responses=_ERR_RESPONSES)
def stories(
    topic: Optional[str] = Query(None, description="exact topic / category"),
    publisher: Optional[str] = Query(None, description="stories that include this publisher"),
    lean: Optional[str] = Query(None, description="stories with coverage on left | center | right"),
    country: Optional[str] = Query(None, description="stories whose EVENT happened in this "
                                                     "ISO 3166-1 alpha-2 country (member consensus; "
                                                     "publisher home never substitutes)"),
    blindspot: Optional[str] = Query(None, description="any | left | center | right — stories with "
                                                       "a DETECTED coverage gap (the thin side); "
                                                       "balanced-or-unknown stories never match"),
    # Named `type` because FastAPI takes the query-string name from the parameter name, and the
    # filter is `?type=`. It shadows the builtin for the length of this function, which never calls
    # it; renaming it would quietly rename the public parameter, so use `alias=` if it ever must.
    type: Optional[str] = Query(None, description="news | research | community — stories with "
                                                  "coverage from a source CURATED as that type "
                                                  "(outlet registry `kind`); a publisher the "
                                                  "registry does not carry matches no type"),
    dateFrom: Optional[str] = Query(None, description="ISO lower bound on publication time"),
    dateTo: Optional[str] = Query(None, description="ISO upper bound on publication time"),
    sort: str = Query("top", description="top | latest | oldest | publishers"),
    limit: int = Query(30, ge=1, le=200),
    offset: int = Query(0, ge=0),
    debug: bool = Query(False, description="include clusterMs + cluster diagnostics"),
) -> dict:
    """News events clustered from the live FeedArticle catalog by the single **Story Service** — the
    same service Discover consumes. Filter by topic/publisher/lean/date, sort, and paginate; each Story
    carries its cross-publisher coverage (each article opening its canonical publisher URL, unchanged
    Read flow) and the nullable `image` contract for future enrichment. Never touches the recommender."""
    debug = debug or os.environ.get("RWE_STORIES_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}
    result = story_service.list_stories(_require_store(), topic=topic, publisher=publisher, lean=lean,
                                        country=country, blindspot=blindspot, story_type=type,
                                        date_from=dateFrom, date_to=dateTo, sort=sort,
                                        limit=limit, offset=offset, debug=debug)
    # Additive Story Intelligence summary (freshness + lifecycle) per story — computed HERE (the API
    # layer consumes Story Intelligence; story_service never does), so cards badge without extra calls.
    for s in result.get("stories", []):
        s.update(story_intelligence.compute_summary(s))
    return result


@app.get("/api/story/{story_id}", response_model=StoryModel, response_model_exclude_none=True,
         tags=["discover"], summary="One clustered Story with full cross-publisher coverage",
         responses=_ERR_RESPONSES)
def story_single(story_id: str) -> dict:
    """One Story by its stable id (anchored to the representative article, so it survives new coverage
    of the same event). Consumes the Story Service — no independent Story construction."""
    s = story_service.get_story(_require_store(), story_id)
    if s is None:
        raise HTTPException(status_code=404, detail="Story not found.")
    return s


@app.get("/api/stories/{story_id}", response_model=StoryModel, response_model_exclude_none=True,
         tags=["discover"], summary="One clustered Story (backward-compatible alias of /api/story/{id})",
         responses=_ERR_RESPONSES)
def story(story_id: str) -> dict:
    """Backward-compatible alias of ``GET /api/story/{story_id}`` (the web detail page still calls the
    plural path). Same Story Service, same result."""
    return story_single(story_id)


@app.get("/api/story/{story_id}/intelligence", response_model=StoryIntelligenceModel,
         response_model_exclude_none=True, tags=["discover"],
         summary="Story Intelligence — freshness, lifecycle, momentum, timeline, new-since-last-visit",
         responses=_ERR_RESPONSES)
def story_intelligence_endpoint(request: Request, story_id: str) -> dict:
    """Deterministic intelligence computed **on top of** the Story (freshness / lifecycle / momentum /
    coverage statistics / expanded timeline / coverage alerts). When a trusted signed-in reader is
    named, ``newSinceLastVisit`` is computed from their existing browser-extension reads (baseline =
    their most recent read of this event); anonymous requests get an empty ``newSinceLastVisit``. This
    is a read-only consumer of the Story Service — it changes no recommendation, report, or read
    tracking. 404 when the event is no longer in the live catalog."""
    st = _require_store()
    uid = _real_uid(request)
    reads = st.list_reads(uid) if uid is not None else None
    intel = story_intelligence.intelligence_for(st, story_id, reads=reads)
    if intel is None:
        raise HTTPException(status_code=404, detail="Story not found.")
    return intel


@app.get("/api/search", response_model=SearchResponseModel, response_model_exclude_none=True,
         tags=["discover"], summary="Live search over the FeedArticle catalog (text + facets + paging)",
         responses=_ERR_RESPONSES)
def search_feed(
    query: Optional[str] = Query(None, description="free text over title / description / publisher / topic"),
    publisher: Optional[str] = Query(None, description="exact publisher"),
    lean: Optional[str] = Query(None, description="left | center | right"),
    topic: Optional[str] = Query(None, description="exact topic / category"),
    dateFrom: Optional[str] = Query(None, description="ISO lower bound on publication time"),
    dateTo: Optional[str] = Query(None, description="ISO upper bound on publication time"),
    source: Optional[str] = Query(None, description="exact source feed URL"),
    country: Optional[str] = Query(None, description="articles about events in this ISO 3166-1 "
                                   "alpha-2 country (event geography; publisher home is provenance, not a filter)"),
    sort: str = Query("newest", description="newest | oldest | publisher | relevance"),
    limit: int = Query(30, ge=1, le=200),
    offset: int = Query(0, ge=0),
    debug: bool = Query(False, description="include queryMs + ftsAvailable diagnostics"),
) -> dict:
    """Search the live RSS catalog directly (index-backed SQL) — never the recommendation engine.
    Results reuse the exact Article contract, so Read Article opens the canonical publisher URL and the
    browser-extension read flow is identical to Discover and recommendations. Timing is surfaced when
    ``debug`` (query param) or ``RWE_SEARCH_DEBUG`` is set."""
    debug = debug or os.environ.get("RWE_SEARCH_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}
    return search.search(_require_store(), query=query, publisher=publisher, lean=lean, topic=topic,
                         date_from=dateFrom, date_to=dateTo, source=source, country=country,
                         sort=sort, limit=limit, offset=offset, debug=debug)


@app.post("/api/estimate", response_model=HealthReportModel, response_model_exclude_none=True,
          tags=["report"], summary="Initial Information Health Estimate from selected outlets",
          responses=_ERR_RESPONSES)
def estimate(req: EstimateRequest) -> dict:
    """The onboarding result: an Information Health *Estimate* computed from the selected
    publishers only (never fabricated reads), explicitly flagged mode='estimate'."""
    try:
        return _require_backend().estimate(req.outlets)
    except ValueError:
        raise HTTPException(status_code=400, detail="Select at least one known publisher.")


@app.post("/api/analyze", response_model=AnalysisModel, tags=["analysis"],
          summary="Analyze a news-article URL (anonymous; reader-enriched when signed in)",
          responses=_ERR_RESPONSES)
def analyze_article(request: Request, req: AnalyzeRequest) -> dict:
    """The Information Health analysis of one article URL — ANALYSIS CONTRACT v1, verbatim from
    the ``article_analyzer`` service (the endpoint exposes; it never reinterprets). Catalog-first
    (a known article reuses its stored scoring), fetchless (a miss is scored in memory from the
    URL + the optional client-supplied ``metadata``), and **zero-write** — analysis alone never
    influences Information Health, recommendations, or analytics. An unparseable URL is a contract
    outcome (``status: "invalid_url"``), not an HTTP error.

    A3 (auth-aware, additive): the analyzer output is unchanged, but a signed-in **measured**
    reader additionally receives the reader-relative ``explanation`` + ``recommendation`` sections
    (``analysis_enrichment``). Anonymous and non-measured readers get the byte-identical A2
    analysis (sections null). Enrichment is read-only — it creates no reads, recommendation events,
    or feedback."""
    md = req.metadata.model_dump() if req.metadata is not None else None
    analysis = article_analyzer.analyze(_require_store(), req.url, metadata=md)
    uid = _real_uid(request)                       # None for anonymous → analysis stays untouched
    if uid is not None:
        sections = analysis_enrichment.enrich_for_reader(state.personalizer, state.store, uid, analysis)
        if sections.get("explanation") is not None or sections.get("recommendation") is not None:
            analysis = {**analysis, **sections}
    return analysis


@app.post("/api/me/onboarding", response_model=HealthReportModel, response_model_exclude_none=True,
          tags=["report"], summary="Persist onboarding choices + first estimate for the signed-in user",
          responses=_ERR_RESPONSES)
def save_my_onboarding(request: Request, req: OnboardingSaveRequest) -> dict:
    """Called after sign-in to save what the user picked during onboarding. The estimate is
    recomputed from the outlets server-side (never trusted from the client) and stored, so the
    user's first result survives to their next visit."""
    uid = _require_real_user(request)
    try:
        estimate = _require_backend().estimate(req.outlets)
    except ValueError:
        raise HTTPException(status_code=400, detail="Select at least one known publisher.")
    st = _require_store()
    st.save_onboarding(uid, req.outlets)
    st.save_report(uid, estimate)
    return estimate


@app.get("/api/me", response_model=MeModel, response_model_exclude_none=True, tags=["meta"],
         summary="The signed-in user's saved onboarding + latest result", responses=_ERR_RESPONSES)
def me(request: Request) -> dict:
    uid = _require_real_user(request)
    st = _require_store()
    outlets = st.get_onboarding(uid)
    return {"onboarding": {"outlets": outlets} if outlets is not None else None,
            "report": st.latest_report(uid),
            "reads": st.count_reads(uid)}


# Distinct readers needed to promote a provisional (extension-created) article into Discover —
# independent readers corroborate it the way a feed re-discovery does. Env-tunable, floor 1.
_PROMOTE_MIN_READERS = max(1, int(os.environ.get("RWE_PROMOTE_MIN_READERS", "2")))


def _catalog_from_extension_read(item: ReadInput, url: str, scored, scorer, st) -> None:
    """Commit 18 — the browser extension as FeedArticle producer #4, through the SAME pipeline as
    RSS/NewsAPI/GDELT: build a :class:`FeedEntry` from the page metadata the extension collected and
    hand it to ``rss_ingest.ingest_entries`` (scoring is a cache hit — the read was scored a moment
    ago with the same canonical key). Born ``provisional`` (hidden from Discover until a feed
    re-discovers it or enough distinct readers corroborate it); Stories/Search/corpus see it at once.
    Best-effort by contract: the Read is already recorded — this must never fail the request."""
    try:
        entry = rss_ingest.FeedEntry(
            url=url,
            title=(item.title or scored.title or ""),
            description=item.description or "",
            published_at=item.publishedAt,
            image=item.image,
            image_source="og:image" if item.image else None,
            publisher_hint=(item.siteName or item.outlet or ""),
            category=item.category,
            language=item.language,          # accepted (privacy allowlist); not yet persisted
            source_type="extension",
            source_provider="Browser extension",
        )
        stats = rss_ingest.ingest_entries([entry], item.siteName or None, "extension", scorer, st)
        st.maybe_promote_feed_article(scored.article_id, _PROMOTE_MIN_READERS)
        # D6: a NEW catalog article from the request path — nudge the poller so the next cycle runs
        # the refresh check even if the feeds bring nothing new (bounds graph latency to one interval).
        if stats.get("new", 0) > 0 and state.refresh is not None:
            state.refresh.mark_catalog_dirty()
    except Exception as e:                   # Case 10: the read is preserved; creation is best-effort
        _log(logging.WARNING, "extension_catalog_failed", url=url[:200], error=type(e).__name__)


@app.post("/api/me/reads", response_model=IngestResultModel, tags=["report"],
          summary="Record reading events for the signed-in user (shared ingestion API)",
          responses=_ERR_RESPONSES)
def add_reads(request: Request, req: ReadsRequest) -> dict:
    """The single ingestion API — paste URL, in-app Read, browser extension. Each read is
    scored once (cached) and recorded idempotently per (user, canonical URL); repeat submits are
    no-ops. An **extension** read additionally feeds the article into the shared FeedArticle
    catalog (producer #4 — see ``_catalog_from_extension_read``); the Read itself is always
    recorded first. Returns coverage so the client knows when enough reads exist for a measured
    report."""
    uid = _require_real_user(request)
    # Bound the batch shape (count + per-read field lengths) before any scoring — the byte cap
    # already bounded the raw body; this rejects an over-count / over-long batch that fits under it.
    batch_error = reqlimits.reads_batch_error(req.reads)
    if batch_error is not None:
        raise HTTPException(status_code=413, detail=batch_error)
    scorer = _require_scorer()
    st = _require_store()
    # Remember where the reader is, so DAY bucketing can be theirs. A streak counts days, and a day
    # is local — the engine has no other way to learn a zone, since notifications compute streaks
    # offline with no request in flight to read a header from. Written only when it actually
    # changes (a settings write per read would be a write on every article opened), and only when
    # it normalises to a resolvable zone, so a nonsense value can never land in stored settings.
    _tz = next((i.timeZone for i in req.reads if i.timeZone), None)
    if _tz:
        _current = settings_service.get(st, uid).get("timeZone")
        if settings_service._normalize_timezone(_tz) not in (None, _current):
            settings_service.update(st, uid, {"timeZone": _tz})
    accepted = duplicates = rejected = 0
    for item in req.reads:
        url = ingest.normalize_url(item.url)
        if not ingest.has_host(url):
            rejected += 1
            continue
        raw = ingest.RawRead(url=url, title=item.title or "", outlet=item.outlet or "",
                             category=item.category or "", political=item.political,
                             read_at=item.observedAt, subtitle=item.subtitle or "",
                             description=item.description or "")
        # Read persistence, split into its two real costs: scoring (cached per article) and the
        # idempotent row write. Observational only — the same two calls, timed.
        _t0 = time.perf_counter()
        scored = ingest.score_with_cache(raw, scorer, st)
        _obs_ms("read_score_ms", _t0)
        _t0 = time.perf_counter()
        _added = st.add_read(uid, scored.article_id, dataclasses.asdict(scored), scored.read_at,
                             read_source=item.readSource, opened_from=item.openedFrom,
                             device=item.device)
        _obs_ms("read_persist_ms", _t0)
        if _added:
            accepted += 1
        else:
            duplicates += 1
        # Commit 18: an extension read also produces/merges the catalog article (read recorded first;
        # runs for duplicates too so a transient failure heals on the next open, and a second reader
        # can promote an article created by the first).
        if (item.readSource or "").strip().lower() == "extension":
            _catalog_from_extension_read(item, url, scored, scorer, st)
    total = st.count_reads(uid)
    return {"accepted": accepted, "duplicates": duplicates, "rejected": rejected,
            "totalReads": total, "threshold": engine.ESTIMATE_MIN_READS,
            "sufficient": total >= engine.ESTIMATE_MIN_READS}


def _continuation_outcome(outcome: str) -> None:
    """Count what the continuation endpoint actually ANSWERED, not merely that it answered.

    ``requests_total|GET /api/me/continuation|2xx`` cannot distinguish an offer from a null: "no
    continuation" is a 200 with a ``null`` body, so a feature that never fires and a feature that
    fires every time produce identical request counters. That ambiguity is why a clean 2xx count was
    read as proof the strip was working when it proved only that the route was reachable.

    One counter splits the two halves of any "it does not appear" report: ``offer`` > 0 means the
    engine is producing offers and the loss is in the browser; ``offer`` == 0 with ``null`` > 0 means
    the loss is server-side and ``audit_continuation`` says which gate. No URL, no user, no payload —
    four labels and their counts."""
    try:
        import obs_metrics
        obs_metrics.incr(f"continuation_result_total|{outcome}")
    except Exception:                  # instrumentation must never break the read path
        pass


@app.get("/api/me/continuation", response_model=ContinuationModel | None, tags=["report"],
         summary="The signed-in user's continuation offer for one article they just opened",
         responses=_ERR_RESPONSES)
def my_continuation(request: Request, url: str = Query(..., max_length=2048)) -> "dict | None":
    """Story Continuation (docs/STORY_CONTINUATION_DESIGN.md): given an article the reader has just
    opened, the one unread account of the SAME event from the opposite side of the rated spectrum —
    or ``null``.

    ``null`` is the ordinary answer and is not an error. The client prefetches this at Read-click so
    the request overlaps the tab switch; a null, a failure, and a timeout are all the same thing to
    it — no strip. Nothing here is written, logged per-URL, or fed back into the model.

    Read-only and cheap: a dict lookup on the TTL-cached story index the recommendations path
    already warms, a scan of that one cluster, and the reader's stored reads. It never builds the
    index inline — a boot-window miss returns ``null`` rather than spending ~24 s of a request
    thread on a click path.

    Off by default (``RWE_STORY_CONTINUATION``): while dark the route exists and always answers
    ``null``, so the client contract can ship and be exercised before the feature is turned on."""
    uid = _require_real_user(request)
    if not story_continuation.enabled():
        _continuation_outcome("disabled")
        return None
    st = _require_store()
    # The reader's own openness picks WHICH opposing outlet wins (nearest / novelty-first /
    # furthest) — never whether one is offered. Settings are the same source the feed's blend plan
    # reads, so the two surfaces cannot disagree about where the slider sits.
    try:
        openness = int(settings_service.get(st, uid).get("politicalOpenness", 50))
    except Exception:
        openness = 50
    try:
        offer = story_continuation.resolve(st, uid, url, openness=openness)
    except Exception:                  # an enhancement, never a hard dependency of the read path
        _log(logging.WARNING, "continuation_resolve_failed", url=url[:200])
        _continuation_outcome("error")
        return None
    _continuation_outcome("offer" if offer else "null")
    return offer


def _attach_published_at(arts: list) -> None:
    """Attach the REAL publication timestamp from the ``FeedArticle`` catalog to already-serialised
    Article dicts (matched by canonical URL). The serialiser emits ``""`` for a real article's
    ``publishedAt`` rather than fabricate one (Commit C4); articles the catalog doesn't know keep
    ``""`` and the UI hides the date segment. Timestamp only — deliberately narrower than the
    recommendation enrichment, so history rows don't sprout images/logos as a side effect."""
    if not arts or state.store is None:
        return
    try:
        urls = [ingest.canonical_url(a["url"]) for a in arts
                if isinstance(a, dict) and a.get("url")]
        by_url = state.store.feed_article_media(urls) if urls else {}
    except Exception:
        return
    for a in arts:
        if isinstance(a, dict) and a.get("url") and not a.get("publishedAt"):
            m = by_url.get(ingest.canonical_url(a["url"]))
            if m and m.get("publishedAt"):
                a["publishedAt"] = m["publishedAt"]


@app.get("/api/me/history", response_model=list[HistoryEntryModel], response_model_exclude_none=True,
         tags=["report"], summary="The signed-in user's reading history (their stored scored reads)",
         responses=_ERR_RESPONSES)
def my_history(request: Request) -> list:
    """The reader's own reading history: every article they've recorded, newest first, rendered as
    the same Article shape used across the product. Reuses the stored, already-scored reads — no
    re-scoring, no augmented model. Same trust boundary as the other /api/me endpoints. Each
    article's ``publishedAt`` is the real publication timestamp when the catalog knows the article
    (Commit C4) — never a synthesized date."""
    uid = _require_real_user(request)
    entries = _require_backend().serialize_history(_require_store().list_reads(uid))
    _attach_published_at([e.get("article") for e in entries])
    return entries


@app.get("/api/me/analytics", response_model=AnalyticsModel, response_model_exclude_none=True,
         tags=["report"], summary="The signed-in user's analytics (trends over their stored data)",
         responses=_ERR_RESPONSES)
def my_analytics(request: Request) -> dict:
    """Analytics for the signed-in reader: score / metric / reading / tone / acceptance trends,
    composed entirely from their stored report snapshots, reads, and recommendation events — no new
    metric or algorithm. Empty series when there's no history yet (an honest empty state)."""
    uid = _require_real_user(request)
    st = _require_store()
    return _require_backend().build_analytics(
        st.report_metric_series(uid), st.list_reads(uid), st.list_rec_events(uid))


@app.get("/api/me/profile", response_model=ProfileModel, response_model_exclude_none=True,
         tags=["meta"], summary="The signed-in user's account profile", responses=_ERR_RESPONSES)
def my_profile(request: Request) -> dict:
    """The reader's profile from persisted data only: identity from their account, streaks from
    their stored reads, the health journey from their saved report snapshots, and the real Saved
    count from persisted saves. Achievements are an honest empty state until that feature exists."""
    uid = _require_real_user(request)
    st = _require_store()
    u = st.get_user(uid)
    user = {"email": u.email, "displayName": u.display_name,
            "createdAt": u.created_at.isoformat() if u.created_at else None}
    return _require_backend().build_profile(user, st.list_reads(uid), st.list_report_snapshots(uid),
                                            saved_count=st.count_saved(uid),
                                            time_zone=settings_service.get(st, uid).get("timeZone"))


@app.get("/api/me/settings", response_model=SettingsModel, tags=["meta"],
         summary="The signed-in user's preferences (server defaults where unset)",
         responses=_ERR_RESPONSES)
def get_my_settings(request: Request) -> dict:
    """The reader's product preferences, with honest server defaults for anything they haven't set.
    Political openness / Recommendation strength shape the reader's own recommendations
    (per-request RWE-B epsilon / RWE-D beta) and the reading goal shapes the dashboard's
    today-vs-goal progress; nothing here ever influences the health report."""
    uid = _require_real_user(request)
    return settings_service.get(_require_store(), uid)


@app.post("/api/me/settings", response_model=SettingsModel, tags=["meta"],
          summary="Update the signed-in user's preferences (partial patch, merged + persisted)",
          responses=_ERR_RESPONSES)
def update_my_settings(request: Request, req: SettingsUpdateModel) -> dict:
    """Merge a (partial) preferences patch over the user's stored preferences, normalise to the
    stable contract, persist, and return the full result. Any client may send only the fields it
    changed. The recommendation sliders take effect on the reader's next recommendations request
    (per-request parameters — no model rebuild, no cache churn); nothing here touches the health
    report."""
    uid = _require_real_user(request)
    st = _require_store()
    patch = req.model_dump(exclude_none=True)
    # ``exclude_none`` cannot tell "field omitted" from "field sent as null", so it drops both —
    # which means a nullable preference can be SET but never CLEARED. For the country preference
    # null is the whole Global state, so the explicitly-sent nulls are re-admitted here, keyed off
    # ``exclude_unset`` (which does distinguish the two). Scoped to the fields whose contract
    # defines null as a value rather than an absence: flipping the whole endpoint to
    # ``exclude_unset`` would change what an explicit null means for every other field at once,
    # which is its own decision and wants its own measurement.
    explicitly_sent = req.model_dump(exclude_unset=True)
    for key in ("recommendationCountry",):
        if key in explicitly_sent and explicitly_sent[key] is None:
            patch[key] = None
    updated = settings_service.update(st, uid, patch)
    # Re-mirror the per-category push flags onto the reader's registered devices. The mirror is a
    # query accelerator for fan-out (store.PushSubscription), and this is the one place preferences
    # change — so syncing here is what keeps it from drifting from the authority it accelerates.
    # Fail-soft: a preference save must never fail because a device row could not be touched.
    try:
        st.sync_push_subscription_flags(uid, (updated.get("notifications") or {}).get("categories"))
    except Exception as e:                          # noqa: BLE001 — see above
        _log(logging.WARNING, "push_flag_sync_failed", error=f"{type(e).__name__}: {e}")
    return updated


def _notification_view(n: dict) -> dict:
    """Map a stored notification (a ``store.list_notifications`` row) to the NotificationModel wire
    shape (snake_case body keys -> camelCase fields)."""
    return {"id": n["id"], "kind": n.get("kind"), "titleKey": n.get("title_key"),
            "payload": n.get("payload") or {}, "createdAt": n.get("created_at"),
            "seenAt": n.get("seenAt"), "gatedBy": n.get("gated_by")}


@app.get("/api/me/notifications", response_model=list[NotificationModel], tags=["meta"],
         summary="The signed-in user's notifications (materialised on read, newest first)",
         responses=_ERR_RESPONSES)
def my_notifications(request: Request, unseenOnly: bool = Query(False),
                     limit: int = Query(50, ge=1, le=200)) -> list:
    """Evaluate the reader's due notifications from their **persisted** state, persist any new ones
    (idempotent — the dedupe ledger suppresses repeats), then return the stored list, newest-first.
    Every kind is gated by the reader's own preferences; nothing here generates a recommendation, a
    report, an explanation, or a coach turn."""
    uid = _require_real_user(request)
    st = _require_store()
    notification_delivery.materialize_notifications(st, uid)          # evaluate-on-fetch (idempotent)
    return [_notification_view(n)
            for n in st.list_notifications(uid, unseen_only=unseenOnly, limit=limit)]


@app.post("/api/me/notifications/{notification_id}/seen", tags=["meta"],
          summary="Mark one of the signed-in user's notifications as seen (idempotent, user-scoped)",
          responses=_ERR_RESPONSES)
def mark_my_notification_seen(request: Request, notification_id: int) -> dict:
    """Idempotent and user-scoped: stamps ``seenAt`` on the notification iff it belongs to the caller
    and isn't already seen. ``changed`` is ``True`` only the first time; another user's id (or an
    already-seen one) returns ``changed=False`` and is never modified."""
    uid = _require_real_user(request)
    st = _require_store()
    return {"ok": True, "changed": bool(st.mark_notification_seen(uid, notification_id))}


# ---- browser push: subscription registration (Phase B1 — nothing here sends) --------------------
def _push_enabled() -> bool:
    """Whether browsers may subscribe at all. Default OFF: subscribing prompts the reader for a
    permission this product has never asked for, so switching it on is an operational act. Read at
    call time, so it is a restart rather than a rebuild — in both directions."""
    return os.environ.get("RWE_PUSH_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}


def _vapid_public_key() -> str:
    """The VAPID public key browsers subscribe against, or ``""`` when unconfigured."""
    return (os.environ.get("RWE_VAPID_PUBLIC_KEY") or "").strip()


def _push_max_devices() -> int:
    """How many devices one reader may hold (default 10). 0 or junk means unbounded.

    A bound rather than a preference: every registered device is a send attempt for every notification
    once Phase B2 exists, so an unbounded table is unbounded fan-out cost — and nothing stops an
    authenticated reader accumulating rows, since every fresh browser profile mints a new endpoint.
    Ten is generous for a person and small enough that the ceiling is never the thing that breaks."""
    raw = os.environ.get("RWE_PUSH_MAX_DEVICES", "")
    return int(raw) if raw.strip().isdigit() else 10


def _endpoint_digest(endpoint: str) -> str:
    """A short, stable, non-reversible handle for a push endpoint — what the logs carry instead of the
    URL.

    An endpoint is a capability: anything that can reach it can, with the right key, address that
    device. It also identifies a specific browser install. Neither belongs in a log that is shipped,
    rotated and read by people, so operational lines carry this digest, which is enough to correlate a
    registration with the deletion of the same device and nothing else."""
    return hashlib.sha256((endpoint or "").encode("utf-8")).hexdigest()[:12]


def _require_push_registration() -> None:
    """Fail-closed gate for **registration only**. 503 rather than 404: the route exists and the
    reason it will not serve is configuration, which is what an operator needs to be told.

    Reads and deletions are deliberately NOT gated. Rolling push back must not strand a reader with a
    registered device they cannot inspect or remove: the rows survive a rollback by design (so
    re-enabling does not ask everyone to opt in again), which is exactly why the way out has to keep
    working while the way in is closed."""
    if not _push_enabled():
        raise HTTPException(status_code=503, detail="Push notifications are not enabled.")
    if not _vapid_public_key():
        raise HTTPException(status_code=503, detail="Push notifications are not configured.")


@app.get("/api/push/config", response_model=PushConfigModel, tags=["meta"],
         summary="Whether browser push is available, and the VAPID public key to subscribe with")
def push_config() -> dict:
    """Unauthenticated on purpose: a browser must know whether to offer push *before* it asks the
    reader for anything, and the only value returned is a public key. Reports ``enabled=False`` when
    the switch is off **or** the key is missing — an operator who set one without the other should see
    the feature reported unavailable rather than half-live."""
    key = _vapid_public_key()
    return {"enabled": bool(_push_enabled() and key), "publicKey": key}


@app.get("/api/me/push/subscriptions", response_model=list[PushSubscriptionModel], tags=["meta"],
         summary="The signed-in reader's registered push devices", responses=_ERR_RESPONSES)
def my_push_subscriptions(request: Request) -> list:
    """One entry per device. Never includes the devices' encryption keys.

    **Not gated by the feature switch** — see :func:`_require_push_registration`. A reader must be able
    to see what is registered in their name even while push is switched off, because the rows outlive
    the rollback."""
    uid = _require_real_user(request)
    return _require_store().list_push_subscriptions(uid)


@app.post("/api/me/push/subscriptions", response_model=PushSubscriptionModel, tags=["meta"],
          summary="Register or refresh a push subscription for the signed-in reader",
          responses=_ERR_RESPONSES)
def create_my_push_subscription(request: Request, req: PushSubscriptionCreate) -> dict:
    """Idempotent on the endpoint, which is what makes this one route serve all three real cases: a
    new device, the same browser re-subscribing after a key rotation or ``pushsubscriptionchange``,
    and the same browser now signed in as a different reader (the endpoint is reassigned — see
    ``store.PushSubscription``).

    The reader's current per-category push preferences are mirrored onto the row as an indexed query
    accelerator for a later fan-out. Settings remain the authority; this copy is never consulted for
    consent on its own.

    The **only** push route the feature switch gates: while push is off no new device may register,
    but the ones already registered stay readable and removable."""
    uid = _require_real_user(request)
    try:
        _require_push_registration()
    except HTTPException:
        # Logged rather than silently 503'd: a burst of these is how an operator learns that browsers
        # are still trying to register against a deployment where push was switched off — either a
        # rollback that readers have not seen yet, or a half-configured deploy.
        _log(logging.INFO, "push_registration_rejected", userId=uid, reason=req.reason,
             enabled=_push_enabled(), configured=bool(_vapid_public_key()))
        raise
    st = _require_store()
    categories = (settings_service.get(st, uid).get("notifications") or {}).get("categories")
    expires_at = None
    if req.expirationTime is not None:
        # The DOM spec gives epoch milliseconds; the store keeps ISO-8601 like every other timestamp
        # here. Out-of-range values are dropped rather than rejected — the field is advisory, and a
        # browser sending nonsense in it should not cost the reader their subscription.
        try:
            expires_at = datetime.fromtimestamp(req.expirationTime / 1000, timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            expires_at = None
    try:
        saved = st.upsert_push_subscription(
            uid, req.endpoint, p256dh=req.p256dh, auth=req.auth,
            content_encoding=req.contentEncoding, expires_at=expires_at,
            user_agent=req.userAgent or (request.headers.get("user-agent") or ""),
            categories=categories, max_devices=_push_max_devices())
    except store.PushOwnershipError:
        # Someone submitted an endpoint that belongs to another reader without the subscription's own
        # secret. A real browser cannot produce this — an endpoint and its keys are minted together —
        # so it is either a leaked endpoint being replayed or a client bug, and both are worth seeing.
        _log(logging.WARNING, "push_subscription_claim_refused", userId=uid,
             endpointDigest=_endpoint_digest(req.endpoint), reason=req.reason)
        raise HTTPException(status_code=409,
                            detail="This subscription belongs to another account.")

    # One line per registration, carrying the digest rather than the endpoint. `outcome` is what makes
    # these worth reading: `created` is a new device, `updated` is the same browser re-registering (a
    # key rotation or `pushsubscriptionchange`), and `reassigned` is a shared browser changing hands —
    # the last is the one nobody should see often, and until now it happened silently.
    outcome = saved.get("outcome")
    _log(logging.WARNING if outcome == "reassigned" else logging.INFO,
         f"push_subscription_{outcome}", userId=uid, subscriptionId=saved.get("id"),
         endpointDigest=_endpoint_digest(req.endpoint), reason=req.reason,
         **({"previousUserId": saved["previousUserId"]} if outcome == "reassigned" else {}))
    # One line per device the cap dropped. Worth its own event rather than a count on the line above:
    # a reader losing a device they did not ask to lose is the cost of the bound, and an operator
    # seeing these regularly should raise RWE_PUSH_MAX_DEVICES rather than wonder why push is flaky.
    for endpoint in saved.get("evicted") or []:
        _log(logging.INFO, "push_subscription_evicted", userId=uid,
             endpointDigest=_endpoint_digest(endpoint), cap=_push_max_devices())
    return saved


@app.delete("/api/me/push/subscriptions", tags=["meta"],
            summary="Unregister a push subscription for the signed-in reader",
            responses=_ERR_RESPONSES)
def delete_my_push_subscription(request: Request, endpoint: str,
                                reason: str = "user") -> dict:
    """Unregister one device. ``endpoint`` is a query parameter because it is a URL and must not sit
    in a path segment (same reason as ``DELETE /api/me/saved``). User-scoped and idempotent:
    ``removed`` is ``False`` for an endpoint that was already gone or was never this reader's.

    **Not gated by the feature switch.** Turning push off must never trap a reader with a device they
    cannot remove — see :func:`_require_push_registration`."""
    uid = _require_real_user(request)
    removed = bool(_require_store().delete_push_subscription(uid, endpoint))
    _log(logging.INFO, "push_subscription_deleted", userId=uid,
         endpointDigest=_endpoint_digest(endpoint), reason=_push_reason(reason), removed=removed)
    return {"ok": True, "removed": removed}


@app.post("/api/me/recommendations/opened", response_model=RecReceptionModel,
          tags=["recommendations"],
          summary="Record that the signed-in user opened a recommended article",
          responses=_ERR_RESPONSES)
def open_recommendation(request: Request, req: RecOpenRequest) -> dict:
    """Record the *reception* of a recommendation the engine already produced: the reader opened it.
    A cross-cutting open is the real-user analogue of the population's cross-cutting click-through,
    so once enough have been surfaced and opened, **Open-Mindedness** populates automatically on the
    Measured report. This reuses the existing recommendation pipeline (no new recommender) and the
    same trust boundary as the other ``/api/me`` endpoints; the cached measured model is invalidated
    so the next report reflects the new reception."""
    uid = _require_real_user(request)
    st = _require_store()
    p = _require_personalizer()
    if not _is_demo_account(uid):           # exhibit account: a successful NO-OP (interaction
        st.record_recommendation_open(uid, req.articleId, cross_cutting=req.crossCutting)
        p.invalidate(uid)                   # next /api/report rebuilds with the new reception
    om = p.openmindedness(uid)
    return {"shownCross": om["shownCross"], "openedCross": om["openedCross"],
            "rate": om["rate"], "threshold": om["minShown"], "active": om["active"]}


@app.post("/api/me/recommendations/feedback", response_model=RecFeedbackAckModel,
          tags=["recommendations"],
          summary="Record the signed-in user's explicit feedback on a recommendation",
          responses=_ERR_RESPONSES)
def recommendation_feedback(request: Request, req: RecFeedbackRequest) -> dict:
    """Record an explicit feedback signal (like / dislike / ignore / read_later) on a recommendation
    the engine already produced — the same trust boundary as the other ``/api/me`` endpoints
    (``_require_real_user`` → 401 for anonymous). **Recorded only** (B1): unlike
    ``/api/me/recommendations/opened``, this feeds no metric — so it deliberately does NOT invalidate
    the cached measured model, is NOT skipped for the exhibit account (there is no metric to keep
    pristine), and drives no recommender, ranking, or personalization path. ``changed`` is ``False``
    for an idempotent repeat of the same signal on the same article."""
    uid = _require_real_user(request)
    changed = _require_store().record_recommendation_feedback(uid, req.articleId, req.feedback)
    return {"ok": True, "feedback": req.feedback, "changed": bool(changed)}


@app.get("/api/me/recommendations/feedback", response_model=list[RecFeedbackEntryModel],
         tags=["recommendations"],
         summary="The signed-in user's recorded recommendation feedback (oldest first)",
         responses=_ERR_RESPONSES)
def my_recommendation_feedback(request: Request) -> list:
    """The reader's own recorded feedback, oldest-first — a read-only projection the web tier uses to
    keep an *ignored* card dismissed across a reload and to render the settings page's "active
    feedback effects" list. Per-user (``_require_real_user`` → 401 anon); it reads only the
    ``rec_feedback`` table and drives no ranking or recommender."""
    uid = _require_real_user(request)
    return _require_store().list_recommendation_feedback(uid)


@app.delete("/api/me/recommendations/feedback", response_model=RecFeedbackRemoveAckModel,
            tags=["recommendations"],
            summary="Remove the signed-in user's feedback on one article (the undo)",
            responses=_ERR_RESPONSES)
def remove_recommendation_feedback(request: Request, req: RecFeedbackRemoveRequest) -> dict:
    """Delete one recorded signal — or, with ``feedback`` omitted, every signal the reader gave
    this article. The undo behind the Tier-2 visible-consequence UI: a ranking consequence the
    reader can see but not retract would be surveillance, so removal is as first-class as
    recording. With ``RWE_REC_FEEDBACK`` on, the next feed simply no longer carries the signal —
    there is no tombstone, because absence IS the intended state."""
    uid = _require_real_user(request)
    removed = _require_store().remove_recommendation_feedback(uid, req.articleId, req.feedback)
    return {"ok": True, "removed": int(removed)}


@app.get("/api/me/recommendations/feedback/effects", response_model=FeedbackEffectsModel,
         tags=["recommendations"],
         summary="The signed-in user's feedback grouped as the effects it has on their feed",
         responses=_ERR_RESPONSES)
def my_feedback_effects(request: Request) -> dict:
    """The settings ledger's view: publisher and topic chips a reader can understand ("seeing
    less from X", "more of Y") plus the dismissed-article list, humanized where the catalog still
    knows the article and honestly marked expired where it does not. Read-only; the grouping is
    computed by the engine from the same FEEDBACK_DIMENSIONS table the rerank consumes, so this
    endpoint cannot claim an effect the feed does not apply."""
    uid = _require_real_user(request)
    st = _require_store()
    rows = st.list_recommendation_feedback(uid)
    import evidence_resolver
    meta = (lambda u: st.get_feed_article(evidence_resolver._canon(str(u)))
            or st.get_feed_article(str(u)))
    return _require_backend().feedback_effects(rows, meta)


@app.post("/api/me/recommendations/improvements/{rec_key}/{event}",
          response_model=ImprovementEventAckModel, tags=["recommendations"],
          summary="Record a lifecycle signal (accept / dismiss / view) on an improvement recommendation",
          responses=_ERR_RESPONSES)
def improvement_lifecycle_event(request: Request, rec_key: str, event: ImprovementEventType) -> dict:
    """Record the signed-in reader's explicit lifecycle signal on an improvement recommendation
    (``accept`` → accepted, ``dismiss`` → dismissed, ``view`` → viewed). Idempotent per
    ``(user, rec_key, event)``. **Recorded only** (RC2.3): it drives no selection, ordering, ranking, or
    report computation — the derived states (completed / expired / superseded / in_progress) are owned
    by the report-time reconciler, not this endpoint. ``rec_key`` is the improvement's stable id
    (``imp_<metric>``); the metric is derived from it."""
    uid = _require_real_user(request)
    ev = _IMPROVEMENT_EVENT_TO_STATE[event]
    metric = rec_key[len("imp_"):] if rec_key.startswith("imp_") else ""
    created = _require_store().record_improvement_lifecycle_event(uid, rec_key, metric, ev)
    return {"ok": True, "recKey": rec_key, "event": ev, "created": bool(created)}


@app.get("/api/me/recommendations/improvements", response_model=list[ImprovementLifecycleEntryModel],
         tags=["recommendations"],
         summary="The signed-in reader's improvement-recommendation lifecycle ledger (oldest first)",
         responses=_ERR_RESPONSES)
def my_improvement_lifecycle(request: Request) -> list:
    """The reader's full improvement-recommendation lifecycle history — one row per recommendation
    (``imp_<metric>``) with its state and transition timestamps, oldest first. A read-only projection
    for the web tier and future evaluation; it reads only the ``improvement_lifecycle`` table and
    drives no ranking or recommender."""
    uid = _require_real_user(request)
    return _require_store().list_improvement_lifecycle(uid)


@app.get("/api/me/recommendations/evaluation", response_model=ReaderEvaluationModel,
         tags=["recommendations"],
         summary="The signed-in reader's improvement-recommendation evaluation (attribution + calibration)",
         responses=_ERR_RESPONSES)
def my_recommendation_evaluation(request: Request) -> dict:
    """Deterministic, read-only evaluation of the reader's own improvement recommendations (RC2.5):
    per recommendation, the lifecycle outcome, the RC2.2 estimated gain, the realized metric change, its
    three-way attribution (recommendation-attributed / organic / population drift), an attribution
    confidence tier, and the calibration error (attributed − estimated). Reuses the lifecycle ledger and
    the report-snapshot history — nothing is recomputed and no ranking is touched."""
    uid = _require_real_user(request)
    st = _require_store()
    rows = st.list_improvement_lifecycle(uid)
    snaps = st.report_eval_snapshots(uid)
    return recommendation_eval.evaluate_reader(snaps, rows)


@app.get("/api/me/saved", response_model=list[SavedArticleModel], tags=["meta"],
         summary="The signed-in user's saved articles (newest first)", responses=_ERR_RESPONSES)
def list_my_saved(request: Request) -> list:
    """The reader's saved articles — the single "Saved" concept (there is no separate bookmark).
    Newest first, each carrying the Article snapshot the reader saw so the list renders without
    re-fetching the catalog. Per-user; touches no recommender, report, corpus, or ingestion path."""
    uid = _require_real_user(request)
    return _require_store().list_saved(uid)


@app.post("/api/me/saved", response_model=SaveResultModel, tags=["meta"],
          summary="Save an article for the signed-in user (idempotent)", responses=_ERR_RESPONSES)
def save_my_article(request: Request, req: SaveArticleRequest) -> dict:
    """Persist a saved article. Idempotent per ``(user, article)`` — saving one already saved is a
    no-op (the duplicate is ignored) that only refreshes the stored snapshot. Returns the resulting
    saved state and the reader's live saved total (the profile's Saved counter)."""
    uid = _require_real_user(request)
    st = _require_store()
    st.save_article(uid, req.articleId, req.article)
    return {"articleId": req.articleId, "saved": True, "savedCount": st.count_saved(uid)}


@app.delete("/api/me/saved", response_model=SaveResultModel, tags=["meta"],
            summary="Remove a saved article for the signed-in user", responses=_ERR_RESPONSES)
def unsave_my_article(request: Request, articleId: str) -> dict:
    """Remove a saved article. ``articleId`` is a query parameter (article ids are URLs, so they must
    not sit in a path segment). Safe when the article isn't saved. Returns the resulting saved state
    and the reader's live saved total."""
    uid = _require_real_user(request)
    st = _require_store()
    st.unsave_article(uid, articleId)
    return {"articleId": articleId, "saved": False, "savedCount": st.count_saved(uid)}


@app.post("/api/me/tokens", response_model=TokenMintModel, tags=["meta"],
          summary="Mint a per-user API token (browser extension)", responses=_ERR_RESPONSES)
def create_my_token(request: Request, req: CreateTokenRequest) -> dict:
    """Create a personal token the browser extension sends to attribute reads to this user.
    The plaintext is returned **once** here (only its hash is stored); show it to the user to
    copy into the extension. Same trust boundary as the other /api/me endpoints."""
    uid = _require_real_user(request)
    token, meta = _require_store().create_token(uid, label=req.label)
    return {"id": meta["id"], "token": token, "label": meta["label"], "createdAt": meta["createdAt"]}


@app.get("/api/me/tokens", response_model=list[TokenModel], tags=["meta"],
         summary="List the signed-in user's API tokens (metadata only)", responses=_ERR_RESPONSES)
def list_my_tokens(request: Request) -> list:
    return _require_store().list_tokens(_require_real_user(request))


@app.delete("/api/me/tokens/{token_id}", tags=["meta"],
            summary="Revoke one of the signed-in user's API tokens", responses=_ERR_RESPONSES)
def revoke_my_token(request: Request, token_id: int) -> dict:
    uid = _require_real_user(request)
    if not _require_store().revoke_token(uid, token_id):
        raise HTTPException(status_code=404, detail="No such token.")
    return {"ok": True}


def _resolver_ctx(active: "corpus_refresh.Active", kind: str, val) -> dict:
    """The per-reader context the Evidence Resolver can honestly claim from (21a.3)."""
    return (active.personalizer.explanation_context(val) if kind == "personal"
            else active.backend.explanation_context(val))


def _attach_explanations(recs: list, active: "corpus_refresh.Active", kind: str, val) -> None:
    """Evidence Resolver post-pass (21a.3) — after the (protected) serializer and the media
    enrichment, replace each rec's templated reason with the ONE explanation its evidence
    licenses (structured ``explanation``; ``reason`` mirrors ``message`` for back-compat).
    Best-effort: a resolver failure leaves the 21a evidence-gated templates in place."""
    try:
        ctx = _resolver_ctx(active, kind, val)
        idx = evidence_resolver.story_index(state.store)
        for r in recs:
            r["explanation"] = evidence_resolver.resolve(r, ctx, idx)
            r["reason"] = r["explanation"]["message"]
    except Exception:
        _log(logging.WARNING, "explanation_resolver_failed")


def _rec_request_params(uid: "int | None") -> "dict | None":
    """The per-request recommender params for a signed-in reader — ONE builder, shared by the
    serving endpoint and the explain observer, so an explanation can never be produced from
    different inputs than the feed it explains (the 21a parity rule, extended to reader state).

    Two layers, both best-effort (a store failure serves the layer below, never an error):

    * **Sliders** — ``rec_params_from_settings``: Political openness → RWE-B epsilon / bridge
      budget, Recommendation strength → RWE-D beta, Interest Intensity, the For You country.
      Untouched sliders ship no key, so the no-preference feed is byte-identical.
    * **Reader state** (X-audit Tier 1; ``RWE_REC_FEEDBACK`` / ``RWE_REC_REPETITION``, both
      default OFF) — ``rec_context.attach_reader_state``: the reader's explicit card feedback
      and recently-surfaced cards, consumed by the engine as bounded rank multipliers."""
    if uid is None or state.store is None:
        return None
    try:
        params = engine.rec_params_from_settings(state.store.get_settings(uid))
    except Exception:
        params = None
    try:
        # The durable-identity seam: stored feedback keys articles by canonical URL (positional
        # ids die at every corpus refresh); the active backend's translator resolves them to the
        # generation actually serving this request. Inside the same try — reader state stays
        # additive, and a translator failure degrades to sliders-only, never to an error.
        params = rec_context.attach_reader_state(
            params, state.store, uid, translate=_require_backend().feedback_id_translator())
    except Exception:                     # reader state is additive — degrade to sliders-only
        _log(logging.WARNING, "rec_reader_state_failed", userId=uid)
    return params


@app.get("/api/recommendations", response_model=list[RecommendationModel],
         response_model_exclude_none=True, tags=["recommendations"],
         summary="RWE recommendations (blended, or a single strategy)", responses=_ERR_RESPONSES)
def recommendations(
    request: Request,
    user: str | None = Query(None),
    strategy: str | None = Query(None, description="rwe-b | rwe-d | adaptive; omit for a blended feed"),
) -> list:
    active = _active()
    kind, val, is_sample = _serve(active, request, user)
    # A SIGNED-IN reader with no reading of their own gets an empty feed rather than the fallback
    # reader's. Recommendations are not a neutral article list: every card carries "this article
    # offers another political perspective", which is a claim about the reader's existing diet.
    # Served to somebody who has read nothing, three "Bridging" cards bridge away from a position
    # they never held — and the response is a plain list, so unlike the report there is nowhere to
    # hang a marker. Nothing is the honest payload; the web renders its empty state from it.
    #
    # Anonymous requests keep the showcase: a visitor browsing the landing experience is not being
    # told these are theirs.
    if is_sample and _real_uid(request) is not None:
        return []
    uid = _real_uid(request)
    params = _rec_request_params(uid)
    # Stage timers (observational only): the handler's own post-passes are on the critical path of
    # every feed, so a breakdown that stopped at `recommendations()` would attribute their cost to
    # the recommender. Each is recorded through obs_metrics, which swallows its own failures.
    _ms: dict = {}

    def _t(name, fn):
        t0 = time.perf_counter()
        try:
            return fn()
        finally:
            d = (time.perf_counter() - t0) * 1000.0
            try:
                _ms[name] = round(d, 1)
                obs_metrics.observe(f"rec_{name}_ms", d)
            except Exception:                   # never let a timer fail a served feed
                pass

    recs = _t("handler_recommend",
              lambda: (active.personalizer.recommendations(val, strategy, params) if kind == "personal"
                       else active.backend.recommendations(val, strategy, params)))
    # attach image (from the live FeedArticle) + publisher logo — additive
    _t("handler_media", lambda: _enrich_rec_media(recs))
    # Evidence Resolver (21a.3) — additive post-pass
    _t("handler_explanations", lambda: _attach_explanations(recs, active, kind, val))
    # A recommendation the engine surfaced to a signed-in reader becomes a measurable event: record
    # which (cross-cutting) recs were shown — the denominator for Open-Mindedness. Best-effort; a
    # recording failure must never fail the recommendations response. No new recommender is created.
    if uid is not None and state.store is not None and not _is_demo_account(uid):
        def _record():
            try:
                state.store.record_recommendations_shown(
                    uid, ((r["article"]["id"], r["crossCutting"]) for r in recs))
            except Exception:
                _log(logging.WARNING, "rec_shown_record_failed", userId=uid)
        _t("handler_record_shown", _record)
    _log(logging.INFO, "rec_handler_stages", ms=_ms, totalMs=round(sum(_ms.values()), 1),
         kind=kind, strategy=strategy or "all", cards=len(recs))
    return recs


def _enrich_rec_media(recs: list) -> None:
    """Attach media, the REAL publication timestamp, and a publisher logo to already-serialised
    recommendation articles **after** the (protected) recommender serialiser has run — so
    recommendation cards can show an image and a truthful date without any change to ``api_server``.
    Everything comes from the live ``FeedArticle`` catalog (recs whose URL matches an ingested
    article); the serialiser no longer fabricates ``publishedAt`` for a real article (Commit C4), so
    this join is the only source of a rec card's date — and it runs BEFORE the Evidence Resolver,
    so story explanations (e.g. follow_up) reason over real timestamps. Best-effort + additive: a
    lookup failure or a rec with no matching article leaves the card image-less / date-less."""
    if not recs or state.store is None:
        return
    arts = [r["article"] for r in recs if isinstance(r.get("article"), dict)]
    # The catalog is keyed by CANONICAL URL while a serialized article carries the original
    # publisher URL (e.g. with "www.") — canonicalize before the join or every lookup misses.
    try:
        urls = [ingest.canonical_url(a["url"]) for a in arts if a.get("url")]
        by_url = state.store.feed_article_media(urls) if urls else {}
    except Exception:
        by_url = {}
    for a in arts:
        m = by_url.get(ingest.canonical_url(a["url"])) if a.get("url") else None
        if m:
            a.update({k: v for k, v in m.items() if v is not None and k != "catalogLean"})
            # ONE lean vocabulary for the UI (docs/LEAN_CONSISTENCY.md F1/F3): the card's lean is
            # the catalog's SCORED registry value — the same number Discover, search, stories and
            # the analyzer serve for this article — never the corpus-internal ranking position
            # (which had CNN at −0.6 on a rec card and −1.0 everywhere else). The crossCutting
            # flag was computed from the position upstream and cannot disagree on sidedness: the
            # scored/position partition is byte-identical (|v| ≥ 0.5), pinned by tests.
            cl = m.get("catalogLean")
            if cl is not None:
                a["lean"] = float(cl)
                a["leanBucket"] = engine._lean_bucket(float(cl))
                a["publisherLean"] = round(float(cl), 2)
        a.update(media.pick_best_logo(a.get("publisher", ""), a.get("url")))


def _v2_message(turn: dict, mid: str) -> dict:
    """The ONE wire mapping from a coach_turn dict to the CoachMessageModel envelope — shared
    by the reply route (M4) and the proactive greeting (M6) so the two can never drift."""
    arts = [c.get("article") for c in turn["cards"] if isinstance(c, dict) and c.get("article")]
    return {"id": mid, "role": "assistant", "content": turn["content"],
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "citations": [{"metric": c["key"], "value": c["value"], "source": c["source"]}
                          for c in turn["citations"][:8]] or None,
            "suggestions": arts[:3] or None,
            "intent": turn["intent"], "resolution": turn["resolution"],
            "followUps": turn["followUps"] or None,
            "cards": turn["cards"] or None,
            "weeklyReview": turn.get("weeklyReview"),
            "echo": turn["echo"]}


def _coach_first_run(uid: int, message: str = "") -> dict:
    """The Guide's turn for a signed-in reader with no reading of their own.

    v1 narrates the FALLBACK reader's report, so a brand-new beta tester was greeted with
    "Echo Chamber Score: 77 · Viewpoint Balance: 84" — somebody else's numbers, under a page footer
    that reads "The Guide narrates engine-computed metrics. It won't invent statistics." It did not
    invent them; it borrowed them, which the reader cannot tell apart.

    Coach v2 never even ran: it is gated on ``kind == "personal"``, and a reader with no reads is
    routed to ``kind == "row"``. So the newest account got the OLDEST path — the one thing a first
    impression should not be.

    NO citations. There is nothing to cite yet, and an empty citation list is the honest form of
    that. Deliberately not routed through Coach v2 either: v2's trigger ladder reads a reader's
    surfaces, and there are none."""
    return {
        "id": f"msg_{engine._stable_int('first-run:' + (message or 'greeting'), uid)}",
        "role": "assistant",
        "content": ("Hi — I'm your Information Health guide. I work from your reading, and there "
                    "isn't any yet, so I have nothing to explain and won't guess. Open a few "
                    "articles from Discover and I'll be able to talk about your topics, sources, "
                    "viewpoints and tone — and show you the numbers behind every answer."),
        "createdAt": datetime.now(timezone.utc).isoformat(),
        # No `followUps`. That is a Coach v2 field, and v2 deliberately did NOT run here — emitting
        # it would make the response look v2-shaped to the client and would (rightly) trip
        # test_coach_api::test_flag_on_below_threshold_reader_stays_v1. The content stands alone,
        # and the web already renders its own suggestion chips.
    }


@app.get("/api/coach", response_model=list[CoachMessageModel], response_model_exclude_none=True,
         tags=["coach"], summary="Coach greeting for a reader", responses=_ERR_RESPONSES)
def coach(request: Request, user: str | None = Query(None)) -> list:
    """v1: the canned grounded greeting. With ``RWE_COACH_V2`` on, the MEASURED path runs the
    M6 deterministic trigger ladder (coach_service.greeting_turn): a stored-goals / weekly-recap
    review through the same coach_turn pipeline, else today's greeting + weakest-metric chips.
    The metric-change and story-update triggers run in SHADOW — logged here, never rendered.
    Flag off is byte-identical to v1 (pinned by tests/test_coach_v1_contract.py)."""
    active = _active()
    kind, val, is_sample = _serve(active, request, user)
    uid = _real_uid(request)
    if is_sample and uid is not None:
        return [_coach_first_run(uid)]
    if kind == "personal" and coach_service.coach_v2_enabled():
        pers = active.personalizer
        g = coach_service.greeting_turn(pers, pers.store, val)
        turn = g["turn"]
        _log(logging.INFO, "coach_greeting", trigger=g["trigger"],
             intent=(turn or {}).get("intent"), tools=(turn or {}).get("toolsRun") or [],
             fallback=(turn or {}).get("fallback"), shadow=g["shadow"], ms=g["ms"])
        if turn is not None:
            return [_v2_message(turn, f"msg_{engine._stable_int('greeting', val)}")]
        msg = g["base"]
        msg["followUps"] = g["followUps"] or None
        return [msg]
    if kind == "personal":
        return active.personalizer.coach_greeting(val)
    return active.backend.coach_greeting(val)


@app.post("/api/coach", response_model=CoachMessageModel, response_model_exclude_none=True,
          tags=["coach"], summary="Send a message; get a grounded reply", responses=_ERR_RESPONSES)
def coach_reply(request: Request, req: CoachRequest) -> dict:
    """v1: the grounded narrator (_serialize_coach_reply). With ``RWE_COACH_V2`` enabled, the
    MEASURED (personal) path routes through the intent-routed coach (examples/coach_service);
    the demo path stays v1 regardless — Coach v2 needs a real reader's Personalizer surfaces.
    Flag off is byte-identical to v1 (pinned by tests/test_coach_v1_contract.py)."""
    active = _active()
    kind, val, is_sample = _serve(active, request, req.user)
    uid = _real_uid(request)
    if is_sample and uid is not None:
        # Same reasoning as the greeting, and it matters more here: the reader has just ASKED
        # "How balanced is my reading?" and v1 would have answered from the fallback reader's facts.
        return _coach_first_run(uid, req.message or "")
    if kind == "personal" and coach_service.coach_v2_enabled():
        pers = active.personalizer
        turn = coach_service.coach_turn(pers, pers.store, val,
                                        message=req.message or "", echo=req.echo)
        # read-only structured observability for every v2 turn (low overhead: one log line)
        _log(logging.INFO, "coach_turn", intent=turn["intent"], resolution=turn["resolution"],
             tools=turn["toolsRun"], failures=[g["tool"] for g in turn["gaps"]],
             fallback=turn["fallback"], ms=turn["ms"])
        return _v2_message(turn, f"msg_{engine._stable_int(req.message or '', val)}")
    if kind == "personal":
        return active.personalizer.coach_reply(val, req.message or "")
    return active.backend.coach_reply(val, req.message or "")


@app.post("/api/internal/users", response_model=UserModel, tags=["meta"],
          summary="Upsert a user by third-party identity (server-to-server)",
          responses=_ERR_RESPONSES)
def upsert_user(request: Request, req: UpsertUserRequest) -> dict:
    """Called by the web tier on sign-in: map a provider account to a stable engine user id,
    creating the user on first sight. Idempotent. Requires the internal secret when set.

    ``refreshProfile=False`` resolves the id without touching an existing user's stored profile;
    the response still reports what is STORED, not what was submitted."""
    _require_trusted(request)
    u = _require_store().upsert_user_by_identity(
        req.provider, req.providerAccountId, email=req.email, display_name=req.displayName,
        refresh_profile=req.refreshProfile)
    return {"userId": u.id, "email": u.email, "displayName": u.display_name}


@app.get("/api/internal/users/{user_id}", response_model=UserModel, tags=["meta"],
         summary="Resolve a user by engine id", responses=_ERR_RESPONSES)
def read_user(request: Request, user_id: int) -> dict:
    _require_trusted(request)
    u = _require_store().get_user(user_id)
    if u is None:
        raise HTTPException(status_code=404, detail="No such user.")
    return {"userId": u.id, "email": u.email, "displayName": u.display_name}


@app.get("/api/internal/storage", response_model=StorageStatusModel, tags=["meta"],
         summary="Storage / durability diagnostics (server-to-server)", responses=_ERR_RESPONSES)
def storage_status(request: Request) -> dict:
    """Ops diagnostics for the durable store: the active database (redacted), whether it is
    ephemeral, the SQLite journal mode + pragmas actually in effect, on-disk size, a fast
    corruption probe (``PRAGMA quick_check``), and backup status. Trusted endpoint — requires the
    internal secret in production, like the other ``/api/internal/*`` routes."""
    _require_trusted(request)
    return _require_store().storage_diagnostics()


@app.get("/api/internal/feeds", response_model=list[FeedHealthModel], tags=["meta"],
         summary="Per-feed RSS polling health + quality (server-to-server)", responses=_ERR_RESPONSES)
def feed_health(request: Request) -> list:
    """Ops diagnostics for the RSS poller: per-feed availability (healthy / consecutive failures /
    last success + failure / latency), quality (imported / duplicate / rejected / missing-metadata /
    newest + oldest article dates), and **freshness** (``stale`` + ``newestAgeDays`` vs
    ``staleThresholdDays``). Staleness is a separate axis from availability — a feed can be ``healthy``
    (polling fine) yet ``stale`` (only serving old content, e.g. a retired/frozen feed). **Observational
    only** — feed health, staleness included, never stops polling a feed and never influences corpus
    construction, article export, or recommendation serving. Trusted endpoint — requires the internal
    secret in production, like the other ``/api/internal/*`` routes."""
    _require_trusted(request)
    warn_after = _int_env("RWE_FEED_WARN_AFTER")
    if warn_after is None:
        warn_after = 1
    out = []
    for r in feed_service.annotate_staleness(_require_store().list_feed_health()):
        status = ("unhealthy" if not r["healthy"]
                  else "degraded" if r["consecutiveFailures"] >= warn_after else "healthy")
        out.append({**r, "status": status})
    return out


@app.get("/api/internal/corpus", response_model=CorpusValidationModel, tags=["meta"],
         summary="Candidate corpus validation + diagnostics (server-to-server)", responses=_ERR_RESPONSES)
def corpus_validation_status(request: Request) -> dict:
    """Ops diagnostics for the corpus-validation gate: builds a publisher-capped candidate from the
    current ``FeedArticle`` catalog, measures it (totals, publisher + political distribution,
    freshness, duplicates, missing metadata, healthy/unhealthy feeds), and reports whether it *would*
    be **eligible** to activate, with every failure + warning reason and the thresholds in effect.

    **Validation only** — this endpoint activates nothing, rebuilds no ``Backend``, and performs no
    hot swap; it is a read-only probe over ``FeedArticle`` + ``feed_health`` and never touches the
    live recommendation corpus. Trusted endpoint — requires the internal secret in production, like the
    other ``/api/internal/*`` routes."""
    _require_trusted(request)
    return corpus_validation.evaluate(_require_store()).to_dict()


@app.get("/api/internal/refresh", response_model=RefreshStatusModel, tags=["meta"],
         summary="Hot recommendation-corpus refresh / activation state (server-to-server)",
         responses=_ERR_RESPONSES)
def refresh_status(request: Request) -> dict:
    """Ops diagnostics for the atomic hot refresh: the active corpus generation + signature, the last
    candidate signature seen, build/activation timings, refresh count, current source, and the last
    success / failure / validation timestamps. **Read-only** — it reports activation state and triggers
    nothing (the poller drives refreshes). Trusted endpoint — requires the internal secret in
    production, like the other ``/api/internal/*`` routes."""
    _require_trusted(request)
    if state.refresh is None:
        raise HTTPException(status_code=503, detail="The engine is still starting up.")
    return state.refresh.snapshot()


@app.get("/api/internal/recommendations/explain", tags=["meta"],
         summary="Explain a reader's recommendation feed (evidence + trace; developer tool)",
         responses=_ERR_RESPONSES)
def explain_recommendations_internal(
    request: Request,
    user: str | None = Query(None, description="demo row (anonymous path), as on /api/recommendations"),
    strategy: str | None = Query(None, description="rwe-b | rwe-d | adaptive; omit for the blended feed"),
    article: str | None = Query(None, description='ask "why was/wasn\'t THIS article (id or URL) in the feed?"'),
) -> dict:
    """Read-only explainability observer (Commit 21a): the exact feed ``/api/recommendations``
    would serve for the same caller, decorated with the evidence that produced it — the real
    pipeline trace (reads → catalog join → graph → per-strategy models with the hyperparameters
    actually in effect → slices → dedup), per-recommendation evidence (per-strategy score/rank,
    match band, cross-cutting derivation, measured outlet familiarity, item-degree percentile,
    two-hop connectivity), and a truthful exclusion verdict for ``article``. Same resolution and
    slider-mapped params as the serving endpoint, so explanations can never describe a different
    feed than the one served. Trusted endpoint, like the other ``/api/internal/*`` routes."""
    _require_trusted(request)
    active = _active()
    kind, val, _is_sample = _serve(active, request, user)
    uid = _real_uid(request)
    params = _rec_request_params(uid)     # the serving endpoint's own builder — parity by construction
    out = (active.personalizer.explain(val, strategy, params, article) if kind == "personal"
           else active.backend.explain_recommendations(val, strategy, params, article))
    # 21a.3: the SAME resolved explanation the card shows, attached per evidence entry — the
    # drawer, dev tooling, and the validation pipeline read one sentence from one resolver.
    try:
        ctx = _resolver_ctx(active, kind, val)
        idx = evidence_resolver.story_index(state.store)
        for r in out.get("recommendations") or []:
            pseudo = {"article": {"id": r.get("articleId"), "url": r.get("url"),
                                  "publisher": r.get("publisher"), "topic": r.get("topic"),
                                  "lean": r.get("lean"), "publishedAt": r.get("publishedAt")},
                      "crossCutting": bool((r.get("crossCutting") or {}).get("value")),
                      "strategy": r.get("chosenBy")}
            r["explanation"] = evidence_resolver.resolve(pseudo, ctx, idx)
    except Exception:
        _log(logging.WARNING, "explanation_resolver_failed", where="explain")
    # Debugging identity (21a.2): serving is deterministic given (corpus generation, model
    # version, params), so this id names the exact recommendation instance a report is about.
    from datetime import datetime, timezone
    out["corpusGeneration"] = int(active.generation)
    who = f"u{uid}" if kind == "personal" else f"demo{val}"
    out["explainId"] = (f"rec_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
                        f"_{who}_g{active.generation}")
    return out


class UnsubscribeRequest(BaseModel):
    token: str


class UnsubscribeAck(BaseModel):
    ok: bool
    unsubscribed: bool


@app.post("/api/unsubscribe", response_model=UnsubscribeAck, tags=["meta"],
          summary="Honour an unsubscribe link from an email (NO session required)",
          responses=_ERR_RESPONSES)
def unsubscribe_email(req: UnsubscribeRequest) -> dict:
    """Turn off weekly digest email for the account a signed token names.

    **Deliberately unauthenticated**, and that is the point: a reader in a mail client, on a device
    they never signed in on, two years after the fact, must still be able to make it stop. Asking
    them to log in first is what gets mail reported as spam instead of unsubscribed. The token is
    an HMAC over (purpose, user id) — it authorises exactly one category off and nothing else, it
    cannot be forged without the server secret, and it is compared in constant time.

    Always answers 200. A bad or expired-looking token reports ``unsubscribed: false`` rather than
    404: an endpoint that distinguishes "no such user" from "wrong signature" is an endpoint that
    enumerates users."""
    uid = email_delivery.unsubscribe(_require_store(), req.token)
    return {"ok": True, "unsubscribed": uid is not None}


class EmailRunAck(BaseModel):
    considered: int
    sent: int
    retried: int
    bounced: int
    skipped: dict


@app.post("/api/internal/email/digest-run", response_model=EmailRunAck, tags=["meta"],
          summary="Run one weekly-digest email pass (internal; scheduled from cron)",
          responses=_ERR_RESPONSES)
def run_digest_emails(request: Request) -> dict:
    """One delivery pass, driven by the host's scheduler rather than a thread in the API process.

    Cron rather than a background thread because a weekly job that runs on a timer inside a
    process that redeploys several times a day either fires repeatedly or not at all, depending on
    when the restart lands. The ledger makes a repeated call harmless — every send is claimed — so
    an at-least-once trigger is exactly the right shape for it."""
    _require_trusted(request)
    return email_delivery.run_once(_require_store()).as_dict()


@app.post("/api/internal/resolve-token", response_model=ResolveTokenModel, tags=["meta"],
          summary="Exchange a per-user API token for its engine user id (server-to-server)",
          responses=_ERR_RESPONSES)
def resolve_token(request: Request, req: ResolveTokenRequest) -> dict:
    """The web tier calls this to attribute an extension's reads to the right user: it presents
    the token, gets back the engine user id, then forwards the read on the *existing*
    /api/me/reads path with the internal secret. Keeps the token out of the engine's public
    surface and reuses the one ingestion pipeline. Requires the internal secret when configured."""
    _require_trusted(request)
    dev = _dev_token()
    if dev and req.token == dev:                 # dev single-identity: always the demo reader
        return {"userId": _ensure_demo_user()}
    uid = _require_store().resolve_token(req.token)
    if uid is None:
        raise HTTPException(status_code=401, detail="Invalid or unknown token.")
    return {"userId": uid}


class DevDiagnosticsModel(BaseModel):
    devMode: bool
    sessionUid: int | None = None       # the signed-in web user (from the trusted X-IH-User-Id)
    extensionUid: int | None = None     # the user a supplied ?token= resolves to
    match: bool                         # session and extension name the SAME engine user
    tokenValid: bool                    # the supplied token resolved to a user
    readCount: int                      # reads stored for that user (what Reading History shows)
    devToken: str | None = None         # the fixed demo token to paste into the extension (dev only)


@app.get("/api/dev/diagnostics", response_model=DevDiagnosticsModel, tags=["meta"],
         summary="[dev only] Reading-sync identity diagnostics", responses=_ERR_RESPONSES)
def dev_diagnostics(request: Request, token: str | None = None) -> dict:
    """Development-only: the one place to see *why* extension reads aren't appearing in Reading
    History. Reports the signed-in session user, the user a supplied extension ``token`` resolves to,
    whether they match, token validity, and the read count for that user. Returns **404 in
    production** so it never exists on a real deployment."""
    if _production():
        raise HTTPException(status_code=404, detail="Not found.")
    st = _require_store()
    session_uid = _real_uid(request)
    ext_uid = None
    if token:
        dev = _dev_token()
        ext_uid = _ensure_demo_user() if (dev and token == dev) else st.resolve_token(token)
    who = session_uid if session_uid is not None else ext_uid
    return {
        "devMode": True,
        "sessionUid": session_uid,
        "extensionUid": ext_uid,
        "match": session_uid is not None and ext_uid is not None and session_uid == ext_uid,
        "tokenValid": ext_uid is not None,
        "readCount": st.count_reads(who) if who is not None else 0,
        "devToken": _dev_token(),
    }


@app.get("/api/dev/recommendations/quality", response_model=CohortRuleQualityModel, tags=["meta"],
         summary="[dev only] Cohort recommendation quality & calibration, per rule", responses=_ERR_RESPONSES)
def dev_recommendation_quality(request: Request) -> dict:
    """Development/operator-only: the deterministic per-rule (per-metric) quality and calibration across
    **all** readers who have improvement-recommendation history — acceptance / completion / dismissal /
    abandonment rates, mean realized improvement, mean estimated impact, and the mean calibration error
    with its over/under direction (RC2.5). Read-only over the lifecycle ledger + report snapshots;
    returns **404 in production** (an internal analytics view, like ``/api/dev/diagnostics``)."""
    if _production():
        raise HTTPException(status_code=404, detail="Not found.")
    st = _require_store()
    uids = st.list_users_with_improvement_lifecycle()
    all_evals, all_rows = [], []
    for uid in uids:
        rows = st.list_improvement_lifecycle(uid)
        snaps = st.report_eval_snapshots(uid)
        for row in rows:
            all_evals.append(recommendation_eval.evaluate_recommendation(snaps, row))
            all_rows.append(row)
    return {"cohortSize": len(uids),
            "ruleQuality": recommendation_eval.rule_quality(all_evals, all_rows)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", choices=sorted(engine.BUILTIN_PROFILES), default=None)
    ap.add_argument("--npz", default=None)
    ap.add_argument("--qbias", default=None)
    ap.add_argument("--register-csv", default=None)
    ap.add_argument("--emotion-csv", default=None)
    ap.add_argument("--behaviors", default=None)
    ap.add_argument("--lean-tau", default=None)
    ap.add_argument("--domain", choices=["news", "reddit"], default=None)
    ap.add_argument("--provider", choices=["gemini", "anthropic"], default=None)
    ap.add_argument("--n-users", default=None)
    ap.add_argument("--max-items", default=None)
    ap.add_argument("--seed", default=None)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()

    # Bridge CLI flags to the env the lifespan reads, so CLI and env behave identically.
    cli_env = {
        "RWE_PROFILE": args.profile, "RWE_NPZ": args.npz, "RWE_QBIAS": args.qbias,
        "RWE_REGISTER_CSV": args.register_csv, "RWE_EMOTION_CSV": args.emotion_csv,
        "RWE_BEHAVIORS": args.behaviors, "RWE_LEAN_TAU": args.lean_tau, "RWE_DOMAIN": args.domain,
        "RWE_PROVIDER": args.provider, "RWE_N_USERS": args.n_users, "RWE_MAX_ITEMS": args.max_items,
        "RWE_SEED": args.seed,
    }
    for k, v in cli_env.items():
        if v is not None:
            os.environ[k] = str(v)

    # Pre-flight: a clean, immediate exit with human-readable diagnostics for the common
    # `python examples/api_fastapi.py` path if production mode is mis-configured (the lifespan
    # enforces the same for the `uvicorn examples.api_fastapi:app` entrypoint that bypasses main()).
    config_errors = _config_errors()
    if config_errors:
        bar = "=" * 74
        print(f"\n{bar}\nFATAL: refusing to start — invalid configuration "
              f"({len(config_errors)} problem(s)):\n", file=sys.stderr)
        for err in config_errors:
            print(f"  ✗ {err}\n", file=sys.stderr)
        print(f"Fix the above, or unset RWE_ENV / RWE_REQUIRE_AUTH for local development.\n{bar}\n",
              file=sys.stderr)
        raise SystemExit(2)

    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
