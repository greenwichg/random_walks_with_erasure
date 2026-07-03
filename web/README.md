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
handlers. Those handlers **proxy to the real Python engine** and fall back to
deterministic mock JSON (shaped by `types/domain.ts`) whenever the engine is
unreachable — so the app always works, and services migrate one at a time.

Wired to the real engine today: **Report** (`health_report.compute` /
`user_report`), **Recommendations** (the real `RWE-B` / `RWE-D` / Adaptive
recommenders), and the **AI Coach** (`narrate_report`, grounded in the live
report metrics). The rest still serve mock data.

### Run against the real engine

```bash
# 1) start the engine from the repo root (stdlib only; no external data needed —
#    it boots the repo's synthetic simulator. Add --npz <data> for real corpora,
#    and an ANTHROPIC_API_KEY to light up the live coach narrative.)
python examples/api_server.py            # serves http://127.0.0.1:8000

# 2) point the web app at it (web/.env.local)
RWE_BACKEND_URL=http://127.0.0.1:8000

# 3) run the app; /api/report, /api/recommendations, /api/coach now serve real
#    engine output. Stop the engine and they transparently fall back to mock.
npm run dev
```

The proxy bridge is `lib/backend.ts` (one env var, fail-soft). The engine
serialises exactly the shapes in `types/domain.ts`, so nothing in the UI,
`services`, or hooks changes when a route goes live.

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
