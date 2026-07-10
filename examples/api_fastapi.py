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
import json
import logging
import os
import sys
import time
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # import sibling api_server
import api_server as engine   # Backend, DatasetProfile, resolve_profile, BUILTIN_PROFILES
import store                  # beta persistence layer (users + identities)
import ingest                 # reading-event scorer + cache (Milestone C)
import enrich                 # headline enrichment (register + emotion) behind ingest.Enricher
import personalize            # per-user augmented Measured report / recs / coach
import ratelimit              # dependency-free token-bucket rate limiter (Private Alpha hardening)
import reqlimits              # request-body size / batch-shape limits (Private Alpha hardening)
import feed_source            # optional: source the recommender catalog from the RSS FeedArticle store
import feed_service           # optional: background RSS polling that keeps the FeedArticle catalog fresh
import sources                # pluggable multi-source ingestion (RSS + NewsAPI + GDELT) via adapters
import rss_ingest             # FeedEntry + ingest_entries — the one producer path (Commit 18: + extension)
import corpus_validation      # corpus-eligibility gate (validation only; no activation / no hot swap)
import corpus_refresh         # atomic hot activation of a validated corpus (background Backend swap)
import discover               # Discover: product-layer exploration over the FeedArticle catalog
import search                 # live full-text + faceted search over the FeedArticle catalog (Commit 6)
import story_service          # the single owner of Story construction (Discover + Stories consume it)
import story_intelligence     # deterministic intelligence computed ON TOP of Story objects (Commit 10)
import media                  # centralised media + publisher-logo selection (rec enrichment, Commit 9)

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
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


