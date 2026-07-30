# End-to-End Regression Suite

Committed Playwright suite covering the critical production user journeys against the **real stack** —
a fresh FastAPI engine plus a production Next build, wired together — so persistence, ranking, and
read-recording are genuinely exercised (not stubbed).

## What it covers

| Spec | Journey |
|------|---------|
| `specs/auth.spec.ts` | Sign in → session established → dashboard loads; signed-out redirect |
| `specs/reading-history.spec.ts` | Record a read → History updates → Dashboard metrics update |
| `specs/recommendation-feedback.spec.ts` | Like / Dislike / Ignore / Read-later persist; ignore survives reload; ranking unchanged; anonymous 401 |
| `specs/health-report.spec.ts` | Report renders; estimate before activity; measured metrics after ≥5 reads |
| `specs/settings.spec.ts` | Theme persists across reload; a preference saves and is retained |
| `specs/saved.spec.ts` | Save → persist across reload → unsave → empty state |
| `specs/error-handling.spec.ts` | Anonymous protected endpoint → 401; unavailable backend → error state, no fabricated data |
| `specs/identity-recovery.spec.ts` | A session with no engine id heals to the **same** account and the re-issued cookie carries it; a legacy token heals from `sub`; a non-Google token is refused |

## How to run

```bash
cd web
npm install                 # installs @playwright/test (already a devDependency)
npm run e2e                 # builds with the demo-login affordance, then runs the suite
```

`npm run e2e` = `build:e2e` (a `next build` with `NEXT_PUBLIC_DEV_LOGIN=1` so `/signin` exposes the
automatable demo sign-in) followed by `playwright test`. To re-run without rebuilding: `npm run e2e:run`.
Open the HTML report with `npm run e2e:report`.

Playwright's `webServer` starts **both** servers automatically and tears them down after:

1. **Engine** — `uvicorn api_fastapi:app` on `:8000`, cwd `../examples`, with an **isolated temp SQLite
   DB** (`web/.e2e-tmp/engine.db`) that is deleted before each run. Dev trust (no internal secret is
   needed for localhost), rate limiting off (so seeding never 429s).
2. **Web** — `next start` on `:3300`, pointed at the engine, run with the **production mock policy**
   (`RWE_ALLOW_MOCK_FALLBACK=false`) so mock data can never appear, plus `RWE_DEV_LOGIN=1` for the
   demo credentials provider and a fixed `NEXTAUTH_SECRET` the fixtures sign session cookies with.

### Environment

The browser: the suite uses whatever Chromium `@playwright/test` resolves. In this repo's container
that is the pre-installed browser (`PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers`, already exported). On
a generic CI runner, install it once with `npx playwright install chromium`. Python deps for the
engine (`fastapi`, `uvicorn`, …) must be importable from `../examples` (the same environment the
backend `pytest` suite uses).

No secrets or external services are required — everything is local and synthetic.

## Design notes

- **Determinism**: one worker, no parallelism (a single shared engine DB), a fresh DB per run, and
  **one isolated engine user per test** (the `uid` / `authedPage` fixtures in `fixtures.ts`).
- **No fixed sleeps**: every wait is condition-based — `expect(...).toBeVisible()`, `expect.poll(...)`
  for the fire-and-forget feedback writes, and `page.waitForResponse(...)` before a navigation that
  depends on a just-issued persist call.
- **Auth**: OAuth is external and un-automatable, so `auth.spec` drives the demo credentials provider
  (same session + engine-upsert path as Google), and every other spec starts already authenticated
  via a minted session cookie (`helpers.ts::mintSessionCookie`) — the same JWT a completed sign-in
  would set.
- **Helpers** (`helpers.ts`) only *set up* and *read back* state through the engine's real endpoints
  (`createEngineUser`, `seedReads`, `seedOnboarding`, `engineGet/Post`). They add no product behavior.

Artifacts (the temp DB, traces, HTML report) are written under `web/.e2e-tmp/` and are git-ignored.
