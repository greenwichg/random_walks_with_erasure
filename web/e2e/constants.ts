/**
 * Shared constants for the E2E regression suite — imported by both `playwright.config.ts` (to launch
 * the two servers) and the fixtures/helpers (to talk to them). Keeping them in one module means the
 * ports, the engine origin, and the session secret can never drift between the config and the tests.
 *
 * NONE of these are production values: the secret is fixed only so the fixtures can mint a session
 * cookie the test web server will accept, and the ports/origins are local. See e2e/README.md.
 */
export const ENGINE_PORT = 8000;
export const WEB_PORT = 3300;

export const ENGINE_URL = `http://127.0.0.1:${ENGINE_PORT}`;
export const WEB_URL = `http://localhost:${WEB_PORT}`;

/** Signs the NextAuth session JWT; the test web server is started with the identical value. */
export const NEXTAUTH_SECRET = "e2e-fixed-secret-not-for-production";

/** The measured-report threshold (examples/api_server.py: ESTIMATE_MIN_READS). Below it a reader
 *  gets the Initial Estimate; at/above it, the Measured report with personalized metrics. */
export const MEASURED_MIN_READS = 5;
