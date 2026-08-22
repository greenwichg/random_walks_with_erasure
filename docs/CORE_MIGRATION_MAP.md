# The `packages/core` migration map

The audit behind splitting Hidden View into `packages/core` (shared product logic), `web/`
(Next.js/PWA) and `mobile/` (Expo). **No code has been moved yet — this is the map.**

Every classification below comes from parsing the import graph and scanning for platform globals,
not from reading file names. The script's rules are stated where they matter, because two of them
changed the answer materially.

## Headline numbers

| | files | lines |
|---|---|---|
| `web/` source modules audited (`lib`, `types`, `services`, `hooks`, `mock`) | 62 | 9,263 |
| **Portable today** — no platform import, no JSX, no browser-only global, transitively | 35 | 4,743 |
| → of which belong in **`packages/core`** | **29** | **4,175** |
| → portable but a *web-platform* concern, so they stay in `web/` | 5 | 481 |
| → portable but needs a transport seam | 1 | 87 |
| Blocked, worth adapting | 10 | ~1,700 |
| Blocked, stays web | 17 | ~2,800 |
| `web/components/` (not audited for portability — all web UI) | 111 | 11,236 |

Plus `web/messages/*.json` — 5 catalogs × 922 keys, pure data, moves as-is.

### Two measurement decisions that changed the answer

**`fetch`, `URL`, `AbortController`, `Intl` and `crypto` are not blockers.** They exist in React
Native / Hermes. An earlier pass counted them and reported ~9,800 portable lines across 69 files;
that number was inflated by counting test files and by a JSX heuristic (`<Foo>`) that matched
TypeScript generics. The measured figure is 4,175 lines in 29 modules.

**`axios` and `@tanstack/react-query` are not blockers either** — both run under React Native. That
is what moves `services/` (233 lines, the entire typed API client) from "blocked" to "portable", and
it matters more than its size: it is the module the Expo app talks to.

**`history` and `location` needed precise matching.** `services/index.ts` and `hooks/use-data.ts`
were first reported as touching the DOM; both were matching the *object key* `history:`. Corrected
by requiring the identifier not be preceded by `.` and not be followed by `:`.

## A thing the candidate list assumes that is not true

The brief lists "recommendation/ranking logic", "Interest Intensity", "story/coverage calculations"
and "history/health analytics calculations" as candidates to move into the shared core.

**Those algorithms are not in TypeScript.** They are in the Python engine — `examples/engine.py`,
`examples/story_service.py`, `examples/health_report.py`, `examples/discover.py` and friends.
`InterestIntensity` appears in the TypeScript tree exactly twice, both in `types/domain.ts`, as a
contract. What `web/lib` holds is the **presentation** of engine output: how to bucket a lean, how
to lay out a coverage plate, how to pick chart ticks, how to phrase an explanation.

That is still exactly right to share — two clients must present identical numbers identically, and a
second implementation of "which tick labels does this axis get" is a second source of truth. But the
shared core will not contain a recommender, and expecting one there would be a standing
disappointment. **The one shared product core already exists: it is the Python engine.**
`packages/core` is the shared *client* core.

## Move to `packages/core` — 29 modules, 4,175 lines

Grouped as the brief's directory layout.

### `core/domain/` — the contract

| Module | Lines | Note |
|---|---|---|
| `types/domain.ts` | 1,250 | **87 importers.** The single biggest reason to do this at all: one contract, three clients |

### `core/logic/` — product logic

| Module | Lines | Area |
|---|---|---|
| `lib/history-insights.ts` | 370 | history / health analytics shaping |
| `lib/analysis-presentation.ts` | 358 | article analysis presentation |
| `lib/chart-axis.ts` | 191 | axis scales + tick labels |
| `lib/home.ts` | 189 | home composition |
| `lib/rec-presentation.ts` | 157 | recommendation card presentation |
| `lib/auth-decision.ts` | 137 | the Phase 1 auth verdict — already written dependency-free for this reason |
| `lib/calendar-grid.ts` | 122 | streak calendar layout |
| `lib/coach-presentation.ts` | 122 | coach turn presentation |
| `lib/notification-kinds.ts` | 121 | the notification registry — its own header already says "pure data, no icons, no DOM" |
| `lib/countries.ts` | 115 | country logic |
| `lib/publisher-logo.ts` | 94 | logo URL selection + minimum-size rule |
| `lib/story-timeline.ts` | 80 | story timeline |
| `lib/settings-diff.ts` | 71 | settings patch diffing |
| `lib/framing.ts` | 62 | framing comparison |
| `lib/coverage.ts` | 58 | coverage calculations |
| `lib/engine-fallback.ts` | 44 | engine-failure → decision routing |
| `lib/bar-items.ts` | 37 | bar chart items |
| `lib/country-partition.ts` | 37 | country partitioning |
| `lib/discover-order.ts` | 28 | discover river interleave |
| `lib/discover-params.ts` | 28 | discover filter params |
| `lib/hero-copy.ts` | 25 | hero copy selection |
| `lib/request-params.ts` | 24 | query-param parsing |
| `lib/story-wire-keys.ts` | 22 | story wire keys |

