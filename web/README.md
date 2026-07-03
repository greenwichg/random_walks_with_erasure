# Information Health — Web App

The product frontend for **Information Health**: a reading-diet intelligence
tool built on the RWE recommender + Information Health Report backend (the Python
services in the repo root). This app is the UI layer — it talks to those services
over HTTP and never re-implements any algorithm.

## Stack

- **Next.js 14** (App Router) · **React 18** · **TypeScript** (strict)
- **Tailwind CSS** + **shadcn-style** primitives (vendored in `components/ui`)
- **Framer Motion** (animation) · **Recharts** (charts)
- **TanStack Query** (server state) · **Axios** (transport)

## Getting started

```bash
npm install
npm run dev      # http://localhost:3000
```

`npm run build && npm start` for a production build. `npm run typecheck` / `npm run lint` to verify.

## Backend integration — real engine, mock fallback

Every screen reads data through `hooks/use-data.ts` → `services/index.ts` →
`services/api.ts` (a single Axios client) → the app's own `app/api/*` route
handlers. Those handlers **proxy to the real Python engine** (`examples/api_server.py`)
via `lib/backend.ts`, and serialise exactly the shapes in `types/domain.ts` — so
nothing in the UI, `services`, or hooks changes when a route goes live.

Wired to the real engine today: **Report** (`health_report.compute` /
`user_report`), **Recommendations** (the real `RWE-B` / `RWE-D` / Adaptive
recommenders), and the **AI Coach** (`narrate_report`, grounded in the live
report metrics). The rest still serve mock data.

### Fallback policy — dev convenience, production honesty

`lib/backend.ts` is fail-soft, but whether a route may then serve mock data is a
policy set by `MOCK_FALLBACK_ENABLED`:

- **Development** — engine-backed routes fall back to deterministic mock JSON when
  the engine is unreachable, so the app runs without the engine.
- **Production** — fallback is **off**; an unreachable engine returns a typed
  `503 { error: { code: "engine_unavailable", … } }` and the UI shows its error
  state, so a reader is never shown fabricated health numbers. Override with
  `RWE_ALLOW_MOCK_FALLBACK=true|false`.

Mock-only routes (no engine counterpart yet) ignore this and keep serving mock
until their real service lands.

### Backend is the source of truth

Derived, product-defined values are computed once by the engine and consumed by
the UI, not recomputed per client. The engine emits each article's `leanBucket`
and `dominantEmotion`, and the health `band` (Healthy / Fair / Needs work) on the
report and every metric. The frontend consumes these; the local helpers
(`scoreBand`, `leanBucket`, `dominantEmotion` — via `resolveBand()` and optional
props) remain only as a fallback for mock data and for payloads that carry a raw
lean but no bucket (e.g. `SourceSlice`).

### Dataset profiles — switch data by configuration only

The engine selects its data source through a **named `DatasetProfile`** — no code
change to move between corpora. Profiles are chosen by flag or environment
variable (CLI > env > profile default):

| Profile | Source | Select with |
| --- | --- | --- |
| `synthetic` (default) | the repo's own simulator — no external data | *(nothing)* |
| `qbias` | synthetic users over a real Qbias AllSides catalog | `--qbias <csv>` / `RWE_QBIAS` |
| `mind` | an ingested MIND `.npz` (news) | `--npz <file>` / `RWE_NPZ` |
| `politosphere` | an ingested Politosphere `.npz` (reddit) | `--npz <file>` / `RWE_NPZ` |

Enrichment (`--register-csv`, `--emotion-csv`, `--behaviors`) and the lean-axis
centre (`--lean-tau`, sourced from the engine's own `LEAN_TAU`) are per-profile,
so a new production corpus is a new profile, not new code.

### Run against the real engine

```bash
# 1) start the engine from the repo root (stdlib only; boots the synthetic
#    simulator with zero external data). Add ANTHROPIC_API_KEY for the live coach.
python examples/api_server.py                         # synthetic, :8000
python examples/api_server.py --profile mind --npz mind_full.npz
RWE_PROFILE=mind RWE_NPZ=mind_full.npz python examples/api_server.py   # config-only

# 2) point the web app at it (web/.env.local)
RWE_BACKEND_URL=http://127.0.0.1:8000

# 3) run the app; /api/report, /api/recommendations, /api/coach now serve real
#    engine output (dev falls back to mock if the engine is down).
npm run dev
```

The JSON contract is guarded by `tests/test_api_server.py`.

## Pages

| Route | What it shows | Backend service |
| --- | --- | --- |
| `/` | Dashboard — overall score, today's reading, metric cards | Information Health Report + reading history |
| `/report` | The flagship report — radar, distributions, blind spots, improvements | Health Report |
| `/recommendations` | Cross-cutting / diversifying reads with per-item reasons and score impact | RWE-B / RWE-D / Adaptive RWE |
| `/coach` | Grounded chat that explains metrics and suggests reads | AI Coach (narrate) |
| `/history` | Searchable, filterable reading log — timeline + calendar heatmap | Reading history |
| `/discover` | Featured top story, topic exploration, trending clusters | Story clustering + topic extraction |
| `/stories` · `/stories/[id]` | One event, coverage across the spectrum — publisher/register/emotion side by side | Story clustering, NER, register + emotion classifiers |
| `/analytics` | 30-day trends: health, diversity, tone, reporting mix, acceptance | Diversity + satisfaction metrics |
| `/settings` | Recommender tuning (openness, strength), reports, notifications, privacy | Adaptive RWE-B knobs |
| `/profile` | Streaks, achievements, health journey, milestones | Profile + report history |

Global states are handled too: route-level `loading`, an `error` boundary, and a
branded `not-found`.

## Project structure

```
app/
  (app)/            authenticated shell (sidebar + header) + product pages
  api/              mock backend (swap for the real API via env)
  layout.tsx        root: fonts, theme, providers
components/
  ui/               vendored shadcn primitives (button, card, dialog, …)
  layout/           sidebar, header, search, page container
  shared/           cross-page building blocks (MetricCard, ScoreRing, charts…)
  dashboard/        dashboard widgets       report/    report widgets
  recommendations/  recommendation card     coach/     chat bubbles
  stories/          story card
hooks/              React Query hooks + utilities (useMeasure)
services/           typed data access + query keys (the API boundary)
types/              domain model — the contract for the whole app
lib/                design tokens (metrics, political helpers), utils
mock/               deterministic seed data behind the mock API
```

## Design system

Colors are CSS variables in `app/globals.css`, consumed via Tailwind tokens in
`tailwind.config.ts`. Light + dark themes swap by class (`next-themes`). The
metric palette, political spectrum (left/center/right), and emotion colors are
centralised in `lib/metrics.ts`, so the whole product re-themes from a few files.

## Scalability

The `services` boundary and typed domain model mean new surfaces — a browser
extension, mobile app, publisher/enterprise dashboards, an email digest — can
reuse the same data layer without touching UI code.
