# `@ih/core` — the shared client core

Platform-independent product logic, shared by `web/` (Next.js / PWA) and `mobile/` (Expo).

## The rule

**Nothing in this package may import React, React Native, Next.js, the DOM, or any UI library, or
reference a browser global.** Two mechanisms enforce it, and they cover different failures:

- `tsconfig.json` sets `"lib": ["ES2022"]` with **no `"DOM"`**, so `document` and `window` are not
  declared. The compiler refuses them at the point of writing, by name.
- `guard.test.ts` scans this package's own source for the things the compiler cannot see — a
  `react-native` import type-checks perfectly well, and so does `navigator.sendBeacon`.

If a module needs the platform, it does not belong here. Give it a seam instead: take the value as
an argument, or accept an injected reader. `logic/record-read.ts` is the worked example — it builds
the payload, and the caller supplies the transport.

## Layout

| Directory | Holds |
|---|---|
| `domain/` | the type contract shared by every client and mirrored by the Python engine |
| `logic/` | product logic — presentation of engine output, ordering, bucketing, layout maths |
| `api/` | the typed API client and its transport policy |
| `i18n/` | the message catalogs and the pure resolver |

## What is *not* here, deliberately

**The recommender, the clustering, the health-report computation and the Interest Intensity
weighting are not in this package and are not coming.** They live in the Python engine
(`examples/engine.py`, `examples/story_service.py`, `examples/health_report.py`). Hidden View's one
shared product core is the engine; this is the shared *client* core, and what it holds is how engine
output is shaped and shown.

That distinction is worth keeping straight, because "shared core" invites the assumption that the
algorithms are in here. Presenting the same number two different ways on two platforms is the bug
this package prevents. Computing it twice was never on the table.

## Importing

```ts
import type { Story } from "@ih/core/domain/types";
import { coverageFacts } from "@ih/core/logic/coverage";
```

Subpath exports map straight to the TypeScript source (`"./*": "./*.ts"`), so there is no build step
and no `dist/`. All three toolchains resolve it: `tsc` (via `moduleResolution: "bundler"`),
`next build` (webpack follows the workspace symlink to real source outside `node_modules`, and
`transpilePackages` makes that explicit), and `node --test` (Node 22 strips types natively).

## Compatibility shims in `web/`

Most modules moved here still have a one-line re-export at their old path:

```ts
// web/lib/coverage.ts
export * from "@ih/core/logic/coverage";
```

Existing `@/lib/…` imports keep working untouched. Call sites move to `@ih/core` incrementally; the
shims are deleted when the last one does. New code should import from `@ih/core` directly.