def _profile_from_env() -> "engine.DatasetProfile":
    """Resolve the dataset profile from environment only (deployment config), reusing the
    exact same resolution as the CLI so behaviour is identical."""
    ns = SimpleNamespace(
        profile=None, npz=None, qbias=None, register_csv=None, emotion_csv=None,
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

    @property
    def backend(self) -> "engine.Backend | None":
        return self.active.backend if self.active is not None else None

    @property
    def personalizer(self) -> "personalize.Personalizer | None":
        return self.active.personalizer if self.active is not None else None


state = _State()


def _configure_recs_source(st) -> "str | None":
    """Opt-in live recommendation source. When ``RWE_RECS_SOURCE=feed`` and the RSS ``FeedArticle``
    catalog is large enough, export it to a qbias-format CSV and point the engine's corpus at it
    **authoritatively** — ``RWE_QBIAS`` at the CSV *and* ``RWE_PROFILE=qbias`` — then return the CSV
    path. The feed catalog *is* the corpus, so the profile must become ``qbias`` even if
    ``RWE_PROFILE`` was already set (e.g. the docker/compose default ``synthetic``); otherwise the
    engine would build the pre-set corpus and silently ignore the feed CSV (and no publisher URL
    would ever reach a recommendation). Returns ``None`` — keep the existing corpus, touch no env —
    when the source is disabled or the catalog is below ``RWE_FEED_MIN_ARTICLES`` (so enabling the
    flag before any RSS ingest stays safe). No recommendation algorithm is affected; this only
    selects the article source."""
    if not feed_source.enabled():
        return None
    feed_csv = feed_source.prepare(st)
    if not feed_csv:
        _log(logging.WARNING, "recs_source_fallback", source="feed",
             reason="catalog below RWE_FEED_MIN_ARTICLES", articles=st.count_feed_articles())
        return None
    os.environ["RWE_QBIAS"] = feed_csv
    os.environ["RWE_PROFILE"] = "qbias"   # authoritative: the feed catalog is now the corpus
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
    state.scorer = ingest.Scorer(enricher=enrich.make_enricher())   # baseline register+emotion
    state.limiter = ratelimit.RateLimiter()          # per-process token-bucket limiter
    # Live recommendation source (opt-in): build the recommender's catalog from the RSS FeedArticle
    # store instead of the static qbias CSV / synthetic generator. Additive — it just points RWE_QBIAS
    # at a FeedArticle-derived qbias-format CSV, so the ENGINE and the protected simulator are unchanged
    # and the recommender operates over live articles exactly as over qbias. Falls back (keeps the
    # existing corpus) when the catalog is too small.
    feed_csv = _configure_recs_source(st)
    be = engine.Backend(_profile_from_env(), provider=provider)
    if feed_csv:
        # Map the corpus item ids (Q{i}) back to their FeedArticle publisher URLs, so recommendations
        # carry the real openable URL (the Honest URL Pass-through). Additive; no algorithm change.
        be.attach_url_resolver(feed_source.load_url_map(feed_csv))
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
    registry = sources.default_registry()
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
    yield
    if state.poller is not None:
        state.poller.stop()          # graceful: signal + join the current cycle
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
        resp = _body_limit_check(request) or _rate_limit_check(request)
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
        _log(logging.INFO if status < 500 else logging.ERROR, "request",
             method=request.method, path=request.url.path, status=status,
             durationMs=round((time.perf_counter() - start) * 1000, 1))


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
    return _error(422, "invalid_request", "One or more request parameters are invalid.")


@app.exception_handler(StarletteHTTPException)
async def _on_http_error(request: Request, exc: StarletteHTTPException):
    code = _HTTP_CODES.get(exc.status_code, "http_error")
    return _error(exc.status_code, code, str(exc.detail))


@app.exception_handler(Exception)
async def _on_unhandled_error(request: Request, exc: Exception):
    # Log the failure (type + path) for correlation; never leak internals to the client.
    _log(logging.ERROR, "unhandled_exception", path=request.url.path, error=type(exc).__name__)
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


class MetricModel(BaseModel):
    key: str
    score: int
    delta: int
    band: str
    benchmark: Optional[int] = None
    raw: Optional[RawModel] = None


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


class ImprovementModel(BaseModel):
    id: str
    title: str
    detail: str
    metric: str
    impact: int


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


class NotificationPrefsModel(BaseModel):
    recommendations: bool
    weeklyDigest: bool
    streakReminders: bool
    blindSpotAlerts: bool


class PrivacyPrefsModel(BaseModel):
    shareAnonymizedMetrics: bool
    personalizedAds: bool


class SettingsModel(BaseModel):
    theme: str
    language: str
    politicalOpenness: int
    recommendationStrength: int
    readingGoalMinutes: int
    weeklyReport: bool
    monthlyReport: bool
    notifications: NotificationPrefsModel
    privacy: PrivacyPrefsModel


# Update model — every field optional so any client can PATCH a subset; the engine merges it over
# the stored preferences and returns the full, normalised SettingsModel.
class NotificationPrefsUpdate(BaseModel):
    recommendations: bool | None = None
    weeklyDigest: bool | None = None
    streakReminders: bool | None = None
    blindSpotAlerts: bool | None = None


class PrivacyPrefsUpdate(BaseModel):
    shareAnonymizedMetrics: bool | None = None
    personalizedAds: bool | None = None


class SettingsUpdateModel(BaseModel):
    theme: str | None = None
    language: str | None = None
    politicalOpenness: int | None = None
    recommendationStrength: int | None = None
    readingGoalMinutes: int | None = None
    weeklyReport: bool | None = None
    monthlyReport: bool | None = None
    notifications: NotificationPrefsUpdate | None = None
    privacy: PrivacyPrefsUpdate | None = None


class ArticleModel(BaseModel):
    # `register` shadows a BaseModel attribute, so hold it under an alias and serialise
    # it back to the wire key "register" (FastAPI responds by_alias).
    model_config = ConfigDict(populate_by_name=True)

    id: str
    headline: str
    publisher: str
    publisherLean: float
    topic: str
    # the canonical publisher URL — present only when verified (live feed source / a real read),
    # omitted otherwise (response_model_exclude_none); the frontend opens it for the Read flow.
    url: Optional[str] = None
    # short summary — populated for Discover/Stories (from the feed); omitted for recommendations.
    description: Optional[str] = None
    lean: float
    leanBucket: str
    confidence: float
    emotion: EmotionShareModel
    dominantEmotion: str
    register_: str = Field(alias="register")
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
    publisherLogo: Optional[str] = None
    publisherLogoDark: Optional[str] = None
    publisherLogoSource: Optional[str] = None


class RecommendationModel(BaseModel):
    article: ArticleModel
    reason: str
    strategy: str
    healthImpact: int
    helpsMetric: str
    crossCutting: bool


class HistoryEntryModel(BaseModel):
    id: str
    article: ArticleModel
    readAt: str | None = None
    readingMinutes: int
    completed: bool
    readSource: str | None = None     # additive: app | extension | <import> (omitted when unknown)
    openedFrom: str | None = None     # additive: the in-app surface a read came from


# ---- Discover & Stories (FeedArticle-powered exploration; product layer) ----
class DiscoverResponseModel(BaseModel):
    articles: list[ArticleModel]
    topics: list[str]        # facet values for the topic filter
    publishers: list[str]    # facet values for the publisher filter


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
    lean: float
    leanBucket: str
    register_: str = Field(alias="register")
    emotion: EmotionShareModel
    url: Optional[str] = None
    publishedAt: str


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
    # Story Intelligence summary (Commit 10) — attached by the API layer so cards can badge without an
    # extra request. story_service stays untouched. Omitted (exclude_none) if not computed.
    freshness: Optional[dict[str, Any]] = None    # {band, score}
    lifecycle: Optional[str] = None


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


class CitationModel(BaseModel):
    metric: str
    value: int


class CoachMessageModel(BaseModel):
    id: str
    role: str
    content: str
    createdAt: str
    citations: Optional[list[CitationModel]] = None
    suggestions: Optional[list[ArticleModel]] = None


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


class CoachRequest(BaseModel):
    message: str = ""
    user: str | None = None


class UpsertUserRequest(BaseModel):
    provider: str
    providerAccountId: str
    email: str | None = None
    displayName: str | None = None


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


class EstimateRequest(BaseModel):
    outlets: list[str] = []


class OnboardingSaveRequest(BaseModel):
    outlets: list[str] = []


class MeModel(BaseModel):
    onboarding: Optional[dict] = None
    report: Optional[HealthReportModel] = None


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


def _serve(active: "corpus_refresh.Active", request: Request, user: str | None):
    """Routing for recommendations + coach (which have no Estimate form). Reads the single captured
    ``active`` bundle so the whole request stays on one corpus generation across a hot swap.

    Returns ``("personal", uid)`` when the signed-in reader has crossed the read threshold — the
    request is served from their augmented corpus — else ``("row", row)``: the demo reader for a
    below-threshold real user (the existing behaviour), or the ``?user=`` selection for an
    anonymous request (unchanged for the frontend and contract tests)."""
    uid = _real_uid(request)
    if uid is not None and active.personalizer.has_measured(uid):
        return "personal", uid
    if uid is not None:
        return "row", active.backend.demo_user
    return "row", _anon_row(active, request, user)


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
    return _report_for(_active(), request, user)


def _report_for(active: "corpus_refresh.Active", request: Request, user: str | None) -> dict:
    """The report a reader would see — **Measured** (augmented corpus), **Estimate** (stored
    onboarding), or **Demo** (anonymous / no onboarding). Shared by ``GET /api/report`` and the
    dashboard so both speak the exact same report with no duplicated routing or serialisation. Serves
    the whole request from one captured ``active`` bundle (swap-consistent)."""
    be = active.backend
    uid = _real_uid(request)
    if uid is None:
        return be.report(_anon_row(active, request, user))
    if active.personalizer.has_measured(uid):
        return active.personalizer.report(uid)
    outlets = _require_store().get_onboarding(uid)
    if outlets:
        try:
            return be.estimate(outlets)
        except ValueError:
            pass
    return be.report(be.demo_user)


@app.get("/api/dashboard", response_model=DashboardModel, response_model_exclude_none=True,
         tags=["report"], summary="Home dashboard summary for a reader", responses=_ERR_RESPONSES)
def dashboard(request: Request,
              user: str | None = Query(None, description="reader id; defaults to the demo reader")) -> dict:
    """The home dashboard, composed from data that already exists: the reader's report (overall +
    the eight metrics, reused verbatim), their saved health trend (report snapshots), and today's
    reading + streak (their stored reads). Same Measured/Estimate/Demo routing as ``/api/report`` —
    no new report serialisation, no algorithm."""
    active = _active()
    rep = _report_for(active, request, user)
    st, uid = _require_store(), _real_uid(request)
    reads = st.list_reads(uid) if uid is not None else []
    snaps = st.list_report_snapshots(uid) if uid is not None else []
    # A signed-in reader's stored daily reading goal drives the today-vs-goal progress (their
    # settings always normalise to a goal, so every real user gets one); anonymous/demo has none.
    goal = (engine.normalize_settings(st.get_settings(uid))["readingGoalMinutes"]
            if uid is not None else None)
    return active.backend.build_dashboard(rep, reads, snaps, goal_minutes=goal)


@app.get("/api/outlets", response_model=list[OutletModel], tags=["meta"],
         summary="Publishers available for onboarding selection", responses=_ERR_RESPONSES)
def outlets() -> list:
    return _require_backend().outlets()


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
    limit: int = Query(60, ge=1, le=200),
) -> dict:
    return discover.list_discover(_require_store(), topic=topic, publisher=publisher,
                                  lean=lean, limit=limit)


