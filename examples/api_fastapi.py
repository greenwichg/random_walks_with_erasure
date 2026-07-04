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
    backend: "engine.Backend | None" = None
    store: "store.Store | None" = None
    scorer: "ingest.Scorer | None" = None


state = _State()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Build the engine (dataset + compute + recommender inputs) once, reuse per request.
    provider = os.environ.get("RWE_PROVIDER", "anthropic")
    be = engine.Backend(_profile_from_env(), provider=provider)
    state.backend = be
    state.store = store.Store()
    state.scorer = ingest.Scorer()
    _log(logging.INFO, "startup", profile=be.profile.name, demoUser=be.demo_user,
         eligibleReaders=int(len(be.eligible)), db=state.store.url)
    yield
    state.backend = None
    state.store = None
    state.scorer = None


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
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


@app.middleware("http")
async def _observability(request: Request, call_next):
    """Tag each request with an id, time it, and emit one structured log line."""
    rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
    _request_id.set(rid)
    start = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        response.headers["X-Request-ID"] = rid
        return response
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
               405: "method_not_allowed", 422: "invalid_request", 503: "engine_unavailable"}


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


class ArticleModel(BaseModel):
    # `register` shadows a BaseModel attribute, so hold it under an alias and serialise
    # it back to the wire key "register" (FastAPI responds by_alias).
    model_config = ConfigDict(populate_by_name=True)

    id: str
    headline: str
    publisher: str
    publisherLean: float
    topic: str
    lean: float
    leanBucket: str
    confidence: float
    emotion: EmotionShareModel
    dominantEmotion: str
    register_: str = Field(alias="register")
    publishedAt: str
    readingMinutes: int


class RecommendationModel(BaseModel):
    article: ArticleModel
    reason: str
    strategy: str
    healthImpact: int
    helpsMetric: str
    crossCutting: bool


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


class ReadsRequest(BaseModel):
    reads: list[ReadInput] = []


class IngestResultModel(BaseModel):
    accepted: int
    duplicates: int
    rejected: int
    totalReads: int
    threshold: int
    sufficient: bool


def _require_backend() -> "engine.Backend":
    if state.backend is None:
        raise HTTPException(status_code=503, detail="The engine is still starting up.")
    return state.backend


def _resolve(user: str | None) -> int:
    return _require_backend().resolve_user({"user": [user]} if user is not None else {})


_REAL_USER_HEADER = "x-ih-user-id"
_AUTH_HEADER = "x-ih-auth"


def _internal_secret() -> "str | None":
    """The shared secret the web tier signs internal calls with, or None if unset.

    When unset (local dev) the engine trusts the caller so the app runs with no extra
    configuration; set it in any shared/production deployment to authenticate the web tier.
    Read per-request so it can be rotated without a restart."""
    return os.environ.get("RWE_INTERNAL_SECRET") or None


def _trusted(request: Request) -> bool:
    """Whether a request is from the trusted web tier: always true when no secret is
    configured, otherwise only when it carries the matching X-IH-Auth header."""
    secret = _internal_secret()
    return secret is None or request.headers.get(_AUTH_HEADER) == secret


def _require_trusted(request: Request) -> None:
    """Reject an internal call lacking the configured secret (a no-op when unset)."""
    if not _trusted(request):
        raise HTTPException(status_code=401, detail="Missing or invalid internal credentials.")


def _require_store() -> "store.Store":
    if state.store is None:
        raise HTTPException(status_code=503, detail="The engine is still starting up.")
    return state.store


def _require_scorer() -> "ingest.Scorer":
    if state.scorer is None:
        raise HTTPException(status_code=503, detail="The engine is still starting up.")
    return state.scorer


def _resolve_request(request: Request, user: str | None) -> int:
    """Corpus row to serve for a request.

    A signed-in reader is identified by the ``X-IH-User-Id`` header the web tier sets after
    Google sign-in; when it names a real user we currently serve the reference reader (their
    own *measured* report arrives in Milestone B, once reading data exists). Absent or unknown,
    the existing ``?user=`` selector applies — unchanged for the frontend and contract tests."""
    be = _require_backend()
    raw = request.headers.get(_REAL_USER_HEADER)
    if (raw and raw.lstrip("-").isdigit() and _trusted(request) and state.store is not None
            and state.store.get_user(int(raw)) is not None):
        return be.demo_user
    return be.resolve_user({"user": [user]} if user is not None else {})


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
    return _require_backend().health()


@app.get("/api/report", response_model=HealthReportModel, response_model_exclude_none=True,
         tags=["report"], summary="Information Health Report for a reader", responses=_ERR_RESPONSES)
def report(request: Request,
           user: str | None = Query(None, description="reader id; defaults to the demo reader")) -> dict:
    return _require_backend().report(_resolve_request(request, user))


@app.get("/api/outlets", response_model=list[OutletModel], tags=["meta"],
         summary="Publishers available for onboarding selection", responses=_ERR_RESPONSES)
def outlets() -> list:
    return _require_backend().outlets()


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


@app.post("/api/me/reads", response_model=IngestResultModel, tags=["report"],
          summary="Record reading events for the signed-in user (shared ingestion API)",
          responses=_ERR_RESPONSES)
def add_reads(request: Request, req: ReadsRequest) -> dict:
    """The single ingestion API — paste URL now, browser extension + RSS later. Each read is
    scored once (cached) and recorded idempotently per (user, canonical URL); repeat submits are
    no-ops. Returns coverage so the client knows when enough reads exist for a measured report."""
    uid = _require_real_user(request)
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
                             read_at=item.observedAt)
        scored = ingest.score_with_cache(raw, scorer, st)
        if st.add_read(uid, scored.article_id, dataclasses.asdict(scored), scored.read_at):
            accepted += 1
        else:
            duplicates += 1
    total = st.count_reads(uid)
    return {"accepted": accepted, "duplicates": duplicates, "rejected": rejected,
            "totalReads": total, "threshold": engine.ESTIMATE_MIN_READS,
            "sufficient": total >= engine.ESTIMATE_MIN_READS}


@app.get("/api/recommendations", response_model=list[RecommendationModel],
         response_model_exclude_none=True, tags=["recommendations"],
         summary="RWE recommendations (blended, or a single strategy)", responses=_ERR_RESPONSES)
def recommendations(
    request: Request,
    user: str | None = Query(None),
    strategy: str | None = Query(None, description="rwe-b | rwe-d | adaptive; omit for a blended feed"),
) -> list:
    return _require_backend().recommendations(_resolve_request(request, user), strategy)


@app.get("/api/coach", response_model=list[CoachMessageModel], response_model_exclude_none=True,
         tags=["coach"], summary="Coach greeting for a reader", responses=_ERR_RESPONSES)
def coach(request: Request, user: str | None = Query(None)) -> list:
    return _require_backend().coach_greeting(_resolve_request(request, user))


@app.post("/api/coach", response_model=CoachMessageModel, response_model_exclude_none=True,
          tags=["coach"], summary="Send a message; get a grounded reply", responses=_ERR_RESPONSES)
def coach_reply(request: Request, req: CoachRequest) -> dict:
    return _require_backend().coach_reply(_resolve_request(request, req.user), req.message or "")


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

    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