### `core/api/` — the typed client

| Module | Lines | Note |
|---|---|---|
| `services/index.ts` | 177 | 33 typed call sites, one per endpoint area |
| `services/api.ts` | 58 | the single axios instance — its request interceptor is **already a commented placeholder for the bearer header** |
| `lib/engine-timeout.ts` | 95 | fetch deadline + backoff |

### `core/i18n/`

| Module | Lines | Note |
|---|---|---|
| `messages/*.json` | 5 × 922 keys | pure catalogs, move unchanged |
| `lib/i18n-core.ts` | 147 | **needs a 3-line adaptation** — see below |

### Fixtures (low priority, move with the rest or later)

`mock/onboarding.ts` (67), `mock/publishers.ts` (36).

## Stay web-only — even though they are portable (5 modules, 481 lines)

Portability is not the test; *what the module is about* is. These would pass any lint rule the core
package could impose and still be wrong there.

| Module | Lines | Why it is a web concern |
|---|---|---|
| `lib/env-validation.mjs` | 148 | the Next build-time env gate |
| `lib/rum.ts` | 133 | web vitals (LCP, CLS, longtask) — the concepts do not exist in React Native |
| `lib/sw-fetch-policy.ts` | 83 | the service worker's fetch policy; consumed by `public/sw.js` |
| `lib/security-headers.mjs` | 82 | CSP and HTTP response headers; consumed by `next.config.mjs` |
| `types/next-auth.d.ts` | 35 | module augmentation for a web-only package |

## Requires adaptation — ranked by leverage

### Cheap and high-value (do these with the move)

| Module | Lines | The blocker | The split | Unlocks |
|---|---|---|---|---|
| `lib/metrics.ts` | 187 | `lucide-react` | **9 of 187 lines** mention an icon — one `icon:` per metric. Metric keys, labels, thresholds and ordering are core; the icon map is web | `lib/political.ts` (66, Political Viewpoint Diversity) and `mock/data.ts` (604) |
| `lib/i18n-core.ts` | 147 | `document` | `activeLang()` reads `document.documentElement.lang` — **3 lines, already guarded**. Take the locale as an argument, or inject a reader | `lib/push.ts` (262) copy-building |
| `lib/notifications.ts` | 74 | `lucide-react` | same icon split | — |
| `lib/nav.ts` | 91 | `lucide-react` | the route table is core; icons and chrome are web | mobile navigation reuses the route list |
| `lib/record-read.ts` | 87 | `navigator.sendBeacon` | payload building + dedupe are core; the transport is per-platform. **`sendBeacon` has no React Native equivalent** and cannot become plain `fetch` on web — surviving the navigation to the publisher is the entire point | the read pipeline on mobile |
| `lib/onboarding.ts` | 84 | `window`, `localStorage` | the predicates are core; the pre-sign-in stash is per-platform storage | — |

`lib/metrics.ts` is the highest-leverage item on this page: nine lines of icon references are what
currently keep 857 lines (metrics + political + mock data) out of the core.

### Larger, later (leave until the Expo app needs them)

| Module | Lines | Shape of the split |
|---|---|---|
| `lib/continuation.ts` | 391 | the offer/dismissal decision core is portable; impression counting, `sessionStorage` and visibility handling are not |
| `lib/push.ts` | 262 | notification copy building is core; VAPID/`web-push` payloads are web, and APNs/FCM will be mobile |
| `lib/analytics.ts` | 189 | session + attribution model is core; the DOM collectors are web |
| `lib/observability.ts` | 82 | one guarded `window.location?.pathname` |

## Stay in `web/` — 17 modules plus all of `components/` and `app/`

| Group | Modules |
|---|---|
| Server / Next runtime | `lib/backend.ts`, `lib/require-user.ts`, `lib/engine-auth.ts`, `lib/auth.ts`, `lib/auth-callbacks.ts`, `lib/engine-identity.ts`, `lib/beta-access.ts` (`node:fs`), `lib/body-limit.ts` |
| Web UI plumbing | `lib/utils.ts` (`clsx` + `tailwind-merge`), `lib/i18n.tsx` (React provider), `lib/install-prompt.ts` (PWA), `lib/push-client.ts` (VAPID) |
| React hooks | `hooks/use-data.ts`, `hooks/use-push.ts`, `hooks/use-measure.ts`, `hooks/use-visibility-return.ts` |
| Everything rendering | `components/` (111 files, 11,236 lines), `app/` (70 files) |

`hooks/` is worth a note: `use-data.ts` is `@tanstack/react-query` over `services/`, and react-query
runs on React Native. Its blocker is `next-auth`. When mobile arrives, the *query keys and functions*
are shareable and the session wiring is not — but that is a Phase 3 refinement, not part of this move.

## Mobile-only, later

Nothing moves into `mobile/` in this migration. `mobile/` gets an Expo app boundary and no screens:
navigation, native components, `expo-secure-store` for the bearer token, APNs/FCM registration, and
the mobile design system. Named here only so the boundary is obvious from day one.

