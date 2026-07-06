/**
 * Startup environment validation for the web tier — pure logic, no side effects, so it can be
 * unit-tested (node --test) and called from `instrumentation.ts` at server boot.
 *
 * Philosophy (matches the engine): production is the cross-tier switch `RWE_ENV=production` (NOT
 * `NODE_ENV`, because the Colab demo serves a production Next build yet must stay zero-config). In
 * production the app refuses to boot with an incomplete configuration; outside production nothing
 * is fatal (local dev and Colab keep working with zero setup) and only advisory warnings are shown.
 */

/** @param {Record<string, string | undefined>} env */
export function isProduction(env) {
  const v = (env.RWE_ENV || "").trim().toLowerCase();
  return v === "production" || v === "prod";
}

const truthy = (v) => typeof v === "string" && v.trim() !== "";

/**
 * Validate the environment. Returns errors (fatal in production) and warnings (advisory).
 * @param {Record<string, string | undefined>} env
 * @returns {{ production: boolean, errors: string[], warnings: string[] }}
 */
export function validateEnv(env) {
  const production = isProduction(env);
  const errors = [];
  const warnings = [];

  const googleId = truthy(env.GOOGLE_CLIENT_ID);
  const googleSecret = truthy(env.GOOGLE_CLIENT_SECRET);

  // RWE_BACKEND_URL must be a valid URL wherever it is set (fatal in prod, advisory in dev).
  if (truthy(env.RWE_BACKEND_URL)) {
    try {
      new URL(env.RWE_BACKEND_URL);
    } catch {
      (production ? errors : warnings).push(
        `RWE_BACKEND_URL is not a valid URL: ${env.RWE_BACKEND_URL}`,
      );
    }
  }

  if (production) {
    if (!truthy(env.NEXTAUTH_SECRET))
      errors.push("NEXTAUTH_SECRET is required in production — it signs the session JWTs; without it sign-in throws at runtime. Generate one with `openssl rand -base64 32`.");
    if (!truthy(env.RWE_INTERNAL_SECRET))
      errors.push("RWE_INTERNAL_SECRET is required in production — it must match the engine's, which rejects unsigned per-user calls in production. Set the same value on both services.");
    if (!truthy(env.RWE_BACKEND_URL))
      errors.push("RWE_BACKEND_URL is required in production — the engine origin the proxy calls (e.g. https://engine.internal).");

    // Sign-in method: the dev demo-login is disabled in production, so Google OAuth is the only way in.
    if (googleId && googleSecret) {
      if (!truthy(env.NEXTAUTH_URL))
        errors.push("NEXTAUTH_URL is required in production when Google OAuth is enabled — it builds the OAuth callback URLs (e.g. https://app.example.com).");
    } else if (googleId || googleSecret) {
      errors.push("Google OAuth is only half-configured — set BOTH GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET (or neither).");
    } else {
      errors.push("No sign-in method is configured for production — set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET (the dev demo-login is disabled in production).");
    }
  } else {
    // Development / Colab: never fatal; surface a half-configured OAuth pair as a warning only.
    if (googleId !== googleSecret)
      warnings.push("Only one of GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET is set — Google sign-in will fail. Set both, or neither (dev demo-login).");
  }

  return { production, errors, warnings };
}

/**
 * Format a report for console output. Returned as lines so the caller decides how to emit them.
 * @param {{ production: boolean, errors: string[], warnings: string[] }} report
 * @returns {{ warnLines: string[], errorLines: string[] }}
 */
export function formatReport(report) {
  return {
    warnLines: report.warnings.map((w) => `[env] warning: ${w}`),
    errorLines: report.errors.map((e) => `  ✗ ${e}`),
  };
}
