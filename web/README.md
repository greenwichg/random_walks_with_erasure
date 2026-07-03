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
  dashboard/        dashboard-specific widgets
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
