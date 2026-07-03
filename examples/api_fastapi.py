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
import os
import sys
from contextlib import asynccontextmanager
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # import sibling api_server
import api_server as engine   # Backend, DatasetProfile, resolve_profile, BUILTIN_PROFILES

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel


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


state = _State()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Build the engine (dataset + compute + recommender inputs) once, reuse per request.
    provider = os.environ.get("RWE_PROVIDER", "anthropic")
    state.backend = engine.Backend(_profile_from_env(), provider=provider)
    yield
    state.backend = None


app = FastAPI(
    title="Information Health API",
    version="1.0.0",
    summary="Real Information Health engine — report, recommendations, and AI coach.",
    description=(
        "JSON over the deterministic Information Health Report (`health_report`), the RWE "
        "recommender family, and the grounded AI coach (`narrate_report`). Responses match "
        "the frontend domain contract (`web/types/domain.ts`)."
    ),
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


class CoachRequest(BaseModel):
    message: str = ""
    user: str | None = None


def _resolve(user: str | None) -> int:
    return state.backend.resolve_user({"user": [user]} if user is not None else {})


@app.get("/api/health")
def health() -> dict:
    return state.backend.health()


@app.get("/api/report")
def report(user: str | None = Query(None, description="reader id; defaults to the demo reader")) -> dict:
    return state.backend.report(_resolve(user))


@app.get("/api/recommendations")
def recommendations(
    user: str | None = Query(None),
    strategy: str | None = Query(None, description="rwe-b | rwe-d | adaptive; omit for a blended feed"),
) -> list:
    return state.backend.recommendations(_resolve(user), strategy)


@app.get("/api/coach")
def coach(user: str | None = Query(None)) -> list:
    return state.backend.coach_greeting(_resolve(user))


@app.post("/api/coach")
def coach_reply(req: CoachRequest) -> dict:
    u = int(req.user) if (req.user or "").lstrip("-").isdigit() else state.backend.demo_user
    return state.backend.coach_reply(u, req.message or "")


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
