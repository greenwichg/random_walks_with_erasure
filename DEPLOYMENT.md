# Deployment Guide

How to run the complete Information Health system — the **Next.js web app** and the
**FastAPI engine** — locally, switch between dataset profiles, and package it for
deployment. No feature configuration here; just running and shipping what exists.

## Architecture in one picture

```
Browser ──▶ Next.js web app ──▶ /api/* route handlers ──▶ FastAPI engine ──▶ real algorithms
 :3000       (BFF + proxy)        (lib/backend.ts)          :8000            (rwe, health_report,
                                                            /docs             narrate_report)
```

- The web app never calls the engine from the browser; its own `/api/*` routes proxy to it.
- In **development** those routes fall back to mock JSON if the engine is down; in
  **production** (`NODE_ENV=production`) they return a typed `503` instead of fabricated data.
- The engine picks its data source from a **dataset profile** (below) — configuration only.

## Prerequisites

- **Python** ≥ 3.9 (3.11 recommended) for the engine.
- **Node** ≥ 18.17 (20/22 fine) for the web app.
- Optional: **Docker** (24+) with Compose v2 for the packaged deployment.
- Optional: **`ANTHROPIC_API_KEY`** (or `GEMINI_API_KEY`) for the live AI‑coach narrative.
  Without a key the coach still works, using a deterministic grounded reply.

---

## Run locally (two processes)

### 1 · Engine (FastAPI)

```bash
# from the repo root
pip install -e ".[serve]"            # rwe + fastapi + uvicorn (once)
python examples/api_fastapi.py       # synthetic data, no downloads → http://127.0.0.1:8000
```

Verify: `curl -s localhost:8000/api/health` → `{"ok": true, ...}`, and open
**http://localhost:8000/docs** for the interactive OpenAPI docs.

### 2 · Web app (Next.js)

```bash
cd web
npm install
echo "RWE_BACKEND_URL=http://127.0.0.1:8000" > .env.local
npm run dev                          # → http://localhost:3000
```

Open **http://localhost:3000**. The Report, Recommendations, and AI Coach pages now serve
real engine output; the remaining pages serve mock data until Phase 3.

> Stop the engine and the dev app transparently falls back to mock — handy for frontend
> work. Set `RWE_ALLOW_MOCK_FALLBACK=false` to force the production behaviour locally.

---

## Dataset profiles

The engine switches data sources by **configuration only** — a CLI flag or an environment
variable, no code change. `synthetic` needs nothing; the others need a data file.

| Profile | Data source | Run it | Data prep |
| --- | --- | --- | --- |
| `synthetic` *(default)* | the repo's own simulator | `python examples/api_fastapi.py` | none |
| `qbias` | synthetic readers over a real Qbias AllSides catalog | `… --profile qbias --qbias allsides_balanced_news.csv` | download the Qbias CSV |
| `mind` | an ingested MIND release (news) | `… --profile mind --npz mind.npz` | `ingest_mind.py` (below) |
| `politosphere` | an ingested Reddit Politosphere (reddit) | `… --profile politosphere --npz politosphere.npz` | `ingest_politosphere.py` (below) |

Equivalent via environment (what deployments use):

```bash
RWE_PROFILE=mind RWE_NPZ=mind.npz python examples/api_fastapi.py
```

**Producing the `.npz` files** (MIND and Politosphere are external, licensed datasets — you
supply the raw data):

```bash
# MIND → mind.npz  (see the ingest_mind.py header for ideology/lean options)
python examples/ingest_mind.py --mind-dir data/MINDsmall_train --out mind.npz

# Reddit Politosphere → politosphere.npz
python examples/ingest_politosphere.py --comments-dir data/politosphere --out politosphere.npz
```

The web app is **identical** across profiles — it only ever sees the JSON contract, so
switching data never touches the frontend.

---

## Configuration reference

**Engine** (`examples/api_fastapi.py`) — CLI flag or env var (CLI > env > profile default):

| Env var | Flag | Meaning |
| --- | --- | --- |
| `RWE_PROFILE` | `--profile` | `synthetic` \| `qbias` \| `mind` \| `politosphere` |
| `RWE_NPZ` | `--npz` | dataset file for `mind` / `politosphere` |
| `RWE_QBIAS` | `--qbias` | Qbias AllSides CSV for the `qbias` profile |
| `RWE_DOMAIN` | `--domain` | `news` \| `reddit` (set by the profile) |
| `RWE_REGISTER_CSV` / `RWE_EMOTION_CSV` / `RWE_BEHAVIORS` | `--register-csv` / `--emotion-csv` / `--behaviors` | optional enrichment for `.npz` profiles |
| `RWE_LEAN_TAU` | `--lean-tau` | lean‑axis centre half‑width (default = engine `LEAN_TAU`) |
| `RWE_N_USERS` / `RWE_MAX_ITEMS` / `RWE_SEED` | `--n-users` / `--max-items` / `--seed` | synthetic corpus size + seed |
| `RWE_PROVIDER` | `--provider` | coach LLM provider: `anthropic` \| `gemini` |
| `RWE_LOG_LEVEL` | — | log level for structured logs (default `INFO`) |
| `RWE_INTERNAL_SECRET` | — | shared secret authenticating the web tier's server-to-server calls (unset = trust local callers) |
| `ANTHROPIC_API_KEY` / `GEMINI_API_KEY` | — | enable the live coach narrative |