@app.get("/api/stories", response_model=StoriesResponseModel, response_model_exclude_none=True,
         tags=["discover"], summary="News events — FeedArticles clustered into Stories (filtered + paged)",
         responses=_ERR_RESPONSES)
def stories(
    topic: Optional[str] = Query(None, description="exact topic / category"),
    publisher: Optional[str] = Query(None, description="stories that include this publisher"),
    lean: Optional[str] = Query(None, description="stories with coverage on left | center | right"),
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
                         date_from=dateFrom, date_to=dateTo, source=source, sort=sort,
                         limit=limit, offset=offset, debug=debug)


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
            "report": st.latest_report(uid)}


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
        scored = ingest.score_with_cache(raw, scorer, st)
        if st.add_read(uid, scored.article_id, dataclasses.asdict(scored), scored.read_at,
                       read_source=item.readSource, opened_from=item.openedFrom, device=item.device):
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


@app.get("/api/me/history", response_model=list[HistoryEntryModel], response_model_exclude_none=True,
         tags=["report"], summary="The signed-in user's reading history (their stored scored reads)",
         responses=_ERR_RESPONSES)
def my_history(request: Request) -> list:
    """The reader's own reading history: every article they've recorded, newest first, rendered as
    the same Article shape used across the product. Reuses the stored, already-scored reads — no
    re-scoring, no augmented model. Same trust boundary as the other /api/me endpoints."""
    uid = _require_real_user(request)
    return _require_backend().serialize_history(_require_store().list_reads(uid))


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
                                            saved_count=st.count_saved(uid))


