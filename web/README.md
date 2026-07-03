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

## Backend integration — mock today, real tomorrow

Every screen reads data through `hooks/use-data.ts` → `services/index.ts` →
`services/api.ts` (a single Axios client). Today those calls hit **mock API
routes** under `app/api/*` that return realistic JSON shaped by `types/domain.ts`.

To point at the real Python backend, set one env var — nothing else changes:

```bash
# .env.local
NEXT_PUBLIC_API_BASE_URL=https://api.informationhealth.app
```

The mock routes (`/api/report`, `/api/recommendations`, `/api/coach`, …) map 1:1
to the backend endpoints, so you can migrate them one at a time.

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
