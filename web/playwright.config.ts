import { defineConfig, devices } from "@playwright/test";
import path from "node:path";
import { ENGINE_PORT, WEB_PORT, WEB_URL, NEXTAUTH_SECRET } from "./e2e/constants";

/**
 * E2E regression suite — drives the REAL stack: a fresh FastAPI engine (isolated temp SQLite DB) and
 * a production Next build, wired together, so persistence / ranking / recording are genuinely
 * exercised (not stubbed). See e2e/README.md for the environment contract.
 *
 * Determinism: one worker, no parallelism (a single shared engine DB), a fresh DB per run (the engine
 * command deletes it before starting), and one isolated engine user per test (the fixtures). The web
 * server runs with the PRODUCTION mock policy (`RWE_ALLOW_MOCK_FALLBACK=false`) so mock data can
 * never appear — the error-handling spec asserts an unavailable backend yields an error state, not a
 * fabricated one.
 */
const WEB_DIR = __dirname;
const REPO_ROOT = path.join(WEB_DIR, "..");
const TMP = path.join(WEB_DIR, ".e2e-tmp");
const DB = path.join(TMP, "engine.db");

export default defineConfig({
  testDir: "./e2e/specs",
  outputDir: path.join(TMP, "results"),
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: [["list"], ["html", { outputFolder: path.join(TMP, "report"), open: "never" }]],
  timeout: 60_000,
  expect: { timeout: 15_000 },
  use: {
    baseURL: WEB_URL,
    actionTimeout: 15_000,
    navigationTimeout: 30_000,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "off",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    {
      // Fresh, isolated DB every run (deleted before uvicorn binds); dev trust (no internal secret is
      // needed for localhost server-to-server); rate limiting off so rapid seeding never 429s.
      command:
        `sh -c "mkdir -p '${TMP}' && rm -f '${DB}' '${DB}-wal' '${DB}-shm' && ` +
        `exec python -m uvicorn api_fastapi:app --host 127.0.0.1 --port ${ENGINE_PORT}"`,
      cwd: path.join(REPO_ROOT, "examples"),
      env: {
        RWE_DB_URL: `sqlite:///${DB}`,
        RWE_ENV: "dev",
        RWE_RATELIMIT_ENABLED: "0",
        RWE_LOG_LEVEL: "WARNING",
      },
      url: `${WEB_URL.replace(String(WEB_PORT), String(ENGINE_PORT)).replace("localhost", "127.0.0.1")}/api/health`,
      timeout: 120_000,
      reuseExistingServer: !process.env.CI,
      stdout: "ignore",
      stderr: "pipe",
    },
    {
      // Serves the production build produced by `npm run build:e2e` (which bakes NEXT_PUBLIC_DEV_LOGIN
      // so /signin exposes the automatable demo sign-in). PRODUCTION mock policy: no mock fallback.
      command: `npx next start -p ${WEB_PORT}`,
      cwd: WEB_DIR,
      env: {
        RWE_BACKEND_URL: `http://127.0.0.1:${ENGINE_PORT}`,
        NEXTAUTH_SECRET,
        NEXTAUTH_URL: WEB_URL,
        RWE_ALLOW_MOCK_FALLBACK: "false",
        RWE_DEV_LOGIN: "1",
      },
      url: WEB_URL,
      timeout: 120_000,
      reuseExistingServer: !process.env.CI,
    },
  ],
});