## How to move without breaking the web app

**171 import sites** reference the 29 core modules — 87 of them `types/domain.ts` alone. Rewriting
all of them in one commit is the version of this migration that breaks production.

Instead: move the file, and leave a one-line re-export where it was.

```ts
// web/lib/coverage.ts
export * from "@ih/core/logic/coverage";
```

Every existing `@/lib/coverage` import keeps working, unchanged, forever if need be. Call sites move
to `@ih/core` incrementally, and a later lint rule can ban new imports of the shims. This is what
"preserve existing imports through compatibility exports" buys: the migration stops being one
high-risk commit and becomes a sequence of individually revertible ones.

## Workspace tooling: npm workspaces, no Turborepo

The repository has **no root `package.json`** today; `web/` is a standalone package with its own
lockfile. The move needs a root `package.json` with `"workspaces": ["web", "packages/*", "mobile"]`
and a root lockfile.

Turborepo/Nx would buy task orchestration and remote caching across many packages. There are three
packages, two build commands, and one CI runner. npm workspaces (npm 10 ships with the Node 20 the
production image already uses) covers it, and adds no tool to learn or keep current.

`tsconfig` is already prepared for this: `moduleResolution: "bundler"` and a single `@/*` path alias.
`@ih/core` resolves through workspaces without a path alias at all.

## The risk that is not a test failure

**`deploy/Dockerfile.web` copies only `web/`.**

```dockerfile
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web ./
RUN npm run build
```

The build context is the repo root (`docker-compose.yml` sets `context: ..`), so the files are
*available* — they are simply never copied. The moment `web/` imports `@ih/core`, this image fails to
build. Not a test failure, not a lint failure: a production deploy failure, discovered at deploy
time.

It must change to copy the root manifests, `packages/`, and `web/`, and run `npm ci` at the root.
Two CI jobs also pin `cache-dependency-path: web/package-lock.json` and `working-directory: web` and
need the same treatment. **Verify the Docker build locally before the first push that adds a
`@ih/core` import to `web/`** — that ordering is the whole mitigation.

Also to re-point: `playwright.config.ts` computes `REPO_ROOT` from `__dirname`, and `package.json`'s
`test` script names **45 test files by explicit path** — the ones that move need their paths updated
in whichever package ends up running them. That hand-maintained list is itself an argument for giving
`packages/core` its own `npm test` that globs, rather than extending the web one.

## The guard that keeps core clean

A test in `packages/core`, run by `npm test`, scanning its own source — the same shape as
`web/lib/api-auth-guard.test.ts`, and for the same reason: the rule is invisible while you are
writing the module that breaks it.

It should fail on any of:

- an import of `react`, `react-dom`, `react-native`, `expo*`, `next`, `next-auth`, or any
  `@radix-ui`/`recharts`/`framer-motion`/`lucide-react`/`tailwind`/`clsx` package;
- an import of `node:*` (outside `*.test.ts`);
- a `.tsx` file anywhere in the package;
- a reference to `document`, `window`, `localStorage`, `sessionStorage`, `matchMedia`,
  `IntersectionObserver`, `HTMLElement`, `navigator.sendBeacon`, or `getComputedStyle`;
- an import that escapes the package (`../../web/…`).

Two things it must get right, both learned from the auth guard:

1. **Strip comments and string literals before scanning.** The auth guard's first draft counted a
   `SESSION_ONLY` mention *in a doc comment* and passed a real violation. The same trap is waiting
   here — these modules have long headers that name `document` and `React` while explaining why they
   avoid them.
2. **Assert the file count is non-zero.** A guard that scans a renamed directory reports success for
   checking nothing.

And it must be proved by mutation, not by a green run: add a file importing `react`, confirm the
guard fails, delete it.

`tsconfig` for the package should also set `"lib": ["ES2022"]` with **no `"DOM"`**, so `document`
stops type-checking rather than merely being caught by a regex. The regex covers what the compiler
cannot (a `react-native` import type-checks fine); the compiler covers what the regex would miss.

## Suggested order

Each step leaves the web app green and is revertible on its own.

1. Root `package.json` with workspaces; `packages/core` scaffold, `tsconfig`, guard test, empty index. Nothing imports it.
2. **Fix `deploy/Dockerfile.web` and both CI jobs. Verify the image builds.** Before any import exists.
3. Move `types/domain.ts` + `web/messages/` — the contract and the catalogs. Re-export shim at `web/types/domain.ts`.
4. Move the 23 `core/logic/` modules with shims. Run typecheck, lint, `npm test`, e2e.
5. Move `services/` and `lib/engine-timeout.ts` into `core/api/`.
6. Adapt `lib/metrics.ts` and `lib/i18n-core.ts` — the two cheap unlocks — and move what they free.
7. `mobile/` Expo boundary: `package.json`, `app.json`, `tsconfig` extending the root, a single placeholder screen. No product screens.
8. Retire the shims incrementally; add a lint rule banning new imports of them.

Steps 1–5 are mechanical. Step 6 is the only one that changes behaviour-carrying code, and it changes
where an icon comes from, not what a metric is.