@app.get("/api/me/settings", response_model=SettingsModel, tags=["meta"],
         summary="The signed-in user's preferences (server defaults where unset)",
         responses=_ERR_RESPONSES)
def get_my_settings(request: Request) -> dict:
    """The reader's product preferences, with honest server defaults for anything they haven't set.
    Political openness / Recommendation strength shape the reader's own recommendations
    (per-request RWE-B epsilon / RWE-D beta) and the reading goal shapes the dashboard's
    today-vs-goal progress; nothing here ever influences the health report."""
    uid = _require_real_user(request)
    return engine.normalize_settings(_require_store().get_settings(uid))


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
    updated = engine.normalize_settings(st.get_settings(uid), req.model_dump(exclude_none=True))
    st.save_settings(uid, updated)
    return updated


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
    st.record_recommendation_open(uid, req.articleId, cross_cutting=req.crossCutting)
    p = _require_personalizer()
    p.invalidate(uid)                       # next /api/report rebuilds with the new reception
    om = p.openmindedness(uid)
    return {"shownCross": om["shownCross"], "openedCross": om["openedCross"],
            "rate": om["rate"], "threshold": om["minShown"], "active": om["active"]}


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


@app.get("/api/recommendations", response_model=list[RecommendationModel],
         response_model_exclude_none=True, tags=["recommendations"],
         summary="RWE recommendations (blended, or a single strategy)", responses=_ERR_RESPONSES)