**Web app** (`web/.env.local`):

| Env var | Default | Meaning |
| --- | --- | --- |
| `RWE_BACKEND_URL` | `http://127.0.0.1:8000` | engine origin the proxy calls |
| `RWE_BACKEND_TIMEOUT_MS` | `6000` | proxy timeout before fallback/error |
| `RWE_ALLOW_MOCK_FALLBACK` | on in dev, off in prod | allow mock when the engine is down |
| `NODE_ENV` | — | `production` disables the mock fallback |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | *(empty)* | Google OAuth client for sign-in (NextAuth) |
| `NEXTAUTH_SECRET` / `NEXTAUTH_URL` | *(empty)* | session-JWT signing secret + this app's canonical URL |
| `RWE_INTERNAL_SECRET` | *(empty)* | shared secret sent as `X-IH-Auth`; must match the engine's |
| `NEXT_PUBLIC_API_BASE_URL` | *(empty)* | advanced: call a different API origin from the browser |

---

## Production build without Docker

```bash
# Engine — a real ASGI server; add workers to scale out (each worker builds its own
# in-memory engine at startup, so size memory accordingly).
pip install -e ".[serve]"
python examples/api_fastapi.py --host 0.0.0.0 --port 8000
#   or, multi-worker:  uvicorn examples.api_fastapi:app --host 0.0.0.0 --port 8000 --workers 4

# Web — production Next.js build (mock fallback OFF)
cd web && npm ci && npm run build
NODE_ENV=production RWE_BACKEND_URL=https://engine.internal npm start
```

---

## Package with Docker

Ready-to-use artifacts live in `deploy/`:

- `deploy/Dockerfile.api` — the FastAPI engine (`python:3.11-slim`, `pip install ".[serve]"`).
- `deploy/Dockerfile.web` — the Next.js app (`node:20-slim`, `npm ci && npm run build`).
- `deploy/docker-compose.yml` — both services wired together, `web → api`.
- `.dockerignore` — keeps the build context small.

```bash
# from the repo root — builds both images and starts the system
docker compose -f deploy/docker-compose.yml up --build
# → web  http://localhost:3000
# → api  http://localhost:8000/docs
```

**Switch datasets in Compose** — uncomment the relevant lines in `docker-compose.yml`
(`RWE_PROFILE`, `RWE_NPZ`/`RWE_QBIAS`) and the `./data:/data:ro` volume, then place your
`.npz`/CSV under `./data`. No image rebuild is needed to change profiles.

Build the images individually if you deploy them separately (e.g. to a registry):

```bash
docker build -f deploy/Dockerfile.api -t ih-api .
docker build -f deploy/Dockerfile.web -t ih-web .
```

For a single-host or platform deployment (Fly, Render, Cloud Run, ECS): deploy the two
images as two services, set `RWE_BACKEND_URL` on the web service to the engine's URL, set
`NODE_ENV=production`, and mount/attach any dataset the engine profile needs.

---

## Health & observability

- **Readiness / liveness:** `GET /api/health` → `200` with the active profile and reader
  counts (the Compose file uses it as a healthcheck; wire it to your platform's probes).
- **Logs:** the engine emits one structured JSON line per request
  (`{"event":"request","method","path","status","durationMs","requestId"}`) plus a `startup`
  line. Set verbosity with `RWE_LOG_LEVEL`.
- **Tracing:** every response carries an `X-Request-ID` (echoing an inbound one if provided),
  and every error body includes `error.requestId` — so a user‑visible failure maps to a log line.

## Notes & caveats

- The **synthetic** profile is a product PoC — generated readers, not real behaviour. It
  exists so the whole stack runs with zero external data; every metric is still computed by
  the real pipeline.
- The **AI coach** returns a deterministic, grounded reply when no LLM key is set; add
  `ANTHROPIC_API_KEY` to enable the generated narrative.
- Recommenders are built **once per engine process** at startup; with `--workers N` that cost
  and memory are paid per worker.
- MIND, Politosphere, and Qbias are **external datasets** with their own licenses/downloads;
  only the synthetic profile ships ready to run.