def recommendations(
    request: Request,
    user: str | None = Query(None),
    strategy: str | None = Query(None, description="rwe-b | rwe-d | adaptive; omit for a blended feed"),
) -> list:
    active = _active()
    kind, val = _serve(active, request, user)
    # A signed-in reader's preference sliders map to per-request recommender hyperparameters
    # (Political openness → RWE-B epsilon, Recommendation strength → RWE-D beta). Untouched
    # sliders map to None — the shared default stack — so demo/anonymous requests and readers
    # who never moved a slider get exactly the pre-slider feed. Best-effort: a settings read
    # failure serves defaults, never an error.
    uid = _real_uid(request)
    params = None
    if uid is not None and state.store is not None:
        try:
            params = engine.rec_params_from_settings(state.store.get_settings(uid))
        except Exception:
            params = None
    recs = (active.personalizer.recommendations(val, strategy, params) if kind == "personal"
            else active.backend.recommendations(val, strategy, params))
    _enrich_rec_media(recs)     # attach image (from the live FeedArticle) + publisher logo — additive
    # A recommendation the engine surfaced to a signed-in reader becomes a measurable event: record
    # which (cross-cutting) recs were shown — the denominator for Open-Mindedness. Best-effort; a
    # recording failure must never fail the recommendations response. No new recommender is created.
    if uid is not None and state.store is not None:
        try:
            state.store.record_recommendations_shown(
                uid, ((r["article"]["id"], r["crossCutting"]) for r in recs))
        except Exception:
            _log(logging.WARNING, "rec_shown_record_failed", userId=uid)
    return recs


def _enrich_rec_media(recs: list) -> None:
    """Attach media + a publisher logo to already-serialised recommendation articles **after** the
    (protected) recommender serialiser has run — so recommendation cards can show an image without any
    change to ``api_server``. The image comes from the live ``FeedArticle`` catalog (recs whose URL
    matches an ingested article); the logo is derived from the publisher URL. Best-effort + additive:
    a lookup failure or a rec with no matching article simply leaves it image-less (text-only card)."""
    if not recs or state.store is None:
        return
    try:
        urls = [r["article"].get("url") for r in recs if r.get("article", {}).get("url")]
        by_url = state.store.feed_article_media(urls) if urls else {}
    except Exception:
        by_url = {}
    for r in recs:
        a = r.get("article")
        if not isinstance(a, dict):
            continue
        m = by_url.get(a.get("url"))
        if m:
            a.update({k: v for k, v in m.items() if v is not None})
        a.update(media.pick_best_logo(a.get("publisher", ""), a.get("url")))


@app.get("/api/coach", response_model=list[CoachMessageModel], response_model_exclude_none=True,
         tags=["coach"], summary="Coach greeting for a reader", responses=_ERR_RESPONSES)
def coach(request: Request, user: str | None = Query(None)) -> list:
    active = _active()
    kind, val = _serve(active, request, user)
    if kind == "personal":
        return active.personalizer.coach_greeting(val)
    return active.backend.coach_greeting(val)


@app.post("/api/coach", response_model=CoachMessageModel, response_model_exclude_none=True,
          tags=["coach"], summary="Send a message; get a grounded reply", responses=_ERR_RESPONSES)
def coach_reply(request: Request, req: CoachRequest) -> dict:
    active = _active()
    kind, val = _serve(active, request, req.user)
    if kind == "personal":
        return active.personalizer.coach_reply(val, req.message or "")
    return active.backend.coach_reply(val, req.message or "")


@app.post("/api/internal/users", response_model=UserModel, tags=["meta"],
          summary="Upsert a user by third-party identity (server-to-server)",
          responses=_ERR_RESPONSES)
def upsert_user(request: Request, req: UpsertUserRequest) -> dict:
    """Called by the web tier on sign-in: map a provider account to a stable engine user id,
    creating the user on first sight. Idempotent. Requires the internal secret when set."""
    _require_trusted(request)
    u = _require_store().upsert_user_by_identity(
        req.provider, req.providerAccountId, email=req.email, display_name=req.displayName)
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
